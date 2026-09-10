#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Case12: TSM4 GUI Wireless Disable/Enable FH sync check (WiFi_inf enforce no-reboot version).

Purpose:
    Verify that TSM4 GUI Wireless enable/disable state is synced to Booster FH
    UCI state in both ETH BH and WiFi BH stages.

Flow per loop:
    ETH BH:
        relay 6 on -> wait -> GUI disable wireless -> wait -> SSH check FH disabled=1
        GUI enable wireless -> wait -> SSH check FH disabled=0
    WiFi BH:
        relay 6 off -> wait -> same disable/enable FH check

Fail policy:
    - ETH BH FAIL: do not run WiFi BH.
    - Case12 returns rc=1 on FAIL so the current runner can record failure.
    - Case12 runs common fail diagnostic/check_RE_status + ETH restore.
    - Runner-level factory-default/reset GW+RE workaround should not run for case12.
    - Case12 never sends RE/Booster reboot on FAIL.
    - PASS or FAIL: best-effort enable TSM4 Wireless, then restore ETH BH / fail diagnostic.
"""
import argparse
import os
import sys
import time
from typing import Optional, Tuple

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from testlib import config as cfg
from testlib.logger import init_log_filenames, log_progress, log_step, log_result, log_separator, write_summary, init_summary_log, write_recovery_note
from testlib.env_info import create_chrome_driver, get_environment_fw_versions_close_browser
from testlib.relay import control_relay, restore_eth_backhaul, restore_eth_backhaul_between_loops
from testlib.recovery import safe_handle_fail_recovery
from testlib.dut_health import wait_for_onboarding_if_recently_rebooted, monitored_wait
from testlib.serial_console import (
    receive_monitor,
    start_background_serial_logger,
    stop_background_serial_logger,
    get_serial_for_command,
    _SERIAL_IO_LOCK,
)
from testlib.ssh_client import discover_ssh_host_by_serial, run_ssh_command, clear_cached_ssh_host
from testlib.web_gui import wait_loading_done, _try_login_if_needed, _js_click, save_gui_screenshot


def cfg_get(name, default):
    """Read optional Case12 config while preserving user's existing config.py values."""
    return getattr(cfg, name, default)


# Keep Case12-specific values optional so users do not need to overwrite their tuned config.py.
CASE12_ETH_BH_INIT_WAIT_DEFAULT    = cfg_get("CASE12_ETH_BH_INIT_WAIT", 30)
CASE12_WIFI_BH_INIT_WAIT_DEFAULT   = cfg_get("CASE12_WIFI_BH_INIT_WAIT", 150)
CASE12_WIRELESS_SYNC_WAIT_DEFAULT  = cfg_get("CASE12_WIRELESS_SYNC_WAIT", 30)
CASE12_ENABLE_RECOVERY_WAIT_DEFAULT = cfg_get("CASE12_ENABLE_RECOVERY_WAIT", 30)
CASE12_GUI_RETRY_WAIT_DEFAULT      = cfg_get("CASE12_GUI_RETRY_WAIT", 30)
CASE12_GUI_MAX_ATTEMPTS_DEFAULT    = cfg_get("CASE12_GUI_MAX_ATTEMPTS", 2)

# UCI commands and XPath are owned by config.py.
FH_24G_CMD = cfg.CASE12_FH_24G_DISABLED_CMD
FH_5G_CMD = cfg.CASE12_FH_5G_DISABLED_CMD
XPATH_WIRELESS_ENABLE_TOGGLE = cfg.CASE12_XPATH_WIRELESS_ENABLE_TOGGLE
XPATH_WIFI_BASIC_APPLY = cfg.CASE12_XPATH_WIFI_BASIC_APPLY


def parse_args():
    parser = argparse.ArgumentParser(description="Case12 TSM4 Wireless Disable/Enable FH sync check")
    parser.add_argument("--loops", type=int, default=cfg.TOTAL_LOOPS)
    parser.add_argument("--booster-port", default=cfg.BOOSTER_PORT)
    parser.add_argument("--relay-port", default=cfg.RELAY_PORT)
    parser.add_argument("--eth-bh-wait", type=int, default=CASE12_ETH_BH_INIT_WAIT_DEFAULT)
    parser.add_argument("--wifi-bh-wait", type=int, default=CASE12_WIFI_BH_INIT_WAIT_DEFAULT)
    parser.add_argument("--sync-wait", type=int, default=CASE12_WIRELESS_SYNC_WAIT_DEFAULT)
    parser.add_argument("--loop-eth-restore-wait", type=int, default=cfg_get("LOOP_ETH_RESTORE_WAIT", 60))
    parser.add_argument("--booster-host", default=cfg_get("ONBOARDING_SSH_HOST", None), help="Booster/RE SSH IP. Default: auto-discover by serial br-lan.")
    parser.add_argument("--ssh-timeout", type=int, default=cfg_get("ONBOARDING_SSH_TIMEOUT", 10))
    parser.add_argument("--enable-recovery-wait", type=int, default=CASE12_ENABLE_RECOVERY_WAIT_DEFAULT, help="Wait after best-effort GUI wireless enable on FAIL path.")
    parser.add_argument("--gui-retry-wait", type=int, default=CASE12_GUI_RETRY_WAIT_DEFAULT, help="GUI abnormal/crash retry wait seconds.")
    parser.add_argument("--gui-max-attempts", type=int, default=CASE12_GUI_MAX_ATTEMPTS_DEFAULT, help="GUI max attempts. Default 2 = first try + one retry.")
    return parser.parse_args()


