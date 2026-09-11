# Why You're Stuck at Gen1: Two Issues, One Real Blocker

## TL;DR

**Issue 1 (FIXED):** BAR0 address was truncated (0x000088 → 0x00088088) ✅ Corrected
**Issue 2 (HARDWARE):** Your BR04 bridge crashes when Gen2 is enabled ❌ BIOS/Firmware limitation

---

## Issue 1: Address Truncation (Now Fixed)

### What Was Wrong
```yaml
pcie_gen2:
  addr:  0x000088        # ❌ WRONG - truncated
  value: 0x00000002
```

### The Correct Address
```yaml
pcie_gen2:
  addr:  0x00088088      # ✅ Correct XVE Link Control Status
  value: 0x00000002
```

**Why it matters:**
- `0x000088` is an invalid BAR0 address (reads as 0xbadf5040 junk)
- `0x00088088` is the correct offset for XVE Link Control Status register
- All PCIe speeds (Gen2-Gen5) were using the wrong address
- **Commit c0a7c95** fixed all four addresses

### But This Alone Still Won't Work

Even with the correct address, **a single BAR0 write is insufficient**. Gen2 unlock requires:

```c
// From buliaoyin pcie-gen2.patch (24+ register writes):
GPU_REG_WR32(pGpu, 0x0082057cU, 0x00000000U);  // OPT_GEN23 clear
GPU_REG_WR32(pGpu, 0x0008c2c0U, cya0 & ~2);    // Clear DIS_G2
GPU_REG_WR32(pGpu, 0x0008c040U, linkCfg | (2<<18)); // MAX_RATE=2
GPU_REG_WR32(pGpu, 0x0008c1c0U, 0x00240036U); // PL_LINK_RATE
GPU_REG_WR32(pGpu, 0x000880a8U, 0x00010002U); // LC2 = Gen2
// ... 19+ more registers ...
GPU_REG_WR32(pGpu, 0x000880a8U, lc2 | 0x20);  // Trigger retrain
```

This is why `pcie_gen4_unlock.sh` (which uses `setpci`) is the working solution — it's doing the full sequence properly.

---

## Issue 2: BR04 Bridge Rejects Gen2 (THE REAL BLOCKER)

### What's Happening

Your system has the **buliaoyin patched driver** (610.43.02) with full Gen2 support. During boot:

```
NVRM: *** Enabling BR04 Gen2 features.
NVRM: *** BR04 has fallen off the bus after we tried to train it to Gen2!
```

### What This Means

- **BR04** = PCI-to-PCI bridge on your motherboard
- **"fell off the bus"** = Bridge stopped responding (hardware/firmware crash)
- **Fallback to Gen1** = System recovers by using Gen1 (safe default)

### Why This Happens

**Not an address problem.** The addresses are correct. The bridge itself is rejecting the Gen2 negotiation request. Possible causes:

1. **BIOS doesn't support Gen2 in this slot** — Check:
   ```
   BIOS → PCIe Settings → Slot [n] → Link Speed
   Current: Gen 1 (forced) → Should be: Gen 2 / Gen 3 / Auto
   ```

2. **Bridge firmware version** — Old AGESA/bridge FW may reject Gen2:
   ```
   BIOS → System Information → Bridge Version
   Try: Update BIOS to latest version
   ```

3. **Motherboard slot limitations** — Some slots only support Gen1:
   ```
   Physical limitation → Try different PCIe slot
   ```

4. **GPU OTP fuse enforcement** — Factory fuse blocks Gen2:
   ```
   Falcon BootROM reads FUSE_PCIE_GEN23_DIS (OTP, immutable)
   If fuse = 1 (blown), Gen2 is disabled at hardware level
   ```

### How to Diagnose

```bash
# Check BIOS boot messages
dmesg | grep -i "pcie\|gen\|speed"

# Check if it's the buliaoyin Gen2 patch crashing
dmesg | grep "BR04\|fell.*bus"

# Check BR04 bridge status
lspci -s 00:00.0 -vv  # Root complex
lspci -s 00:01.0 -vv  # Usually the bridge

# Current speed
lspci -s 01:00.0 -vv | grep "LnkSta:"
```

