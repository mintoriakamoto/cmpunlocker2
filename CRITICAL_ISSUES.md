# Critical Issues & Limitations

⚠️ **This document describes critical issues that affect stability and require attention before production deployment.**

## Overview

The CMP 170HX unlock implementation successfully demonstrates the Falcon BootROM ROP exploit and enables 40GB memory unlock. However, two architectural issues prevent stable long-term operation:

1. **RCU Kernel Locking Violation** — Intermittent kernel panics during daemon operation
2. **Falcon BootROM Corruption** — Cumulative state corruption after ~11 PLM writes

Both issues are discovered through code review and real-world testing. Both require significant architectural changes to resolve.

---

## Issue 1: RCU Kernel Locking Violation

### Symptom

```
WARNING: kernel/rcu/tree_plugin.h:332 at rcu_note_context_switch
Voluntary context switch within RCU read-side critical section!
CPU#11: CORRUPTED
Kernel panic — not syncing: RCU read-side CS blocked at rcu_note_context_switch+0xXXX
```

System freezes or reboots unexpectedly while daemon is running.

### Root Cause

The watchdog daemon checks GPU state every 1 second using a busy-loop:

```python
while True:
    for pci in gpus:
        _check_card(pci, state)  # Calls BAR0 reads via unlock/compute.py
    time.sleep(CHECK_INTERVAL)   # <-- VIOLATION: context switch in RCU critical section
```

The `time.sleep(1)` triggers a context switch. If this coincides with GPU driver operations that hold RCU read-side locks, the kernel panics.

**Why it happens:**
- GPU driver code may hold RCU read-side locks internally
- The watchdog's 1-second polling has high collision probability
- RCU critical sections forbid context switches (reads must be atomic)
- `time.sleep()` always triggers a context switch

### Frequency

**Intermittent but deterministic:** Occurs under system load when daemon and GPU driver operations overlap. Not every run, but guaranteed to happen eventually.

### Current Impact

- **Probability**: Medium to high (appears within hours/days of uptime)
- **Severity**: Critical (system crash, data loss risk)
- **Recovery**: Power cycle required

### Temporary Workarounds

1. **Increase CHECK_INTERVAL (Reduces Probability)**
   ```bash
   # Edit /etc/systemd/system/cmpunlocker.service
   Environment="CMPUNLOCKER_CHECK_INTERVAL=300"  # Check every 5 minutes instead of 1 second
   systemctl daemon-reload && systemctl restart cmpunlocker
   ```
   This reduces collision probability but doesn't eliminate the issue.

2. **Disable Daemon (Sacrifices Persistence)**
   ```bash
   systemctl stop cmpunlocker
   systemctl mask cmpunlocker
   ```
   Unlock remains applied until driver reload. Reapply manually after reboots.

3. **Avoid System Load** (Impractical)
   Run GPU workloads during periods of low system activity.

### Permanent Fix

Replace polling with **event-based monitoring**:

**Option A: sysfs inotify (Recommended)**
- Watch `/sys/class/drm/.../` attributes for GPU state changes
- Use `inotify()` syscall to get notified instead of polling
- No busy-loop, no RCU violations

**Option B: netlink Socket**
- Subscribe to GPU state change notifications via kernel generic netlink
- Driver posts events when relevant state changes
- More complex but more reliable

**Option C: uevent Listener**
- Monitor `/dev/` for GPU-related uevents
- Works for device state changes (driver load/unload)
- Won't help for register-level changes

**Recommended implementation:** Replace `while True: time.sleep()` loop with `inotify_add_watch()` on GPU sysfs attributes.

---

## Issue 2: Falcon BootROM Corruption

### Symptom

After 10-15 system boots with daemon running:

```
nvidia 0000:XX:00.0: failed to load fwsignature_ga100
[drm:gk20a_init [nvidia]] *ERROR* failed to start gk20a
[drm:gv100_init_gsp_fw [nvidia]] *ERROR* GSP-RM init failed
GSP firmware initialization: status=0xffff
BooterLoad failures: 728/728
Max GSP-RM boot attempts exceeded (4/4)
RmInitAdapter failed: 0x1000D (RM_ERR_GPU_LOST)
GPU 0 lost, cannot continue
```