def apply_args(args):
    cfg.TOTAL_LOOPS = args.loops
    cfg.BOOSTER_PORT = args.booster_port
    cfg.RELAY_PORT = args.relay_port
    cfg.CASE12_ETH_BH_INIT_WAIT = args.eth_bh_wait
    cfg.CASE12_WIFI_BH_INIT_WAIT = args.wifi_bh_wait
    cfg.CASE12_WIRELESS_SYNC_WAIT = args.sync_wait
    cfg.LOOP_ETH_RESTORE_WAIT = args.loop_eth_restore_wait
    cfg.ONBOARDING_SSH_HOST = args.booster_host
    cfg.ONBOARDING_SSH_TIMEOUT = args.ssh_timeout
    cfg.CASE12_ENABLE_RECOVERY_WAIT = args.enable_recovery_wait
    cfg.CASE12_GUI_RETRY_WAIT = args.gui_retry_wait
    cfg.CASE12_GUI_MAX_ATTEMPTS = args.gui_max_attempts


def get_booster_host() -> Optional[str]:
    host = cfg.ONBOARDING_SSH_HOST or discover_ssh_host_by_serial(log_prefix="[CASE12][SSH]")
    if host:
        return host
    log_progress("[CASE12][SSH] 無法取得 Booster SSH IP")
    return None


_SERIAL_CMD_BEGIN = "__C12_CMD_BEGIN__"
_SERIAL_CMD_END   = "__C12_CMD_END__"


def serial_get_trim(command: str, read_time: int = 2) -> Tuple[bool, str, Optional[str]]:
    """Run a shell command on Booster via serial (COM port) and return the trimmed last line.

    Uses begin/end markers so the output is cleanly extracted even when the
    background serial logger is running.
    """
    log_progress(f"[CASE12][SERIAL CMD] {command}")
    try:
        ser, close_after = get_serial_for_command()
        if ser is None:
            return False, "", "serial port not available"
        cmd_text = f"echo {_SERIAL_CMD_BEGIN}; {command}; echo {_SERIAL_CMD_END}\n"
        try:
            with _SERIAL_IO_LOCK:
                ser.write(b"\r\n")
                receive_monitor(0.5, ser)
                try:
                    ser.reset_input_buffer()
                except Exception:
                    pass
                ser.write(cmd_text.encode("utf-8"))
                output = receive_monitor(read_time, ser)
        finally:
            if close_after:
                try:
                    ser.close()
                except Exception:
                    pass
        text = output.replace("\r", "")
        # Use rfind to skip the command echo which also contains the markers.
        # Serial console echoes the full command line, so markers appear twice:
        # once in the echo, once in the actual output. rfind finds the real pair.
        end_pos = text.rfind(_SERIAL_CMD_END)
        if end_pos >= 0:
            begin_pos = text.rfind(_SERIAL_CMD_BEGIN, 0, end_pos)
            if begin_pos >= 0:
                body = text[begin_pos + len(_SERIAL_CMD_BEGIN):end_pos].strip()
            else:
                body = text[:end_pos].strip()
        else:
            body = text.strip()
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        value = lines[-1] if lines else ""
        return True, value, None
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}"


def serial_get_output(command: str, read_time: int = 5) -> Tuple[bool, str, Optional[str]]:
    """Run a shell command on Booster via serial and return the full trimmed body (multi-line)."""
    log_progress(f"[CASE12][SERIAL CMD] {command}")
    try:
        ser, close_after = get_serial_for_command()
        if ser is None:
            return False, "", "serial port not available"
        cmd_text = f"echo {_SERIAL_CMD_BEGIN}; {command}; echo {_SERIAL_CMD_END}\n"
        try:
            with _SERIAL_IO_LOCK:
                ser.write(b"\r\n")
                receive_monitor(0.5, ser)
                try:
                    ser.reset_input_buffer()
                except Exception:
                    pass
                ser.write(cmd_text.encode("utf-8"))
                output = receive_monitor(read_time, ser)
        finally:
            if close_after:
                try:
                    ser.close()
                except Exception:
                    pass
        text = output.replace("\r", "")
        end_pos = text.rfind(_SERIAL_CMD_END)
        if end_pos >= 0:
            begin_pos = text.rfind(_SERIAL_CMD_BEGIN, 0, end_pos)
            if begin_pos >= 0:
                body = text[begin_pos + len(_SERIAL_CMD_BEGIN):end_pos].strip()
            else:
                body = text[:end_pos].strip()
        else:
            body = text.strip()
        return True, body, None
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}"


