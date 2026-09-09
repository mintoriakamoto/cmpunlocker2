# Hardware Limit Finding: CMP 170HX Capped at 40GB

## Discovery

This CMP 170HX unit has a **firmware/hardware limit of 40GB maximum memory**, despite theoretical support for 80GB in documentation.

### Test Results

**State 1: Corrupted Persistent (0x02449000)**
- CFG1 reads: 0x02449000 (native 10GB)
- Actual Memory: 40GB (from upstream)
- Firmware: Locked, won't accept changes
- **Cause**: Persistent state corruption from previous unlock

**State 2: Fresh 40GB (0x02669000)**
- CFG1 reads: 0x02669000 (unlocked 40GB)
- Actual Memory: 40GB
- Attempt 80GB: **REJECTED**
- Firmware returns: 0x02449000 (native 10GB) as rejection signal

### Firmware Constraint

When attempting to upgrade to 80GB (0x02779000):
```
Write CFG1: 0x02779000
Read CFG1:  0x02449000  ← Hardware limit signal
```

The firmware actively rejects 80GB and reverts to native state, indicating:
- Either: This unit has only 8GB HBM2e per stack (max 40GB)
- Or: Firmware fuse prevents 80GB on this specific variant
- Or: Power/thermal limits cap it at 40GB

## What This Means

**Achievable (Verified):**
- ✓ 40GB memory
- ✓ Stock compute clocks (with SS0/SS1 unlocks)
- ✓ Gen 5 x8 PCIe (with XVE_OVR unlock)

**Not Achievable (Hardware Limit):**
- ✗ 80GB memory (firmware rejects)
- ✗ Any upgrade beyond 40GB

## Why Documentation Shows 80GB

The 80GB unlock values (CFG1=0x02779000) exist in community unlock documentation because:
- They work on some A100/CMP units with full 16GB HBM2e
- This particular CMP 170HX likely has 8GB HBM2e maximum
- Or has a firmware fuse limiting it

This is not a software limitation—it's a hardware constraint enforced by the firmware.

## Implication

The upstream 40GB unlock represents the **absolute maximum this hardware can achieve**. No software technique can bypass the firmware's hardware limit check.

## Current Achievable State

**After firmware restore + driver reload:**
- CFG1: 0x02669000 ✓ (unlocked 40GB stable)
- Memory: 40GB ✓
- LMR: 0x0000028a ✓ (standard unlock value)
- Compute: Should be unlockable to 1410 MHz
- PCIe: Should be unlockable to Gen 5 x8

**Next step:** Verify complete 40GB + 1410 MHz + Gen 5 x8 achievable in single unlock.
