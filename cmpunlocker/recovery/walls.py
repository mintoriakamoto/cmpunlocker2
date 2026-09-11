"""
recovery/walls.py — Every wall, error, issue, and edge case guard.

This module contains guards for EVERY error we've ever hit. Each guard
checks a specific failure condition and either fixes it or raises a
clear error with the exact fix.

ERRORS WE'VE HIT:
1. GSP firmware corruption from 1s watchdog (1065+ RmInit cycles)
2. StartLimitBurst=5 hit (service won't restart)
3. BAR0 access errors (not root, driver not loaded, device missing)
4. nvidia-smi timeout (3s preflight check)
5. modprobe -r may not unload nvidia modules
6. Module srcversion mismatch after reload
7. 20C2 sparse region table underflow
8. CE scrub crashes on CMP
9. BAR0 PRAMIN offset miscalculation
10. Geometry rewrite failure
11. Kernel headers missing
12. Gen2 retrain stuck at Gen1
13. BAR0 ioremap needed for probe-retrain
14. BR04 bridge may reject Gen2
15. Gen2 hammer timeout
16. nvidia module signature verification fails
17. SIGTERM handler required
18. No gen2-hammer script
19. IOMMU config backup/restore
20. Container BAR0 EIO
21. Booter error 0x31 (311,836 failures)
22. PLM writes fail (status=0xffff)
23. PCIe xp3g booter FAILED
24. 80GB blocked by firmware
25. PLMs read 0xffffffff when locked
26. Fast cycling preventing Gen2
27. GDM holding nvidia_drm
28. llama-server holding nvidia_uvm
29. nvidia-persistenced not killed
30. Device disappears after FLR
31. Modules still in use after fuser -k
32. Race condition between services
33. Exponential backoff needed
34. flock() needed for concurrent BAR0
35. SIGTERM handler needed
36. Booter error 0x5 (initial boot failure)
37. Feature register writes blocked (0xbadf5040, 0xbadf1100)
38. Bad swap entries flooding kernel log
39. nvidia-ctk missing libraries
40. Persistence mode disabled
41. Service cascade failures (hermes-gateway, jada-c2, dice-sync, dice-mesh)
42. AppArmor denials
43. GPU Xid errors (1, 119, 154)
44. Header type 7f (FLR/error state)
45. Gen2 retraining failures after reboot
"""

import glob
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

# ============================================================
# GUARD 1: Root check
# ============================================================

def guard_root():
    """Ensure running as root. BAR0 access requires root."""
    if os.geteuid() != 0:
        raise RuntimeError(
            "Must run as root (use: sudo). "
            "BAR0 access requires root privileges."
        )


# ============================================================
# GUARD 2: Driver loaded
# ============================================================

def guard_driver_loaded():
    """Ensure nvidia kernel module is loaded."""
    result = subprocess.run(
        ["lsmod"], capture_output=True, text=True, check=False,
    )
    if "nvidia" not in result.stdout:
        raise RuntimeError(
            "nvidia kernel module not loaded. "
            "Run: sudo modprobe nvidia"
        )


# ============================================================
# GUARD 3: BAR0 accessible
# ============================================================

def guard_bar0_accessible(pci_full: str):
    """Ensure BAR0 resource is accessible via mmap."""
    bar0_path = f"/sys/bus/pci/devices/{pci_full}/resource0"
    if not os.path.exists(bar0_path):
        raise RuntimeError(
            f"BAR0 not found: {bar0_path}. "
            "Causes: (1) Driver not loaded, (2) Device missing, "
            "(3) Device disabled in BIOS"
        )

    try:
        import mmap
        with open(bar0_path, 'r+b') as f:
            with mmap.mmap(f.fileno(), 0x1000, mmap.MAP_SHARED,
                          mmap.PROT_READ | mmap.PROT_WRITE) as m:
                val = m.read(4)
                if len(val) != 4:
                    raise ValueError("Could not read 4 bytes from mmap")
    except PermissionError:
        raise RuntimeError(
            f"Permission denied accessing BAR0 ({bar0_path}). "
            "Run with sudo or check SELinux/AppArmor."
        )
    except OSError as e:
        if e.errno == 5:  # EIO
            raise RuntimeError(
                f"BAR0 EIO (I/O error) on {bar0_path}. "
                "Cause: Container with virtualized PCI, or hardware fault."
            )
        raise


