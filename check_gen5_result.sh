#!/bin/bash
# check_gen5_result.sh — Post-cold-boot Gen 5 unlock diagnostics

set -e

echo "=========================================="
echo "Gen 5 x8 Unlock Verification"
echo "=========================================="
echo ""

# 1. Memory and compute status
echo "=== Memory & Compute Status ==="
nvidia-smi --query-gpu=memory.total,clocks.max.sm --format=csv,noheader 2>/dev/null || echo "nvidia-smi unavailable (GPU initializing?)"
echo ""

# 2. PCIe speed
echo "=== PCIe Speed Status ==="
lspci -s 01:00.0 -vv 2>/dev/null | grep -E "LnkSpd|LnkWid|LnkSta" | head -5 || echo "Could not read PCIe status"
echo ""

# 3. Check if Gen 5 register write happened (BAR0 0x000088 should be 0x5 for Gen 5)
echo "=== Gen 5 Register Status (BAR0 0x000088) ==="
python3 << 'PYEOF'
import sys
sys.path.insert(0, '/home/ai/cmpunlocker2')
sys.path.insert(0, '/home/ai/cmpunlocker2/cmpunlocker')
try:
    from payload.bar0 import Bar0
    with Bar0('0000:01:00.0') as bar0:
        xve_reg = bar0.rd32(0x000088)
        print(f"XVE_LINK_CONTROL_STATUS (0x000088) = 0x{xve_reg:08x}")
        speed = xve_reg & 0xf
        print(f"  → Speed bits [3:0] = {speed}")
        if speed == 5:
            print(f"  ✓ Gen 5 ENABLED")
        elif speed == 4:
            print(f"  ⚠ Gen 4 (Gen 5 failed, fallback?)")
        elif speed == 2:
            print(f"  ⚠ Gen 2 (Gen 5 did not stick)")
        else:
            print(f"  ⚠ Unknown speed: Gen{speed}")
except Exception as e:
    print(f"Could not read BAR0: {e}")
PYEOF
echo ""

# 4. Check system logs for unlock evidence
echo "=== Kernel Log Evidence (last 50 lines) ==="
sudo dmesg | tail -50 | grep -E "Gen 5|PCIe|XVE|PTOP|PLM|unlock" || echo "(No Gen 5 messages found in dmesg)"
echo ""

# 5. Check if daemon ran
echo "=== Daemon Status ==="
if systemctl is-active cmpunlocker > /dev/null 2>&1; then
    echo "✓ Daemon is running"
    sudo journalctl -u cmpunlocker -n 30 --no-pager | grep -E "Gen 5|PCIe|unlock" || echo "(No unlock messages in daemon log)"
else
    echo "✗ Daemon is not running"
fi
echo ""

# 6. Summary
echo "=========================================="
echo "Summary:"
echo "=========================================="
echo "If you see:"
echo "  • XVE register = 0x5 → Gen 5 x8 SUCCESSFUL ✓"
echo "  • XVE register = 0x4 → Gen 4 (Gen 5 write stalled)"
echo "  • XVE register = 0x2 → Gen 2 (Gen 5 not attempted/failed)"
echo "  • memory.total = 81920 → 80GB unlocked ✓"
echo "  • clocks.max.sm = 1410 → compute unlocked ✓"
echo ""
