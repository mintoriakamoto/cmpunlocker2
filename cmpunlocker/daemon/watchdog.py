"""
watchdog.py — Monitor loop that reapplies unlocks as needed.

Every CHECK_INTERVAL seconds, checks each GPU:
  1. Is PLM open? If not, run the full unlock.
  2. Is the compute unlock (SS0/SS1) in place? If not, reapply.
  3. Is the memory unlock (CFG1/LMR) in place? If not, reapply.

Reapplying is much faster than the full unlock — it just writes
the values via BAR0.

Uses a lock file to prevent concurrent unlock attempts.
"""

import fcntl
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cmpunlocker.payload.gpu import find_all_gpus
from cmpunlocker.payload.pipeline import run_full_unlock
from unlock.compute import apply_unlock as apply_compute, is_plm_open, is_unlocked
from unlock.memory import apply_unlock as apply_memory, is_memory_unlocked
from unlock.features import apply_feature_unlocks, is_pcie_gen4, is_nvlink_enabled

CHECK_INTERVAL = 1  # seconds
LOCK_FILE = "/var/lock/cmpunlocker.lock"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s cmpunlocker[%(process)d]: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("cmpunlocker")


def _acquire_lock():
    """Acquire an exclusive lock to prevent concurrent unlocks.

    Returns file object if lock acquired, None if another process holds it.
    """
    try:
        # Create lock file with mode 0o644
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            # Try to acquire exclusive lock (non-blocking)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            # Another process holds the lock
            os.close(fd)
            return None
    except OSError as e:
        log.error("Could not create lock file: %s", e)
        return None


def _release_lock(fd):
    """Release the lock and close the file."""
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        except OSError as e:
            log.error("Error releasing lock: %s", e)


def _unlock_card(pci: str) -> None:
    """Run full unlock with lock protection to prevent concurrent attempts."""
    lock_fd = _acquire_lock()
    if lock_fd is None:
        log.info("[%s] Another unlock in progress, skipping", pci)
        return

    try:
        run_full_unlock(pci)
    except Exception as exc:
        log.error("[%s] Full unlock failed: %s", pci, exc)
    finally:
        _release_lock(lock_fd)


def _check_card(pci: str) -> None:
    try:
        if not is_plm_open(pci):
            log.warning("[%s] PLM closed — re-running full unlock", pci)
            _unlock_card(pci)
            return

        if not is_unlocked(pci):
            ok, msg = apply_compute(pci)
            if ok:
                log.info("[%s] Reapplied SS0/SS1", pci)
            else:
                log.warning("[%s] Compute reapply failed: %s", pci, msg)

        if not is_memory_unlocked(pci):
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
    log.info("cmpunlocker daemon starting (PID=%d)", os.getpid())

    gpus = find_all_gpus()
    if not gpus:
        log.error("No compatible GPU found (10de:20b0/20c2/2082)")
        sys.exit(1)

    log.info("Found %d GPU(s): %s", len(gpus), ", ".join(gpus))

    # Run initial unlock for each GPU
    for pci in gpus:
        log.info("[%s] Running initial unlock", pci)
        _unlock_card(pci)

    log.info("Entering monitor loop (interval=%ds)", CHECK_INTERVAL)
    try:
        while True:
            for pci in gpus:
                _check_card(pci)
            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        log.info("Daemon shutting down gracefully")
    except Exception as exc:
        log.error("Unexpected error in main loop: %s", exc)
        sys.exit(1)
    finally:
        # Clean up lock file on shutdown
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
                log.info("Cleaned up lock file")
        except OSError as e:
            log.warning("Could not remove lock file: %s", e)


if __name__ == "__main__":
    main()
