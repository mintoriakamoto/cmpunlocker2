#!/usr/bin/env bash
set -Eeuo pipefail

ARTIFACT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KVER="$(uname -r)"
STAGE_DIR="${SEC2_ONE_RUN_STAGE_DIR:-${ARTIFACT_DIR}/out/${KVER}}"
TARGET_DIR="/lib/modules/${KVER}/updates/cmpunlocker-sec2-one-run"
LOAD_NOW=0
REBUILD_INITRAMFS=0
REPLACE=0
INSTALL_USERSPACE=1

MODULES=(
    nvidia.ko
    nvidia-modeset.ko
    nvidia-uvm.ko
    nvidia-drm.ko
    nvidia-peermem.ko
)

usage() {
    printf 'Usage: sudo %s [--load] [--initramfs] [--replace] [--modules-only]\n' "$0"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --load) LOAD_NOW=1 ;;
        --initramfs) REBUILD_INITRAMFS=1 ;;
        --replace) REPLACE=1 ;;
        --modules-only) INSTALL_USERSPACE=0 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 1 ;;
    esac
    shift
done

info() { printf '[INFO] %s\n' "$*"; }
ok()   { printf '[ OK ] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
die()  { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "Run as root"
[[ -d "${STAGE_DIR}" ]] ||
    die "No staged modules for ${KVER}; run ${ARTIFACT_DIR}/build.sh first"

for module in "${MODULES[@]}"; do
    [[ -f "${STAGE_DIR}/${module}" ]] || die "Missing staged module: ${module}"
done

(
    cd "${STAGE_DIR}"
    sha256sum --check SHA256SUMS
)

staged_vermagic="$(modinfo -F vermagic "${STAGE_DIR}/nvidia.ko")"
[[ "${staged_vermagic}" == "${KVER} "* ]] ||
    die "Module was built for another kernel: ${staged_vermagic}"

staged_version="$(modinfo -F version "${STAGE_DIR}/nvidia.ko")"
[[ "${staged_version}" == "$(tr -d '[:space:]' < "${ARTIFACT_DIR}/VERSION")" ]] ||
    die "Staged module version does not match VERSION: ${staged_version}"

if [[ "${INSTALL_USERSPACE}" -eq 1 ]]; then
    "${ARTIFACT_DIR}/install-userspace.sh"
else
    warn "Skipping NVIDIA userspace; nvidia-smi/NVML must independently match ${staged_version}"
fi

if [[ -d "${TARGET_DIR}" && "${REPLACE}" -ne 1 ]]; then
    for module in "${MODULES[@]}"; do
        if [[ -e "${TARGET_DIR}/${module}" ]] &&
           ! cmp -s "${STAGE_DIR}/${module}" "${TARGET_DIR}/${module}"; then
            die "${TARGET_DIR} already contains a different ${module}; use --replace"
        fi
    done
fi

mkdir -p "${TARGET_DIR}"
for module in "${MODULES[@]}"; do
    install -m 0644 "${STAGE_DIR}/${module}" "${TARGET_DIR}/${module}"
done
install -m 0644 "${STAGE_DIR}/SHA256SUMS" "${TARGET_DIR}/SHA256SUMS"
install -m 0644 "${STAGE_DIR}/NVIDIA_VERSION" "${TARGET_DIR}/NVIDIA_VERSION"
install -m 0644 "${STAGE_DIR}/KERNEL_VERSION" "${TARGET_DIR}/KERNEL_VERSION"
install -m 0644 "${STAGE_DIR}/SOURCE_TARBALL_SHA256" \
    "${TARGET_DIR}/SOURCE_TARBALL_SHA256"

depmod -a "${KVER}"
ok "Installed clean one-run modules in ${TARGET_DIR}"

resolved="$(modinfo -n nvidia 2>/dev/null || true)"
if [[ "${resolved}" == "${TARGET_DIR}/nvidia.ko" ||
      "${resolved}" == "${TARGET_DIR}/nvidia.ko."* ]]; then
    ok "modprobe resolves to the clean one-run module"
else
    warn "modprobe currently resolves nvidia to: ${resolved:-unknown}"
    warn "Inspect module precedence before rebooting"
fi

if [[ "${REBUILD_INITRAMFS}" -eq 1 ]]; then
    if command -v update-initramfs >/dev/null 2>&1; then
        update-initramfs -u -k "${KVER}"
    elif command -v dracut >/dev/null 2>&1; then
        dracut --force --kver "${KVER}"
    elif command -v mkinitcpio >/dev/null 2>&1; then
        mkinitcpio -P
    else
        die "No supported initramfs tool found"
    fi
    ok "Initramfs rebuilt"
fi

if [[ "${LOAD_NOW}" -eq 1 ]]; then
    info "Stopping NVIDIA services before module reload"
    systemctl stop nvidia-persistenced 2>/dev/null || true
    systemctl stop nvidia-fabricmanager 2>/dev/null || true

    for module in nvidia_drm nvidia_uvm nvidia_modeset nvidia; do
        modprobe -r "${module}" 2>/dev/null || true
    done

    if lsmod | grep -q '^nvidia'; then
        die "NVIDIA modules are still in use; installation is complete, but reload was skipped"
    fi

    modprobe nvidia
    modprobe nvidia-modeset
    modprobe nvidia-uvm 2>/dev/null || true
    modprobe nvidia-drm 2>/dev/null || true

    running_srcversion="$(cat /sys/module/nvidia/srcversion 2>/dev/null || true)"
    staged_srcversion="$(modinfo -F srcversion "${STAGE_DIR}/nvidia.ko")"
    [[ -n "${running_srcversion}" &&
       "${running_srcversion}" == "${staged_srcversion}" ]] ||
        die "Loaded nvidia module does not match the staged clean module"

    if command -v nvidia-modprobe >/dev/null 2>&1; then
        # The base device and UVM nodes require separate nvidia-modprobe calls.
        nvidia-modprobe -c 0
        nvidia-modprobe -u
    else
        warn "nvidia-modprobe is absent; /dev/nvidia* may need to be created by udev"
    fi

    ok "Clean one-run driver is loaded"
else
    info "Modules were not reloaded. Use --load or reboot explicitly when ready."
fi