**System becomes unbootable.** GPU initialization fails completely; system cannot enter normal boot state.

### Root Cause

The ROP chain exploit executes arbitrary code in the Falcon BootROM context. Each execution:

1. Loads the ROP chain into Falcon DMEM
2. Executes it (opening a single PLM register)
3. Falcon BootROM's execution context is now **corrupted**:
   - Stale values left in registers
   - DMEM state not reset
   - Program counter/stack may be in inconsistent state
4. Restores original GSP signature
5. Driver reloads, Falcon runs again

On the **second ROP chain execution**, Falcon starts with corrupted internal state from the first execution. After ~11 executions, the accumulation of corruption becomes unrecoverable—Falcon cannot initialize GSP firmware.

**Why firmware initialization fails:**
- GSP firmware load sequence checks Falcon's internal state
- Corrupted Falcon state causes GSP signature verification to fail
- Status code `0xffff` indicates all 728 BooterLoad attempts failed
- Falcon can't recover without explicit reset

### Frequency

**Deterministic:** Occurs reliably after ~11 PLM write operations (approximately 10-15 system boots with daemon reapplying unlock).

### Current Impact

- **Probability**: Guaranteed (deterministic)
- **Timeline**: 10-15 reboots
- **Severity**: Critical (GPU unbootable)
- **Recovery**: Requires manual intervention (user performed undocumented recovery using "bullaytin specific fork")

### Why Daemon Triggers It

Each daemon initialization runs full unlock pipeline:
1. Opens 8 PLM registers (8 ROP chain executions)
2. System boots normally
3. Driver reload triggers daemon reapplication
4. Opens 8 PLM registers again
5. Repeat...

After boot 2, Falcon has executed ROP chains 16 times. After boot 3, 24 times. Around boot 2-3, cumulative corruption makes GSP initialization fail.

### Workarounds

1. **Apply Once, Avoid Reboots**
   - Run `./install.sh` once
   - Avoid system reboots and driver reloads
   - Unlock persists in GPU state until power cycle
   - Works indefinitely if system never reboots

2. **Disable Daemon**
   - Apply unlock manually via `./install.sh`
   - Disable daemon: `systemctl mask cmpunlocker`
   - Reapply manually after power cycles or driver unload
   - Manual reapplication works fine (only after 10+ times does corruption occur)

3. **Use "Bullaytin Specific Fork"**
   - User's undocumented recovery procedure somehow resets Falcon state
   - Procedure unknown; requires reverse-engineering or user documentation

### Permanent Fix

**Option A: Reset Falcon State Between ROP Chains (Preferred)**
- Add explicit Falcon reset after each ROP chain execution
- Reset mechanism unknown—requires reverse-engineering Falcon architecture
- Likely requires hardware-level operation (FLR reset already attempted, may need more aggressive reset)

**Option B: Persistent Unlock Without Repeated Falcon Execution**
- Write unlock values to persistent GPU memory (not feasible without firmware modification)
- Firmware changes required to load persisted state on boot

**Option C: Accept Current Limitation**
- Document that unlock works for ~10 boots before requiring recovery
- Provide recovery procedure with instructions
- Not production-viable for systems with frequent reboots

**Recommended investigation:**
1. Research Falcon BootROM reset sequence
2. Attempt explicit Falcon reset via:
   - FALCON_DMEMC register (Falcon DMEM control)
   - SEC2 coprocessor reset
   - Full GPU reset (GPU_RESET register)
   - Hardware PCIe FLR reset (already attempted)
3. Validate that reset clears corruption without affecting PLM open state

---

## Issue 3: Memory Limit Misunderstanding

### Symptom

Users attempt to unlock 80GB (10GB model) or 64GB (8GB model) using configuration targets, but GPU still reports 40GB/32GB maximum.

