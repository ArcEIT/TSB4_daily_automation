"""Web GUI action helpers for Case5~Case9."""
import os
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from . import config as cfg
from .env_info import create_chrome_driver
from .logger import log_progress, log_step, log_result
from .serial_console import receive_monitor


def save_gui_screenshot(driver, label):
    """Save a GUI screenshot to the log directory for debugging. Silently skips on any error."""
    log_file = getattr(cfg, "FULL_CONSOLE_LOG", "")
    if not log_file or driver is None:
        return
    try:
        log_dir = os.path.dirname(os.path.abspath(log_file)) if log_file else "."
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(label))
        path = os.path.join(log_dir, f"{safe}.png")
        driver.save_screenshot(path)
        log_progress(f"[SCREENSHOT] {path}")
    except Exception as e:
        log_progress(f"[SCREENSHOT] 儲存失敗 ({label}): {type(e).__name__}: {e}")


GUI_RETRY_WAIT_TIME = 30
GUI_MAX_ATTEMPTS = 2        # attempts per phase (phase 1: normal retries; phase 2: after relay reboot)
GUI_TOTAL_ATTEMPTS = GUI_MAX_ATTEMPTS * 2  # trigger_web_action: 2 normal + relay reboot TSM4 + 2 more


def wait_loading_done(wait, timeout_note="loadingModal"):
    try:
        wait.until(EC.invisibility_of_element_located((By.ID, "loadingModal")))
    except Exception:
        # Detail only. loadingModal may not appear on every page; do not mark as STEP/RESULT.
        log_progress(f"等待 {timeout_note} 消失逾時或未出現，繼續流程")


def _js_click(driver, element, wait_after=1):
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    receive_monitor(0.5)
    driver.execute_script("arguments[0].click();", element)
    if wait_after:
        receive_monitor(wait_after)


def _try_login_if_needed(driver, wait):
    log_step(f"Web GUI: open {cfg.GATEWAY_URL}")
    driver.get(cfg.GATEWAY_URL)
    receive_monitor(2)

    try:
        short_wait = WebDriverWait(driver, 5)
        short_wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_WIFI_SETTINGS)))
        log_result("Web GUI login state: already logged in")
        log_progress("偵測到 Web GUI 已登入，略過登入流程。")
        return True
    except Exception:
        pass

    log_step("Web GUI: login required, submit credentials")
    log_progress("Web GUI 尚未登入，執行登入流程...")
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
    log_result("Web GUI login submitted")
    return True


def _handle_discard_if_present(driver):
    try:
        short_wait = WebDriverWait(driver, 3)
        discard_btn = short_wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_DISCARD_CLOSE_BTN)))
        log_step("Web GUI: discard pending changes dialog")
        log_progress("偵測到 Discard 視窗，點擊 Discard / Yes 繼續。")
        driver.execute_script("arguments[0].click();", discard_btn)
        receive_monitor(1.5)
        log_result("Web GUI: discard dialog handled")
        return True
    except Exception:
        return False


def navigate_to_boosters(driver):
    wait = WebDriverWait(driver, cfg.WAIT_TIMEOUT)
    log_step("Web GUI: navigate to Wi-Fi Boosters page")
    _try_login_if_needed(driver, wait)

    log_progress("進入 WiFi Settings -> Wi-Fi Mesh -> Wi-Fi Boosters")
    wifi_link = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_WIFI_SETTINGS)))
    _js_click(driver, wifi_link, wait_after=1)
    wait_loading_done(wait)
    receive_monitor(2)

    mesh_btn = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_WIFI_MESH)))
    _js_click(driver, mesh_btn, wait_after=1)
    _handle_discard_if_present(driver)
    wait_loading_done(wait)
    receive_monitor(1.5)

    boosters_btn = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_WIFI_BOOSTERS)))
    _js_click(driver, boosters_btn, wait_after=1)
    wait_loading_done(wait)
    receive_monitor(3)
    log_result("Web GUI: Wi-Fi Boosters page opened")


def navigate_to_maintenance(driver):
    wait = WebDriverWait(driver, cfg.WAIT_TIMEOUT)
    log_step("Web GUI: navigate to Maintenance page")
    _try_login_if_needed(driver, wait)

    log_progress("進入 Settings -> Maintenance")
    settings_link = wait.until(EC.element_to_be_clickable((By.XPATH, cfg.XPATH_SETTINGS)))
    _js_click(driver, settings_link, wait_after=1)
    wait_loading_done(wait)
    receive_monitor(1)

    maintenance_link = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_MAINTENANCE)))
    _js_click(driver, maintenance_link, wait_after=1)
    wait_loading_done(wait)
    receive_monitor(1)
    log_result("Web GUI: Maintenance page opened")


