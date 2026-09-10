"""Shared runner for Case6~Case9 GUI action onboarding cases."""
import os
import time

from testlib import config as cfg
from testlib import raspi5_air_capture_auto_channel
from testlib import tcpdump_debug
from testlib.logger import (
    init_summary_log,
    log_progress,
    log_step,
    log_result,
    log_separator,
    write_summary,
    summary_loop_display,
)
from testlib.env_info import get_router_fw_version, get_booster_fw_version
from testlib.web_gui import trigger_web_action
from testlib.relay import control_relay, restore_eth_backhaul
from testlib.serial_console import receive_monitor
from testlib.onboarding import run_polling_or_recover, poll_booster_console
from testlib.ssh_client import get_cached_ssh_host, run_ssh_command
from testlib.recovery import safe_handle_fail_recovery


def _verify_device_offline(label, timeout, poll_interval):
    """Poll SSH until device goes offline (confirms reboot started). Returns True if offline detected."""
    host = get_cached_ssh_host()
    if host is None:
        log_progress(f"[{label}] 無法取得 SSH host，跳過 offline 確認")
        return True

    log_step(f"{label}: verify reboot offline (timeout={timeout}s, poll={poll_interval}s, host={host})")
    log_progress(f"[{label}] 確認裝置已下線（重開機成功），最多等待 {timeout}s...")
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        ok, _, _ = run_ssh_command(host, "echo alive", timeout=3)
        if not ok:
            log_result(f"[{label}] Reboot offline 確認成功 (attempt {attempt})：SSH 不可達，裝置正在重開機")
            return True
        remaining = int(deadline - time.monotonic())
        log_progress(f"[{label}] SSH 仍可達 (attempt {attempt})，等待裝置下線... (剩餘 {remaining}s)")
        receive_monitor(poll_interval)

    log_result(f"[{label}] Reboot offline 確認失敗：{timeout}s 內 SSH 仍可達，裝置可能未重開機")
    return False


def _verify_reboot_by_serial(label, timeout, poll_interval=2, keywords=None):
    """Watch the session log file for serial boot keywords to confirm reboot (method 2).

    Called after _verify_device_offline() succeeds. Reads only new log content written
    after this function starts, so it cannot be confused by earlier boot messages.
    Returns True if any keyword is found within timeout; False otherwise.
    If the log file path is not configured, returns True (skip, SSH offline is enough).
    """
    if keywords is None:
        keywords = getattr(cfg, "REBOOT_SERIAL_KEYWORDS", ["Restarting system", "BusyBox v", "Please press Enter to activate"])

    log_file = getattr(cfg, "FULL_CONSOLE_LOG", "")
    if not log_file:
        log_progress(f"[{label}] Serial reboot verify: log 檔路徑未設定，跳過 (SSH offline 已確認)")
        return True

    try:
        start_pos = os.path.getsize(log_file) if os.path.exists(log_file) else 0
    except Exception:
        start_pos = 0

    log_step(f"{label}: verify reboot by serial log (timeout={timeout}s, keywords={keywords})")
    log_progress(f"[{label}] 等待 serial 確認 Booster 重開機訊號 (timeout={timeout}s)...")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if os.path.exists(log_file):
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    f.seek(start_pos)
                    new_content = f.read()
                for kw in keywords:
                    if kw in new_content:
                        log_result(f"[{label}] Serial reboot 確認成功：偵測到 '{kw}'")
                        return True
        except Exception as e:
            log_progress(f"[{label}] Serial log 讀取異常: {e}")

        remaining = int(deadline - time.monotonic())
        log_progress(f"[{label}] 等待 serial reboot 訊號... (剩餘 {remaining}s)")
        receive_monitor(poll_interval)

    log_result(f"[{label}] Serial reboot 確認失敗：{timeout}s 內未偵測到 reboot 訊號 (keywords={keywords})")
    return False


def get_init_wait_time_by_action(action_label):
    label = str(action_label).lower()
    if "reset" in label:
        return cfg.RESET_INIT_WAIT_TIME
    if "reboot" in label:
        return cfg.REBOOT_INIT_WAIT_TIME
    return cfg.INIT_WAIT_TIME


