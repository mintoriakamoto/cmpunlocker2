"""
recovery/gsp.py — GSP firmware recovery.

The GSP (GPU System Processor) firmware can become corrupted if:
1. The daemon writes too many RmInit cycles (old 1s watchdog)
2. The patched driver writes garbage to the xp3gTable
3. A power cycle interrupts a firmware write

Recovery restores the stock GSP firmware from backup.
"""

import glob
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_GSP_GLOB = "/lib/firmware/nvidia/*/gsp_tu10x.bin"
_STOCK_BACKUP = "/usr/src/nvidia-610.43.02/nvidia.ko"
_STOCK_FW_BACKUP = "/lib/firmware/nvidia/610.43.02/gsp_tu10x.bin"


def check_gsp_status() -> dict:
    """Check GSP firmware status.

    Returns:
        dict with 'exists' (bool), 'path' (str), 'size' (int),
        'stock_backup' (bool), 'patched' (bool), 'corrupted' (bool)
    """
    result = {
        'exists': False,
        'path': '',
        'size': 0,
        'stock_backup': False,
        'patched': False,
        'corrupted': False,
    }

    # Find current GSP firmware
    matches = glob.glob(_GSP_GLOB)
    if not matches:
        return result

    gsp_path = matches[0]
    result['exists'] = True
    result['path'] = gsp_path
    result['size'] = os.path.getsize(gsp_path)

    # Check for stock backup
    stock_dir = Path("/lib/firmware/nvidia/610.43.02")
    if stock_dir.exists():
        stock_gsp = stock_dir / "gsp_tu10x.bin"
        if stock_gsp.exists():
            result['stock_backup'] = True

    # Check if GSP is patched (has cmpunlocker backup)
    backup_path = gsp_path + ".cmpunlocker.bak"
    if os.path.exists(backup_path):
        result['patched'] = True

    # Check for signs of corruption (size mismatch, unreadable)
    if result['size'] == 0:
        result['corrupted'] = True
    elif result['size'] < 1024 * 1024:  # GSP should be > 1MB
        result['corrupted'] = True

    return result


def recover_gsp(restore_stock: bool = False) -> bool:
    """Recover GSP firmware.

    Args:
        restore_stock: If True, restore from stock backup.
                      If False, restore from cmpunlocker backup.

    Returns True if recovery succeeded.
    """
    status = check_gsp_status()

    if not status['exists']:
        log.error("GSP firmware not found")
        return False

    if status['corrupted']:
        log.warning("GSP firmware appears corrupted (size=%d)", status['size'])

    gsp_path = status['path']

    if restore_stock:
        # Restore from stock backup
        if not os.path.exists(_STOCK_FW_BACKUP):
            log.error("Stock GSP backup not found at %s", _STOCK_FW_BACKUP)
            return False

        log.info("Restoring GSP from stock backup: %s", _STOCK_FW_BACKUP)
        shutil.copy2(_STOCK_FW_BACKUP, gsp_path)
    else:
        # Restore from cmpunlocker backup
        backup_path = gsp_path + ".cmpunlocker.bak"
        if not os.path.exists(backup_path):
            log.error("cmpunlocker backup not found at %s", backup_path)
            log.info("Falling back to stock backup")
            return recover_gsp(restore_stock=True)

        log.info("Restoring GSP from cmpunlocker backup: %s", backup_path)
        shutil.copy2(backup_path, gsp_path)

    # Verify
    new_size = os.path.getsize(gsp_path)
    if new_size > 1024 * 1024:  # > 1MB
        log.info("GSP recovery successful (size=%d)", new_size)
        return True
    else:
        log.error("GSP recovery failed — size=%d", new_size)
        return False


def backup_gsp() -> bool:
    """Create a backup of the current GSP firmware.

    Returns True if backup succeeded.
    """
    status = check_gsp_status()
    if not status['exists']:
        log.error("GSP firmware not found, cannot backup")
        return False

    gsp_path = status['path']
    backup_path = gsp_path + ".cmpunlocker.bak"

    if os.path.exists(backup_path):
        log.info("Backup already exists at %s", backup_path)
        return True

    log.info("Backing up GSP firmware to %s", backup_path)
    shutil.copy2(gsp_path, backup_path)

    # Verify
    if os.path.exists(backup_path) and os.path.getsize(backup_path) == status['size']:
        log.info("GSP backup successful")
        return True
    else:
        log.error("GSP backup failed")
        return False
