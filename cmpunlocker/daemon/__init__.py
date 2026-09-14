"""Persistence daemon for monitoring and reapplying GPU unlocks.

The watchdog daemon runs continuously and monitors each GPU every
CMPUNLOCKER_CHECK_INTERVAL seconds (default 300s), reapplying unlocks
as needed after driver resets or system reboots.
"""
