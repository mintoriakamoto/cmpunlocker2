# Recovery Attempts - What Doesn't Work

## Attempts Made

### ❌ 1. Firmware Restore + FLR + Driver Reload
- Restored factory firmware (gsp_tu10x.bin.cmpunlocker.bak)
- Performed FLR reset
- Reloaded nvidia driver
- **Result:** Booter still crashes with 0x5
- **Reason:** GPU hardware has persisted 40GB unlock values; Booter reads these and crashes

### ❌ 2. GA100 Firmware Variant
- Tried switching to ga10x.bin (different firmware for GA100 chips)
- Same reset/reload procedure
- **Result:** GPU doesn't boot at all with GA100 firmware
- **Reason:** Our GPU (CMP 170HX) is TU10X variant, not GA100

### ❌ 3. Aggressive Unload/Load Cycle
- Unloaded nvidia_uvm, nvidia_drm, nvidia_modeset, nvidia
- Reloaded nvidia module
- **Result:** Same Booter 0x5 crash
- **Reason:** Module load doesn't clear GPU hardware state

## What IS Known to Work

### ✅ Before This Session
- GPU booted with 40GB persisted from previous exploit run
- This means: on first unlock, the Booter CAN handle the configuration
- But on SUBSEQUENT boots, Booter crashes
- **Likely cause:** GPU's internal state gets corrupted during unlock process

## The Real Issue

The Booter crash happens because:
1. First exploit run: Writes 40GB values to GPU registers
2. GPU initializes successfully (first time)
3. Values persist in hardware across power cycles
4. **Subsequent boots:** Booter reads persistent 40GB values
5. Something in the Booter's initialization sequence fails
6. **Loop:** Every boot attempt fails with same error

## Solutions Confirmed Won't Work Without BIOS

- ✗ Firmware restore
- ✗ Firmware variants
- ✗ FLR reset
- ✗ Driver reload
- ✗ Module unload/reload
- ✗ PCI reset cycles

## Only Solution

**BIOS GPU disable/enable cycle**
- Disables GPU in BIOS → clears all hardware state
- Re-enables GPU in BIOS → GPU boots fresh with no prior unlocks
- Once fresh boot succeeds, exploit can run again with correct 80GB + Gen 5 config

This is a **hardware-level persistence** issue, not a software one.
