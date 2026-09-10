#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit test: Reboot GW+RE, WiFi BH onboarding only.

Assumes environment is already on WiFi BH (no relay switching).

Flow per loop:
  1. GUI: Reboot GW+RE
  2. Wait post_reboot_wait (default 60s) for TSM4 reboot + raspi5 LAN recovery
  3. Start raspi5 air capture (Auto_Scan_BSSID_DumpPackets.sh --bssid)
  4. Wait 5s
  5. Start booster tcpdump (ath1, via serial)
  6. Poll onboarding
     PASS → delete air pcap + cleanup booster tcpdump
     FAIL → diag collect (check_RE_status + diagnosticcomlog.tgz + booster ath1.pcap)
            + fetch raspi5 air pcap
"""
import argparse
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from testlib import config as cfg
from testlib.logger import init_log_filenames
from testlib.serial_console import start_background_serial_logger, stop_background_serial_logger
from cases._case_common import add_common_args, apply_common_args
from cases._gui_action_case import run_gui_action_wifi_bh_unit_case


def parse_args():
    parser = argparse.ArgumentParser(
        description="Unit test: Reboot GW+RE WiFi BH onboarding (no relay switch)"
    )
    add_common_args(parser)
    parser.add_argument("--wifi-init-wait", type=int, default=cfg.CASE6_WIFI_ONBOARDING_INIT_WAIT_TIME)
    parser.add_argument("--max-total-limit", type=int, default=cfg.CASE6_MAX_TOTAL_LIMIT)
    parser.add_argument(
        "--post-reboot-wait", type=int, default=60,
        help="Seconds to wait after Reboot button press before starting captures (default: 60)",
    )
    parser.add_argument(
        "--air-capture", action="store_true",
        default=getattr(cfg, "RASPI5_AIR_CAPTURE_ENABLE", False),
        help="Enable raspi5 air capture (Auto_Scan_BSSID_DumpPackets.sh)",
    )
    parser.add_argument(
        "--bssid",
        default=getattr(cfg, "RASPI5_AIR_CAPTURE_BSSID", ""),
        help="TSM4 5GHz BH BSSID for air capture, e.g. AA:BB:CC:DD:EE:FF",
    )
    parser.add_argument(
        "--air-capture-local-dir",
        default=getattr(cfg, "RASPI5_AIR_CAPTURE_LOCAL_DIR", "."),
        help="Local directory to save raspi5 pcap on FAIL",
    )
    parser.add_argument(
        "--wifi-bh-tcpdump", action="store_true",
        default=getattr(cfg, "WIFI_BH_TCPDUMP_ENABLE", False),
        help="Enable booster ath1 tcpdump (via serial)",
    )
    return parser.parse_args()


def apply_args(args):
    apply_common_args(args)
    cfg.CASE6_WIFI_ONBOARDING_INIT_WAIT_TIME = args.wifi_init_wait
    cfg.CASE6_MAX_TOTAL_LIMIT = args.max_total_limit
    cfg.RASPI5_AIR_CAPTURE_ENABLE = args.air_capture
    cfg.RASPI5_AIR_CAPTURE_BSSID = args.bssid
    cfg.RASPI5_AIR_CAPTURE_LOCAL_DIR = args.air_capture_local_dir
    cfg.WIFI_BH_TCPDUMP_ENABLE = args.wifi_bh_tcpdump
    if args.wifi_bh_tcpdump:
        cfg.WIFI_BH_TCPDUMP_FILTER      = ""
        cfg.WIFI_BH_TCPDUMP_MAX_PACKETS = 0
        cfg.WIFI_BH_TCPDUMP_REMOTE_PATH = "/tmp/wifi_bh_ath1.pcap"


if __name__ == "__main__":
    cfg.TEST_CASE_NAME = "unit_Reboot GW+RE WiFi BH Onboarding"
    args = parse_args()
    apply_args(args)
    init_log_filenames()
    start_background_serial_logger()
    exit_code = 1
    try:
        ok = run_gui_action_wifi_bh_unit_case(
            cfg.XPATH_REBOOT_ALL,
            "Reboot GW+RE",
            cfg.CASE6_MAX_TOTAL_LIMIT,
            cfg.ONBOARDING_THRESHOLD,
            cfg.CASE6_WIFI_ONBOARDING_INIT_WAIT_TIME,
            post_reset_wait=args.post_reboot_wait,
            air_capture_bssid=cfg.RASPI5_AIR_CAPTURE_BSSID if cfg.RASPI5_AIR_CAPTURE_ENABLE else None,
            air_capture_local_dir=cfg.RASPI5_AIR_CAPTURE_LOCAL_DIR,
        )
        exit_code = 0 if ok else 1
    finally:
        stop_background_serial_logger()
    raise SystemExit(exit_code)