def _create_attempt_driver(passed_driver, attempt_index, action_label, total=None):
    total = total or GUI_MAX_ATTEMPTS
    if attempt_index == 1 and passed_driver is not None:
        log_step(f"Web GUI action: reuse existing Chrome session ({action_label}, attempt {attempt_index}/{total})")
        return passed_driver

    log_step(f"Web GUI action: create Chrome session ({action_label}, attempt {attempt_index}/{total})")
    driver = create_chrome_driver()
    if driver is None:
        log_result(f"Web GUI action FAIL: Chrome create failed ({action_label}, attempt {attempt_index}/{total})")
        log_progress(f"[Chrome] 建立失敗，無法執行 {action_label}")
    return driver


def _close_driver(driver):
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass


def _log_gui_exception(action_label, attempt_index, exc, total=None):
    total = total or GUI_MAX_ATTEMPTS
    log_result(f"Web GUI action FAIL: {action_label}, attempt {attempt_index}/{total}, reason={type(exc).__name__}: {exc}")
    log_progress(f"Web GUI 操作發生異常 ({action_label}, attempt {attempt_index}/{total}): {type(exc).__name__}: {exc}")


def _wait_before_retry(action_label):
    log_step(f"Web GUI action retry wait: {action_label}, wait={GUI_RETRY_WAIT_TIME}s")
    log_progress(f"Web GUI 異常，等待 {GUI_RETRY_WAIT_TIME} 秒後 retry 一次: {action_label}")
    receive_monitor(GUI_RETRY_WAIT_TIME)
    log_step(f"Web GUI action retry start: {action_label}")
    log_progress(f"Web GUI retry 開始: {action_label}")


def _relay_reboot_tsm4(action_label):
    """Power-cycle TSM4 via relay (relay off → wait → relay on → boot wait)."""
    from .relay import control_relay_channel
    port = int(getattr(cfg, "TSM4_POWER_RELAY_PORT", 2))
    off_wait = int(getattr(cfg, "TSM4_RELAY_REBOOT_OFF_WAIT", 8))
    boot_wait = int(getattr(cfg, "TSM4_GUI_RELAY_BOOT_WAIT", 300))
    log_step(f"Web GUI relay reboot TSM4: relay {port} off → {off_wait}s → relay {port} on → wait {boot_wait}s ({action_label})")
    log_progress(f"[RELAY REBOOT] TSM4 GUI 連續失敗，透過 relay {port} 重開 TSM4...")
    control_relay_channel(port, "off")
    receive_monitor(off_wait)
    control_relay_channel(port, "on")
    log_progress(f"[RELAY REBOOT] TSM4 relay on，等待 {boot_wait}s 開機完成再繼續 GUI...")
    receive_monitor(boot_wait)
    log_result(f"[RELAY REBOOT] TSM4 relay reboot 完成，繼續 GUI retry ({action_label})")