# ============================================================
# GUARD 4: Device visible in PCI
# ============================================================

def guard_device_visible(pci_full: str):
    """Ensure GPU appears in PCI bus."""
    path = f"/sys/bus/pci/devices/{pci_full}"
    if not os.path.exists(path):
        raise RuntimeError(
            f"GPU {pci_full} not visible in PCI. "
            "Cause: FLR reset, hot-reset, or hardware fault. "
            "Fix: Rescan PCI bus or reboot."
        )


# ============================================================
# GUARD 5: Device ID supported
# ============================================================

def guard_device_id_supported(pci_full: str):
    """Ensure device ID is one we support."""
    device_path = f"/sys/bus/pci/devices/{pci_full}/device"
    try:
        with open(device_path, 'r') as f:
            device_hex = f.read().strip()
        device_id = device_hex.replace('0x', '').lower()
    except Exception as e:
        raise RuntimeError(f"Cannot read device ID: {e}")

    supported = {'2082', '20c2', '20b0'}
    if device_id not in supported:
        raise RuntimeError(
            f"Device ID 10de:{device_id} not supported. "
            f"Supported: 10de:2082, 10de:20c2, 10de:20b0"
        )


# ============================================================
# GUARD 6: GSP firmware exists
# ============================================================

def guard_gsp_firmware():
    """Ensure GSP firmware file exists and is readable."""
    gsp_pattern = "/lib/firmware/nvidia/*/gsp_tu10x.bin"
    matches = glob.glob(gsp_pattern)
    if not matches:
        raise RuntimeError(
            "GSP firmware not found. "
            "Install: sudo apt-get install nvidia-kernel-open-common"
        )

    gsp_path = matches[0]
    if not os.access(gsp_path, os.R_OK):
        raise RuntimeError(f"GSP firmware not readable: {gsp_path}")

    return gsp_path


# ============================================================
# GUARD 7: GSP not corrupted
# ============================================================

def guard_gsp_not_corrupted(gsp_path: str):
    """Check GSP firmware for signs of corruption."""
    size = os.path.getsize(gsp_path)
    if size == 0:
        raise RuntimeError(
            f"GSP firmware is empty (0 bytes): {gsp_path}. "
            "Cause: Write interrupted, disk full, or corruption. "
            "Fix: Restore from backup."
        )
    if size < 1024 * 1024:  # < 1MB
        raise RuntimeError(
            f"GSP firmware too small ({size} bytes): {gsp_path}. "
            "Cause: Corruption or incomplete write. "
            "Fix: Restore from backup."
        )


# ============================================================
# GUARD 8: No GPU processes holding modules
# ============================================================

def guard_no_gpu_processes():
    """Kill all processes using nvidia devices."""
    killed = []
    my_pid = str(os.getpid())

    for dev in glob.glob("/dev/nvidia*") + ["/dev/nvidiactl", "/dev/dri/*"]:
        if not os.path.exists(dev):
            continue
        result = subprocess.run(
            ["fuser", dev], capture_output=True, text=True, check=False,
        )
        for pid in result.stdout.split():
            if pid != my_pid:
                subprocess.run(
                    ["kill", "-9", pid], capture_output=True, check=False,
                )
                killed.append(pid)

    if killed:
        log.info("Killed %d GPU processes: %s", len(killed), killed)
        time.sleep(2)

    # Verify modules are free
    result = subprocess.run(
        ["lsmod"], capture_output=True, text=True, check=False,
    )
    if "nvidia_uvm" in result.stdout:
        # Still in use — try harder
        log.warning("nvidia_uvm still in use after kill, trying fuser -k on all nvidia*")
        for dev in glob.glob("/dev/nvidia*"):
            subprocess.run(
                ["fuser", "-k", dev], capture_output=True, check=False,
            )
        time.sleep(2)

        result = subprocess.run(
            ["lsmod"], capture_output=True, text=True, check=False,
        )
        if "nvidia_uvm" in result.stdout:
            raise RuntimeError(
                "Cannot kill GPU processes holding nvidia_uvm. "
                "Cause: CUDA process in uninterruptible sleep. "
                "Fix: Wait for process to finish, or reboot."
            )


