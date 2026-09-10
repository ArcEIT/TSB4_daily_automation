#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Case3: RE warm reboot -> ETH BH / WiFi BH onboarding check."""
import argparse
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from testlib import config as cfg
from testlib.logger import init_log_filenames, init_summary_log, log_progress, log_separator, write_summary, summary_loop_display
from testlib.env_info import get_environment_fw_versions_close_browser
from testlib.relay import control_relay, restore_eth_backhaul, restore_eth_backhaul_between_loops
from testlib.serial_console import receive_monitor, send_command_with_timestamp, start_background_serial_logger, stop_background_serial_logger
from testlib.onboarding import run_polling_or_recover
from testlib.recovery import safe_handle_fail_recovery
from cases._case_common import add_common_args, apply_common_args


def parse_args():
    parser = argparse.ArgumentParser(description="Case3 RE warm reboot onboarding test")
    add_common_args(parser)
    parser.add_argument("--re-warm-reboot-post-wait", type=int, default=cfg.RE_WARM_REBOOT_POST_WAIT)
    parser.add_argument("--re-warm-reboot-relay-post-wait", type=int, default=cfg.RE_WARM_REBOOT_RELAY_POST_WAIT)
    parser.add_argument("--re-warm-reboot-init-wait", type=int, default=None, help="Backward compatibility: set both ETH/WiFi init wait")
    parser.add_argument("--case3-eth-init-wait", type=int, default=cfg.CASE3_ETH_ONBOARDING_INIT_WAIT_TIME)
    parser.add_argument("--case3-wifi-init-wait", type=int, default=cfg.CASE3_WIFI_ONBOARDING_INIT_WAIT_TIME)
    parser.add_argument("--case3-max-total-limit", type=int, default=cfg.CASE3_MAX_TOTAL_LIMIT)
    return parser.parse_args()


def apply_args(args):
    apply_common_args(args)
    cfg.RE_WARM_REBOOT_POST_WAIT = args.re_warm_reboot_post_wait
    cfg.RE_WARM_REBOOT_RELAY_POST_WAIT = args.re_warm_reboot_relay_post_wait
    if args.re_warm_reboot_init_wait is not None:
        cfg.CASE3_ETH_ONBOARDING_INIT_WAIT_TIME = args.re_warm_reboot_init_wait
        cfg.CASE3_WIFI_ONBOARDING_INIT_WAIT_TIME = args.re_warm_reboot_init_wait
    else:
        cfg.CASE3_ETH_ONBOARDING_INIT_WAIT_TIME = args.case3_eth_init_wait
        cfg.CASE3_WIFI_ONBOARDING_INIT_WAIT_TIME = args.case3_wifi_init_wait
    cfg.CASE3_MAX_TOTAL_LIMIT = args.case3_max_total_limit


def get_stage_init_wait(interface_name):
    return cfg.CASE3_ETH_ONBOARDING_INIT_WAIT_TIME if interface_name == "ETH BH" else cfg.CASE3_WIFI_ONBOARDING_INIT_WAIT_TIME


def execute_stage(loop, interface_name, relay_state):
    log_separator(f"LOOP {loop} - {interface_name} 測試開始")
    backhaul_name = "ETH BH" if str(relay_state).lower() == "on" else "WiFi BH"
    log_progress(f"STEP: Relay 切換 ({relay_state.upper()}) 配置 {interface_name}")
    control_relay(relay_state)
    receive_monitor(cfg.RELAY_SETTLE_TIME)

    log_progress("送出 RE warm reboot 指令: reboot")
    cmd_ok, duration_start_time = send_command_with_timestamp("reboot\n", wait_after=0)
    if not cmd_ok:
        write_summary(summary_loop_display(str(loop), interface_name), interface_name, "N/A", "FAIL", "Serial Command Error")
        safe_handle_fail_recovery(f"Loop{loop}_{cfg.CASE_ID}_WarmReboot_Command_Fail")
        return False

    init_wait_time = get_stage_init_wait(interface_name)
    log_progress(f"{interface_name} onboarding init wait = {init_wait_time} 秒")

    return run_polling_or_recover(
        loop,
        interface_name,
        init_wait_time,
        cfg.ONBOARDING_THRESHOLD,
        f"{interface_name.replace(' ', '_')}_Fail",
        duration_start_time=duration_start_time,
        max_total_limit=cfg.CASE3_MAX_TOTAL_LIMIT,
    )


def run_test():
    try:
        router_fw, booster_fw = get_environment_fw_versions_close_browser()
        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {cfg.TOTAL_LOOPS} Loops) - {cfg.TEST_CASE_NAME}")
        log_progress("Case3 policy: RE warm reboot; ETH/WiFi init wait split; reboot uses NORMAL_MAX_TOTAL_LIMIT; ETH BH FAIL will not continue WiFi BH.")

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
    cfg.TEST_CASE_NAME = "case3_RE Warm Reboot Onboarding"
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
