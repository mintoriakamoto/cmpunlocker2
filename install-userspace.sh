#!/usr/bin/env bash
set -Eeuo pipefail

ARTIFACT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' < "${ARTIFACT_DIR}/VERSION")"
RUN_NAME="NVIDIA-Linux-x86_64-${VERSION}.run"
RUN_URL="https://download.nvidia.com/XFree86/Linux-x86_64/${VERSION}/${RUN_NAME}"
RUN_SHA256="45e2d4c134a23c35e50f253a4aa63e7e5e8d17e3d185d4a07c8a58e9612ed392"
CACHE_DIR="${SEC2_ONE_RUN_USERSPACE_CACHE_DIR:-/var/cache/cmpunlocker-sec2-one-run}"
RUN_FILE="${SEC2_ONE_RUN_DRIVER_RUNFILE:-${CACHE_DIR}/${RUN_NAME}}"

info() { printf '[INFO] %s\n' "$*"; }
ok()   { printf '[ OK ] %s\n' "$*"; }
die()  { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "Run as root"
[[ "$(uname -m)" == "x86_64" ]] ||
    die "The pinned NVIDIA userspace installer is for x86_64"
[[ -n "${VERSION}" ]] || die "VERSION is empty"

for tool in awk curl grep ldconfig modinfo readlink sha256sum sh strings; do
    command -v "${tool}" >/dev/null 2>&1 || die "Missing command: ${tool}"
done

nvml_real_path() {
    local nvml_path

    nvml_path="$(ldconfig -p 2>/dev/null |
        awk '
            /libnvidia-ml\.so\.1 \(libc6,x86-64\)/ && !found {
                path=$NF
                found=1
            }
            END { print path }
        ')"
    [[ -n "${nvml_path}" ]] || return 1
    readlink -f "${nvml_path}"
}

smi_contains_version() {
    local smi

    smi="$(command -v nvidia-smi 2>/dev/null)" || return 1
    strings "${smi}" | grep -Fx "${VERSION}" >/dev/null
}

if smi_contains_version &&
   nvml_real="$(nvml_real_path)" &&
   [[ "${nvml_real}" == *".so.${VERSION}" ]]; then
    ok "nvidia-smi and NVIDIA userspace ${VERSION} are already installed"
    exit 0
fi

if [[ -z "${SEC2_ONE_RUN_DRIVER_RUNFILE:-}" ]]; then
    mkdir -p "${CACHE_DIR}"
fi

if [[ ! -f "${RUN_FILE}" ]]; then
    [[ -z "${SEC2_ONE_RUN_DRIVER_RUNFILE:-}" ]] ||
        die "Supplied installer does not exist: ${RUN_FILE}"
    info "Downloading official NVIDIA userspace ${VERSION} (about 461 MB)"
    curl -L --fail --show-error --output "${RUN_FILE}.partial" "${RUN_URL}"
    mv -- "${RUN_FILE}.partial" "${RUN_FILE}"
fi

actual_sha="$(sha256sum "${RUN_FILE}" | awk '{print $1}')"
[[ "${actual_sha}" == "${RUN_SHA256}" ]] ||
    die "NVIDIA installer SHA-256 mismatch: ${actual_sha}"
ok "Verified official NVIDIA userspace installer"

sh "${RUN_FILE}" --check

module_path_before="$(modinfo -n nvidia 2>/dev/null || true)"

info "Installing NVIDIA ${VERSION} userspace without kernel modules"
sh "${RUN_FILE}" \
    --silent \
    --no-kernel-modules \
    --no-kernel-module-source \
    --no-dkms \
    --skip-module-load \
    --no-x-check \
    --no-nouveau-check \
    --no-disable-nouveau

module_path_after="$(modinfo -n nvidia 2>/dev/null || true)"
if [[ -n "${module_path_before}" && "${module_path_after}" != "${module_path_before}" ]]; then
    die "Userspace-only install changed module resolution: ${module_path_before} -> ${module_path_after}"
fi

command -v nvidia-smi >/dev/null 2>&1 ||
    die "nvidia-smi was not installed"
smi_contains_version ||
    die "Installed nvidia-smi does not contain version ${VERSION}"

nvml_real="$(nvml_real_path)" ||
    die "libnvidia-ml.so.1 is missing from the linker cache"
[[ "${nvml_real}" == *".so.${VERSION}" ]] ||
    die "NVML does not resolve to ${VERSION}: ${nvml_real}"

ok "Installed nvidia-smi and NVIDIA userspace ${VERSION}"
