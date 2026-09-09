# GPU Recovery & Gen 5 Test Plan

## Current Status

**GPU State:** Unbootable - Booter error 0x5 on every boot
**Root Cause:** Previous test attempt left GPU with 40GB configuration; firmware Booter cannot initialize
**Solution:** Hardware reset via BIOS to clear persistent GPU state

**Exploit Ready:** Yes
- ✅ 80GB memory unlock (CFG1=0x02779000)
- ✅ 1410 MHz compute (SS0=0x88888888)
- ✅ Gen 5 x8 via correct register (XVE_OVR @ 0x8872c = 0x06)

---

## Step 1: BIOS GPU Reset (Required)

### Procedure
1. **Reboot to BIOS** (press DEL/F2 during POST)
2. **Navigate to:** PCIe Settings or Integrated Peripherals
3. **Find:** "Onboard Video" or "GPU" or "CMP Device"
4. **Disable GPU**
5. **Save & Exit** → Boot to OS
6. **Verify GPU is disabled:**
   ```bash
   lspci | grep -i nvidia
   # Should show nothing
   ```
7. **Shutdown completely** (sudo shutdown -h now)
8. **Power cycle** (wait 10 seconds, power on)
9. **Back to BIOS** → Re-enable GPU
10. **Save & Exit** → Boot normally

### Expected Result
- GPU shows as present in lspci
- nvidia-smi shows "No devices found" initially
- Exploit will run automatically on driver load

---

## Step 2: Automatic Exploit Execution

When you boot after BIOS reset:

1. GPU firmware initializes (fresh state, PLM opens naturally on cold boot)
2. Exploit runs automatically (during driver initialization)
3. Exploit writes:
   - **CFG1 = 0x02779000** (80GB memory)
   - **SS0 = 0x88888888** (1410 MHz compute)
   - **SS1 = 0x00000008** (IMLA4 override)
   - **XVE_OVR = 0x00000006** (Gen 5 x8 capability)
4. Firmware continues initialization with unlocked configuration
5. Driver loads successfully

---

## Step 3: Verify Results

```bash
# Check memory and compute
nvidia-smi --query-gpu=memory.total,clocks.max.sm --format=csv,noheader
# Expected: 81920 MiB (80GB), 1410 MHz

# Check PCIe Gen 5
lspci -s 01:00.0 -vv | grep -E "LnkSpd|LnkWid"
# Look for "32.0 GT/s" or similar Gen 5 speed

# Or read XVE_OVR register directly
python3 << 'EOF'
import sys
sys.path.insert(0, '/home/ai/cmpunlocker2')
sys.path.insert(0, '/home/ai/cmpunlocker2/cmpunlocker')
from payload.bar0 import Bar0
with Bar0('0000:01:00.0') as bar0:
    xve_ovr = bar0.rd32(0x8872c)
    print(f"XVE_OVR @ 0x8872c = 0x{xve_ovr:08x}")
    gen = xve_ovr & 0xf
    print(f"Gen capability: {gen} (5=Gen5 x16, 6=Gen5 x8)")
EOF
```

---

## Possible Outcomes

### ✅ Success
- Memory: 81920 MiB (80GB)
- Compute: 1410 MHz
- PCIe: Gen 5 or Gen 5 x8
- **Result:** Full unlock achieved!

### ⚠️ Partial Success
- Memory: 81920 MiB (80GB) ✅
- Compute: 1410 MHz ✅
- PCIe: Gen 2 or Gen 4 (Gen 5 didn't stick)
- **Result:** Core unlocks work, Gen 5 incompatible with motherboard

### ❌ Booter Still Crashes
- GPU won't boot after BIOS reset
- **Cause:** Firmware incompatibility goes deeper
- **Next step:** Try different firmware version or older BIOS

### ❌ Bootloader Doesn't Reach Exploit
- Firmware initializes but exploit doesn't run
- **Cause:** PLM not opening on this boot
- **Next step:** Check daemon/driver logs, verify PLM opening

---

## What Changed Since Last Attempt

1. **Correct Gen 5 register identified:** 0x8872c (XVE_OVR)
   - Previous attempts: 0x88c1c, 0x000088 (wrong)
   - Should not cause Booter crash

2. **Hardware reset via BIOS**
   - Clears all persistent GPU state
   - GPU boots fresh with no prior unlock interference

3. **80GB targeted directly**
   - Previous: 40GB (intermediate state)
   - Now: 80GB + Gen 5 (full configuration)

---

## Timeline

- **Step 1:** BIOS GPU disable/enable (5 min)
- **Step 2:** Cold boot, exploit runs automatically (30 sec)
- **Step 3:** Verification (2 min)
- **Total:** ~10 minutes

---

## If Things Go Wrong

**Booter still crashes:**
- Try booting without driver load (LiveUSB, headless mode)
- Check if there's a factory BIOS reset option
- Look for firmware downgrade path

**Gen 5 doesn't stick:**
- Motherboard may not support Gen 5 in this slot
- Use Gen 2/Gen 4 unlock script instead
- Gen 5 register is correct but hardware-limited

**GPU not detected after BIOS reset:**
- Re-enter BIOS, verify GPU is enabled
- Check if PCIe slot is active (may need to enable in BIOS)
- Motherboard may have moved GPU to different slot

---

## Success Criteria

✅ GPU boots successfully
✅ 80GB memory reported
✅ 1410 MHz compute clock
✅ Gen 5 x8 enabled (bonus, fallback to Gen 2 if not available)
