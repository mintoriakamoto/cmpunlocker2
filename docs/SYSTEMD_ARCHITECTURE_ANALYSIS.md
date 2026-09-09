# GPU Unlock Projects - Systemd Architecture Analysis

## Repositories Found

### 1. d3dx9/cmpunlocker
**Repository**: https://github.com/d3dx9/cmpunlocker  
**Target**: NVIDIA CMP 170HX (GA100)  
**Driver**: nvidia-open 580.x  
**Status**: Single daemon for persistent unlock

#### Service Architecture:
- **Single Service Unit**: `cmpunlocker.service` (Type=simple)
- **Installed at**: `/etc/systemd/system/cmpunlocker.service`
- **Execution**: Runs Python watchdog daemon continuously
- **Restart Policy**: on-failure with 10s delay
- **Polling Interval**: CHECK_INTERVAL = 1 second (hardcoded in watchdog.py)

#### Monitoring Pattern:
```python
# From watchdog.py
Every 1 second, checks each GPU:
1. Is PLM open? If not, run full unlock
2. Is SS0/SS1 (compute) in place? If not, reapply
3. Is CFG1/LMR (memory) in place? If not, reapply
```

**Approach**: Continuous polling with fast 1-second interval  
**Multi-GPU**: All CMP 170HX cards discovered at startup, tracked in main loop  
**Per-GPU Templating**: Not used; single watchdog monitors all cards  

---

### 2. amoghmunikote/cmpunlocker
**Repository**: https://github.com/amoghmunikote/cmpunlocker  
**Target**: NVIDIA CMP 170HX (GA100, variants 8GB/10GB/ES)  
**Driver**: Multiple version support with per-profile unlock  
**Status**: Complex multi-component system

#### Service Architecture:
- **Single Early-Boot Service**: `gen2.service` (Type=oneshot)
- **Location**: `/etc/systemd/system/gen2.service`
- **Timing**: 
  - Starts: After=sysinit.target
  - Stops before: basic.target, multi-user.target, graphical.target
- **Boot Phase**: Early boot (before DM/graphical services)
- **Timeout**: 45 seconds (TimeoutStartSec=45)
- **Execution**: `/usr/local/sbin/gen2-hammer`
- **Persistence**: RemainAfterExit=no (one-shot, not persistent)

#### Installation Features:
- Multi-GPU support with per-card profile detection
- Floorsweep signature reading to detect ES cards
- IOMMU configuration for passthrough (kernel cmdline patching)
- PCIe Gen2 retrain via modprobe options
- Optional bootstrap module and VFIO passthrough setup
- 7+ step install process with extensive validation

**Approach**: Early-boot single operation, no continuous daemon  
**Multi-GPU**: Each GPU profile detected and configured at install time  
**Per-GPU Templating**: Not systemd-based; profiles baked into driver config  

---

### 3. xrip/cmp50hx-unlock
**Repository**: https://github.com/xrip/cmp50hx-unlock  
**Target**: CMP 50HX (GA102) and CMP 90HX (GA102) mining cards  
**Driver**: nvidia 610.43.03 userland + patched kernel modules  
**Status**: Multi-service system with optional components

#### Service Architecture:

##### For CMP 50HX:
- **Service**: `cmp50hx-gen2.service` (Type=oneshot)
- **Execution**: `/usr/local/sbin/cmp50hx-gen2`
- **Timing**: Waits for card to self-unlock before PCIe Gen2 retrain
- **Timeout**: 25 minutes (TimeoutStartSec=25min) — long wait for self-unlock
- **Start**: multi-user.target
- **Persistence**: One-shot (runs once per boot)

##### For CMP 90HX:
- **Service**: `cmp90hx-gen2.service` (Type=oneshot)
- **Execution**: `/opt/cmp50hx-unlock/cmp90hx/rejoin16-apply-all.sh`
- **PreExecution**: Clears stale rejoin16 write spec from `/var/lib/cmpunlocker-rs/`
- **Timeout**: 30 minutes (TimeoutStartSec=1800)
- **Persistence**: RemainAfterExit=yes (marked complete after first run)
- **Boot Cycles**: ~35 driver reload cycles (7-8 minutes) on COLD boot
- **Start**: After multi-user.target
- **Comment**: "full unlock state applied by cmp90hx-gen2.service at next boot"

