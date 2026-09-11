"""
recovery/orchestrator.py — Full recovery orchestrator.

Ties together all recovery methods into a single diagnostic and recovery
sequence. This is the main entry point for recovery operations.

Recovery order:
1. Check GSP firmware (corrupted firmware breaks everything)
2. Check driver (wrong kernel = no driver)
3. Check PLMs (locked = no memory/compute unlock)
4. Check Gen2 (not trained = slow PCIe)
"""

import logging
import subprocess
import sys
import time

log = logging.getLogger(__name__)


def diagnose(pci_full: str = None) -> dict:
    """Run full diagnostic on the CMP 170HX.

    Returns comprehensive status of all subsystems.
    """
    from recovery.gsp import check_gsp_status
    from recovery.driver import check_driver_status
    from recovery.plm import check_plm_status
    from recovery.gen2 import check_gen2_status

    if pci_full is None:
        pci_full = _detect_gpu()

    result = {
        'pci': pci_full,
        'gsp': check_gsp_status(),
        'driver': check_driver_status(pci_full),
        'plm': check_plm_status(pci_full),
        'gen2': check_gen2_status(pci_full),
    }

    # Determine overall status
    result['healthy'] = (
        result['gsp']['exists'] and
        not result['gsp']['corrupted'] and
        result['driver']['loaded'] and
        result['driver']['patched'] and
        result['plm']['open'] and
        result['gen2']['is_gen2']
    )

    return result


def full_recovery(pci_full: str = None, skip_gen2: bool = False) -> bool:
    """Run full recovery sequence.

    Order matters:
    1. GSP firmware (everything depends on this)
    2. Driver (PLM unlock depends on patched driver)
    3. PLMs (memory/compute unlock depends on open PLMs)
    4. Gen2 (optional, can be done later)

    Returns True if all critical steps succeeded.
    """
    if pci_full is None:
        pci_full = _detect_gpu()

    log.info("=" * 60)
    log.info("CMP 170HX FULL RECOVERY — %s", pci_full)
    log.info("=" * 60)

    # Step 1: Check and recover GSP firmware
    log.info("")
    log.info("Step 1/4: GSP Firmware")
    from recovery.gsp import check_gsp_status, recover_gsp
    gsp = check_gsp_status()

    if not gsp['exists']:
        log.error("GSP firmware not found — cannot recover")
        return False

    if gsp['corrupted']:
        log.warning("GSP firmware corrupted — restoring from backup")
        if not recover_gsp():
            log.error("GSP recovery failed")
            return False

    log.info("GSP: OK (size=%d)", gsp['size'])

    # Step 2: Check and recover driver
    log.info("")
    log.info("Step 2/4: Driver")
    from recovery.driver import check_driver_status, recover_driver
    driver = check_driver_status(pci_full)

    if not driver['loaded'] or not driver['patched']:
        log.warning("Driver needs recovery (loaded=%s, patched=%s)",
                    driver['loaded'], driver['patched'])
        if not recover_driver(pci_full):
            log.error("Driver recovery failed")
            return False

    log.info("Driver: OK (v%s, kernel %s)", driver['version'], driver['kernel'])

    # Step 3: Check and recover PLMs
    log.info("")
    log.info("Step 3/4: PLM Registers")
    from recovery.plm import check_plm_status, recover_plm
    plm = check_plm_status(pci_full)

    if not plm['open']:
        log.warning("PLMs locked (%d/%d open) — running unlock pipeline",
                    plm['count'], plm['total'])
        if not recover_plm(pci_full):
            log.error("PLM recovery failed")
            return False

    log.info("PLM: OK (%d/%d open)", plm['count'], plm['total'])

    # Step 4: Check and recover Gen2
    if not skip_gen2:
        log.info("")
        log.info("Step 4/4: Gen2 PCIe")
        from recovery.gen2 import check_gen2_status, recover_gen2
        gen2 = check_gen2_status(pci_full)

        if not gen2['is_gen2']:
            log.warning("PCIe at Gen%d (%s) — attempting Gen2 recovery",
                        gen2['gen'], gen2['speed'])
            if not recover_gen2(pci_full):
                log.warning("Gen2 recovery failed (non-critical)")
        else:
            log.info("Gen2: OK (%s %s)", gen2['speed'], gen2['width'])
    else:
        log.info("")
        log.info("Step 4/4: Gen2 PCIe — SKIPPED")

    # Final status
    log.info("")
    log.info("=" * 60)
    log.info("RECOVERY COMPLETE")
    log.info("=" * 60)

    # Verify final state
    final = diagnose(pci_full)
    if final['healthy']:
        log.info("All systems healthy")
        _print_status(final)
        return True
    else:
        log.warning("Some systems not healthy:")
        _print_status(final)
        return False


