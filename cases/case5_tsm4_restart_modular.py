#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Case5: TSM4 GUI Restart -> ETH BH / WiFi BH onboarding check."""
import argparse
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from testlib import config as cfg
from testlib.logger import init_log_filenames, init_summary_log, log_progress, log_separator, write_summary, summary_loop_display
from testlib.env_info import get_router_fw_version, get_booster_fw_version
from testlib.web_gui import trigger_tsm4_restart
from testlib.relay import control_relay, restore_eth_backhaul, restore_eth_backhaul_between_loops
from testlib.serial_console import receive_monitor, start_background_serial_logger, stop_background_serial_logger
from testlib.onboarding import run_polling_or_recover
from cases._case_common import add_common_args, apply_common_args


def parse_args():
    parser = argparse.ArgumentParser(description="Case5 TSM4 GUI Restart onboarding test")
    add_common_args(parser)
    parser.add_argument("--tsm4-reboot-chrome-close-wait", type=int, default=cfg.TSM4_REBOOT_CHROME_CLOSE_WAIT)
    parser.add_argument("--tsm4-reboot-post-wait", type=int, default=cfg.TSM4_REBOOT_POST_WAIT)
    parser.add_argument("--tsm4-reboot-relay-post-wait", type=int, default=cfg.TSM4_REBOOT_RELAY_POST_WAIT)
    parser.add_argument("--case5-eth-init-wait", type=int, default=cfg.CASE5_ETH_ONBOARDING_INIT_WAIT_TIME)
    parser.add_argument("--case5-wifi-init-wait", type=int, default=cfg.CASE5_WIFI_ONBOARDING_INIT_WAIT_TIME)
    parser.add_argument("--case5-max-total-limit", type=int, default=cfg.CASE5_MAX_TOTAL_LIMIT)
    return parser.parse_args()


def apply_args(args):
    apply_common_args(args)
    cfg.TSM4_REBOOT_CHROME_CLOSE_WAIT = args.tsm4_reboot_chrome_close_wait
    cfg.TSM4_REBOOT_POST_WAIT = args.tsm4_reboot_post_wait
    cfg.TSM4_REBOOT_RELAY_POST_WAIT = args.tsm4_reboot_relay_post_wait
    cfg.CASE5_ETH_ONBOARDING_INIT_WAIT_TIME = args.case5_eth_init_wait
    cfg.CASE5_WIFI_ONBOARDING_INIT_WAIT_TIME = args.case5_wifi_init_wait
    cfg.CASE5_MAX_TOTAL_LIMIT = args.case5_max_total_limit


def get_stage_init_wait(interface_name):
    return cfg.CASE5_ETH_ONBOARDING_INIT_WAIT_TIME if interface_name == "ETH BH" else cfg.CASE5_WIFI_ONBOARDING_INIT_WAIT_TIME


def execute_stage(loop, interface_name, relay_state, active_driver=None):
    log_separator(f"LOOP {loop} - {interface_name} 測試開始")
    log_progress(f"STEP: Relay 切換 ({relay_state.upper()}) 配置 {interface_name}")
    control_relay(relay_state)
    receive_monitor(cfg.RELAY_SETTLE_TIME)

    log_progress(f"STEP: 準備執行 {interface_name} 測試 (GUI 觸發 TSM4 Restart)")

    gui_ok, duration_start_time = trigger_tsm4_restart(active_driver)
    if not gui_ok:
        write_summary(summary_loop_display(str(loop), interface_name), interface_name, "N/A", "FAIL", "GUI Error")
        log_progress(f"!! {interface_name} GUI 操作失敗，只寫 Summary，不執行 diag / recovery !!")
        restore_eth_backhaul(f"{interface_name} GUI Error")
        return False

    log_progress(f"等待 TSM4 reboot 後系統穩定 {cfg.TSM4_REBOOT_POST_WAIT}s...")
    receive_monitor(cfg.TSM4_REBOOT_POST_WAIT)

    init_wait_time = get_stage_init_wait(interface_name)
    log_progress(f"{interface_name} onboarding init wait = {init_wait_time} 秒")

    return run_polling_or_recover(
        loop,
        interface_name,
        init_wait_time,
        cfg.ONBOARDING_THRESHOLD,
        f"{interface_name.replace(' ', '_')}_Fail",
        duration_start_time=duration_start_time,
        max_total_limit=cfg.CASE5_MAX_TOTAL_LIMIT,
    )


def run_test():
    active_driver = None
    try:
        router_fw, active_driver = get_router_fw_version()
        log_progress(f"GW FW 取得完成，保留 Chrome，等待 {cfg.GW_FW_TO_GUI_ACTION_SLEEP} 秒後繼續 GUI login/navigation...")
        receive_monitor(cfg.GW_FW_TO_GUI_ACTION_SLEEP)
        booster_fw = get_booster_fw_version()

        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {cfg.TOTAL_LOOPS} Loops) - {cfg.TEST_CASE_NAME}")
        log_progress("Case5 policy: TSM4 GUI Restart; ETH/WiFi init wait split; reboot uses NORMAL_MAX_TOTAL_LIMIT; ETH BH FAIL will not continue WiFi BH.")

        for loop in range(1, cfg.TOTAL_LOOPS + 1):
            eth_pass = execute_stage(loop, "ETH BH", "on", active_driver)
            active_driver = None
            if not eth_pass:
                log_progress(f"LOOP {loop} ETH BH FAIL / GUI Error，停止測試，不繼續 WiFi BH。")
                return 1

            wifi_pass = execute_stage(loop, "WiFi BH", "off", None)
            if not wifi_pass:
                log_progress(f"LOOP {loop} WiFi BH FAIL / GUI Error，停止測試。")
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
    finally:
        if active_driver is not None:
            try:
                active_driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    cfg.TEST_CASE_NAME = "case5_TSM4 Restart Onboarding"
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
