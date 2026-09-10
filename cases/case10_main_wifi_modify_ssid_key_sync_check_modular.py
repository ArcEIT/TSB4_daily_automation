#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Case10 - Main WiFi SSID/key modify and RE UCI sync check.

New modular architecture, legacy flow:
  ETH BH relay on  -> GUI modify -> fixed monitor wait -> SSH UCI check
  WiFi BH relay off -> GUI modify -> fixed monitor wait -> SSH UCI check

Important:
  - No four-dimensional onboarding polling.
  - Generic knobs are read from testlib.config.
  - Serial is used for full-session console logging and SSH host discovery only.
  - UCI value check is done by SSH in one bundled command.
"""

import argparse
import os
import random
import string
import sys
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from testlib import config as cfg
from testlib.logger import (
    init_log_filenames,
    init_summary_log,
    log_progress,
    log_step,
    log_result,
    log_separator,
    write_summary,
    summary_loop_display,
)
from testlib.env_info import get_environment_fw_versions_close_browser
from testlib.relay import control_relay, restore_eth_backhaul, restore_eth_backhaul_between_loops
from testlib.serial_console import (
    receive_monitor,
    start_background_serial_logger,
    stop_background_serial_logger,
    get_serial_for_command,
)
from testlib.ssh_client import run_ssh_command, discover_ssh_host_by_serial
from testlib.recovery import safe_handle_fail_recovery
from testlib.web_gui import save_gui_screenshot
from testlib.dut_health import wait_for_onboarding_if_recently_rebooted, monitored_wait
from cases._case_common import add_common_args, apply_common_args


UCI_BEGIN_MARKER = "__ARC_CASE10_UCI_BEGIN__"
UCI_END_MARKER = "__ARC_CASE10_UCI_END__"
UCI_ITEM_PREFIX = "__ARC_CASE10_UCI_ITEM__"


def _cfg(name, default):
    return getattr(cfg, name, default)


def generate_random_value(prefix, total_random_len, special_chars=None):
    normal_chars = string.ascii_letters + string.digits
    if special_chars is None:
        special_chars = _cfg("CASE10_SPECIAL_CHARS", "!@#%^&*_-+=?")

    total_random_len = max(int(total_random_len), 2)
    if special_chars:
        random_part = [random.choice(normal_chars) for _ in range(total_random_len - 1)]
        random_part.append(random.choice(special_chars))
    else:
        random_part = [random.choice(normal_chars) for _ in range(total_random_len)]
    random.shuffle(random_part)
    return f"{prefix}-{''.join(random_part)}"


def generate_wifi_profile(prefix):
    ssid = generate_random_value(prefix, _cfg("CASE10_SSID_RANDOM_LEN", 8), special_chars="")
    key = generate_random_value(
        _cfg("CASE10_WIFI_KEY_PREFIX", "K"),
        _cfg("CASE10_WIFI_KEY_RANDOM_LEN", 14),
        special_chars=_cfg("CASE10_KEY_SPECIAL_CHARS", "!@#%^&*_-+=?"),
    )

    if len(key) < 8 or len(key) > 63:
        raise ValueError(f"Generated WiFi key length invalid: {len(key)}")

    return ssid, key


def _shell_sq(text):
    """Single-quote text for POSIX shell."""
    return "'" + str(text).replace("'", "'\"'\"'") + "'"


def _all_case10_uci_entries():
    entries = []
    for cmd in _cfg("CASE10_SSID_UCI_CMDS", []):
        entries.append(("SSID", cmd))
    for cmd in _cfg("CASE10_KEY_UCI_CMDS", []):
        entries.append(("KEY", cmd))
    return entries


def build_case10_uci_bundle_cmd():
    """Build one SSH command that reads all Case10 UCI values."""
    parts = [f"echo {UCI_BEGIN_MARKER}"]

    for item_type, cmd in _all_case10_uci_entries():
        label = f"{item_type}|{cmd}"
        parts.append(
            "printf " + _shell_sq(f"{UCI_ITEM_PREFIX}|{label}|")
            + "; " + f"{cmd} 2>/dev/null || true"
            + "; echo"
        )

    parts.append(f"echo {UCI_END_MARKER}")
    return "; ".join(parts)


def parse_uci_bundle_output(output):
    """Return {(item_type, cmd): value} parsed from the SSH bundle output."""
    result = {}
    collecting = False

    for raw_line in (output or "").replace("\r", "").split("\n"):
        line = raw_line.strip()

        if line == UCI_BEGIN_MARKER:
            collecting = True
            continue

        if line == UCI_END_MARKER:
            break

        if not collecting or not line.startswith(UCI_ITEM_PREFIX + "|"):
            continue

        parts = line.split("|", 3)
        if len(parts) != 4:
            continue

        _, item_type, cmd, value = parts
        result[(item_type, cmd)] = value.strip()

    return result


def discover_case10_ssh_host():
    """Use config host first; otherwise discover RE br-lan IP via serial."""
    host = _cfg("ONBOARDING_SSH_HOST", None)
    if host:
        log_progress(f"[CASE10][SSH] 使用 config 指定 host: {host}")
        return host

    ser = None
    close_after_use = False
    try:
        ser, close_after_use = get_serial_for_command()
        host = discover_ssh_host_by_serial(ser, force=False, log_prefix="[CASE10][SSH]")
        if host:
            log_progress(f"[CASE10][SSH] RE host={host}")
        return host
    finally:
        if close_after_use and ser is not None:
            try:
                ser.close()
            except Exception:
                pass


def _validate_ssid_values(values, expected_ssid):
    failures = []
    for cmd in _cfg("CASE10_SSID_UCI_CMDS", []):
        actual = values.get(("SSID", cmd), "")
        log_progress(f"[CASE10][CHECK][SSID] {cmd} => {actual or '<empty>'}")
        if actual != expected_ssid:
            failures.append(f"{cmd}: expected='{expected_ssid}', actual='{actual or '<empty>'}'")
    return failures


def _default_key_groups():
    cmds = list(_cfg("CASE10_KEY_UCI_CMDS", []))
    if len(cmds) >= 4:
        return [cmds[:2], cmds[2:4]]
    return [[cmd] for cmd in cmds]


def _validate_key_values(values, expected_key):
    """Validate key UCI values.

    Default mode is per_band_any:
      - CASE10_KEY_UCI_CMDS[0:2] are treated as 2.4G key candidates.
      - CASE10_KEY_UCI_CMDS[2:4] are treated as 5G key candidates.
      - At least one key candidate per band must match expected_key.

    Set CASE10_KEY_MATCH_MODE="all" in config.py if every key command must match.
    """
    failures = []
    mode = str(_cfg("CASE10_KEY_MATCH_MODE", "per_band_any")).lower()
    key_cmds = list(_cfg("CASE10_KEY_UCI_CMDS", []))

    for cmd in key_cmds:
        actual = values.get(("KEY", cmd), "")
        match_text = "<match>" if actual == expected_key else "<mismatch/empty>"
        log_progress(f"[CASE10][CHECK][KEY] {cmd} => {match_text}")

    if mode == "all":
        for cmd in key_cmds:
            actual = values.get(("KEY", cmd), "")
            if actual != expected_key:
                failures.append(f"{cmd}: expected='<hidden>', actual='<hidden or empty>'")
        return failures

    groups = _cfg("CASE10_KEY_UCI_GROUPS", None) or _default_key_groups()
    for idx, group in enumerate(groups, start=1):
        if not group:
            continue
        if not any(values.get(("KEY", cmd), "") == expected_key for cmd in group):
            failures.append(f"KEY group {idx}: no matching key UCI among {group}")

    return failures


def check_re_wifi_sync(expected_ssid, expected_key):
    """Check RE Main WiFi UCI values via SSH after GUI apply monitor wait."""
    host = discover_case10_ssh_host()
    if not host:
        return False, "RE SSH host discover failed"

    log_step(f"Case10: check RE Main WiFi UCI sync via SSH ({host})")
    timeout = int(_cfg("CASE10_SSH_UCI_TIMEOUT", 15))
    ok, output, reason = run_ssh_command(host, build_case10_uci_bundle_cmd(), timeout=timeout)
    if not ok:
        return False, f"SSH UCI check failed: {reason}"

    values = parse_uci_bundle_output(output)
    failures = []
    failures.extend(_validate_ssid_values(values, expected_ssid))
    failures.extend(_validate_key_values(values, expected_key))

    if failures:
        log_result("Case10: RE Main WiFi UCI sync FAIL")
        for failure in failures:
            log_progress(f"[CASE10][CHECK][FAIL] {failure}")
        return False, "; ".join(failures)

    log_result("Case10: RE Main WiFi UCI sync PASS")
    return True, "None"


def wait_loading_done(wait, timeout_note="loadingModal"):
    try:
        wait.until(EC.invisibility_of_element_located((By.ID, "loadingModal")))
    except Exception:
        log_progress(f"等待 {timeout_note} 消失逾時或未出現，繼續流程")


def js_set_input_value(driver, element, text):
    driver.execute_script(
        """
        const input = arguments[0];
        const text = arguments[1];
        input.focus();
        input.value = text;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        input.blur();
        """,
        element,
        text,
    )


def js_click(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    receive_monitor(float(_cfg("CASE10_GUI_FIELD_SCROLL_WAIT", 0.5)))
    driver.execute_script("arguments[0].click();", element)


def is_logged_in(driver):
    try:
        short_wait = WebDriverWait(driver, 5)
        short_wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_WIFI_SETTINGS)))
        return True
    except Exception:
        return False


def modify_wifi_by_gui(ssid, wifi_password):
    log_step(f"Web GUI action: modify main WiFi SSID to {ssid}")
    driver = None

    try:
        from testlib import env_info as env

        driver = env.create_chrome_driver()
        if driver is None:
            return False, "Chrome create failed"

        wait = WebDriverWait(driver, cfg.WAIT_TIMEOUT)

        log_progress("開啟 Web GUI 頁面...")
        driver.get(cfg.GATEWAY_URL)
        receive_monitor(float(_cfg("CASE10_GUI_OPEN_WAIT", 2)))

        if not is_logged_in(driver):
            log_progress("Web GUI 填入帳密執行認證...")
            user_input = wait.until(EC.element_to_be_clickable((By.XPATH, cfg.XPATH_LOGIN_USER)))
            user_input.clear()
            user_input.send_keys(cfg.ROUTER_USERNAME)

            pass_input = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_LOGIN_PASS)))
            pass_input.clear()
            js_set_input_value(driver, pass_input, cfg.ROUTER_PASSWORD)
            receive_monitor(float(_cfg("CASE10_GUI_AFTER_LOGIN_INPUT_WAIT", 0.5)))

            submit_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
            driver.execute_script("arguments[0].click();", submit_btn)
            wait_loading_done(wait)
            receive_monitor(float(_cfg("CASE10_GUI_AFTER_LOGIN_WAIT", 2)))

        log_progress("導航至 WiFi Settings 基礎配置頁面...")
        wifi_link = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_WIFI_SETTINGS)))
        js_click(driver, wifi_link)
        receive_monitor(float(_cfg("CASE10_GUI_WIFI_PAGE_WAIT", 10)))

        ssid_input = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_MAIN_WIFI_SSID_INPUT)))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", ssid_input)
        receive_monitor(float(_cfg("CASE10_GUI_FIELD_SCROLL_WAIT", 0.5)))
        js_set_input_value(driver, ssid_input, ssid)

        key_input = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_MAIN_WIFI_KEY_INPUT)))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", key_input)
        receive_monitor(float(_cfg("CASE10_GUI_FIELD_SCROLL_WAIT", 0.5)))
        js_set_input_value(driver, key_input, wifi_password)

        receive_monitor(float(_cfg("CASE10_GUI_BEFORE_APPLY_WAIT", 1.5)))

        log_progress("點擊 Apply 執行設定變更...")
        apply_btn = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_WIFI_BASIC_APPLY)))
        js_click(driver, apply_btn)
        receive_monitor(float(_cfg("CASE10_GUI_AFTER_APPLY_CLICK_WAIT", 1)))
        wait_loading_done(wait)
        receive_monitor(float(_cfg("CASE10_GUI_AFTER_APPLY_DONE_WAIT", 2)))
        save_gui_screenshot(driver, f"{cfg.TEST_CASE_NAME}_main_wifi_after_apply")

        log_result("Web GUI action PASS: WiFi modification submitted")
        return True, "None"

    except Exception as e:
        reason = f"Web GUI action FAIL: {type(e).__name__}: {e}"
        log_result(reason)
        return False, reason

    finally:
        if driver:
            receive_monitor(float(_cfg("CASE10_GUI_BEFORE_QUIT_WAIT", 3)))
            driver.quit()


def run_one_stage(loop_str, interface_name, ssid, key, monitor_time):
    log_separator(f"LOOP {loop_str} - {interface_name} main WiFi modify + sync check")
    log_progress(f"Target SSID={ssid}, key=<hidden>")

    relay_state = "on" if interface_name == "ETH BH" else "off"
    if not control_relay(relay_state):
        return False, "Relay switch failed"

    receive_monitor(cfg.RELAY_SETTLE_TIME)

    if interface_name == "WiFi BH":
        pre_gui_wait = int(_cfg("CASE10_WIFI_BH_PRE_GUI_WAIT", 0))
        if pre_gui_wait > 0:
            log_progress(f"WiFi BH pre-GUI wait {pre_gui_wait}s：等待 RE 建立 WiFi BH 並取得 DHCP 後再變更 SSID")
            receive_monitor(pre_gui_wait)

    gui_ok, gui_reason = modify_wifi_by_gui(ssid, key)
    if not gui_ok:
        return False, gui_reason

    log_progress(f"GUI apply 完成，monitored_wait {monitor_time}s 等待 RE UCI 同步")
    if not monitored_wait(monitor_time, f"Case10 {interface_name} GUI apply sync", check_interval=30):
        return False, f"DUT_Unexpected_Reboot_During_{interface_name.replace(' ', '_')}_Monitor_Wait"

    return check_re_wifi_sync(ssid, key)


def run_test():
    try:
        router_fw, booster_fw = get_environment_fw_versions_close_browser()
        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {cfg.TOTAL_LOOPS} Loops) - {cfg.TEST_CASE_NAME}")

        if not wait_for_onboarding_if_recently_rebooted(log_prefix="[CASE10]"):
            log_progress("Case10 FAIL: DUT onboarding 未就緒 (可能前一個 case 觸發了 reboot)")
            write_summary("0", "Pre-check", "N/A", "FAIL", "DUT_Onboarding_Not_Ready_At_Start")
            return 1

        for loop in range(1, cfg.TOTAL_LOOPS + 1):
            eth_ssid, eth_key = generate_wifi_profile(_cfg("CASE10_ETH_SSID_PREFIX", "ETHSYNC"))
            wifi_ssid, wifi_key = generate_wifi_profile(_cfg("CASE10_WIFI_SSID_PREFIX", "WIFISYNC"))

            eth_start = time.time()
            eth_ok, eth_reason = run_one_stage(
                str(loop),
                "ETH BH",
                eth_ssid,
                eth_key,
                int(_cfg("CASE10_ETH_AFTER_GUI_APPLY_MONITOR_TIME", 30)),
            )
            eth_duration = round(time.time() - eth_start, 2)
            write_summary(
                summary_loop_display(str(loop), "ETH BH"),
                "ETH BH",
                f"{eth_duration}s",
                "PASS" if eth_ok else "FAIL",
                eth_reason,
            )

            if not eth_ok:
                safe_handle_fail_recovery(f"Loop{loop}_case10_ETH_BH_Fail", restore_eth_bh=True)
                return 1

            log_progress(f"LOOP {loop} ETH BH PASS，cooldown {cfg.PASS_COOLDOWN_TIME}s 後執行 WiFi BH")
            receive_monitor(cfg.PASS_COOLDOWN_TIME)

            wifi_start = time.time()
            wifi_ok, wifi_reason = run_one_stage(
                str(loop),
                "WiFi BH",
                wifi_ssid,
                wifi_key,
                int(_cfg("CASE10_WIFI_AFTER_GUI_APPLY_MONITOR_TIME", 120)),
            )
            wifi_duration = round(time.time() - wifi_start, 2)
            write_summary(
                summary_loop_display(str(loop), "WiFi BH"),
                "WiFi BH",
                f"{wifi_duration}s",
                "PASS" if wifi_ok else "FAIL",
                wifi_reason,
            )

            if not wifi_ok:
                safe_handle_fail_recovery(f"Loop{loop}_case10_WiFi_BH_Fail", restore_eth_bh=True)
                return 1

            log_progress(f"LOOP {loop} PASS")
            if loop < cfg.TOTAL_LOOPS:
                restore_eth_backhaul_between_loops(loop)

        restore_eth_backhaul("測試 PASS 結束")
        log_separator("所有測試迴圈執行完畢，結果 PASS")
        return 0

    except KeyboardInterrupt:
        log_progress("使用者中斷測試。")
        restore_eth_backhaul("使用者中斷")
        return 130
    except Exception as e:
        log_result(f"主程式發生未預期錯誤: {type(e).__name__}: {e}")
        restore_eth_backhaul("主程式未預期錯誤")
        return 1


def parse_args():
    parser = argparse.ArgumentParser(description="Modular Case10 Main WiFi SSID/key sync check")
    add_common_args(parser)
    return parser.parse_args()


if __name__ == "__main__":
    cfg.TEST_CASE_NAME = "case10_Main_WiFi_Random_SSID_Key_Sync_SpecialChar"
    args = parse_args()
    apply_common_args(args)
    init_log_filenames()
    start_background_serial_logger()
    exit_code = 1
    try:
        exit_code = run_test()
    finally:
        stop_background_serial_logger()
    raise SystemExit(exit_code)
