#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
case14_tsm4_wps_button_re_wps_onboarding_modular.py

Case14: TSM4 WPS 5GHz Push Button + RE WPS onboarding check.

Flow:
  1. Send RE factory default command through Booster/RE serial console.
  2. Wait 20s.
  3. Switch relay 6 off to WiFi BH.
  4. Wait 150s for RE factory-default reboot/system stable.
  5. Login TSM4 Web GUI and press WPS 5GHz Push Button.
  6. Wait 3s.
  7. Send RE WPS PBC command through serial console.
  8. Wait/poll onboarding using the same onboarding check as Case1~Case9.
  9. Always restore relay 6 on before exit.

This case has WiFi BH only. There is no ETH BH onboarding stage.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from testlib import config as cfg
from testlib.env_info import create_chrome_driver, get_environment_fw_versions_close_browser
from testlib.logger import (
    init_log_filenames,
    init_summary_log,
    log_details,
    log_progress,
    log_result,
    log_separator,
    log_step,
    write_recovery_note,
    write_summary,
)
from testlib.onboarding import run_polling_or_recover
from testlib.recovery import safe_handle_fail_recovery
from testlib.relay import control_relay, restore_eth_backhaul
from testlib.web_gui import save_gui_screenshot
from testlib.serial_console import (
    _SERIAL_IO_LOCK,
    get_serial_for_command,
    receive_monitor,
    start_background_serial_logger,
    stop_background_serial_logger,
)


DEFAULT_CASE_NAME = "case14_TSM4_WPS_RE_Onboarding"

DEFAULT_XPATH_WPS_TAB = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/app-top-menu/nav/div/ul/li[5]/a"
DEFAULT_XPATH_WPS_5G_TAB = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/app-top-menu/div[1]/nav/div/ul/li[2]/a"
DEFAULT_XPATH_WPS_5G_PUSH_BUTTON = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-wps/div/div/form/div[3]/div/div/div[1]/div/div/ol/li[2]/button/div"
DEFAULT_XPATH_WPS_5G_PUSH_BUTTON_FALLBACK = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-wps/div/div/form/div[3]/div/div/div[1]/div/div/ol/li[2]"
DEFAULT_XPATH_DISCARD_CHANGES_YES = "/html/body/ngb-modal-window/div/div/app-modal-discard-changes/div[3]/div/button[2]"


def cfg_get(name, default):
    return getattr(cfg, name, default)