def _print_status(status: dict) -> None:
    """Print formatted status."""
    gsp = status['gsp']
    driver = status['driver']
    plm = status['plm']
    gen2 = status['gen2']

    print(f"  GSP:     {'OK' if gsp['exists'] and not gsp['corrupted'] else 'FAIL'}"
          f" (size={gsp['size']}, patched={gsp['patched']})")
    print(f"  Driver:  {'OK' if driver['loaded'] and driver['patched'] else 'FAIL'}"
          f" (v{driver['version']}, kernel={driver['kernel']})")
    print(f"  PLM:     {'OK' if plm['open'] else 'FAIL'}"
          f" ({plm['count']}/{plm['total']} open)")
    print(f"  Gen2:    {'OK' if gen2['is_gen2'] else 'FAIL'}"
          f" (Gen{gen2['gen']}, {gen2['speed']}, {gen2['width']})")


def _detect_gpu() -> str:
    """Auto-detect GPU PCI BDF."""
    result = subprocess.run(
        ["lspci", "-nn"],
        capture_output=True, text=True, check=False,
    )
    for line in result.stdout.splitlines():
        if "10de" in line and ("2082" in line or "20c2" in line or "20b0" in line):
            bdf = line.split()[0]
            # Ensure full BDF format (add domain if missing)
            if bdf.count(':') == 1:
                bdf = f"0000:{bdf}"
            return bdf

    raise RuntimeError("CMP 170HX GPU not found in lspci")


def main():
    """CLI entry point for recovery."""
    import argparse

    parser = argparse.ArgumentParser(
        description="CMP 170HX Recovery Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  cmprecover diagnose              # Run diagnostics
  cmprecover full                  # Full recovery
  cmprecover full --skip-gen2      # Full recovery, skip Gen2
  cmprecover plm                   # PLM recovery only
  cmprecover gen2                  # Gen2 recovery only
  cmprecover driver                # Driver rebuild only
  cmprecover gsp                   # GSP firmware restore only
        """,
    )

    parser.add_argument("action", choices=[
        "diagnose", "full", "plm", "gen2", "driver", "gsp",
    ], help="Recovery action to perform")

    parser.add_argument("--pci", help="GPU PCI BDF (auto-detected if omitted)")
    parser.add_argument("--skip-gen2", action="store_true",
                        help="Skip Gen2 recovery (for full recovery)")
    parser.add_argument("--restore-stock-gsp", action="store_true",
                        help="Restore stock GSP firmware (for gsp action)")

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    pci = args.pci
    if pci is None:
        try:
            pci = _detect_gpu()
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

    if args.action == "diagnose":
        status = diagnose(pci)
        _print_status(status)
        sys.exit(0 if status['healthy'] else 1)

    elif args.action == "full":
        ok = full_recovery(pci, skip_gen2=args.skip_gen2)
        sys.exit(0 if ok else 1)

    elif args.action == "plm":
        from recovery.plm import recover_plm
        ok = recover_plm(pci)
        sys.exit(0 if ok else 1)

    elif args.action == "gen2":
        from recovery.gen2 import recover_gen2
        ok = recover_gen2(pci)
        sys.exit(0 if ok else 1)

    elif args.action == "driver":
        from recovery.driver import recover_driver
        ok = recover_driver(pci)
        sys.exit(0 if ok else 1)

    elif args.action == "gsp":
        from recovery.gsp import recover_gsp
        ok = recover_gsp(restore_stock=args.restore_stock_gsp)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