def execute_one_backhaul_test(
    loop,
    interface_name,
    relay_state,
    action_xpath,
    action_label,
    max_total_limit,
    threshold,
    active_driver=None,
    init_wait_time=None,
    restore_eth_bh=True,
    pre_connect_check=False,
    precheck_init_wait=None,
    precheck_threshold=None,
    precheck_max_limit=None,
    air_capture_bssid=None,
    skip_onboarding_poll=False,
):
    log_separator(f"LOOP {loop} - {interface_name} 測試開始")

    backhaul_name = "ETH BH" if str(relay_state).lower() == "on" else "WiFi BH"

    if pre_connect_check:
        # Switch backhaul first, confirm booster is connected, then trigger reboot
        log_step(f"Loop {loop} {interface_name}: pre-switch backhaul to {backhaul_name}, relay {cfg.RELAY_ETH_PORT} {relay_state}")
        log_progress(f"STEP: 先切換 Relay ({relay_state.upper()})，等待 Booster 透過 {backhaul_name} 連回...")
        control_relay(relay_state)
        receive_monitor(cfg.RELAY_SETTLE_TIME)

        _precheck_init_wait = precheck_init_wait if precheck_init_wait is not None else cfg.CASE6_WIFI_PRECHECK_INIT_WAIT
        _precheck_threshold = precheck_threshold if precheck_threshold is not None else cfg.CASE6_WIFI_PRECHECK_THRESHOLD
        _precheck_max_limit = precheck_max_limit if precheck_max_limit is not None else cfg.CASE6_WIFI_PRECHECK_MAX_LIMIT
        log_step(
            f"Loop {loop} {interface_name}: WiFi BH pre-check "
            f"(init_wait={_precheck_init_wait}s, threshold={_precheck_threshold}, "
            f"max_limit={_precheck_max_limit}s)"
        )
        log_progress(f"STEP: 確認 Booster 已透過 {backhaul_name} 連線，才進行 reboot 測試...")
        _precheck_reason_out = []
        precheck_ok = poll_booster_console(
            str(loop),
            f"{interface_name} Pre-Check",
            _precheck_init_wait,
            _precheck_threshold,
            max_total_limit=_precheck_max_limit,
            write_summary_on_pass=False,
            write_summary_on_fail=False,
            reason_out=_precheck_reason_out,
        )
        if not precheck_ok:
            _base = f"Booster 未連回 TSM4 ({_precheck_max_limit}s)"
            fail_reason = f"{_base} | {_precheck_reason_out[0]}" if _precheck_reason_out else _base
            write_summary(summary_loop_display(str(loop), interface_name), interface_name, "N/A", "FAIL", fail_reason)
            log_result(f"Loop {loop} {interface_name}: FAIL, Booster 未能透過 {backhaul_name} 連線，跳過 reboot 測試")
            log_progress(f"!! {interface_name} Pre-Check FAIL，Booster 未連上 {backhaul_name}，停止測試 !!")
            safe_handle_fail_recovery(
                f"Loop{loop}_{interface_name.replace(' ', '_')}_PreCheck_Fail",
                restore_eth_bh=restore_eth_bh,
            )
            return False

        log_result(f"Loop {loop} {interface_name}: Pre-Check PASS, Booster 已連上 {backhaul_name}，開始 reboot 測試")

    if not pre_connect_check:
        log_step(f"Loop {loop} {interface_name}: switch backhaul to {backhaul_name}, relay {cfg.RELAY_ETH_PORT} {relay_state}")
        log_progress(f"STEP: Relay 切換 ({relay_state.upper()}) 配置 {interface_name}")
        control_relay(relay_state)
        receive_monitor(cfg.RELAY_SETTLE_TIME)

    log_step(f"Loop {loop} {interface_name}: GUI action start ({action_label})")
    log_progress(f"STEP: 準備執行 {interface_name} 測試 (GUI 觸發 {action_label})")

    gui_ok, duration_start_time = trigger_web_action(action_xpath, action_label, active_driver)
    if not gui_ok:
        write_summary(summary_loop_display(str(loop), interface_name), interface_name, "N/A", "FAIL", "GUI Error")
        log_result(f"Loop {loop} {interface_name}: FAIL, GUI Error ({action_label})")
        log_progress(f"!! {interface_name} GUI 操作失敗，只寫 Summary，不執行 diag / recovery !!")
        if restore_eth_bh:
            log_step(f"Loop {loop} {interface_name}: restore ETH BH after GUI Error")
            restore_eth_backhaul(f"{interface_name} GUI Error")
        return False

    log_result(f"Loop {loop} {interface_name}: GUI action command sent ({action_label})")

    for _offline_retry in range(cfg.REBOOT_OFFLINE_VERIFY_RETRIES + 1):
        if _offline_retry > 0:
            log_progress(
                f"[{interface_name}] Reboot 指令已送出但裝置未下線，"
                f"重送 GUI 指令 (retry {_offline_retry}/{cfg.REBOOT_OFFLINE_VERIFY_RETRIES})..."
            )
            _gui_ok2, duration_start_time = trigger_web_action(action_xpath, action_label, None)
            if not _gui_ok2:
                write_summary(summary_loop_display(str(loop), interface_name), interface_name, "N/A", "FAIL", "GUI Error (offline retry)")
                log_result(f"Loop {loop} {interface_name}: FAIL, GUI Error on offline retry ({action_label})")
                log_progress(f"!! {interface_name} offline retry GUI 操作失敗 !!")
                if restore_eth_bh:
                    log_step(f"Loop {loop} {interface_name}: restore ETH BH after GUI Error (offline retry)")
                    restore_eth_backhaul(f"{interface_name} GUI Error (offline retry)")
                return False

        log_step(f"Loop {loop} {interface_name}: wait action sync, wait={cfg.REBOOT_SYNC_WAIT}s")
        log_progress(f"等待 {cfg.REBOOT_SYNC_WAIT} 秒讓 Booster 確實收到指令...")
        receive_monitor(cfg.REBOOT_SYNC_WAIT)

        if _verify_device_offline(
            f"Loop {loop} {interface_name}",
            cfg.REBOOT_OFFLINE_VERIFY_TIMEOUT,
            cfg.REBOOT_OFFLINE_POLL_INTERVAL,
        ):
            break
    else:
        write_summary(
            summary_loop_display(str(loop), interface_name), interface_name, "N/A", "FAIL",
            f"Booster did not reboot: TSM4 '{action_label}' sent but Booster SSH still reachable ({cfg.REBOOT_OFFLINE_VERIFY_RETRIES + 1} attempts) - command not received"
        )
        log_result(
            f"Loop {loop} {interface_name}: FAIL, "
            f"送出 {cfg.REBOOT_OFFLINE_VERIFY_RETRIES + 1} 次 '{action_label}' 指令後 Booster SSH 仍可達，未收到指令"
        )
        log_progress(f"!! {interface_name} Reboot 指令送出後裝置未下線，停止測試 !!")
        if restore_eth_bh:
            log_step(f"Loop {loop} {interface_name}: restore ETH BH after Reboot Not Detected")
            restore_eth_backhaul(f"{interface_name} Reboot Not Detected")
        return False

    if skip_onboarding_poll:
        # Method 2: confirm reboot via serial log keywords
        serial_ok = _verify_reboot_by_serial(
            f"Loop {loop} {interface_name}",
            cfg.REBOOT_SERIAL_VERIFY_TIMEOUT,
            cfg.REBOOT_SERIAL_VERIFY_POLL_INTERVAL,
        )
        if not serial_ok:
            write_summary(
                summary_loop_display(str(loop), interface_name), interface_name, "N/A", "FAIL",
                f"Booster did not reboot: TSM4 '{action_label}' sent, SSH offline but no serial boot signal detected - command not received"
            )
            log_result(f"Loop {loop} {interface_name}: FAIL, Serial 未偵測到 reboot 訊號，Booster 未收到 '{action_label}' 指令")
            log_progress(f"!! {interface_name} Serial reboot 確認失敗，停止測試 !!")
            if restore_eth_bh:
                log_step(f"Loop {loop} {interface_name}: restore ETH BH after Serial Reboot Not Detected")
                restore_eth_backhaul(f"{interface_name} Serial Reboot Not Detected")
            return False
        write_summary(
            summary_loop_display(str(loop), interface_name),
            interface_name, "N/A", "PASS", "SSH offline + Serial reboot confirmed"
        )
        log_result(f"Loop {loop} {interface_name}: PASS (SSH offline + serial reboot 確認)")
        return True

    if air_capture_bssid:
        log_step(f"Loop {loop} {interface_name}: start air capture (bssid={air_capture_bssid})")
        raspi5_air_capture_auto_channel.start(air_capture_bssid)

    if init_wait_time is None:
        init_wait_time = get_init_wait_time_by_action(action_label)
        log_progress(f"Polling init wait time 依 action_label='{action_label}' 設定為 {init_wait_time} 秒")
    else:
        log_progress(f"Polling init wait time for {interface_name}: {init_wait_time} 秒")

    log_step(
        f"Loop {loop} {interface_name}: onboarding check start "
        f"(init_wait={init_wait_time}s, threshold={threshold}, max_total_limit={max_total_limit}s)"
    )
    result = run_polling_or_recover(
        loop,
        interface_name,
        init_wait_time,
        threshold,
        f"{interface_name.replace(' ', '_')}_Fail",
        duration_start_time=duration_start_time,
        max_total_limit=max_total_limit,
        restore_eth_bh=restore_eth_bh,
    )

    if result:
        log_result(f"Loop {loop} {interface_name}: PASS")
    else:
        log_result(f"Loop {loop} {interface_name}: FAIL")
    return result