def parse_args():
    parser = argparse.ArgumentParser(description="Case14 TSM4 WPS + RE WPS onboarding test")
    parser.add_argument("--loops", type=int, default=cfg_get("TOTAL_LOOPS", 1))
    parser.add_argument("--booster-port", default=cfg_get("BOOSTER_PORT", "COM4"))
    parser.add_argument("--relay-port", default=cfg_get("RELAY_PORT", "COM3"))

    parser.add_argument("--re-factory-default-cmd", default=cfg_get("CASE14_RE_FACTORY_DEFAULT_CMD", "factory_default"))
    parser.add_argument("--re-factory-default-post-wait", type=int, default=cfg_get("CASE14_RE_FACTORY_DEFAULT_POST_WAIT", 20))
    parser.add_argument("--wifi-bh-pre-wps-wait", type=int, default=cfg_get("CASE14_WIFI_BH_PRE_WPS_WAIT", 150))
    parser.add_argument("--after-tsm4-wps-wait", type=int, default=cfg_get("CASE14_AFTER_TSM4_WPS_WAIT", 3))
    parser.add_argument("--re-wps-cmd", default=cfg_get("CASE14_RE_WPS_CMD", "wpa_cli -p /var/run/wpa_supplicant-ath1 wps_pbc multi_ap=2"))
    parser.add_argument("--re-wps-cmd-2", default=cfg_get("CASE14_RE_WPS_CMD_2", "wpa_cli -p /var/run/wpa_supplicant-ath2 wps_pbc multi_ap=2"))
    parser.add_argument("--re-wps-read-time", type=float, default=cfg_get("CASE14_RE_WPS_READ_TIME", 3))

    parser.add_argument("--wps-onboarding-init-wait", type=int, default=cfg_get("CASE14_WPS_ONBOARDING_INIT_WAIT", 240))
    parser.add_argument("--case14-max-total-limit", type=int, default=cfg_get("CASE14_MAX_TOTAL_LIMIT", cfg_get("RESET_MAX_TOTAL_LIMIT", 960)))
    parser.add_argument("--threshold", type=int, default=cfg_get("CASE14_ONBOARDING_THRESHOLD", cfg_get("ONBOARDING_THRESHOLD", 3)))

    parser.add_argument("--wps-browser-close-wait", type=float, default=cfg_get("CASE14_WPS_BROWSER_CLOSE_WAIT", 5))
    parser.add_argument("--gui-wait-timeout", type=int, default=cfg_get("WAIT_TIMEOUT", 30))
    parser.add_argument("--xpath-wps-tab", default=cfg_get("XPATH_WPS_TAB", DEFAULT_XPATH_WPS_TAB))
    parser.add_argument("--xpath-wps-5g-tab", default=cfg_get("XPATH_WPS_5G_TAB", DEFAULT_XPATH_WPS_5G_TAB))
    parser.add_argument("--xpath-wps-5g-push-button", default=cfg_get("XPATH_WPS_5G_PUSH_BUTTON", DEFAULT_XPATH_WPS_5G_PUSH_BUTTON))
    parser.add_argument("--xpath-wps-5g-push-button-fallback", default=cfg_get("XPATH_WPS_5G_PUSH_BUTTON_FALLBACK", DEFAULT_XPATH_WPS_5G_PUSH_BUTTON_FALLBACK))
    parser.add_argument("--xpath-discard-changes-yes", default=cfg_get("XPATH_DISCARD_CHANGES_YES", cfg_get("XPATH_DISCARD_CLOSE_BTN", DEFAULT_XPATH_DISCARD_CHANGES_YES)))

    parser.add_argument("--check-re-status-script", default=cfg_get("CHECK_RE_STATUS_SCRIPT", "check_RE_status.py"))
    parser.add_argument("--check-re-status-com-port", default=None)
    parser.add_argument("--check-re-status-com-port-arg", default=cfg_get("CHECK_RE_STATUS_COM_PORT_ARG", ""))
    parser.add_argument("--enable-fail-reboot-recovery", action="store_true", default=cfg_get("FAIL_RECOVERY_REBOOT_ENABLE", False))
    return parser.parse_args()


def apply_args(args):
    cfg.TOTAL_LOOPS = args.loops
    cfg.BOOSTER_PORT = args.booster_port
    cfg.RELAY_PORT = args.relay_port

    cfg.CASE14_RE_FACTORY_DEFAULT_CMD = args.re_factory_default_cmd
    cfg.CASE14_RE_FACTORY_DEFAULT_POST_WAIT = args.re_factory_default_post_wait
    cfg.CASE14_WIFI_BH_PRE_WPS_WAIT = args.wifi_bh_pre_wps_wait
    cfg.CASE14_AFTER_TSM4_WPS_WAIT = args.after_tsm4_wps_wait
    cfg.CASE14_RE_WPS_CMD   = args.re_wps_cmd
    cfg.CASE14_RE_WPS_CMD_2 = args.re_wps_cmd_2
    cfg.CASE14_RE_WPS_READ_TIME = args.re_wps_read_time
    cfg.CASE14_WPS_ONBOARDING_INIT_WAIT = args.wps_onboarding_init_wait
    cfg.CASE14_MAX_TOTAL_LIMIT = args.case14_max_total_limit
    cfg.CASE14_ONBOARDING_THRESHOLD = args.threshold
    cfg.CASE14_WPS_BROWSER_CLOSE_WAIT = args.wps_browser_close_wait

    cfg.XPATH_WPS_TAB = args.xpath_wps_tab
    cfg.XPATH_WPS_5G_TAB = args.xpath_wps_5g_tab
    cfg.XPATH_WPS_5G_PUSH_BUTTON = args.xpath_wps_5g_push_button
    cfg.XPATH_WPS_5G_PUSH_BUTTON_FALLBACK = args.xpath_wps_5g_push_button_fallback
    cfg.XPATH_DISCARD_CHANGES_YES = args.xpath_discard_changes_yes

    cfg.CHECK_RE_STATUS_SCRIPT = args.check_re_status_script
    cfg.CHECK_RE_STATUS_COM_PORT = args.check_re_status_com_port or cfg.BOOSTER_PORT
    cfg.CHECK_RE_STATUS_COM_PORT_ARG = args.check_re_status_com_port_arg or ""
    cfg.FAIL_RECOVERY_REBOOT_ENABLE = bool(args.enable_fail_reboot_recovery)


