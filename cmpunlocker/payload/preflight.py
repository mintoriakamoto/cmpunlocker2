"""
preflight.py — Pre-unlock validation checklist.

Before attempting the full unlock pipeline, verify:
  1. GPU is detected by lspci
  2. Device path exists in sysfs
  3. BAR0 resource is readable
  4. Driver is loaded (nvidia module)
  5. nvidia-smi can query the device
  6. GSP firmware exists and is readable
  7. Running as root
  8. Device ID is supported

This catches common misconfiguration issues early with clear error messages,
preventing cryptic failures mid-unlock.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

from common.constants import get

log = logging.getLogger(__name__)


class PrefightError(Exception):
    """Clear error message for preflight failures."""
    pass


def _check_root() -> None:
    """Ensure running as root."""
    if os.geteuid() != 0:
        raise PrefightError("Must run as root (use: sudo)")


def _check_gpu_in_lspci(pci_full: str) -> None:
    """Verify GPU appears in lspci output."""
    result = subprocess.run(
        ["lspci", "-nn"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise PrefightError("lspci command failed — PCI bus unavailable")

    ids = {f"10de:{did}" for did in get('gpu.device_ids')}
    found = False
    for line in result.stdout.splitlines():
        if pci_full in line and any(dev_id in line for dev_id in ids):
            found = True
            break

    if not found:
        raise PrefightError(
            f"GPU {pci_full} not found in lspci output. "
            "Check: Is GPU installed? Does BIOS/firmware detect it? "
            "Supported IDs: 10de:20b0, 10de:20c2, 10de:2082"
        )


def _check_device_path(pci_full: str) -> None:
    """Verify /sys/bus/pci/devices/{pci}/resource0 exists."""
    sysfs_path = f"/sys/bus/pci/devices/{pci_full}"
    if not os.path.exists(sysfs_path):
        raise PrefightError(
            f"Device path {sysfs_path} not found. "
            "Check: Is driver loaded? Run: lsmod | grep nvidia"
        )


def _check_bar0_readable(pci_full: str) -> None:
    """Verify BAR0 resource is readable (prerequisite for mmap)."""
    bar0_path = f"/sys/bus/pci/devices/{pci_full}/resource0"
    if not os.path.exists(bar0_path):
        raise PrefightError(
            f"BAR0 {bar0_path} not found. "
            "Driver module is not loaded. Run: sudo modprobe nvidia"
        )

    try:
        with open(bar0_path, 'rb') as f:
            # Try to read first 4 bytes
            f.read(4)
    except PermissionError:
        raise PrefightError(
            f"Permission denied reading BAR0 ({bar0_path}). "
            "Check: Running as root? SELinux/AppArmor restrictions?"
        )
    except Exception as e:
        raise PrefightError(f"Cannot read BAR0: {e}")


def _check_driver_module_loaded() -> None:
    """Verify nvidia kernel module is loaded."""
    result = subprocess.run(
        ["lsmod"],
        capture_output=True,
        text=True,
        check=False,
    )
    if "nvidia" not in result.stdout:
        raise PrefightError(
            "nvidia kernel module not loaded. "
            "Run: sudo modprobe nvidia"
        )


def _check_nvidia_smi_sees_device(pci_full: str) -> None:
    """Verify nvidia-smi can query the device."""
    result = subprocess.run(
        ["timeout", "3", "nvidia-smi", "--query-gpu=pci.bus_id",
         "--format=csv,noheader"],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise PrefightError(
            "nvidia-smi cannot query GPU. "
            "Check: Is driver initialized? Run: nvidia-smi"
        )

    if "No devices" in result.stdout or not result.stdout.strip():
        raise PrefightError(
            "nvidia-smi reports 'No devices'. "
            "Driver loaded but GPU not initialized. "
            "Check: BIOS GPU enable? PCIe slot working? "
            "Try: sudo nvidia-smi -L"
        )


def _check_gsp_firmware_exists() -> None:
    """Verify GSP firmware file is present and readable."""
    gsp_pattern = "/lib/firmware/nvidia/*/gsp_tu10x.bin"
    # Use shell glob since Python glob doesn't expand *
    result = subprocess.run(
        ["bash", "-c", f"ls {gsp_pattern} 2>/dev/null"],
        capture_output=True,
        text=True,
        check=False,
    )

    if not result.stdout.strip():
        raise PrefightError(
            "GSP firmware not found. "
            "Install: sudo apt-get install nvidia-kernel-open-common (or your package manager)"
        )

    gsp_path = result.stdout.strip().splitlines()[0]
    if not os.access(gsp_path, os.R_OK):
        raise PrefightError(
            f"GSP firmware not readable: {gsp_path}"
        )


def _check_device_id_supported(pci_full: str) -> str:
    """Read device ID from sysfs and verify it's supported."""
    device_path = f"/sys/bus/pci/devices/{pci_full}/device"
    try:
        with open(device_path, 'r') as f:
            device_hex = f.read().strip()
        device_id = device_hex.replace('0x', '')
    except Exception as e:
        raise PrefightError(f"Cannot read device ID: {e}")

    supported = set(get('gpu.device_ids'))
    if device_id.lower() not in supported:
        raise PrefightError(
            f"Device ID {device_hex} not supported. "
            f"Supported: {', '.join(f'10de:{d}' for d in sorted(supported))}"
        )

    return device_id


def run_preflight(pci_full: str) -> bool:
    """Run all preflight checks. Returns True if all pass.

    Raises PrefightError with clear messages on failure.
    """
    checks = [
        ("Running as root", _check_root),
        ("GPU in lspci", lambda: _check_gpu_in_lspci(pci_full)),
        ("Device in sysfs", lambda: _check_device_path(pci_full)),
        ("BAR0 readable", lambda: _check_bar0_readable(pci_full)),
        ("nvidia module loaded", _check_driver_module_loaded),
        ("nvidia-smi sees device", lambda: _check_nvidia_smi_sees_device(pci_full)),
        ("GSP firmware available", _check_gsp_firmware_exists),
        ("Device ID supported", lambda: _check_device_id_supported(pci_full)),
    ]

    for check_name, check_fn in checks:
        try:
            log.info("Checking: %s", check_name)
            check_fn()
            log.info("  ✓ %s", check_name)
        except PrefightError as e:
            log.error("  ✗ %s", check_name)
            raise

    log.info("All preflight checks passed")
    return True
