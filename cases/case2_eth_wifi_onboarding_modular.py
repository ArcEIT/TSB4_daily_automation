#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
case2_eth_wifi_onboarding_modular.py

ETH BH → WiFi BH → ETH BH onboarding check.
每個 loop 依序執行三段 BH 切換並各自驗證 onboarding。
"""
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
from testlib.relay import control_relay, restore_eth_backhaul, restore_eth_backhaul_between_loops
from testlib.serial_console import receive_monitor, start_background_serial_logger, stop_background_serial_logger
from testlib.onboarding import run_polling_or_recover
from testlib.dut_health import wait_for_onboarding_if_recently_rebooted


def parse_args():
    parser = argparse.ArgumentParser(description="Case2 ETH/WiFi onboarding test")
    parser.add_argument("--loops", type=int, default=cfg.TOTAL_LOOPS)
    parser.add_argument("--booster-port", default=cfg.BOOSTER_PORT)
    parser.add_argument("--relay-port", default=cfg.RELAY_PORT)
    parser.add_argument("--case2-eth-init-wait", type=int, default=cfg.CASE2_ETH_ONBOARDING_INIT_WAIT_TIME)
    parser.add_argument("--case2-wifi-init-wait", type=int, default=cfg.CASE2_WIFI_ONBOARDING_INIT_WAIT_TIME)
    parser.add_argument("--case2-eth-back-init-wait", type=int, default=cfg.CASE2_ETH_BACK_ONBOARDING_INIT_WAIT_TIME)
    parser.add_argument("--case2-max-total-limit", type=int, default=cfg.CASE2_MAX_TOTAL_LIMIT)
    parser.add_argument("--pass-cooldown-time", type=int, default=cfg.PASS_COOLDOWN_TIME)
    parser.add_argument("--loop-eth-restore-wait", type=int, default=cfg.LOOP_ETH_RESTORE_WAIT)
    parser.add_argument("--check-re-status-script", default=cfg.CHECK_RE_STATUS_SCRIPT)
    parser.add_argument("--check-re-status-com-port", default=None)
    parser.add_argument("--check-re-status-com-port-arg", default=cfg.CHECK_RE_STATUS_COM_PORT_ARG)
    parser.add_argument("--enable-fail-reboot-recovery", action="store_true", default=cfg.FAIL_RECOVERY_REBOOT_ENABLE, help="Enable optional RE reboot -f during fail recovery. Default is off.")
    return parser.parse_args()


def apply_args(args):
    cfg.TOTAL_LOOPS = args.loops
    cfg.BOOSTER_PORT = args.booster_port
    cfg.RELAY_PORT = args.relay_port
    cfg.CASE2_ETH_ONBOARDING_INIT_WAIT_TIME = args.case2_eth_init_wait
    cfg.CASE2_WIFI_ONBOARDING_INIT_WAIT_TIME = args.case2_wifi_init_wait
    cfg.CASE2_ETH_BACK_ONBOARDING_INIT_WAIT_TIME = args.case2_eth_back_init_wait
    cfg.CASE2_MAX_TOTAL_LIMIT = args.case2_max_total_limit
    cfg.PASS_COOLDOWN_TIME = args.pass_cooldown_time
    cfg.LOOP_ETH_RESTORE_WAIT = args.loop_eth_restore_wait
    cfg.CHECK_RE_STATUS_SCRIPT = args.check_re_status_script
    cfg.CHECK_RE_STATUS_COM_PORT = args.check_re_status_com_port or cfg.BOOSTER_PORT
    cfg.CHECK_RE_STATUS_COM_PORT_ARG = args.check_re_status_com_port_arg or ""
    cfg.FAIL_RECOVERY_REBOOT_ENABLE = args.enable_fail_reboot_recovery


def run_test():
    try:
        router_fw, booster_fw = get_environment_fw_versions_close_browser()
        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {cfg.TOTAL_LOOPS} Loops) - {cfg.TEST_CASE_NAME}")
        log_progress("Case2 policy: ETH/WiFi onboarding only; ETH BH FAIL will not continue WiFi BH.")

        if not wait_for_onboarding_if_recently_rebooted(log_prefix="[CASE2]"):
            log_progress("Case2 FAIL: DUT onboarding 未就緒 (可能前一個 case 觸發了 reboot)")
            return 1

        for loop in range(1, cfg.TOTAL_LOOPS + 1):
            log_separator(f"LOOP {loop} - ETH BH 測試開始")
            control_relay("on")
            duration_start_time = time.time()
            receive_monitor(cfg.RELAY_SETTLE_TIME)

            if not run_polling_or_recover(
                loop,
                "ETH BH",
                cfg.CASE2_ETH_ONBOARDING_INIT_WAIT_TIME,
                cfg.ONBOARDING_THRESHOLD,
                "ETH_BH_Fail",
                duration_start_time,
                max_total_limit=cfg.CASE2_MAX_TOTAL_LIMIT,
            ):
                log_progress(f"LOOP {loop} ETH BH FAIL，停止測試，不繼續 WiFi BH。")
                return 1

            log_separator(f"LOOP {loop} - WiFi BH 測試開始")
            control_relay("off")
            duration_start_time = time.time()
            receive_monitor(cfg.RELAY_SETTLE_TIME)

            if not run_polling_or_recover(
                loop,
                "WiFi BH",
                cfg.CASE2_WIFI_ONBOARDING_INIT_WAIT_TIME,
                cfg.ONBOARDING_THRESHOLD,
                "WiFi_BH_Fail",
                duration_start_time,
                max_total_limit=cfg.CASE2_MAX_TOTAL_LIMIT,
            ):
                log_progress(f"LOOP {loop} WiFi BH FAIL，停止測試。")
                return 1

            log_separator(f"LOOP {loop} - ETH BH (回切) 測試開始")
            control_relay("on")
            duration_start_time = time.time()
            receive_monitor(cfg.RELAY_SETTLE_TIME)

            if not run_polling_or_recover(
                loop,
                "ETH BH",
                cfg.CASE2_ETH_BACK_ONBOARDING_INIT_WAIT_TIME,
                cfg.ONBOARDING_THRESHOLD,
                "ETH_BH_Back_Fail",
                duration_start_time,
                max_total_limit=cfg.CASE2_MAX_TOTAL_LIMIT,
            ):
                log_progress(f"LOOP {loop} ETH BH (回切) FAIL，停止測試。")
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
    cfg.TEST_CASE_NAME = "case2_ETH-WiFi-ETH BH Onboarding"
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
