# Troubleshooting Guide

## Daemon Won't Start

### `BAR0 access validation failed`
**Cause:** Daemon cannot access memory-mapped BAR0 registers  
**Solutions:**
- Ensure running with `sudo` (requires root access)
- Check that GPU is visible: `lspci | grep 20b0` or `lspci | grep 20c2`
- Verify driver is installed: `nvidia-smi` should show the CMP device
- Check `/dev/mem` exists: `ls -l /dev/mem`

### `No compatible GPU found`
**Cause:** GPU device ID not recognized  
**Solutions:**
- Verify GPU is installed: `lspci -d 10de:20b0` (or 20c2/2082)
- Check that nvidia-open driver is installed (not proprietary nvidia driver)
- For driver version: `modinfo nvidia | grep version`

### Service won't restart after failure
**Cause:** Hit StartLimitBurst limit  
**Fix:** Reset with `sudo systemctl reset-failed cmpunlocker.service`

---

## Unlock Not Working

### Memory capacity still shows factory value
**Causes & Solutions:**
1. **Driver not loaded yet:** Wait 5-10 seconds after boot, check again
   ```bash
   nvidia-smi --query-gpu=memory.total --format=csv,noheader
   ```

2. **PLM is closing:** Check daemon logs for PLM failures
   ```bash
   journalctl -u cmpunlocker | grep PLM
   ```

3. **Memory unlock failed:** Check if reapply is working
   ```bash
   journalctl -u cmpunlocker | grep -E "(Reapplied|Memory reapply failed)"
   ```

### SM clock is still capped
**Cause:** Compute unlock (SS0/SS1) not applied  
**Verify:**
```bash
# Should show max boost clock (1410+ MHz)
nvidia-smi --query-gpu=clocks.max.sm --format=csv,noheader

# Watch daemon apply it
journalctl -u cmpunlocker | grep SS0
```

### PCIe speed is still Gen 1 or Gen 2
**Cause:** Link needs retraining after BAR0 writes  
**Fix:** Run the link retraining script
```bash
sudo ./cmpunlocker/scripts/pcie_gen4_unlock.sh
```

This script:
- Auto-detects your motherboard's PCIe capability (Gen 2-5)
- Triggers link retraining via PCI config space
- Takes 10-30 seconds to complete

---

## Daemon Behavior

### High CPU usage / spam in logs
**Normal:** First 30 seconds at boot while initial unlock runs.  
**After 30s:** Should be <1% CPU, quiet logs unless state changes.

**Check state tracking in logs:**
```bash
journalctl -u cmpunlocker -n 50
# Look for "recovered" messages only when unlocks temporarily failed
```

### "Another unlock in progress" message
**Normal:** Daemon skips a check if previous full unlock is still running.  
**Expected:** Rare; only during first boot or after PLM closure.

### Repeated "Reapplied" messages every second
**Problem:** Core unlock is not sticking (RAM or compute).  
**Debug:**
```bash
# Check if there are register write failures
journalctl -u cmpunlocker | grep -E "(failed|error)"

# Try manual unlock
sudo python3 -c "
from cmpunlocker.payload.pipeline import run_full_unlock
from cmpunlocker.payload.gpu import find_all_gpus
gpus = find_all_gpus()
if gpus:
    run_full_unlock(gpus[0])
"
```

---

## Multi-GPU Systems

### Only one GPU is unlocked
**Cause:** Daemon should find all GPUs at startup.  
**Debug:**
```bash
# Check what daemon found
journalctl -u cmpunlocker | grep "Found.*GPU"

# Verify both cards are visible
lspci | grep -E "20b0|20c2|2082"

# Check for errors per card
journalctl -u cmpunlocker | grep "Monitor error"
```

---

## Environment Variables

Configure daemon behavior without editing code:

```bash
# Change monitoring interval (default: 1 second)
sudo CMPUNLOCKER_CHECK_INTERVAL=5 systemctl restart cmpunlocker

# Or set persistently in /etc/systemd/system/cmpunlocker.service.d/override.conf
sudo mkdir -p /etc/systemd/system/cmpunlocker.service.d
cat << 'EOF' | sudo tee /etc/systemd/system/cmpunlocker.service.d/override.conf
[Service]
Environment="CMPUNLOCKER_CHECK_INTERVAL=5"
EOF
sudo systemctl daemon-reload
sudo systemctl restart cmpunlocker
```

---

## Logs

### Real-time monitoring
```bash
sudo journalctl -u cmpunlocker -f
```

### Recent history
```bash
sudo journalctl -u cmpunlocker -n 100
```

### Boot sequence
```bash
sudo journalctl -u cmpunlocker -b 0
```

### Specific GPU troubleshooting
```bash
# All messages for one GPU (example: 00:1F.0)
sudo journalctl -u cmpunlocker | grep "00:1F.0"
```

---

## State Transitions

The daemon tracks unlock state per GPU. Expected transitions:

### Normal (Healthy System)
```
[Boot]
→ "Running initial unlock"
→ "Entering monitor loop"
→ (silent if all stays unlocked)
```

### Recovery After Transient Failure
```
→ "[GPU] Compute reapply failed: ..."
→ "[GPU] Compute unlock recovered"
→ (back to normal)
```

### PLM Closure (requires full unlock)
```
→ "[GPU] PLM closed — re-running full unlock"
→ "Running initial unlock"
→ (back to normal)
```

---

## Performance

### Expected Resource Usage
- **CPU:** <1% in steady state (1-second polling)
- **Memory:** ~50 MB resident (Python + BAR0 context)
- **I/O:** 4 BAR0 reads per second per GPU (minimal)

### Long-term Stability
- Runs continuously for days/weeks without issue
- Automatic restart on crash (within 10-second backoff)
- Graceful shutdown on `systemctl stop` or SIGTERM

---

## Advanced Debugging

### Manual unlock (without daemon)
```bash
sudo python3 << 'EOF'
import sys
sys.path.insert(0, '/opt/cmpunlocker')
from cmpunlocker.payload.gpu import find_all_gpus
from cmpunlocker.payload.pipeline import run_full_unlock

gpus = find_all_gpus()
if gpus:
    print(f"Found GPU: {gpus[0]}")
    run_full_unlock(gpus[0])
    print("Unlock complete")
else:
    print("No GPUs found")
EOF
```

### Check unlock state via BAR0
```bash
sudo python3 << 'EOF'
import sys
sys.path.insert(0, '/opt/cmpunlocker')
from cmpunlocker.payload.bar0 import Bar0
from cmpunlocker.common.constants import get
from cmpunlocker.payload.gpu import find_all_gpus

gpus = find_all_gpus()
if gpus:
    pci = gpus[0]
    with Bar0(pci) as bar0:
        # Read SS0/SS1 (compute unlock)
        ss0 = bar0.rd32(0x0082381C)
        ss1 = bar0.rd32(0x00823820)
        print(f"SS0: 0x{ss0:08x} (should be 0x88888888)")
        print(f"SS1: 0x{ss1:08x} (should be 0x00000008)")
        
        # Read CFG1/LMR (memory unlock)
        cfg1 = bar0.rd32(0x009A0204)
        lmr = bar0.rd32(0x00100CE0)
        print(f"CFG1: 0x{cfg1:08x} (should be 0x02779000 for 80GB)")
        print(f"LMR: 0x{lmr:08x}")
EOF
```

---

## Still Stuck?

1. **Collect diagnostic info:**
   ```bash
   sudo journalctl -u cmpunlocker -n 200 > /tmp/daemon.log
   lspci -d 10de: > /tmp/lspci.log
   nvidia-smi > /tmp/nvidia-smi.log
   ```

2. **Check repo issues:** https://github.com/mintoriakamoto/cmpunlocker2/issues

3. **Common fixes:**
   - Reboot: `sudo reboot`
   - Reinstall daemon: `sudo ./install.sh`
   - Update driver: `sudo apt-get install --only-upgrade nvidia-open` (or your package manager)