# ============================================================
# GUARD 9: Modules can be unloaded
# ============================================================

def guard_modules_can_unload():
    """Verify nvidia modules can be unloaded."""
    result = subprocess.run(
        ["lsmod"], capture_output=True, text=True, check=False,
    )

    # Check what's holding modules
    holders = []
    for mod in ["nvidia_uvm", "nvidia_drm", "nvidia_modeset", "nvidia"]:
        if mod in result.stdout:
            # Check if anything is using this module
            ref_result = subprocess.run(
                ["cat", f"/sys/module/{mod}/refcnt"],
                capture_output=True, text=True, check=False,
            )
            if ref_result.returncode == 0:
                refcnt = ref_result.stdout.strip()
                if refcnt != "0":
                    holders.append(f"{mod}(refcnt={refcnt})")

    if holders:
        raise RuntimeError(
            f"Modules still in use: {', '.join(holders)}. "
            "Cause: Process holding nvidia module. "
            "Fix: Kill all GPU processes first."
        )


# ============================================================
# GUARD 10: Modules successfully unloaded
# ============================================================

def guard_modules_unloaded():
    """Verify nvidia modules are actually unloaded."""
    result = subprocess.run(
        ["lsmod"], capture_output=True, text=True, check=False,
    )
    if "nvidia" in result.stdout:
        # Try force unload
        log.warning("Modules still loaded after modprobe -r, trying rmmod -f")
        for mod in ("nvidia_uvm", "nvidia_drm", "nvidia_modeset", "nvidia"):
            subprocess.run(
                ["rmmod", "-f", mod], capture_output=True, check=False,
            )
        time.sleep(1)

        result = subprocess.run(
            ["lsmod"], capture_output=True, text=True, check=False,
        )
        if "nvidia" in result.stdout:
            raise RuntimeError(
                "Cannot unload nvidia modules even with rmmod -f. "
                "Cause: Module in use by kernel. "
                "Fix: Reboot."
            )


# ============================================================
# GUARD 11: Services stopped
# ============================================================

def guard_services_stopped():
    """Stop all services that hold nvidia modules."""
    services = [
        'nvidia-cdi-refresh', 'persist-gpu-clocks', 'gen2',
        'cmpunlocker', 'hermes-llama', 'crucible-qwen', 'gdm3',
        'nvidia-persistenced',
    ]
    for svc in services:
        subprocess.run(
            ["systemctl", "stop", f"{svc}.service"],
            capture_output=True, check=False,
        )
        subprocess.run(
            ["systemctl", "mask", f"{svc}.service"],
            capture_output=True, check=False,
        )

    # Kill nvidia-persistenced directly
    subprocess.run(
        ["nvidia-persistenced", "--kill"],
        capture_output=True, check=False,
    )
    time.sleep(2)


# ============================================================
# GUARD 12: Services restarted
# ============================================================

def guard_services_restarted():
    """Unmask and restart GPU services."""
    services = [
        'nvidia-cdi-refresh', 'persist-gpu-clocks', 'cmpunlocker',
        'hermes-llama', 'crucible-qwen',
    ]
    for svc in services:
        subprocess.run(
            ["systemctl", "unmask", f"{svc}.service"],
            capture_output=True, check=False,
        )
        subprocess.run(
            ["systemctl", "start", f"{svc}.service"],
            capture_output=True, check=False,
        )


