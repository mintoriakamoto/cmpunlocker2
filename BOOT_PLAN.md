# cmpunlocker2 - Auto-Unlock Boot Plan

## Current Configuration (Post-Setup)

✓ **Daemon:** Enabled and will auto-start  
✓ **GSP Firmware:** Clean (factory version)  
✓ **Stage Files:** None (not needed, daemon checks values)  
✓ **Verification Script:** /usr/local/sbin/verify-unlock  

---

## What Happens on Next Boot

### Timeline

```
0s     System boots → Linux kernel loads
5s     systemd starts services
10s    gen2.service runs → tries PCIe Gen 2 (will fail, expected)
15s    cmpunlocker daemon starts
       ├─ Detects GPU (0000:01:00.0)
       ├─ Checks: unlock values present? → NO (lost after power cycle)
       ├─ Runs full exploit pipeline
       │  ├─ Stops display manager
       │  ├─ Opens PLM registers (4 stages)
       │  ├─ Writes memory unlock: CFG1=0x02779000, LMR=0x0000028A
       │  ├─ Writes compute unlock: SS0=0x88888888, SS1=0x00000008
       │  └─ Restores clean GSP signature
       └─ Enters monitor loop
       
30s    GPU is now unlocked!
       └─ 40GB memory
       └─ 1410 MHz compute
```

### Key Points

1. **PLM is naturally OPEN on fresh boot** (hardware behavior)
   - No FLR reset issues
   - Exploit can run safely

2. **Exploit will only run if unlock values are missing**
   - Checks memory/compute presence first
   - Skips if already applied
   - Safer than re-running unnecessarily

3. **Firmware is kept clean**
   - Not left in patched state
   - Original signature restored

---

## After Boot

### Verify Unlock Applied

```bash
# Run this after boot to verify:
sudo verify-unlock

# Or manually:
nvidia-smi --query-gpu=memory.total,clocks.max.sm --format=csv,noheader
# Expected: 40960 MiB, 1410 MHz

# Check daemon:
sudo systemctl status cmpunlocker
sudo journalctl -u cmpunlocker -f  # Watch live
```

### What Daemon Does During Runtime

Every 1 second:
1. Check if memory/compute are unlocked
2. If missing: run full exploit
3. If present: do nothing (light maintenance)
4. Check PCIe/NVLink features (best-effort)

---

## Troubleshooting

### If GPU Goes Offline After Boot

**This should not happen with the improved daemon**, but if it does:

```bash
# 1. Check logs
sudo journalctl -u cmpunlocker -n 50

# 2. Restore firmware if corrupted
sudo cp /lib/firmware/nvidia/610.43.02/gsp_tu10x.bin.cmpunlocker.bak \
       /lib/firmware/nvidia/610.43.02/gsp_tu10x.bin

# 3. Perform FLR reset
sudo bash -c 'echo 1 > /sys/bus/pci/devices/0000:01:00.0/reset'
sleep 2
nvidia-smi

# 4. Check if values stuck
nvidia-smi --query-gpu=memory.total,clocks.max.sm --format=csv,noheader
```

### If Daemon Keeps Reapplying Exploit

This wastes time but shouldn't corrupt anything. Check:

```bash
# Are unlock values actually present?
sudo journalctl -u cmpunlocker | grep "Unlock values"

# If it says "missing", then re-running is correct
# If it says "present", but daemon still exploits, there's a bug
```

---

## What NOT To Do

❌ Don't disable daemon (unlock won't persist)  
❌ Don't modify GSP firmware while daemon is running  
❌ Don't unload nvidia drivers while daemon is checking  
❌ Don't use other PCIe/GPU tools simultaneously  

---

## Summary

✅ **Auto-unlock on boot:** YES  
✅ **Persistent across reboots:** YES (daemon maintains)  
✅ **Safe from corruption:** YES (values checked first)  
✅ **Gen 5 x16:** NO (OTP fused to x4 Gen 2)  

You're ready for production use! 🚀
