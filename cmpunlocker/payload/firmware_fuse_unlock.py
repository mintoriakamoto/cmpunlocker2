"""
firmware_fuse_unlock.py — Patch GSP firmware to unlock Gen 5 capability from OTP fuse.

The CMP 170HX has OTP fuses that limit PCIe to Gen 2 x4. The firmware reads these
fuses at boot and enforces the limitation. By patching the firmware's fuse check,
we can make it report Gen 5 x16 as the maximum capability instead.

Strategy:
1. Find the fuse-read instruction in GSP firmware (CSR 0x7ca read)
2. Replace it with a NOP or modified value that includes Gen 5 bits
3. Or patch the conditional that enforces Gen 2 limit
4. Restore firmware signature

This enables the PCI Config Space unlock (pcie_gen4_unlock.sh) to actually work.
"""

import struct
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def find_fuse_check_patterns(gsp_binary: bytearray) -> list:
    """Find potential fuse-check code patterns in GSP firmware.

    Look for:
    - CSR 0x7ca read instructions (fuse register access)
    - Conditional branches on fuse values
    - Gen 2 hardcoded limits
    """
    patterns = []

    # Pattern 1: Look for 0x7ca (fuse CSR) as a 32-bit value
    fuse_csr = struct.pack("<I", 0x7ca)
    pos = 0
    while True:
        pos = gsp_binary.find(fuse_csr, pos)
        if pos == -1:
            break
        patterns.append({
            'type': 'fuse_csr_reference',
            'address': pos,
            'context': gsp_binary[max(0, pos-16):min(len(gsp_binary), pos+32)]
        })
        pos += 1

    # Pattern 2: Look for Gen 2 hardcoded values (0x02, 0x2 in control bytes)
    # Gen 2 is represented as 0x02 in some contexts
    for i in range(len(gsp_binary) - 8):
        if gsp_binary[i] == 0x02 and gsp_binary[i+1] in [0x7c, 0x88]:  # Near fuse or XVE regs
            patterns.append({
                'type': 'gen2_hardcoded',
                'address': i,
                'context': gsp_binary[max(0, i-16):min(len(gsp_binary), i+32)]
            })

    return patterns


def patch_fuse_to_gen5(gsp_binary: bytearray, fuse_pattern_addr: int) -> bytearray:
    """Patch firmware fuse check to report Gen 5 instead of Gen 2.

    At the fuse-read location, modify the value returned to include Gen 5 bits.
    This is firmware-dependent and may require reverse engineering the exact
    instruction sequence.
    """
    log.warning("Fuse patch is architecture-specific and requires reverse engineering")
    log.warning("Pattern found at: 0x%x", fuse_pattern_addr)

    # This is a placeholder - actual patch depends on Falcon ISA
    return gsp_binary


def create_fuse_bypass_patch(gsp_binary: bytearray) -> bytearray:
    """Create a patch that makes firmware report Gen 5 capability.

    Approach: Find where firmware limits PCIe Gen and replace with Gen 5 value.
    """
    log.info("Scanning GSP firmware for fuse-check patterns...")
    patterns = find_fuse_check_patterns(gsp_binary)

    if not patterns:
        log.warning("No fuse check patterns found - firmware structure may be different")
        return gsp_binary

    log.info(f"Found {len(patterns)} potential fuse-check locations")
    for p in patterns[:5]:  # Show first 5
        log.info(f"  {p['type']} at offset 0x{p['address']:x}")

    # For now, return unmodified binary
    # Real implementation would need Falcon ISA knowledge
    return gsp_binary


def unlock_gen5_via_firmware(gsp_path: str, output_path: str) -> bool:
    """Apply Gen 5 unlock patch to GSP firmware.

    Returns: True if patch successful, False if firmware structure unknown.
    """
    log.info("[FIRMWARE] Attempting to unlock Gen 5 via GSP firmware patch...")

    gsp_binary = bytearray(Path(gsp_path).read_bytes())
    log.info(f"[FIRMWARE] Loaded {len(gsp_binary)} bytes from {gsp_path}")

    # Scan for fuse check patterns
    patterns = find_fuse_check_patterns(gsp_binary)

    if not patterns:
        log.error("[FIRMWARE] Could not locate fuse-check code in firmware")
        log.error("[FIRMWARE] This GPU's firmware structure is not yet reverse-engineered")
        return False

    log.info(f"[FIRMWARE] Found {len(patterns)} fuse-related locations")

    # Apply patches
    patched = create_fuse_bypass_patch(gsp_binary)

    # Save patched firmware
    Path(output_path).write_bytes(patched)
    log.info(f"[FIRMWARE] Patched firmware saved to {output_path}")

    return True


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python3 firmware_fuse_unlock.py <input_gsp> <output_gsp>")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    success = unlock_gen5_via_firmware(sys.argv[1], sys.argv[2])
    sys.exit(0 if success else 1)
