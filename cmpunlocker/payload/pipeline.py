"""
pipeline.py — Run the full unlock sequence.

Mirrors the open-gpu-kernel-modules-610.43.03 fork's SEC2 post-bootloader
timing unlock exactly, extended to 11 PLM registers.

  1. Stop display manager, unload nvidia modules
  2. Find GSP firmware and the stock signature section
  3. Save the stock signature for later restore
  4. For each of 11 PLM registers (WPR_CFG, FBPA, WPR, FEAT, XVE, XVE_B, XVE_C, FEAT2, OPT_PLM, PJTAG_PLM, PJTAG_SEC_PLM):
     a. Refill the ROP payload with the target address/value
     b. Patch the GSP firmware .fwsignature_ga100 section
     c. modprobe nvidia -> triggers kgspBootGspRm -> kgspExecuteBooterLoad
     d. Verify the PLM register was opened (loop up to 2 times)
  5. After all PLMs are open, write the memory unlock values (CFG1, LMR)
  6. Write the compute unlock values (SS0, SS1) via BAR0
  7. Restore the original GSP signature
  8. modprobe nvidia to reload with the original signature

CRITICAL: PLM values must be firmware-actual, not 0xffffffff.
FEAT/FEAT2 = 0xfffffcee (firmware clears bits 0,8,9 as protection markers).
Using 0xffffffff corrupts Falcon state and triggers the 4-try lockout.
"""

import glob
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.constants import get
from payload.driver import (
    aggressive_unload, flr_reset, load_module, stop_display_manager, unload_modules,
)
from payload.gsp_patch import patch_gsp
from payload.preflight import run_preflight, PrefightError
from payload.build import build as build_payload, fill_payload, refill_payload
from payload.bar0 import Bar0

log = logging.getLogger(__name__)

_GSP_GLOB = "/lib/firmware/nvidia/*/gsp_tu10x.bin"


def _find_gsp() -> str:
    paths = sorted(glob.glob(_GSP_GLOB), reverse=True)
    if not paths:
        raise FileNotFoundError(f"No GSP firmware found matching {_GSP_GLOB}")
    return paths[0]


