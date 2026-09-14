# D3DX9-Pattern Staged Unlock Guide

This document explains how to use cmpunlocker's staged unlock approach, which follows the D3DX9 pattern with a mandatory **cold shutdown** (full power-off, not a warm/soft reboot) between stages.

## Overview

The staged unlock approach reduces risk by breaking the complex unlock process into 2 stages:

1. Test basic BAR0 access with PCIe Gen 2 unlock (Stage 1) — runs automatically at every boot via `gen2.service`
2. Open PLM and unlock memory (40GB max, firmware-locked) + compute + features (Stage 2) — runs automatically via the `cmpunlocker` daemon once Stage 1 has been verified

Each stage is resumable from a persistent state file, and you can verify success before trusting the next stage.

**Why a cold shutdown and not just a reboot?** PCIe link speed (Gen 2/3/4/5) is renegotiated during power-on link training. A warm/soft reboot (`reboot`, ACPI restart) can leave the PCIe root complex's existing link-speed state in place on many boards, which would make Stage 1 look like it "didn't take" even though the BAR0 write succeeded. A full power-off (`sudo shutdown -h now`) followed by powering the system back on forces link retraining, so the Gen 2 target actually shows up in `lspci`.

## Why Staged Unlock?

**Single-shot risks:**
- If the complex ROP exploit fails mid-way, you don't know which parts succeeded
- No opportunity to verify the GPU is still responsive after initial writes
- No verification that the exploit actually opened the PLM registers

**Staged approach benefits:**
- Stage 1 is low-risk (just writes to always-accessible XVE register)
- A cold shutdown between stages ensures the link-speed change actually persists and is verifiable
- Explicit verification commands between stages
- If stage 2 fails, you still have stage 1 (Gen 2) working
- Each stage is resumable from a persistent state file

## Quick Start

### Stage 1: PCIe Gen 2 Unlock (Low Risk, Automatic)

Stage 1 runs automatically at every boot via `gen2.service` (installed and enabled by `install.sh`). It is volatile — lost on power cycle — so it must reapply on every boot, not just once.

**What it does:**
- Writes Gen 2 target (0x00000002) to BAR0 XVE register (0x000088)
- Verifies the write stuck
- Saves state to `/var/lib/cmpunlocker/stage_0000:XX:YY.Z`

**To verify it applied:**
```bash
# Do a full power-off, then power back on (not a warm reboot)
sudo shutdown -h now

# After the system is back up, verify Gen 2 is present
lspci -s 0000:XX:YY.Z | grep Speed
# Expected: "Speed 5GT/s" or higher
```

To run Stage 1 manually (e.g. to test before installing the service):
```bash
sudo python3 -m cmpunlocker.daemon.gen2_boot
```

### Stage 2: PLM Opening + Core Unlocks (Medium Risk, Automatic)

Stage 2 runs automatically once the `cmpunlocker` daemon (which starts `After=gen2.service`) sees Stage 1 is complete. To run it manually:

```bash
sudo ./install.sh --stage=2
```

**What it does:**
- Reads current stage from state file (must be ≥ 1)
- Executes ROP exploit to open all 4 PLM registers
- Writes memory unlock (CFG1/LMR) for 40GB (firmware-locked maximum)
- Writes compute unlock (SS0/SS1) for SM clock
- Applies PCIe Gen 3-5 and verified feature unlocks
- Restores original GSP signature (preserves driver integrity)
- Reloads driver with unlocked state in place
- Saves state to stage 2

**After Stage 2:**
```bash
# Cold shutdown, then power back on
sudo shutdown -h now

# After boot, verify unlock is present
nvidia-smi --query-gpu=memory.total --format=csv,noheader
# Expected: ~40960 MiB (40GB, firmware-locked maximum)

nvidia-smi --query-gpu=clocks.max.sm --format=csv,noheader
# Expected: 1410 MHz or higher
```

## Full Unlock (All Stages at Once)

If you want to run all stages in one go without mandatory verification reboots:

```bash
sudo ./install.sh
```

This is equivalent to running a traditional single-shot unlock. **Not recommended** unless you're familiar with the process and confident in your hardware.

## State Persistence

The current unlock stage is stored in:
```
/var/lib/cmpunlocker/stage_0000:XX:YY.Z
```

This file contains a single integer (0-2):
- **0** = No unlock applied
- **1** = Stage 1 complete (Gen 2)
- **2** = Stage 2 complete (40GB + compute + features)

The state persists across reboots and script crashes, allowing resumption at any point.

## Resuming Interrupted Stages

