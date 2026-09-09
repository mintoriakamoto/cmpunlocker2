#!/bin/bash
# cmpunlocker install.sh — D3DX9-pattern staged installer with mandatory reboots
set -euo pipefail

INSTALL_DIR="/opt/cmpunlocker"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE=""

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
else
    RED=""; GREEN=""; YELLOW=""; CYAN=""; NC=""
fi

info() { echo -e "${CYAN}==>${NC} $*"; }
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*" >&2; }

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --stage=*)
            STAGE="${1#--stage=}"
            if [[ ! "$STAGE" =~ ^[1-3]$ ]]; then
                err "Invalid stage: $STAGE. Must be 1, 2, or 3."
                exit 1
            fi
            ;;
        --help)
            echo "Usage: sudo $0 [OPTIONS]"
            echo ""
            echo "D3DX9-pattern staged unlock with mandatory verification reboots:"
            echo ""
            echo "Options:"
            echo "  --stage=1    Run Stage 1 only (PCIe Gen 2 unlock)"
            echo "  --stage=2    Run Stage 2 only (PLM opening + core unlocks)"
            echo "  --stage=3    Run Stage 3 only (Feature unlocks)"
            echo "  (no option)  Run full unlock pipeline (all stages in one go)"
            echo "  --help       Show this help message"
            echo ""
            echo "RECOMMENDED: Staged approach for verification:"
            echo "  1. sudo $0 --stage=1        # Reboot and verify Gen 2"
            echo "  2. sudo $0 --stage=2        # Reboot and verify 80GB + clock"
            echo "  3. sudo $0 --stage=3        # Optional feature unlocks"
            echo ""
            echo "Environment variables:"
            echo "  CMPUNLOCKER_PCI=0000:XX:YY.Z  Override GPU detection"
            echo "  CMPUNLOCKER_TARGET=unlocked_80gb  Memory target (default)"
            exit 0
            ;;
        *)
            err "Unknown option: $1"
            exit 1
            ;;
    esac
    shift
done

if [ "$EUID" -ne 0 ]; then
    err "Run as root: sudo $0"
    exit 1
fi

info "Step 1/6: Verifying environment"
if ! command -v python3 &>/dev/null; then err "python3 not found"; exit 1; fi
if ! python3 -c "import yaml" 2>/dev/null; then
    pip install pyyaml 2>&1 | tail -3
fi
ok "Environment OK"

info "Step 2/6: Detecting GPU"
# Support for CMP 170HX (GA100), 90HX (GH100), 50HX (GH100)
# From ecosystem research: pearlfortune extends hardware support

# Try environment variable override first
if [ -n "${CMPUNLOCKER_PCI:-}" ]; then
    PCI_FULL="$CMPUNLOCKER_PCI"
    info "Using PCI from CMPUNLOCKER_PCI: $PCI_FULL"
else
    PCI=""
    # Try lspci first
    if command -v lspci &>/dev/null; then
        PCI=$(lspci -nn 2>/dev/null | grep -iE "10de:(20b0|20c2|2082|220d|2209)" | head -1 | awk '{print $1}')
    fi
    # Fall back to sysfs search
    if [ -z "$PCI" ]; then
        for dev in /sys/bus/pci/devices/*/; do
            vendor=$(cat "$dev/vendor" 2>/dev/null)
            device=$(cat "$dev/device" 2>/dev/null)
            if [ "$vendor" = "0x10de" ] && [ "$device" = "0x2082" ]; then
                PCI=$(basename "$dev")
                break
            fi
        done
    fi
    if [ -z "$PCI" ]; then
        err "No CMP card found via lspci or sysfs"
        echo "  Supported: CMP 170HX (10de:20b0/20c2/2082)"
        echo "  Supported: CMP 90HX (10de:220d)"
        echo "  Supported: CMP 50HX (10de:2209)"
        echo ""
        echo "  Manual override: export CMPUNLOCKER_PCI=0000:XX:YY.Z"
        exit 1
    fi
    PCI_FULL="0000:${PCI}"
    # Remove domain prefix if already present (01:00.0 -> 01:00.0, 0000:01:00.0 -> 01:00.0)
    PCI_FULL=$(echo "$PCI_FULL" | sed 's/^0000://')
    PCI_FULL="0000:${PCI_FULL}"
fi

# Extract device ID from sysfs
if [ -f "/sys/bus/pci/devices/$PCI_FULL/device" ]; then
    DEVICE_HEX=$(cat "/sys/bus/pci/devices/$PCI_FULL/device" 2>/dev/null)
    GPU_ID=$(echo "$DEVICE_HEX" | sed 's/^0x//')
else
    GPU_ID="unknown"
fi
case "$GPU_ID" in
  20b0|20c2|2082) GPU_NAME="CMP 170HX (GA100)" ;;
  220d) GPU_NAME="CMP 90HX (GH100)" ;;
  2209) GPU_NAME="CMP 50HX (GH100)" ;;
  *) GPU_NAME="Unknown CMP" ;;
esac
ok "GPU: ${PCI_FULL} – ${GPU_NAME}"

info "Step 3/6: Verifying driver compatibility"
# 610.x family (610.43.02+): Falcon BootROM ROP exploit compatible
DRIVER_VERSION=$(timeout 3 nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | grep -v "^No devices" | head -1)
if [ -z "$DRIVER_VERSION" ]; then
    warn "Could not detect driver version (nvidia-smi failed or not found)"
else
    DRIVER_MAJOR=$(echo "$DRIVER_VERSION" | cut -d. -f1)
    case "$DRIVER_MAJOR" in
        61) ok "Driver ${DRIVER_VERSION} (610.x verified compatible)" ;;
        *) warn "Driver ${DRIVER_VERSION} (may not work, 610.x recommended)" ;;
    esac
fi

info "Step 4/6: Locating GSP firmware"
GSP_PATH=$(ls /lib/firmware/nvidia/*/gsp_tu10x.bin 2>/dev/null | sort -rV | head -1)
[ -z "$GSP_PATH" ] && err "No GSP firmware found" && exit 1
ok "GSP: $GSP_PATH"

