"""
recovery/__init__.py — CMP 170HX recovery module.

All recovery methods for the CMP 170HX unlock:

1. PLM recovery — re-open locked PLM registers
2. Gen2 recovery — retrain PCIe to Gen2 after cold boot
3. Driver recovery — rebuild driver after kernel upgrade
4. GSP recovery — restore corrupted GSP firmware
5. Full recovery — orchestrated sequence of all methods
"""

from recovery.plm import recover_plm, check_plm_status
from recovery.gen2 import recover_gen2, check_gen2_status
from recovery.driver import recover_driver, check_driver_status
from recovery.gsp import recover_gsp, check_gsp_status
from recovery.orchestrator import full_recovery, diagnose

__all__ = [
    'recover_plm', 'check_plm_status',
    'recover_gen2', 'check_gen2_status',
    'recover_driver', 'check_driver_status',
    'recover_gsp', 'check_gsp_status',
    'full_recovery', 'diagnose',
]
