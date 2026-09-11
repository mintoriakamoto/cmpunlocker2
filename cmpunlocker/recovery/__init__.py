"""
recovery/__init__.py — CMP 170HX recovery module.

All recovery methods for the CMP 170HX unlock:

1. PLM recovery — re-open locked PLM registers
2. Gen2 recovery — retrain PCIe to Gen2 after cold boot
3. Driver recovery — rebuild driver after kernel upgrade
4. GSP recovery — restore corrupted GSP firmware
5. Walls — every error/issue/edge case guard WITH RECOVERY
6. Full recovery — orchestrated sequence of all methods
"""

from recovery.plm import recover_plm, check_plm_status
from recovery.gen2 import recover_gen2, check_gen2_status
from recovery.driver import recover_driver, check_driver_status
from recovery.gsp import recover_gsp, check_gsp_status
from recovery.walls import (
    guard_root, guard_driver_loaded, guard_bar0_accessible,
    guard_device_visible, guard_device_id_supported,
    guard_gsp_firmware, guard_gsp_not_corrupted,
    guard_no_gpu_processes, guard_modules_can_unload,
    guard_modules_unloaded, guard_services_stopped,
    guard_services_restarted, guard_start_limit_reset,
    guard_kernel_headers, guard_driver_source,
    guard_srcversion_match, guard_gsp_backup,
    guard_plm_not_stuck, guard_wpr2_valid,
    guard_gen2_not_stuck, guard_not_fast_cycling,
    guard_device_reappears, guard_nvidia_smi_responsive,
    guard_sigterm_handler, guard_not_80gb_target,
    guard_correct_lmr, ExponentialBackoff, guard_not_speed15,
    Bar0Flock,
    guard_no_booter_flood, guard_feature_registers_ok,
    guard_no_bad_swap_entries, guard_nvidia_ctk_libs,
    guard_persistence_mode, guard_no_cascade_failures,
    guard_no_apparmor_denials, guard_no_xid_errors,
    guard_not_flr_state, guard_gen2_reliable,
    guard_kernel_taint, guard_gpu_memory_ok,
    guard_iommu_backup,
)
from recovery.orchestrator import full_recovery, diagnose

__all__ = [
    'recover_plm', 'check_plm_status',
    'recover_gen2', 'check_gen2_status',
    'recover_driver', 'check_driver_status',
    'recover_gsp', 'check_gsp_status',
    'full_recovery', 'diagnose',
    # Wall guards (all have fix=True option for auto-recovery)
    'guard_root', 'guard_driver_loaded', 'guard_bar0_accessible',
    'guard_device_visible', 'guard_device_id_supported',
    'guard_gsp_firmware', 'guard_gsp_not_corrupted',
    'guard_no_gpu_processes', 'guard_modules_can_unload',
    'guard_modules_unloaded', 'guard_services_stopped',
    'guard_services_restarted', 'guard_start_limit_reset',
    'guard_kernel_headers', 'guard_driver_source',
    'guard_srcversion_match', 'guard_gsp_backup',
    'guard_plm_not_stuck', 'guard_wpr2_valid',
    'guard_gen2_not_stuck', 'guard_not_fast_cycling',
    'guard_device_reappears', 'guard_nvidia_smi_responsive',
    'guard_sigterm_handler', 'guard_not_80gb_target',
    'guard_correct_lmr', 'ExponentialBackoff', 'guard_not_speed15',
    'Bar0Flock',
    'guard_no_booter_flood', 'guard_feature_registers_ok',
    'guard_no_bad_swap_entries', 'guard_nvidia_ctk_libs',
    'guard_persistence_mode', 'guard_no_cascade_failures',
    'guard_no_apparmor_denials', 'guard_no_xid_errors',
    'guard_not_flr_state', 'guard_gen2_reliable',
    'guard_kernel_taint', 'guard_gpu_memory_ok',
    'guard_iommu_backup',
]
