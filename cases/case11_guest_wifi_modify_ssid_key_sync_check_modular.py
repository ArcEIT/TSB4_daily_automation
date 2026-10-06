#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Case11 - Guest WiFi SSID/key modify and RE UCI sync check.

New modular architecture, legacy flow:
  ETH BH relay on  -> onboarding check -> GUI modify -> poll RE until synced (SSH UCI + live SSID)
  WiFi BH relay off -> onboarding check -> GUI modify -> poll RE until synced (SSH UCI + live SSID)

Important:
  - No four-dimensional onboarding polling.
  - Generic knobs are read from testlib.config.
  - Serial is used for full-session console logging and SSH host discovery only.
  - Sync check polls the RE over SSH (testlib.wifi_sync_check): UCI values + live SSID, stops at first match.
  - Before the GUI change, the RE must be onboarding done (state done + MAP Done=1), retry per PRE_ACTION_*.
  - At the end (PASS, FAIL or interrupt) the Guest WiFi is disabled again (CASE11_CLEANUP_DISABLE_GUEST_WIFI).
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
from testlib.ssh_client import discover_ssh_host_by_serial
from testlib.recovery import safe_handle_fail_recovery
from testlib.web_gui import save_gui_screenshot
from testlib.dut_health import wait_for_onboarding_if_recently_rebooted
from testlib import wifi_sync_check, gui_failure
from testlib.onboarding import wait_onboarding_done_with_retry
from cases._case_common import add_common_args, apply_common_args


def _cfg(name, default):
    return getattr(cfg, name, default)


def generate_random_value(prefix, total_random_len, special_chars=None):
    normal_chars = string.ascii_letters + string.digits
    if special_chars is None:
        special_chars = _cfg("CASE11_SPECIAL_CHARS", "!@#%^&*_-+=?")

    total_random_len = max(int(total_random_len), 2)
    if special_chars:
        random_part = [random.choice(normal_chars) for _ in range(total_random_len - 1)]
        random_part.append(random.choice(special_chars))
    else:
        random_part = [random.choice(normal_chars) for _ in range(total_random_len)]
    random.shuffle(random_part)
    return f"{prefix}-{''.join(random_part)}"


def generate_wifi_profile(prefix):
    ssid = generate_random_value(prefix, _cfg("CASE11_SSID_RANDOM_LEN", 8), special_chars="")
    key = generate_random_value(
        _cfg("CASE11_GUEST_WIFI_KEY_PREFIX", "K"),
        _cfg("CASE11_WIFI_KEY_RANDOM_LEN", 14),
        special_chars=_cfg("CASE11_KEY_SPECIAL_CHARS", "!@#%^&*_-+=?"),
    )

    if len(key) < 8 or len(key) > 63:
        raise ValueError(f"Generated WiFi key length invalid: {len(key)}")

    return ssid, key


def discover_case11_ssh_host():
    """Use config host first; otherwise discover RE br-lan IP via serial."""
    host = _cfg("ONBOARDING_SSH_HOST", None)
    if host:
        log_progress(f"[CASE11][SSH] 使用 config 指定 host: {host}")
        return host

    ser = None
    close_after_use = False
    try:
        ser, close_after_use = get_serial_for_command()
        host = discover_ssh_host_by_serial(ser, force=False, log_prefix="[CASE11][SSH]")
        if host:
            log_progress(f"[CASE11][SSH] RE host={host}")
        return host
    finally:
        if close_after_use and ser is not None:
            try:
                ser.close()
            except Exception:
                pass


