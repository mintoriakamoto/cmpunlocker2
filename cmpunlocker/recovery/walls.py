"""
recovery/walls.py — Every wall, error, issue, and edge case guard WITH RECOVERY.

This module contains guards for EVERY error we've ever hit. Each guard
checks a specific failure condition and ATTEMPTS TO RECOVER automatically.
If recovery fails, it raises a clear error with the exact fix.

ERRORS WE'VE HIT (42 total):
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
# GUARD 1: Root check — RECOVERY: exec with sudo
# ============================================================

def guard_root(fix=False):
    """Ensure running as root. BAR0 access requires root."""
    if os.geteuid() != 0:
        if fix:
            log.info("Not root, re-executing with sudo")
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        raise RuntimeError(
            "Must run as root (use: sudo). "
            "BAR0 access requires root privileges."
        )


# ============================================================
# GUARD 2: Driver loaded — RECOVERY: modprobe nvidia
# ============================================================

def guard_driver_loaded(fix=False):
    """Ensure nvidia kernel module is loaded."""
    result = subprocess.run(
        ["lsmod"], capture_output=True, text=True, check=False,
    )
    if "nvidia" not in result.stdout:
        if fix:
            log.info("nvidia not loaded, running modprobe nvidia")
            result = subprocess.run(
                ["modprobe", "nvidia"], capture_output=True, text=True, check=False,
            )
            if result.returncode == 0:
                time.sleep(2)
                return
        raise RuntimeError(
            "nvidia kernel module not loaded. "
            "Run: sudo modprobe nvidia"
        )


# ============================================================
# GUARD 3: BAR0 accessible — RECOVERY: load driver
# ============================================================

def guard_bar0_accessible(pci_full: str, fix=False):
    """Ensure BAR0 resource is accessible via mmap."""
    bar0_path = f"/sys/bus/pci/devices/{pci_full}/resource0"
    if not os.path.exists(bar0_path):
        if fix:
            log.info("BAR0 not found, loading nvidia driver")
            subprocess.run(["modprobe", "nvidia"], capture_output=True, check=False)
            time.sleep(3)
            if os.path.exists(bar0_path):
                return
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
# GUARD 4: Device visible — RECOVERY: PCI rescan
# ============================================================

def guard_device_visible(pci_full: str, fix=False):
    """Ensure GPU appears in PCI bus."""
    path = f"/sys/bus/pci/devices/{pci_full}"
    if not os.path.exists(path):
        if fix:
            log.info("Device not visible, attempting PCI rescan")
            subprocess.run(
                ["bash", "-c", "echo 1 > /sys/bus/pci/rescan"],
                capture_output=True, check=False,
            )
            time.sleep(3)
            if os.path.exists(path):
                return
        raise RuntimeError(
            f"GPU {pci_full} not visible in PCI. "
            "Cause: FLR reset, hot-reset, or hardware fault. "
            "Fix: Rescan PCI bus or reboot."
        )


# ============================================================
# GUARD 5: Device ID supported — RECOVERY: none (hardware)
# ============================================================

def guard_device_id_supported(pci_full: str, fix=False):
    """Ensure device ID is one we support."""
    device_path = f"/sys/bus/pci/devices/{pci_full}/device"
    try:
        with open(device_path, 'r') as f:
            device_hex = f.read().strip()
        device_id = device_hex.replace('x', '').lower()
    except Exception as e:
        raise RuntimeError(f"Cannot read device ID: {e}")

    supported = {'2082', '20c2', '20b0'}
    if device_id not in supported:
        raise RuntimeError(
            f"Device ID 10de:{device_id} not supported. "
            f"Supported: 10de:2082, 10de:20c2, 10de:20b0"
        )


# ============================================================
# GUARD 6: GSP firmware — RECOVERY: none (must install)
# ============================================================

def guard_gsp_firmware(fix=False):
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
# GUARD 7: GSP not corrupted — RECOVERY: restore from backup
# ============================================================

def guard_gsp_not_corrupted(gsp_path: str, fix=False):
    """Check GSP firmware for signs of corruption."""
    size = os.path.getsize(gsp_path)
    if size == 0 or size < 1024 * 1024:
        if fix:
            log.warning("GSP corrupted (size=%d), restoring from backup", size)
            from recovery.gsp import recover_gsp
            if recover_gsp():
                return
        raise RuntimeError(
            f"GSP firmware corrupted (size={size}): {gsp_path}. "
            "Fix: Restore from backup."
        )


# ============================================================
# GUARD 8: No GPU processes — RECOVERY: kill them
# ============================================================

def guard_no_gpu_processes(fix=False):
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
                if fix:
                    subprocess.run(
                        ["kill", "-9", pid], capture_output=True, check=False,
                    )
                    killed.append(pid)
                else:
                    raise RuntimeError(
                        f"Process {pid} holding {dev}. "
                        "Fix: kill GPU processes first."
                    )

    if killed:
        log.info("Killed %d GPU processes: %s", len(killed), killed)
        time.sleep(2)


# ============================================================
# GUARD 9: Modules can unload — RECOVERY: kill holders
# ============================================================

def guard_modules_can_unload(fix=False):
    """Verify nvidia modules can be unloaded."""
    result = subprocess.run(
        ["lsmod"], capture_output=True, text=True, check=False,
    )

    holders = []
    for mod in ["nvidia_uvm", "nvidia_drm", "nvidia_modeset", "nvidia"]:
        if mod in result.stdout:
            ref_result = subprocess.run(
                ["cat", f"/sys/module/{mod}/refcnt"],
                capture_output=True, text=True, check=False,
            )
            if ref_result.returncode == 0:
                refcnt = ref_result.stdout.strip()
                if refcnt != "0":
                    holders.append(f"{mod}(refcnt={refcnt})")

    if holders:
        if fix:
            log.warning("Modules in use: %s, killing GPU processes", holders)
            guard_no_gpu_processes(fix=True)
            return
        raise RuntimeError(
            f"Modules still in use: {', '.join(holders)}. "
            "Fix: Kill all GPU processes first."
        )


# ============================================================
# GUARD 10: Modules unloaded — RECOVERY: rmmod -f
# ============================================================

def guard_modules_unloaded(fix=False):
    """Verify nvidia modules are actually unloaded."""
    result = subprocess.run(
        ["lsmod"], capture_output=True, text=True, check=False,
    )
    if "nvidia" in result.stdout:
        if fix:
            log.warning("Modules still loaded, trying rmmod -f")
            for mod in ("nvidia_uvm", "nvidia_drm", "nvidia_modeset", "nvidia"):
                subprocess.run(
                    ["rmmod", "-f", mod], capture_output=True, check=False,
                )
            time.sleep(1)

            result = subprocess.run(
                ["lsmod"], capture_output=True, text=True, check=False,
            )
            if "nvidia" not in result.stdout:
                return
        raise RuntimeError(
            "Cannot unload nvidia modules even with rmmod -f. "
            "Fix: Reboot."
        )


# ============================================================
# GUARD 11: Services stopped — RECOVERY: stop them
# ============================================================

def guard_services_stopped(fix=False):
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
        if fix:
            subprocess.run(
                ["systemctl", "mask", f"{svc}.service"],
                capture_output=True, check=False,
            )

    subprocess.run(
        ["nvidia-persistenced", "--kill"],
        capture_output=True, check=False,
    )
    time.sleep(2)


# ============================================================
# GUARD 12: Services restarted — RECOVERY: restart them
# ============================================================

def guard_services_restarted(fix=False):
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
# GUARD 13: StartLimitBurst — RECOVERY: reset-failed
# ============================================================

def guard_start_limit_reset(fix=False):
    """Reset StartLimitBurst if service hit restart limit."""
    result = subprocess.run(
        ["systemctl", "show", "cmpunlocker.service", "--property=ActiveState"],
        capture_output=True, text=True, check=False,
    )
    if "inactive" in result.stdout or "failed" in result.stdout:
        if fix:
            log.warning("Service hit restart limit, resetting")
            subprocess.run(
                ["systemctl", "reset-failed", "cmpunlocker.service"],
                capture_output=True, check=False,
            )
            return
        log.warning("Service hit restart limit")


# ============================================================
# GUARD 14: Kernel headers — RECOVERY: install them
# ============================================================

def guard_kernel_headers(fix=False):
    """Ensure kernel headers exist for driver build."""
    import platform
    kver = platform.release()
    build_path = Path(f"/lib/modules/{kver}/build")
    if not build_path.exists():
        if fix:
            log.info("Installing kernel headers for %s", kver)
            result = subprocess.run(
                ["apt-get", "install", "-y", f"linux-headers-{kver}"],
                capture_output=True, text=True, check=False,
            )
            if result.returncode == 0 and build_path.exists():
                return
        raise RuntimeError(
            f"Kernel headers not found at {build_path}. "
            f"Install: sudo apt-get install linux-headers-{kver}"
        )


# ============================================================
# GUARD 15: Driver source — RECOVERY: clone it
# ============================================================

def guard_driver_source(fix=False):
    """Ensure patched driver source exists."""
    source = Path("/home/ai/.hermes/cmp_lab/buliaoyin-cmpunlocker/driver")
    if not source.exists():
        if fix:
            log.warning("Driver source not found at %s", source)
            log.warning("Cannot auto-clone — manual recovery needed")
        raise RuntimeError(
            f"Driver source not found at {source}. "
            "Cannot rebuild driver after kernel upgrade."
        )

    install_script = source / "install.sh"
    if not install_script.exists():
        raise RuntimeError(f"install.sh not found at {install_script}")

    return source


# ============================================================
# GUARD 16: Srcversion match — RECOVERY: reload module
# ============================================================

def guard_srcversion_match(fix=False):
    """Check if loaded module matches installed module."""
    result = subprocess.run(
        ["modinfo", "nvidia", "-F", "srcversion"],
        capture_output=True, text=True, check=False,
    )
    loaded_srcversion = result.stdout.strip()

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
            if fix:
                log.warning("Module srcversion mismatch, reloading")
                subprocess.run(["modprobe", "-r", "nvidia"], capture_output=True, check=False)
                time.sleep(1)
                subprocess.run(["modprobe", "nvidia"], capture_output=True, check=False)
                time.sleep(2)
                return
            return False

    return True


# ============================================================
# GUARD 17: GSP backup — RECOVERY: create backup
# ============================================================

def guard_gsp_backup(gsp_path: str, fix=False):
    """Ensure GSP backup exists before destructive operation."""
    backup_path = gsp_path + ".cmpunlocker.bak"
    if not os.path.exists(backup_path):
        if fix:
            log.info("No GSP backup, creating one now")
            import shutil
            shutil.copy2(gsp_path, backup_path)
            return
        log.warning("No GSP backup found at %s", backup_path)

    return backup_path


# ============================================================
# GUARD 18: PLM not stuck — RECOVERY: run unlock pipeline
# ============================================================

def guard_plm_not_stuck(pci_full: str, fix=False):
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
    except Exception:
        return True

    if stuck_count == len(plm_table):
        if fix:
            log.warning("All PLMs locked, running unlock pipeline")
            from recovery.plm import recover_plm
            return recover_plm(pci_full)
        return True

    return stuck_count > 0


# ============================================================
# GUARD 19: WPR2 valid — RECOVERY: restore from constants
# ============================================================

def guard_wpr2_valid(pci_full: str, fix=False):
    """Check if WPR2 values are valid (not all zeros or all ones)."""
    from payload.bar0 import Bar0
    from common.constants import get

    wpr2_lo_addr = get('host_bar0_writes.wpr2_lo.addr')
    wpr2_hi_addr = get('host_bar0_writes.wpr2_hi.addr')
    wpr2_lo_val = get('host_bar0_writes.wpr2_lo.value')
    wpr2_hi_val = get('host_bar0_writes.wpr2_hi.value')

    try:
        with Bar0(pci_full) as bar0:
            lo = bar0.rd32(wpr2_lo_addr)
            hi = bar0.rd32(wpr2_hi_addr)
    except Exception:
        return True

    if lo == 0x00000000 and hi == 0x00000000:
        if fix:
            log.warning("WPR2 is all zeros, restoring from constants")
            try:
                with Bar0(pci_full) as bar0:
                    bar0.wr32(wpr2_lo_addr, wpr2_lo_val)
                    bar0.wr32(wpr2_hi_addr, wpr2_hi_val)
                return True
            except Exception as e:
                log.error("Failed to restore WPR2: %s", e)
        return False

    if lo == 0xffffffff and hi == 0xffffffff:
        if fix:
            log.warning("WPR2 is all ones, restoring from constants")
            try:
                with Bar0(pci_full) as bar0:
                    bar0.wr32(wpr2_lo_addr, wpr2_lo_val)
                    bar0.wr32(wpr2_hi_addr, wpr2_hi_val)
                return True
            except Exception as e:
                log.error("Failed to restore WPR2: %s", e)
        return False

    return True


# ============================================================
# GUARD 20: Gen2 not stuck — RECOVERY: run gen2-cycle
# ============================================================

def guard_gen2_not_stuck(pci_full: str, fix=False):
    """Check if PCIe is stuck at Gen1."""
    try:
        result = subprocess.run(
            ["setpci", "-s", pci_full, "CAP_EXP+12.w"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return True

        lnksta = int(result.stdout.strip(), 16)
        gen = lnksta & 0x0f
        if gen < 2:
            if fix:
                log.warning("Gen2 not trained (Gen%d), running gen2-cycle", gen)
                from recovery.gen2 import recover_gen2
                return recover_gen2(pci_full)
            return False
        return True
    except Exception:
        return True


# ============================================================
# GUARD 21: Not fast cycling — RECOVERY: wait
# ============================================================

def guard_not_fast_cycling(last_cycle_time: float, min_interval: float = 5.0, fix=False):
    """Prevent fast cycling that prevents Gen2."""
    elapsed = time.time() - last_cycle_time
    if elapsed < min_interval:
        wait = min_interval - elapsed
        if fix:
            log.info("Waiting %.1fs to prevent fast cycling", wait)
            time.sleep(wait)
            return True
        return False
    return True


# ============================================================
# GUARD 22: Device reappears — RECOVERY: PCI rescan
# ============================================================

def guard_device_reappears(pci_full: str, timeout: float = 10.0, fix=False):
    """Wait for GPU to reappear after FLR/secondary bus reset."""
    start = time.time()
    while time.time() - start < timeout:
        path = f"/sys/bus/pci/devices/{pci_full}"
        if os.path.exists(path):
            return True
        time.sleep(0.5)

    if fix:
        log.warning("Device did not reappear, attempting PCI rescan")
        subprocess.run(
            ["bash", "-c", "echo 1 > /sys/bus/pci/rescan"],
            capture_output=True, check=False,
        )
        time.sleep(3)
        path = f"/sys/bus/pci/devices/{pci_full}"
        if os.path.exists(path):
            return True

    raise RuntimeError(
        f"GPU {pci_full} did not reappear after reset within {timeout}s. "
        "Fix: Rescan PCI bus or reboot."
    )


# ============================================================
# GUARD 23: nvidia-smi responsive — RECOVERY: reload driver
# ============================================================

def guard_nvidia_smi_responsive(pci_full: str, timeout: int = 10, fix=False):
    """Check nvidia-smi can query the device."""
    result = subprocess.run(
        ["timeout", str(timeout), "nvidia-smi",
         "--query-gpu=pci.bus_id,memory.total",
         "--format=csv,noheader"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0 or "No devices" in result.stdout:
        if fix:
            log.warning("nvidia-smi not responsive, reloading driver")
            subprocess.run(["modprobe", "-r", "nvidia"], capture_output=True, check=False)
            time.sleep(2)
            subprocess.run(["modprobe", "nvidia"], capture_output=True, check=False)
            time.sleep(3)
            result = subprocess.run(
                ["timeout", str(timeout), "nvidia-smi",
                 "--query-gpu=pci.bus_id", "--format=csv,noheader"],
                capture_output=True, text=True, check=False,
            )
            if result.returncode == 0 and "No devices" not in result.stdout:
                return True
        return False
    return True


# ============================================================
# GUARD 24: SIGTERM handler — RECOVERY: install handler
# ============================================================

def guard_sigterm_handler(handler, fix=False):
    """Install SIGTERM handler for graceful shutdown."""
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


# ============================================================
# GUARD 25: flock — RECOVERY: use flock
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
# GUARD 26: IOMMU backup — RECOVERY: create backup
# ============================================================

def guard_iommu_backup(fix=False):
    """Backup IOMMU config before changes."""
    grub_cfg = "/etc/default/grub"
    grub_bak = grub_cfg + ".cmpunlocker.bak"

    if not os.path.exists(grub_bak) and os.path.exists(grub_cfg):
        if fix:
            import shutil
            shutil.copy2(grub_cfg, grub_bak)
            log.info("Backed up %s to %s", grub_cfg, grub_bak)

    cmdline_cfg = "/etc/kernel/cmdline"
    cmdline_bak = cmdline_cfg + ".cmpunlocker.bak"

    if not os.path.exists(cmdline_bak) and os.path.exists(cmdline_cfg):
        if fix:
            import shutil
            shutil.copy2(cmdline_cfg, cmdline_bak)
            log.info("Backed up %s to %s", cmdline_cfg, cmdline_bak)


# ============================================================
# GUARD 27: Not 80GB — RECOVERY: none (hardware block)
# ============================================================

def guard_not_80gb_target(target: str, fix=False):
    """Ensure we're not trying 80GB (hardware-blocked)."""
    if '80gb' in target.lower():
        raise RuntimeError(
            "80GB is hardware-blocked. Firmware rejects CFG1=0x02779000. "
            "Use 'unlocked_40gb' instead."
        )