##### Optional Idle Governor:
- **Service**: `cmp-idle-governor.service` (Type=simple)
- **Execution**: `/opt/cmp50hx-unlock/idle-governor/cmp-governor` (native C binary)
- **Function**: Forces P8 idle state, returns to P16 on load
- **Restart**: on-failure, RestartSec=10
- **Power Impact**: Reduces idle power from ~50W to ~2W
- **Enabled**: Only with `--idle-governor` flag at install

#### Installation Flow (10 major steps):
1. Detect card type (50HX vs 90HX)
2. Install build tools and kernel headers
3. Fetch NVIDIA userland
4. Build patched kernel modules
5. Install modules with backups
6. Make patched modules boot-persistent (via initramfs)
7. Install PCIe Gen2 boot service (CMP50HX/90HX specific)
8. Install optional idle governor
9. Install tuning utility
10. Live check and initramfs validation

**Approach**: Service runs once per boot; NVIDIA driver handles persistence  
**Multi-GPU**: Single build serves one card type; must rerun installer for different type  
**Per-GPU Templating**: Not used; driver module is card-specific  

---

### 4. WildFlash1st/cmp90hx-unlock
**Repository**: https://github.com/WildFlash1st/cmp90hx-unlock  
**Target**: CMP 90HX (GA102) / CMP 170HX (GA100)  
**Driver**: nvidia-open 610.43.03  
**Status**: Profiled driver unlock (no systemd service in latest variant)

#### Service Architecture:
- **Default Mode**: No systemd service
- **Legacy Mode**: `cmp90hx-persistent.service` (bendy2 bootstrap) — now DISABLED
- **Variant 1**: "rejoin15" persistent unlock
  - V67 inlined into driver init
  - Survives reboots by itself
  - No systemd service needed
  - No bootstrap module needed

#### Installation Flow:
- Detects card memory profile (8GB → 64GB, 10GB → 40GB)
- Supports CMP 90 compute-only unlock mode
- Patches nvidia-open driver init for persistence
- Optional rejoin15 (V67) for persistent unlock without services

**Approach**: Driver-level injection; no systemd persistence wrapper needed  
**Multi-GPU**: Auto-detects, enforces one build per card type  
**Per-GPU Templating**: Driver handles all card variants  

---

## Comparative Systemd Architecture Patterns

| Aspect | d3dx9 | amoghmunikote | xrip (50/90HX) | WildFlash1st |
|--------|-------|---------------|----------------|--------------|
| **Service Type** | simple (continuous) | oneshot (early boot) | oneshot (post boot) | none (driver-level) |
| **Monitoring** | 1-second polling | One-time operation | Once per boot | Driver persistence |
| **Restart Policy** | on-failure | N/A (oneshot) | N/A (once/boot) | N/A (none) |
| **Multi-GPU** | Single daemon all cards | Per-card profile at install | Per-card at install | Driver handles |
| **Timing** | Continuous 24/7 | sysinit→basic | multi-user→graphical | Boot-time only |
| **Health Check** | Self-monitors via BAR0 | N/A (one-shot) | N/A (one-shot) | Driver monitors |
| **Lock Mechanism** | None; continuous reapply | N/A | None; driver init | None; driver-level |
| **Polling/Interval** | 1 second hardcoded | N/A | N/A | N/A |
| **Timeout** | Restart on failure | 45 seconds | 25min (50HX), 30min (90HX) | N/A |

---

## Unique Architecture Patterns Observed

### 1. Continuous Polling Approach (d3dx9)
**Strength**: Immediate detection of unlock loss, catches all PLM resets  
**Challenge**: 1 second polling on CPU for I/O every second  
**State**: Watchdog.py checks via BAR0 memory-mapped I/O  
**Recovery**: Automatic reapply within 1 second  

### 2. Early-Boot Single-Operation (amoghmunikote)
**Strength**: Minimal runtime overhead; works before multi-user services  
**Challenge**: Unlock lost if PLM closes after boot; no recovery  
**State**: Relies on driver persistence after unlock  
**Validation**: Kernel command line patching (IOMMU) for passthrough  

### 3. Post-Boot Long-Duration Service (xrip)
**Strength**: Multiple boot cycles for complex PLM unlocking (90HX needs 35+ cycles)  
**Challenge**: Blocks boot until done (25-30 min timeout)  
**State**: RemainAfterExit=yes prevents re-running  
**Recovery**: rejoin16-apply-all.sh with manual cycle scripts for troubleshooting  