def _sync_spec():
    return wifi_sync_check.SyncSpec(
        tag="CASE11",
        title="Guest WiFi",
        ssid_cmds=list(_cfg("CASE11_GUEST_SSID_UCI_CMDS", [])),
        key_cmds=list(_cfg("CASE11_GUEST_KEY_UCI_CMDS", [])),
        key_groups=_cfg("CASE11_KEY_UCI_GROUPS", None),
        key_mode=str(_cfg("CASE11_KEY_MATCH_MODE", "per_band_any")).lower(),
        ssh_timeout=int(_cfg("CASE11_SSH_UCI_TIMEOUT", 15)),
        live_net="lan1",
        live_mode=str(_cfg("CASE11_LIVE_SSID_CHECK_MODE", "fail")).lower(),
        live_min=int(_cfg("CASE11_LIVE_SSID_MIN_IFACES", 2)),
    )


def check_re_wifi_sync(expected_ssid, expected_key, interface_name, timeout):
    """Poll the RE over SSH until the new guest WiFi SSID/key are synced (UCI + live SSID) or timeout."""
    return wifi_sync_check.poll_until_synced(
        _sync_spec(),
        discover_case11_ssh_host,
        expected_ssid,
        expected_key,
        timeout,
        int(_cfg("CASE11_SYNC_POLL_INTERVAL", 10)),
        interface_name,
    )


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
    receive_monitor(float(_cfg("CASE11_GUI_FIELD_SCROLL_WAIT", 0.5)))
    driver.execute_script("arguments[0].click();", element)


def is_logged_in(driver):
    try:
        short_wait = WebDriverWait(driver, 5)
        short_wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_WIFI_SETTINGS)))
        return True
    except Exception:
        return False


def _safe_xpath_value(name, default):
    value = getattr(cfg, name, default)
    return value or default


def handle_discard_changes_modal(driver, note=""):
    """Click the TSM4 'Discard Changes' -> Yes modal if it appears.

    The absolute XPath is kept as first priority, but fallback text-based XPath
    is used because ngb-modal DOM depth changes between FW versions.
    """
    timeout = float(_cfg("CASE11_GUI_DISCARD_MODAL_WAIT", 5))
    locators = [
        _safe_xpath_value("XPATH_GUEST_WIFI_DISCARD_YES", ""),
        "//app-modal-discard-changes//button[normalize-space()='Yes']",
        "//app-modal-discard-changes//button[contains(normalize-space(.), 'Yes')]",
        "//ngb-modal-window//button[normalize-space()='Yes']",
        "//ngb-modal-window//button[contains(normalize-space(.), 'Yes')]",
    ]

    for xpath in [x for x in locators if x]:
        try:
            yes_btn = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            log_progress(f"偵測到 Discard Changes 視窗，點擊 Yes 繼續切頁 {note}".strip())
            driver.execute_script("arguments[0].click();", yes_btn)
            receive_monitor(float(_cfg("CASE11_GUI_DISCARD_MODAL_AFTER_CLICK_WAIT", 1.5)))
            return True
        except Exception:
            continue

    log_progress(f"未偵測到 Discard Changes 視窗 {note}".strip())
    return False


def get_guest_wifi_toggle_text(driver, toggle):
    """Read nearby text around Guest Enable Wireless toggle."""
    try:
        return driver.execute_script(
            """
            let e = arguments[0];
            for (let i = 0; i < 7 && e; i++) {
                if (e.innerText && (e.innerText.includes('On') || e.innerText.includes('Off'))) {
                    return e.innerText;
                }
                e = e.parentElement;
            }
            return arguments[0].innerText || '';
            """,
            toggle,
        )
    except Exception:
        return ""