def _save_stock_signature(gsp_path: str) -> bytes:
    import struct
    sig_name = get('elf.signature_section').encode()
    gsp = Path(gsp_path).read_bytes()

    e_shoff     = struct.unpack_from("<Q", gsp, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", gsp, 0x3A)[0]
    e_shnum     = struct.unpack_from("<H", gsp, 0x3C)[0]
    e_shstrndx  = struct.unpack_from("<H", gsp, 0x3E)[0]

    shdr_total = e_shnum * e_shentsize
    shdrs = gsp[e_shoff : e_shoff + shdr_total]

    strtab_hdr_off = e_shstrndx * e_shentsize
    strtab_off = struct.unpack_from("<Q", shdrs, strtab_hdr_off + 0x18)[0]
    strtab_sz  = struct.unpack_from("<Q", shdrs, strtab_hdr_off + 0x20)[0]
    strtab = gsp[strtab_off : strtab_off + strtab_sz]

    for i in range(e_shnum):
        base = i * e_shentsize
        name_idx = struct.unpack_from("<I", shdrs, base)[0]
        end = strtab.find(b"\x00", name_idx)
        if end == -1:
            end = len(strtab)
        if strtab[name_idx:end] == sig_name:
            sig_file_off = struct.unpack_from("<Q", shdrs, base + 0x18)[0]
            sig_size = struct.unpack_from("<Q", shdrs, base + 0x20)[0]
            return bytes(gsp[sig_file_off : sig_file_off + sig_size])
    raise ValueError(f"Section {sig_name.decode()} not found in {gsp_path}")


def _open_plm_register(pci_full: str, gsp_path: str, stock_sig: bytes,
                         write_addr: int, write_value: int, reg_name: str) -> bool:
    """Try to open one PLM register by running the ROP chain."""
    payload = fill_payload(write_addr, write_value)
    backup = gsp_path + ".cmpunlocker.bak"
    patched = gsp_path + ".cmpunlocker.patched"

    patch_gsp(backup, payload, patched)
    shutil.copy2(patched, gsp_path)

    for attempt in range(2):
        aggressive_unload()

        if not flr_reset(pci_full):
            log.warning("[%s] FLR failed on attempt %d, trying without", pci_full, attempt + 1)

        load_module()
        time.sleep(5)

        try:
            from payload.bar0 import Bar0
            with Bar0(pci_full) as bar0:
                actual = bar0.rd32(write_addr)
        except RuntimeError as e:
            log.error("[%s] BAR0 access failed (attempt %d): %s", pci_full, attempt + 1, e)
            continue

        if actual == write_value:
            log.info("[%s] %s (0x%08x) opened (attempt %d, reg=0x%08x)",
                     pci_full, reg_name, write_addr, attempt + 1, actual)
            return True
        log.warning("[%s] %s (0x%08x) attempt %d failed (got 0x%08x, want 0x%08x)",
                    pci_full, reg_name, write_addr, attempt + 1, actual, write_value)

    return False


def _write_bar0(pci_full: str, addr: int, value: int, label: str) -> bool:
    from payload.bar0 import Bar0
    try:
        with Bar0(pci_full) as bar0:
            bar0.wr32(addr, value)
            actual = bar0.rd32(addr)
    except RuntimeError as e:
        log.error("[%s] BAR0 access failed for %s: %s", pci_full, label, e)
        return False

    if actual == value:
        log.info("[%s] %s = 0x%08x OK", pci_full, label, value)
        return True
    log.warning("[%s] %s write failed (wrote 0x%08x, got 0x%08x)", pci_full, label, value, actual)
    return False


def _apply_pcie_gen2_setpci(pci_full: str) -> bool:
    """Apply PCIe Gen2 unlock via pcie_gen4_unlock.sh (PCI Config Space via setpci)."""
    script_dir = Path(__file__).parent.parent / "scripts"
    script_path = script_dir / "pcie_gen4_unlock.sh"

    if not script_path.exists():
        log.warning("[%s] pcie_gen4_unlock.sh not found, skipping Gen2", pci_full)
        return False

    try:
        result = subprocess.run(
            ["sudo", str(script_path), pci_full],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            log.info("[%s] pcie_gen4_unlock.sh succeeded", pci_full)
            return True
        log.warning("[%s] pcie_gen4_unlock.sh failed (exit %d)", pci_full, result.returncode)
        return False
    except subprocess.TimeoutExpired:
        log.error("[%s] pcie_gen4_unlock.sh timed out", pci_full)
        return False
    except Exception as e:
        log.error("[%s] pcie_gen4_unlock.sh error: %s", pci_full, e)
        return False


def run_full_unlock(pci_full: str, gsp_path: str = None,
                     target: str = None) -> bool:
    """Run the full unlock pipeline."""
    try:
        run_preflight(pci_full)
    except PrefightError as e:
        log.error("Preflight check failed: %s", e)
        return False

    if gsp_path is None:
        gsp_path = _find_gsp()
    if target is None:
        target = get('memory_unlock.default_target')

    backup = gsp_path + ".cmpunlocker.bak"

    log.info("[%s] Starting full unlock pipeline", pci_full)
    log.info("[%s] GSP firmware: %s", pci_full, gsp_path)
    log.info("[%s] Target: %s", pci_full, target)

    stop_display_manager()
    unload_modules()

    if not os.path.exists(backup):
        shutil.copy2(gsp_path, backup)
        log.info("[%s] GSP backup written to %s", pci_full, backup)

    stock_sig = _save_stock_signature(gsp_path)

    plm_table = get('plm_table_40gb')
    log.info("[%s] Using 40GB PLM unlock values", pci_full)

    plm_open_count = 0

    # CRITICAL: Save WPR2 lo/hi BEFORE PLM loop (matches driver patch exactly).
    # Driver restores WPR2 before EACH PLM attempt to prevent state corruption.
    wpr2_lo_addr = get('host_bar0_writes.wpr2_lo.addr')
    wpr2_hi_addr = get('host_bar0_writes.wpr2_hi.addr')
    wpr2_lo_val = get('host_bar0_writes.wpr2_lo.value')
    wpr2_hi_val = get('host_bar0_writes.wpr2_hi.value')
    try:
        with Bar0(pci_full) as bar0:
            saved_wpr2_lo = bar0.rd32(wpr2_lo_addr)
            saved_wpr2_hi = bar0.rd32(wpr2_hi_addr)
        log.info("[%s] Saved WPR2: lo=0x%08x hi=0x%08x", pci_full, saved_wpr2_lo, saved_wpr2_hi)
    except Exception:
        saved_wpr2_lo = wpr2_lo_val
        saved_wpr2_hi = wpr2_hi_val

    for entry in plm_table:
        try:
            with Bar0(pci_full) as bar0:
                bar0.wr32(wpr2_lo_addr, saved_wpr2_lo)
                bar0.wr32(wpr2_hi_addr, saved_wpr2_hi)
        except Exception:
            pass

        ok = _open_plm_register(
            pci_full, gsp_path, stock_sig,
            entry['addr'], entry['value'], entry['name'])
        if ok:
            plm_open_count += 1
        else:
            log.warning("[%s] Failed to open %s (0x%08x), continuing with partial PLM state",
                        pci_full, entry['name'], entry['addr'])

    try:
        with Bar0(pci_full) as bar0:
            bar0.wr32(wpr2_lo_addr, saved_wpr2_lo)
            bar0.wr32(wpr2_hi_addr, saved_wpr2_hi)
        log.info("[%s] Restored WPR2 after PLM loop", pci_full)
    except Exception:
        pass

    if plm_open_count == 0:
        log.error("[%s] No PLM registers opened, aborting", pci_full)
        return False

    log.info("[%s] %d of %d PLM registers opened", pci_full, plm_open_count, len(plm_table))

    targets = get('memory_unlock.targets')
    mem = targets[target]

    device_variants = get('device_variants')
    lmr_value = mem['lmr']
    for devid_key, variant in device_variants.items():
        if pci_full.endswith(devid_key.split(':')[1]) or devid_key in pci_full:
            if 'lmr' in variant:
                lmr_value = variant['lmr']
                log.info("[%s] Detected device %s, using LMR=0x%08x",
                         pci_full, devid_key, lmr_value)
            break

    _write_bar0(pci_full, get('host_bar0_writes.wpr2_lo.addr'),
                get('host_bar0_writes.wpr2_lo.value'), 'WPR2_LO')
    _write_bar0(pci_full, get('host_bar0_writes.wpr2_hi.addr'),
                get('host_bar0_writes.wpr2_hi.value'), 'WPR2_HI')

    cfg1_addr = get('memory_unlock.cfg1.addr')
    lmr_addr = get('memory_unlock.lmr.addr')

    log.info("[%s] Writing memory unlock: CFG1=0x%08x LMR=0x%08x",
             pci_full, mem['cfg1'], lmr_value)

    with Bar0(pci_full) as bar0:
        firmware_lmr = bar0.rd32(lmr_addr)
        log.info("[%s] Firmware-signaled LMR: 0x%08x", pci_full, firmware_lmr)

        bar0.wr32(lmr_addr, lmr_value)
        lmr_check = bar0.rd32(lmr_addr)
        lmr_ok = lmr_check == lmr_value
        if not lmr_ok:
            log.warning("[%s] LMR_UNLOCK failed (wrote 0x%08x, got 0x%08x)",
                       pci_full, lmr_value, lmr_check)

        bar0.wr32(cfg1_addr, mem['cfg1'])
        cfg1_check = bar0.rd32(cfg1_addr)
        cfg1_ok = cfg1_check == mem['cfg1']
        if not cfg1_ok:
            log.warning("[%s] CFG1 write failed (wrote 0x%08x, got 0x%08x)",
                       pci_full, mem['cfg1'], cfg1_check)

    ss0_ok = _write_bar0(pci_full, get('host_bar0_writes.ss0.addr'),
                         get('host_bar0_writes.ss0.value'), 'SS0')
    ss1_ok = _write_bar0(pci_full, get('host_bar0_writes.ss1.addr'),
                         get('host_bar0_writes.ss1.value'), 'SS1')

    from unlock.features import apply_feature_unlocks
    feat_results = apply_feature_unlocks(pci_full)
    feat_ok = all(r.get("stuck", False) for r in feat_results.values()) if feat_results else True

    # CRITICAL FIX: BAR0 Gen2 write (0x000088) doesn't work.
    # Use pcie_gen4_unlock.sh for proper PCI Config Space Gen2 unlock via setpci.
    log.info("[%s] Applying PCIe Gen2 via pcie_gen4_unlock.sh (PCI Config Space)", pci_full)
    gen2_ok = _apply_pcie_gen2_setpci(pci_full)

    shutil.copy2(backup, gsp_path)
    load_module()
    time.sleep(3)

    all_ok = cfg1_ok and lmr_ok and ss0_ok and ss1_ok and gen2_ok
    log.info("[%s] Pipeline complete — memory=%s compute=%s features=%s pcie_gen2=%s overall=%s",
             pci_full,
             "OK" if (cfg1_ok and lmr_ok) else "FAIL",
             "OK" if (ss0_ok and ss1_ok) else "FAIL",
             "OK" if feat_ok else "PARTIAL",
             "OK" if gen2_ok else "FAIL",
             "OK" if all_ok else "FAIL")
    return all_ok


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="CMP 170HX unlock pipeline")
    parser.add_argument("pci", nargs="?", help="PCI BDF address")
    parser.add_argument("gsp", nargs="?", help="GSP firmware path")
    args = parser.parse_args()

    pci = args.pci
    if pci is None:
        from payload.gpu import find_gpu
        pci = find_gpu()
        if pci is None:
            print("ERROR: No compatible GPU found")
            sys.exit(1)
    ok = run_full_unlock(pci, args.gsp)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