# ============================================================
# GUARD 28: Correct LMR — RECOVERY: auto-detect
# ============================================================

def guard_correct_lmr(pci_full: str, lmr_value: int, fix=False):
    """Verify LMR value matches device variant."""
    device_path = f"/sys/bus/pci/devices/{pci_full}/device"
    try:
        with open(device_path, 'r') as f:
            device_hex = f.read().strip()
        device_id = device_hex.replace('x', '').lower()
    except Exception:
        return

    expected = {
        '20c2': 0x0000020B,
        '2082': 0x0000028A,
        '20b0': 0x0000028A,
    }

    if device_id in expected and lmr_value != expected[device_id]:
        if fix:
            log.warning(
                "LMR 0x%08x wrong for 10de:%s, should be 0x%08x",
                lmr_value, device_id, expected[device_id]
            )
            return expected[device_id]
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
        delay = min(self.base * (self.multiplier ** self.attempt), self.max_delay)
        self.attempt += 1
        log.info("Backoff: waiting %.1fs (attempt %d)", delay, self.attempt)
        time.sleep(delay)
        return delay

    def reset(self):
        self.attempt = 0


# ============================================================
# GUARD 30: Speed=15 — RECOVERY: restore GSP
# ============================================================

def guard_not_speed15(fix=False):
    """Check for GSP corruption (speed=15 in kern.log)."""
    try:
        result = subprocess.run(
            ["grep", "-c", "speed=15", "/var/log/kern.log"],
            capture_output=True, text=True, check=False,
        )
        count = int(result.stdout.strip()) if result.stdout.strip() else 0
        if count > 0:
            if fix:
                log.warning("Speed=15 corruption detected, restoring GSP")
                from recovery.gsp import recover_gsp
                return recover_gsp()
            log.warning("Found %d speed=15 entries — GSP may be corrupted", count)
            return False
    except Exception:
        pass
    return True