def set_guest_wifi_enabled(driver, wait, enable=True):
    """Ensure Guest WiFi enable toggle is in the requested state. Returns True when the toggle was clicked."""
    xpath = getattr(cfg, "XPATH_GUEST_WIFI_ENABLE_TOGGLE", "")
    if not xpath:
        log_progress("Case11: XPATH_GUEST_WIFI_ENABLE_TOGGLE 未設定，略過 Guest Enable 檢查")
        return False

    desired_text = "On" if enable else "Off"
    opposite_text = "Off" if enable else "On"

    toggle = wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
    state_text = str(get_guest_wifi_toggle_text(driver, toggle))
    log_progress(f"Guest WiFi toggle 狀態文字: {state_text or '<unknown>'}")

    if opposite_text in state_text:
        log_progress(f"Guest WiFi 目前不是 {desired_text}，點擊 Enable Wireless toggle 切成 {desired_text}")
        js_click(driver, toggle)
        receive_monitor(float(_cfg("CASE11_GUI_TOGGLE_WAIT", 2)))
        save_gui_screenshot(driver, f"{cfg.TEST_CASE_NAME}_guest_wifi_toggle_after_click")
        return True

    log_progress(f"Guest WiFi 看起來已是 {desired_text} 或無法判讀狀態，略過 toggle")
    return False


def modify_wifi_by_gui(ssid, wifi_password):
    log_step(f"Web GUI action: modify guest WiFi SSID to {ssid}")
    driver = None
    step = "init"

    try:
        from testlib import env_info as env

        step = "create Chrome"
        driver = env.create_chrome_driver()
        if driver is None:
            return False, "Chrome create failed"

        wait = WebDriverWait(driver, cfg.WAIT_TIMEOUT)

        step = "open Web GUI"
        log_progress("開啟 Web GUI 頁面...")
        driver.get(cfg.GATEWAY_URL)
        receive_monitor(float(_cfg("CASE11_GUI_OPEN_WAIT", 2)))

        if not is_logged_in(driver):
            step = "login"
            log_progress("Web GUI 填入帳密執行認證...")
            user_input = wait.until(EC.element_to_be_clickable((By.XPATH, cfg.XPATH_LOGIN_USER)))
            user_input.clear()
            user_input.send_keys(cfg.ROUTER_USERNAME)

            pass_input = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_LOGIN_PASS)))
            pass_input.clear()
            js_set_input_value(driver, pass_input, cfg.ROUTER_PASSWORD)
            receive_monitor(float(_cfg("CASE11_GUI_AFTER_LOGIN_INPUT_WAIT", 0.5)))

            submit_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
            driver.execute_script("arguments[0].click();", submit_btn)
            wait_loading_done(wait)
            receive_monitor(float(_cfg("CASE11_GUI_AFTER_LOGIN_WAIT", 2)))

        step = "navigate to WiFi Settings"
        log_progress("導航至 WiFi Settings 頁面...")
        wifi_link = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_WIFI_SETTINGS)))
        js_click(driver, wifi_link)
        receive_monitor(float(_cfg("CASE11_GUI_WIFI_PAGE_WAIT", 10)))

        step = "open Guest WiFi tab"
        log_progress("切換至 Guest WiFi 頁籤...")
        guest_tab = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_GUEST_WIFI_TAB)))
        js_click(driver, guest_tab)

        # TSM4 may show "Discard Changes?" when leaving Basic WiFi page.
        # Must click Yes before waiting for Guest page elements.
        handle_discard_changes_modal(driver, note="after clicking Guest tab")
        receive_monitor(float(_cfg("CASE11_GUI_GUEST_PAGE_WAIT", 5)))

        step = "wait Guest SSID input"
        try:
            ssid_input = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_GUEST_WIFI_SSID_INPUT)))
        except Exception:
            # If the first Guest tab click was blocked by the modal timing, retry once.
            log_progress("Guest SSID input 尚未出現，重試 Guest tab click 並再次處理 Discard Changes 視窗")
            guest_tab = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_GUEST_WIFI_TAB)))
            js_click(driver, guest_tab)
            handle_discard_changes_modal(driver, note="after retry clicking Guest tab")
            ssid_input = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_GUEST_WIFI_SSID_INPUT)))

        step = "enable Guest WiFi toggle"
        set_guest_wifi_enabled(driver, wait, enable=True)

        step = "set Guest SSID"
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", ssid_input)
        receive_monitor(float(_cfg("CASE11_GUI_FIELD_SCROLL_WAIT", 0.5)))
        js_set_input_value(driver, ssid_input, ssid)

        step = "set Guest key"
        key_input = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_GUEST_WIFI_KEY_INPUT)))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", key_input)
        receive_monitor(float(_cfg("CASE11_GUI_FIELD_SCROLL_WAIT", 0.5)))
        js_set_input_value(driver, key_input, wifi_password)

        receive_monitor(float(_cfg("CASE11_GUI_BEFORE_APPLY_WAIT", 1.5)))

        step = "click Apply"
        log_progress("點擊 Apply 執行設定變更...")
        apply_btn = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_GUEST_WIFI_APPLY_BTN)))
        js_click(driver, apply_btn)
        receive_monitor(float(_cfg("CASE11_GUI_AFTER_APPLY_CLICK_WAIT", 1)))
        wait_loading_done(wait)
        receive_monitor(float(_cfg("CASE11_GUI_AFTER_APPLY_DONE_WAIT", 2)))
        save_gui_screenshot(driver, f"{cfg.TEST_CASE_NAME}_guest_wifi_after_apply")

        log_result("Web GUI action PASS: WiFi modification submitted")
        return True, "None"

    except Exception as e:
        reason = gui_failure.describe_gui_exception(e, step, cfg.WAIT_TIMEOUT)
        log_result(reason)
        log_progress(f"[GUI_FAIL] raw exception: {type(e).__name__}: {str(e)[:300]!r}")
        save_gui_screenshot(driver, f"{cfg.TEST_CASE_NAME}_gui_fail_{step}")
        return False, reason

    finally:
        if driver:
            receive_monitor(float(_cfg("CASE11_GUI_BEFORE_QUIT_WAIT", 3)))
            driver.quit()


