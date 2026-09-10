#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Case8 Reboot RE onboarding test."""
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
from cases._gui_action_case import run_gui_action_case


def parse_args():
    parser = argparse.ArgumentParser(description="Case8 Reboot RE onboarding test")
    add_common_args(parser)
    parser.add_argument("--reboot-sync-wait", type=int, default=cfg.REBOOT_SYNC_WAIT)
    parser.add_argument("--reboot-init-wait", type=int, default=None, help="Backward compatibility: set both ETH/WiFi init wait")
    parser.add_argument("--case8-eth-init-wait", type=int, default=cfg.CASE8_ETH_ONBOARDING_INIT_WAIT_TIME)
    parser.add_argument("--case8-wifi-init-wait", type=int, default=cfg.CASE8_WIFI_ONBOARDING_INIT_WAIT_TIME)
    parser.add_argument("--case8-max-total-limit", type=int, default=cfg.CASE8_MAX_TOTAL_LIMIT)
    return parser.parse_args()


def apply_args(args):
    apply_common_args(args)
    cfg.REBOOT_SYNC_WAIT = args.reboot_sync_wait
    if args.reboot_init_wait is not None:
        cfg.CASE8_ETH_ONBOARDING_INIT_WAIT_TIME = args.reboot_init_wait
        cfg.CASE8_WIFI_ONBOARDING_INIT_WAIT_TIME = args.reboot_init_wait
    else:
        cfg.CASE8_ETH_ONBOARDING_INIT_WAIT_TIME = args.case8_eth_init_wait
        cfg.CASE8_WIFI_ONBOARDING_INIT_WAIT_TIME = args.case8_wifi_init_wait
    cfg.CASE8_MAX_TOTAL_LIMIT = args.case8_max_total_limit


if __name__ == "__main__":
    cfg.TEST_CASE_NAME = "case8_Reboot RE Onboarding"
    args = parse_args()
    apply_args(args)
    init_log_filenames()
    start_background_serial_logger()
    exit_code = 1
    try:
        ok = run_gui_action_case(
            cfg.XPATH_REBOOT_RE,
            "Reboot RE",
            cfg.CASE8_MAX_TOTAL_LIMIT,
            cfg.ONBOARDING_THRESHOLD,
            cfg.CASE8_ETH_ONBOARDING_INIT_WAIT_TIME,
            cfg.CASE8_WIFI_ONBOARDING_INIT_WAIT_TIME,
            precheck_init_wait=cfg.CASE8_WIFI_PRECHECK_INIT_WAIT,
            precheck_threshold=cfg.CASE8_WIFI_PRECHECK_THRESHOLD,
            precheck_max_limit=cfg.CASE8_WIFI_PRECHECK_MAX_LIMIT,
        )
        exit_code = 0 if ok else 1
    finally:
        stop_background_serial_logger()
    raise SystemExit(exit_code)