def trigger_tsm4_restart(passed_driver=None):
    """Trigger TSM4 restart from GUI.

    Workaround: if GUI navigation/action fails or Chrome crashes, wait 30 seconds
    and retry once with a fresh Chrome session. No automatic screenshot is saved.
    """
    import time

    for attempt_index in range(1, GUI_MAX_ATTEMPTS + 1):
        log_step(f"Web GUI action start: TSM4 Restart, attempt {attempt_index}/{GUI_MAX_ATTEMPTS}")
        driver = _create_attempt_driver(passed_driver, attempt_index, "TSM4 Restart")
        if driver is None:
            if attempt_index < GUI_MAX_ATTEMPTS:
                _wait_before_retry("TSM4 Restart")
                continue
            return False, None

        wait = WebDriverWait(driver, cfg.WAIT_TIMEOUT)
        try:
            navigate_to_maintenance(driver)
            log_step("Web GUI action: click TSM4 Restart button")
            log_progress("觸發 TSM4 Restart 動作...")
            restart_btn = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_TSM4_RESTART)))
            _js_click(driver, restart_btn, wait_after=1)

            log_step("Web GUI action: confirm YES for TSM4 Restart")
            log_progress("點擊確認視窗的 Yes...")
            confirm_yes = wait.until(EC.element_to_be_clickable((By.XPATH, cfg.XPATH_CONFIRM_YES)))
            driver.execute_script("arguments[0].click();", confirm_yes)
            action_start_time = time.time()
            receive_monitor(2)
            save_gui_screenshot(driver, f"{cfg.TEST_CASE_NAME}_tsm4_restart_confirmed")
            log_result("Web GUI action PASS: TSM4 Restart command submitted")
            log_progress("GUI 已確認送出 TSM4 Restart 訊號")
            log_step(f"Web GUI action: wait before closing Chrome, wait={cfg.TSM4_REBOOT_CHROME_CLOSE_WAIT}s")
            log_progress(f"等待 {cfg.TSM4_REBOOT_CHROME_CLOSE_WAIT}s 後關閉 Chrome...")
            receive_monitor(cfg.TSM4_REBOOT_CHROME_CLOSE_WAIT)
            return True, action_start_time

        except Exception as e:
            _log_gui_exception("TSM4 Restart", attempt_index, e)
            if attempt_index < GUI_MAX_ATTEMPTS:
                _wait_before_retry("TSM4 Restart")
            else:
                log_result("Web GUI action FAIL: TSM4 Restart exhausted retries")
                return False, None

        finally:
            _close_driver(driver)

    log_result("Web GUI action FAIL: TSM4 Restart exhausted retries")
    return False, None


def trigger_web_action(action_xpath, action_label, passed_driver=None):
    """Trigger a GUI action for Case6~Case9.

    Flow:
      - Attempt 1~2: normal GUI retries (30s wait between).
      - If both fail: relay power-cycle TSM4 (relay off → 8s → relay on → wait 300s).
      - Attempt 3~4: GUI retries with fresh Chrome after relay reboot.
    """
    import time

    for attempt_index in range(1, GUI_TOTAL_ATTEMPTS + 1):
        # Between phase 1 and phase 2: relay reboot TSM4
        if attempt_index == GUI_MAX_ATTEMPTS + 1:
            _relay_reboot_tsm4(action_label)

        log_step(f"Web GUI action start: {action_label}, attempt {attempt_index}/{GUI_TOTAL_ATTEMPTS}")
        driver = _create_attempt_driver(passed_driver, attempt_index, action_label, total=GUI_TOTAL_ATTEMPTS)
        if driver is None:
            # No retry wait before relay reboot (attempt GUI_MAX_ATTEMPTS → next is relay)
            if attempt_index < GUI_TOTAL_ATTEMPTS and attempt_index != GUI_MAX_ATTEMPTS:
                _wait_before_retry(action_label)
            continue

        wait = WebDriverWait(driver, cfg.WAIT_TIMEOUT)
        try:
            navigate_to_boosters(driver)
            log_step(f"Web GUI action: click action button ({action_label})")
            log_progress(f"觸發 {action_label} 動作...")
            action_btn = wait.until(EC.element_to_be_clickable((By.XPATH, action_xpath)))
            save_gui_screenshot(driver, f"{cfg.TEST_CASE_NAME}_{action_label.replace(' ', '_').replace('+', '_')}_before_click")
            _js_click(driver, action_btn, wait_after=1)

            log_step(f"Web GUI action: confirm YES ({action_label})")
            log_progress("點擊確認視窗的 Yes...")
            confirm_yes = wait.until(EC.element_to_be_clickable((By.XPATH, cfg.XPATH_CONFIRM_YES)))
            driver.execute_script("arguments[0].click();", confirm_yes)
            action_start_time = time.time()
            receive_monitor(2)
            safe_label = action_label.replace(" ", "_").replace("+", "_")
            save_gui_screenshot(driver, f"{cfg.TEST_CASE_NAME}_{safe_label}_confirmed")
            log_result(f"Web GUI action PASS: {action_label} command submitted")
            log_progress(f"GUI 已確認送出 {action_label} 訊號")
            return True, action_start_time

        except Exception as e:
            _log_gui_exception(action_label, attempt_index, e, total=GUI_TOTAL_ATTEMPTS)
            # No retry wait before relay reboot, and no wait after the last attempt
            if attempt_index < GUI_TOTAL_ATTEMPTS and attempt_index != GUI_MAX_ATTEMPTS:
                _wait_before_retry(action_label)

        finally:
            receive_monitor(3)
            _close_driver(driver)

    log_result(f"Web GUI action FAIL: {action_label} exhausted all {GUI_TOTAL_ATTEMPTS} attempts")
    return False, None