def ssh_get_trim(host: str, command: str) -> Tuple[bool, str, Optional[str]]:
    log_progress(f"[CASE12][SSH CMD] {command}")
    ok, output, reason = run_ssh_command(host, command, timeout=cfg.ONBOARDING_SSH_TIMEOUT)
    if not ok:
        log_progress(f"[CASE12][SSH] SSH 失敗 ({reason})，fallback serial (COM port)")
        return serial_get_trim(command)
    lines = [line.strip() for line in output.replace("\r", "").splitlines() if line.strip()]
    value = lines[-1] if lines else ""
    return True, value, None


def read_booster_fh_disabled_state(host: str) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    ok24, val24, reason24 = ssh_get_trim(host, FH_24G_CMD)
    ok5, val5, reason5 = ssh_get_trim(host, FH_5G_CMD)
    if not ok24 or not ok5:
        return False, val24 if ok24 else None, val5 if ok5 else None, f"2.4G={reason24}; 5G={reason5}"
    return True, val24, val5, None


def check_fh_state(host: str, expected_disabled: str, label: str) -> Tuple[bool, str]:
    ok, val24, val5, reason = read_booster_fh_disabled_state(host)
    log_separator(f"{label} - SSH check Booster FH .disabled expected={expected_disabled}")
    if not ok:
        log_progress(f"FH check SSH 失敗: {reason}")
        return False, f"UCI SSH/serial 失敗({reason})"

    pass24 = val24 == expected_disabled
    pass5 = val5 == expected_disabled
    log_progress(f"2.4G FH: {FH_24G_CMD} -> {val24} ({'PASS' if pass24 else 'FAIL'})")
    log_progress(f"5G   FH: {FH_5G_CMD} -> {val5} ({'PASS' if pass5 else 'FAIL'})")
    if pass24 and pass5:
        return True, ""
    parts = []
    if not pass24:
        parts.append(f"2.4G={val24}(期望{expected_disabled})")
    if not pass5:
        parts.append(f"5G={val5}(期望{expected_disabled})")
    return False, "UCI: " + ", ".join(parts)


def _check_wifi_bh_bridge_note(host: str) -> str:
    """Check brctl show for mld1.10 (WiFi BH required bridge member).

    Returns a warning string if mld1.10 is missing or SSH fails, empty string if present.
    Only intended to be called in the WiFi BH fail path.
    """
    ok, output, reason = run_ssh_command(host, "brctl show", timeout=cfg.ONBOARDING_SSH_TIMEOUT)
    if not ok:
        return f"⚠ brctl show SSH失敗({reason})"
    if "mld1.10" not in output:
        return "⚠ brctl show: mld1.10 缺失 (WiFi BH link 可能已斷線)"
    return ""


def _poll_fh_sync(
    host: str,
    label: str,
    expected_disabled: str,
    expected_enabled: bool,
    max_total: int,
    poll_interval: int = 15,
) -> Tuple[bool, str]:
    """Poll UCI + WiFi_inf every poll_interval seconds until both pass or max_total expires.

    Returns (ok, fail_detail). Exits early the moment both checks pass — no need to
    wait the full max_total when sync happens quickly.
    """
    log_progress(f"[CASE12][SYNC POLL] 開始輪詢 {label}，max={max_total}s，間隔={poll_interval}s")
    start = time.time()
    last_fail_parts: list = []
    while True:
        elapsed = int(time.time() - start)
        wifi_ok, wifi_reason = check_wifi_inf_lan_ap_active_state(host, expected_enabled, label=f"{label} [{elapsed}s/{max_total}s]")
        if wifi_ok:
            log_progress(f"[CASE12][SYNC POLL] {elapsed}s 達成同步 — PASS")
            return True, ""
        last_fail_parts = [r for r in [wifi_reason] if r]
        log_progress(f"[CASE12][SYNC POLL {elapsed}s/{max_total}s] 未達成: {' | '.join(last_fail_parts)}")
        remaining = max_total - (time.time() - start)
        if remaining <= poll_interval * 0.5:
            break
        receive_monitor(min(poll_interval, remaining))
    log_progress(f"[CASE12][SYNC POLL] 超時 {max_total}s — FAIL")
    return False, " | ".join(last_fail_parts)


