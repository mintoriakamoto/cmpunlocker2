"""
recovery/gen2.py — Gen2 PCIe link training recovery.

After cold boot, the GPU's PCIe link may train at Gen1 (5GT/s) instead
of Gen2 (5GT/s). This is because the booter's LC2/PL writes don't stick
on the 1st boot after a secondary bus reset.

Recovery uses a 2-boot cycle:
1. rmmod → secondary bus reset → modprobe (booter writes, don't stick)
2. rmmod → secondary bus reset → modprobe (booter writes STICK)
3. nvidia-smi → triggers probe-retrain → Gen2 achieved

The probe-retrain is handled by the patched driver's
nv_cmp170hx_retrain_gen2() function.
"""

import glob
import logging
import os
import subprocess
import time

log = logging.getLogger(__name__)

# GPU PCI BDF — auto-detected at runtime
_GPU_BDF = None


def _get_gpu_bdf() -> str:
    """Auto-detect GPU PCI BDF."""
    global _GPU_BDF
    if _GPU_BDF:
        return _GPU_BDF

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
            _GPU_BDF = bdf
            return _GPU_BDF

    raise RuntimeError("CMP 170HX GPU not found in lspci")


def check_gen2_status(pci_full: str = None) -> dict:
    """Check current PCIe link generation.

    Returns:
        dict with 'gen' (int), 'speed' (str), 'width' (str), 'is_gen2' (bool)
    """
    if pci_full is None:
        pci_full = _get_gpu_bdf()

    try:
        # Read Link Status from PCI config space
        result = subprocess.run(
            ["setpci", "-s", pci_full, "CAP_EXP+12.w"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return {'gen': 0, 'speed': 'unknown', 'width': 'unknown', 'is_gen2': False}

        lnksta = int(result.stdout.strip(), 16)
        gen = lnksta & 0x0f
        width = (lnksta >> 4) & 0x3f

        speed_map = {1: '2.5GT/s', 2: '5GT/s', 3: '8GT/s', 4: '16GT/s'}
        return {
            'gen': gen,
            'speed': speed_map.get(gen, f'{gen}GT/s'),
            'width': f'x{width}',
            'is_gen2': gen >= 2,
        }
    except Exception as e:
        log.error("Failed to check Gen2 status: %s", e)
        return {'gen': 0, 'speed': 'unknown', 'width': 'unknown', 'is_gen2': False}


def recover_gen2(pci_full: str = None, max_cycles: int = 3,
                 cycle_delay: float = 2.0) -> bool:
    """Recover Gen2 PCIe link training.

    Uses the 2-boot cycle method:
    1. Stop all GPU processes and services
    2. Unload nvidia modules (triggers secondary bus reset)
    3. Reload nvidia (booter runs, writes LC2/PL)
    4. Open GPU with nvidia-smi (triggers probe-retrain)
    5. Repeat if Gen2 not achieved

    Args:
        pci_full: GPU PCI BDF (auto-detected if None)
        max_cycles: Maximum number of 2-boot cycles to attempt
        cycle_delay: Seconds to wait after each rmmod for bus reset

    Returns True if Gen2 achieved.
    """
    if pci_full is None:
        pci_full = _get_gpu_bdf()

    # Check if already Gen2
    status = check_gen2_status(pci_full)
    if status['is_gen2']:
        log.info("Already Gen%d (%s), skipping recovery", status['gen'], status['speed'])
        return True

    log.info("Gen2 recovery: current Gen%d (%s), attempting up to %d cycles",
             status['gen'], status['speed'], max_cycles)

    # Stop GPU-holding services
    _stop_gpu_services()

    # Kill any remaining GPU processes
    _kill_gpu_processes()

    # Verify modules are free
    if not _verify_modules_free():
        log.error("Cannot unload nvidia — modules still in use")
        return False

    for cycle in range(1, max_cycles + 1):
        log.info("Cycle %d/%d: performing 2-boot Gen2 cycle", cycle, max_cycles)

        # Boot 1: Fresh boot after secondary bus reset
        log.info("  Boot 1: unloading modules...")
        _unload_modules()
        time.sleep(cycle_delay)

        log.info("  Boot 1: loading nvidia...")
        _load_nvidia()
        time.sleep(2)

        # Trigger probe-retrain by opening GPU
        subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader"],
            capture_output=True, check=False,
        )
        time.sleep(2)

        # Boot 2: Reload after another secondary bus reset
        log.info("  Boot 2: unloading modules...")
        _unload_modules()
        time.sleep(cycle_delay)

        log.info("  Boot 2: loading nvidia...")
        _load_nvidia()
        time.sleep(2)

        # Trigger probe-retrain
        subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader"],
            capture_output=True, check=False,
        )
        time.sleep(3)

        # Check if Gen2 achieved
        status = check_gen2_status(pci_full)
        if status['is_gen2']:
            log.info("SUCCESS: Gen%d (%s) achieved at cycle %d",
                     status['gen'], status['speed'], cycle)
            _restart_gpu_services()
            return True

        log.info("  Cycle %d: Gen%d (not yet Gen2)", cycle, status['gen'])

    log.error("FAILED: Gen2 not achieved after %d cycles", max_cycles)
    _restart_gpu_services()
    return False


def _stop_gpu_services() -> None:
    """Stop services that hold nvidia modules."""
    services = [
        'nvidia-cdi-refresh', 'persist-gpu-clocks', 'gen2',
        'cmpunlocker', 'hermes-llama', 'crucible-qwen', 'gdm3',
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

    # Kill nvidia-persistenced
    subprocess.run(
        ["nvidia-persistenced", "--kill"],
        capture_output=True, check=False,
    )
    time.sleep(2)


def _restart_gpu_services() -> None:
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


def _kill_gpu_processes() -> None:
    """Kill any processes using nvidia devices."""
    import glob as glob_mod

    for dev in glob_mod.glob("/dev/nvidia*") + ["/dev/nvidiactl", "/dev/dri/*"]:
        if not os.path.exists(dev):
            continue
        subprocess.run(
            ["fuser", "-k", dev],
            capture_output=True, check=False,
        )
    time.sleep(2)


def _verify_modules_free() -> bool:
    """Verify nvidia modules are not in use."""
    result = subprocess.run(
        ["lsmod"], capture_output=True, text=True, check=False,
    )
    return "nvidia_uvm" not in result.stdout


def _unload_modules() -> None:
    """Unload nvidia modules in correct order."""
    for mod in ("nvidia_uvm", "nvidia_drm", "nvidia_modeset", "nvidia"):
        subprocess.run(
            ["rmmod", mod], capture_output=True, check=False,
        )
    time.sleep(1)

    # Force unload if still loaded
    result = subprocess.run(
        ["lsmod"], capture_output=True, text=True, check=False,
    )
    if "nvidia" in result.stdout:
        log.warning("Modules still loaded, forcing unload")
        for mod in ("nvidia_uvm", "nvidia_drm", "nvidia_modeset", "nvidia"):
            subprocess.run(
                ["rmmod", "-f", mod], capture_output=True, check=False,
            )
        time.sleep(1)


def _load_nvidia() -> None:
    """Load nvidia module."""
    result = subprocess.run(
        ["modprobe", "nvidia"], capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        log.error("modprobe nvidia failed: %s", result.stderr.strip())
