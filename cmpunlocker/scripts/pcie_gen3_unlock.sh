#!/bin/bash
# pcie_gen3_unlock.sh - Force PCIe Gen 3 (8.0 GT/s) on CMP 170HX
#
# Unlike pcie_gen4_unlock.sh, this script does NOT gate on the GPU's
# reported Link Capabilities. CMP 170HX reports LnkCap=2.5GT/s (Gen1)
# until the XVE feature unlock and retrain sequence completes.
# Checking LnkCap before forcing the target always fails on this card.
#
# Root cause of Gen3/4/5 failure: OTP fuse FUSE_PCIE_GEN23_DIS in
# immutable BootROM hard-caps at Gen2. XVE_OVR@0x8872c accepts writes
# but firmware does not honor them for physical link negotiation.
# This script is EXPERIMENTAL - Gen3 is likely blocked by fuse.
#
# Two approaches:
#   1. PCI Config Space via setpci (NV_XVE_DEVICE_CONTROL_STATUS_2 @ 0xA0)
#      + retrain bit in NV_XVE_LINK_CONTROL_STATUS @ 0x88
#   2. BAR0 XVE write at 0x00088088 = 0x3 (requires PLMs open)
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${CYAN}==>\${NC} $*"; }
ok()   { echo -e "${GREEN}v\${NC} $*"; }
warn() { echo -e "${YELLOW}!\${NC} $*"; }
err()  { echo -e "${RED}x\${NC} $*" >&2; }

if [ "$EUID" -ne 0 ]; then err "Run as root: sudo $0"; exit 1; fi
if ! command -v setpci &>/dev/null; then err "setpci not found (apt install pciutils)"; exit 1; fi

BDF="${1:-}"
if [ -z "$BDF" ]; then
    BDF=$(lspci -nn 2>/dev/null | grep -iE "10de:(20b0|20c2|2082|220d|2209)" | head -1 | awk '{print $1}')
    if [ -z "$BDF" ]; then err "No CMP card found"; exit 1; fi
fi
info "GPU BDF: $BDF"

NV_XVE_LINK_CONTROL_STATUS=0x88
NV_XVE_DEVICE_CONTROL_STATUS_2=0xA0
TARGET_SPEED=3
TARGET_DESC="Gen 3 (8.0 GT/s)"

info "Current link state (informational):"
LINK_CTRL=$(setpci -s "$BDF" ${NV_XVE_LINK_CONTROL_STATUS}.W 2>/dev/null) || LINK_CTRL="0000"
CUR_SPEED=$(( (0x${LINK_CTRL} >> 16) & 0xf )) 2>/dev/null || CUR_SPEED=0
info "  Current negotiated speed: Gen${CUR_SPEED}"

info "=== SETTING TARGET: $TARGET_DESC ==="
DEV_CTRL_2=$(setpci -s "$BDF" ${NV_XVE_DEVICE_CONTROL_STATUS_2}.W 2>/dev/null) || DEV_CTRL_2="0000"
NEW_DEV_CTRL_2=$(( (0x${DEV_CTRL_2} & ~0xf) | TARGET_SPEED ))
info "Writing 0x$(printf '%04x' $NEW_DEV_CTRL_2) to Device Control 2..."
setpci -s "$BDF" ${NV_XVE_DEVICE_CONTROL_STATUS_2}.W=$(printf '%04x' $NEW_DEV_CTRL_2)
ok "Target speed set to Gen ${TARGET_SPEED}"

info "Triggering link retrain (Link Control bit 5)..."
LC=$(setpci -s "$BDF" ${NV_XVE_LINK_CONTROL_STATUS}.W 2>/dev/null) || LC="0000"
setpci -s "$BDF" ${NV_XVE_LINK_CONTROL_STATUS}.W=$(printf '%04x' $(( (0x${LC} | 0x20) & 0xffff )))
ok "Retrain bit set"

info "Waiting 3s for link retrain..."
sleep 3

info "=== VERIFICATION ==="
NEW_LINK_CTRL=$(setpci -s "$BDF" ${NV_XVE_LINK_CONTROL_STATUS}.W 2>/dev/null) || NEW_LINK_CTRL="0000"
NEW_SPEED=$(( (0x${NEW_LINK_CTRL} >> 16) & 0xf )) 2>/dev/null || NEW_SPEED=0
info "New Link Status: 0x${NEW_LINK_CTRL} -> Gen${NEW_SPEED}"

if [ "$NEW_SPEED" -eq "$TARGET_SPEED" ]; then
    ok "SUCCESS: PCIe Gen ${TARGET_SPEED} (8.0 GT/s) active!"
    exit 0
fi

warn "setpci approach did not achieve Gen3 (got Gen${NEW_SPEED}). Trying BAR0 XVE write..."

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if python3 - <<PYEOF 2>/dev/null
import sys
sys.path.insert(0, '$REPO_ROOT')
sys.path.insert(0, '$REPO_ROOT/cmpunlocker')
from payload.bar0 import Bar0
with Bar0('$BDF') as bar0:
    bar0.wr32(0x00088088, 0x3)
    v = bar0.rd32(0x00088088)
    print(f"0x00088088 readback: 0x{v:08x}")
PYEOF
then
    sleep 2
    LC2=$(setpci -s "$BDF" ${NV_XVE_LINK_CONTROL_STATUS}.W 2>/dev/null) || LC2="0000"
    setpci -s "$BDF" ${NV_XVE_LINK_CONTROL_STATUS}.W=$(printf '%04x' $(( (0x${LC2} | 0x20) & 0xffff ))) 2>/dev/null || true
    sleep 3
    FINAL_CTRL=$(setpci -s "$BDF" ${NV_XVE_LINK_CONTROL_STATUS}.W 2>/dev/null) || FINAL_CTRL="0000"
    FINAL_SPEED=$(( (0x${FINAL_CTRL} >> 16) & 0xf )) 2>/dev/null || FINAL_SPEED=0
    if [ "$FINAL_SPEED" -eq "$TARGET_SPEED" ]; then
        ok "SUCCESS via BAR0+retrain: PCIe Gen${TARGET_SPEED} active!"
        exit 0
    fi
    warn "Gen3 via BAR0 also did not stick (Gen${FINAL_SPEED})"
fi

err "Gen 3 unlock failed. Current speed: Gen${NEW_SPEED}"
err "Root cause: OTP fuse FUSE_PCIE_GEN23_DIS in immutable BootROM hard-caps at Gen2."
err "XVE_OVR@0x8872c accepts writes but firmware does not honor them for negotiation."
err "GA100 hardware max is Gen4; Gen5 is Hopper architecture only."
exit 1