# -----------------------------------------------------------------------------
# Optional Case12 live beacon / active-state check via WiFi_inf_ChOnOff.sh
# -----------------------------------------------------------------------------
def parse_wifi_inf_chonoff_rows(output: str) -> dict:
    """Parse WiFi_inf_ChOnOff.sh rows using simple end-field anchoring.

    Target rows look like:
      idx dev mode mld ifnm mapBSS net ssid active hid dis disd ch rate

    The active column can be empty, so we do not rely on fixed split indexes
    after ssid. Instead, fields after ssid are parsed from the last 5 columns:
      hid dis disd ch rate
    and active is everything between ssid and hid.
    """
    rows = {}
    for raw in str(output or "").replace("\r", "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("idx ") or stripped.startswith("bridge "):
            continue
        parts = stripped.split()
        if len(parts) < 12 or not parts[0].isdigit():
            continue

        idx = parts[0]
        # Minimum stable layout before active:
        # 0 idx, 1 dev, 2 mode, 3 mld, 4 ifnm, 5 mapBSS, 6 net, 7 ssid
        ssid = parts[7]
        hid, dis, disd, ch, rate = parts[-5:]
        active = " ".join(parts[8:-5]).strip()

        rows[idx] = {
            "idx": idx,
            "dev": parts[1],
            "mode": parts[2],
            "mld": parts[3],
            "ifnm": parts[4],
            "mapBSS": parts[5],
            "net": parts[6],
            "ssid": ssid,
            "active": active,
            "hid": hid,
            "dis": dis,
            "disd": disd,
            "ch": ch,
            "rate": rate,
            "raw": raw,
        }
    return rows


def check_wifi_inf_lan_ap_active_state(host: str, expected_enabled: bool, label: str) -> Tuple[bool, str]:
    """Check idx 2/5 LAN AP active state from WiFi_inf_ChOnOff.sh.

    expected_enabled=True:
      idx 2 and 5 active must equal ssid and disd must be 0.
    expected_enabled=False:
      idx 2 and 5 active must be empty and disd must be 1.
    """
    if not bool(getattr(cfg, "CASE12_WIFI_INF_CHECK_ENABLE", True)):
        log_progress(f"[CASE12][WiFi_inf] check disabled by CASE12_WIFI_INF_CHECK_ENABLE=False")
        return True

    command = getattr(cfg, "CASE12_WIFI_INF_CHONOFF_CMD", "WiFi_inf_ChOnOff.sh")
    target_indexes = [str(x) for x in getattr(cfg, "CASE12_WIFI_INF_TARGET_INDEXES", ["2", "5"])]
    log_separator(f"{label} - SSH check WiFi_inf_ChOnOff.sh active expected_enabled={expected_enabled}")
    log_step(f"Case12 WiFi_inf check: {label}, expected_enabled={expected_enabled}, indexes={','.join(target_indexes)}")

    ok, output, reason = run_ssh_command(host, command, timeout=int(getattr(cfg, "ONBOARDING_SSH_TIMEOUT", 10)))
    if not ok:
        log_progress(f"[CASE12][WiFi_inf] SSH 失敗 ({reason})，fallback serial (COM port)")
        ok, output, reason = serial_get_output(command)
        if not ok:
            log_result(f"Case12 WiFi_inf check FAIL: SSH+serial 均失敗, reason={reason}")
            return False, f"WiFi_inf SSH+serial失敗({reason})"
        log_progress(f"[CASE12][WiFi_inf] serial fallback 成功，解析輸出")

    rows = parse_wifi_inf_chonoff_rows(output)
    failures = []
    for idx in target_indexes:
        row = rows.get(idx)
        if not row:
            failures.append(f"idx {idx} missing")
            continue

        ssid = row.get("ssid", "")
        active = row.get("active", "")
        disd = row.get("disd", "")
        raw = row.get("raw", "")
        log_progress(
            f"[CASE12][WiFi_inf] idx={idx}, ssid={ssid or '<empty>'}, "
            f"active={active or '<empty>'}, disd={disd}, raw={raw}"
        )

        if expected_enabled:
            # Enabled: real SSID, should NOT start with "FH"
            if ssid.startswith("FH"):
                failures.append(f"idx{idx} ssid={ssid}(期望非FH開頭/real SSID)")
        else:
            # Disabled: SSID changes to FH_xxx random, should start with "FH"
            if not ssid.startswith("FH"):
                failures.append(f"idx{idx} ssid={ssid or '<empty>'}(期望FH開頭)")

    if failures:
        for item in failures:
            log_progress(f"[CASE12][WiFi_inf][FAIL] {item}")
        log_result(f"Case12 WiFi_inf check FAIL: {label}")
        return False, "WiFi_inf: " + "; ".join(failures)

    log_result(f"Case12 WiFi_inf check PASS: {label}")
    return True, ""


def read_gui_toggle_state(driver, toggle_element) -> Optional[bool]:
    # Priority order:
    #   1. aria-checked attribute (most reliable for Angular toggle components)
    #   2. aria-pressed attribute
    #   3. input[type=checkbox].checked inside the element
    # Avoid scanning outerHTML / combined text because attribute names like
    # "aria-disabled" contain the word "disabled" and trigger false negatives.
    script = r"""
    const el = arguments[0];
    const checked = el.getAttribute('aria-checked');
    if (checked === 'true')  return true;
    if (checked === 'false') return false;
    const pressed = el.getAttribute('aria-pressed');
    if (pressed === 'true')  return true;
    if (pressed === 'false') return false;
    const input = el.querySelector('input[type="checkbox"]');
    if (input !== null) return !!input.checked;
    return null;
    """
    try:
        result = driver.execute_script(script, toggle_element)
        if isinstance(result, bool):
            return result
    except Exception:
        pass
    return None


def open_wifi_basic(driver, wait):
    _try_login_if_needed(driver, wait)
    log_progress("進入 WiFi Settings -> Basic")
    wifi_link = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_WIFI_SETTINGS)))
    _js_click(driver, wifi_link, wait_after=1)
    wait_loading_done(wait)
    receive_monitor(2)