### Root Cause

The codebase includes `unlocked_80gb` and `unlocked_64gb` targets in configuration, implying these are achievable. However, firmware-level state machine validation rejects these values:

```
CFG1 write request: 0x02779000 (80GB target)
GPU firmware state machine intercepts write
Checks internal limit table: 10GB model max = 0x02669000 (40GB)
Rejects write as exceeding firmware limit
Resets CFG1 to factory default
Result: GPU stays at 40GB
```

The exploit successfully opens PLM and issues the BAR0 write, but firmware-level protection (baked into silicon during manufacturing) rejects values beyond 40GB/32GB.

### Current Impact

- **Severity**: Misleading documentation (not a critical bug)
- **Impact**: Users set `unlocked_80gb` target and wonder why they're capped at 40GB
- **Root cause confusion**: Appears to be software limitation, actually firmware protection

### Fix

Remove false targets from default configuration:

1. **Keep as research-only:**
   - Add to constants.yaml under `# RESEARCH_ONLY_TARGETS` section
   - Document that they don't work

2. **Warn users:**
   - Update `install.sh` to reject these targets with clear error message
   - Explain firmware protection boundary
   - Suggest using `unlocked_40gb` instead

3. **Default to working target:**
   - Change default from `unlocked_80gb` to `unlocked_40gb`
   - Align default with firmware-verified maximum

---

## Issue 4: Unverified Feature Unlocks

### Symptom

Features marked as "community guess, NOT verified" are applied to GPU:
- NVLink enable
- ECC enable
- ARC mutex

Unknown side effects on GPU operation.

### Risk

**Unverified register writes may:**
- Have no effect (safe but wasteful)
- Corrupt GPU state silently
- Cause cascading failures on subsequent GPU operations
- Break compatibility with specific drivers

### Current Impact

- **Severity**: Low to Medium (depends on GPU hardware tolerance)
- **Impact**: Unknown side effects, potential reliability degradation
- **Risk**: Features appear to work but may cause subtle failures later

### Fix

1. **Disable by default:**
   - Comment out unverified features in constants.yaml
   - Require explicit `--enable-feature=nvlink` flag to use

2. **Add verification warnings:**
   - Document which features are verified vs. guesses
   - Add installer warnings for unverified features
   - Include in README under "Known Limitations"

3. **Separate configuration:**
   - Split config into:
     - `verified_features` (default: enabled)
     - `experimental_features` (default: disabled, warn if used)

---

## Summary Table

| Issue | Severity | Frequency | Timeline | Fix Complexity |
|-------|----------|-----------|----------|-----------------|
| RCU Locking Violation | Critical | Intermittent | Hours-days | High |
| Falcon Corruption | Critical | Deterministic | 10-15 boots | High |
| Memory Limit Misunderstanding | Medium | Deterministic | Per-user | Low |
| Unverified Feature Unlocks | Low-Medium | Always (when used) | Immediate | Low |

---

## Recommendations for Users

### Current (Until Issues Fixed)

**Not recommended for production systems with:**
- Frequent reboots (multiple times per day)
- Long uptime requirements (beyond 10 boots)
- Continuous workload with high reliability demands

**Safe for:**
- One-time unlock applied, system left running continuously
- Test/research scenarios
- Systems with manual recovery capability

### Before Production Deployment

**Must address:**
1. RCU locking violation (kernel panic risk)
2. Falcon corruption recovery procedure (or eliminate the need to reapply)

**Should address:**
3. Memory limit targets clarification
4. Feature unlock verification

---

## Resources

- **CODE_REVIEW.md**: Detailed code analysis and architectural recommendations
- **IMPLEMENTATION.md Part 12**: Brief issue descriptions
- **Temporary workarounds**: See sections above

## Contact & Recovery

For systems affected by Falcon corruption:
- Recovery procedure: Contact user who performed successful recovery using "bullaytin specific fork"
- Procedure undocumented; requires reverse-engineering or user documentation
- See GitHub issue thread or user community for recovery details

