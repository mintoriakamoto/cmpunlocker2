#!/usr/bin/env python3
"""
gen2_boot.py — Boot-time entry point for Stage 1 (PCIe Gen 2 unlock).

Run by gen2.service on every boot. PCIe Gen 2 is volatile (lost on power
cycle) so this must reapply on every boot, not just the first. On success
for a GPU, stage1_pcie_gen2() records that GPU's stage as >= 1 so the
cmpunlocker daemon (which starts After=gen2.service) knows it can proceed
to Stage 2.

Exits 0 if Stage 1 succeeds for every detected GPU (or no GPU is present —
nothing to do). Exits 1 if any detected GPU fails Stage 1, so systemd
records gen2.service as failed and dependents ordered with Requires= on it
will not start.
"""

import logging
import sys

from cmpunlocker.payload.gpu import find_all_gpus
from cmpunlocker.payload.staged_unlock import stage1_pcie_gen2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s cmpunlocker-gen2[%(process)d]: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("cmpunlocker.gen2")


def main() -> None:
    gpus = find_all_gpus()
    if not gpus:
        log.info("No compatible GPU found — nothing to do")
        sys.exit(0)

    all_ok = True
    for pci in gpus:
        if not stage1_pcie_gen2(pci):
            log.error("[%s] Stage 1 (PCIe Gen 2) failed", pci)
            all_ok = False

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