def run_gui_action_wifi_only_case(action_xpath, action_label, max_total_limit, threshold=None, wifi_init_wait=None, precheck_init_wait=None, precheck_threshold=None, precheck_max_limit=None, air_capture_bssid=None, air_capture_local_dir="."):
    """Run GUI action test with WiFi BH stage only (skip ETH BH)."""
    active_driver = None
    threshold = cfg.ONBOARDING_THRESHOLD if threshold is None else threshold
    log_step(f"{cfg.TEST_CASE_NAME}: GUI action WiFi BH only case start ({action_label}), loops={cfg.TOTAL_LOOPS}")
    try:
        router_fw, active_driver = get_router_fw_version()
        log_step(f"{cfg.TEST_CASE_NAME}: wait before GUI action navigation, wait={cfg.GW_FW_TO_GUI_ACTION_SLEEP}s")
        log_progress(f"GW FW 取得完成，保留 Chrome，等待 {cfg.GW_FW_TO_GUI_ACTION_SLEEP} 秒後繼續 GUI login/navigation...")
        receive_monitor(cfg.GW_FW_TO_GUI_ACTION_SLEEP)
        booster_fw = get_booster_fw_version()

        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {cfg.TOTAL_LOOPS} Loops) - {cfg.TEST_CASE_NAME}")
        log_progress("Fail policy: GUI Error only writes Summary; any FAIL stops current script.")

        for loop in range(1, cfg.TOTAL_LOOPS + 1):
            raspi5_air_capture_auto_channel.reset()
            log_step(f"{cfg.TEST_CASE_NAME}: Loop {loop} start")
            wifi_pass = execute_one_backhaul_test(
                loop, "WiFi BH", "off", action_xpath, action_label,
                max_total_limit, threshold, active_driver, wifi_init_wait,
                restore_eth_bh=False,
                pre_connect_check=True,
                precheck_init_wait=precheck_init_wait,
                precheck_threshold=precheck_threshold,
                precheck_max_limit=precheck_max_limit,
                air_capture_bssid=air_capture_bssid,
            )
            active_driver = None
            if not wifi_pass:
                log_result(f"{cfg.TEST_CASE_NAME}: Loop {loop} FAIL at WiFi BH")
                log_progress(f"LOOP {loop} WiFi BH FAIL / GUI Error，停止測試。")
                raspi5_air_capture_auto_channel.stop_and_fetch(air_capture_local_dir)
                return False

            raspi5_air_capture_auto_channel.stop_and_delete()
            log_result(f"{cfg.TEST_CASE_NAME}: Loop {loop} PASS")
            log_progress(f"LOOP {loop} PASS。")

        log_result(f"{cfg.TEST_CASE_NAME}: PASS")
        log_separator("所有測試迴圈執行完畢，結果 PASS")
        return True
    except KeyboardInterrupt:
        log_result(f"{cfg.TEST_CASE_NAME}: interrupted by user")
        log_progress("使用者中斷測試。")
        return False
    except Exception as e:
        log_result(f"{cfg.TEST_CASE_NAME}: FAIL, unexpected error {type(e).__name__}: {e}")
        log_progress(f"主程式發生未預期錯誤: {type(e).__name__}: {e}")
        return False
    finally:
        if active_driver is not None:
            try:
                active_driver.quit()
            except Exception:
                pass


