#!/bin/bash
# pcie_gen5_unlock.sh — Enable PCIe Gen 5 x8 capability
#
# Uses PCI Config Space access (setpci) to safely configure Gen 5 after GPU boots.
# This is the safe approach — does NOT interfere with Booter initialization.
#
# Unlike the ROP exploit approach (which broke Booter), this runs AFTER the GPU
# has successfully initialized with unlocked memory/compute.
#
# Usage: sudo ./pcie_gen5_unlock.sh [PCI_BDF]

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info() { echo -e "${CYAN}==>${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*" >&2; }

# Check root
if [ "$EUID" -ne 0 ]; then
    err "Run as root: sudo $0"
    exit 1
fi

# Check setpci
if ! command -v setpci &>/dev/null; then
    err "setpci not found (apt install pciutils)"
    exit 1
fi

# Detect GPU
BDF="${1:-}"
if [ -z "$BDF" ]; then
    BDF=$(lspci -nn 2>/dev/null | grep -iE "10de:(20b0|20c2|2082)" | head -1 | awk '{print $1}')
    if [ -z "$BDF" ]; then
        err "No CMP 170HX found"
        exit 1
    fi
fi
info "GPU BDF: $BDF"

# Verify GPU exists
if ! lspci -s "$BDF" &>/dev/null; then
    err "GPU not found at $BDF"
    exit 1
fi

# PCI Config Space offsets for XVE registers
NV_XVE_DEVICE_CONTROL_STATUS_2=0xA0
NV_XVE_PASSTHROUGH_EMULATED_CONFIG=0xE8

info "=== PCIe Gen 5 x8 Unlock via PCI Config Space ==="
info "Reading current Link Control 2..."
CURRENT=$(setpci -s "$BDF" ${NV_XVE_DEVICE_CONTROL_STATUS_2}.w)
info "  Current value: 0x$CURRENT"
CURRENT_SPEED=$((0x${CURRENT:2:2} & 0xf))
info "  Current speed bits: $CURRENT_SPEED"

# Write Gen 5 (0x05) to bits [3:0]
# Keep other bits, set speed to 5
NEW_VALUE=$((0x${CURRENT} & 0xfff0 | 0x0005))
NEW_HEX=$(printf "%04X" $NEW_VALUE)

info "Setting target speed to Gen 5 (0x05)..."
info "  Writing 0x$NEW_HEX to offset 0x$NV_XVE_DEVICE_CONTROL_STATUS_2..."
setpci -s "$BDF" ${NV_XVE_DEVICE_CONTROL_STATUS_2}.w=$NEW_HEX

# Verify
VERIFY=$(setpci -s "$BDF" ${NV_XVE_DEVICE_CONTROL_STATUS_2}.w)
VERIFY_SPEED=$((0x${VERIFY:2:2} & 0xf))
info "After write: 0x$VERIFY (speed bits: $VERIFY_SPEED)"

if [ "$VERIFY_SPEED" = "5" ]; then
    ok "✓ Gen 5 x8 target set successfully"
    echo ""
    info "Triggering PCIe link retrain..."
    # Set retrain bit (bit 5 of Link Control)
    LC=$(setpci -s "$BDF" 0x88.w)
    setpci -s "$BDF" 0x88.w=$((0x${LC} | 0x20))
    ok "Retrain bit set"

    sleep 2

    info "Verifying link negotiation..."
    NEW_STATUS=$(setpci -s "$BDF" 0x88.w)
    NEW_SPEED=$(( (0x${NEW_STATUS} >> 16) & 0xf ))
    info "  Link status speed: Gen$NEW_SPEED"

    if [ "$NEW_SPEED" = "5" ]; then
        ok "✓ PCIe Gen 5 x8 ACTIVE"
    else
        warn "Target Gen 5 but link negotiated Gen$NEW_SPEED (motherboard limit?)"
    fi
else
    err "✗ Gen 5 write did not stick (got $VERIFY_SPEED)"
    err "  Motherboard may not support Gen 5 in this slot"
    exit 1
fi

echo ""
echo "=== Gen 5 x8 Configuration ==="
echo "✓ GPU: $BDF"
echo "✓ Memory: Check with: nvidia-smi --query-gpu=memory.total --format=csv,noheader"
echo "✓ Compute: Check with: nvidia-smi --query-gpu=clocks.max.sm --format=csv,noheader"
echo "✓ PCIe Gen: Check with: lspci -s $BDF -vv | grep LnkSpd"
