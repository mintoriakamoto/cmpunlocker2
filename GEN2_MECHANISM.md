# CMP 170HX Gen2 PCIe Link Training — Complete Technical Reference

## Hardware

| Property | Value |
|----------|-------|
| GPU | NVIDIA CMP 170HX (GA100) |
| PCI ID | 10de:2082 (10GB variant) |
| Memory | 40960 MiB (40GB) |
| PCIe | Gen2 5GT/s x4 |
| Driver | 610.43.02 (patched) |
| Motherboard | ASUS TUF GAMING B650E-PLUS WIFI |

## Final State (Locked In)

```
LnkSta:  Speed 5GT/s, Width x4
LnkCtl2: Target Link Speed: 5GT/s
nvidia-smi: pcie.link.gen.current=2, pcie.link.gen.max=2, memory.total=40960 MiB
```

## The Four Write Phases

The CMP 170HX unlock involves four distinct write phases, each targeting different hardware layers:

### Phase 1: PLM Unlock (11 registers via SEC2 Falcon)

The 11 PLM (Power Limit Management) registers control GPU power domains:

| Register | Address | Purpose |
|----------|---------|---------|
| WPR_CFG | 0x1fa7cc | Write Protection Config |
| FBPA | 0x9a0148 | Frame Buffer Partition Access |
| WPR | 0x1fa7c4 | Write Protection Register |
| FEAT | 0x823804 | Feature Control |
| XVE | 0x88ff4 | PCIe Virtual Endpoint |
| XVE_B | 0x88ab4 | PCIe VE Extension B |
| XVE_C | 0x88ff8 | PCIe VE Extension C |
| FEAT2 | 0x823b00 | Feature Control 2 |
| OPT_PLM | 0x8200fc | Optional PLM |
| PJTAG_PLM | 0xc840 | PJTAG PLM |
| PJTAG_SEC_PLM | 0xc848 | PJTAG Secondary PLM |

**Method:** Each PLM is unlocked by patching the GSP firmware's xp3gTable with the target address/value, then triggering kgspExecuteBooterLoad via modprobe. The SEC2 Falcon writes the register during boot.

**Persistence:** PLMs persist across reboots because the patched booter rewrites them on every GSP firmware load. After buliaoyin's install.sh, the patched driver is installed permanently.

### Phase 2: Memory Unlock (CFG1 + LMR via booter)

| Register | Address | Value (10GB) | Purpose |
|----------|---------|--------------|---------|
| CFG1 | 0x02449000 | 0x02449000 | Frame buffer config |
| LMR | 0x8c040 | 0x0000028A | Memory link rate |

**Method:** Written via BAR0 after PLMs are open. CFG1 controls framebuffer geometry. LMR is device-variant-specific (10de:2082 → 0x028A, 10de:20c2 → 0x020B).

**Persistence:** Volatile — lost on power cycle. Daemon reapplies on every driver reload.

**80GB attempt:** CFG1=0x02779000 (80GB) was rejected by firmware, reads back 0x02449000. Hardware limit confirmed.

### Phase 3: Compute Unlock (SS0 + SS1 via BAR0)

| Register | Address | Value | Purpose |
|----------|---------|-------|---------|
| SS0 | 0x88404 | 0x88888888 | Shader/SM enable |
| SS1 | 0x88408 | 0x00000008 | Shader/SM config |

**Method:** Direct BAR0 writes after PLMs and memory are unlocked.

**Persistence:** Volatile — lost on power cycle. Daemon reapplies.

### Phase 4: Gen2 PCIe Link (LC2 + PL via booter BAR0)

| Register | Address | Value | Purpose |
|----------|---------|-------|---------|
| LC2 | 0x30 (PCI config) | 0x000F0002 | Link Control 2 (Gen2 target) |
| PL | 0x34 (PCI config) | 0x00240036 | Power Limit (bit 20 cleared) |

**Method:** Written by the booter during kgspBootGspRm (GSP firmware load). The booter writes LC2 to target Gen2 and PL to clear bit 20.

**Persistence:** LC2/PL are PCI config space registers, NOT NVRAM. They reset on secondary bus reset. But after the 2nd boot post-reset, the booter writes stick and the link trains at Gen2.

## The Lockout Method

After buliaoyin's install.sh completes:

1. **Booter lockout:** The patched booter writes PLMs on every GSP firmware load. The 11 PLM registers are now permanently "open" — the GPU's internal power domains are unlocked.

2. **Register lockout:** PCI config space registers (LC2, PL, CAP) are hardware-locked after the GPU's PCIe subsystem initializes. They can only be written during the booter phase (before PCIe link training completes).

