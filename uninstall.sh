#!/usr/bin/env bash
set -Eeuo pipefail

KVER="$(uname -r)"
TARGET_DIR="/lib/modules/${KVER}/updates/cmpunlocker-sec2-one-run"
REBUILD_INITRAMFS=0

if [[ "${1:-}" == "--initramfs" ]]; then
    REBUILD_INITRAMFS=1
elif [[ $# -ne 0 ]]; then
    printf 'Usage: sudo %s [--initramfs]\n' "$0" >&2
    exit 1
fi

die() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
[[ "${EUID}" -eq 0 ]] || die "Run as root"

if [[ ! -d "${TARGET_DIR}" ]]; then
    printf '[INFO] Nothing installed at %s\n' "${TARGET_DIR}"
    exit 0
fi

for file in \
    nvidia.ko nvidia-modeset.ko nvidia-uvm.ko nvidia-drm.ko \
    nvidia-peermem.ko SHA256SUMS NVIDIA_VERSION KERNEL_VERSION \
    SOURCE_TARBALL_SHA256
do
    if [[ -f "${TARGET_DIR}/${file}" ]]; then
        rm -- "${TARGET_DIR}/${file}"
    fi
done

if ! rmdir "${TARGET_DIR}" 2>/dev/null; then
    printf '[WARN] Kept non-empty directory %s; unexpected files were not removed\n' \
        "${TARGET_DIR}" >&2
fi

depmod -a "${KVER}"
printf '[ OK ] Removed clean one-run modules from disk\n'
printf '[INFO] A currently loaded module remains active until unload or reboot\n'

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
    printf '[ OK ] Initramfs rebuilt\n'
fi