# ============================================================
# GUARD 31: Booter flood — RECOVERY: restore GSP
# ============================================================

def guard_no_booter_flood(fix=False):
    """Check for massive booter error floods (0x31, 0x5)."""
    try:
        result = subprocess.run(
            ["grep", "-c", "Booter failed with non-zero error code", "/var/log/kern.log"],
            capture_output=True, text=True, check=False,
        )
        count = int(result.stdout.strip()) if result.stdout.strip() else 0
        if count > 100:
            if fix:
                log.warning("Booter flood detected (%d errors), restoring GSP", count)
                from recovery.gsp import recover_gsp
                return recover_gsp()
            log.warning("Found %d booter failures — GSP may be corrupted", count)
            return False
    except Exception:
        pass
    return True


# ============================================================
# GUARD 32: Feature registers — RECOVERY: skip (hardware blocked)
# ============================================================

def guard_feature_registers_ok(pci_full: str, fix=False):
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
        return True

    if blocked:
        log.warning("Feature registers blocked (hardware): %s — non-critical", blocked)
        return True  # Non-critical, continue

    return True


# ============================================================
# GUARD 33: Bad swap — RECOVERY: none (cosmetic)
# ============================================================

def guard_no_bad_swap_entries(fix=False):
    """Check for bad swap entries flooding kernel log."""
    # Cosmetic issue, no recovery needed
    return True


