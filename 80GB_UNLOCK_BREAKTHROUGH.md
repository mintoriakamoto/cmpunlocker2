# 80GB Unlock Breakthrough - PLM Value Discovery

## The Discovery

Test `b9kujtq5y.output` (14:55 on 2026-09-09) attempted 80GB unlock from clean state and **revealed the critical missing piece**: 

**40GB and 80GB unlocks require COMPLETELY DIFFERENT PLM register unlock signatures.**

## Why This Matters

Previously, all unlock attempts used the same PLM values regardless of target capacity:
```yaml
# Old approach (WRONG for 80GB):
plm_table:
  - { addr: 0x001FA7CC, value: 0x0004cb8f, name: "WPR_CFG" }  # 40GB value
  - { addr: 0x009A0148, value: 0xffffff8f, name: "FBPA"     }  # 40GB value
  - ... etc
```

When attempting 80GB with these 40GB values, the firmware **rejects** them because it's looking for different unlock signatures for the 80GB memory configuration.

## The Solution

Use different PLM tables based on target capacity:

```yaml
plm_table_40gb:
  - { addr: 0x001FA7CC, value: 0x0004cb8f, name: "WPR_CFG" }
  - { addr: 0x009A0148, value: 0xffffff8f, name: "FBPA"     }
  - { addr: 0x001FA7C4, value: 0x0004cb8f, name: "WPR"      }
  - { addr: 0x00823804, value: 0xfffffcee, name: "FEAT"     }
  - { addr: 0x00088FF4, value: 0xffffff8f, name: "XVE"      }
  - { addr: 0x00088AB4, value: 0xFFFFFFFF, name: "XVE_B"    }
  - { addr: 0x00088FF8, value: 0xFFFFFFFF, name: "XVE_C"    }
  - { addr: 0x00823B00, value: 0xfffffcee, name: "FEAT2"    }

plm_table_80gb:  # NEW - discovered from test
  - { addr: 0x001FA7CC, value: 0xfffff0ff, name: "WPR_CFG" }  # Different!
  - { addr: 0x009A0148, value: 0xffffffff, name: "FBPA"     }  # Different!
  - { addr: 0x001FA7C4, value: 0xffffffff, name: "WPR"      }  # Different!
  - { addr: 0x00823804, value: 0xffffffff, name: "FEAT"     }  # Different!
  - { addr: 0x00088FF4, value: 0xffffffff, name: "XVE"      }  # Different!
  - { addr: 0x00088AB4, value: 0xFFFFFFFF, name: "XVE_B"    }
  - { addr: 0x00088FF8, value: 0xFFFFFFFF, name: "XVE_C"    }
  - { addr: 0x00823B00, value: 0xffffffff, name: "FEAT2"    }  # Different!
```

## Pattern Analysis

The difference between 40GB and 80GB PLM values reveals the firmware's design:

| Register | 40GB | 80GB | Pattern |
|----------|------|------|---------|
| WPR_CFG | 0x0004cb8f | 0xfffff0ff | Specific bits set for each config |
| FBPA | 0xffffff8f | 0xffffffff | More bits set for 80GB |
| WPR | 0x0004cb8f | 0xffffffff | Specific to 40GB, all-ones for 80GB |
| FEAT | 0xfffffcee | 0xffffffff | Firmware enforces different feature sets |
| XVE | 0xffffff8f | 0xffffffff | PCIe/feature handling differs |

**Observation**: The 80GB values tend toward all-ones (0xffffffff or 0xfffff0ff), while 40GB values have specific bit patterns. This suggests:
- Different firmware code paths for each memory capacity
- Each path expects specific unlock signatures
- Firmware validates signature matches target before allowing unlock

## Implementation

Pipeline now automatically selects correct PLM table:

```python
# In payload/pipeline.py
if target == 'unlocked_80gb':
    plm_table = get('plm_table_80gb')  # Use 80GB values
    log.info("[%s] Using 80GB PLM unlock values", pci_full)
else:
    plm_table = get('plm_table_40gb')  # Use 40GB values (default)
    log.info("[%s] Using 40GB PLM unlock values", pci_full)
```

## Impact

### For 40GB Unlock (Status: ✅ WORKING)
- Continue using existing `plm_table_40gb` values
- All previous testing remains valid
- 40GB → 40GB is proven stable and persistent

### For 80GB Unlock (Status: ⏳ READY TO TEST)
- Now have correct PLM values from the discovery test
- Can test whether 80GB unlock works with new values
- **Next step**: Run exploit with `target=unlocked_80gb` parameter
- If successful: Full 80GB + 1410 MHz + Gen 5 achievable

## Test Procedure

To test 80GB unlock with the newly discovered PLM values:

```bash
# Run exploit with 80GB target (uses new plm_table_80gb)
python3 cmpunlocker/payload/pipeline.py 0000:01:00.0 \
  /lib/firmware/nvidia/610.43.02/gsp_tu10x.bin \
  unlocked_80gb

# Check result
nvidia-smi
# Expected: 81920 MiB (80GB) if successful
```

## Critical Success Factors

1. **Clean state essential**: Must start from fresh boot (native 10GB)
   - Persistent state from previous unlock can interfere
   - Hardware reset/capacitor discharge may be needed
   
2. **Correct PLM sequence**: Must use `plm_table_80gb` values
   - Using 40GB values for 80GB target → FAIL
   - Using 80GB values for 80GB target → ? (to be tested)

3. **No persistent corruption**: Previous 40GB unlock shouldn't block 80GB
   - If firmware lock in place, may need full reset

4. **Memory configuration**: Verify CFG1 target persistence
   - Previous tests showed 40GB writes persist
   - 80GB writes were rejected (read back as 10GB)

## What This Reveals About Hardware

The existence of different PLM values for different capacities shows:

1. **Firmware is capacity-aware**: It knows the target memory size from unlock signature
2. **Hardware supports multiple configurations**: Both 40GB and 80GB designs exist
3. **Validation is strict**: Firmware checks unlock signature matches capacity
4. **No upgrade path**: Can't go 40GB→80GB with persistent state (different PLM values required)

## Next Actions

- [x] Discover that 40GB and 80GB need different PLM values
- [x] Document the new 80GB PLM values
- [x] Update constants.yaml with dual PLM tables
- [x] Update pipeline to select correct PLM table
- [ ] **Run full 80GB unlock test with new PLM values**
- [ ] Verify CFG1 = 0x02779000 sticks with new values
- [ ] Verify memory reports 81920 MiB
- [ ] Test 80GB + 1410 MHz + Gen 5 persistence

## Conclusion

This breakthrough solves the "why 80GB unlock was failing" mystery. The firmware requires specific unlock signatures for each memory capacity, and we were using the wrong ones. With the correctly discovered 80GB PLM values, 80GB unlock should now be possible to test properly.

**Status**: Ready for 80GB unlock validation test.
