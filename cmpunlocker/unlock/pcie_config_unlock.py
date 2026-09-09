"""
pcie_config_unlock.py — Unlock PCIe Gen 5 x16 via PCI Config Space.

This is the COMPLETE Gen 5 unlock method using standard PCI Config Space registers:
- 0xA0 (NV_XVE_DEVICE_CONTROL_STATUS_2): Set target speed
- 0x88 (NV_XVE_LINK_CONTROL_STATUS): Trigger link retrain

This ONLY works if firmware reports Gen 5 as maximum capability (which requires
firmware_fuse_unlock.py to have patched the OTP fuse check first).
"""

import logging
import subprocess
import time

log = logging.getLogger(__name__)


def get_pci_bdf() -> str:
    """Auto-detect CMP 170HX PCI bus:device.function"""
    try:
        result = subprocess.run(
            ["lspci", "-nn"],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.split('\n'):
            if '10de:' in line and any(id in line for id in ['20b0', '20c2', '2082', '220d', '2209']):
                return line.split()[0]
    except Exception as e:
        log.error(f"Failed to detect GPU: {e}")
    return None


def read_pci_config(bdf: str, offset: int) -> int:
    """Read a PCI Config Space register via setpci"""
    try:
        result = subprocess.run(
            ["setpci", "-s", bdf, f"{offset:x}.L"],
            capture_output=True,
            text=True,
            check=True,
        )
        return int(result.stdout.strip(), 16)
    except Exception as e:
        log.error(f"Failed to read PCI {offset:x}: {e}")
        return None


def write_pci_config(bdf: str, offset: int, value: int) -> bool:
    """Write a PCI Config Space register via setpci"""
    try:
        subprocess.run(
            ["setpci", "-s", bdf, f"{offset:x}.L={value:x}"],
            check=True,
            capture_output=True,
        )
        return True
    except Exception as e:
        log.error(f"Failed to write PCI {offset:x}: {e}")
        return False


def unlock_pcie_gen5_config(bdf: str = None) -> bool:
    """Complete PCIe Gen 5 x16 unlock via PCI Config Space.

    Steps:
    1. Detect GPU
    2. Read current capabilities
    3. Set target speed to Gen 5 in Device Control 2
    4. Trigger link retrain
    5. Verify
    """
    if not bdf:
        bdf = get_pci_bdf()
        if not bdf:
            log.error("Could not detect GPU")
            return False

    log.info(f"[PCIE-CONFIG] Unlocking Gen 5 on {bdf}")

    # XVE register offsets in PCI Config Space
    XVE_LINK_CAPABILITIES = 0x84
    XVE_LINK_CONTROL_STATUS = 0x88
    XVE_DEVICE_CONTROL_2 = 0xA0
    XVE_PASSTHROUGH_CONFIG = 0xE8

    # Step 1: Read current state
    log.info("[PCIE-CONFIG] Reading current capabilities...")
    link_cap = read_pci_config(bdf, XVE_LINK_CAPABILITIES)
    if link_cap is None:
        return False

    max_speed = link_cap & 0xf
    max_width = (link_cap >> 4) & 0x3f
    log.info(f"[PCIE-CONFIG] Max Speed: Gen{max_speed}, Max Width: x{max_width}")

    # Step 2: Read device control 2
    dev_ctrl_2 = read_pci_config(bdf, XVE_DEVICE_CONTROL_2)
    if dev_ctrl_2 is None:
        return False
    log.info(f"[PCIE-CONFIG] Device Control 2: 0x{dev_ctrl_2:08x}")

    # Step 3: Set target speed to Gen 5 (value 5)
    target_speed = 5
    new_dev_ctrl = (dev_ctrl_2 & ~0xf) | target_speed
    log.info(f"[PCIE-CONFIG] Setting target speed to Gen {target_speed}...")
    if not write_pci_config(bdf, XVE_DEVICE_CONTROL_2, new_dev_ctrl):
        return False

    # Step 4: Trigger link retrain (set bit 5 in Link Control)
    link_ctrl = read_pci_config(bdf, XVE_LINK_CONTROL_STATUS)
    if link_ctrl is None:
        return False
    log.info(f"[PCIE-CONFIG] Current Link Control: 0x{link_ctrl:08x}")

    # Set bit 5 (retrain bit)
    retrain_bit = 0x20
    new_link_ctrl = link_ctrl | retrain_bit
    log.info("[PCIE-CONFIG] Triggering link retrain...")
    if not write_pci_config(bdf, XVE_LINK_CONTROL_STATUS, new_link_ctrl):
        return False

    # Step 5: Wait for retrain
    log.info("[PCIE-CONFIG] Waiting for link retrain...")
    time.sleep(2)

    # Step 6: Verify
    log.info("[PCIE-CONFIG] Verifying new speed...")
    new_link_ctrl_read = read_pci_config(bdf, XVE_LINK_CONTROL_STATUS)
    if new_link_ctrl_read is None:
        return False

    new_speed = (new_link_ctrl_read >> 16) & 0xf
    new_width = (new_link_ctrl_read >> 0) & 0xff
    log.info(f"[PCIE-CONFIG] New Speed: Gen{new_speed}, Width: x{new_width}")

    if new_speed == target_speed:
        log.info(f"[PCIE-CONFIG] ✓ SUCCESS: PCIe Gen {target_speed} x{new_width} ACTIVE!")
        return True
    else:
        log.error(f"[PCIE-CONFIG] ✗ FAILED: Speed is Gen{new_speed}, expected Gen{target_speed}")
        return False


if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s: %(message)s"
    )
    bdf = sys.argv[1] if len(sys.argv) > 1 else None
    success = unlock_pcie_gen5_config(bdf)
    sys.exit(0 if success else 1)
