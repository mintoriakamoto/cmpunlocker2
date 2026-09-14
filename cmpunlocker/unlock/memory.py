"""
memory.py — Check and apply the memory unlock (CFG1 + LMR).

After the ROP chain opens the 4 PLM registers, the HBM controller
accepts writes to CFG1 (geometry) and LMR (memory rank). This module
performs those writes from the host driver in NS-mode.
"""

import logging
from typing import NamedTuple

from cmpunlocker.payload.bar0 import Bar0
from cmpunlocker.common.constants import get

log = logging.getLogger(__name__)


class UnlockResult(NamedTuple):
    """Standardized result type for unlock operations."""
    success: bool
    message: str


def get_target_values(target: str | None = None) -> tuple[int, int]:
    """Return the (cfg1, lmr) target values for the given memory target."""
    if target is None:
        target = get('memory_unlock.default_target')
    targets = get('memory_unlock.targets')
    if target not in targets:
        raise ValueError(f"unknown memory target: {target}")
    return targets[target]['cfg1'], targets[target]['lmr']


def is_memory_unlocked(pci_full: str, target: str | None = None) -> bool:
    """Check if CFG1 and LMR are currently set to the target values."""
    cfg1_want, lmr_want = get_target_values(target)
    cfg1_addr = get('memory_unlock.cfg1.addr')
    lmr_addr  = get('memory_unlock.lmr.addr')
    with Bar0(pci_full) as bar0:
        cfg1 = bar0.rd32(cfg1_addr)
        lmr  = bar0.rd32(lmr_addr)
    return cfg1 == cfg1_want and lmr == lmr_want


def current_memory_config(pci_full: str) -> dict[str, int | str]:
    """Read the current CFG1 and LMR values and decode them."""
    cfg1_addr = get('memory_unlock.cfg1.addr')
    lmr_addr  = get('memory_unlock.lmr.addr')
    with Bar0(pci_full) as bar0:
        cfg1 = bar0.rd32(cfg1_addr)
        lmr  = bar0.rd32(lmr_addr)

    strap = (cfg1 >> 16) & 0xff
    feature = (cfg1 >> 8) & 0xff

    per_stack_gb = {
        0x44: 2, 0x54: 2, 0x55: 4,
        0x66: 8, 0x70: 8, 0x77: 16,
    }.get(strap, 0)
    stacks = 4 if feature == 0x00 else (5 if feature == 0x90 else 0)
    total_gb = per_stack_gb * stacks

    return {
        "cfg1": cfg1,
        "lmr": lmr,
        "strap": strap,
        "feature": feature,
        "per_stack_gb": per_stack_gb,
        "stacks": stacks,
        "total_gb": total_gb,
    }


def verify_firmware_accepts_unlock(pci_full: str, target: str | None = None) -> tuple[bool, str]:
    """Verify that the firmware actually accepted the unlock request.

    After BAR0 writes complete, the driver loads firmware which may override
    CFG1 to enforce limits. This function detects if a firmware cap was applied.

    Returns: (firmware_accepted, explanation)
    - True: firmware accepted the target (or we're at the firmware limit)
    - False: firmware rejected the unlock or a critical error occurred
    """
    if target is None:
        target = get('memory_unlock.default_target')

    # Query what the firmware is actually reporting
    try:
        config = current_memory_config(pci_full)
        actual_gb = config['total_gb']
    except Exception as e:
        return False, f"Could not query firmware state: {e}"

    # Expected values based on target
    expected_gb_map = {
        'nativ_8gb': 8,
        'nativ_10gb': 10,
        'unlocked_32gb': 32,
        'unlocked_40gb': 40,
        'unlocked_64gb': 40,  # firmware-capped
        'unlocked_80gb': 40,  # firmware-capped
    }
    expected_gb = expected_gb_map.get(target)

    if expected_gb is None:
        return False, f"Unknown target: {target}"

    if actual_gb == expected_gb:
        return True, f"Firmware reports {actual_gb}GB (as expected)"

    # Detect firmware cap
    if target in ('unlocked_80gb', 'unlocked_64gb'):
        if actual_gb == 40:
            return True, f"Firmware capped 80GB→40GB (expected firmware-blocked behavior)"
        elif actual_gb == 32:
            return True, f"Firmware capped 64GB→32GB (expected firmware-blocked behavior)"

    return False, f"Firmware reports {actual_gb}GB but expected {expected_gb}GB for target '{target}'"


def apply_unlock(pci_full: str, target: str | None = None) -> UnlockResult:
    """Apply the memory unlock: write CFG1 and LMR via BAR0.

    Returns UnlockResult with success flag and message.
    NOTE: 80GB and 64GB targets are firmware-blocked at 40GB/32GB max. Writes
    will succeed but firmware will override to the limit. This function detects
    and warns about this behavior.
    """
    if target is None:
        target = get('memory_unlock.default_target')

    cfg1_want, lmr_want = get_target_values(target)
    cfg1_addr = get('memory_unlock.cfg1.addr')
    lmr_addr  = get('memory_unlock.lmr.addr')
    plm_addr  = get('host_bar0_writes.feat_ovr_plm.addr')
    plm_want  = get('host_bar0_writes.feat_ovr_plm.value')

    # Warn if user attempted firmware-blocked target
    firmware_blocked_targets = {
        'unlocked_80gb': 'firmware blocks at 40GB max',
        'unlocked_64gb': 'firmware blocks at 32GB max',
    }
    if target in firmware_blocked_targets:
        log.warning(f"Target '{target}' is firmware-blocked ({firmware_blocked_targets[target]}). Will apply write but expect firmware to cap result.")

    with Bar0(pci_full) as bar0:
        plm = bar0.rd32(plm_addr)
        if plm != plm_want:
            return UnlockResult(False, f"PLM not open (0x{plm:08X} vs 0x{plm_want:08X}) — run full unlock first")

        bar0.wr32(cfg1_addr, cfg1_want)
        bar0.wr32(lmr_addr, lmr_want)
        cfg1 = bar0.rd32(cfg1_addr)
        lmr  = bar0.rd32(lmr_addr)

    if cfg1 == cfg1_want and lmr == lmr_want:
        msg = f"CFG1=0x{cfg1_want:08x} LMR=0x{lmr_want:08x} applied"
        if target in firmware_blocked_targets:
            msg += f" (but firmware will cap to {firmware_blocked_targets[target]})"
        return UnlockResult(True, msg)
    return UnlockResult(False, f"values did not stick (CFG1=0x{cfg1:08x} LMR=0x{lmr:08x})")
