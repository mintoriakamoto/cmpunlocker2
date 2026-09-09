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
    Writes PCIe configuration registers that control Gen capability.
    
    Returns:
        True if Gen 5 writes succeeded and stuck, False otherwise.
    """
    log.info("[%s] === PCIe Gen 5 Unlock (x8) ===", pci_full)
    
    try:
        with Bar0(pci_full) as bar0:
            # PTOP_GEN4_CTRL register - try to set Gen 5
            # This is a control register that may allow setting target PCIe Gen
            ptop_gen4_ctrl_addr = 0x88c1c
            
            # Attempt 1: Write 0x05 for Gen 5
            log.info("[%s] Writing PTOP_GEN4_CTRL (0x%06x) = 0x00000005 (Gen 5)", 
                     pci_full, ptop_gen4_ctrl_addr)
            bar0.wr32(ptop_gen4_ctrl_addr, 0x00000005)
            val = bar0.rd32(ptop_gen4_ctrl_addr)
            log.info("[%s] PTOP_GEN4_CTRL read back: 0x%08x", pci_full, val)
            
            if (val & 0xf) == 0x05:
                log.info("[%s] ✓ Gen 5 write stuck!", pci_full)
                return True
            else:
                log.warning("[%s] Gen 5 write did not stick (got 0x%08x)", pci_full, val)
            
            # Attempt 2: Try alternative register addresses
            # NV_XVE_LINK_CONTROL_STATUS at 0x000088 (via BAR0 offset)
            xve_link_ctrl_addr = 0x000088
            log.info("[%s] Trying XVE_LINK_CONTROL_STATUS (0x%06x)", 
                     pci_full, xve_link_ctrl_addr)
            
            current = bar0.rd32(xve_link_ctrl_addr)
            log.info("[%s] XVE current: 0x%08x", pci_full, current)
            
            # Write Gen 5 to bits[3:0]
            new_val = (current & ~0xf) | 0x05
            bar0.wr32(xve_link_ctrl_addr, new_val)
            check = bar0.rd32(xve_link_ctrl_addr)
            log.info("[%s] After write: 0x%08x", pci_full, check)
            
            if (check & 0xf) == 0x05:
                log.info("[%s] ✓ XVE Gen 5 write stuck!", pci_full)
                return True
            
            log.warning("[%s] PCIe Gen 5 unlock attempted but register writes did not stick", 
                        pci_full)
            log.info("[%s] Note: Gen 5 may still be available if GPU firmware supports it", 
                     pci_full)
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
