"""
cmpunlocker — NVIDIA CMP 170HX (GA100) unlock tooling.

This package provides tools to restore full A100 compute throughput on CMP 170HX
mining cards by exploiting the Falcon BootROM .fwsignature_ga100 load bug.

Two-Stage Unlock Pattern (D3DX9):
  Stage 1 (Kernel driver): PCIe Gen 2 test unlock (low-risk, tests driver stability)
  Stage 2 (Daemon): Full PLM + memory + compute + optional features (requires Stage 1)

Core Modules:
  - payload.gpu: GPU discovery and device enumeration
  - payload.bar0: GPU BAR0 register space access via mmap
  - unlock.compute: Compute unlock (SS0/SS1 FEAT_OVR_SM_SPD registers)
  - unlock.memory: Memory unlock (CFG1/LMR HBM controller configuration)
  - unlock.features: Optional feature unlocks (PCIe Gen 3-5, NVLink, ECC)
  - daemon.watchdog: Persistence daemon that reapplies unlocks after reboots

Key Limitations:
  - Firmware-locked memory ceiling at 40GB (not 80GB despite PLM availability)
  - RCU locking violation in high-frequency polling (kernel panic risk)
  - Falcon corruption after ~10-15 system reboots (GPU becomes unbootable)
  - Unverified features (NVLink, ECC, ARC) are experimental, not tested

See CRITICAL_ISSUES.md for detailed analysis of limitations and workarounds.
"""