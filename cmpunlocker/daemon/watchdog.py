"""
watchdog.py — Monitor loop with state machine and safe reapply.

The old daemon polled every 1 second and called run_full_unlock()
(multi-second ROP chain) on every miss, causing 1000+ module
load/unload cycles per boot that corrupted GSP firmware.

New design:
  - State machine: IDLE (60s), MONITOR (10s), DEAD (300s)
  - Full exploit ONLY on initial boot when values are missing
  - Monitor loop does light reapply only (BAR0 writes, no ROP)
  - flock() around all BAR0 access to prevent concurrent access
  - Exponential backoff on consecutive failures
  - GPU not found → DEAD state with 300s wait (no crash loop)
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
from cmpunlocker.unlock.compute import apply_unlock as apply_compute, is_unlocked
from cmpunlocker.unlock.memory import apply_unlock as apply_memory, is_memory_unlocked
from cmpunlocker.unlock.features import apply_feature_unlocks, is_pcie_gen4, is_nvlink_enabled

# --- Timing ---
INTERVAL_IDLE = 60       # seconds between checks when stable
INTERVAL_MONITOR = 10    # seconds between checks when values missing
INTERVAL_DEAD = 300      # seconds to wait when GPU not found
BACKOFF_INITIAL = 10     # initial backoff on failure
BACKOFF_MAX = 300        # maximum backoff (5 min)

# --- State machine ---
STATE_IDLE = "idle"       # GPU present, unlock stable
STATE_MONITOR = "monitor" # GPU present, values missing/uncertain
STATE_DEAD = "dead"       # GPU not found

LOCK_PATH = Path("/var/lock/cmpunlocker")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s cmpunlocker[%(process)d]: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("cmpunlocker")


class _Flock:
    """Context manager for advisory file lock around BAR0 access."""

    def __init__(self):
        self._fd = None

    def __enter__(self):
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(LOCK_PATH, "w")
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self._fd:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None


def _light_reapply(pci: str) -> bool:
    """Reapply SS0/SS1 and CFG1/LMR via BAR0 writes.

    Returns True if all values are present after reapply.
    Does NOT run the ROP exploit — that requires PLMs to already be open.
    """
    try:
        with _Flock():
            compute_ok = is_unlocked(pci)
            mem_ok = is_memory_unlocked(pci)

            if compute_ok and mem_ok:
                return True

            # Try light reapply (BAR0 writes)
            if not compute_ok:
                ok, msg = apply_compute(pci)
                if ok:
                    log.info("[%s] Reapplied SS0/SS1", pci)
                else:
                    log.warning("[%s] Compute reapply: %s", pci, msg)

            if not mem_ok:
                ok, msg = apply_memory(pci)
                if ok:
                    log.info("[%s] Reapplied memory unlock", pci)
                else:
                    log.warning("[%s] Memory reapply: %s", pci, msg)

            # Check features
            if not is_pcie_gen4(pci) or not is_nvlink_enabled(pci):
                apply_feature_unlocks(pci)

            # Verify
            return is_unlocked(pci) and is_memory_unlocked(pci)

    except Exception as exc:
        log.error("[%s] Light reapply error: %s", pci, exc)
        return False


def _full_unlock(pci: str) -> bool:
    """Run the full ROP exploit chain. Called only on initial boot."""
    try:
        log.info("[%s] Running full unlock (ROP chain)...", pci)
        run_full_unlock(pci)
        # Verify after exploit
        with _Flock():
            ok = is_unlocked(pci) and is_memory_unlocked(pci)
        if ok:
            log.info("[%s] Full unlock succeeded", pci)
        else:
            log.warning("[%s] Full unlock completed but values not confirmed", pci)
        return ok
    except Exception as exc:
        log.error("[%s] Full unlock failed: %s", pci, exc)
        return False


class GpuMonitor:
    """State machine for a single GPU."""

    def __init__(self, pci: str):
        self.pci = pci
        self.state = STATE_IDLE
        self.consecutive_failures = 0
        self.initial_exploit_done = False

    def _backoff(self) -> float:
        return min(BACKOFF_INITIAL * (2 ** self.consecutive_failures), BACKOFF_MAX)

    def tick(self) -> float:
        """Run one tick. Returns the number of seconds to sleep."""
        # DEAD state: wait for GPU to reappear
        if self.state == STATE_DEAD:
            gpus = find_all_gpus()
            if self.pci in gpus:
                log.info("[%s] GPU reappeared, moving to IDLE", self.pci)
                self.state = STATE_IDLE
                self.consecutive_failures = 0
                self.initial_exploit_done = False
            return INTERVAL_DEAD

        # Check GPU exists
        gpus = find_all_gpus()
        if self.pci not in gpus:
            log.warning("[%s] GPU not found, moving to DEAD (wait %ds)",
                        self.pci, INTERVAL_DEAD)
            self.state = STATE_DEAD
            return INTERVAL_DEAD

        # Initial boot: if values missing, run full exploit once
        if not self.initial_exploit_done:
            with _Flock():
                values_ok = is_unlocked(self.pci) and is_memory_unlocked(self.pci)

            if not values_ok:
                log.info("[%s] Values missing at boot, running full exploit", self.pci)
                if _full_unlock(self.pci):
                    self.initial_exploit_done = True
                    self.state = STATE_IDLE
                    self.consecutive_failures = 0
                    return INTERVAL_IDLE
                else:
                    self.state = STATE_MONITOR
                    self.consecutive_failures += 1
                    return self._backoff()
            else:
                log.info("[%s] Values already present, skipping exploit", self.pci)
                self.initial_exploit_done = True
                self.state = STATE_IDLE
                return INTERVAL_IDLE

        # Monitor: light reapply only (no ROP)
        ok = _light_reapply(self.pci)
        if ok:
            if self.state != STATE_IDLE:
                log.info("[%s] Values restored, moving to IDLE", self.pci)
            self.state = STATE_IDLE
            self.consecutive_failures = 0
            return INTERVAL_IDLE
        else:
            self.state = STATE_MONITOR
            self.consecutive_failures += 1
            if self.consecutive_failures >= 3:
                log.warning("[%s] %d consecutive failures, backing off %ds",
                            self.pci, self.consecutive_failures, self._backoff())
            return self._backoff()


def main() -> None:
    log.info("cmpunlocker daemon starting (state-machine mode)")

    gpus = find_all_gpus()
    if not gpus:
        log.warning("No compatible GPU found, entering DEAD state (wait %ds)", INTERVAL_DEAD)
        # Stay alive — don't sys.exit(1). Just wait and retry.
        while True:
            time.sleep(INTERVAL_DEAD)
            gpus = find_all_gpus()
            if gpus:
                break

    log.info("Found %d GPU(s): %s", len(gpus), ", ".join(gpus))

    monitors = [GpuMonitor(pci) for pci in gpus]

    def _shutdown(signum, frame):
        log.info("Received signal %d, shutting down", signum)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log.info("Entering monitor loop")
    while True:
        shortest = INTERVAL_IDLE
        for mon in monitors:
            wait = mon.tick()
            if wait < shortest:
                shortest = wait
        time.sleep(shortest)


if __name__ == "__main__":
    main()