def run_gui_action_case(action_xpath, action_label, max_total_limit, threshold=None, eth_init_wait=None, wifi_init_wait=None, precheck_init_wait=None, precheck_threshold=None, precheck_max_limit=None):
    active_driver = None
    threshold = cfg.ONBOARDING_THRESHOLD if threshold is None else threshold
    log_step(f"{cfg.TEST_CASE_NAME}: GUI action case start ({action_label}), loops={cfg.TOTAL_LOOPS}")
    try:
        router_fw, active_driver = get_router_fw_version()
        log_step(f"{cfg.TEST_CASE_NAME}: wait before GUI action navigation, wait={cfg.GW_FW_TO_GUI_ACTION_SLEEP}s")
        log_progress(f"GW FW 取得完成，保留 Chrome，等待 {cfg.GW_FW_TO_GUI_ACTION_SLEEP} 秒後繼續 GUI login/navigation...")
        receive_monitor(cfg.GW_FW_TO_GUI_ACTION_SLEEP)
        booster_fw = get_booster_fw_version()

        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {cfg.TOTAL_LOOPS} Loops) - {cfg.TEST_CASE_NAME}")
        log_progress("Fail policy: GUI Error only writes Summary; any FAIL stops current script; ETH BH FAIL will not continue WiFi BH.")

        for loop in range(1, cfg.TOTAL_LOOPS + 1):
            log_step(f"{cfg.TEST_CASE_NAME}: Loop {loop} start")
            eth_pass = execute_one_backhaul_test(
                loop, "ETH BH", "on", action_xpath, action_label,
                max_total_limit, threshold, active_driver, eth_init_wait,
                skip_onboarding_poll=True,
            )
            active_driver = None
            if not eth_pass:
                log_result(f"{cfg.TEST_CASE_NAME}: Loop {loop} FAIL at ETH BH")
                log_progress(f"LOOP {loop} ETH BH FAIL / GUI Error，停止測試，不繼續 WiFi BH。")
                return False

            wifi_pass = execute_one_backhaul_test(
                loop, "WiFi BH", "off", action_xpath, action_label,
                max_total_limit, threshold, None, wifi_init_wait,
                pre_connect_check=True,
                precheck_init_wait=precheck_init_wait,
                precheck_threshold=precheck_threshold,
                precheck_max_limit=precheck_max_limit,
                skip_onboarding_poll=True,
            )
            if not wifi_pass:
                log_result(f"{cfg.TEST_CASE_NAME}: Loop {loop} FAIL at WiFi BH")
                log_progress(f"LOOP {loop} WiFi BH FAIL / GUI Error，停止測試。")
                return False

            log_result(f"{cfg.TEST_CASE_NAME}: Loop {loop} PASS")
            log_progress(f"LOOP {loop} PASS。")
            if loop < cfg.TOTAL_LOOPS:
                log_step(f"{cfg.TEST_CASE_NAME}: Loop {loop} cooldown, restore ETH BH, wait={cfg.LOOP_ETH_RESTORE_WAIT}s")
                log_progress(f"LOOP {loop} 完成，恢復 ETH BH，等待 {cfg.LOOP_ETH_RESTORE_WAIT}s 讓 Booster 穩定...")
                restore_eth_backhaul(f"Loop {loop} cooldown")
                receive_monitor(cfg.LOOP_ETH_RESTORE_WAIT)
                log_step(f"{cfg.TEST_CASE_NAME}: Loop {loop} cooldown post-check, verify Booster onboard before Loop {loop + 1}")
                log_progress(f"LOOP {loop} cooldown: 確認 Booster 已 onboard (ETH BH)，才開始 Loop {loop + 1}...")
                cooldown_ok = poll_booster_console(
                    str(loop),
                    "Loop Cooldown ETH BH",
                    0,
                    cfg.ONBOARDING_THRESHOLD,
                    max_total_limit=cfg.NORMAL_MAX_TOTAL_LIMIT,
                    write_summary_on_pass=False,
                    write_summary_on_fail=False,
                )
                if not cooldown_ok:
                    log_result(f"{cfg.TEST_CASE_NAME}: Loop {loop} cooldown post-check FAIL，Booster 未 onboard，停止測試")
                    log_progress(f"LOOP {loop} cooldown Booster 未連線，無法繼續 Loop {loop + 1}，停止測試。")
                    return False

        log_step(f"{cfg.TEST_CASE_NAME}: all loops PASS, restore ETH BH")
        restore_eth_backhaul("測試 PASS 結束")
        log_result(f"{cfg.TEST_CASE_NAME}: PASS")
        log_separator("所有測試迴圈執行完畢，結果 PASS")
        return True
    except KeyboardInterrupt:
        log_result(f"{cfg.TEST_CASE_NAME}: interrupted by user")
        log_progress("使用者中斷測試。")
        restore_eth_backhaul("使用者中斷")
        return False
    except Exception as e:
        log_result(f"{cfg.TEST_CASE_NAME}: FAIL, unexpected error {type(e).__name__}: {e}")
        log_progress(f"主程式發生未預期錯誤: {type(e).__name__}: {e}")
        restore_eth_backhaul("主程式未預期錯誤")
        return False
    finally:
        if active_driver is not None:
            try:
                active_driver.quit()
            except Exception:
                pass


