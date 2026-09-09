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
    Writes PCIe XVE OVERRIDE register that controls Gen capability.

    Target register: 0x8872c (XVE_OVR) - the firmware's override register for PCIe Gen
    Observed in kernel logs: "PCIe XVE_OVR@8872c=0x00000006"

    Values:
    - 0x05: Gen 5 (32.0 GT/s)
    - 0x06: Gen 5 x8 (per firmware debug output)

    Returns:
        True if Gen 5 writes succeeded and stuck, False otherwise.
    """
    log.info("[%s] === PCIe Gen 5 Unlock (x8) ===", pci_full)

    try:
        with Bar0(pci_full) as bar0:
            # XVE_OVR register at 0x8872c - the firmware's PCIe Gen override register
            # Kernel logs show this being set to 0x00000006 for Gen 5 x8
            xve_ovr_addr = 0x8872c

            log.info("[%s] Writing XVE_OVR (0x%06x) with Gen 5 x8 value",
                     pci_full, xve_ovr_addr)

            # Read current value
            current = bar0.rd32(xve_ovr_addr)
            log.info("[%s] XVE_OVR current: 0x%08x", pci_full, current)

            # Firmware logs show 0x00000006 for Gen 5 x8
            # Try writing 0x06 first
            bar0.wr32(xve_ovr_addr, 0x00000006)
            val = bar0.rd32(xve_ovr_addr)
            log.info("[%s] XVE_OVR after write: 0x%08x", pci_full, val)

            if val == 0x00000006:
                log.info("[%s] ✓ Gen 5 x8 enabled on XVE_OVR!", pci_full)
                return True

            # Try Gen 5 x16 value
            bar0.wr32(xve_ovr_addr, 0x00000005)
            val2 = bar0.rd32(xve_ovr_addr)
            log.info("[%s] XVE_OVR Gen5 x16 attempt: 0x%08x", pci_full, val2)

            if val2 == 0x00000005:
                log.info("[%s] ✓ Gen 5 (x16) enabled", pci_full)
                return True

            log.warning("[%s] Gen 5 write did not stick on XVE_OVR", pci_full)
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
