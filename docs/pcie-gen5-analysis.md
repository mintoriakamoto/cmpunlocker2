# PCIe Gen 5 Unlock Analysis - cmpunlocker2

## Current Status
- **PLM IS OPEN** (0xFFFFFFFF) ✓
- **Compute unlocked:** 1410 MHz ✓  
- **Memory unlocked:** 80GB ✓
- **PCIe stuck at:** x4 Gen 2 ✗

## Why Gen 5 x16 Can't Be Unlocked

### Register Access Testing
```
XVE Config Space (0x88):        Reads 0xbadf5040 (invalid)
PTOP_GEN4_STATUS (0x88c20):     Reads 0x00000000 (valid but can't write)
PTOP_GEN4_CTRL (0x88c1c):       Reads 0xd0010026 (write-protected)
```

### Key Finding
- **PCIe link negotiation is hardware-locked**
- Registers can be READ but NOT WRITTEN on a running system
- This prevents any runtime PCIe speed/width changes

### Hardware Capability
```
GPU Firmware Limit:  x4 Gen 2 (OTP fused)
PCIe Bridge (00:01.1) Max: x8 Gen 5 (available but not used)
```

## Root Cause

The CMP 170HX has **OTP fuses that lock PCIe to x4 Gen 2** at the firmware level.
Even with PLM open, the GPU firmware won't negotiate higher speeds.

This is an **intentional NVIDIA anti-repurposing measure** for CMP mining cards.

## Possible Solutions (Advanced)

### Option 1: BIOS Configuration ⭐ EASIEST
- Check motherboard BIOS for PCIe slot settings
- Try to configure slot for x16 instead of x4
- Requires motherboard support (may not help if firmware prevents it)

### Option 2: Firmware Patching 🔴 VERY HARD
- Extract GPU firmware with full ROP exploit
- Locate XVE initialization code
- Patch to enable x16 Gen 5 capability
- Requires deep understanding of GA100 firmware internals
- Could brick the GPU

### Option 3: Different Hardware 💰 PRACTICAL
- Use unrestricted cards: A100 80GB, H100, L40S
- These don't have CMP manufacturing restrictions
- Already ship with full capabilities

## Conclusion

**Gen 5 x16 unlock is not feasible with current reverse-engineering tools.**

The memory and compute unlocks (80GB, 1410 MHz) are already significant wins.
PCIe is a firmware-level restriction, not a register-level one.

Recommend accepting x4 Gen 2 as the hardware limit for CMP 170HX.