If the script crashes or you interrupt it mid-stage:

```bash
# Check current stage
cat /var/lib/cmpunlocker/stage_0000:XX:YY.Z

# Resume from next incomplete stage
sudo ./install.sh --stage=$((stage + 1))
```

Example: If stage 1 completed but stage 2 crashed:
```bash
sudo ./install.sh --stage=2
```

## Daemon Behavior

The systemd daemon (cmpunlocker.service) respects the staged unlock process:

- **During staged unlock (stage < 2):** Daemon logs warnings and skips auto-reapply
- **After completion (stage = 2):** Daemon monitors GPU state and automatically reapplies unlocks if lost (e.g., driver reload, power fluctuations)

No manual interaction with the daemon is needed; it automatically adapts to the unlock stage.

## Troubleshooting

### Stage X fails to complete

Check the logs:
```bash
journalctl -u gen2 -n 50 -e         # Stage 1
journalctl -u cmpunlocker -n 50 -e  # Stage 2 (auto-run by the daemon)
# or directly running the stage
sudo ./install.sh --stage=2
```

### Cold shutdown didn't persist the unlock

If you power off before a stage records itself complete:

1. Check which stage is marked complete:
   ```bash
   cat /var/lib/cmpunlocker/stage_0000:XX:YY.Z
   ```

2. Run the same stage again (it's safe to re-run):
   ```bash
   sudo ./install.sh --stage=2
   ```

### GPU disappeared after stage 2

This can happen if the ROP exploit had issues or power management interfered. Check:

```bash
lspci -s 0000:XX:YY.Z
# If blank, GPU not detected in firmware

nvidia-smi
# If "No devices", driver not loaded

# Try explicit device enable
echo 1 > /sys/bus/pci/devices/0000:XX:YY.Z/remove
echo 1 > /sys/bus/pci/rescan
```

### Manual stage file recovery

If the stage file is corrupted or lost, reset it:

```bash
# Reset to stage 0 (no unlock)
echo "0" | sudo tee /var/lib/cmpunlocker/stage_0000:XX:YY.Z

# Or set to a specific stage
echo "2" | sudo tee /var/lib/cmpunlocker/stage_0000:XX:YY.Z
```

**Note:** Setting the state file doesn't re-apply unlocks; it just marks a stage as complete. Actual BAR0 unlocks are verified by the daemon.

## Advanced: Environment Variables

```bash
# Override GPU detection (PCI address)
export CMPUNLOCKER_PCI=0000:01:00.0

# Override memory target (default: unlocked_40gb, firmware-locked max)
export CMPUNLOCKER_TARGET=unlocked_40gb
sudo ./install.sh --stage=2

# Daemon check interval (seconds, default: 300)
export CMPUNLOCKER_CHECK_INTERVAL=300
systemctl restart cmpunlocker
```

## Reference: Stage Details

### Stage 1: PCIe Gen 2
- **Register:** BAR0[0x000088] (XVE)
- **Value:** 0x00000002 (5.0 GT/s)
- **Risk:** Very low (doesn't require PLM)
- **Persistence:** Volatile (lost on power cycle) — reapplied every boot by `gen2.service`
- **Purpose:** Test BAR0 access before exploit

### Stage 2: PLM Opening + Core Unlocks
- **Exploit:** Falcon BootROM ROP via modified GSP .fwsignature_ga100
- **Operations:**
  - Open 4 PLM registers (WPR_CFG, FBPA, WPR, FEAT)
  - Write CFG1 for 40GB memory (firmware-locked maximum; 80GB is defined for
    research but is rejected by firmware-level validation)
  - Write LMR (0x0000028A) for memory layout
  - Write SS0 (0x88888888) and SS1 (0x00000008) for compute
- **Risk:** Medium (complex exploit, but highly tested)
- **Persistence:** Volatile (lost on power cycle)
- **Verification:** nvidia-smi shows 40GB + 1410+ MHz

### Stage 3: Feature Unlocks
- **Features:** PCIe Gen 3-5, NVLink, ECC, ARC
- **Risk:** Low (best-effort, doesn't block)
- **Persistence:** Volatile (lost on power cycle)
- **Purpose:** Maximize performance beyond stage 2

## See Also

- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - General troubleshooting
- [SYSTEMD_ARCHITECTURE_ANALYSIS.md](SYSTEMD_ARCHITECTURE_ANALYSIS.md) - Daemon design
- [REBOOT_SHUTDOWN_ARCHITECTURE.md](REBOOT_SHUTDOWN_ARCHITECTURE.md) - State persistence details
