#!/usr/bin/env bash
set -Eeuo pipefail

ARTIFACT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' < "${ARTIFACT_DIR}/VERSION")"
PATCH="${ARTIFACT_DIR}/patches/0001-sec2-one-run-clean.patch"
KVER="${SEC2_ONE_RUN_KERNEL_VERSION:-$(uname -r)}"
KMOD_ROOT="/lib/modules/${KVER}"
KOUT="${SEC2_ONE_RUN_KERNEL_OUT:-${KMOD_ROOT}/build}"
KSRC="${SEC2_ONE_RUN_KERNEL_SOURCE:-${KMOD_ROOT}/source}"
if [[ ! -d "${KSRC}" ]]; then
    KSRC="${KOUT}"
fi

BUILD_ROOT="${SEC2_ONE_RUN_BUILD_DIR:-${ARTIFACT_DIR}/.build}"
SOURCE_NAME="open-gpu-kernel-modules-${VERSION}"
SOURCE_DIR="${BUILD_ROOT}/${SOURCE_NAME}"
TARBALL="${SEC2_ONE_RUN_TARBALL:-${BUILD_ROOT}/${SOURCE_NAME}.tar.gz}"
TARBALL_URL="https://github.com/NVIDIA/open-gpu-kernel-modules/archive/refs/tags/${VERSION}.tar.gz"
TARBALL_SHA256="9df87d753cd9c05aa0eedc462af9b35debb549a657136e863282f94c96ee2640"
STAGE_DIR="${SEC2_ONE_RUN_STAGE_DIR:-${ARTIFACT_DIR}/out/${KVER}}"
JOBS="${SEC2_ONE_RUN_JOBS:-$(nproc)}"

MODULES=(
    nvidia.ko
    nvidia-modeset.ko
    nvidia-uvm.ko
    nvidia-drm.ko
    nvidia-peermem.ko
)

info() { printf '[INFO] %s\n' "$*"; }
ok()   { printf '[ OK ] %s\n' "$*"; }
die()  { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

[[ -n "${VERSION}" ]] || die "VERSION is empty"
[[ -f "${PATCH}" ]] || die "Missing patch: ${PATCH}"
[[ -d "${KSRC}" && -d "${KOUT}" ]] ||
    die "Kernel headers for ${KVER} are missing"

for tool in curl sha256sum tar patch make install modinfo; do
    command -v "${tool}" >/dev/null 2>&1 || die "Missing command: ${tool}"
done

mkdir -p "${BUILD_ROOT}"

if [[ ! -f "${TARBALL}" ]]; then
    info "Downloading NVIDIA open kernel modules ${VERSION}"
    curl -L --fail --output "${TARBALL}.partial" "${TARBALL_URL}"
    mv -- "${TARBALL}.partial" "${TARBALL}"
fi

actual_tarball_sha="$(sha256sum "${TARBALL}" | awk '{print $1}')"
[[ "${actual_tarball_sha}" == "${TARBALL_SHA256}" ]] ||
    die "Tarball SHA-256 mismatch: ${actual_tarball_sha}"
ok "Verified source tarball"

case "${SOURCE_DIR}" in
    "${BUILD_ROOT}"/open-gpu-kernel-modules-*)
        ;;
    *)
        die "Unsafe source directory: ${SOURCE_DIR}"
        ;;
esac

if [[ -e "${SOURCE_DIR}" ]]; then
    info "Removing previous exact build tree: ${SOURCE_DIR}"
    rm -rf -- "${SOURCE_DIR}"
fi

info "Extracting pristine ${SOURCE_NAME}"
tar -xzf "${TARBALL}" -C "${BUILD_ROOT}"
[[ -d "${SOURCE_DIR}" ]] || die "Expected source directory was not extracted"

info "Applying the single clean one-run patch"
patch --batch --forward -p1 -d "${SOURCE_DIR}" < "${PATCH}"
"${ARTIFACT_DIR}/verify.sh" --source "${SOURCE_DIR}"

info "Building modules for ${KVER} with ${JOBS} jobs"
find "${SOURCE_DIR}" -type f -name '*.sh' -exec chmod +x {} +
make -C "${SOURCE_DIR}" -j"${JOBS}" modules SYSSRC="${KSRC}" SYSOUT="${KOUT}"

mkdir -p "${STAGE_DIR}"
for module in "${MODULES[@]}"; do
    source_module="${SOURCE_DIR}/kernel-open/${module}"
    [[ -f "${source_module}" ]] || die "Built module missing: ${source_module}"
    install -m 0644 "${source_module}" "${STAGE_DIR}/${module}"
done

module_vermagic="$(modinfo -F vermagic "${STAGE_DIR}/nvidia.ko")"
[[ "${module_vermagic}" == "${KVER} "* ]] ||
    die "Unexpected vermagic: ${module_vermagic}"

(
    cd "${STAGE_DIR}"
    sha256sum "${MODULES[@]}" > SHA256SUMS
)

printf '%s\n' "${VERSION}" > "${STAGE_DIR}/NVIDIA_VERSION"
printf '%s\n' "${KVER}" > "${STAGE_DIR}/KERNEL_VERSION"
printf '%s\n' "${TARBALL_SHA256}" > "${STAGE_DIR}/SOURCE_TARBALL_SHA256"

ok "Clean one-run modules staged in ${STAGE_DIR}"
printf 'Next: sudo %s/install.sh\n' "${ARTIFACT_DIR}"