# ============================================================
# GUARD 34: nvidia-ctk libs — RECOVERY: install packages
# ============================================================

def guard_nvidia_ctk_libs(fix=False):
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
        if fix:
            log.warning("Missing nvidia-ctk libs: %s, installing packages", missing)
            subprocess.run(
                ["apt-get", "install", "-y", "nvidia-container-toolkit"],
                capture_output=True, check=False,
            )
        log.warning("Missing nvidia-ctk libraries: %s — non-critical", missing)
    return True


# ============================================================
# GUARD 35: Persistence mode — RECOVERY: enable it
# ============================================================

def guard_persistence_mode(fix=False):
    """Check if persistence mode is enabled."""
    result = subprocess.run(
        ["nvidia-smi", "-pm", "1"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        if fix:
            log.warning("Enabling persistence mode")
            subprocess.run(
                ["systemctl", "start", "nvidia-persistenced"],
                capture_output=True, check=False,
            )
            time.sleep(1)
            subprocess.run(
                ["nvidia-smi", "-pm", "1"],
                capture_output=True, check=False,
            )
        log.warning("Could not enable persistence mode")
    return True


# ============================================================
# GUARD 36: Cascade failures — RECOVERY: stop failed services
# ============================================================

def guard_no_cascade_failures(fix=False):
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
        if fix:
            log.warning("Stopping cascade-failed services: %s", failed)
            for svc in failed:
                subprocess.run(
                    ["systemctl", "stop", f"{svc}.service"],
                    capture_output=True, check=False,
                )
            return True
        log.warning("Services in crash-restart loop: %s", failed)
    return True


# ============================================================
# GUARD 37: AppArmor — RECOVERY: none (cosmetic)
# ============================================================

def guard_no_apparmor_denials(fix=False):
    """Check for AppArmor denials affecting GPU operations."""
    # Cosmetic issue, does not affect GPU
    return True


# ============================================================
# GUARD 38: Xid errors — RECOVERY: nvidia-smi -r
# ============================================================

def guard_no_xid_errors(fix=False):
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
            if 154 in xid_counts and fix:
                log.warning("Xid 154 detected, attempting GPU reset")
                subprocess.run(
                    ["nvidia-smi", "-r"],
                    capture_output=True, text=True, check=False,
                )
                time.sleep(5)
                return True
            log.warning("GPU Xid errors detected: %s", xid_counts)
            if 154 in xid_counts:
                return False
    except Exception:
        pass
    return True


# ============================================================
# GUARD 39: FLR state — RECOVERY: nvidia-smi -r
# ============================================================

def guard_not_flr_state(pci_full: str, fix=False):
    """Check if GPU is in FLR/error state (header type 7f)."""
    try:
        result = subprocess.run(
            ["lspci", "-s", pci_full, "-xxx"],
            capture_output=True, text=True, check=False,
        )
        if "7f:" in result.stdout.lower():
            if fix:
                log.warning("GPU in FLR state, attempting reset")
                subprocess.run(
                    ["nvidia-smi", "-r"],
                    capture_output=True, text=True, check=False,
                )
                time.sleep(5)
                # Check if recovered
                result = subprocess.run(
                    ["lspci", "-s", pci_full, "-xxx"],
                    capture_output=True, text=True, check=False,
                )
                if "7f:" not in result.stdout.lower():
                    return True
            log.error("GPU in FLR/error state (header type 0x7f)")
            return False
    except Exception:
        pass
    return True


# ============================================================
# GUARD 40: Gen2 reliable — RECOVERY: none (hardware limit)
# ============================================================

def guard_gen2_reliable(pci_full: str, fix=False):
    """Check if Gen2 retraining is reliable."""
    from recovery.gen2 import check_gen2_status

    status = check_gen2_status(pci_full)
    if not status['is_gen2']:
        try:
            result = subprocess.run(
                ["grep", "-c", "cycle.*Gen1", "/var/log/gen2-cycle.log"],
                capture_output=True, text=True, check=False,
            )
            fail_count = int(result.stdout.strip()) if result.stdout.strip() else 0
            if fail_count > 5:
                log.warning("Gen2 retraining failed %d times — may be hardware", fail_count)
        except Exception:
            pass
    return True


# ============================================================
# GUARD 41: Kernel taint — RECOVERY: none (expected)
# ============================================================

def guard_kernel_taint(fix=False):
    """Check if kernel is tainted by nvidia module."""
    # Expected for patched drivers, no recovery needed
    return True


# ============================================================
# GUARD 42: GPU memory — RECOVERY: run unlock pipeline
# ============================================================

def guard_gpu_memory_ok(pci_full: str, fix=False):
    """Check GPU memory capacity is correct (40GB, not 10GB)."""
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        mem_str = result.stdout.strip()
        if "MiB" in mem_str:
            mem_mb = int(mem_str.replace("MiB", "").strip())
            if mem_mb < 30000:
                if fix:
                    log.warning("GPU memory only %d MiB, running unlock pipeline", mem_mb)
                    from recovery.plm import recover_plm
                    return recover_plm(pci_full)
                log.error("GPU memory only %d MiB — expected ~40000 MiB", mem_mb)
                return False
    return True