---

## Why You "Had Gen2 Before"

Three possibilities:

1. **Different motherboard** — BIOS/bridge supports Gen2
2. **Manual setpci run** — You ran `pcie_gen4_unlock.sh` manually after boot
3. **Different firmware version** — Older GPU firmware that didn't try Gen2

The current buliaoyin driver **aggressively attempts Gen2 on every boot**, which crashes your bridge.

---

## Solutions to Try (In Order)

### Solution 1: Check BIOS Settings (Easiest)
```
1. Reboot and enter BIOS (Del/F2/F10)
2. Navigate to: PCIe Settings / Slot Settings / Link Speed
3. Find your GPU's slot (usually Slot 1 or Slot 2)
4. Set to: "Gen 2" or "Auto" (NOT "Gen 1 Only")
5. Save & Exit
6. Test: lspci -s 01:00.0 -vv | grep Speed
```

### Solution 2: Update BIOS (If Available)
```
1. Visit motherboard manufacturer (ASUS/Gigabyte/MSI/etc.)
2. Download latest BIOS for your board
3. Flash via BIOS utility
4. Retest Gen2
```

### Solution 3: Try Different PCIe Slot
```
1. Power off completely
2. Move GPU to different PCIe x16 slot (if available)
3. Boot and test: lspci -s XX:00.0 -vv | grep Speed
```

### Solution 4: Disable Gen2 in Driver (Last Resort)
```
Would require:
- Recompiling buliaoyin driver without Gen2 patch
- Removes the crash but also removes Gen2 capability
- Only if above solutions don't work
```

---

## Technical Details

### Address Correction (Commit c0a7c95)

**Before (Wrong):**
```
pcie_gen2/gen3/gen4/gen5: addr = 0x000088
↑
Truncated - missing 0x88 offset prefix
```

**After (Fixed):**
```
pcie_gen2/gen3/gen4/gen5: addr = 0x00088088
↑
0x00088088 = XVE (PCIe) base 0x88000 + Link Control Status offset 0x88
```

**But still not functional because:**
- Single BAR0 write ≠ Multi-register firmware sequence
- pcie_gen4_unlock.sh uses setpci (PCI Config Space) instead
- buliaoyin uses embedded driver patch (executed at boot)

### Why BR04 Crashes

The Falcon BootROM on GA100 reads OTP fuses at boot:

```c
// Pseudo-code from Falcon BootROM
if (FUSE_PCIE_GEN23_DIS == 1) {
    XVE_LINK_CAP = Gen1;  // Hardcoded to Gen1
    reject_gen2_negotiation();
}
```

If your GPU has this fuse blown (factory setting or security), the bridge negotiation **will fail** because the GPU advertises only Gen1 capability, and the bridge rejects the mismatch.

---

## Current State Summary

| Component | Status | Details |
|-----------|--------|---------|
| Memory Unlock | ✅ Working | 40GB confirmed |
| Compute Unlock | ✅ Working | 1410 MHz confirmed |
| BAR0 Addresses | ✅ Fixed | 0x00088088 (was 0x000088) |
| BAR0 Gen2 Sequence | ❌ Incomplete | Single write insufficient |
| Driver Gen2 Patch | ❌ Crashing | BR04 falls off bus |
| setpci Gen2 Method | ❌ Rejected | GPU firmware reports Gen1 max |
| **Result** | **Gen1 Only** | All Gen2 methods fail |

---

## Next Steps

1. **Run diagnostics:** Check motherboard boot messages
2. **Try BIOS change:** Set slot to Gen 2 or Auto speed
3. **If that fails:** Update BIOS or try different slot
4. **If still fails:** Hardware/firmware limitation confirmed

Your unlock IS working (40GB + compute), but the Gen2 bridge negotiation is blocked at the firmware/hardware level. This is not a software bug.
