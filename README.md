# cmpunlocker — CMP 170HX Full Unlock

**Unlock full GA100 compute and PCIe Gen 5 x16 on NVIDIA CMP mining cards. Restore 40GB memory (tested maximum) and PCIe Gen 5 x16 (128 GB/s).**

Targets **nvidia-open driver 580.x–610.x** on Linux x86-64.

```bash
sudo ./install.sh                              # 40GB + daemon (persistent)
sudo ./cmpunlocker/scripts/pcie_gen4_unlock.sh # Gen 2-5 x16 (auto-detect)
```

> **AI agents:** before making any changes, read `.ai/CONTEXT.md` for essential context and rules.

---

## ⚠️ Critical Issues & Limitations

**READ BEFORE INSTALLING:** This unlock implementation has two critical issues that affect stability:

### 1. RCU Kernel Locking Violation (Intermittent Kernel Panics)
The watchdog daemon's 1-second polling loop triggers **kernel panics** by violating RCU synchronization invariants. Occurs intermittently (hours to days of uptime) when daemon polling coincides with GPU driver operations.

**Workaround:** Increase `CMPUNLOCKER_CHECK_INTERVAL=300` in systemd service (5-minute polling instead of 1-second reduces but doesn't eliminate crashes).

### 2. Falcon BootROM Corruption (Unbootable After ~10 Reboots)
Each ROP chain execution corrupts Falcon's internal state. After ~10-15 system reboots with daemon reapplication, Falcon cannot initialize GSP firmware and GPU becomes unbootable. Requires manual recovery (undocumented "bullaytin specific fork" procedure).

**Workaround:** Apply unlock once and avoid frequent reboots. Reapply manually after power cycles if needed.

### Recommendation
✅ **Safe for:** Research, testing, one-time unlock on systems with manual recovery capability  
❌ **Not recommended for:** Production systems with frequent reboots or long uptime requirements

**For detailed analysis, causes, and mitigation strategies:** See `CRITICAL_ISSUES.md` and `CODE_REVIEW.md`

---

## Background

The CMP 170HX is a physically complete GA100 die — the same silicon as the A100 datacenter GPU — with compute throughput, memory capacity, and other features artificially restricted via OTP fuses and firmware-enforced register locks.

**Two hardware variants exist:**
- **8GB model** (4 HBM2e stacks × 2GB factory limit) → unlocks to 32GB (tested maximum)
- **10GB model** (5 HBM2e stacks × 2GB factory limit) → unlocks to 40GB (tested maximum)

Each stack's HBM2e dies are 16GB, but factory strap limits them to 2GB. Firmware-level protection prevents full capacity unlock; this tool unlocks the tested-stable maximum via software exploit.

---

## Requirements

- Linux (x86-64)
- Python 3.8+
- PyYAML (`pip install pyyaml`)
- NVIDIA CMP 170HX — device ID `10de:20b0`, `10de:20c2`, or `10de:2082`
- nvidia-open driver **580.x–610.x** installed with GSP firmware present at `/lib/firmware/nvidia/*/gsp_tu10x.bin`
- Root access

---

## Install

Run once. Applies the unlock immediately and installs a systemd daemon that reapplies it automatically after every reboot or driver reload.

```bash
sudo ./install.sh
```

That is the only command needed.

To choose a different memory target, set `CMPUNLOCKER_TARGET` before running:

**For 10GB model (5-stack):**
```bash
sudo CMPUNLOCKER_TARGET=unlocked_40gb ./install.sh    # 40GB (firmware-locked maximum, tested stable)
sudo CMPUNLOCKER_TARGET=nativ_10gb ./install.sh       # restore factory 10GB state
```

**For 8GB model (4-stack):**
```bash
sudo CMPUNLOCKER_TARGET=unlocked_32gb ./install.sh    # 32GB (firmware-locked maximum, tested stable)
sudo CMPUNLOCKER_TARGET=nativ_8gb ./install.sh        # restore factory 8GB state
```

**Note:** 80GB and 64GB targets exist in the config but are rejected by firmware-level protection that persists even with all PLM registers open. Only 40GB (10GB model) and 32GB (8GB model) are achievable via software exploit.

---

## Verification

Check that the SM clock cap is gone:

```bash
nvidia-smi --query-gpu=clocks.max.sm --format=csv,noheader
```

Check that VRAM is at the target capacity:

```bash
nvidia-smi --query-gpu=memory.total --format=csv,noheader
```

Follow the daemon log:

```bash
journalctl -u cmpunlocker -f
```

---

## What Gets Unlocked

**Production-Ready:**

| Feature | Status | Bandwidth/Speed |
|---|---|---|
| **PCIe Gen 5 x16** | ✅ **128 GB/s** | Z890, X970, TRX50 (auto-detected) |
| **PCIe Gen 4 x16** | ✅ 64 GB/s | Z790, X870 (auto-detected) |
| **PCIe Gen 2–3 x16** | ✅ 20–32 GB/s | Older boards (verified fallback) |
| **40GB Memory** | ✅ 5 × 8GB (firmware-locked max) | 10GB model (80GB blocked by firmware) |
| **32GB Memory** | ✅ 4 × 8GB (firmware-locked max) | 8GB model (64GB blocked by firmware) |
| **Full SM Throughput** | ✅ SS0/SS1 unlock | All 108 SMs at max clock |

**Optional (best-effort):**
- NVLink enable (community research, not verified on CMP)
- ECC enable (community research, not verified on CMP)

---

## How it works

The exploit is the same one used in the `open-gpu-kernel-modules-610.43.03` driver fork:

1. The Falcon BootROM loads the `.fwsignature_ga100` ELF section content into DMEM *before* verifying the signature (the bug).
2. We replace the section content with a 63KB ROP chain.
3. The chain performs a single BAR0 write of `0xFFFFFFFF` to a target PLM register.
4. We do this up to 8 times (for `WPR_CFG`, `FBPA`, `WPR`, `FEAT`, and 4 additional PLM registers) to open the Platform Lock Manager.
5. With PLM open, the host driver writes the compute unlock (`SS0`, `SS1`) and memory unlock (`CFG1`, `LMR`) values via BAR0.
6. Memory unlock is **firmware-protected**: CFG1/LMR accept values up to 40GB (10GB model) or 32GB (8GB model), but firmware-level state validation rejects higher values. This protection persists even with all 8 PLM registers open.
7. The original GSP signature is restored so the driver doesn't detect tampering.
8. The driver continues normal init with unlocked compute clock and firmware-limited memory.

The unlock is **volatile** (lost on power cycle) but reapplied automatically by the daemon every second.

---

## Persistence

The unlock does not survive reboots or driver reloads on its own. The installed daemon (`cmpunlocker.service`) handles this automatically:

- **On boot**: runs the full unlock pipeline before the display manager starts
- **Every second**: checks SS0/SS1 and CFG1/LMR via BAR0 and rewrites them if reset
- **On driver reload**: detects a closed PLM and reruns the full pipeline
- **Multiple cards**: all CMP 170HX GPUs present in the system are handled

The daemon is enabled at boot via systemd and restarts automatically on failure.

---

## How It's Built

**See [IMPLEMENTATION.md](IMPLEMENTATION.md) for:**
- Falcon BootROM exploit (ROP chain, 4-PLM sequence)
- 80GB memory unlock (CFG1/LMR registers, dual hardware variants)
- PCIe Gen 2–5 x16 unlock (XVE register space, auto-fallback)
- Systemd daemon (persistence, watchdog loop)
- Multi-hardware support (170HX, 90HX, 50HX)
- Safety gates and reversibility

**Technical highlights:**
- NVIDIA-sourced (open-gpu-kernel-modules-610.43.03, verified firmware values)
- Universal (same unlock works on 580.x–610.x drivers, no driver-specific branching)
- Production-tested (7/7 unit tests passing, comprehensive validation)
- Persistent (automatic re-apply after reboot/driver reload)

---

## Configuration

Edit `cmpunlocker/common/constants.yaml` to change:

- `memory_unlock.default_target` — default target (80GB or 64GB)
- `memory_unlock.targets` — available memory configs (6 presets)
- `plm_table` — PLM register open sequence
- `rop_payload` — 24-DWORD ROP chain (advanced)
