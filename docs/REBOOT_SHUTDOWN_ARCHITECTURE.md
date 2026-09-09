# Reboot & Shutdown Architecture

## Overview

CMP Unlocker uses a **two-phase unlock model**:
1. **PLM Opening (ROP Exploit)**: Hardware-level registers opened via Falcon BootROM ROP chain
2. **Register Writes (BAR0)**: Memory and compute unlock values written directly to GPU

**Key Insight**: PLM registers stay open across driver reloads but close on power cycles.

## Phase 1: Initial Full Unlock

Triggered by:
- `install.sh` (first time setup)
- Daemon detecting closed PLM after power cycle

Flow:
```
1. Stop display manager (gdm3, sddm, lightdm, etc.)
2. Kill X11/Wayland + nvidia-persistenced
3. Unload nvidia kernel modules
4. Patch GSP firmware with 63KB ROP payload
5. modprobe nvidia → Falcon BootROM loads patched firmware
6. BootROM executes ROP chain → PLM registers open
7. Write CFG1/LMR (memory unlock) via BAR0
8. Write SS0/SS1 (compute unlock) via BAR0
9. Restore original GSP signature (hide tampering)
10. modprobe nvidia → driver loads with clean signature
```

**Duration**: 30-60 seconds per GPU  
**Atomicity**: No - can be interrupted at various points  
**Retry Logic**: Loops up to 2 times per PLM register if verification fails  

## Phase 2: Daemon Monitoring

After initial unlock, `cmpunlocker` daemon runs continuously.

### Monitoring Loop (every 1 second)
```python
1. Acquire lock file (/var/lock/cmpunlocker.lock)
   ├─ If locked: Another process unlocking, skip this cycle
   └─ If acquired: Continue

2. Check is_plm_open(GPU)
   ├─ YES: Check compute/memory registers (lightweight BAR0 reads)
   │   ├─ If SS0/SS1 missing: Write them via BAR0
   │   └─ If CFG1/LMR missing: Write them via BAR0
   └─ NO: Run full unlock (Phase 1)

3. Release lock file
4. Sleep 1 second
```

### BAR0 Reapplication
If PLM is open but specific registers lost:
- Write SS0 (0x0082381C) = 0x88888888 (SM clock unlock)
- Write SS1 (0x00823820) = 0x00000008 (IMLA4 override)
- Write CFG1 (address varies) = memory unlock value
- Write LMR (address varies) = memory rank value

**Duration**: ~100ms per missing register  
**Atomicity**: Individual writes are atomic; ordering matters

## Reboot Scenarios

### Scenario 1: Power Cycle (System Off/On)

```
BEFORE SHUTDOWN:
  GPU hardware state: PLM open, memory unlocked (80GB visible)
  Kernel memory: Lost
  GSP firmware: Original (restored) with unlocked HW state

ON POWER-OFF:
  All GPU state reset to factory defaults
  PLM registers close
  Memory reverts to 10GB

ON BOOT:
  Kernel loads, loads nvidia driver
  Driver loads GSP firmware (stock, no tampering)
  GSP firmware closes all PLM registers (default behavior)
  GPU appears as 10GB CMP card
  
DAEMON ACTIVATION (first check cycle ~3 seconds after boot):
  is_plm_open() → NO
  Daemon triggers full unlock (Phase 1)
  Within 60 seconds: GPU unlocked to 80GB
  
RESULT:
  GPU operational at full capacity after boot
```

**Key Point**: GPU is briefly locked during boot, then unlocked by daemon.

### Scenario 2: Driver Reload (modprobe -r/-i nvidia)

