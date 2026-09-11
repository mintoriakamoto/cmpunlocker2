"""
recovery/driver.py — Driver rebuild recovery.

After a kernel upgrade, the patched nvidia driver may no longer load.
Recovery rebuilds the driver against the new kernel headers.

The patched driver source is in /home/ai/.hermes/cmp_lab/buliaoyin-cmpunlocker/driver/
The build output goes to /lib/modules/$(uname -r)/updates/cmpunlocker/
"""

import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

_DRIVER_SOURCE = Path("/home/ai/.hermes/cmp_lab/buliaoyin-cmpunlocker/driver")
_DRIVER_OUTPUT = Path("/lib/modules/$(uname -r)/updates/cmpunlocker")


def check_driver_status(pci_full: str = None) -> dict:
    """Check if patched driver is loaded and working.

    Returns:
        dict with 'loaded' (bool), 'patched' (bool), 'version' (str),
        'kernel' (str), 'needs_rebuild' (bool)
    """
    result = {
        'loaded': False,
        'patched': False,
        'version': 'unknown',
        'kernel': 'unknown',
        'needs_rebuild': False,
    }

    # Check if nvidia module is loaded
    lsmod = subprocess.run(
        ["lsmod"], capture_output=True, text=True, check=False,
    ).stdout
    if "nvidia" not in lsmod:
        result['needs_rebuild'] = True
        return result

    result['loaded'] = True

    # Check kernel version
    uname = subprocess.run(
        ["uname", "-r"], capture_output=True, text=True, check=False,
    ).stdout.strip()
    result['kernel'] = uname

    # Check if patched driver exists for this kernel
    patched_path = Path(f"/lib/modules/{uname}/updates/cmpunlocker/nvidia.ko")
    if patched_path.exists():
        result['patched'] = True
    else:
        result['needs_rebuild'] = True

    # Check driver version
    modinfo = subprocess.run(
        ["modinfo", "nvidia", "-F", "version"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    if modinfo:
        result['version'] = modinfo

    return result


def check_driver_source() -> dict:
    """Check if driver source exists and is usable.

    Returns:
        dict with 'exists' (bool), 'path' (str), 'has_patches' (bool),
        'has_build_script' (bool)
    """
    result = {
        'exists': _DRIVER_SOURCE.exists(),
        'path': str(_DRIVER_SOURCE),
        'has_patches': False,
        'has_build_script': False,
    }

    if not result['exists']:
        return result

    patches_dir = _DRIVER_SOURCE / "patches"
    result['has_patches'] = patches_dir.exists() and any(patches_dir.glob("*.patch"))

    install_script = _DRIVER_SOURCE / "install.sh"
    result['has_build_script'] = install_script.exists()

    return result


def rebuild_driver() -> bool:
    """Rebuild the patched driver against current kernel.

    Uses buliaoyin's install.sh which:
    1. Applies the Gen2 booter patch
    2. Applies the late retrain patch
    3. Builds the driver
    4. Installs to /lib/modules/$(uname -r)/updates/cmpunlocker/

    Returns True if build succeeded.
    """
    if not _DRIVER_SOURCE.exists():
        log.error("Driver source not found at %s", _DRIVER_SOURCE)
        return False

    install_script = _DRIVER_SOURCE / "install.sh"
    if not install_script.exists():
        log.error("install.sh not found at %s", install_script)
        return False

    log.info("Rebuilding driver from %s", _DRIVER_SOURCE)
    log.info("This may take a few minutes...")

    result = subprocess.run(
        ["sudo", "./install.sh", "--profile=10gb", "--no-iommu"],
        cwd=str(_DRIVER_SOURCE),
        capture_output=True, text=True, check=False,
    )

    if result.returncode != 0:
        log.error("Driver rebuild failed:")
        log.error("STDOUT: %s", result.stdout[-500:] if result.stdout else '')
        log.error("STDERR: %s", result.stderr[-500:] if result.stderr else '')
        return False

    log.info("Driver rebuild succeeded")
    return True


def recover_driver(pci_full: str = None) -> bool:
    """Full driver recovery sequence.

    1. Check current driver status
    2. If driver source exists, rebuild
    3. Load the new driver
    4. Verify it works

    Returns True if driver is working.
    """
    status = check_driver_status(pci_full)

    if status['loaded'] and status['patched'] and not status['needs_rebuild']:
        log.info("Driver already loaded and patched (v%s, kernel %s)",
                 status['version'], status['kernel'])
        return True

    if status['needs_rebuild']:
        log.info("Driver needs rebuild (kernel %s)", status['kernel'])

        # Check if source exists
        src = check_driver_source()
        if not src['exists']:
            log.error("Driver source not found at %s", _DRIVER_SOURCE)
            log.error("Cannot rebuild driver — source missing")
            return False

        if not src['has_build_script']:
            log.error("install.sh not found in driver source")
            return False

        # Rebuild
        if not rebuild_driver():
            return False

    # Load the driver
    log.info("Loading rebuilt driver...")
    result = subprocess.run(
        ["modprobe", "nvidia"], capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        log.error("Failed to load driver: %s", result.stderr.strip())
        return False

    time.sleep(2)

    # Verify
    status = check_driver_status(pci_full)
    if status['loaded'] and status['patched']:
        log.info("Driver recovery successful (v%s)", status['version'])
        return True
    else:
        log.error("Driver recovery failed — not loaded or not patched")
        return False
