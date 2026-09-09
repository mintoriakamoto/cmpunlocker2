# PCIe Gen 5 Reverse Engineering Analysis

## Discovery Process

### Stage 1: Wrong Registers (0x88c1c, 0x000088)
- Initial Gen 5 code wrote to these addresses
- Result: Booter crashed with error 0x5
- Root cause: These were not the registers the firmware expected

### Stage 2: Correct XVE_OVR Register (0x8872c)
- Analyzed kernel boot logs from failed cold boot
- Found firmware debug line: `PCIe XVE_OVR@8872c=0x00000006; skip mid-boot retrain`
- 0x8872c is the firmware's PCIe Gen OVERRIDE register
- Value 0x06 = Gen 5 x8 capability

### Register Map (from kernel analysis)
```
0x88ff4  = PLM[4] XVE (firmware-controlled, opened as PLM register)
0x88ab4  = PLM[5] XVE_B (firmware-controlled)
0x88ff8  = PLM[6] XVE_C (firmware-controlled)
0x8872c  = XVE_OVR (firmware override for Gen capability) ← WRITE HERE
```

## Current Status

✅ Correct Gen 5 register identified: 0x8872c
✅ Target value determined: 0x06 (Gen 5 x8) or 0x05 (Gen 5 x16)
✅ Code updated to write to correct register

❌ GPU currently unbootable (Booter error 0x5 persists from previous test)

## Recovery Required

The GPU is stuck in a Booter crash loop because:
1. Previous 40GB unlock values (CFG1=0x02669000) persisted in GPU hardware
2. Booter code cannot handle 40GB memory initialization on this firmware
3. Needs hardware reset via BIOS GPU disable/enable cycle

Once recovered:
- New Gen 5 implementation should work
- Will write to correct override register (0x8872c)
- Firmware will properly negotiate Gen 5 x8

## Firmware Behavior

Firmware initialization sequence:
1. Booter runs, crashes with 0x5
2. On retry, reads CFG1 register
3. Sees 0x02669000 (40GB) and calculates WPR for 40GB
4. Checks XVE_OVR at 0x8872c for Gen capability
5. Attempts to initialize with 40GB + Gen capability
6. Booter crashes again (firmware incompatibility with 40GB)

The Booter code appears to have hardcoded assumptions about native 8-10GB memory.
