"""
pcie_gen5.py — Unlock PCIe Gen 5 x8 capability during ROP exploit.

This module writes PCIe configuration registers while PLM is open (during ROP exploit),
enabling the GPU to negotiate Gen 5 x8 speeds with the motherboard.

Strategy:
  1. PLM is open (ROP exploit succeeded)
  2. Write PCIe Gen 5 capability registers via BAR0
  3. These persist across firmware signature restoration
  4. GPU firmware init sees Gen 5 enabled and negotiates accordingly

Target: Gen 5 x8 (32 GB/s) on motherboards that support it
"""

import logging
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from payload.bar0 import Bar0
from common.constants import get

log = logging.getLogger(__name__)


def unlock_pcie_gen5(pci_full: str) -> bool:
    """
    Unlock PCIe Gen 5 x8 capability.

    Must be called WHILE PLM IS OPEN (during ROP exploit execution).
    Writes PCIe XVE configuration register that controls Gen capability.

    Target register: 0x88ff4 (XVE) - observed in kernel logs as PLM[4]

    Returns:
        True if Gen 5 writes succeeded and stuck, False otherwise.
    """
    log.info("[%s] === PCIe Gen 5 Unlock (x8) ===", pci_full)

    try:
        with Bar0(pci_full) as bar0:
            # XVE register at 0x88ff4 - this is the register the firmware
            # actually tries to open during initialization (from kernel logs)
            xve_addr = 0x88ff4

            log.info("[%s] Writing XVE (0x%06x) with Gen 5 capability",
                     pci_full, xve_addr)

            # Read current value first
            current = bar0.rd32(xve_addr)
            log.info("[%s] XVE current: 0x%08x", pci_full, current)

            # Write Gen 5 (0x5) to the speed bits
            # Try writing 0x05 directly first
            bar0.wr32(xve_addr, 0x00000005)
            val = bar0.rd32(xve_addr)
            log.info("[%s] XVE after write: 0x%08x", pci_full, val)

            if (val & 0xf) == 0x05:
                log.info("[%s] ✓ Gen 5 enabled on XVE register!", pci_full)
                return True

            # Try alternative: merge Gen 5 with current value
            new_val = (current & ~0xf) | 0x05
            bar0.wr32(xve_addr, new_val)
            check = bar0.rd32(xve_addr)
            log.info("[%s] XVE after merge: 0x%08x", pci_full, check)

            if (check & 0xf) == 0x05:
                log.info("[%s] ✓ Gen 5 enabled (merged with current)", pci_full)
                return True

            log.warning("[%s] Gen 5 write did not stick on XVE register", pci_full)
            return False

    except Exception as e:
        log.error("[%s] PCIe Gen 5 unlock failed: %s", pci_full, e)
        return False


def check_pcie_gen5(pci_full: str) -> bool:
    """Check if PCIe Gen 5 is currently enabled."""
    try:
        with Bar0(pci_full) as bar0:
            addr = 0x88c1c
            val = bar0.rd32(addr)
            is_gen5 = (val & 0xf) == 0x05
            log.info("[%s] PCIe Gen capability check: 0x%08x (Gen %d)", 
                     pci_full, val, val & 0xf)
            return is_gen5
    except Exception as e:
        log.warning("[%s] Could not check PCIe Gen: %s", pci_full, e)
        return False