def wait_loading_done(wait, timeout_note="loadingModal"):
    try:
        wait.until(EC.invisibility_of_element_located((By.ID, "loadingModal")))
    except Exception:
        log_progress(f"[CASE14][GUI] wait {timeout_note} timeout/not present; continue")


def js_click(driver, element, wait_after=1.0):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    receive_monitor(0.3)
    driver.execute_script("arguments[0].click();", element)
    if wait_after > 0:
        receive_monitor(wait_after)


def handle_discard_changes_if_present(driver, xpath_discard_yes, context=""):
    try:
        short_wait = WebDriverWait(driver, 3)
        yes_btn = short_wait.until(EC.element_to_be_clickable((By.XPATH, xpath_discard_yes)))
        suffix = f" ({context})" if context else ""
        log_step(f"Case14 GUI: discard changes modal detected{suffix}, click YES")
        driver.execute_script("arguments[0].click();", yes_btn)
        receive_monitor(1)
        log_result("Case14 GUI: discard changes confirmed")
        return True
    except Exception:
        return False


def login_if_needed(driver, wait, gateway_url):
    log_step(f"Case14 GUI: open Web GUI {gateway_url}")
    driver.get(gateway_url)
    receive_monitor(2)

    try:
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_WIFI_SETTINGS)))
        log_result("Case14 GUI: already logged in")
        return True
    except Exception:
        pass

    log_step("Case14 GUI: login Web GUI")
    user_input = wait.until(EC.element_to_be_clickable((By.XPATH, cfg.XPATH_LOGIN_USER)))
    user_input.clear()
    user_input.send_keys(cfg.ROUTER_USERNAME)
    receive_monitor(0.3)

    pass_input = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_LOGIN_PASS)))
    pass_input.clear()
    driver.execute_script("arguments[0].value = arguments[1];", pass_input, cfg.ROUTER_PASSWORD)
    driver.execute_script("arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", pass_input)
    driver.execute_script("arguments[0].dispatchEvent(new Event('change', { bubbles: true }));", pass_input)
    receive_monitor(0.5)

    submit_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
    driver.execute_script("arguments[0].click();", submit_btn)
    wait_loading_done(wait)
    receive_monitor(2)
    log_result("Case14 GUI: login submitted")
    return True