def run_one_stage(loop_str, interface_name, ssid, key, monitor_time):
    log_separator(f"LOOP {loop_str} - {interface_name} guest WiFi modify + sync check")
    log_progress(f"Target SSID={ssid}, key=<hidden>")

    relay_state = "on" if interface_name == "ETH BH" else "off"
    if not control_relay(relay_state):
        return False, "Relay switch failed"

    receive_monitor(cfg.RELAY_SETTLE_TIME)

    if interface_name == "WiFi BH":
        pre_gui_wait = int(_cfg("CASE11_WIFI_BH_PRE_GUI_WAIT", 0))
        if pre_gui_wait > 0:
            log_progress(f"WiFi BH pre-GUI wait {pre_gui_wait}s：等待 RE 建立 WiFi BH 並取得 DHCP 後再變更 SSID")
            receive_monitor(pre_gui_wait)

    # Pre-GUI check: the RE must be onboarded (state done + MAP OnboardingDone=1) before the TSM4 setting is
    # changed. An RE that is not registered with the TSM4 controller can never receive the sync, so the later
    # UCI check would fail for a reason unrelated to the sync itself.
    if _cfg("PRE_ACTION_ONBOARDING_CHECK_ENABLE", False):
        log_progress(f"Case11 {interface_name}: 修改 WiFi 前，先確認 RE onboarding state = done 且 MAP Done = 1...")
        ready, detail = wait_onboarding_done_with_retry(f"Case11 {interface_name} Pre-GUI")
        if not ready:
            log_result(f"Case11 {interface_name}: RE 尚未 onboarding done ({detail})，未修改 WiFi 設定")
            return False, f"RE not onboarding done before modifying guest WiFi ({detail}) - WiFi setting not changed"

    gui_ok, gui_reason = gui_failure.run_with_retry(
        lambda: modify_wifi_by_gui(ssid, key),
        _cfg("CASE11_GUI_MAX_ATTEMPTS", 2),
        _cfg("CASE11_GUI_RETRY_WAIT", 30),
        f"Case11 {interface_name} GUI",
    )
    if not gui_ok:
        return False, gui_reason

    log_progress(f"GUI apply 完成，輪詢 RE UCI/live SSID 同步 (最多 {monitor_time}s)")
    return check_re_wifi_sync(ssid, key, interface_name, monitor_time)