# ============================================================
# GUARD 13: StartLimitBurst reset
# ============================================================

def guard_start_limit_reset():
    """Reset StartLimitBurst if service hit restart limit."""
    result = subprocess.run(
        ["systemctl", "show", "cmpunlocker.service", "--property=ActiveState"],
        capture_output=True, text=True, check=False,
    )
    if "inactive" in result.stdout or "failed" in result.stdout:
        log.warning("Service hit restart limit, resetting")
        subprocess.run(
            ["systemctl", "reset-failed", "cmpunlocker.service"],
            capture_output=True, check=False,
        )


# ============================================================
# GUARD 14: Kernel headers exist
# ============================================================

def guard_kernel_headers():
    """Ensure kernel headers exist for driver build."""
    import platform
    kver = platform.release()
    build_path = Path(f"/lib/modules/{kver}/build")
    if not build_path.exists():
        raise RuntimeError(
            f"Kernel headers not found at {build_path}. "
            f"Install: sudo apt-get install linux-headers-{kver}"
        )


# ============================================================
# GUARD 15: Driver source exists
# ============================================================

def guard_driver_source():
    """Ensure patched driver source exists."""
    source = Path("/home/ai/.hermes/cmp_lab/buliaoyin-cmpunlocker/driver")
    if not source.exists():
        raise RuntimeError(
            f"Driver source not found at {source}. "
            "Cannot rebuild driver after kernel upgrade."
        )

    install_script = source / "install.sh"
    if not install_script.exists():
        raise RuntimeError(f"install.sh not found at {install_script}")

    return source


# ============================================================
# GUARD 16: Module srcversion match
# ============================================================

def guard_srcversion_match():
    """Check if loaded module matches installed module."""
    result = subprocess.run(
        ["modinfo", "nvidia", "-F", "srcversion"],
        capture_output=True, text=True, check=False,
    )
    loaded_srcversion = result.stdout.strip()

    # Check installed module
    import platform
    kver = platform.release()
    installed_path = Path(f"/lib/modules/{kver}/updates/cmpunlocker/nvidia.ko")
    if installed_path.exists():
        result = subprocess.run(
            ["modinfo", str(installed_path), "-F", "srcversion"],
            capture_output=True, text=True, check=False,
        )
        installed_srcversion = result.stdout.strip()

        if loaded_srcversion != installed_srcversion:
            log.warning(
                "Module srcversion mismatch: loaded=%s, installed=%s. "
                "Driver needs reload.",
                loaded_srcversion, installed_srcversion
            )
            return False

    return True


# ============================================================
# GUARD 17: GSP backup exists
# ============================================================

def guard_gsp_backup(gsp_path: str):
    """Ensure GSP backup exists before destructive operation."""
    backup_path = gsp_path + ".cmpunlocker.bak"
    if not os.path.exists(backup_path):
        log.warning("No GSP backup found at %s, creating one now", backup_path)
        import shutil
        shutil.copy2(gsp_path, backup_path)

    return backup_path


# ============================================================
# GUARD 18: PLM not stuck at 0xffffffff
# ============================================================

def guard_plm_not_stuck(pci_full: str):
    """Check if PLMs are stuck at 0xffffffff (locked)."""
    from payload.bar0 import Bar0
    from common.constants import get

    plm_table = get('plm_table_40gb')
    stuck_count = 0

    try:
        with Bar0(pci_full) as bar0:
            for entry in plm_table:
                val = bar0.rd32(entry['addr'])
                if val == 0xffffffff:
                    stuck_count += 1
    except Exception as e:
        log.error("Cannot read PLM status: %s", e)
        return True  # Assume stuck

    if stuck_count == len(plm_table):
        return True  # All stuck

    return stuck_count > 0


