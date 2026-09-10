import glob
import logging
import os
import subprocess
import time

log = logging.getLogger(__name__)


def stop_display_manager() -> None:
    for svc in ("gdm3", "sddm", "lightdm", "display-manager"):
        subprocess.run(["systemctl", "stop", svc], capture_output=True, check=False)
    subprocess.run(["killall", "-9", "Xorg", "Xwayland", "nvidia-persistenced"],
                   capture_output=True, check=False)
    time.sleep(2)


def unload_modules() -> None:
    for mod in ("nvidia-uvm", "nvidia_drm", "nvidia_modeset", "nvidia"):
        subprocess.run(["modprobe", "-r", mod], capture_output=True, check=False)
    time.sleep(2)

    # Verify modules actually unloaded
    lsmod = subprocess.run(["lsmod"], capture_output=True, text=True, check=False).stdout
    if "nvidia" in lsmod:
        log.warning("nvidia modules still loaded after modprobe -r, trying rmmod -f")
        for mod in ("nvidia_uvm", "nvidia_drm", "nvidia_modeset", "nvidia"):
            subprocess.run(["rmmod", "-f", mod], capture_output=True, check=False)
        time.sleep(1)


def load_module() -> None:
    result = subprocess.run(["modprobe", "nvidia"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"modprobe nvidia failed: {result.stderr.strip()}")


def is_device_visible(pci_full: str) -> bool:
    """Check if the GPU is visible in PCI."""
    path = f"/sys/bus/pci/devices/{pci_full}"
    return os.path.exists(path)


def is_driver_loaded(pci_full: str) -> bool:
    """Check if nvidia driver is bound to the GPU."""
    driver_path = f"/sys/bus/pci/devices/{pci_full}/driver"
    if not os.path.exists(driver_path):
        return False
    driver = os.path.basename(os.readlink(driver_path))
    return "nvidia" in driver


def flr_reset(pci_full: str) -> bool:
    """Perform Function Level Reset on the GPU.

    Returns True if reset succeeded and device is visible after.
    Must be called with nvidia module UNLOADED.
    """
    if not is_device_visible(pci_full):
        log.error("[%s] Device not visible before FLR", pci_full)
        return False

    reset_path = f"/sys/bus/pci/devices/{pci_full}/reset"
    try:
        with open(reset_path, "w", encoding="utf-8") as f:
            f.write("1")
    except OSError as e:
        log.error("[%s] FLR write failed: %s", pci_full, e)
        return False

    # Wait for device to re-enumerate
    time.sleep(3)

    if not is_device_visible(pci_full):
        log.error("[%s] Device disappeared after FLR", pci_full)
        return False

    log.info("[%s] FLR reset succeeded", pci_full)
    return True


def aggressive_unload() -> None:
    """Unload nvidia modules and kill all GPU users."""
    my_pid = str(os.getpid())

    stop_display_manager()
    subprocess.run(["systemctl", "stop", "nvidia-persistenced"], capture_output=True, check=False)

    # Kill any processes using nvidia devices
    for dev in glob.glob("/dev/nvidia*") + ["/dev/nvidiactl"]:
        if not os.path.exists(dev):
            continue
        res = subprocess.run(["fuser", dev], capture_output=True, text=True, check=False)
        for pid in res.stdout.split():
            if pid != my_pid:
                subprocess.run(["kill", "-9", pid], capture_output=True, check=False)
    time.sleep(1)

    unload_modules()
