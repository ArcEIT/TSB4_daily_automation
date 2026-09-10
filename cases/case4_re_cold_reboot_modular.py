#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Case4: RE cold reboot -> ETH BH / WiFi BH onboarding check."""
import argparse
import os
import sys
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from testlib import config as cfg
from testlib.logger import init_log_filenames, init_summary_log, log_progress, log_separator
from testlib.env_info import get_environment_fw_versions_close_browser
from testlib.relay import control_relay, control_relay_channel, restore_eth_backhaul, restore_eth_backhaul_between_loops
from testlib.serial_console import receive_monitor, start_background_serial_logger, stop_background_serial_logger
from testlib.onboarding import run_polling_or_recover
from cases._case_common import add_common_args, apply_common_args


def parse_args():
    parser = argparse.ArgumentParser(description="Case4 RE cold reboot onboarding test")
    add_common_args(parser)
    parser.add_argument("--re-cold-power-relay-port", type=int, default=cfg.RE_COLD_POWER_RELAY_PORT)
    parser.add_argument("--re-cold-reboot-power-off-time", type=int, default=cfg.RE_COLD_REBOOT_POWER_OFF_TIME)
    parser.add_argument("--re-cold-reboot-post-wait", type=int, default=cfg.RE_COLD_REBOOT_POST_WAIT)
    parser.add_argument("--re-cold-reboot-relay-post-wait", type=int, default=cfg.RE_COLD_REBOOT_RELAY_POST_WAIT)
    parser.add_argument("--re-cold-reboot-init-wait", type=int, default=None, help="Backward compatibility: set both ETH/WiFi init wait")
    parser.add_argument("--case4-eth-init-wait", type=int, default=cfg.CASE4_ETH_ONBOARDING_INIT_WAIT_TIME)
    parser.add_argument("--case4-wifi-init-wait", type=int, default=cfg.CASE4_WIFI_ONBOARDING_INIT_WAIT_TIME)
    parser.add_argument("--case4-max-total-limit", type=int, default=cfg.CASE4_MAX_TOTAL_LIMIT)
    return parser.parse_args()


def apply_args(args):
    apply_common_args(args)
    cfg.RE_COLD_POWER_RELAY_PORT = args.re_cold_power_relay_port
    cfg.RE_COLD_REBOOT_POWER_OFF_TIME = args.re_cold_reboot_power_off_time
    cfg.RE_COLD_REBOOT_POST_WAIT = args.re_cold_reboot_post_wait
    cfg.RE_COLD_REBOOT_RELAY_POST_WAIT = args.re_cold_reboot_relay_post_wait
    if args.re_cold_reboot_init_wait is not None:
        cfg.CASE4_ETH_ONBOARDING_INIT_WAIT_TIME = args.re_cold_reboot_init_wait
        cfg.CASE4_WIFI_ONBOARDING_INIT_WAIT_TIME = args.re_cold_reboot_init_wait
    else:
        cfg.CASE4_ETH_ONBOARDING_INIT_WAIT_TIME = args.case4_eth_init_wait
        cfg.CASE4_WIFI_ONBOARDING_INIT_WAIT_TIME = args.case4_wifi_init_wait
    cfg.CASE4_MAX_TOTAL_LIMIT = args.case4_max_total_limit


def get_stage_init_wait(interface_name):
    return cfg.CASE4_ETH_ONBOARDING_INIT_WAIT_TIME if interface_name == "ETH BH" else cfg.CASE4_WIFI_ONBOARDING_INIT_WAIT_TIME


def execute_stage(loop, interface_name, relay_state):
    log_separator(f"LOOP {loop} - {interface_name} 測試開始")
    log_progress(
        f"STEP: Relay {cfg.RELAY_ETH_PORT} 切換 ({relay_state.upper()}) 配置 {interface_name}"
    )
    control_relay(relay_state)
    receive_monitor(cfg.RELAY_SETTLE_TIME)

    log_progress(
        f"STEP: RE cold reboot power cycle - relay {cfg.RE_COLD_POWER_RELAY_PORT} off "
        f"-> 等待 {cfg.RE_COLD_REBOOT_POWER_OFF_TIME} 秒 -> relay {cfg.RE_COLD_POWER_RELAY_PORT} on"
    )
    log_progress(f"RE cold reboot: relay {cfg.RE_COLD_POWER_RELAY_PORT} off")
    control_relay_channel(cfg.RE_COLD_POWER_RELAY_PORT, "off")
    log_progress(f"等待 RE power off {cfg.RE_COLD_REBOOT_POWER_OFF_TIME} 秒...")
    receive_monitor(cfg.RE_COLD_REBOOT_POWER_OFF_TIME)

    log_progress(f"RE cold reboot: relay {cfg.RE_COLD_POWER_RELAY_PORT} on")
    control_relay_channel(cfg.RE_COLD_POWER_RELAY_PORT, "on")
    duration_start_time = time.time()
    log_progress(f"等待 RE power on / boot 開始 {cfg.RE_COLD_REBOOT_POST_WAIT} 秒...")
    receive_monitor(cfg.RE_COLD_REBOOT_POST_WAIT)

    init_wait_time = get_stage_init_wait(interface_name)
    log_progress(f"{interface_name} onboarding init wait = {init_wait_time} 秒")

    return run_polling_or_recover(
        loop,
        interface_name,
        init_wait_time,
        cfg.ONBOARDING_THRESHOLD,
        f"{interface_name.replace(' ', '_')}_Fail",
        duration_start_time=duration_start_time,
        max_total_limit=cfg.CASE4_MAX_TOTAL_LIMIT,
    )


def run_test():
    try:
        router_fw, booster_fw = get_environment_fw_versions_close_browser()
        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {cfg.TOTAL_LOOPS} Loops) - {cfg.TEST_CASE_NAME}")
        log_progress("Case4 policy: RE cold reboot; ETH/WiFi init wait split; reboot uses NORMAL_MAX_TOTAL_LIMIT; ETH BH FAIL will not continue WiFi BH.")

        control_relay_channel(cfg.RE_COLD_POWER_RELAY_PORT, "on")
        receive_monitor(cfg.RELAY_SETTLE_TIME)

        for loop in range(1, cfg.TOTAL_LOOPS + 1):
            if not execute_stage(loop, "ETH BH", "on"):
                log_progress(f"LOOP {loop} ETH BH FAIL，停止測試，不繼續 WiFi BH。")
                return 1
            if not execute_stage(loop, "WiFi BH", "off"):
                log_progress(f"LOOP {loop} WiFi BH FAIL，停止測試。")
                return 1
            log_progress(f"LOOP {loop} PASS。")
            restore_eth_backhaul_between_loops(loop)

        restore_eth_backhaul("測試 PASS 結束")
        log_separator("所有測試迴圈執行完畢，結果 PASS")
        return 0
    except KeyboardInterrupt:
        log_progress("使用者中斷測試。")
        restore_eth_backhaul("使用者中斷")
        return 130
    except Exception as e:
        log_progress(f"主程式發生未預期錯誤: {type(e).__name__}: {e}")
        restore_eth_backhaul("主程式未預期錯誤")
        return 1


if __name__ == "__main__":
    cfg.TEST_CASE_NAME = "case4_RE Cold Reboot Onboarding"
    args = parse_args()
    apply_args(args)
    init_log_filenames()
    start_background_serial_logger()
    exit_code = 1
    try:
        exit_code = run_test()
    finally:
        stop_background_serial_logger()
    raise SystemExit(exit_code)
