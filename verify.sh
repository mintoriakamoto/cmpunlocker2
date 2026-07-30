#!/usr/bin/env bash
set -Eeuo pipefail

ARTIFACT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_BASE="${ARTIFACT_DIR}/patches/0001-sec2-one-run-clean.patch"
PATCH_HANDOFF="${ARTIFACT_DIR}/patches/0002-sec2-same-booter-handoff.patch"
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

[[ -f "${PATCH_BASE}" ]] || die "Missing patch: ${PATCH_BASE}"
[[ -f "${PATCH_HANDOFF}" ]] || die "Missing patch: ${PATCH_HANDOFF}"
[[ -x "${USERSPACE_INSTALLER}" ]] ||
    die "Missing executable userspace installer: ${USERSPACE_INSTALLER}"

base_patch_sha="$(sha256sum "${PATCH_BASE}" | awk '{print $1}')"
[[ "${base_patch_sha}" == \
   "132b74e77a0a3311c82b272b690e78d1fb4bf3a2cfd46b2159feab5d90bf577a" ]] ||
    die "Published clean base patch changed: ${base_patch_sha}"

mapfile -t patch_series < <(
    find "${ARTIFACT_DIR}/patches" -maxdepth 1 -type f -name '*.patch' |
        sort
)
[[ ${#patch_series[@]} -eq 2 ]] ||
    die "Clean package must contain exactly two patches"
[[ "${patch_series[0]}" == "${PATCH_BASE}" &&
   "${patch_series[1]}" == "${PATCH_HANDOFF}" ]] ||
    die "Unexpected clean patch ordering"

grep -Fq 'NVIDIA-Linux-x86_64-${VERSION}.run' "${USERSPACE_INSTALLER}" ||
    die "Pinned NVIDIA runfile name is missing"
grep -Fq '45e2d4c134a23c35e50f253a4aa63e7e5e8d17e3d185d4a07c8a58e9612ed392' \
    "${USERSPACE_INSTALLER}" ||
    die "Pinned NVIDIA runfile SHA-256 is missing"
grep -Fq -- '--no-kernel-modules' "${USERSPACE_INSTALLER}" ||
    die "Userspace installer may overwrite the patched kernel modules"
grep -Fq '"${ARTIFACT_DIR}/install-userspace.sh"' "${ARTIFACT_DIR}/install.sh" ||
    die "Main installer does not install matching NVIDIA userspace"

mapfile -t base_files < <(
    awk '/^--- a\// {sub(/^--- a\//, ""); print}' "${PATCH_BASE}"
)
[[ ${#base_files[@]} -eq 2 ]] ||
    die "Base patch must touch exactly two files"
[[ "${base_files[0]}" == "src/nvidia/generated/g_kernel_gsp_nvoc.h" &&
   "${base_files[1]}" == "src/nvidia/src/kernel/gpu/gsp/kernel_gsp.c" ]] ||
    die "Unexpected base-patch file set"

mapfile -t handoff_files < <(
    awk '/^--- a\// {sub(/^--- a\//, ""); print}' "${PATCH_HANDOFF}"
)
[[ ${#handoff_files[@]} -eq 3 ]] ||
    die "Same-Booter patch must touch exactly three files"
[[ "${handoff_files[0]}" == "src/nvidia/inc/kernel/gpu/gsp/kernel_gsp.h" &&
   "${handoff_files[1]}" == \
      "src/nvidia/src/kernel/gpu/gsp/arch/turing/kernel_gsp_tu102.c" &&
   "${handoff_files[2]}" == "src/nvidia/src/kernel/gpu/gsp/kernel_gsp.c" ]] ||
    die "Unexpected same-Booter patch file set"

added_booter_calls="$(
    awk '/^\+[^+].*kgspExecuteBooterLoad_HAL/ {count++} END {print count + 0}' \
        "${PATCH_BASE}"
)"
[[ "${added_booter_calls}" -eq 1 ]] ||
    die "Base patch must add exactly one SEC2 Booter execution"

handoff_added_booter_calls="$(
    awk '/^\+[^+].*kgspExecuteBooterLoad_HAL/ {count++} END {print count + 0}' \
        "${PATCH_HANDOFF}"
)"
[[ "${handoff_added_booter_calls}" -eq 0 ]] ||
    die "Same-Booter patch must not add another Booter execution"

handoff_removed_booter_calls="$(
    awk '/^-[^-].*kgspExecuteBooterLoad_HAL/ {count++} END {print count + 0}' \
        "${PATCH_HANDOFF}"
)"
[[ "${handoff_removed_booter_calls}" -eq 1 ]] ||
    die "Same-Booter patch must remove the base patch's early Booter execution"

if awk '
    /^\+[^+]/ &&
    /SEC2_PCIE|RmPcie|NV_XVE|XP3G|SYS_DT|ATOMIC_UPHY|UPHY_CMD|FLR|retrain|CHANGE_SPEED|LINK_ENCODING_OVERRIDE|GPU_BUS_CFG_WR32|0x0*820250|0x0*820520|0x0*82057[cC]|0x0*820580|0x0*8872[cC]|0x0*8841[cC]|0x0*8[eE]1[0-9a-fA-F][0-9a-fA-F]/ {
        found=1
    }
    END { exit found ? 0 : 1 }
' "${PATCH_HANDOFF}"; then
    die "PCIe/XP/XVE/fuse/reset experiment found in same-Booter patch"
fi

for required in \
    '0x001fa7ccU, 0xfffff0ffU' \
    '0x009a0148U, 0xffffffffU' \
    '0x001fa7c4U, 0xffffffffU' \
    '0x00823804U, 0xffffffffU' \
    '0x0082381cU, 0x88888888U' \
    '0x00823820U, 0x00000008U' \
    '0x009a0204U, 0x00000000U' \
    '0x00100ce0U, 0x00000000U'
do
    grep -Fq "${required}" "${PATCH_BASE}" ||
        die "Required clean one-run element missing: ${required}"
done

for required in \
    'kgspSec2PostblTimingStageBooterLoad' \
    'kgspSec2PostblTimingCompleteBooterLoad' \
    'SEC2_POSTBL_TIMING_OVERFLOW_COPY_SIZE' \
    'SEC2_POSTBL_TIMING_MULTIWRITE_MAX_WRITES   88U' \
    'SEC2_POSTBL_TIMING_MULTIWRITE_MARKER_VALUE' \
    'SEC2_POSTBL_TIMING_STOCK_SIGNATURE_LIMIT' \
    '0x001180f8U' \
    '0x11000000U' \
    '0x0000fff3U' \
    '0x0000273aU' \
    '0x0000810dU' \
    'pKernelGsp->pStockSignatureData' \
    'pKernelGsp->stockSignatureSize' \
    'same-Booter payload staged for the original' \
    'same original stock Booter completed the signed' \
    'same-Booter path failed; suppressing unsafe'
do
    grep -Fq "${required}" "${PATCH_HANDOFF}" ||
        die "Required same-Booter element missing: ${required}"
done

dio_calls="$(grep -Fc '0x00000b47U' "${PATCH_HANDOFF}")"
[[ "${dio_calls}" -eq 2 ]] ||
    die "Expected exactly two write-only DIO release calls, found ${dio_calls}"

if [[ -n "${SOURCE_DIR}" ]]; then
    header_source="${SOURCE_DIR}/src/nvidia/inc/kernel/gpu/gsp/kernel_gsp.h"
    gsp_source="${SOURCE_DIR}/src/nvidia/src/kernel/gpu/gsp/kernel_gsp.c"
    tu102_source="${SOURCE_DIR}/src/nvidia/src/kernel/gpu/gsp/arch/turing/kernel_gsp_tu102.c"
    [[ -f "${header_source}" && -f "${gsp_source}" && -f "${tu102_source}" ]] ||
        die "Missing patched same-Booter source"

    [[ "$(grep -c 'kgspExecuteBooterLoad_HAL' "${gsp_source}")" -eq 0 ]] ||
        die "kernel_gsp.c must not execute an early Booter"
    [[ "$(grep -c 'kgspExecuteBooterLoad_HAL' "${tu102_source}")" -eq 1 ]] ||
        die "TU102 bootstrap must retain exactly one stock Booter call"
    [[ "$(grep -Fc '0x00000b47U' "${gsp_source}")" -eq 2 ]] ||
        die "Applied source lost the two DIO release calls"

    for required in \
        'kgspSec2PostblTimingStageBooterLoad' \
        'kgspSec2PostblTimingCompleteBooterLoad' \
        'same-Booter payload staged for the original' \
        'same original stock Booter completed the signed' \
        'SEC2_POSTBL_TIMING_MULTIWRITE_MAX_WRITES   88U' \
        'pKernelGsp->pWprMeta->sizeOfSignature =' \
        'SEC2_POSTBL_TIMING_OVERFLOW_COPY_SIZE;'
    do
        grep -Fq "${required}" "${gsp_source}" ||
            die "Applied same-Booter source invariant missing: ${required}"
    done

    if grep -Fq 'bSec2StockResumeStarted' "${SOURCE_DIR}/src/nvidia/generated/g_kernel_gsp_nvoc.h" ||
       grep -Fq 'skipping duplicate reset and Booter Load' "${tu102_source}"; then
        die "Duplicate-Booter skip state remains in applied source"
    fi

    scrubber_line="$(
        grep -n 'kgspExecuteScrubberIfNeeded_HAL' "${tu102_source}" |
            head -n1 | cut -d: -f1
    )"
    fwsec_line="$(
        grep -n 'status = kgspExecuteFwsec_HAL' "${tu102_source}" |
            head -n1 | cut -d: -f1
    )"
    reset_line="$(
        grep -n 'kflcnResetIntoRiscv_HAL' "${tu102_source}" |
            head -n1 | cut -d: -f1
    )"
    boot_args_line="$(
        grep -n 'kgspProgramLibosBootArgsAddr_HAL' "${tu102_source}" |
            head -n1 | cut -d: -f1
    )"
    stage_line="$(
        grep -n 'status = kgspSec2PostblTimingStageBooterLoad' \
            "${tu102_source}" | head -n1 | cut -d: -f1
    )"
    booter_line="$(
        grep -n 'status = kgspExecuteBooterLoad_HAL' "${tu102_source}" |
            head -n1 | cut -d: -f1
    )"
    complete_line="$(
        grep -n 'status = kgspSec2PostblTimingCompleteBooterLoad' \
            "${tu102_source}" | head -n1 | cut -d: -f1
    )"
    init_line="$(
        grep -n 'status = kgspSendInitRpcs' "${tu102_source}" |
            head -n1 | cut -d: -f1
    )"
    [[ -n "${scrubber_line}" && -n "${fwsec_line}" &&
       -n "${reset_line}" && -n "${boot_args_line}" &&
       -n "${stage_line}" && -n "${booter_line}" &&
       -n "${complete_line}" && -n "${init_line}" ]] ||
        die "Cannot prove stock one-Booter flow ordering"
    (( scrubber_line < fwsec_line &&
       fwsec_line < reset_line &&
       reset_line < boot_args_line &&
       boot_args_line < stage_line &&
       stage_line < booter_line &&
       booter_line < complete_line &&
       complete_line < init_line )) ||
        die "Same-Booter hooks disturb the stock bootstrap ordering"
fi

printf '[ OK ] clean one-run + same-Booter invariants verified\n'
