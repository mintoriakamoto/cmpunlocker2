#!/bin/bash
# gen2-hammer — Aggressive Gen2 PCIe retraining
#
# Used by gen2.service for early-boot Gen2 retraining.
# This is a more aggressive version of gen2-cycle that:
# 1. Kills all GPU processes
# 2. Does multiple rmmod/modprobe cycles
# 3. Uses setpci to force Gen2 on both GPU and upstream bridge
#
# Usage: gen2-hammer [max_cycles] [delay_seconds]
# Default: 2000 cycles, 2 second delay

set -uo pipefail

LOG="/var/log/gen2-hammer.log"
MAX_CYCLES="${1:-2000}"
DELAY="${2:-2}"
GPU_BDF="0000:01:00.0"

log() { echo "[$(date -Is)] $*" | tee -a "$LOG"; }

check_gen2() {
    local sta
    sta=$(setpci -s "$GPU_BDF" CAP_EXP+12.w 2>/dev/null) || return 1
    [[ "$sta" =~ ^[0-a-fA-F]{4}$ ]] || return 1
    echo $((0x${sta} & 0x0f))
}

# EARLY EXIT: Skip if already Gen2
gen=$(check_gen2 2>/dev/null) || gen="?"
if [[ "$gen" =~ ^[0-9]+$ ]] && (( gen >= 2 )); then
    log "Already Gen$gen, skipping."
    exit 0
fi

log "=== gen2-hammer start (max=$MAX_CYCLES, delay=$DELAY) ==="

# Kill EVERYTHING that holds nvidia modules
for svc in nvidia-cdi-refresh persist-gpu-clocks gen2 cmpunlocker hermes-llama crucible-qwen gdm3; do
    systemctl stop "${svc}.service" 2>/dev/null
    systemctl mask "${svc}.service" 2>/dev/null
done
nvidia-persistenced --kill 2>/dev/null
sleep 2

# Kill any remaining GPU processes
fuser -k /dev/nvidiactl /dev/nvidia-uvm /dev/nvidia-uvm-tools /dev/dri/* 2>/dev/null
sleep 2

# Verify modules are free
if lsmod | grep -q nvidia_uvm; then
    log "ERROR: nvidia_uvm still in use, cannot proceed"
    exit 1
fi

for ((i=1; i<=MAX_CYCLES; i++)); do
    # Unload modules (triggers secondary bus reset)
    rmmod nvidia 2>/dev/null
    sleep "$DELAY"

    # Load nvidia (booter runs, writes LC2/PL)
    modprobe nvidia 2>/dev/null
    sleep 1

    # Trigger probe-retrain by opening GPU
    nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null >/dev/null
    sleep 2

    gen=$(check_gen2 2>/dev/null) || gen="?"
    if [[ "$gen" =~ ^[0-9]+$ ]] && (( gen >= 2 )); then
        log "SUCCESS at cycle $i: Gen$gen"

        # Unmask and restart services
        for svc in nvidia-cdi-refresh persist-gpu-clocks cmpunlocker hermes-llama crucible-qwen; do
            systemctl unmask "${svc}.service" 2>/dev/null
            systemctl start "${svc}.service" 2>/dev/null
        done

        exit 0
    fi

    if (( i % 10 == 0 )); then
        log "Cycle $i: still Gen$gen"
    fi
done

log "FAILED: Gen2 not achieved after $MAX_CYCLES cycles"

# Unmask services
for svc in nvidia-cdi-refresh persist-gpu-clocks cmpunlocker hermes-llama crucible-qwen; do
    systemctl unmask "${svc}.service" 2>/dev/null
done

exit 1
