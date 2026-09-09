"""
watchdog.py — Monitor loop that reapplies unlocks as needed.

Every CHECK_INTERVAL seconds, checks each GPU:
  1. Is PLM open? If not, run the full unlock.
  2. Is the compute unlock (SS0/SS1) in place? If not, reapply.
  3. Is the memory unlock (CFG1/LMR) in place? If not, reapply.

Reapplying is much faster than the full unlock — it just writes
the values via BAR0.
"""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cmpunlocker.payload.gpu import find_all_gpus
from cmpunlocker.payload.pipeline import run_full_unlock
from cmpunlocker.unlock.compute import apply_unlock as apply_compute, is_plm_open, is_unlocked
from cmpunlocker.unlock.memory import apply_unlock as apply_memory, is_memory_unlocked
from cmpunlocker.unlock.features import apply_feature_unlocks, is_pcie_gen4, is_nvlink_enabled

CHECK_INTERVAL = 1  # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s cmpunlocker[%(process)d]: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("cmpunlocker")


def _unlock_card(pci: str) -> None:
    try:
        run_full_unlock(pci)
    except Exception as exc:
        log.error("[%s] Full unlock failed: %s", pci, exc)


def _check_card(pci: str) -> None:
    try:
        # Check if memory/compute are actually unlocked
        mem_ok = is_memory_unlocked(pci)
        compute_ok = is_unlocked(pci)

        if not mem_ok or not compute_ok:
            # Unlock values are missing - need full exploit to re-open PLM
            log.warning("[%s] Unlock values missing (mem=%s compute=%s) — re-running full unlock",
                        pci, mem_ok, compute_ok)
            _unlock_card(pci)
            return

        # Unlock values are still applied - just do light maintenance
        if not compute_ok:
            ok, msg = apply_compute(pci)
            if ok:
                log.info("[%s] Reapplied SS0/SS1", pci)
            else:
                log.warning("[%s] Compute reapply failed: %s", pci, msg)

        if not mem_ok:
            ok, msg = apply_memory(pci)
            if ok:
                log.info("[%s] Reapplied memory unlock", pci)
            else:
                log.warning("[%s] Memory reapply failed: %s", pci, msg)

        if not is_pcie_gen4(pci) or not is_nvlink_enabled(pci):
            apply_feature_unlocks(pci)

    except Exception as exc:
        log.error("[%s] Monitor error: %s", pci, exc)


def main() -> None:
    log.info("cmpunlocker daemon starting")

    gpus = find_all_gpus()
    if not gpus:
        log.error("No compatible GPU found (10de:20b0/20c2/2082)")
        sys.exit(1)

    log.info("Found %d GPU(s): %s", len(gpus), ", ".join(gpus))

    for pci in gpus:
        # Check if unlock values are already present before attempting exploit
        mem_ok = is_memory_unlocked(pci)
        compute_ok = is_unlocked(pci)
        if mem_ok and compute_ok:
            log.info("[%s] Unlock values already present (mem=%s compute=%s), skipping exploit",
                     pci, mem_ok, compute_ok)
        else:
            log.info("[%s] Unlock values missing (mem=%s compute=%s), running initial exploit",
                     pci, mem_ok, compute_ok)
            _unlock_card(pci)

    log.info("Entering monitor loop (interval=%ds)", CHECK_INTERVAL)
    while True:
        for pci in gpus:
            _check_card(pci)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