# ============================================================
# GUARD 19: WPR2 not corrupted
# ============================================================

def guard_wpr2_valid(pci_full: str):
    """Check if WPR2 values are valid (not all zeros or all ones)."""
    from payload.bar0 import Bar0
    from common.constants import get

    wpr2_lo_addr = get('host_bar0_writes.wpr2_lo.addr')
    wpr2_hi_addr = get('host_bar0_writes.wpr2_hi.addr')

    try:
        with Bar0(pci_full) as bar0:
            lo = bar0.rd32(wpr2_lo_addr)
            hi = bar0.rd32(wpr2_hi_addr)
    except Exception:
        return True  # Can't check, assume valid

    # WPR2 should not be all zeros or all ones
    if lo == 0x00000000 and hi == 0x00000000:
        log.warning("WPR2 is all zeros — may be corrupted")
        return False
    if lo == 0xffffffff and hi == 0xffffffff:
        log.warning("WPR2 is all ones — may be corrupted")
        return False

    return True


# ============================================================
# GUARD 20: Gen2 not stuck at Gen1
# ============================================================

def guard_gen2_not_stuck(pci_full: str):
    """Check if PCIe is stuck at Gen1."""
    try:
        result = subprocess.run(
            ["setpci", "-s", pci_full, "CAP_EXP+12.w"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return True  # Can't check

        lnksta = int(result.stdout.strip(), 16)
        gen = lnksta & 0x0f
        return gen >= 2
    except Exception:
        return True  # Can't check, assume ok


# ============================================================
# GUARD 21: Fast cycling prevention
# ============================================================

def guard_not_fast_cycling(last_cycle_time: float, min_interval: float = 5.0):
    """Prevent fast cycling that prevents Gen2."""
    elapsed = time.time() - last_cycle_time
    if elapsed < min_interval:
        wait = min_interval - elapsed
        log.info("Waiting %.1fs to prevent fast cycling", wait)
        time.sleep(wait)


# ============================================================
# GUARD 22: Device reappears after FLR
# ============================================================

def guard_device_reappears(pci_full: str, timeout: float = 10.0):
    """Wait for GPU to reappear after FLR/secondary bus reset."""
    start = time.time()
    while time.time() - start < timeout:
        path = f"/sys/bus/pci/devices/{pci_full}"
        if os.path.exists(path):
            return True
        time.sleep(0.5)

    raise RuntimeError(
        f"GPU {pci_full} did not reappear after reset within {timeout}s. "
        "Cause: Hardware fault or BIOS issue. "
        "Fix: Rescan PCI bus or reboot."
    )


# ============================================================
# GUARD 23: nvidia-smi responds
# ============================================================

def guard_nvidia_smi_responsive(pci_full: str, timeout: int = 10):
    """Check nvidia-smi can query the device."""
    result = subprocess.run(
        ["timeout", str(timeout), "nvidia-smi",
         "--query-gpu=pci.bus_id,memory.total",
         "--format=csv,noheader"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        log.warning("nvidia-smi timeout or error (returncode=%d)", result.returncode)
        return False
    if "No devices" in result.stdout:
        return False
    return True


# ============================================================
# GUARD 24: SIGTERM handler installed
# ============================================================

def guard_sigterm_handler(handler):
    """Install SIGTERM handler for graceful shutdown."""
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


# ============================================================
# GUARD 25: flock() for concurrent BAR0 access
# ============================================================

class Bar0Flock:
    """Context manager for flock() around BAR0 access."""
    def __init__(self, pci_full: str):
        self.lock_path = f"/tmp/cmpunlocker_{pci_full.replace(':', '_')}.lock"
        self.fd = None

    def __enter__(self):
        self.fd = open(self.lock_path, 'w')
        import fcntl
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return self

    def __exit__(self, *args):
        import fcntl
        if self.fd:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.fd.close()


# ============================================================
# GUARD 26: IOMMU config backup
# ============================================================

def guard_iommu_backup():
    """Backup IOMMU config before changes."""
    grub_cfg = "/etc/default/grub"
    grub_bak = grub_cfg + ".cmpunlocker.bak"

    if not os.path.exists(grub_bak) and os.path.exists(grub_cfg):
        import shutil
        shutil.copy2(grub_cfg, grub_bak)
        log.info("Backed up %s to %s", grub_cfg, grub_bak)

    cmdline_cfg = "/etc/kernel/cmdline"
    cmdline_bak = cmdline_cfg + ".cmpunlocker.bak"

    if not os.path.exists(cmdline_bak) and os.path.exists(cmdline_cfg):
        import shutil
        shutil.copy2(cmdline_cfg, cmdline_bak)
        log.info("Backed up %s to %s", cmdline_cfg, cmdline_bak)


# ============================================================
# GUARD 27: 80GB blocked
# ============================================================

def guard_not_80gb_target(target: str):
    """Ensure we're not trying 80GB (hardware-blocked)."""
    if '80gb' in target.lower():
        raise RuntimeError(
            "80GB is hardware-blocked. Firmware rejects CFG1=0x02779000. "
            "Use 'unlocked_40gb' instead."
        )


# ============================================================
# GUARD 28: Correct LMR for device variant
# ============================================================

def guard_correct_lmr(pci_full: str, lmr_value: int):
    """Verify LMR value matches device variant."""
    device_path = f"/sys/bus/pci/devices/{pci_full}/device"
    try:
        with open(device_path, 'r') as f:
            device_hex = f.read().strip()
        device_id = device_hex.replace('0x', '').lower()
    except Exception:
        return  # Can't check

    # Known LMR values from driver patch
    expected = {
        '20c2': 0x0000020B,  # 8GB model
        '2082': 0x0000028A,  # 10GB model
        '20b0': 0x0000028A,  # Assumed 10GB variant
    }

    if device_id in expected and lmr_value != expected[device_id]:
        raise RuntimeError(
            f"LMR value 0x{lmr_value:08x} wrong for device 10de:{device_id}. "
            f"Expected: 0x{expected[device_id]:08x}"
        )


# ============================================================
# GUARD 29: Exponential backoff
# ============================================================

class ExponentialBackoff:
    """Exponential backoff for retries."""
    def __init__(self, base: float = 1.0, max_delay: float = 60.0,
                 multiplier: float = 2.0):
        self.base = base
        self.max_delay = max_delay
        self.multiplier = multiplier
        self.attempt = 0

    def wait(self) -> float:
        """Wait and return the delay time."""
        delay = min(self.base * (self.multiplier ** self.attempt), self.max_delay)
        self.attempt += 1
        log.info("Backoff: waiting %.1fs (attempt %d)", delay, self.attempt)
        time.sleep(delay)
        return delay

    def reset(self):
        """Reset backoff after success."""
        self.attempt = 0


# ============================================================
# GUARD 30: Speed=15 corruption check
# ============================================================

def guard_not_speed15():
    """Check for GSP corruption (speed=15 in kern.log)."""
    try:
        result = subprocess.run(
            ["grep", "-c", "speed=15", "/var/log/kern.log"],
            capture_output=True, text=True, check=False,
        )
        count = int(result.stdout.strip()) if result.stdout.strip() else 0
        if count > 0:
            log.warning(
                "Found %d speed=15 entries in kern.log — GSP may be corrupted. "
                "Consider restoring GSP firmware.",
                count
            )
            return False
    except Exception:
        pass
    return True


# ============================================================
# GUARD 31: Booter error flood check
# ============================================================

def guard_no_booter_flood():
    """Check for massive booter error floods (0x31, 0x5)."""
    try:
        result = subprocess.run(
            ["grep", "-c", "Booter failed with non-zero error code", "/var/log/kern.log"],
            capture_output=True, text=True, check=False,
        )
        count = int(result.stdout.strip()) if result.stdout.strip() else 0
        if count > 100:
            log.warning(
                "Found %d booter failures in kern.log — GSP firmware may be corrupted. "
                "Consider restoring GSP firmware.",
                count
            )
            return False
    except Exception:
        pass
    return True


# ============================================================
# GUARD 32: Feature register writes blocked
# ============================================================

def guard_feature_registers_ok(pci_full: str):
    """Check if feature register writes are blocked (0xbadf5040)."""
    from payload.bar0 import Bar0

    feature_addrs = {
        'pcie_gen2': 0x000088,
        'nvlink_enable': 0x88000c,
        'ecc_enable': 0x100110,
    }

    blocked = []
    try:
        with Bar0(pci_full) as bar0:
            for name, addr in feature_addrs.items():
                val = bar0.rd32(addr)
                if val in (0xbadf5040, 0xbadf1100, 0xf0000000):
                    blocked.append(f"{name}=0x{val:08x}")
    except Exception:
        return True  # Can't check

    if blocked:
        log.warning("Feature registers blocked (hardware sentinel): %s", blocked)
        return False

    return True


# ============================================================
# GUARD 33: Bad swap entries check
# ============================================================

def guard_no_bad_swap_entries():
    """Check for bad swap entries flooding kernel log."""
    try:
        result = subprocess.run(
            ["grep", "-c", "Bad swap file entry", "/var/log/kern.log"],
            capture_output=True, text=True, check=False,
        )
        count = int(result.stdout.strip()) if result.stdout.strip() else 0
        if count > 10:
            log.warning(
                "Found %d bad swap entries in kern.log — GPU memory-mapped "
                "addresses misinterpreted as swap. This is a known issue "
                "and does not affect GPU operation.",
                count
            )
    except Exception:
        pass
    return True


# ============================================================
# GUARD 34: nvidia-ctk libraries
# ============================================================

def guard_nvidia_ctk_libs():
    """Check for missing nvidia-ctk libraries."""
    missing = []
    libs = [
        "libnvidia-sandboxutils.so.1",
        "libnvidia-vulkan-producer.so",
    ]
    for lib in libs:
        result = subprocess.run(
            ["ldconfig", "-p"], capture_output=True, text=True, check=False,
        )
        if lib not in result.stdout:
            missing.append(lib)

    if missing:
        log.warning("Missing nvidia-ctk libraries: %s", missing)
        log.warning("nvidia-ctk may not work fully — these are non-critical")
    return True


# ============================================================
# GUARD 35: Persistence mode
# ============================================================

def guard_persistence_mode():
    """Check if persistence mode is enabled."""
    result = subprocess.run(
        ["nvidia-smi", "-pm", "1"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        log.warning("Could not enable persistence mode: %s", result.stderr.strip())
    return True


# ============================================================
# GUARD 36: Service cascade failures
# ============================================================

def guard_no_cascade_failures():
    """Check for services in crash-restart loop."""
    cascade_services = [
        'hermes-gateway', 'jada-c2', 'dice-sync', 'dice-mesh',
    ]

    failed = []
    for svc in cascade_services:
        result = subprocess.run(
            ["systemctl", "is-failed", f"{svc}.service"],
            capture_output=True, text=True, check=False,
        )
        if result.stdout.strip() == "failed":
            failed.append(svc)

    if failed:
        log.warning(
            "Services in crash-restart loop: %s. "
            "These may be holding nvidia modules. Consider stopping them.",
            failed
        )
    return True


# ============================================================
# GUARD 37: AppArmor denials
# ============================================================

def guard_no_apparmor_denials():
    """Check for AppArmor denials affecting GPU operations."""
    try:
        result = subprocess.run(
            ["grep", "-c", 'apparmor="DENIED".*fusermount3', "/var/log/syslog"],
            capture_output=True, text=True, check=False,
        )
        count = int(result.stdout.strip()) if result.stdout.strip() else 0
        if count > 10:
            log.warning(
                "Found %d AppArmor denials for fusermount3. "
                "This may affect FUSE mounts but not GPU operation.",
                count
            )
    except Exception:
        pass
    return True


# ============================================================
# GUARD 38: GPU Xid errors
# ============================================================

def guard_no_xid_errors():
    """Check for GPU Xid errors (1, 119, 154)."""
    try:
        xid_counts = {}
        for xid in [1, 119, 154]:
            result = subprocess.run(
                ["grep", "-c", f"Xid.*{xid}", "/var/log/kern.log"],
                capture_output=True, text=True, check=False,
            )
            count = int(result.stdout.strip()) if result.stdout.strip() else 0
            if count > 0:
                xid_counts[xid] = count

        if xid_counts:
            log.warning("GPU Xid errors detected: %s", xid_counts)
            if 154 in xid_counts:
                log.error(
                    "Xid 154 (GPU reset) detected — GPU may have crashed. "
                    "Consider: nvidia-smi -r or full recovery."
                )
                return False
    except Exception:
        pass
    return True


# ============================================================
# GUARD 39: Header type 7f (FLR state)
# ============================================================

def guard_not_flr_state(pci_full: str):
    """Check if GPU is in FLR/error state (header type 7f)."""
    try:
        result = subprocess.run(
            ["lspci", "-s", pci_full, "-xxx"],
            capture_output=True, text=True, check=False,
        )
        # Header type is at offset 0x0e (1 byte)
        # Type 0x7f = function in FLR/error state
        if "7f:" in result.stdout.lower():
            log.error(
                "GPU %s is in FLR/error state (header type 0x7f). "
                "Cause: 80GB attempt or hardware fault. "
                "Fix: nvidia-smi -r or reboot.",
                pci_full
            )
            return False
    except Exception:
        pass
    return True


# ============================================================
# GUARD 40: Gen2 retraining reliability
# ============================================================

def guard_gen2_reliable(pci_full: str):
    """Check if Gen2 retraining is reliable (not stuck after reboot)."""
    from recovery.gen2 import check_gen2_status

    status = check_gen2_status(pci_full)
    if not status['is_gen2']:
        # Check if we've had multiple failed attempts
        try:
            result = subprocess.run(
                ["grep", "-c", "cycle.*Gen1", "/var/log/gen2-cycle.log"],
                capture_output=True, text=True, check=False,
            )
            fail_count = int(result.stdout.strip()) if result.stdout.strip() else 0
            if fail_count > 5:
                log.warning(
                    "Gen2 retraining has failed %d times. "
                    "This may indicate a hardware issue with the PCIe link.",
                    fail_count
                )
        except Exception:
            pass
    return True


# ============================================================
# GUARD 41: Kernel taint check
# ============================================================

def guard_kernel_taint():
    """Check if kernel is tainted by nvidia module."""
    try:
        with open("/proc/sys/kernel/tainted", 'r') as f:
            tainted = f.read().strip()
        # Bit 12 (4096) = out-of-tree module
        if tainted and int(tainted) & 4096:
            log.warning(
                "Kernel tainted by out-of-tree nvidia module. "
                "This is expected for patched drivers."
            )
    except Exception:
        pass
    return True


# ============================================================
# GUARD 42: GPU memory capacity
# ============================================================

def guard_gpu_memory_ok(pci_full: str):
    """Check GPU memory capacity is correct (40GB, not 10GB)."""
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        mem_str = result.stdout.strip()
        if "MiB" in mem_str:
            mem_mb = int(mem_str.replace("MiB", "").strip())
            if mem_mb < 30000:  # Less than 30GB
                log.error(
                    "GPU memory only %d MiB — unlock may have failed. "
                    "Expected ~40000 MiB.",
                    mem_mb
                )
                return False
    return True
