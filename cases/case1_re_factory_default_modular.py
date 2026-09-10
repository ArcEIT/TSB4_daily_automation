#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
case1_re_factory_default_modular.py

每個 loop 都執行：
    RE factory_default -> ETH BH onboarding check -> WiFi BH onboarding check

這版不再 import 大型 common.py，而是使用 testlib 小模組。
"""
import argparse
import os
import sys
import time

# 讓從 cases/ 目錄執行時也能 import ../testlib
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from testlib import config as cfg
from testlib.logger import init_log_filenames, init_summary_log, log_progress, log_separator, write_recovery_note, write_summary, summary_loop_display
from testlib.env_info import get_environment_fw_versions_close_browser
from testlib.relay import control_relay, restore_eth_backhaul, restore_eth_backhaul_between_loops
from testlib.serial_console import receive_monitor, send_command_with_timestamp, start_background_serial_logger, stop_background_serial_logger
from testlib.onboarding import poll_booster_console
from testlib.recovery import safe_handle_fail_recovery


def parse_args():
    parser = argparse.ArgumentParser(description="Case1 RE factory default onboarding test")
    parser.add_argument("--loops", type=int, default=cfg.TOTAL_LOOPS)
    parser.add_argument("--booster-port", default=cfg.BOOSTER_PORT)
    parser.add_argument("--relay-port", default=cfg.RELAY_PORT)
    parser.add_argument("--case1-factory-default-init-wait", type=int, default=cfg.CASE1_FACTORY_DEFAULT_INIT_WAIT_TIME)
    parser.add_argument("--case1-normal-init-wait", type=int, default=cfg.CASE1_NORMAL_INIT_WAIT_TIME)
    parser.add_argument("--case1-factory-default-max-total", type=int, default=cfg.CASE1_FACTORY_DEFAULT_MAX_TOTAL_LIMIT)
    parser.add_argument("--case1-normal-max-total", type=int, default=cfg.CASE1_NORMAL_MAX_TOTAL_LIMIT)
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
    cfg.CASE1_FACTORY_DEFAULT_INIT_WAIT_TIME = args.case1_factory_default_init_wait
    cfg.CASE1_NORMAL_INIT_WAIT_TIME = args.case1_normal_init_wait
    cfg.CASE1_FACTORY_DEFAULT_MAX_TOTAL_LIMIT = args.case1_factory_default_max_total
    cfg.CASE1_NORMAL_MAX_TOTAL_LIMIT = args.case1_normal_max_total
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
        log_progress("Case1 policy: 每個 loop 都執行 RE factory default; ETH BH FAIL will not continue WiFi BH.")

        for loop in range(1, cfg.TOTAL_LOOPS + 1):
            loop_label = f"{loop}(Def)"

            log_separator(f"LOOP {loop} - Factory Default + ETH BH 測試開始")
            log_progress("STEP: 切換 ETH BH - relay 6 on")
            control_relay("on")
            receive_monitor(2)

            log_progress("STEP: 送出 RE factory_default 指令...")
            cmd_ok, duration_start_time = send_command_with_timestamp("factory_default\n", wait_after=0)
            if not cmd_ok:
                write_summary(summary_loop_display(loop_label, "ETH BH"), "ETH BH", "N/A", "FAIL", "Serial Command Error")
                write_recovery_note("ETH BH")
                safe_handle_fail_recovery(f"Loop{loop}_{cfg.CASE_ID}_FactoryDefault_Command_Fail")
                return 1

            result = poll_booster_console(
                loop_label,
                "ETH BH",
                cfg.CASE1_FACTORY_DEFAULT_INIT_WAIT_TIME,
                cfg.RESET_ONBOARDING_THRESHOLD,
                max_total_limit=cfg.CASE1_FACTORY_DEFAULT_MAX_TOTAL_LIMIT,
                duration_start_time=duration_start_time,
            )
            if not result:
                log_progress(f"LOOP {loop} ETH BH FAIL，停止測試，不繼續 WiFi BH。")
                write_recovery_note("ETH BH")
                safe_handle_fail_recovery(f"Loop{loop}_{cfg.CASE_ID}_FactoryDefault_ETH_BH_Fail")
                return 1

            log_progress(f"LOOP {loop} ETH BH PASS，冷卻後切換 WiFi BH。")
            log_separator(f"LOOP {loop} - WiFi BH 測試開始")
            log_progress("STEP: 切換 WiFi BH - relay 6 off")
            control_relay("off")
            duration_start_time = time.time()
            receive_monitor(cfg.RELAY_SETTLE_TIME)

            result = poll_booster_console(
                loop_label,
                "WiFi BH",
                cfg.CASE1_NORMAL_INIT_WAIT_TIME,
                cfg.RESET_ONBOARDING_THRESHOLD,
                max_total_limit=cfg.CASE1_NORMAL_MAX_TOTAL_LIMIT,
                duration_start_time=duration_start_time,
            )
            if not result:
                log_progress(f"LOOP {loop} WiFi BH FAIL，停止測試。")
                write_recovery_note("WiFi BH")
                safe_handle_fail_recovery(f"Loop{loop}_{cfg.CASE_ID}_FactoryDefault_WiFi_BH_Fail")
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
    cfg.TEST_CASE_NAME = "case1_Factory Default Onboarding"
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