def click_apply_if_available(driver, wait, action_label=""):
    try:
        apply_btn = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIFI_BASIC_APPLY)))
        log_progress("點擊 Apply 套用 Wireless 設定")
        _js_click(driver, apply_btn, wait_after=1)
        wait_loading_done(wait)
        receive_monitor(2)
        safe = action_label.replace(" ", "_") if action_label else "apply"
        save_gui_screenshot(driver, f"{cfg.TEST_CASE_NAME}_{safe}_after_apply")
    except Exception as e:
        log_progress(f"Apply button 未出現或不可點擊，繼續流程: {type(e).__name__}: {e}")


def set_tsm4_wireless(driver, wait, desired_enabled: bool, booster_host: str, action_label: str = "") -> bool:
    open_wifi_basic(driver, wait)
    toggle = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIRELESS_ENABLE_TOGGLE)))
    gui_state = read_gui_toggle_state(driver, toggle)

    if gui_state is None:
        ok, val24, val5, reason = read_booster_fh_disabled_state(booster_host)
        if ok and val24 in ("0", "1") and val5 in ("0", "1"):
            gui_state = val24 == "0" and val5 == "0"
            log_progress(f"GUI wireless toggle state 無法判斷，改用 Booster FH 狀態輔助: enabled={gui_state}")
        else:
            log_progress(f"GUI wireless toggle state 無法判斷，且 Booster FH 狀態讀取失敗: {reason}")

    log_progress(f"GUI wireless toggle current state: {'enabled' if gui_state else 'disabled' if gui_state is not None else 'unknown'}")

    if gui_state == desired_enabled:
        log_progress(f"GUI wireless 已是目標狀態: {'enabled' if desired_enabled else 'disabled'}，仍點 Apply 確認。")
        click_apply_if_available(driver, wait, action_label)
        return True

    log_progress(f"切換 TSM4 Wireless -> {'Enable' if desired_enabled else 'Disable'}")
    _js_click(driver, toggle, wait_after=1)

    # Verify the toggle actually flipped before clicking Apply.
    toggle_after = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_WIRELESS_ENABLE_TOGGLE)))
    new_state = read_gui_toggle_state(driver, toggle_after)
    receive_monitor(2)
    safe_label = action_label.replace(" ", "_") if action_label else "toggle"
    save_gui_screenshot(driver, f"{cfg.TEST_CASE_NAME}_{safe_label}_toggle_after_click")
    if new_state is not None and new_state != desired_enabled:
        log_progress(f"[CASE12][GUI] toggle 點擊後狀態仍為 {'enabled' if new_state else 'disabled'}，未切換成功，回傳 False")
        return False

    click_apply_if_available(driver, wait, action_label)
    return True


def no_reboot_fail_out(stage_name: str, reason: str) -> bool:
    """Case12 FAIL policy: do not reboot RE/Booster.

    The caller will return False so the outer fail-recovery path can run:
      1. best-effort enable TSM4 Wireless
      2. check_RE_status.py
      3. collect diagnosticcomlog.tgz
      4. restore ETH BH
    """
    log_result(f"Case12 {stage_name} FAIL: {reason}; no RE/Booster reboot will be sent")
    log_progress(f"[CASE12][NO_REBOOT] {stage_name}: {reason}")
    return False

