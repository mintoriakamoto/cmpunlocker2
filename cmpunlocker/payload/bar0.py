"""
bar0.py — GPU BAR0 register space memory-mapped I/O access.

BAR0 (Base Address Register 0) is the GPU's primary register space accessed
via /sys/bus/pci/devices/{pci_addr}/resource0. All hardware unlock operations
(PLM writes, CFG1/LMR memory config, SS0/SS1 compute unlock) write to BAR0.

This module provides a context manager for safe BAR0 access with automatic
resource cleanup. All reads/writes are little-endian 32-bit integers.

Permissions: Requires root access (BAR0 is device-level I/O memory).
"""

import mmap
import os
import struct
from typing import Any

from cmpunlocker.common.constants import get
from cmpunlocker.payload.gpu import bar0_path


class Bar0:

    def __init__(self, pci_full: str):
        path = bar0_path(pci_full)
        try:
            self._fd = os.open(path, os.O_RDWR)
        except FileNotFoundError:
            raise RuntimeError(
                f"BAR0 not found: {path}\n"
                "Causes: (1) Driver not loaded, (2) Device missing, "
                "(3) Device disabled in BIOS\n"
                "Fix: Verify 'nvidia-smi' works and shows GPU"
            )
        except PermissionError:
            raise RuntimeError(
                f"Permission denied reading BAR0: {path}\n"
                "Causes: Not running as root, or SELinux/AppArmor blocks access\n"
                "Fix: Run with 'sudo' or check security policies"
            )
        try:
            mmap_size = get('dmem_layout.bar0_mmap_size')
            self._mm = mmap.mmap(self._fd, mmap_size, access=mmap.ACCESS_WRITE)
        except OSError as e:
            os.close(self._fd)
            raise RuntimeError(
                f"Cannot mmap BAR0 ({mmap_size} bytes): {e}\n"
                "Causes: Insufficient privileges, or device/memory error\n"
                "Fix: Ensure running as root with no other GPU clients"
            )

    def rd32(self, off: int) -> int:
        return struct.unpack_from("<I", self._mm, off)[0]

    def wr32(self, off: int, val: int) -> None:
        struct.pack_into("<I", self._mm, off, val & 0xFFFFFFFF)

    def close(self) -> None:
        self._mm.close()
        os.close(self._fd)

    def __enter__(self) -> "Bar0":
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None,
                 exc_tb: Any) -> None:
        self.close()
