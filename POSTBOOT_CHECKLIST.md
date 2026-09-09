# Post-Cold-Boot Gen 5 Unlock Verification

## What happened during shutdown → boot

1. **You shut down the system** (PLM naturally locked as power cycled)
2. **System boots** (GPU cold-starts, PLM opens naturally)
3. **Exploit runs automatically** (via daemon or on driver load)
4. **During ROP chain execution:**
   - Stage 1: Open PLM registers (WPR_CFG, FBPA, WPR, FEAT)
   - **Stage 3: Attempt Gen 5 x8 unlock** (NEW)
     - Writes to PTOP_GEN4_CTRL (0x88c1c) = 0x5 for Gen 5
     - Writes to XVE register (0x000088) = 0x5 for Gen 5
   - Stage 4: Write memory unlock (CFG1, LMR)
   - Stage 5: Write compute unlock (SS0, SS1)
5. **Result stored in GPU hardware** (persists across power cycles)

## How to check the result

### Quick check (2 seconds)
```bash
cd /home/ai/cmpunlocker2
sudo ./check_gen5_result.sh
```

### Manual checks

**Memory unlock:**
```bash
nvidia-smi --query-gpu=memory.total --format=csv,noheader
# Expected: 81920 MiB (80GB)
```

**Compute unlock:**
```bash
nvidia-smi --query-gpu=clocks.max.sm --format=csv,noheader
# Expected: 1410 MHz
```

**PCIe Gen 5 (the new test):**
```bash
lspci -s 01:00.0 -vv | grep -E "LnkSpd|LnkWid"
# Look for "5.0 GT/s" in output (Gen 5 = 5.0 GT/s per lane)
# OR check XVE register directly:
python3 << 'EOF'
import sys
sys.path.insert(0, '/home/ai/cmpunlocker2')
sys.path.insert(0, '/home/ai/cmpunlocker2/cmpunlocker')
from payload.bar0 import Bar0
with Bar0('0000:01:00.0') as bar0:
    xve = bar0.rd32(0x000088)
    print(f"XVE register 0x000088 = 0x{xve:08x}")
    speed = xve & 0xf
    if speed == 5:
        print("✓ Gen 5 ENABLED")
    else:
        print(f"Speed = Gen{speed} (Gen 5 failed)")
EOF
```

## Expected outcomes

| Scenario | Memory | Compute | PCIe | Status |
|----------|--------|---------|------|--------|
| ✅ Success | 80GB | 1410 MHz | Gen 5 x8 | All working |
| ⚠️ Gen 5 failed | 80GB | 1410 MHz | Gen 4 x8 | Fallback, core unlocks OK |
| ❌ Exploit failed | 10GB | 1410 MHz | Gen 2 x16 | Only compute persisted? |
| ❌ Full failure | Native 8-10GB | 1305 MHz | Gen 2 x16 | Factory state |

## If Gen 5 didn't work

**Likely reasons:**
- Motherboard doesn't support Gen 5 (check BIOS for PCIe 5.0 setting)
- Register write didn't stick (hardware limitation)
- PLM didn't open (shouldn't happen on cold boot, but check logs)

**Check logs:**
```bash
sudo dmesg | grep -E "Gen 5|PCIe|XVE|PTOP"
sudo journalctl -u cmpunlocker -n 50 | grep -E "Gen 5|PCIe"
```

## Files involved in this test

- **Exploit code:** `/home/ai/cmpunlocker2/cmpunlocker/payload/pipeline.py`
- **Gen 5 unlock module:** `/home/ai/cmpunlocker2/cmpunlocker/unlock/pcie_gen5.py`
- **Configuration:** `/home/ai/cmpunlocker2/cmpunlocker/common/constants.yaml` (lines 122-128)
- **This script:** `/home/ai/cmpunlocker2/check_gen5_result.sh`

## Next steps

1. Boot system
2. Run `sudo ./check_gen5_result.sh` from `/home/ai/cmpunlocker2/`
3. Report what you see (memory, compute, PCIe speed)
4. If Gen 5 failed, check logs and BIOS