def disable_guest_wifi_by_gui():
    """Cleanup: leave the TSM4 Guest WiFi disabled. Returns (ok, reason). Never raises.

    Case11 turns Guest on and changes its SSID/key; without this the Guest stays on for whatever runs next."""
    log_step("Case11 cleanup: disable TSM4 Guest WiFi")
    driver = None
    step = "init"
    try:
        from testlib import env_info as env

        step = "create Chrome"
        driver = env.create_chrome_driver()
        if driver is None:
            return False, "Chrome create failed"
        wait = WebDriverWait(driver, cfg.WAIT_TIMEOUT)

        step = "open Web GUI"
        driver.get(cfg.GATEWAY_URL)
        receive_monitor(float(_cfg("CASE11_GUI_OPEN_WAIT", 2)))

        if not is_logged_in(driver):
            step = "login"
            user_input = wait.until(EC.element_to_be_clickable((By.XPATH, cfg.XPATH_LOGIN_USER)))
            user_input.clear()
            user_input.send_keys(cfg.ROUTER_USERNAME)
            pass_input = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_LOGIN_PASS)))
            pass_input.clear()
            js_set_input_value(driver, pass_input, cfg.ROUTER_PASSWORD)
            receive_monitor(float(_cfg("CASE11_GUI_AFTER_LOGIN_INPUT_WAIT", 0.5)))
            submit_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
            driver.execute_script("arguments[0].click();", submit_btn)
            wait_loading_done(wait)
            receive_monitor(float(_cfg("CASE11_GUI_AFTER_LOGIN_WAIT", 2)))

        step = "navigate to WiFi Settings"
        wifi_link = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_WIFI_SETTINGS)))
        js_click(driver, wifi_link)
        receive_monitor(float(_cfg("CASE11_GUI_WIFI_PAGE_WAIT", 10)))

        step = "open Guest WiFi tab"
        guest_tab = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_GUEST_WIFI_TAB)))
        js_click(driver, guest_tab)
        handle_discard_changes_modal(driver, note="after clicking Guest tab (cleanup)")
        receive_monitor(float(_cfg("CASE11_GUI_GUEST_PAGE_WAIT", 5)))

        step = "disable Guest WiFi toggle"
        toggled = set_guest_wifi_enabled(driver, wait, enable=False)
        if toggled:
            step = "click Apply"
            apply_btn = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_GUEST_WIFI_APPLY_BTN)))
            js_click(driver, apply_btn)
            receive_monitor(float(_cfg("CASE11_GUI_AFTER_APPLY_CLICK_WAIT", 1)))
            wait_loading_done(wait)
            receive_monitor(float(_cfg("CASE11_GUI_AFTER_APPLY_DONE_WAIT", 2)))
            save_gui_screenshot(driver, f"{cfg.TEST_CASE_NAME}_cleanup_guest_wifi_disabled")
            log_result("Case11 cleanup PASS: Guest WiFi disabled")
        else:
            log_result("Case11 cleanup PASS: Guest WiFi was already disabled")
        return True, "None"

    except Exception as e:
        reason = gui_failure.describe_gui_exception(e, f"cleanup: {step}", cfg.WAIT_TIMEOUT)
        log_progress(f"[GUI_FAIL] cleanup raw exception: {type(e).__name__}: {str(e)[:300]!r}")
        save_gui_screenshot(driver, f"{cfg.TEST_CASE_NAME}_cleanup_gui_fail_{step}")
        return False, reason

    finally:
        if driver:
            try:
                receive_monitor(float(_cfg("CASE11_GUI_BEFORE_QUIT_WAIT", 3)))
                driver.quit()
            except Exception:
                pass