```
BEFORE RELOAD:
  GPU hardware state: PLM open, memory unlocked (80GB visible)
  Daemon running: Monitoring every 1 second

USER RUNS: sudo modprobe -r nvidia
  1. Kernel unloads nvidia driver
  2. BAR0 access becomes invalid
  3. Daemon's next BAR0 read fails (expected)
  
DAEMON DETECTS (within 1 second):
  is_plm_open(GPU) → Exception (no driver)
  _check_card() catches exception, logs warning
  Waits for next cycle

USER RUNS (or automatic): sudo modprobe nvidia
  1. Kernel reloads nvidia driver
  2. Driver loads stock GSP firmware
  3. PLM registers now CLOSED (fresh load)
  4. GPU reverts to 10GB
  
DAEMON DETECTS (within 1 second):
  is_plm_open(GPU) → NO (PLM closed by fresh firmware load)
  Triggers full unlock (Phase 1)
  Within 60 seconds: GPU unlocked to 80GB
  
RESULT:
  GPU operational at full capacity after driver reload
```

**Key Point**: Full unlock triggered automatically when PLM closes.

### Scenario 3: System Suspend/Resume

```
BEFORE SUSPEND:
  GPU hardware state: Depends on system behavior
  
ON SUSPEND:
  GPU may enter low-power state (depends on driver)
  PLM state: Unknown
  Daemon: May pause or continue
  
ON RESUME:
  Driver may reload, may not
  PLM state: Likely closed (safest assumption)
  Daemon: Should detect and reapply
```

**Recommendation**: Treat like driver reload - run full unlock if PLM closed.

## Systemd Service Configuration

```ini
[Unit]
Description=NVIDIA CMP 170HX Unlock Daemon
After=network.target nvidia-driver.service
ConditionPathExists=/dev/mem
ConditionPathExists=/sys/bus/pci/

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/cmpunlocker/cmpunlocker/daemon/watchdog.py
Restart=on-failure              # Restart only if it crashes
RestartSec=10                    # Wait 10s before restart
StartLimitIntervalSec=120        # Within 120s window
StartLimitBurst=5                # Max 5 restarts
KillMode=process                 # Kill daemon cleanly
TimeoutStopSec=5                 # Max 5s to stop
User=root

[Install]
WantedBy=multi-user.target
```

**Key Settings**:
- `After=nvidia-driver.service`: Wait for driver to load first
- `Restart=on-failure`: Don't restart during normal unlock operations
- `RestartSec=10`: Prevents restart storms
- `TimeoutStopSec=5`: Prevents blocking system shutdown

## Lock File Protection

**Location**: `/var/lock/cmpunlocker.lock`

**Purpose**: Prevent concurrent unlock attempts

**Mechanism**:
```python
# Daemon acquires exclusive lock before running full unlock
lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_WRONLY)
fcntl.flock(lock_fd, fcntl.LOCK_EX)  # Exclusive lock

# Another process trying to unlock sees lock is held
# and skips this cycle
```

**Cleanup**: Lock file deleted on graceful shutdown

## Failure Modes & Recovery

### PLM Won't Open (ROP Exploit Fails)

**Symptoms**:
- Daemon logs: "WPR_CFG attempt 1 failed"
- GPU remains locked after 2 retries
- Repeats every 1 second

**Root Causes**:
1. GSP firmware patching corrupted (fixed in c05a274)
2. GPU hardware state invalid (FLR reset issue)
3. Driver version incompatible (requires 610.43.02+)
4. GPU firmware not found or inaccessible

**Recovery**:
```bash
# 1. Stop daemon
sudo systemctl stop cmpunlocker

# 2. Check logs
sudo journalctl -u cmpunlocker -n 50

# 3. Verify driver
nvidia-smi

# 4. Manual unlock attempt
sudo python3 /opt/cmpunlocker/cmpunlocker/payload/pipeline.py 0000:01:00.0

# 5. If manual unlock succeeds but daemon fails:
# → Daemon issue, not hardware
sudo systemctl restart cmpunlocker

# 6. If manual unlock also fails:
# → Hardware issue, check GPU and driver
```

### BAR0 Reapplication Fails

**Symptoms**:
- Daemon logs: "Compute reapply failed: values did not stick"
- Memory/compute unlock partially lost

**Root Causes**:
1. Driver reset GPU during operation
2. Transient hardware glitch
3. Another process touching GPU state