def set_tsm4_wireless_with_retry(host: str, desired_enabled: bool, action_label: str) -> bool:
    """Open Chrome only when Case12 needs GUI action, retry once by default, then close Chrome.

    This avoids keeping a blank data: Chrome window open during FW collection, relay waits,
    SSH checks, and sync waits. It also matches the Case5~Case9 GUI retry style.
    """
    max_attempts = int(getattr(cfg, "CASE12_GUI_MAX_ATTEMPTS", CASE12_GUI_MAX_ATTEMPTS_DEFAULT))
    retry_wait = int(getattr(cfg, "CASE12_GUI_RETRY_WAIT", CASE12_GUI_RETRY_WAIT_DEFAULT))
    target = "Enable" if desired_enabled else "Disable"
    log_step(f"Case12 GUI: {action_label}, target={target}, max_attempts={max_attempts}")

    for attempt in range(1, max_attempts + 1):
        driver = None
        try:
            log_progress(f"[CASE12][GUI] {action_label}: 開啟 Chrome，attempt {attempt}/{max_attempts}")
            driver = create_chrome_driver()
            if driver is None:
                raise RuntimeError("Chrome driver 建立失敗")

            wait = WebDriverWait(driver, cfg.WAIT_TIMEOUT)
            ok = bool(set_tsm4_wireless(driver, wait, desired_enabled=desired_enabled, booster_host=host, action_label=action_label))
            if ok:
                log_result(f"Case12 GUI {action_label} PASS: target={target}")
            return ok

        except Exception as e:
            log_progress(f"[CASE12][GUI] {action_label} 發生異常 (attempt {attempt}/{max_attempts}): {type(e).__name__}: {e}")
            if attempt < max_attempts:
                log_progress(f"[CASE12][GUI] 等待 {retry_wait} 秒後 retry: {action_label}")
                receive_monitor(retry_wait)
            else:
                log_progress(f"[CASE12][GUI] {action_label} 已達最大嘗試次數，判定 GUI 操作失敗。")
                log_result(f"Case12 GUI {action_label} FAIL: exhausted retries")
                return False
        finally:
            if driver is not None:
                try:
                    driver.quit()
                    log_progress(f"[CASE12][GUI] {action_label}: Chrome 已關閉")
                except Exception:
                    pass

    return False


def ensure_tsm4_wireless_enabled_best_effort(host: Optional[str], reason: str, wait_after: Optional[int] = None) -> bool:
    """Best-effort cleanup: always try to leave TSM4 Wireless enabled.

    This is used on PASS final cleanup and all FAIL paths before diagnostic
    recovery, so the next case is not affected by Wireless remaining disabled.
    """
    if not host:
        log_result(f"Case12 final wireless enable SKIP: no Booster SSH host ({reason})")
        return False

    log_step(f"Case12 cleanup: ensure TSM4 Wireless enabled ({reason})")
    ok = set_tsm4_wireless_with_retry(host, desired_enabled=True, action_label=f"Cleanup Enable Wireless - {reason}")

    if wait_after is None:
        wait_after = int(getattr(cfg, "CASE12_FINAL_ENABLE_WAIT", 10))
    if wait_after > 0:
        log_step(f"Case12 cleanup: wait after Wireless enable, wait={wait_after}s")
        receive_monitor(wait_after)

    if ok:
        log_result(f"Case12 cleanup PASS: TSM4 Wireless enable GUI sent ({reason})")
        return True

    log_result(f"Case12 cleanup FAIL: unable to send TSM4 Wireless enable ({reason})")
    return False


def enable_wireless_then_fail_no_reboot(host: str, stage_name: str, reason: str) -> bool:
    """If disable phase fails, try to enable Wireless, then fail without reboot."""
    log_progress(f"{stage_name} FAIL cleanup: disable wireless 階段失敗，先嘗試把 TSM4 Wireless enable 回來；不送 RE/Booster reboot。")
    try:
        ensure_tsm4_wireless_enabled_best_effort(
            host,
            f"{stage_name} disable-stage fail cleanup",
            wait_after=int(getattr(cfg, "CASE12_ENABLE_RECOVERY_WAIT", CASE12_ENABLE_RECOVERY_WAIT_DEFAULT)),
        )
    except Exception as e:
        log_progress(f"{stage_name} Disable FAIL cleanup enable wireless 發生異常，但仍不送 reboot: {type(e).__name__}: {e}")

    return no_reboot_fail_out(stage_name, reason)