def press_tsm4_wps_5g(args):
    driver = None
    try:
        log_step("Case14: press TSM4 WPS 5GHz button")
        driver = create_chrome_driver()
        if driver is None:
            log_result("Case14 WPS GUI FAIL: Chrome driver create failed")
            return False, "Chrome driver create failed"

        wait = WebDriverWait(driver, int(args.gui_wait_timeout))
        login_if_needed(driver, wait, cfg.GATEWAY_URL)

        log_step("Case14 GUI: navigate to Wi-Fi Settings")
        wifi_link = wait.until(EC.element_to_be_clickable((By.XPATH, cfg.XPATH_WIFI_SETTINGS)))
        js_click(driver, wifi_link, wait_after=1)
        handle_discard_changes_if_present(driver, cfg.XPATH_DISCARD_CHANGES_YES, "after Wi-Fi Settings click")
        wait_loading_done(wait)
        receive_monitor(1)

        log_step("Case14 GUI: open WPS tab")
        wps_tab = wait.until(EC.element_to_be_clickable((By.XPATH, cfg.XPATH_WPS_TAB)))
        js_click(driver, wps_tab, wait_after=1)
        handle_discard_changes_if_present(driver, cfg.XPATH_DISCARD_CHANGES_YES, "after WPS tab click")
        wait_loading_done(wait)
        receive_monitor(1)

        log_step("Case14 GUI: select WPS 5GHz")
        wps_5g_tab = wait.until(EC.element_to_be_clickable((By.XPATH, cfg.XPATH_WPS_5G_TAB)))
        js_click(driver, wps_5g_tab, wait_after=1)
        handle_discard_changes_if_present(driver, cfg.XPATH_DISCARD_CHANGES_YES, "after WPS 5GHz tab click")
        wait_loading_done(wait)
        receive_monitor(1)

        log_step("Case14 GUI: click 5GHz WPS Push Button")
        try:
            wps_button = wait.until(EC.element_to_be_clickable((By.XPATH, cfg.XPATH_WPS_5G_PUSH_BUTTON)))
            js_click(driver, wps_button, wait_after=1)
        except Exception as first_e:
            log_progress(f"[CASE14][GUI] primary WPS click failed: {type(first_e).__name__}: {first_e}; try fallback")
            wps_button_fb = wait.until(EC.element_to_be_clickable((By.XPATH, cfg.XPATH_WPS_5G_PUSH_BUTTON_FALLBACK)))
            js_click(driver, wps_button_fb, wait_after=1)
        receive_monitor(2)
        save_gui_screenshot(driver, f"{cfg.TEST_CASE_NAME}_wps_5g_button_clicked")

        log_result("Case14 TSM4 WPS 5GHz PASS: push button submitted")
        close_wait = float(getattr(cfg, "CASE14_WPS_BROWSER_CLOSE_WAIT", 5) or 0)
        if close_wait > 0:
            log_step(f"Case14 GUI: keep browser open after WPS press, wait={close_wait}s")
            receive_monitor(close_wait)
        return True, "None"

    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        log_result(f"Case14 TSM4 WPS 5GHz FAIL: {reason}")
        return False, reason
    finally:
        if driver is not None:
            try:
                driver.quit()
                log_result("Case14 GUI: Chrome closed")
            except Exception:
                pass


def run_serial_command(command, read_time, label):
    ser = None
    close_after_use = False
    try:
        ser, close_after_use = get_serial_for_command()
        if ser is None:
            return False, "", "No serial handle"
        log_step(f"Case14 serial: {label}, command={command}")
        with _SERIAL_IO_LOCK:
            ser.write(b"\r\n")
            receive_monitor(0.5, ser)
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
            ser.write((str(command).strip() + "\n").encode("utf-8"))
            output = receive_monitor(float(read_time), ser)
        log_details(f"[CASE14][SERIAL][{label}] output begin")
        for line in (output or "").replace("\r", "").split("\n"):
            line = line.strip()
            if line:
                log_details(f"[CASE14][SERIAL] {line}")
        log_details(f"[CASE14][SERIAL][{label}] output end")
        log_result(f"Case14 serial command sent: {label}")
        return True, output or "", "None"
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        log_result(f"Case14 serial command FAIL: {label}, reason={reason}")
        return False, "", reason
    finally:
        if close_after_use and ser is not None:
            try:
                ser.close()
            except Exception:
                pass


def fail_and_recover(loop, reason, suffix, duration="N/A"):
    log_result(f"Case14 FAIL: {reason}")
    write_summary(str(loop), "WiFi BH", duration, "FAIL", reason)
    write_recovery_note("WiFi BH", "Recovery(check_RE_status_collect_diag_restore_ETH_BH)")
    safe_handle_fail_recovery(f"Loop{loop}_{cfg.CASE_ID}_{suffix}")