### 4. Driver-Level Persistence (WildFlash1st)
**Strength**: No userspace overhead; V67 embedded in driver init  
**Challenge**: Requires exact driver version (610.43.03)  
**State**: Driver patches handle unlock on every boot  
**Recovery**: Driver-level, not visible to systemd  

---

## Key Implementation Details

### Per-GPU Templating / Multi-GPU Handling

**None of the projects use systemd templating** (e.g., `cmpunlocker@%i.service`).  
Instead:

- **d3dx9**: Single daemon discovers all GPUs at startup, monitors in loop
- **amoghmunikote**: Multi-GPU support via GPU_INVENTORY env var passed to driver build
- **xrip**: Single build per card type; rerun installer for different types
- **WildFlash1st**: Driver detects card type automatically during init

### Health Check Patterns

1. **d3dx9** (most sophisticated):
   - Reads PLM status via BAR0 offset
   - Checks SS0/SS1 compute unlock values
   - Checks CFG1/LMR memory unlock values
   - Reapplies only what's missing (fast path)

2. **amoghmunikote**:
   - Early-boot check via floorsweep register
   - IOMMU kernel cmdline validation
   - No runtime health check (one-time operation)

3. **xrip**:
   - Live check script (cmp50hx: rm-issue-rate tool; cmp90hx: PLM register read)
   - Post-boot verification script for manual inspection
   - No automatic recovery

4. **WildFlash1st**:
   - Driver-level checks (not exposed to userspace)
   - Optional live check after install

### Lock Files & Coordination

- **d3dx9**: None; watchdog is stateless
- **amoghmunikote**: None; one-time install
- **xrip**: `/var/lib/cmpunlocker-rs/rejoin16-next-write.bin` for multi-cycle coordination
- **WildFlash1st**: None; driver-level

---

## Notable Architecture Decisions

### Timeout Patterns
- **45 seconds** (amoghmunikote): Tight window for early-boot operation
- **25 minutes** (xrip 50HX): Long wait for card's self-unlock mechanism
- **30 minutes** (xrip 90HX): 35 driver reload cycles need time
- **None** (d3dx9): Runs forever; restarts on failure

### Wait Strategies
- **d3dx9**: Continuously checks; no waiting
- **amoghmunikote**: Synchronous, blocking early boot
- **xrip 50HX**: Waits for card to unlock itself via hardware mechanism
- **xrip 90HX**: Cycles driver 35+ times, reads PLM state between cycles

### Persistence Mechanisms
- **d3dx9**: Continuous daemon re-applies every second
- **amoghmunikote**: Driver+OTP fuses remain set after initial unlock
- **xrip**: Driver initialization runs unlock on every boot
- **WildFlash1st**: V67 runs during driver init (inlined code)

---

## Recommendations for cmpunlocker2

Based on the comparison:

1. **Polling Interval**: Keep d3dx9's 1-second interval if using continuous daemon model
2. **Timeout Configuration**:
   - Use `StartLimitIntervalSec=120 StartLimitBurst=5` to prevent restart loops
   - Use `TimeoutStopSec=5` to prevent blocking shutdown
3. **Multi-GPU Support**:
   - Discover all compatible GPUs at daemon startup (like d3dx9)
   - Store GPU list in memory; no lock files needed
4. **Health Checks**:
   - Implement BAR0 read-only checks (SS0/SS1, CFG1/LMR state)
   - Log state changes to journal (set `StandardOutput=journal`)
5. **Boot Ordering**:
   - Use `After=multi-user.target nvidia-driver.service`
   - Avoid blocking boot (use Type=simple, not oneshot)
6. **Graceful Shutdown**:
   - Use `KillMode=process` to prevent daemon from blocking systemd
   - Implement signal handlers for SIGTERM

---

## Sources

- [d3dx9/cmpunlocker](https://github.com/d3dx9/cmpunlocker) - Continuous polling daemon
- [amoghmunikote/cmpunlocker](https://github.com/amoghmunikote/cmpunlocker) - Early-boot multi-GPU profiles
- [xrip/cmp50hx-unlock](https://github.com/xrip/cmp50hx-unlock) - Long-duration boot services
- [WildFlash1st/cmp90hx-unlock](https://github.com/WildFlash1st/cmp90hx-unlock) - Driver-level persistence
- [NVIDIA GPU Unlock Community](https://github.com/Consensus-Protocol/cmp170hx) - Technical documentation