3. **Memory lockout:** CFG1/LMR are written after PLMs are open but before the PCIe link trains. Once the link is up, these registers are locked by the GPU's internal state machine.

4. **The secondary bus reset clears the lockout:** When rmmod triggers a secondary bus reset, the GPU powers down, PLMs relock, and the PCIe subsystem resets. On the next modprobe, the booter re-opens the PLMs and rewrites LC2/PL. On the 2nd boot (reload), all writes stick.

## Recovery via buliaoyin

If the unlock is lost (kernel upgrade, driver rebuild, etc.):

```bash
cd /home/ai/.hermes/cmp_lab/buliaoyin-cmpunlocker
sudo ./install.sh --profile=10gb --no-iommu
sudo shutdown -h now  # cold boot required
```

This:
1. Rebuilds the patched driver with buliaoyin's xp3gTable
2. Installs the Gen2 retrain service
3. Configures IOMMU passthrough
4. Requires cold boot to apply

After cold boot, the booter writes PLMs + LC2/PL. Gen2 should train automatically.

If Gen2 doesn't train automatically:
```bash
sudo gen2-cycle 2000
```

## What Messed Us Up (Lessons Learned)

### 1. Old daemon watchdog polling at 1s
The original daemon polled every 1 second, doing rmmod/modprobe each cycle. This caused 1065+ RmInit cycles in 20 minutes, corrupting the GPU firmware (speed=15 errors in kern.log). Fix: rewrote daemon to state machine with flock(), only reapply when values drift.

### 2. xp3gTable LC2/PL entries in driver
We added LC2 and PL entries to the xp3gTable in the driver patch. These compiled but the writes always readback 0xffffffff (hardware-blocked by SEC2 Falcon). The entries were wasteful but harmless. We reverted them to match original buliaoyin.

### 3. Fast daemon cycling preventing Gen2
The daemon cycled at ~370ms/rmmod+modprobe. The GPU never completed its secondary bus reset before the next modprobe. The booter's LC2/PL writes couldn't stick because the GPU's internal state blocked them on the 1st boot after reset. Fix: added 2-second waits after each rmmod.

### 4. GDM holding nvidia_drm
GDM/GNOME held /dev/dri/card1 via nvidia_drm, preventing rmmod. The gen2-cycle service masked GDM at boot, causing login to fail. Fix: disable gen2-cycle at boot, use manually.

### 5. llama-server holding nvidia_uvm
CUDA processes held /dev/nvidia-uvm, preventing rmmod. Fix: kill all GPU processes before cycling.

### 6. Services resetting Gen2
After achieving Gen2, restarting services (especially nvidia-cdi-refresh running nvidia-ctk) appeared to reset it. Actually: the services were fine, but rmmod was silently failing because GDM/llama held modules. Once processes were killed, Gen2 persisted through all service restarts.

## Key Register Addresses

| Register | BAR0 Address | PCI Offset | Notes |
|----------|-------------|------------|-------|
| CAP | 0x00088084 | 0x00 | PCIe Capabilities |
| LC2 | — | 0x30 | Link Control 2 |
| PL | — | 0x34 | Power Limit |
| STAT | 0x00088088 | 0x12 | Link Status |
| LNKCTL2 | — | 0x30 | Link Control 2 (setpci) |
| LNKCTL | — | 0x10 | Link Control (setpci) |
| LNKSTA | — | 0x12 | Link Status (setpci) |
| CFG1 | 0x02449000 | — | Frame buffer config |
| LMR | 0x0008c040 | — | Memory link rate |
| SS0 | 0x00088404 | — | Shader enable |
| SS1 | 0x00088408 | — | Shader config |
| XVE_OVR | 0x0008872c | — | PCIe override |

## Boot Sequence with Gen2

```
1. GPU powers on → PLMs relocked → LC2=0x00000001 (Gen1 default)
2. Kernel loads nvidia.ko → kgspBootGspRm runs
3. Booter: reads PLMs (all 0xffffffff = locked)
4. Booter: writes xp3gTable via SEC2 Falcon
5. Booter: writes CAP/LC2/PL via BAR0 (may not stick on fresh boot)
6. GSP firmware initializes → PCIe link trains at Gen1
7. nvidia-smi opens GPU → nv_cmp170hx_retrain_gen2() runs
8. Probe-retrain: setpci LC2=Gen2 on GPU + upstream
9. Probe-retrain: setpci Retrain_Link on upstream
10. PHY retrain completes → link at Gen2 (5GT/s)
11. LC2 bit 16 (0x10000) set by hardware → persists across rmmod
```
