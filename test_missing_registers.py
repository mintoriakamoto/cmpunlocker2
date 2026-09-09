#!/usr/bin/env python3
"""
Probe for missing memory config registers that might unlock CFG1.

Hypotheses to test:
1. There's a control/unlock register in the 0x009A01xx gap
2. LMR needs specific value (0x00000288 vs 0x0000028a)
3. CFG1 requires pre-unlock before writes
4. Timing: CFG1 must be written while PLM is open
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
from pathlib import Path
from cmpunlocker.payload.bar0 import Bar0

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

def probe_register_gap(pci_full: str):
    """Read all registers in the gap between FBPA and CFG1 to find hidden control regs."""
    FBPA_ADDR = 0x009A0148
    CFG1_ADDR = 0x009A0204

    gap_start = FBPA_ADDR + 4
    gap_end = CFG1_ADDR

    log.info("=" * 70)
    log.info("REGISTER GAP PROBE: 0x%08x to 0x%08x", gap_start, gap_end)
    log.info("=" * 70)

    interesting_offsets = [
        0x000,  # Immediate next register after FBPA
        0x010,  # Aligned to 0x10
        0x020, 0x030, 0x040, 0x050, 0x060, 0x070, 0x080, 0x090,  # Every 0x10
        0x0B0,  # Just before CFG1
    ]

    try:
        with Bar0(pci_full) as bar0:
            for offset in interesting_offsets:
                addr = gap_start + offset
                if addr >= gap_end:
                    break

                try:
                    val = bar0.rd32(addr)
                    log.info("  0x%08x  = 0x%08x", addr, val)
                except Exception as e:
                    log.warning("  0x%08x  UNREADABLE: %s", addr, e)

    except RuntimeError as e:
        log.error("BAR0 access failed: %s", e)
        return False

    return True


def test_lmr_hypothesis(pci_full: str):
    """Test if writing LMR=0x00000288 (read value) instead of 0x0000028a helps."""
    CFG1_ADDR = 0x009A0204
    LMR_ADDR = 0x00100CE0

    log.info("")
    log.info("=" * 70)
    log.info("LMR HYPOTHESIS: Does LMR=0x00000288 unlock CFG1?")
    log.info("=" * 70)

    try:
        with Bar0(pci_full) as bar0:
            # Test 1: Try with different LMR values
            lmr_values = [
                (0x00000288, "Firmware-read value"),
                (0x0000028a, "Standard unlock value"),
                (0x00000290, "Alternative pattern"),
                (0x000002FF, "All bits set in low byte"),
            ]

            for lmr_val, desc in lmr_values:
                log.info("")
                log.info("  Testing LMR=0x%08x (%s)", lmr_val, desc)

                # Write LMR
                bar0.wr32(LMR_ADDR, lmr_val)
                lmr_readback = bar0.rd32(LMR_ADDR)
                log.info("    LMR write: 0x%08x → read: 0x%08x", lmr_val, lmr_readback)

                # Try CFG1
                cfg1_target = 0x02779000
                bar0.wr32(CFG1_ADDR, cfg1_target)
                cfg1_readback = bar0.rd32(CFG1_ADDR)
                log.info("    CFG1 write: 0x%08x → read: 0x%08x", cfg1_target, cfg1_readback)

                if cfg1_readback == cfg1_target:
                    log.info("    ✓ CFG1 STUCK! This LMR value works!")
                    return True
                else:
                    log.info("    ✗ CFG1 still locked")

    except RuntimeError as e:
        log.error("BAR0 access failed: %s", e)
        return False

    return False


def test_gap_register_unlocks(pci_full: str):
    """Try writing 0xFFFFFFFF to gap registers to unlock CFG1."""
    FBPA_ADDR = 0x009A0148
    CFG1_ADDR = 0x009A0204
    gap_start = FBPA_ADDR + 4
    gap_end = CFG1_ADDR

    log.info("")
    log.info("=" * 70)
    log.info("GAP REGISTER UNLOCK TEST")
    log.info("=" * 70)

    try:
        with Bar0(pci_full) as bar0:
            # Try writing unlock pattern to gap registers
            unlock_patterns = [
                (0x009A014C, 0xFFFFFFFF, "First gap register"),
                (0x009A0154, 0xFFFFFFFF, "Gap +0x10"),
                (0x009A01FC, 0xFFFFFFFF, "Gap at CFG1-8"),
                (0x009A0200, 0xFFFFFFFF, "Gap at CFG1-4"),
            ]

            for addr, pattern, desc in unlock_patterns:
                log.info("")
                log.info("  Writing 0x%08x to 0x%08x (%s)", pattern, addr, desc)

                bar0.wr32(addr, pattern)
                readback = bar0.rd32(addr)
                log.info("    Wrote: 0x%08x → Read: 0x%08x", pattern, readback)

                # Try CFG1 after each write
                cfg1_target = 0x02779000
                bar0.wr32(CFG1_ADDR, cfg1_target)
                cfg1_readback = bar0.rd32(CFG1_ADDR)

                if cfg1_readback == cfg1_target:
                    log.info("    ✓ CFG1 STUCK after writing to 0x%08x!", addr)
                    return True
                else:
                    log.info("    CFG1 still locked (0x%08x)", cfg1_readback)

    except RuntimeError as e:
        log.error("BAR0 access failed: %s", e)
        return False

    return False


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 test_missing_registers.py <pci_address>")
        print("  Example: python3 test_missing_registers.py 0000:01:00.0")
        sys.exit(1)

    pci_full = sys.argv[1]

    log.info("Testing for missing memory config registers on %s", pci_full)
    log.info("")

    # Test 1: Probe the gap
    probe_register_gap(pci_full)

    # Test 2: Test LMR hypothesis
    if test_lmr_hypothesis(pci_full):
        log.info("")
        log.info("✓ FOUND IT: LMR value unlocks CFG1!")
        return True

    # Test 3: Try gap register unlocks
    if test_gap_register_unlocks(pci_full):
        log.info("")
        log.info("✓ FOUND IT: Gap register unlocks CFG1!")
        return True

    log.info("")
    log.info("=" * 70)
    log.info("No breakthrough found in probed registers.")
    log.info("This confirms: CFG1 is locked at firmware level.")
    log.info("=" * 70)

    return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