info "Step 4/6: Installing to ${INSTALL_DIR}"
rm -rf "${INSTALL_DIR}"
cp -r "${SCRIPT_DIR}" "${INSTALL_DIR}"
ok "Installed"

info "Step 5/6: Running unlock"
TARGET="${CMPUNLOCKER_TARGET:-unlocked_80gb}"

# Determine which unlock method to use
if [ -n "$STAGE" ]; then
    # Staged unlock approach
    python3 "${INSTALL_DIR}/cmpunlocker/payload/staged_unlock_cli.py" \
        --stage="$STAGE" --pci="${PCI_FULL}" --gsp="${GSP_PATH}" --target="${TARGET}"
    ok "Stage $STAGE unlock applied"

    # Print stage-specific instructions
    case "$STAGE" in
        1)
            echo
            echo -e "${CYAN}╔════════════════════════════════════════════════════════════════════╗${NC}"
            echo -e "${CYAN}║${NC}   ${GREEN}✓ STAGE 1 COMPLETE: PCIe Gen 2 Unlock${CYAN}                   ║${NC}"
            echo -e "${CYAN}╚════════════════════════════════════════════════════════════════════╝${NC}"
            echo
            echo -e "${YELLOW}!${NC} MANDATORY: Reboot now for Gen 2 to persist:"
            echo "  sudo reboot"
            echo
            echo "After reboot, verify Gen 2 is present:"
            echo "  lspci -s ${PCI_FULL} | grep Speed"
            echo "  # Expected: 'Speed 5GT/s' or higher"
            echo
            echo "Then proceed to Stage 2:"
            echo "  sudo $0 --stage=2"
            ;;
        2)
            echo
            echo -e "${CYAN}╔════════════════════════════════════════════════════════════════════╗${NC}"
            echo -e "${CYAN}║${NC}   ${GREEN}✓ STAGE 2 COMPLETE: PLM Opening + Core Unlocks${CYAN}            ║${NC}"
            echo -e "${CYAN}╚════════════════════════════════════════════════════════════════════╝${NC}"
            echo
            echo -e "${YELLOW}!${NC} MANDATORY: Reboot now for 80GB + SM clock to persist:"
            echo "  sudo reboot"
            echo
            echo "After reboot, verify unlock is present:"
            echo "  nvidia-smi --query-gpu=memory.total --format=csv,noheader"
            echo "  # Expected: '81378 MiB' or similar (80GB+)"
            echo
            echo "  nvidia-smi --query-gpu=clocks.max.sm --format=csv,noheader"
            echo "  # Expected: '1410 MHz' or higher"
            echo
            echo "Then optionally proceed to Stage 3 for feature unlocks:"
            echo "  sudo $0 --stage=3"
            ;;
        3)
            echo
            echo -e "${CYAN}╔════════════════════════════════════════════════════════════════════╗${NC}"
            echo -e "${CYAN}║${NC}   ${GREEN}✓ STAGE 3 COMPLETE: Feature Unlocks Applied${CYAN}               ║${NC}"
            echo -e "${CYAN}╚════════════════════════════════════════════════════════════════════╝${NC}"
            echo
            echo "Feature unlocks applied (Gen 3-5, NVLink, ECC, ARC)"
            echo
            echo "Optional: Enable PCIe Gen 4 link retraining:"
            echo "  sudo ${INSTALL_DIR}/cmpunlocker/scripts/pcie_gen4_unlock.sh"
            echo
            echo "Verify current state:"
            echo "  nvidia-smi --query-gpu=clocks.max.sm,memory.total --format=csv,noheader"
            echo "  lspci -s ${PCI_FULL} | grep Speed"
            ;;
    esac
else
    # Full unlock (all stages in one go)
    python3 "${INSTALL_DIR}/cmpunlocker/payload/pipeline.py" \
        "${PCI_FULL}" "${GSP_PATH}" "${TARGET}"
    ok "Full unlock applied"

    info "Step 6/6: Enabling systemd service"
    cp "${INSTALL_DIR}/cmpunlocker/daemon/cmpunlocker.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable cmpunlocker
    systemctl start cmpunlocker
    ok "Service enabled"

    echo
    echo -e "${CYAN}╔════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}   ${GREEN}✓ cmpunlocker installed${CYAN}             ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════╝${NC}"
    echo
    echo "Verify: nvidia-smi --query-gpu=clocks.max.sm,memory.total --format=csv,noheader"
    echo "Daemon: journalctl -u cmpunlocker -f"
    echo
    echo "Optional: Enable PCIe Gen 4 (if motherboard supports it):"
    echo "  sudo ${INSTALL_DIR}/cmpunlocker/scripts/pcie_gen4_unlock.sh"
    echo "  sudo ${INSTALL_DIR}/cmpunlocker/scripts/pcie_gen4_unlock_bar0.py"
    exit 0
fi
