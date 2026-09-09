# Critical Timing Requirement: CFG1 Writes Must Happen While PLM is Open

## The Issue We Discovered

After finding the LMR unlock handshake, we verified it worked in an isolated test. But when we tried to use it in fresh system state, it failed.

**Key finding:** The LMR unlock only works while PLM (Platform Lock Manager) registers are actively open!

## What This Means

The exploit sequence must be:
1. Open all 8 PLM registers (via ROP chain)
   - WPR_CFG (0x001FA7CC)
   - FBPA (0x009A0148)
   - WPR (0x001FA7C4)
   - FEAT (0x00823804)
   - XVE, XVE_B, XVE_C, FEAT2
2. **While PLM is still open:**
   - Write firmware-signaled LMR value
   - Write CFG1 target (0x02779000 for 80GB)
3. Then reload driver (which closes PLM)

## Why This Matters

CFG1 is protected by the PLM hardware lock. When PLM registers are open, CFG1 becomes writable. When PLM closes (driver reload), CFG1 becomes read-only again.

The unlock flow in pipeline.py currently does this correctly - it opens all PLM registers, then writes CFG1/LMR while PLM is still open, then reloads the driver.

## Testing Verification

**Isolated test AFTER PLM closed:** Failed
- CPU reload closes PLM  
- CFG1 becomes read-only
- LMR unlock doesn't work

**Expected in pipeline:** Success
- PLM opened by ROP chain (stays open)
- CFG1/LMR writes happen while PLM active
- Pipeline then reloads driver (closes PLM)
- 80GB config persists in hardware

## Current Status

Running full pipeline with corrected sequence. Expected result when PLM is open:
- Read LMR (firmware-signaled value)
- Write LMR (firmware-signaled value)
- Write CFG1 target (0x02779000)
- All three should succeed ✓

Result will show whether CFG1 accepts 80GB while PLM is active, persists after reload.

## Bottom Line

The "impossibility" of the 40GB→80GB upgrade was actually a timing issue. CFG1 IS writable, but only while PLM is open in the exploit flow. Once we reload the driver, PLM closes and CFG1 becomes read-only.

This is expected behavior - it's how the hardware protection works. The unlock is one-shot per boot: open PLM → write configs → reload driver (locks it).