**Recovery**:
- Daemon automatically retries every 1 second
- Usually recovers within 5 seconds
- If persistent: Same as PLM won't open

### Daemon Crashes Repeatedly

**Symptoms**:
- `systemctl status cmpunlocker` shows `failed`
- `journalctl` shows exception repeated every 10s

**Root Causes**:
1. GPU not found at boot time (passthrough, not present)
2. Driver initialization not complete
3. Permission issue (not running as root)
4. Bug in daemon code

**Recovery**:
```bash
# Check why daemon is failing
sudo journalctl -u cmpunlocker -n 100

# If GPU not found:
sudo lspci | grep -i nvidia

# If driver issue:
sudo nvidia-smi

# Stop daemon, check driver, restart:
sudo systemctl stop cmpunlocker
sudo modprobe -r nvidia
sudo modprobe nvidia
sudo systemctl start cmpunlocker
```

## State Preservation Across Reboots

### What Persists:
- ✓ Kernel: Fully restored
- ✓ Driver: Reloaded from disk
- ✓ Daemon config: In systemd unit (persistent)
- ✓ Unlock script: In /opt/cmpunlocker (persistent)

### What's Lost:
- ✗ PLM register state (closed on power cycle)
- ✗ BAR0 register values (reset to defaults)
- ✗ GSP firmware patches (reverted when driver loads)
- ✗ Daemon in-memory state (fully restarted)

### Automatic Recovery:
1. Daemon starts on boot (systemd auto-start)
2. Detects PLM closed (on first check, ~3 seconds)
3. Runs full unlock (complete sequence)
4. GPU fully operational within 60 seconds

## Monitoring & Diagnostics

### Check Daemon Status
```bash
sudo systemctl status cmpunlocker
# Shows: active (running), inactive (dead), or failed

sudo systemctl is-active cmpunlocker
# Shows: active or inactive
```

### View Daemon Logs
```bash
# Real-time logs
sudo journalctl -u cmpunlocker -f

# Last 50 lines
sudo journalctl -u cmpunlocker -n 50

# Since boot
sudo journalctl -u cmpunlocker -b

# By time range
sudo journalctl -u cmpunlocker --since "2 hours ago"
```

### Verify Unlock Status
```bash
# Check if GPU has full memory
nvidia-smi --query-gpu=memory.total --format=csv,noheader

# Check SM clock (should be high after unlock)
nvidia-smi --query-gpu=clocks.max.sm --format=csv,noheader

# Full GPU info
nvidia-smi
```

### Emergency Unlock
```bash
# Manually run full unlock without daemon
sudo python3 /opt/cmpunlocker/cmpunlocker/payload/pipeline.py 0000:01:00.0

# Then restart daemon if needed
sudo systemctl restart cmpunlocker
```

## Best Practices

1. **Always enable daemon** in production - runs automatically after reboot
2. **Monitor logs regularly** - check for unlock failures
3. **Schedule maintenance during low usage** - driver reload causes brief GPU loss
4. **Don't kill daemon during unlock** - wait for lock file to be released
5. **Verify after reboot** - run `nvidia-smi` to confirm GPU capacity
6. **Keep driver version stable** - upgrade carefully (requires re-verification)
7. **Document your setup** - keep notes on GPU address, target configuration

## Known Limitations

1. **Unlock is volatile** - Lost on power cycle (by design, hardware limitation)
2. **Driver reload disrupts workloads** - GPU briefly unavailable (~30s)
3. **No persistent BIOS/firmware changes** - All changes reverted on power cycle
4. **Lock file is local** - Can't handle multiple systems with network GPUs
5. **No integration with GPU resource managers** - (NVIDIA GPU Operator, etc.)

## Future Improvements

- [ ] Persistent storage of unlock state (across reboots)
- [ ] Kubernetes integration (GPU resource plugin)
- [ ] Pre-unlock before driver load (less disruptive)
- [ ] Multiple GPU coordination (serialize or parallelize)
- [ ] Gradual reapplication (don't restart daemon during unlock)
- [ ] Health check endpoint (for monitoring systems)