def run_wireless_disable_enable_stage(stage_name: str, init_wait: int, relay_state: str, host: str) -> Tuple[bool, str, str]:
    """Returns (ok, status_label, fail_detail).

    status_label = e.g. 'ETH BH Disable' / 'ETH BH Enable' — identifies which sub-stage failed.
    fail_detail is empty on PASS.
    """
    log_separator(f"{stage_name} 測試開始")
    log_step(f"Case12 {stage_name}: start Wireless Disable/Enable FH sync stage")
    log_progress(f"STEP: Relay 6 切換 ({relay_state.upper()}) 配置 {stage_name}")
    control_relay(relay_state)
    log_progress(f"{stage_name} init wait = {init_wait} 秒")
    if not monitored_wait(init_wait, f"{stage_name} init", check_interval=int(getattr(cfg, "CASE12_INIT_WAIT_CHECK_INTERVAL", 30))):
        fd = f"{stage_name}: DUT 在 init wait 期間發生未預期 reboot"
        return False, f"{stage_name} Unexpected Reboot", fd

    log_separator(f"{stage_name} - STEP 1: TSM4 GUI Disable Wireless")
    if not set_tsm4_wireless_with_retry(host, desired_enabled=False, action_label=f"{stage_name} Disable Wireless"):
        log_progress(f"{stage_name} GUI Disable Wireless 失敗")
        fd = f"{stage_name}: GUI Disable Wireless 失敗，已嘗試 enable wireless recovery。"
        enable_wireless_then_fail_no_reboot(host, stage_name, fd)
        return False, f"{stage_name} Disable", fd
    sync_dis_ok, dis_fail_detail = _poll_fh_sync(
        host, f"{stage_name} Disable", "1", False, cfg.CASE12_WIRELESS_SYNC_WAIT
    )
    if not sync_dis_ok:
        if "WiFi BH" in stage_name:
            bridge_note = _check_wifi_bh_bridge_note(host)
            if bridge_note:
                dis_fail_detail = f"{dis_fail_detail} | {bridge_note}" if dis_fail_detail else bridge_note
        log_progress(f"{stage_name} FAIL: {dis_fail_detail}")
        enable_wireless_then_fail_no_reboot(host, stage_name, f"{stage_name}: disable Wireless 後 FH sync 失敗 — {dis_fail_detail}")
        return False, f"{stage_name} Disable", dis_fail_detail

    log_separator(f"{stage_name} - STEP 2: TSM4 GUI Enable Wireless")
    if not set_tsm4_wireless_with_retry(host, desired_enabled=True, action_label=f"{stage_name} Enable Wireless"):
        log_progress(f"{stage_name} GUI Enable Wireless 失敗")
        fd = f"{stage_name}: GUI Enable Wireless 失敗"
        ensure_tsm4_wireless_enabled_best_effort(host, f"{stage_name} enable-step GUI failure", wait_after=int(getattr(cfg, "CASE12_ENABLE_RECOVERY_WAIT", CASE12_ENABLE_RECOVERY_WAIT_DEFAULT)))
        return False, f"{stage_name} Enable", fd
    sync_ena_ok, ena_fail_detail = _poll_fh_sync(
        host, f"{stage_name} Enable", "0", True, cfg.CASE12_WIRELESS_SYNC_WAIT
    )
    if not sync_ena_ok:
        if "WiFi BH" in stage_name:
            bridge_note = _check_wifi_bh_bridge_note(host)
            if bridge_note:
                ena_fail_detail = f"{ena_fail_detail} | {bridge_note}" if ena_fail_detail else bridge_note
        no_reboot_fail_out(stage_name, f"{stage_name}: enable Wireless 後 FH sync 失敗 — {ena_fail_detail}")
        return False, f"{stage_name} Enable", ena_fail_detail

    log_result(f"Case12 {stage_name} PASS: Disable/Enable Wireless FH sync check")
    log_progress(f"{stage_name} PASS: Disable/Enable Wireless FH sync check 通過")
    return True, f"{stage_name} Enable", ""


def _case12_fail_recovery(loop_index: int, interface_name: str, reason: str, host: Optional[str] = None) -> None:
    """Run current common fail diagnostic flow without factory workaround.

    safe_handle_fail_recovery() runs check_RE_status.py and restores ETH BH.
    The runner-level GW+Booster workaround is still controlled by the runner
    and should not run for case12.
    """
    log_step(f"Case12 FAIL recovery: {interface_name}, reason={reason}")
    log_progress(f"[CASE12][FAIL RECOVERY] {interface_name}: {reason}")
    ensure_tsm4_wireless_enabled_best_effort(host, f"FAIL recovery before diagnostic - {interface_name}: {reason}", wait_after=int(getattr(cfg, "CASE12_ENABLE_RECOVERY_WAIT", CASE12_ENABLE_RECOVERY_WAIT_DEFAULT)))
    write_recovery_note(interface_name, "FailDiagnostic(check_RE_status_collect_diag_restore_ETH_BH)")
    safe_handle_fail_recovery(f"Loop{loop_index}_{cfg.TEST_CASE_NAME}_{interface_name.replace(' ', '_')}_Fail")


