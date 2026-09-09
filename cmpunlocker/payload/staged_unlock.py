"""
staged_unlock.py — D3DX9-pattern 2-stage staged unlock with daemon auto-continuation.

The unlock happens in 2 stages:

STAGE 1: PCIe Gen 2 Only (Manual, Lowest Risk)
  - Write Gen 2 target to BAR0 (doesn't require PLM open)
  - Verify BAR0 write succeeds
  - Full power-off required (user verifies Gen 2 shows in lspci)
  - Purpose: Test BAR0 access before attempting exploit

STAGE 2: PLM Opening + Core + Feature Unlocks (Automatic via Daemon)
  - Execute ROP exploit to open all 4 PLM registers
  - Write memory unlock (CFG1/LMR) for 80GB
  - Write compute unlock (SS0/SS1) for 1410+ MHz
  - Apply all feature unlocks (PCIe Gen 3-5, NVLink, ECC, ARC)
  - Daemon auto-runs this stage after Stage 1 reboot
  - Purpose: Full unlock with all features

Stage 1 is manual (user runs install.sh --stage=1).
Stage 2 is automatic (daemon detects stage 1 complete and runs it).
"""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.constants import get
from payload.preflight import run_preflight, PrefightError
from payload.bar0 import Bar0

log = logging.getLogger(__name__)

STAGE_FILE = "/var/lib/cmpunlocker/stage"


def _ensure_stage_dir():
    """Create stage tracking directory."""
    try:
        Path("/var/lib/cmpunlocker").mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.warning("Could not create stage directory: %s", e)


def _read_stage(pci_full: str) -> int:
    """Read current unlock stage from file (0-3)."""
    _ensure_stage_dir()
    stage_file = f"{STAGE_FILE}_{pci_full}"
    try:
        with open(stage_file, 'r') as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _write_stage(pci_full: str, stage: int) -> None:
    """Write current unlock stage to file."""
    _ensure_stage_dir()
    stage_file = f"{STAGE_FILE}_{pci_full}"
    try:
        with open(stage_file, 'w') as f:
            f.write(str(stage))
    except Exception as e:
        log.warning("Could not write stage file: %s", e)


def stage1_pcie_gen2(pci_full: str) -> bool:
    """Stage 1: PCIe Gen 2 unlock only.

    This is the safest test - writes Gen 2 target to BAR0.
    Doesn't require PLM opening (XVE register is always accessible).
    """
    log.info("[%s] === STAGE 1: PCIe Gen 2 Unlock ===", pci_full)

    # Preflight validation
    try:
        run_preflight(pci_full)
    except PrefightError as e:
        log.error("Preflight failed: %s", e)
        return False

    # Write Gen 2 target to XVE register (doesn't need PLM)
    try:
        with Bar0(pci_full) as bar0:
            gen2_addr = 0x000088
            gen2_val = get('feature_unlocks.pcie_gen2.value')
            bar0.wr32(gen2_addr, gen2_val)
            actual = bar0.rd32(gen2_addr)

        if actual == gen2_val:
            log.info("[%s] ✓ Gen 2 written: 0x%08x = 0x%08x", pci_full, gen2_addr, gen2_val)
            log.info("[%s] === STAGE 1 COMPLETE ===", pci_full)
            log.info("[%s] NEXT: Reboot and verify Gen 2 appears in: lspci -s %s | grep Speed",
                     pci_full, pci_full.split(':')[1])
            _write_stage(pci_full, 1)
            return True
        else:
            log.error("[%s] Gen 2 write failed: got 0x%08x, want 0x%08x",
                      pci_full, actual, gen2_val)
            return False

    except RuntimeError as e:
        log.error("BAR0 access failed: %s", e)
        return False


def stage2_plm_core_unlock(pci_full: str, gsp_path: str = None, target: str = None) -> bool:
    """Stage 2: PLM opening + core unlocks (memory + compute + features).

    After Stage 1 reboot, this runs the full exploit to open PLM registers,
    write memory/compute unlock values, and apply all feature unlocks.
    Combines everything except Gen 2 (which Stage 1 already applied).
    """
    log.info("[%s] === STAGE 2: PLM Opening + Full Core/Feature Unlocks ===", pci_full)

    if gsp_path is None:
        from payload.pipeline import _find_gsp
        gsp_path = _find_gsp()

    if target is None:
        target = get('memory_unlock.default_target')

    # Import here to avoid circular dependency
    from payload.pipeline import run_full_unlock

    # Run the full exploit pipeline (PLM + 80GB + compute + features)
    ok = run_full_unlock(pci_full, gsp_path, target)

    if ok:
        log.info("[%s] === STAGE 2 COMPLETE: Full Unlock Applied ===", pci_full)
        log.info("[%s] All unlocks applied: PLM open, 80GB memory, SM compute, features", pci_full)
        _write_stage(pci_full, 2)
        return True
    else:
        log.error("[%s] Stage 2 failed - PLM opening incomplete", pci_full)
        return False


def get_current_stage(pci_full: str) -> int:
    """Get current unlock stage (0-2)."""
    return _read_stage(pci_full)


def is_stage_complete(pci_full: str, stage: int) -> bool:
    """Check if a given stage is complete."""
    return _read_stage(pci_full) >= stage


def run_next_stage(pci_full: str, gsp_path: str = None, target: str = None) -> bool:
    """Run the next incomplete stage (1 or 2)."""
    current = _read_stage(pci_full)

    if current == 0:
        log.info("Starting from Stage 1")
        return stage1_pcie_gen2(pci_full)
    elif current == 1:
        log.info("Resuming from Stage 2")
        return stage2_plm_core_unlock(pci_full, gsp_path, target)
    else:
        log.info("All stages complete (stage=%d)", current)
        return True
