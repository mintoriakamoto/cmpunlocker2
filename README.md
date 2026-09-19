# cmpunlocker — CMP 170HX GPU Unlock

**Unlock GA100 compute and memory on NVIDIA CMP 170HX mining cards via Falcon BootROM ROP exploit.**

**Achieves:** 40GB memory + 1410 MHz compute + Gen 2 x4 PCIe (stable, persistent)

Targets **nvidia-open driver 610.x** on Linux x86-64.

```bash
sudo python3 cmpunlocker/payload/pipeline.py 0000:01:00.0  # Apply unlock (40GB + 1410MHz)
```

---

## Hardware

| Property | Value |
|----------|-------|
| GPU | NVIDIA CMP 170HX (GA100) |
| PCI ID | `10de:2082` (10GB, 5 HBM2e stacks) |
| Memory | 40960 MiB (40GB) |
| PCIe | Gen2 5GT/s x4 |
| Driver | 610.43.02 (patched) |
| Motherboard | ASUS TUF GAMING B650E-PLUS WIFI |

**80GB is hardware-blocked.** Wrote `CFG1=0x02779000` → firmware rejected, reads back `0x02449000`.

---

## Install

Run once. Applies the unlock immediately and installs a systemd daemon that reapplies it automatically after every reboot or driver reload.

```bash
sudo ./install.sh
```

That is the only command needed.

---

## Verification

Check that the SM clock cap is gone:

```bash
nvidia-smi --query-gpu=clocks.max.sm --format=csv,noheader
```

Check that VRAM is at the target capacity:

```bash
nvidia-smi --query-gpu=memory.total --format=csv,noheader
```

Follow the daemon log:

```bash
journalctl -u cmpunlocker -f
```

---

## What Gets Unlocked

| Feature | Status | Bandwidth/Speed |
|---|---|---|
| **PCIe Gen 2 x4** | ✅ 2 GB/s | 5GT/s, persists across rmmod/modprobe |
| **40GB Memory** | ✅ 5 × HBM2e | Full available capacity |
| **Full SM Throughput** | ✅ SS0/SS1 unlock | All SMs at max clock |

---

## How it works

The exploit is the same one used in the `open-gpu-kernel-modules-610.43.03` driver fork:

1. The Falcon BootROM loads the `.fwsignature_ga100` ELF section content into DMEM *before* verifying the signature (the bug).
2. We replace the section content with a 63KB ROP chain.
3. The chain performs a single BAR0 write of the correct PLM value to a target PLM register.
4. We do this 11 times to open all Platform Lock Manager registers.
5. With PLM open, the host driver writes the memory unlock (`CFG1`, `LMR`) and compute unlock (`SS0`, `SS1`) values via BAR0.
6. The original GSP signature is restored so the driver doesn't detect tampering.
7. The driver continues normal init with full memory + full SM clock.

The unlock is **volatile** (lost on power cycle) but reapplied automatically by the daemon.

**CRITICAL: PLM values must be firmware-actual, not 0xffffffff.**
Using 0xffffffff for FEAT/FEAT2 registers corrupts Falcon state and triggers the 4-try lockout.
See `cmpunlocker/common/constants.yaml` for correct values.

---

## Recovery

If something goes wrong (wrong PLM values, lockout, driver corruption):

```bash
cd /home/ai/.hermes/cmp_lab/buliaoyin-cmpunlocker
sudo ./install.sh --profile=10gb --no-iommu
sudo shutdown -h now   # cold boot required (60s capacitor discharge)
```

The buliaoyin-cmpunlocker uses a different unlock method and is the recovery path.

---

## Branches

| Branch | Purpose |
|--------|----------|
| `master` | Stable 40GB + 1410MHz + Gen2 (recommended) |
| `unlock-80gb` | Experimental 80GB target (blocked by firmware/hardware — see notes) |
| `unlock-gen3` | Experimental Gen3 PCIe unlock (blocked by OTP fuse — see notes) |

### Why 80GB fails
The GPU firmware enforces a "persistent state" lock: once 40GB is applied, the firmware refuses CFG1 changes. A BIOS-level reset (device disable/re-enable) returns to native 10GB state where 80GB unlock is theoretically possible — but HBM timing mismatch then causes GSP crash (Xid 154) at CUDA init.

### Why Gen3/4/5 fails  
OTP fuse `FUSE_PCIE_GEN23_DIS` is burned at the factory in the immutable BootROM. Software writes to XVE_OVR persist but the physical link never negotiates above Gen2. GA100 hardware max is Gen4; Gen5 is Hopper architecture only.

---

## Gen2 PCIe Recovery

After cold boot, Gen2 may need retraining:

```bash
# Check current PCIe speed
nvidia-smi -q | grep "Link"

# If speed=1 (Gen1), run recovery
sudo gen2-cycle 2000
```

---

## Key Files

| File | Purpose |
|------|----------|
| `cmpunlocker/payload/pipeline.py` | Main unlock pipeline |
| `cmpunlocker/common/constants.yaml` | PLM tables, register addresses |
| `cmpunlocker/daemon/watchdog.py` | Daemon watchdog |
| `cmpunlocker/scripts/gen2-cycle` | Gen2 recovery script |
| `cmpunlocker/scripts/pcie_gen4_unlock.sh` | PCIe config space unlock |
