"""
recovery/plm.py — PLM register unlock recovery.

The 11 PLM registers control GPU power domains. After cold boot, they
are locked (all read 0xffffffff). Recovery re-opens them using the
booter exploit.

PLMs persist across reboots because the patched booter rewrites them
on every GSP firmware load. But if the driver is rebuilt without the
patch, or the GSP firmware is corrupted, PLMs lock again.
"""

import glob
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

_GSP_GLOB = "/lib/firmware/nvidia/*/gsp_tu10x.bin"


def check_plm_status(pci_full: str) -> dict:
    """Check if PLM registers are open or locked.

    Returns:
        dict with 'open' (bool), 'count' (int), 'total' (int),
        'registers' (list of dict with name, addr, status)
    """
    from payload.bar0 import Bar0
    from common.constants import get

    plm_table = get('plm_table_40gb')
    results = []
    open_count = 0

    try:
        with Bar0(pci_full) as bar0:
            for entry in plm_table:
                val = bar0.rd32(entry['addr'])
                is_open = val != 0xffffffff
                if is_open:
                    open_count += 1
                results.append({
                    'name': entry['name'],
                    'addr': entry['addr'],
                    'value': val,
                    'open': is_open,
                })
    except Exception as e:
        log.error("Failed to read PLM status: %s", e)
        return {'open': False, 'count': 0, 'total': len(plm_table), 'registers': []}

    return {
        'open': open_count > 0,
        'count': open_count,
        'total': len(plm_table),
        'registers': results,
    }


def recover_plm(pci_full: str, gsp_path: str = None) -> bool:
    """Re-open all PLM registers using the booter exploit.

    This is the core unlock operation. It:
    1. Saves the stock GSP signature
    2. For each PLM register, patches the GSP firmware and triggers booter
    3. Verifies each PLM was opened
    4. Restores the original GSP signature

    Returns True if at least one PLM was opened.
    """
    from payload.pipeline import run_full_unlock

    log.info("Starting PLM recovery for %s", pci_full)

    # Check if PLMs are already open
    status = check_plm_status(pci_full)
    if status['open']:
        log.info("PLMs already open (%d/%d), skipping recovery",
                 status['count'], status['total'])
        return True

    log.info("PLMs locked (%d/%d open), running full unlock pipeline",
             status['count'], status['total'])

    # Run the full unlock pipeline
    ok = run_full_unlock(pci_full, gsp_path)

    if ok:
        # Verify PLMs opened
        status = check_plm_status(pci_full)
        log.info("PLM recovery result: %d/%d open",
                 status['count'], status['total'])
        return status['open']
    else:
        log.error("PLM recovery failed — pipeline returned False")
        return False


def _find_gsp() -> str:
    """Find GSP firmware path."""
    matches = glob.glob(_GSP_GLOB)
    if not matches:
        raise FileNotFoundError("GSP firmware not found")
    return matches[0]
