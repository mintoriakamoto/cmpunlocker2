#!/usr/bin/env python3
"""
staged_unlock_cli.py — Command-line interface for D3DX9-pattern staged unlock.

Usage:
  staged_unlock.py --stage=1 --pci=0000:01:00.0 --gsp=/lib/firmware/nvidia/550/gsp_tu10x.bin --target=unlocked_80gb
  staged_unlock.py --stage=2 --pci=0000:01:00.0 --gsp=/lib/firmware/nvidia/550/gsp_tu10x.bin --target=unlocked_80gb
"""

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from payload.staged_unlock import (
    stage1_pcie_gen2, stage2_plm_core_unlock,
    get_current_stage, is_stage_complete, run_next_stage
)

log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="D3DX9-pattern staged GPU unlock with mandatory verification reboots"
    )
    parser.add_argument("--stage", type=int, choices=[1, 2],
                        help="Run specific stage (1 or 2)")
    parser.add_argument("--pci", required=True, help="PCI address (e.g., 0000:01:00.0)")
    parser.add_argument("--gsp", required=True, help="Path to GSP firmware file")
    parser.add_argument("--target", default="unlocked_80gb",
                        help="Memory unlock target (default: unlocked_80gb)")
    parser.add_argument("--auto", action="store_true",
                        help="Run next incomplete stage automatically (skip manual stage selection)")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    pci_full = args.pci
    gsp_path = args.gsp
    target = args.target

    # Validate inputs
    if not os.path.exists(gsp_path):
        log.error("GSP firmware not found: %s", gsp_path)
        sys.exit(1)

    # Determine which stage to run
    if args.auto:
        # Auto-run next incomplete stage
        ok = run_next_stage(pci_full, gsp_path, target)
        sys.exit(0 if ok else 1)
    elif args.stage:
        # Run specific stage
        current = get_current_stage(pci_full)

        # Validate stage progression
        if args.stage > 1 and current < args.stage - 1:
            log.error(
                "Cannot run stage %d: stage %d not yet complete. "
                "Run stages in order: stage 1 → stage 2",
                args.stage, args.stage - 1
            )
            sys.exit(1)

        if args.stage == 1:
            ok = stage1_pcie_gen2(pci_full)
        elif args.stage == 2:
            ok = stage2_plm_core_unlock(pci_full, gsp_path, target)
        else:
            log.error("Invalid stage: %d", args.stage)
            sys.exit(1)
        sys.exit(0 if ok else 1)
    else:
        # No stage specified and not in auto mode
        current = get_current_stage(pci_full)
        log.info("Current unlock stage: %d", current)
        log.info("Specify --stage=1/2 or use --auto to continue")
        sys.exit(1)


if __name__ == "__main__":
    main()
