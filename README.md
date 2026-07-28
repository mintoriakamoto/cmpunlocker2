# Clean SEC2 one-run package

This directory is the isolated, installable checkpoint for the CMP 170HX
compute/memory unlock. It starts from pristine NVIDIA open kernel modules
`610.43.03` and applies exactly one squashed patch.

Additional documentation:

- `MANY-WRITES.md`: append up to 91 additional arbitrary writes while
  preserving the eight compute/memory writes and final marker.
- `CUSTOM-CODE.md`: reproduce the live-proven non-atomic TU10x/GSP IMEM plus
  private-sequencer path for a small privileged RV64 payload.

The protected SEC2 table contains nine entries in one execution:

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
| 8 | `0x000014fc` | completion marker |

Runtime geometry:

- `0x20c2`: `CFG1=0x02779000`, `LMR=0x0000020b`
- `0x2082`: `CFG1=0x02669000`, `LMR=0x0000028a`

The final writer enters a relocated copy of the proven stock one-write cleanup
frame. This is required so the normal GSP Booter can run immediately after the
exploit.

Deliberately absent:

- repository patches `0002` through `0007`
- PCIe, UPHY, target-speed, link-rate, LTSSM, or retrain writes
- the obsolete atomic Gen3 experiment
- automatic reboot or power-cycle

The patch still includes the host-side WPR/GSP framebuffer metadata handling
that belongs to the original compute/memory unlock. It does not include the
later late-PMA, PRAMIN, CE-scrub, or persistent-state workarounds. This makes
the package an honest isolation test of the one-run primitive.

## Build

```bash
cd /root/cmpunlocker/artifacts/sec2-one-run-clean
./verify.sh
./build.sh
```

The build downloads the pinned NVIDIA source tarball if needed, verifies its
SHA-256, applies the single patch, verifies the one-run invariants, builds all
five modules, and stages them under `out/$(uname -r)/`.

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
SEC2_DEBUG: one-run unlock starting (9 protected writes)
SEC2_DEBUG: one-run status=0xffff marker=0x53310009 expected=0x53310009
SEC2_DEBUG: one-run PLM/compute/memory verify ...
```

`0xffff` is the stale Booter mailbox value, not failure. Exact marker and
target-register readback are authoritative. A later normal GSP Booter must
return status `0x0`.

The one-run `kernel_gsp.c` and SEC2 cleanup were live-validated on device
`10de:2082` on 2026-07-28. The generic `markerIndex` change only removes the
fixed nine-entry marker index so additional writes can be inserted before the
last entry; it does not change the proven nine-write byte layout. This exact
package was rebuilt from the pinned pristine tarball against kernel
`6.12.85+deb13-amd64`; all five modules linked successfully. The rebuilt
generic-index variant has not been live-loaded.

Build-validation hashes:

- source tarball:
  `9df87d753cd9c05aa0eedc462af9b35debb549a657136e863282f94c96ee2640`
- clean patch:
  `132b74e77a0a3311c82b272b690e78d1fb4bf3a2cfd46b2159feab5d90bf577a`
- clean `nvidia.ko`:
  `df94a1e351f5b7fd56bb621b108fbc6f07bcac4a6191802930f1013f9a4d63db`
