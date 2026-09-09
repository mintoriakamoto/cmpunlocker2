# CMP 170HX Gen 5 PCIe Unlock - Complete Integration Guide

## Overview
This guide documents the complete process to achieve Gen 5 x16 PCIe speed on CMP 170HX, building on the existing 40GB memory + 1410 MHz compute unlock.

## Current State
- ✓ Memory: 40GB unlocked and persistent
- ✓ Compute: 1410 MHz (full SM speed)
- ✓ PCIe Register: Gen 5 capability register writes persist (BAR0 0x009088)
- ✗ PCIe Speed: GPU still negotiates Gen 2 x4 (firmware OTP fuse limitation)

## Key Finding: Kernel Driver Patching Approach

**Reference Project**: abobasixseven/unlock-cmp-170hx achieves unlock via **kernel driver patching**, not firmware modification.

### Their Approach: Kernel-Level PLM Opening
1. Patch nvidia-open driver (610.43.03) directly
2. Embed PLM register opening in kgspExecuteBooterLoad_HAL
3. Write register values before GSP-RM firmware boots:
   - FBPA_CFG1 = 0x02779000 (64GB HBM2e geometry)
   - MMU_LMR = 0x0000020B (memory limit)
   - SS0/SS1 = 0x88888888 / 0x00000008 (throttle disables)
4. Patch GspStaticConfigInfo to report FB_BYTES = 0x1000000000 (64GB)
5. Cold reboot (60s capacitor discharge) to reset WPR2 state

**Implication**: Their approach doesn't require firmware modification, only driver patching!

### Gen 2 PCIe via Kernel Patching
- Use register override: CYA_0 bit 2 + OPT_GEN23 setting
- Requires "userspace retrain hammering during GSP bootstrap window"
- Achievable without firmware patching

### Hardware Fuse Limitation
- **Gen 3/Gen 4**: Impossible — hardware fuse FUSE_PCIE_GEN23_DIS blocks software paths
- **Gen 5**: Unknown — not explicitly discussed in reference project

## Two Paths Forward

### Path A: Kernel Driver Patching (Recommended)
**Advantage**: Pure driver-level, no firmware modification
**Challenge**: Requires patching and rebuilding nvidia-open driver 610.43.03

**Steps**:
1. Download nvidia-open 610.43.03 source
2. Apply patches from abobasixseven project for PLM opening
3. Modify to target Gen 5 instead of 64GB if possible
4. Rebuild and install driver
5. Cold reboot with capacitor discharge

### Path B: Firmware Patching (Current Approach)
**Advantage**: Potentially more portable, single-use exploit
**Challenge**: Requires Falcon ISA reverse engineering and firmware binary patching

**Steps**:
1. Reverse engineer GSP firmware to find fuse-check code
2. Patch Falcon ISA instructions to ignore OTP fuse
3. Apply patched firmware via exploit pipeline
4. PCI Config Space unlock for Gen 5 negotiation

## Recommended Next Steps

### Immediate (Next 1-2 hours)
1. ✅ Examine abobasixseven/unlock-cmp-170hx repository in detail
2. ✅ Understand their kernel patching approach
3. ✅ Identify if their patches can be adapted for Gen 5

### Short Term (Next 4-8 hours)
1. Decide: Kernel patching vs firmware patching
2. If kernel patching: Download, patch, rebuild driver
3. If firmware patching: Start Falcon ISA reverse engineering

### Testing
- Current state: 40GB memory + 1410 MHz compute working
- Min success: Maintain 40GB + 1410 MHz + any PCIe Gen improvement
- Target: 40GB + 1410 MHz + Gen 5 x16 (32 GT/s)

## Current Implementation Status

### ✅ Completed
- ROP exploit infrastructure (6/8 PLM registers)
- BAR0 memory-mapped I/O (40GB CFG1/LMR)
- Compute unlock (SS0/SS1)
- PCI Config Space unlock method (ready to deploy)
- Firmware patching framework (skeleton ready)

### ⏳ In Progress
- Research into kernel driver patching approach
- Investigation of abobasixseven reference project

### ❌ Not Yet Started
- Choose primary approach (kernel vs firmware patching)
- Implement chosen method
- Test complete Gen 5 unlock chain

## Files Ready for Integration

```
cmpunlocker/
├── payload/
│   ├── pipeline.py                     ✅ Main pipeline (partially integrated)
│   ├── firmware_fuse_unlock.py          ✅ Framework ready (awaits implementation)
│   ├── falcon_analyzer.py               ✅ Pattern analysis tool ready
│   └── [kernel_patch_integration.py]    ⏳ TODO if kernel approach chosen
└── unlock/
    └── pcie_config_unlock.py            ✅ Ready to deploy
```

## Key Questions to Answer

1. **Can kernel patching achieve Gen 5?** (or only Gen 2?)
2. **Is hardware fuse the blocker for Gen 5?** (like Gen 3/4)
3. **Can firmware patching bypass the fuse?** (our assumption)
4. **What's the easiest path for 32 GT/s bandwidth?**

## Success Criteria

### Minimum (Current Working State)
- ✅ Memory: 40GB
- ✅ Compute: 1410 MHz
- ❌ PCIe: Still Gen 2 x4 (5.0 GT/s)

### Full Success Target
- ✅ Memory: 40GB (or 80GB if firmware lock cleared)
- ✅ Compute: 1410 MHz
- ✅ PCIe: Gen 5 x16 (32 GT/s, 8 GB/s per direction)

---

**Status**: Critical research completed, decision point reached.  
**Decision Required**: Kernel patching vs firmware patching approach?