def run_case14(args):
    cfg.TEST_CASE_NAME = DEFAULT_CASE_NAME
    init_log_filenames()

    start_background_serial_logger()
    cleanup_done = False
    try:
        router_fw, booster_fw = get_environment_fw_versions_close_browser()
        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {cfg.TOTAL_LOOPS} Loops) - {cfg.TEST_CASE_NAME}")
        log_step(
            "Case14 policy: WiFi BH only; RE factory_default -> TSM4 WPS 5GHz -> RE WPS PBC -> onboarding check"
        )

        for loop in range(1, cfg.TOTAL_LOOPS + 1):
            log_separator(f"LOOP {loop} - Case14 WPS onboarding start")

            ok, _, reason = run_serial_command(
                cfg.CASE14_RE_FACTORY_DEFAULT_CMD,
                read_time=3,
                label="RE factory default",
            )
            if not ok:
                fail_and_recover(loop, f"RE factory default command failed: {reason}", "RE_Factory_Default_Cmd_Fail")
                cleanup_done = True
                return 1

            log_step(f"Loop {loop}: wait after RE factory_default, wait={cfg.CASE14_RE_FACTORY_DEFAULT_POST_WAIT}s")
            receive_monitor(cfg.CASE14_RE_FACTORY_DEFAULT_POST_WAIT)

            log_step(f"Loop {loop}: switch to WiFi BH, relay {cfg.RELAY_ETH_PORT} off")
            if not control_relay("off"):
                fail_and_recover(loop, "Relay switch to WiFi BH failed", "Relay_WiFi_BH_Fail")
                cleanup_done = True
                return 1
            receive_monitor(cfg.RELAY_SETTLE_TIME)

            log_step(f"Loop {loop}: wait RE factory-default boot/system stable, wait={cfg.CASE14_WIFI_BH_PRE_WPS_WAIT}s")
            receive_monitor(cfg.CASE14_WIFI_BH_PRE_WPS_WAIT)

            gui_ok, gui_reason = press_tsm4_wps_5g(args)
            if not gui_ok:
                fail_and_recover(loop, f"TSM4 WPS 5GHz GUI failed: {gui_reason}", "TSM4_WPS_GUI_Fail")
                cleanup_done = True
                return 1

            log_step(f"Loop {loop}: wait after TSM4 WPS press before RE WPS command, wait={cfg.CASE14_AFTER_TSM4_WPS_WAIT}s")
            receive_monitor(cfg.CASE14_AFTER_TSM4_WPS_WAIT)

            duration_start_time = time.time()
            ok, _, reason = run_serial_command(
                cfg.CASE14_RE_WPS_CMD,
                read_time=cfg.CASE14_RE_WPS_READ_TIME,
                label="RE WPS PBC ath1",
            )
            if not ok:
                fail_and_recover(loop, f"RE WPS PBC ath1 command failed: {reason}", "RE_WPS_Cmd_Fail")
                cleanup_done = True
                return 1

            ok, _, reason = run_serial_command(
                cfg.CASE14_RE_WPS_CMD_2,
                read_time=cfg.CASE14_RE_WPS_READ_TIME,
                label="RE WPS PBC ath2",
            )
            if not ok:
                fail_and_recover(loop, f"RE WPS PBC ath2 command failed: {reason}", "RE_WPS_Cmd_Fail")
                cleanup_done = True
                return 1

            if not run_polling_or_recover(
                loop,
                "WiFi BH",
                cfg.CASE14_WPS_ONBOARDING_INIT_WAIT,
                cfg.CASE14_ONBOARDING_THRESHOLD,
                "WPS_Onboarding_Fail",
                duration_start_time=duration_start_time,
                max_total_limit=cfg.CASE14_MAX_TOTAL_LIMIT,
            ):
                log_result(f"Loop {loop}: Case14 FAIL at WiFi BH WPS onboarding")
                cleanup_done = True
                return 1

            log_result(f"Loop {loop}: Case14 PASS")

        restore_eth_backhaul("Case14 PASS 結束")
        cleanup_done = True
        log_separator("Case14 所有測試迴圈執行完畢，結果 PASS")
        return 0

    except KeyboardInterrupt:
        log_result("Case14 interrupted by user")
        return 130
    except Exception as e:
        log_result(f"Case14 unexpected FAIL: {type(e).__name__}: {e}")
        return 1
    finally:
        if not cleanup_done:
            try:
                restore_eth_backhaul("Case14 finally cleanup")
            except Exception:
                pass
        stop_background_serial_logger(close_serial=True)


if __name__ == "__main__":
    cfg.TEST_CASE_NAME = DEFAULT_CASE_NAME
    args = parse_args()
    apply_args(args)
    raise SystemExit(run_case14(args))
