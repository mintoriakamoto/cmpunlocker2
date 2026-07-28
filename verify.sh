#!/usr/bin/env bash
set -Eeuo pipefail

ARTIFACT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="${ARTIFACT_DIR}/patches/0001-sec2-one-run-clean.patch"
USERSPACE_INSTALLER="${ARTIFACT_DIR}/install-userspace.sh"
SOURCE_DIR=""

if [[ "${1:-}" == "--source" ]]; then
    SOURCE_DIR="${2:-}"
    [[ -n "${SOURCE_DIR}" ]] || {
        printf '[FAIL] --source needs a directory\n' >&2
        exit 1
    }
elif [[ $# -ne 0 ]]; then
    printf 'Usage: %s [--source SOURCE_DIR]\n' "$0" >&2
    exit 1
fi

die() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

[[ -f "${PATCH}" ]] || die "Missing patch: ${PATCH}"
[[ -x "${USERSPACE_INSTALLER}" ]] ||
    die "Missing executable userspace installer: ${USERSPACE_INSTALLER}"

grep -Fq 'NVIDIA-Linux-x86_64-${VERSION}.run' "${USERSPACE_INSTALLER}" ||
    die "Pinned NVIDIA runfile name is missing"
grep -Fq '45e2d4c134a23c35e50f253a4aa63e7e5e8d17e3d185d4a07c8a58e9612ed392' \
    "${USERSPACE_INSTALLER}" ||
    die "Pinned NVIDIA runfile SHA-256 is missing"
grep -Fq -- '--no-kernel-modules' "${USERSPACE_INSTALLER}" ||
    die "Userspace installer may overwrite the patched kernel modules"
grep -Fq '"${ARTIFACT_DIR}/install-userspace.sh"' "${ARTIFACT_DIR}/install.sh" ||
    die "Main installer does not install matching NVIDIA userspace"

mapfile -t patched_files < <(
    awk '/^--- a\// {sub(/^--- a\//, ""); print}' "${PATCH}"
)
[[ ${#patched_files[@]} -eq 2 ]] ||
    die "Patch must touch exactly two files, found ${#patched_files[@]}"
[[ "${patched_files[0]}" == "src/nvidia/generated/g_kernel_gsp_nvoc.h" ]] ||
    die "Unexpected first patched file: ${patched_files[0]}"
[[ "${patched_files[1]}" == "src/nvidia/src/kernel/gpu/gsp/kernel_gsp.c" ]] ||
    die "Unexpected second patched file: ${patched_files[1]}"

added_booter_calls="$(
    awk '/^\+[^+].*kgspExecuteBooterLoad_HAL/ {count++} END {print count + 0}' "${PATCH}"
)"
[[ "${added_booter_calls}" -eq 1 ]] ||
    die "Expected exactly one added SEC2 Booter execution, found ${added_booter_calls}"

if awk '
    /^\+[^+]/ && /PCIE_GEN3|ATOMIC_UPHY|UPHY_CMD|PCIe retrain|0x0008872[cC]|0x0008[cC]1[cC]0|0x000880[aA]8/ { found=1 }
    END { exit found ? 0 : 1 }
' "${PATCH}"; then
    die "PCIe/UPHY/retrain content found in clean patch"
fi

for required in \
    '0x001fa7ccU, 0xfffff0ffU' \
    '0x009a0148U, 0xffffffffU' \
    '0x001fa7c4U, 0xffffffffU' \
    '0x00823804U, 0xffffffffU' \
    '0x0082381cU, 0x88888888U' \
    '0x00823820U, 0x00000008U' \
    '0x009a0204U, 0x00000000U' \
    '0x00100ce0U, 0x00000000U' \
    '0x53310000U | NV_ARRAY_ELEMENTS(writes)' \
    'tail + 0x68U, 0x00007f2fU'
do
    grep -Fq "${required}" "${PATCH}" ||
        die "Required one-run element missing: ${required}"
done

if [[ -n "${SOURCE_DIR}" ]]; then
    gsp_source="${SOURCE_DIR}/src/nvidia/src/kernel/gpu/gsp/kernel_gsp.c"
    [[ -f "${gsp_source}" ]] || die "Missing patched source: ${gsp_source}"

    source_booter_calls="$(grep -c 'kgspExecuteBooterLoad_HAL' "${gsp_source}")"
    [[ "${source_booter_calls}" -eq 1 ]] ||
        die "Patched kernel_gsp.c has ${source_booter_calls} Booter calls, expected 1"

    grep -Fq 'SEC2_DEBUG: one-run unlock starting' "${gsp_source}" ||
        die "One-run call site missing from patched source"
    grep -Fq 'tail + 0x68U, 0x00007f2fU' "${gsp_source}" ||
        die "Reusable SEC2 cleanup frame missing from patched source"
fi

printf '[ OK ] clean patch invariants verified\n'