def cleanup_guest_wifi():
    """Run the Guest-disable cleanup (retry, best effort). A cleanup problem is logged but never changes the case result."""
    if not _cfg("CASE11_CLEANUP_DISABLE_GUEST_WIFI", False):
        return
    try:
        ok, reason = gui_failure.run_with_retry(
            disable_guest_wifi_by_gui,
            _cfg("CASE11_GUI_MAX_ATTEMPTS", 2),
            _cfg("CASE11_GUI_RETRY_WAIT", 30),
            "Case11 cleanup GUI",
        )
        if not ok:
            log_result(f"Case11 cleanup FAIL: Guest WiFi may still be enabled ({reason}); case result is not changed")
    except Exception as e:
        log_result(f"Case11 cleanup FAIL: {type(e).__name__}: {e}; case result is not changed")


def run_test():
    """Run the case, then (PASS, FAIL or interrupt) leave the Guest WiFi disabled.

    The cleanup runs after the case body so that on FAIL the fail diagnostic is collected first, with the Guest WiFi
    still in the state it had when the case failed."""
    rc = 1
    try:
        rc = _run_test_body()
    finally:
        cleanup_guest_wifi()
        # The cleanup adds many lines after the case result; repeat the result as the last PROGRESS line of the log.
        verdict = "PASS" if rc == 0 else ("INTERRUPTED" if rc == 130 else "FAIL")
        log_separator(f"Case11 最終結果: {verdict} (exit code {rc}; Guest WiFi 收尾已在上方完成)")
        log_result(f"{cfg.TEST_CASE_NAME}: FINAL RESULT {verdict}")
    return rc


def _run_test_body():
    try:
        router_fw, booster_fw = get_environment_fw_versions_close_browser()
        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {cfg.TOTAL_LOOPS} Loops) - {cfg.TEST_CASE_NAME}")

        if not wait_for_onboarding_if_recently_rebooted(log_prefix="[CASE11]"):
            log_progress("Case11 FAIL: DUT onboarding 未就緒 (可能前一個 case 觸發了 reboot)")
            write_summary("0", "Pre-check", "N/A", "FAIL", "DUT_Onboarding_Not_Ready_At_Start")
            return 1

        for loop in range(1, cfg.TOTAL_LOOPS + 1):
            eth_ssid, eth_key = generate_wifi_profile(_cfg("CASE11_ETH_GUEST_SSID_PREFIX", "ETHGUEST"))
            wifi_ssid, wifi_key = generate_wifi_profile(_cfg("CASE11_WIFI_GUEST_SSID_PREFIX", "WIFIGUEST"))

            eth_start = time.time()
            eth_ok, eth_reason = run_one_stage(
                str(loop),
                "ETH BH",
                eth_ssid,
                eth_key,
                int(_cfg("CASE11_ETH_AFTER_GUI_APPLY_MONITOR_TIME", 30)),
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
                safe_handle_fail_recovery(f"Loop{loop}_case11_ETH_BH_Fail")
                return 1

            log_progress(f"LOOP {loop} ETH BH PASS，cooldown {cfg.PASS_COOLDOWN_TIME}s 後執行 WiFi BH")
            receive_monitor(cfg.PASS_COOLDOWN_TIME)

            wifi_start = time.time()
            wifi_ok, wifi_reason = run_one_stage(
                str(loop),
                "WiFi BH",
                wifi_ssid,
                wifi_key,
                int(_cfg("CASE11_WIFI_AFTER_GUI_APPLY_MONITOR_TIME", 120)),
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
                safe_handle_fail_recovery(f"Loop{loop}_case11_WiFi_BH_Fail")
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
    parser = argparse.ArgumentParser(description="Modular Case11 Guest WiFi SSID/key sync check")
    add_common_args(parser)
    return parser.parse_args()


if __name__ == "__main__":
    cfg.TEST_CASE_NAME = "case11_Guest_WiFi_Random_SSID_Key_Sync_SpecialChar"
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
