import mmap
import os
import struct

from common.constants import get
from .gpu import bar0_path


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

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
