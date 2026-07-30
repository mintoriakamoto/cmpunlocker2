# Clean SEC2 same-Booter one-run package

This directory is the isolated, installable checkpoint for the CMP 170HX
compute/memory unlock. It starts from pristine NVIDIA open kernel modules
`610.43.03` and applies exactly two ordered patches:

1. the published clean SEC2 one-run compute/memory unlock;
2. the minimal same-Booter handoff that starts the signed stock GSP-RM
   directly from the original stock Booter.

Additional documentation:

- `MANY-WRITES.md`: append up to 78 additional arbitrary writes while
  preserving the nine protected handoff writes and final marker.
- `CUSTOM-CODE.md`: historical notes for the separate non-atomic TU10x/GSP
  IMEM plus private-sequencer payload; it is not part of this clean path.

The protected SEC2 path performs nine real writes before the signed handoff:

| # | Register | Value |
|---:|---:|---:|
| 0 | `0x001fa7cc` | `0xfffff0ff` |
| 1 | `0x009a0148` | `0xffffffff` |
| 2 | `0x001fa7c4` | `0xffffffff` |
| 3 | `0x00823804` | `0xffffffff` |
| 4 | `0x0082381c` | `0x88888888` |
| 5 | `0x00823820` | `0x00000008` |
| 6 | `0x009a0204` | device-specific CFG1 |
| 7 | `0x00100ce0` | device-specific LMR |
| 8 | `0x001180f8` | clear the Booter pre-image-start gate |

Runtime geometry:

- `0x20c2`: `CFG1=0x02779000`, `LMR=0x0000020b`
- `0x2082`: `CFG1=0x02669000`, `LMR=0x0000028a`

The writer table is padded to exactly 88 slots. Slot 87 writes the completion
marker `0x53310058`. The tail then repairs the Booter DMEM descriptor, resumes
the interrupted signed-image verification, starts the authentic stock GSP-RM,
releases the secure mutex with the preserved runtime owner, commits
`0x001180f8=0x11000000`, and rejoins stock status reporting and secure
teardown.

The normal TU102 bootstrap order remains intact: stock Scrubber, optional
FWSEC, RISC-V reset and boot arguments run first. A narrow staging hook then
prepares the overflow immediately before the existing Booter call. That one
original Booter performs the protected writes and signed GSP handoff; a narrow
completion hook verifies it before the unchanged Init RPCs and GSP-ready wait.
There is no early Booter, duplicate Booter, or skip branch.

Deliberately absent:

- repository patches `0003` and later
- PCIe, UPHY, target-speed, link-rate, LTSSM, or retrain writes
- SYS decode traps, fuse overrides, XVE/XP experiments, and FLR logic
- the obsolete atomic Gen3 experiment
- automatic reboot or power-cycle

The patch still includes the host-side WPR/GSP framebuffer metadata handling
that belongs to the original compute/memory unlock. It does not include the
later late-PMA, PRAMIN, CE-scrub, or persistent-state workarounds. This makes
the package an honest isolation test of the one-run primitive.

## Build

```bash
cd cmpunlocker
./verify.sh
./build.sh
```

The build downloads the pinned NVIDIA source tarball if needed, verifies its
SHA-256, applies only `0001` followed by `0002`, verifies the clean same-Booter
invariants, builds all five modules, and stages them under
`out/$(uname -r)/`.

An existing tarball can be supplied without downloading:

```bash
SEC2_ONE_RUN_TARBALL=/path/to/open-gpu-kernel-modules-610.43.03.tar.gz \
./build.sh
```

## Install

The default install now also downloads and installs the matching official
NVIDIA `610.43.03` userspace, including `nvidia-smi`, NVML, CUDA driver
libraries, and GSP firmware. The NVIDIA `.run` installer is invoked with
`--no-kernel-modules`, so it cannot replace the patched modules. Its pinned
SHA-256 is checked before execution.

Install matching userspace and modules on disk without touching the currently
loaded kernel driver:

```bash
sudo ./install.sh
```

Install matching userspace and modules, then attempt an immediate module
reload:

```bash
sudo ./install.sh --load
```

Use `--modules-only` only if an independently installed NVIDIA userspace
already matches `610.43.03`:

```bash
sudo ./install.sh --modules-only
```

The official 461 MB userspace installer is cached in
`/var/cache/cmpunlocker-sec2-one-run/`, not inside this artifact. An already
downloaded runfile can be supplied explicitly:

```bash
sudo SEC2_ONE_RUN_DRIVER_RUNFILE=/path/to/NVIDIA-Linux-x86_64-610.43.03.run \
    ./install.sh
```

Add `--initramfs` only when this exact module should also be placed into the
next boot's initramfs. The script never reboots the host.

Remove the installed files:

```bash
sudo ./uninstall.sh
```

`uninstall.sh` intentionally removes only the five patched kernel modules. It
does not uninstall NVIDIA userspace because that could also remove CUDA/NVML
components used by other installed kernels.

## Expected proof

The decisive log sequence is:

```text
SEC2_DEBUG: same-Booter payload staged for the original stock Booter (9 protected writes, 88 fixed pre-handoff slots)
SEC2_DEBUG: original stock Booter returned; marker=0x53310058 expected=0x53310058
SEC2_DEBUG: same original stock Booter completed the signed GSP handoff; ...
SEC2_DEBUG: one-run PLM/compute/memory verify ...
```

Unlike the older writer-only run, this path requires Booter status `0x0`, the
exact marker, all eight compute/memory readbacks, and the exact handoff token.
A nonzero Booter status is fatal because it means the signed continuation did
not finish its release/report tail.

This exact stock-near one-Booter sequence was live-validated on device
`10de:2082` on 2026-07-30 after an explicit FLR. The original Booter returned
status `0`, produced marker `0x53310058`, completed the signed handoff, and
continued through the stock Init RPCs. Repeated `nvidia-smi` checks reported
the unlocked `40960 MiB`. Every PCIe experiment remains excluded.

Build-validation hashes:

- source tarball:
  `9df87d753cd9c05aa0eedc462af9b35debb549a657136e863282f94c96ee2640`
- clean patch:
  `132b74e77a0a3311c82b272b690e78d1fb4bf3a2cfd46b2159feab5d90bf577a`
- same-Booter patch:
  `1b0b151ce4ada96f67d8f117e066310be85daa6da2180ee29a38d12f9a9f7709`
- live-tested clean `nvidia.ko`:
  `3eebde60dd5586e15ccdc87327d7756ea6f98126d0fdf45c60ca093cdacc3678`
