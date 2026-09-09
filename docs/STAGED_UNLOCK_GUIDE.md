# D3DX9-Pattern Staged Unlock Guide

This document explains how to use cmpunlocker's staged unlock approach, which follows the D3DX9 pattern with mandatory verification reboots between stages.

## Overview

The staged unlock approach reduces risk by breaking the complex unlock process into 3 independent stages, each requiring user verification before proceeding. This allows you to:

1. Test basic BAR0 access (Stage 1)
2. Open PLM and unlock memory + compute (Stage 2)
3. Apply optional feature unlocks (Stage 3)

Each stage can be resumed independently if interrupted, and you can verify success before proceeding to the next stage.

## Why Staged Unlock?

**Single-shot risks:**
- If the complex ROP exploit fails mid-way, you don't know which parts succeeded
- No opportunity to verify the GPU is still responsive after initial writes
- No verification that the exploit actually opened the PLM registers

**Staged approach benefits:**
- Stage 1 is low-risk (just writes to always-accessible XVE register)
- Mandatory reboot between stages ensures changes persist
- Explicit verification commands between stages
- If stage 2 fails, you still have stage 1 (Gen 2) working
- Each stage is resumable from a persistent state file

## Quick Start

### Stage 1: PCIe Gen 2 Unlock (Low Risk)

```bash
sudo ./install.sh --stage=1
```

**What it does:**
- Writes Gen 2 target (0x00000002) to BAR0 XVE register (0x000088)
- Verifies the write stuck
- Saves state to `/var/lib/cmpunlocker/stage_0000:XX:YY.Z`
- Prints reboot instructions

**After Stage 1:**
```bash
# Mandatory reboot
sudo reboot

# After reboot, verify Gen 2 is present
lspci -s 0000:XX:YY.Z | grep Speed
# Expected: "Speed 5GT/s" or higher
```

### Stage 2: PLM Opening + Core Unlocks (Medium Risk)

```bash
sudo ./install.sh --stage=2
```

**What it does:**
- Reads current stage from state file (must be ≥ 1)
- Executes ROP exploit to open 4 PLM registers
- Writes memory unlock (CFG1/LMR) for 80GB capacity
- Writes compute unlock (SS0/SS1) for SM clock
- Restores original GSP signature (preserves driver integrity)
- Reloads driver with unlocked state in place
- Saves state to stage 2

**After Stage 2:**
```bash
# Mandatory reboot
sudo reboot

# After reboot, verify unlock is present
nvidia-smi --query-gpu=memory.total --format=csv,noheader
# Expected: ~81378 MiB (80GB+)

nvidia-smi --query-gpu=clocks.max.sm --format=csv,noheader
# Expected: 1410 MHz or higher
```

### Stage 3: Feature Unlocks (Low Risk, Optional)

```bash
sudo ./install.sh --stage=3
```

**What it does:**
- Reads current stage from state file (must be ≥ 2)
- Writes Gen 3-5 PCIe speeds to XVE register
- Applies NVLink, ECC, ARC, and other features
- Marks stage 3 complete
- Best-effort, failures don't block

**After Stage 3:**
```bash
# No reboot required for stage 3
# Verify features are in place
nvidia-smi --query-gpu=clocks.max.sm,memory.total --format=csv,noheader

# Optional: Link retraining for PCIe Gen 4
sudo ./cmpunlocker/scripts/pcie_gen4_unlock.sh
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

This file contains a single integer (0-3):
- **0** = No unlock applied
- **1** = Stage 1 complete (Gen 2)
- **2** = Stage 2 complete (80GB + clock)
- **3** = Stage 3 complete (features)

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

- **During staged unlock (stage < 3):** Daemon logs warnings and skips auto-reapply
- **After completion (stage = 3):** Daemon monitors GPU state and automatically reapplies unlocks if lost (e.g., driver reload, power fluctuations)

No manual interaction with the daemon is needed; it automatically adapts to the unlock stage.

## Troubleshooting

### Stage X fails to complete

Check the logs:
```bash
journalctl -u cmpunlocker -n 50 -e
# or directly running the stage
sudo ./install.sh --stage=X
```

### Reboot didn't persist the unlock

If you reboot before marking a stage complete:

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
sudo ./install.sh --stage=1

# Override memory target (default: unlocked_80gb)
export CMPUNLOCKER_TARGET=unlocked_80gb
sudo ./install.sh --stage=2

# Daemon check interval (seconds, default: 1)
export CMPUNLOCKER_CHECK_INTERVAL=5
systemctl restart cmpunlocker
```

## Reference: Stage Details

### Stage 1: PCIe Gen 2
- **Register:** BAR0[0x000088] (XVE)
- **Value:** 0x00000002 (5.0 GT/s)
- **Risk:** Very low (doesn't require PLM)
- **Persistence:** Volatile (lost on power cycle)
- **Purpose:** Test BAR0 access before exploit

### Stage 2: PLM Opening + Core Unlocks
- **Exploit:** Falcon BootROM ROP via modified GSP .fwsignature_ga100
- **Operations:**
  - Open 4 PLM registers (WPR_CFG, FBPA, WPR, FEAT)
  - Write CFG1 (0x02779000) for 80GB memory
  - Write LMR (0x0000028A) for memory layout
  - Write SS0 (0x88888888) and SS1 (0x00000008) for compute
- **Risk:** Medium (complex exploit, but highly tested)
- **Persistence:** Volatile (lost on power cycle)
- **Verification:** nvidia-smi shows 80GB + 1410+ MHz

### Stage 3: Feature Unlocks
- **Features:** PCIe Gen 3-5, NVLink, ECC, ARC
- **Risk:** Low (best-effort, doesn't block)
- **Persistence:** Volatile (lost on power cycle)
- **Purpose:** Maximize performance beyond stage 2

## See Also

- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - General troubleshooting
- [SYSTEMD_ARCHITECTURE_ANALYSIS.md](SYSTEMD_ARCHITECTURE_ANALYSIS.md) - Daemon design
- [REBOOT_SHUTDOWN_ARCHITECTURE.md](REBOOT_SHUTDOWN_ARCHITECTURE.md) - State persistence details