def run_gui_action_wifi_bh_unit_case(
    action_xpath, action_label, max_total_limit,
    threshold=None, wifi_init_wait=None,
    post_reset_wait=100,
    air_capture_bssid=None, air_capture_local_dir=".",
):
    """WiFi BH unit case runner – no relay switch, no precheck.

    Assumes environment is already on WiFi BH and stays on WiFi BH for all loops.

    Flow per loop:
      1. GUI action (Reset Router+Boosters)
      2. Wait post_reset_wait (s) for TSM4 LAN to recover
      3. Start raspi5 air capture (Auto_Scan_BSSID_DumpPackets.sh)
      4. Wait 5s
      5. Start booster tcpdump (ath1, via serial)
      6. Poll onboarding
         PASS → stop_and_delete air capture + cleanup booster tcpdump
         FAIL → safe_handle_fail_recovery (stops booster tcpdump + diag collect)
                + stop_and_fetch air capture
    """
    active_driver = None
    threshold = cfg.RESET_ONBOARDING_THRESHOLD if threshold is None else threshold
    log_step(f"{cfg.TEST_CASE_NAME}: WiFi BH unit case start ({action_label}), loops={cfg.TOTAL_LOOPS}")
    try:
        router_fw, active_driver = get_router_fw_version()
        log_step(f"{cfg.TEST_CASE_NAME}: wait before GUI action navigation, wait={cfg.GW_FW_TO_GUI_ACTION_SLEEP}s")
        log_progress(f"GW FW 取得完成，保留 Chrome，等待 {cfg.GW_FW_TO_GUI_ACTION_SLEEP} 秒後繼續...")
        receive_monitor(cfg.GW_FW_TO_GUI_ACTION_SLEEP)
        booster_fw = get_booster_fw_version()

        init_summary_log(router_fw, booster_fw)
        log_separator(f"自動化測試啟動 (共計 {cfg.TOTAL_LOOPS} Loops) - {cfg.TEST_CASE_NAME}")
        log_progress("Fail policy: GUI Error stops script; FAIL → diag collect + air/tcpdump fetch.")

        for loop in range(1, cfg.TOTAL_LOOPS + 1):
            raspi5_air_capture_auto_channel.reset()
            log_step(f"{cfg.TEST_CASE_NAME}: Loop {loop} start")

            # 1. GUI Reset
            gui_ok, duration_start_time = trigger_web_action(action_xpath, action_label, active_driver)
            active_driver = None
            if not gui_ok:
                write_summary(str(loop), "WiFi BH", "N/A", "FAIL", "GUI Error")
                log_result(f"{cfg.TEST_CASE_NAME}: Loop {loop} FAIL – GUI Error")
                log_progress(f"!! Loop {loop} GUI 操作失敗，停止測試 !!")
                return False

            # 2. Wait for TSM4 to reboot and LAN to recover
            log_step(f"Loop {loop}: post-reset wait {post_reset_wait}s (TSM4 reboot + raspi5 LAN recovery)")
            log_progress(f"等待 {post_reset_wait}s 讓 TSM4 重啟、raspi5 恢復 LAN 連線...")
            receive_monitor(post_reset_wait)

            # 3. Start raspi5 air capture
            if air_capture_bssid:
                log_step(f"Loop {loop}: start raspi5 air capture (bssid={air_capture_bssid})")
                raspi5_air_capture_auto_channel.start(air_capture_bssid)

            # 4. Wait 5s before booster tcpdump
            receive_monitor(5)

            # 5. Start booster tcpdump (ath1, via serial)
            log_step(f"Loop {loop}: start booster tcpdump (ath1)")
            tcpdump_debug.start_wifi_bh_tcpdump()

            # 6. Poll onboarding
            log_step(
                f"Loop {loop}: onboarding poll "
                f"(init_wait={wifi_init_wait}s, threshold={threshold}, max={max_total_limit}s)"
            )
            result = run_polling_or_recover(
                loop, "WiFi BH",
                wifi_init_wait, threshold,
                "WiFi_BH_Fail",
                duration_start_time=duration_start_time,
                max_total_limit=max_total_limit,
                restore_eth_bh=False,
                show_loop_number=True,
            )

            if result:
                tcpdump_debug.stop_and_cleanup_wifi_bh_tcpdump()
                raspi5_air_capture_auto_channel.stop_and_delete()
                log_result(f"{cfg.TEST_CASE_NAME}: Loop {loop} PASS")
                log_progress(f"LOOP {loop} PASS。")
            else:
                # safe_handle_fail_recovery already ran inside run_polling_or_recover:
                # it stopped booster tcpdump + ran check_RE_status + collected diagnosticcomlog
                raspi5_air_capture_auto_channel.stop_and_fetch(air_capture_local_dir)
                log_result(f"{cfg.TEST_CASE_NAME}: Loop {loop} FAIL at WiFi BH")
                log_progress(f"LOOP {loop} WiFi BH FAIL，停止測試。")
                return False

        log_result(f"{cfg.TEST_CASE_NAME}: PASS")
        log_separator("所有測試迴圈執行完畢，結果 PASS")
        return True

    except KeyboardInterrupt:
        log_result(f"{cfg.TEST_CASE_NAME}: interrupted by user")
        log_progress("使用者中斷測試。")
        raspi5_air_capture_auto_channel.stop_and_fetch(air_capture_local_dir)
        tcpdump_debug.stop_for_download()
        return False
    except Exception as e:
        log_result(f"{cfg.TEST_CASE_NAME}: FAIL, unexpected error {type(e).__name__}: {e}")
        log_progress(f"主程式發生未預期錯誤: {type(e).__name__}: {e}")
        raspi5_air_capture_auto_channel.stop_and_fetch(air_capture_local_dir)
        tcpdump_debug.stop_for_download()
        return False
    finally:
        if active_driver is not None:
            try:
                active_driver.quit()
            except Exception:
                pass