def run_one_loop(loop_index: int, host: str) -> bool:
    eth_ok, eth_status, eth_detail = run_wireless_disable_enable_stage(
        "ETH BH", cfg.CASE12_ETH_BH_INIT_WAIT, "on", host
    )
    if not eth_ok:
        log_progress(f"LOOP {loop_index} ETH BH FAIL，WiFi BH 不執行，停止測試。")
        write_summary(f"LOOP {loop_index}", "ETH BH", "N/A", "FAIL", eth_detail or "ETH_BH_FH_Sync_Fail", status=eth_status)
        _case12_fail_recovery(loop_index, "ETH BH", "ETH_BH_FH_Sync_Fail", host)
        return False

    wifi_ok, wifi_status, wifi_detail = run_wireless_disable_enable_stage(
        "WiFi BH", cfg.CASE12_WIFI_BH_INIT_WAIT, "off", host
    )
    if not wifi_ok:
        log_progress(f"LOOP {loop_index} WiFi BH FAIL，停止測試。")
        write_summary(f"LOOP {loop_index}", "WiFi BH", "N/A", "FAIL", wifi_detail or "WiFi_BH_FH_Sync_Fail", status=wifi_status)
        _case12_fail_recovery(loop_index, "WiFi BH", "WiFi_BH_FH_Sync_Fail", host)
        return False

    log_progress(f"LOOP {loop_index} PASS。")
    write_summary(f"LOOP {loop_index}", "ETH/WiFi BH", "N/A", "PASS", "None", status="ETH+WiFi Enable")
    restore_eth_backhaul_between_loops(loop_index)
    return True


def run_test() -> int:
    router_fw, booster_fw = get_environment_fw_versions_close_browser()
    init_summary_log(router_fw, booster_fw)
    log_separator(f"自動化測試啟動 (共計 {cfg.TOTAL_LOOPS} Loops) - {cfg.TEST_CASE_NAME}")
    log_progress("Case12 policy: ETH BH PASS 才執行 WiFi BH；FAIL 不送 Booster reboot；best-effort enable Wireless 後執行 check_RE_status 診斷並還原 ETH BH。")

    host = get_booster_host()
    if not host:
        log_progress("Case12 FAIL: 無法取得 Booster SSH host")
        write_summary("LOOP -", "ETH/WiFi BH", "N/A", "FAIL", "No_Booster_SSH_Host")
        _case12_fail_recovery(0, "ETH BH", "No_Booster_SSH_Host", host)
        return 1
    log_progress(f"Case12 使用 Booster SSH host: {host}")

    if not wait_for_onboarding_if_recently_rebooted(log_prefix="[CASE12]"):
        log_progress("Case12 FAIL: DUT onboarding 未就緒 (可能前一個 case 觸發了 reboot)")
        write_summary("LOOP -", "Pre-check", "N/A", "FAIL", "DUT_Onboarding_Not_Ready_At_Start")
        _case12_fail_recovery(0, "ETH BH", "DUT_Not_Ready_At_Start", host)
        return 1

    log_progress("Case12 GUI policy: 不預先開啟 Chrome；只在 Disable/Enable Wireless 動作期間開啟，動作完成後立即關閉。")

    for loop in range(1, cfg.TOTAL_LOOPS + 1):
        log_separator(f"LOOP {loop} - Case12 Wireless FH Disable/Enable Sync Check")
        clear_cached_ssh_host()
        # Reuse fixed host unless user left it None; rediscover is only for diagnostics if host changes.
        if cfg.ONBOARDING_SSH_HOST is None:
            latest_host = discover_ssh_host_by_serial(log_prefix="[CASE12][SSH]", force=True)
            if latest_host:
                host = latest_host
                log_progress(f"Case12 更新 Booster SSH host: {host}")
        if not run_one_loop(loop, host):
            return 1

    ensure_tsm4_wireless_enabled_best_effort(host, "PASS final cleanup", wait_after=int(getattr(cfg, "CASE12_FINAL_ENABLE_WAIT", 10)))
    restore_eth_backhaul("Case12 測試 PASS 結束")
    log_separator("Case12 所有測試迴圈執行完畢，結果 PASS")
    log_result("Case12 PASS: Wireless enabled, FH sync verified, ETH BH restored")
    return 0


if __name__ == "__main__":
    cfg.TEST_CASE_NAME = "case12_TSM4_Wireless_FH_Disable_Enable_Sync_Check"
    args = parse_args()
    apply_args(args)
    init_log_filenames()
    start_background_serial_logger()
    exit_code = 1
    try:
        exit_code = run_test()
    except KeyboardInterrupt:
        log_progress("使用者中斷測試。")
        restore_eth_backhaul("使用者中斷")
        exit_code = 130
    except Exception as e:
        log_progress(f"Case12 主程式發生未預期錯誤: {type(e).__name__}: {e}")
        restore_eth_backhaul("Case12 主程式未預期錯誤")
        exit_code = 1
    finally:
        stop_background_serial_logger()

    raise SystemExit(exit_code)
