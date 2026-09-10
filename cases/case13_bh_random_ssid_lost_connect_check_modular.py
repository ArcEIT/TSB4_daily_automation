#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
case13_bh_random_ssid_lost_connect_check_modular.py

Case13: BH random SSID check after TSM4 lost connection.

Flow:
  1. Use ETH BH only.
  2. Wait until Booster onboarding is done and GW ping succeeds.
  3. Power off TSM4 by relay 2 off.
  4. Wait for BH lost-connect handling.
  5. Check wireless.@wifi-iface[4].ArcFHRandomSSID through Booster serial console.
     PASS only when the value exists and starts with BH_5_.
     FAIL when ArcFHRandomSSID is missing / "uci: Entry not found" / not BH_5_*,
     or when wireless.@wifi-iface[4].ssid is still normal BH SSID such as Telstra*_Backhaul.
  6. Always power TSM4 back on before exit.

Notes:
  - This case does not run WiFi BH stage.
  - This case does not perform factory-default style GW+RE workaround reboot.
    Keep FAIL_RECOVERY_REBOOT_ENABLE=False and make the top-level runner only
    run GW+Booster workaround for case1/factory-default if that policy is needed.
"""
import argparse
import os
import re
import sys
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from testlib import config as cfg
from testlib.logger import (
    init_log_filenames,
    init_summary_log,
    log_details,
    log_progress,
    log_step,
    log_result,
    log_separator,
    write_summary,
    write_recovery_note,
)
from testlib.env_info import get_environment_fw_versions_close_browser
from testlib.relay import control_relay, control_relay_channel, restore_eth_backhaul
from testlib.recovery import safe_handle_fail_recovery
from testlib.dut_health import wait_for_onboarding_if_recently_rebooted
from testlib.serial_console import (
    receive_monitor,
    get_serial_for_command,
    _SERIAL_IO_LOCK,
    start_background_serial_logger,
    stop_background_serial_logger,
)


DEFAULT_CASE_NAME = "case13_BH_Random_SSID_Lost_Connect_Check"


# Defaults are kept local so this script can run even if config.py has not yet
# been updated with Case13 parameters.
def cfg_get(name, default):
    return getattr(cfg, name, default)


def parse_args():
    parser = argparse.ArgumentParser(description="Case13 BH random SSID lost-connect check")
    parser.add_argument("--loops", type=int, default=cfg_get("TOTAL_LOOPS", 1))
    parser.add_argument("--booster-port", default=cfg_get("BOOSTER_PORT", "COM4"))
    parser.add_argument("--relay-port", default=cfg_get("RELAY_PORT", "COM3"))
    parser.add_argument("--eth-init-wait", type=int, default=cfg_get("CASE13_ETH_ONBOARDING_INIT_WAIT_TIME", 10))
    parser.add_argument("--tsm4-power-off-wait", type=int, default=cfg_get("CASE13_TSM4_POWER_OFF_WAIT", 30))
    parser.add_argument("--tsm4-power-restore-wait", type=int, default=cfg_get("CASE13_TSM4_POWER_RESTORE_WAIT", 120))
    parser.add_argument("--random-prefix", default=cfg_get("CASE13_EXPECTED_RANDOM_PREFIX", "BH_5_"))
    parser.add_argument("--random-ssid-cmd", default=cfg_get("CASE13_ARC_FH_RANDOM_SSID_CMD", "uci get wireless.@wifi-iface[4].ArcFHRandomSSID"))
    parser.add_argument("--check-read-time", type=int, default=cfg_get("CASE13_UCI_CHECK_READ_TIME", 3))
    parser.add_argument("--check-re-status-script", default=cfg_get("CHECK_RE_STATUS_SCRIPT", "check_RE_status.py"))
    parser.add_argument("--check-re-status-com-port", default=None)
    parser.add_argument("--check-re-status-com-port-arg", default=cfg_get("CHECK_RE_STATUS_COM_PORT_ARG", ""))
    parser.add_argument("--enable-fail-reboot-recovery", action="store_true", default=cfg_get("FAIL_RECOVERY_REBOOT_ENABLE", False), help="Normally keep this disabled. This case should not do factory-default style GW+RE workaround reboot.")
    return parser.parse_args()


def apply_args(args):
    cfg.TOTAL_LOOPS = args.loops
    cfg.BOOSTER_PORT = args.booster_port
    cfg.RELAY_PORT = args.relay_port
    cfg.CASE13_ETH_ONBOARDING_INIT_WAIT_TIME = args.eth_init_wait
    cfg.CASE13_TSM4_POWER_OFF_WAIT = args.tsm4_power_off_wait
    cfg.CASE13_TSM4_POWER_RESTORE_WAIT = args.tsm4_power_restore_wait
    cfg.CASE13_EXPECTED_RANDOM_PREFIX = args.random_prefix
    cfg.CASE13_ARC_FH_RANDOM_SSID_CMD = args.random_ssid_cmd
    cfg.CASE13_UCI_CHECK_READ_TIME = args.check_read_time
    cfg.CHECK_RE_STATUS_SCRIPT = args.check_re_status_script
    cfg.CHECK_RE_STATUS_COM_PORT = args.check_re_status_com_port or cfg.BOOSTER_PORT
    cfg.CHECK_RE_STATUS_COM_PORT_ARG = args.check_re_status_com_port_arg or ""

    # Keep case-internal recovery from sending RE reboot unless user explicitly enables it.
    cfg.FAIL_RECOVERY_REBOOT_ENABLE = bool(args.enable_fail_reboot_recovery)


_RANDOM_VALUE_RE = re.compile(r"(?:^|=)['\"]?(BH_5_[A-Za-z0-9_-]+)['\"]?(?:$|\s)")


def _extract_command_body(output, command):
    """Return command output lines after filtering prompt/echo noise."""
    lines = []
    command = str(command).strip()
    for raw in (output or "").replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("root@"):
            # Prompt or echoed prompt line. Keep line only if it also has useful uci error/value.
            if "uci:" not in line and "BH_" not in line and "_Backhaul" not in line:
                continue
        if command and command in line:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def run_serial_command(command, read_time):
    """Run one command on Booster serial console and return filtered command output."""
    ser = None
    close_after_use = False
    try:
        ser, close_after_use = get_serial_for_command()
        if ser is None:
            return False, "", "No serial handle"
        with _SERIAL_IO_LOCK:
            ser.write(b"\r\n")
            receive_monitor(0.5, ser)
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
            log_step(f"Case13 serial check: {command}")
            log_progress(f"[CASE13][SERIAL CMD] {command}")
            ser.write((command.strip() + "\n").encode("utf-8"))
            raw = receive_monitor(read_time, ser)
        body = _extract_command_body(raw, command)
        log_details(f"[CASE13][SERIAL OUTPUT][{command}] begin")
        if body:
            for line in body.splitlines():
                log_details(f"[CASE13][SERIAL OUTPUT] {line}")
        else:
            log_details("[CASE13][SERIAL OUTPUT] <empty>")
        log_details(f"[CASE13][SERIAL OUTPUT][{command}] end")
        log_result(f"Case13 serial check completed: {command}")
        return True, body, "None"
    except Exception as e:
        log_result(f"Case13 serial check FAIL: {command}, {type(e).__name__}: {e}")
        return False, "", f"{type(e).__name__}: {e}"
    finally:
        if close_after_use and ser is not None:
            try:
                ser.close()
            except Exception:
                pass


def parse_random_ssid_value(output, expected_prefix):
    """Extract a BH_5_* random SSID from uci output."""
    if not output:
        return None
    for raw in output.replace("\r", "").split("\n"):
        line = raw.strip().strip("'\"")
        if not line:
            continue
        if "uci: Entry not found" in line:
            return None
        if line.startswith(expected_prefix):
            return line
        m = _RANDOM_VALUE_RE.search(line)
        if m and m.group(1).startswith(expected_prefix):
            return m.group(1)
    return None


def check_bh_random_ssid(args):
    """Check ArcFHRandomSSID only.

    PASS rule:
        ArcFHRandomSSID exists and starts with expected prefix (BH_5_).

    FAIL rules:
        - Serial command failed.
        - ArcFHRandomSSID returns Entry not found (TSM4 still on or not triggered).
        - ArcFHRandomSSID value does not start with BH_5_.
    """
    log_step("Case13: check ArcFHRandomSSID")
    ok, random_output, reason = run_serial_command(args.random_ssid_cmd, args.check_read_time)
    if not ok:
        return False, f"Serial command failed: {reason}"

    random_value = parse_random_ssid_value(random_output, args.random_prefix)
    entry_not_found = "uci: Entry not found" in (random_output or "")

    log_details("[CASE13][VERIFY] --------------------------------------------------")
    log_details(f"[CASE13][VERIFY] ArcFHRandomSSID output : {random_output or '<empty>'}")
    log_details(f"[CASE13][VERIFY] Parsed random SSID    : {random_value or '<none>'}")
    log_details(f"[CASE13][VERIFY] Expected prefix        : {args.random_prefix}")
    log_details("[CASE13][VERIFY] --------------------------------------------------")

    if random_value and random_value.startswith(args.random_prefix):
        log_result(f"Case13 random SSID check PASS: ArcFHRandomSSID={random_value}")
        return True, f"ArcFHRandomSSID={random_value}"

    if entry_not_found:
        log_result("Case13 random SSID check FAIL: ArcFHRandomSSID Entry not found (TSM4 off not triggered)")
        return False, "ArcFHRandomSSID Entry not found"

    log_result("Case13 random SSID check FAIL: ArcFHRandomSSID invalid")
    return False, f"ArcFHRandomSSID invalid: output={random_output or '<empty>'}"


def power_tsm4_off():
    log_separator("TSM4 power off to trigger BH lost-connect random SSID")
    log_step(f"Case13: power off TSM4, relay {cfg.TSM4_POWER_RELAY_PORT} off")
    log_progress(f"[CASE13] TSM4 power off - relay {cfg.TSM4_POWER_RELAY_PORT} off")
    ok = control_relay_channel(cfg.TSM4_POWER_RELAY_PORT, "off")
    log_result(f"Case13 TSM4 power off {'PASS' if ok else 'FAIL'}")
    return ok


def power_tsm4_on(wait_after):
    log_separator("Restore TSM4 power")
    log_step(f"Case13: power on TSM4, relay {cfg.TSM4_POWER_RELAY_PORT} on")
    log_progress(f"[CASE13] TSM4 power on - relay {cfg.TSM4_POWER_RELAY_PORT} on")
    ok = control_relay_channel(cfg.TSM4_POWER_RELAY_PORT, "on")
    log_result(f"Case13 TSM4 power on {'PASS' if ok else 'FAIL'}")
    if wait_after > 0:
        log_step(f"Case13: wait after TSM4 power restore, wait={wait_after}s")
        log_progress(f"[CASE13] TSM4 power restore wait {wait_after} seconds")
        receive_monitor(wait_after)
    return ok


def run_case13(args):
    cfg.TEST_CASE_NAME = DEFAULT_CASE_NAME
    init_log_filenames()

    start_background_serial_logger()
    case_failed = False
    tsm4_powered_off = False
    recovery_done = False

    try:
        router_fw, booster_fw = get_environment_fw_versions_close_browser()
        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {cfg.TOTAL_LOOPS} Loops) - {cfg.TEST_CASE_NAME}")
        log_step(f"Case13 start: loops={cfg.TOTAL_LOOPS}, ETH BH only, expected_prefix={cfg.CASE13_EXPECTED_RANDOM_PREFIX}")
        log_progress("Case13 policy: ETH BH only; PASS requires ArcFHRandomSSID starts with BH_5_; no WiFi BH stage.")
        log_progress("Case13 recovery policy: no factory-default GW+RE workaround inside this case.")

        if not wait_for_onboarding_if_recently_rebooted(log_prefix="[CASE13]"):
            log_progress("Case13 FAIL: DUT onboarding 未就緒 (可能前一個 case 觸發了 reboot)")
            write_summary("0", "Pre-check", "N/A", "FAIL", "DUT_Onboarding_Not_Ready_At_Start")
            return 1

        for loop in range(1, cfg.TOTAL_LOOPS + 1):
            loop_str = str(loop)
            log_separator(f"LOOP {loop} - ETH BH init and TSM4 power off")
            log_step(f"Loop {loop}: switch to ETH BH, relay {cfg.RELAY_ETH_PORT} on")
            log_progress("STEP 1: 切換 ETH BH - relay ETH on")
            control_relay("on")
            receive_monitor(cfg.RELAY_SETTLE_TIME)
            log_step(f"Loop {loop}: ETH BH init wait={cfg.CASE13_ETH_ONBOARDING_INIT_WAIT_TIME}s")
            receive_monitor(cfg.CASE13_ETH_ONBOARDING_INIT_WAIT_TIME)

            log_separator(f"LOOP {loop} - TSM4 power off and BH random SSID check")
            log_step(f"Loop {loop}: TSM4 power off and BH random SSID check")
            if not power_tsm4_off():
                reason = f"TSM4 power off relay {cfg.TSM4_POWER_RELAY_PORT} failed"
                log_result(f"Loop {loop}: Case13 FAIL, {reason}")
                write_summary(loop_str, "ETH BH", "N/A", "FAIL", reason)
                case_failed = True
                write_recovery_note("ETH BH", "Recovery(check_RE_status_only_no_factory_workaround)")
                safe_handle_fail_recovery(f"Loop{loop}_{cfg.CASE_ID}_TSM4_Power_Off_Fail")
                recovery_done = True
                return 1

            tsm4_powered_off = True
            log_step(f"Loop {loop}: wait TSM4 lost-connect handling, wait={cfg.CASE13_TSM4_POWER_OFF_WAIT}s")
            log_progress(f"[CASE13] 等待 TSM4 lost-connect handling {cfg.CASE13_TSM4_POWER_OFF_WAIT} 秒...")
            receive_monitor(cfg.CASE13_TSM4_POWER_OFF_WAIT)

            check_start = time.time()
            pass_ok, reason = check_bh_random_ssid(args)
            duration = round(time.time() - check_start + cfg.CASE13_TSM4_POWER_OFF_WAIT, 2)

            if not pass_ok:
                log_result(f"Loop {loop}: Case13 FAIL, {reason}")
                log_progress(f"[CASE13][FAIL] {reason}")
                write_summary(loop_str, "ETH BH", f"{duration}s", "FAIL", reason)
                case_failed = True
                write_recovery_note("ETH BH", "Recovery(check_RE_status_only_no_factory_workaround)")
                # Restore TSM4 before diagnostic so check_RE_status has a better chance to collect useful data.
                power_tsm4_on(cfg.CASE13_TSM4_POWER_RESTORE_WAIT)
                tsm4_powered_off = False
                safe_handle_fail_recovery(f"Loop{loop}_{cfg.CASE_ID}_BH_Random_SSID_Fail")
                recovery_done = True
                return 1

            log_result(f"Loop {loop}: Case13 PASS, {reason}, duration={duration}s")
            log_progress(f"[CASE13][PASS] {reason}")
            write_summary(loop_str, "ETH BH", f"{duration}s", "PASS", reason)

            power_tsm4_on(cfg.CASE13_TSM4_POWER_RESTORE_WAIT)
            tsm4_powered_off = False

        restore_eth_backhaul("Case13 測試結束")
        log_result(f"{cfg.TEST_CASE_NAME}: PASS")
        log_separator("Case13 所有測試迴圈執行完畢，結果 PASS")
        return 0

    except KeyboardInterrupt:
        log_result(f"{cfg.TEST_CASE_NAME}: interrupted by user")
        log_progress("使用者中斷測試。")
        case_failed = True
        return 130
    except Exception as e:
        log_result(f"{cfg.TEST_CASE_NAME}: FAIL, unexpected error {type(e).__name__}: {e}")
        log_progress(f"Case13 主程式發生未預期錯誤: {type(e).__name__}: {e}")
        case_failed = True
        return 1
    finally:
        if tsm4_powered_off:
            try:
                log_step("Case13 finally: TSM4 is still powered off, restore power")
                power_tsm4_on(cfg.CASE13_TSM4_POWER_RESTORE_WAIT)
            except Exception as e:
                log_progress(f"[CASE13][RESTORE] TSM4 power on failed in finally: {type(e).__name__}: {e}")
        if not recovery_done:
            try:
                log_step("Case13 finally: restore ETH BH cleanup")
                restore_eth_backhaul("Case13 finally cleanup" if case_failed else "Case13 PASS cleanup")
            except Exception:
                pass
        stop_background_serial_logger(close_serial=True)


if __name__ == "__main__":
    args = parse_args()
    apply_args(args)
    raise SystemExit(run_case13(args))
