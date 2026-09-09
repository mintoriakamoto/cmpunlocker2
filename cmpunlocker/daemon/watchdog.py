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
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cmpunlocker.payload.gpu import find_all_gpus
from cmpunlocker.payload.pipeline import run_full_unlock
from cmpunlocker.payload.staged_unlock import get_current_stage
from unlock.compute import apply_unlock as apply_compute, is_plm_open, is_unlocked
from unlock.memory import apply_unlock as apply_memory, is_memory_unlocked
from unlock.features import apply_feature_unlocks, is_pcie_gen2, is_pcie_gen3, is_pcie_gen4, is_pcie_gen5, is_nvlink_enabled

CHECK_INTERVAL = int(os.environ.get("CMPUNLOCKER_CHECK_INTERVAL", "1"))  # seconds
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


def _check_card(pci: str, state: dict) -> None:
    try:
        stage = get_current_stage(pci)
        if stage < 2:
            # Staged unlock incomplete, don't attempt reapply
            if state[pci].get("_stage_warning") != stage:
                log.warning("[%s] Unlock incomplete (stage %d/2), skipping reapply", pci, stage)
                state[pci]["_stage_warning"] = stage
            return

        if not is_plm_open(pci):
            log.warning("[%s] PLM closed — re-running full unlock", pci)
            state[pci] = {"plm": False, "compute": False, "memory": False}
            _unlock_card(pci)
            return

        # Track compute unlock state
        compute_ok = is_unlocked(pci)
        if not compute_ok:
            ok, msg = apply_compute(pci)
            if ok:
                log.info("[%s] Reapplied SS0/SS1", pci)
                state[pci]["compute"] = True
            else:
                log.warning("[%s] Compute reapply failed: %s", pci, msg)
                state[pci]["compute"] = False
        elif state[pci].get("compute") == False:
            log.info("[%s] Compute unlock recovered", pci)
            state[pci]["compute"] = True

        # Track memory unlock state
        memory_ok = is_memory_unlocked(pci)
        if not memory_ok:
            ok, msg = apply_memory(pci)
            if ok:
                log.info("[%s] Reapplied memory unlock", pci)
                state[pci]["memory"] = True
            else:
                log.warning("[%s] Memory reapply failed: %s", pci, msg)
                state[pci]["memory"] = False
        elif state[pci].get("memory") == False:
            log.info("[%s] Memory unlock recovered", pci)
            state[pci]["memory"] = True

        # Only check features if core unlocks are in place
        if compute_ok and memory_ok:
            if (not is_pcie_gen2(pci) or not is_pcie_gen3(pci) or not is_pcie_gen4(pci)
                or not is_pcie_gen5(pci) or not is_nvlink_enabled(pci)):
                apply_feature_unlocks(pci)

        state[pci]["plm"] = True

    except Exception as exc:
        log.error("[%s] Monitor error: %s", pci, exc)


def on_shutdown(sig, frame):
    log.info("Shutdown signal received (SIGTERM), exiting gracefully")
    sys.exit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, on_shutdown)
    log.info("cmpunlocker daemon starting (PID=%d)", os.getpid())

    gpus = find_all_gpus()
    if not gpus:
        log.error("No compatible GPU found (10de:20b0/20c2/2082)")
        sys.exit(1)

    log.info("Found %d GPU(s): %s", len(gpus), ", ".join(gpus))

    # Validate BAR0 access before entering monitor loop
    try:
        from cmpunlocker.payload.bar0 import Bar0
        test_pci = gpus[0]
        with Bar0(test_pci) as bar0:
            bar0.rd32(0x0)
        log.info("BAR0 access validated on %s", test_pci)
    except Exception as e:
        log.error("BAR0 access validation failed: %s — check permissions and hardware", e)
        sys.exit(1)

    # Check unlock stage for each GPU and auto-run stage 2 if stage 1 complete
    gpu_stages = {}
    for pci in gpus:
        stage = get_current_stage(pci)
        gpu_stages[pci] = stage

        if stage == 1:
            # Stage 1 complete, auto-run stage 2 (D3DX9 pattern)
            log.info("[%s] Stage 1 complete (Gen 2), auto-running Stage 2 (PLM + 80GB + features)", pci)
            log.info("[%s] Executing full unlock pipeline...", pci)
            _unlock_card(pci)
            log.info("[%s] Stage 2 complete, reboot may be triggered", pci)
        elif stage == 2:
            log.info("[%s] Unlock complete (stage 2/2), monitoring active", pci)
        else:
            log.warning(
                "[%s] Unlock incomplete (stage %d/2). Run 'sudo install.sh --stage=1' to start",
                pci, stage
            )

    log.info("Entering monitor loop (interval=%ds)", CHECK_INTERVAL)
    state = {pci: {"plm": True, "compute": True, "memory": True} for pci in gpus}
    try:
        while True:
            for pci in gpus:
                _check_card(pci, state)
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
