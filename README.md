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
3. The chain performs a single BAR0 write of `0xFFFFFFFF` to a target PLM register.
4. We do this 11 times to open all Platform Lock Manager registers.
5. With PLM open, the host driver writes the memory unlock (`CFG1`, `LMR`) and compute unlock (`SS0`, `SS1`) values via BAR0.
6. The original GSP signature is restored so the driver doesn't detect tampering.
7. The driver continues normal init with full memory + full SM clock.

The unlock is **volatile** (lost on power cycle) but reapplied automatically by the daemon.

---

## Persistence

The installed daemon (`cmpunlocker.service`) handles persistence:

- **On boot**: runs the full unlock pipeline before the display manager starts
- **Every 10 seconds**: checks SS0/SS1 and CFG1/LMR via BAR0 and rewrites them if drifted
- **On driver reload**: detects a closed PLM and reruns the full pipeline
- **Multiple cards**: all CMP 170HX GPUs present in the system are handled

The daemon is enabled at boot via systemd and restarts automatically on failure.

---

## Gen2 PCIe Recovery

After cold boot, Gen2 may need retraining:

```bash
# Check current PCIe speed
nvidia-smi -q | grep "Link"

# If speed=1 (Gen1), run recovery
sudo gen2-cycle 2000
```

The `gen2-cycle` script stops GPU processes, does a secondary bus reset, and retrains Gen2. Usually succeeds on cycle 1 (~12 seconds).

---

## Recovery

If the unlock is lost (kernel upgrade, driver rebuild), use the built-in recovery tool:

```bash
# Full diagnostic
python3 cmprecover diagnose

# Full recovery (GSP → driver → PLM → Gen2)
sudo python3 cmprecover full

# Individual recovery
sudo python3 cmprecover plm      # Re-open PLM registers
sudo python3 cmprecover gen2     # Retrain Gen2 PCIe
sudo python3 cmprecover driver   # Rebuild driver
sudo python3 cmprecover gsp      # Restore GSP firmware
```

### Recovery Order

1. **GSP firmware** — corrupted firmware breaks everything
2. **Driver** — kernel upgrade requires rebuild
3. **PLM registers** — locked = no memory/compute unlock
4. **Gen2 PCIe** — slow link, can be done later

### Manual Recovery (what we actually used)

The lab tree that recovered this box is **buliaoyin-cmpunlocker**, not a GitHub clone of this repo and not the 80GB experiments.

```bash
cd /home/ai/.hermes/cmp_lab/buliaoyin-cmpunlocker
sudo ./install.sh --profile=10gb --no-iommu
sudo shutdown -h now   # cold boot required
```

That path is this machine only. `cmprecover` above is the in-tree tool.

### Why nvidia-smi may show PCIe Gen 1

On this 170HX the endpoint **LnkCap is 2.5 GT/s only** until the feature unlock + link retrain stick. The CPU bridge can do 32 GT/s; the card is advertising Gen1. `gen2-cycle.service` is **disabled on purpose while TENSELERATE is serving** — that script `rmmod nvidia` and kills anything on `/dev/nvidia*`.

After a cold boot with **no** llama-server:

```bash
sudo gen2-cycle 2000    # stop GPU users, retrain; usually cycle 1
```

Stable ceiling we measured: **Gen2 x4**, not Gen4/Gen5. Do not chase 80GB.

---

## Key Files

| File | Purpose |
|------|---------|
| `cmpunlocker/payload/pipeline.py` | Main unlock pipeline |
| `cmpunlocker/common/constants.py` | PLM tables, register addresses |
| `cmpunlocker/daemon/watchdog.py` | Daemon watchdog |
| `/opt/cmpunlocker/` | Deployed copy |
| `/lib/modules/7.0.0-30-generic/updates/cmpunlocker/nvidia.ko` | Patched driver |
| `/usr/local/sbin/gen2-cycle` | Gen2 recovery script |
| `/etc/modprobe.d/cmp-pcie-gen2.conf` | Gen2 kernel parameters |

---

## Documentation

- `GEN2_MECHANISM.md` — Complete technical reference (4 write phases, lockout method, recovery)
- `docs/TROUBLESHOOTING.md` — Common issues and solutions
