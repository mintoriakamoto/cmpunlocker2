"""
pipeline.py — Run the full unlock sequence (unlock-80gb branch).

Differences from master:
  - Dynamic PLM table selection: uses plm_table_80gb when target contains '80'
  - default_target is unlocked_80gb (in constants.yaml)
  - LMR=0x28B for 80gb target (per ggualerz research)

Status: EXPERIMENTAL. 80GB blocked by firmware persistent-state lock.
GPU must be in native 10GB state for CFG1 to accept 80GB value.
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


def _open_plm_register(pci_full, gsp_path, stock_sig, write_addr, write_value, reg_name):
    payload = fill_payload(write_addr, write_value)
    backup = gsp_path + ".cmpunlocker.bak"
    patched = gsp_path + ".cmpunlocker.patched"
    patch_gsp(backup, payload, patched)
    shutil.copy2(patched, gsp_path)
    for attempt in range(2):
        aggressive_unload()
        if not flr_reset(pci_full):
            log.warning("[%s] FLR failed on attempt %d", pci_full, attempt + 1)
        load_module()
        time.sleep(5)
        try:
            with Bar0(pci_full) as bar0:
                actual = bar0.rd32(write_addr)
        except RuntimeError as e:
            log.error("[%s] BAR0 access failed (attempt %d): %s", pci_full, attempt + 1, e)
            continue
        if actual == write_value:
            log.info("[%s] %s opened (attempt %d)", pci_full, reg_name, attempt + 1)
            return True
        log.warning("[%s] %s attempt %d failed (got 0x%08x, want 0x%08x)",
                    pci_full, reg_name, attempt + 1, actual, write_value)
    return False


def _write_bar0(pci_full, addr, value, label):
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


def _apply_pcie_gen2_setpci(pci_full):
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
        return result.returncode == 0
    except Exception as e:
        log.error("[%s] gen2 script error: %s", pci_full, e)
        return False


def run_full_unlock(pci_full: str, gsp_path: str = None, target: str = None) -> bool:
    """Run the full unlock pipeline targeting 80GB."""
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
    log.info("[%s] Starting full unlock pipeline (target: %s)", pci_full, target)
    log.info("[%s] GSP firmware: %s", pci_full, gsp_path)

    stop_display_manager()
    unload_modules()

    if not os.path.exists(backup):
        shutil.copy2(gsp_path, backup)

    stock_sig = _save_stock_signature(gsp_path)

    # Dynamic PLM table selection: 80gb target uses plm_table_80gb
    # (values are identical to 40gb - PLM is capacity-independent)
    if target and '80' in target:
        plm_table = get('plm_table_80gb')
        log.info("[%s] Using 80GB PLM unlock values", pci_full)
    else:
        plm_table = get('plm_table_40gb')
        log.info("[%s] Using 40GB PLM unlock values", pci_full)

    plm_open_count = 0
    wpr2_lo_addr = get('host_bar0_writes.wpr2_lo.addr')
    wpr2_hi_addr = get('host_bar0_writes.wpr2_hi.addr')
    wpr2_lo_val  = get('host_bar0_writes.wpr2_lo.value')
    wpr2_hi_val  = get('host_bar0_writes.wpr2_hi.value')
    try:
        with Bar0(pci_full) as bar0:
            saved_wpr2_lo = bar0.rd32(wpr2_lo_addr)
            saved_wpr2_hi = bar0.rd32(wpr2_hi_addr)
    except Exception:
        saved_wpr2_lo, saved_wpr2_hi = wpr2_lo_val, wpr2_hi_val

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
            log.warning("[%s] Failed to open %s", pci_full, entry['name'])

    try:
        with Bar0(pci_full) as bar0:
            bar0.wr32(wpr2_lo_addr, saved_wpr2_lo)
            bar0.wr32(wpr2_hi_addr, saved_wpr2_hi)
    except Exception:
        pass

    if plm_open_count == 0:
        log.error("[%s] No PLM registers opened, aborting", pci_full)
        return False

    targets_map = get('memory_unlock.targets')
    mem = targets_map[target]

    device_variants = get('device_variants')
    lmr_value = mem['lmr']
    for devid_key, variant in device_variants.items():
        if pci_full.endswith(devid_key.split(':')[1]) or devid_key in pci_full:
            if 'lmr' in variant:
                lmr_value = variant['lmr']
            break

    _write_bar0(pci_full, get('host_bar0_writes.wpr2_lo.addr'),
                get('host_bar0_writes.wpr2_lo.value'), 'WPR2_LO')
    _write_bar0(pci_full, get('host_bar0_writes.wpr2_hi.addr'),
                get('host_bar0_writes.wpr2_hi.value'), 'WPR2_HI')

    cfg1_addr = get('memory_unlock.cfg1.addr')
    lmr_addr  = get('memory_unlock.lmr.addr')
    log.info("[%s] Writing memory unlock: CFG1=0x%08x LMR=0x%08x",
             pci_full, mem['cfg1'], lmr_value)

    with Bar0(pci_full) as bar0:
        firmware_lmr = bar0.rd32(lmr_addr)
        log.info("[%s] Firmware-signaled LMR: 0x%08x", pci_full, firmware_lmr)
        bar0.wr32(lmr_addr, lmr_value)
        lmr_check = bar0.rd32(lmr_addr)
        lmr_ok = lmr_check == lmr_value
        bar0.wr32(cfg1_addr, mem['cfg1'])
        cfg1_check = bar0.rd32(cfg1_addr)
        cfg1_ok = cfg1_check == mem['cfg1']

    ss0_ok = _write_bar0(pci_full, get('host_bar0_writes.ss0.addr'),
                         get('host_bar0_writes.ss0.value'), 'SS0')
    ss1_ok = _write_bar0(pci_full, get('host_bar0_writes.ss1.addr'),
                         get('host_bar0_writes.ss1.value'), 'SS1')

    from unlock.features import apply_feature_unlocks
    apply_feature_unlocks(pci_full)

    log.info("[%s] Applying PCIe Gen2 via pcie_gen4_unlock.sh", pci_full)
    gen2_ok = _apply_pcie_gen2_setpci(pci_full)

    shutil.copy2(backup, gsp_path)
    load_module()
    time.sleep(3)

    all_ok = cfg1_ok and lmr_ok and ss0_ok and ss1_ok and gen2_ok
    log.info("[%s] Pipeline complete — memory=%s compute=%s pcie_gen2=%s overall=%s",
             pci_full,
             "OK" if (cfg1_ok and lmr_ok) else "FAIL",
             "OK" if (ss0_ok and ss1_ok) else "FAIL",
             "OK" if gen2_ok else "FAIL",
             "OK" if all_ok else "FAIL")
    return all_ok


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="CMP 170HX unlock pipeline (80GB target)")
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
