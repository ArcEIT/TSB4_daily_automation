#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tsm4_gui_factory_default_standalone.py

Standalone TSM4 Web GUI Factory Default tool.
Optimized for GitHub Actions / Windows self-hosted runner:
  - force UTF-8 stdout/stderr to avoid mojibake in GitHub log
  - keep local log as UTF-8-SIG for Windows editor compatibility
  - add robust XPath/text fallback for Settings/Maintenance/Factory/Confirm buttons
  - save screenshot on failure only
  - support Action finished modal verification without mandatory Sign In redirect
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ==========================================================
# Encoding hardening for GitHub Actions on Windows
# ==========================================================

def _force_utf8_stdio() -> None:
    """Force Python console output to UTF-8.

    Windows self-hosted runners may default to cp950/cp1252. GitHub log viewer
    expects UTF-8, so Chinese text becomes mojibake unless stdout/stderr are
    explicitly reconfigured.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            pass


_force_utf8_stdio()

# ==========================================================
# Default config
# ==========================================================
GATEWAY_URL = "http://192.168.0.1/"
WAIT_TIMEOUT = 30
ROUTER_USERNAME = "admin"
ROUTER_PASSWORD = "jum8gf2zry"
CHROME_DRIVER_PATH = str(Path(__file__).resolve().parent / "chromedriver.exe")

# Primary XPath from current GUI
XPATH_LOGIN_USER = "/html/body/app-root/app-login/div/header/div[2]/form/div/div[1]/input"
XPATH_LOGIN_PASS = "/html/body/app-root/app-login/div/header/div[2]/form/div/div[2]/input"
XPATH_WIFI_SETTINGS = "/html/body/app-root/app-main-base/div/app-header/nav/div/div[2]/app-quick-links/div/div[3]/div/div/a/p"
XPATH_SETTINGS = "/html/body/app-root/app-main-base/div/app-header/nav/div/div[2]/app-quick-links/div/div[4]/div/div/a/p"
XPATH_MAINTENANCE = "/html/body/app-root/app-main-base/div/div/main/app-mybox-main/div/div/app-top-menu/nav/div/ul/li[8]/a"
XPATH_TSM4_FACTORY = "/html/body/app-root/app-main-base/div/div/main/app-mybox-main/div/div/app-maintenace-main/div/div/app-maintenance-resets/form/div/div[3]/div[2]/button"
XPATH_CONFIRM_YES = "/html/body/ngb-modal-window/div/div/app-generic-modal/div[3]/button[2]"

# Text/semantic fallbacks. Keep them broad because Angular DOM often shifts.
XPATH_SETTINGS_FALLBACKS = [
    XPATH_SETTINGS,
    "//*[self::a or self::button or self::p or self::span][normalize-space()='Settings']",
    "//*[contains(normalize-space(.), 'Settings') and (self::a or self::button or self::p or self::span)]",
]
XPATH_MAINTENANCE_FALLBACKS = [
    XPATH_MAINTENANCE,
    "//*[self::a or self::button or self::li or self::span][contains(normalize-space(.), 'Maintenance')]",
]
XPATH_FACTORY_FALLBACKS = [
    XPATH_TSM4_FACTORY,
    "//button[contains(normalize-space(.), 'Factory Default')]",
    "//*[self::button or self::a][contains(normalize-space(.), 'Factory')]",
]
XPATH_CONFIRM_YES_FALLBACKS = [
    XPATH_CONFIRM_YES,
    "//ngb-modal-window//button[normalize-space()='Yes']",
    "//ngb-modal-window//*[self::button or self::a][contains(normalize-space(.), 'Yes')]",
    "//button[normalize-space()='Yes']",
]


# Some TSM4 firmware versions do not reboot immediately after Factory Settings.
# Instead they show an "Action finished" modal and redirect to Sign In only after OK.
XPATH_ACTION_FINISHED_MODAL_FALLBACKS = [
    "//ngb-modal-window//*[contains(normalize-space(.), 'Action finished')]",
    "//*[contains(normalize-space(.), 'Action finished')]",
    "//*[contains(normalize-space(.), 'will be redirected to the page') and contains(normalize-space(.), 'Sign In')]",
]
XPATH_ACTION_FINISHED_OK_FALLBACKS = [
    "//ngb-modal-window//button[normalize-space()='OK']",
    "//ngb-modal-window//*[self::button or self::a][normalize-space()='OK']",
    "//button[normalize-space()='OK']",
]

LOG_FILE = ""


def init_log_file(test_name: str) -> None:
    global LOG_FILE
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    LOG_FILE = f"{ts}_{test_name}.log"


def log(message: str) -> None:
    ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")[:-3] + "]"
    line = f"{ts} [GUI] {message}"
    print(line, flush=True)
    if LOG_FILE:
        # utf-8-sig keeps Windows Notepad/Excel-like viewers happier while GitHub
        # still displays it correctly.
        with open(LOG_FILE, "a", encoding="utf-8-sig") as f:
            f.write(line + "\n")


def log_separator(message: str) -> None:
    border = "=" * 70
    log(border)
    log(message)
    log(border)


def gui_sleep(seconds: float) -> None:
    time.sleep(max(0.0, float(seconds)))


def _save_screenshot(driver: Optional[webdriver.Chrome], prefix: str) -> None:
    if driver is None:
        return
    try:
        name = f"{prefix}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        driver.save_screenshot(name)
        log(f"已儲存截圖: {name}")
    except Exception as e:
        log(f"儲存截圖失敗: {type(e).__name__}: {e}")


def element_exists(driver: webdriver.Chrome, by: str, locator: str, timeout: float = 2.0) -> bool:
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            if driver.find_elements(by, locator):
                return True
        except Exception:
            pass
        gui_sleep(0.2)
    return False


def check_login_success(driver: webdriver.Chrome) -> bool:
    """Return True only when logged-in markers are visible and login form is gone."""
    success_markers = [
        (By.XPATH, XPATH_WIFI_SETTINGS),
        (By.XPATH, XPATH_SETTINGS),
        (By.XPATH, "//*[contains(normalize-space(.), 'Logout') or contains(normalize-space(.), 'Log out') or contains(normalize-space(.), 'Sign out')]"),
        (By.XPATH, "//*[contains(normalize-space(.), 'Settings')]"),
        (By.XPATH, "//*[contains(normalize-space(.), 'Status') or contains(normalize-space(.), 'Dashboard') or contains(normalize-space(.), 'Overview')]"),
    ]

    login_form_visible = element_exists(driver, By.XPATH, XPATH_LOGIN_USER, timeout=0.8)
    found_success = any(element_exists(driver, by, locator, timeout=0.8) for by, locator in success_markers)

    if found_success and not login_form_visible:
        return True

    if login_form_visible:
        raise RuntimeError("登入後仍偵測到 login form，判斷為登入失敗或 session 未建立")
    raise RuntimeError("登入後找不到 login success marker，可能 GUI 未載入完成或 XPath/landing page 變更")

def _js_click(driver: webdriver.Chrome, element) -> None:
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    gui_sleep(0.2)
    driver.execute_script("arguments[0].click();", element)


def _wait_click_any(driver: webdriver.Chrome, wait: WebDriverWait, xpaths: Iterable[str], note: str):
    last_error = None
    for xpath in xpaths:
        try:
            elem = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
            _js_click(driver, elem)
            log(f"{note}: clicked by xpath={xpath}")
            return elem
        except Exception as e:
            last_error = e
    raise RuntimeError(f"{note}: element not clickable by all fallback XPath. Last={type(last_error).__name__}: {last_error}")


def _wait_present_any(driver: webdriver.Chrome, wait: WebDriverWait, xpaths: Iterable[str], note: str):
    last_error = None
    for xpath in xpaths:
        try:
            elem = wait.until(EC.presence_of_element_located((By.XPATH, xpath)))
            log(f"{note}: found by xpath={xpath}")
            return elem
        except Exception as e:
            last_error = e
    raise RuntimeError(f"{note}: element not found by all fallback XPath. Last={type(last_error).__name__}: {last_error}")


def create_chrome_driver(chromedriver_path: str, headless: bool) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
    else:
        options.add_argument("--start-maximized")

    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    options.add_argument("--disable-gpu")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-dev-shm-usage")

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        driver_path = ChromeDriverManager().install()
        log(f"[Chrome] 使用 webdriver-manager: {driver_path}")
        log(f"[Chrome] headless mode: {headless}")
        service = Service(executable_path=driver_path)
    except Exception as e:
        log(f"[Chrome] webdriver-manager 失敗: {e}，嘗試本地 chromedriver")
        if os.path.exists(chromedriver_path):
            log(f"[Chrome] 使用本地 chromedriver: {chromedriver_path}")
            log(f"[Chrome] headless mode: {headless}")
            service = Service(executable_path=chromedriver_path)
        else:
            raise FileNotFoundError(f"找不到 chromedriver 且 webdriver-manager 失敗: {e}") from e

    return webdriver.Chrome(service=service, options=options)


def wait_loading_done(driver: webdriver.Chrome, timeout: int = 10) -> None:
    try:
        WebDriverWait(driver, timeout).until(EC.invisibility_of_element_located((By.ID, "loadingModal")))
    except Exception:
        pass


def http_reachable(url: str, timeout: float = 3.0) -> bool:
    """Return True when Web GUI HTTP service responds.

    Note:
      ICMP ping may stay alive during some bridge/LAN states. HTTP reachability
      is a better signal for Web GUI reboot/reset flow.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "TSM4FactoryDefaultVerify/1.0",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Treat HTTP 2xx/3xx/4xx as reachable because the web server answered.
            return 200 <= int(resp.status) < 500
    except Exception:
        return False


def wait_http_state(url: str, expected_reachable: bool, timeout: int, interval: float, note: str) -> bool:
    """Poll Web GUI HTTP state until it matches expected_reachable."""
    log(f"[VERIFY] {note}: expect_reachable={expected_reachable}, timeout={timeout}s, interval={interval}s, url={url}")
    start = time.time()
    last_state = None

    while time.time() - start < timeout:
        reachable = http_reachable(url, timeout=3.0)
        if reachable != last_state:
            log(f"[VERIFY] HTTP reachable={reachable}, elapsed={time.time() - start:.1f}s")
            last_state = reachable

        if reachable == expected_reachable:
            log(f"[VERIFY] {note}: PASS, elapsed={time.time() - start:.1f}s")
            return True

        gui_sleep(interval)

    log(f"[VERIFY] {note}: TIMEOUT, last_reachable={last_state}")
    return False



def wait_action_finished_modal(driver: webdriver.Chrome, wait: WebDriverWait, timeout: int = 120) -> None:
    """Wait until TSM4 reports that the reset action has finished."""
    log(f"[VERIFY] 等待 Action finished 視窗，timeout={timeout}s")
    short_wait = WebDriverWait(driver, timeout)
    _wait_present_any(driver, short_wait, XPATH_ACTION_FINISHED_MODAL_FALLBACKS, "Action finished modal")
    log("[VERIFY] 偵測到 Action finished 視窗")


def click_action_finished_ok(driver: webdriver.Chrome, wait: WebDriverWait) -> None:
    """Click OK on the Action finished modal so GUI redirects to Sign In."""
    log("[VERIFY] 點擊 Action finished 視窗 OK")
    _wait_click_any(driver, wait, XPATH_ACTION_FINISHED_OK_FALLBACKS, "Action finished OK")


def wait_redirect_to_login(driver: webdriver.Chrome, timeout: int = 60) -> None:
    """Verify that GUI returns to login page after OK is clicked."""
    log(f"[VERIFY] 等待重新導向 Sign In/Login page，timeout={timeout}s")
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            url = driver.current_url
            login_form = bool(driver.find_elements(By.XPATH, XPATH_LOGIN_USER))
            log(f"[VERIFY] redirect check: url={url}, login_form={login_form}")
            if "login" in url.lower() or login_form:
                log("[VERIFY] 已回到 Sign In/Login page")
                return
        except Exception as e:
            log(f"[VERIFY] redirect check error: {type(e).__name__}: {e}")
        gui_sleep(2)
    raise RuntimeError("Action finished OK 後未在 timeout 內回到 Sign In/Login page")


def verify_factory_default_by_gui_completion(driver: webdriver.Chrome, wait: WebDriverWait, args: argparse.Namespace) -> None:
    """Verify the TSM4 GUI reset completion modal flow.

    Current observed TSM4 behavior:
      1. Click Factory Settings Reset.
      2. Click Yes.
      3. GUI shows Action finished after the reset action completes.
      4. Click OK if enabled.

    Important:
      - Some firmware does not redirect to Sign In after OK even though Factory
        Default has already completed.
      - Therefore redirect-to-login is optional and disabled by default.
      - HTTP/ping may stay reachable during this flow, so HTTP down/up is not a
        mandatory pass criterion for this firmware behavior.
    """
    wait_action_finished_modal(driver, wait, timeout=args.action_finished_timeout)

    if args.click_action_finished_ok:
        click_action_finished_ok(driver, wait)
        gui_sleep(1)
    else:
        log("[VERIFY] 保留 Action finished 視窗，不點 OK")

    if args.verify_redirect_login:
        wait_redirect_to_login(driver, timeout=args.redirect_login_timeout)
        log("[VERIFY] Factory Default GUI completion modal + redirect 驗證 PASS")
    else:
        log("[VERIFY] 已偵測 Action finished，略過 Sign In/Login redirect check，判定 Factory Default GUI action 完成")


def verify_factory_default_by_http(args: argparse.Namespace) -> None:
    """Verify that Factory Default actually triggers Web GUI down/up cycle."""
    if args.verify_initial_delay > 0:
        log(f"[VERIFY] Factory Default 送出後先等待 {args.verify_initial_delay} 秒再檢查 Web GUI 是否中斷")
        gui_sleep(args.verify_initial_delay)

    down_ok = wait_http_state(
        args.gateway_url,
        expected_reachable=False,
        timeout=args.verify_down_timeout,
        interval=args.verify_interval,
        note="等待 Web GUI 中斷，確認 DUT 開始 reboot/reset",
    )
    if not down_ok:
        raise RuntimeError(
            "Factory Default 送出後 Web GUI 未中斷；疑似沒有真的觸發 reset/reboot，"
            "或這個按鈕不是預期的整機 Factory Default。"
        )

    up_ok = wait_http_state(
        args.gateway_url,
        expected_reachable=True,
        timeout=args.verify_up_timeout,
        interval=args.verify_interval,
        note="等待 Web GUI 恢復",
    )
    if not up_ok:
        raise RuntimeError("Factory Default 後 Web GUI 未在 timeout 內恢復")

    log("[VERIFY] Factory Default reboot/reset HTTP down/up 驗證 PASS")


def js_set_input_value(driver: webdriver.Chrome, element, text: str) -> None:
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


def _wait_login_form_gone(driver: webdriver.Chrome, timeout: float = 20.0) -> None:
    """Poll until the login form input disappears from DOM after submit.

    Angular SPA routing keeps the login component mounted briefly while
    transitioning to the home route.  A fixed sleep races against this;
    polling for DOM removal is deterministic.
    """
    log(f"等待 login form 從 DOM 移除 (Angular routing)，timeout={timeout}s")
    end_time = time.time() + timeout
    while time.time() < end_time:
        if not element_exists(driver, By.XPATH, XPATH_LOGIN_USER, timeout=0.3):
            gui_sleep(1.0)  # small buffer for home page widgets to render
            return
        gui_sleep(0.5)
    log("警告: login form 在 timeout 內未從 DOM 移除，嘗試繼續...")


def try_login_if_needed(driver: webdriver.Chrome, wait: WebDriverWait, url: str, username: str, password: str) -> None:
    driver.get(url)
    gui_sleep(2)
    wait_loading_done(driver)

    try:
        if check_login_success(driver):
            log("偵測到 Web GUI 已登入，略過登入流程。")
            return
    except Exception:
        # Login page is expected here when the session is not established yet.
        pass

    log("Web GUI 尚未登入，執行登入流程...")
    user_input = wait.until(EC.element_to_be_clickable((By.XPATH, XPATH_LOGIN_USER)))
    user_input.clear()
    user_input.send_keys(username)
    gui_sleep(0.3)

    pass_input = wait.until(EC.presence_of_element_located((By.XPATH, XPATH_LOGIN_PASS)))
    pass_input.clear()
    js_set_input_value(driver, pass_input, password)
    gui_sleep(0.5)

    submit_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
    _js_click(driver, submit_btn)
    wait_loading_done(driver, timeout=15)

    # Wait for Angular to unmount login form before checking success.
    # Replacing fixed gui_sleep(2) which raced against Angular route transition.
    _wait_login_form_gone(driver, timeout=20)

    check_login_success(driver)
    log("Web GUI login PASS")

def navigate_to_maintenance(driver: webdriver.Chrome, wait: WebDriverWait, url: str, username: str, password: str) -> None:
    try_login_if_needed(driver, wait, url, username, password)

    log("進入 Settings -> Maintenance")
    _wait_click_any(driver, wait, XPATH_SETTINGS_FALLBACKS, "Settings")
    gui_sleep(1)
    wait_loading_done(driver, timeout=15)

    _wait_click_any(driver, wait, XPATH_MAINTENANCE_FALLBACKS, "Maintenance")
    gui_sleep(1)
    wait_loading_done(driver, timeout=15)


def _run_factory_default_once(args: argparse.Namespace) -> bool:
    driver = None
    try:
        driver = create_chrome_driver(args.chromedriver, args.headless)
        wait = WebDriverWait(driver, args.wait_timeout)

        navigate_to_maintenance(driver, wait, args.gateway_url, args.username, args.password)

        log("點擊 TSM4 Factory Default button")
        factory_btn = _wait_present_any(driver, wait, XPATH_FACTORY_FALLBACKS, "Factory Default button")
        _js_click(driver, factory_btn)
        gui_sleep(1)

        log("點擊確認視窗 Yes")
        _wait_click_any(driver, wait, XPATH_CONFIRM_YES_FALLBACKS, "Confirm YES")

        log("TSM4 Factory Default 已送出")

        if args.verify_gui_completion:
            verify_factory_default_by_gui_completion(driver, wait, args)

        if args.post_confirm_wait > 0:
            log(f"等待 {args.post_confirm_wait} 秒後關閉 Chrome...")
            gui_sleep(args.post_confirm_wait)

        if args.verify_http_downup:
            verify_factory_default_by_http(args)
        elif args.close_wait > 0:
            log(f"等待 {args.close_wait} 秒後關閉 Chrome...")
            gui_sleep(args.close_wait)

        return True

    except Exception as e:
        log(f"TSM4 Factory Default 執行失敗: {type(e).__name__}: {e}")
        if args.traceback:
            for line in traceback.format_exc().splitlines():
                log(f"[TRACEBACK] {line}")
        _save_screenshot(driver, "factory_default_error")
        return False

    finally:
        if driver is not None:
            try:
                driver.quit()
                log("Chrome 已關閉")
            except Exception:
                pass


def run_factory_default(args: argparse.Namespace) -> bool:
    max_attempts = args.max_retries + 1
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            log(f"[RETRY] 第 {attempt}/{max_attempts} 次嘗試 (等待 {args.retry_delay}s)...")
            gui_sleep(args.retry_delay)

        log_separator(f"TSM4 GUI Factory Default Start (attempt {attempt}/{max_attempts})")
        ok = _run_factory_default_once(args)
        if ok:
            log_separator("TSM4 GUI Factory Default Done")
            return True

        if attempt < max_attempts:
            log(f"[RETRY] 第 {attempt} 次失敗，準備重試...")

    log(f"[RETRY] 所有 {max_attempts} 次嘗試均失敗")
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone TSM4 GUI Factory Default tool, no common.py dependency")
    parser.add_argument("--gateway-url", default=GATEWAY_URL, help="TSM4 Web GUI URL")
    parser.add_argument("--username", default=ROUTER_USERNAME, help="TSM4 Web GUI username")
    parser.add_argument("--password", default=ROUTER_PASSWORD, help="TSM4 Web GUI password")
    parser.add_argument("--chromedriver", default=CHROME_DRIVER_PATH, help="chromedriver.exe path")
    parser.add_argument("--wait-timeout", type=int, default=WAIT_TIMEOUT, help="Selenium WebDriverWait timeout seconds")
    parser.add_argument("--close-wait", type=float, default=5, help="Seconds to wait after confirm YES before closing Chrome when --no-verify-reset is used")
    parser.add_argument("--traceback", action="store_true", help="Print full traceback on failure")
    parser.add_argument("--verify-gui-completion", dest="verify_gui_completion", action="store_true", help="After Factory Default YES, verify Action finished modal. Does not require Sign In redirect by default")
    parser.add_argument("--no-verify-gui-completion", dest="verify_gui_completion", action="store_false", help="Do not verify Action finished modal")
    parser.add_argument("--verify-http-downup", dest="verify_http_downup", action="store_true", help="Also verify Web GUI HTTP goes down and comes back. Disabled by default because observed TSM4 flow may keep HTTP reachable.")
    parser.add_argument("--no-verify-http-downup", dest="verify_http_downup", action="store_false", help="Do not verify Web GUI HTTP down/up")
    parser.add_argument("--action-finished-timeout", type=int, default=120, help="Seconds to wait for Action finished modal after Factory Default YES")
    parser.add_argument("--redirect-login-timeout", type=int, default=60, help="Seconds to wait for Sign In/Login page after clicking Action finished OK")
    parser.add_argument("--verify-redirect-login", dest="verify_redirect_login", action="store_true", help="Optionally require Sign In/Login redirect after clicking Action finished OK. Disabled by default")
    parser.add_argument("--no-verify-redirect-login", dest="verify_redirect_login", action="store_false", help="Do not require Sign In/Login redirect after Action finished OK")
    parser.add_argument("--click-action-finished-ok", dest="click_action_finished_ok", action="store_true", help="Click OK on the Action finished modal")
    parser.add_argument("--no-click-action-finished-ok", dest="click_action_finished_ok", action="store_false", help="Leave the Action finished modal open")
    parser.add_argument("--verify-initial-delay", type=float, default=10, help="Seconds to wait after YES before polling Web GUI down state")
    parser.add_argument("--verify-down-timeout", type=int, default=90, help="Seconds to wait for Web GUI to become unreachable after Factory Default")
    parser.add_argument("--verify-up-timeout", type=int, default=240, help="Seconds to wait for Web GUI to become reachable again after Factory Default")
    parser.add_argument("--verify-interval", type=float, default=5, help="Polling interval seconds for reset verification")
    parser.add_argument("--post-confirm-wait", type=float, default=0, help="Extra seconds to wait after completion verification before closing Chrome")

    parser.add_argument("--max-retries", type=int, default=1, help="Maximum retry attempts on failure (default 1 = one retry on fail)")
    parser.add_argument("--retry-delay", type=float, default=15, help="Seconds to wait between retry attempts")

    # GitHub Actions should run headless by default. Local manual execution keeps GUI visible.
    headless_default = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    parser.add_argument("--headless", dest="headless", action="store_true", help="Run Chrome in headless mode")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="Run Chrome with visible browser")
    parser.set_defaults(headless=headless_default, verify_gui_completion=True, verify_http_downup=False, verify_redirect_login=False, click_action_finished_ok=True)
    return parser.parse_args()


def main() -> int:
    init_log_file("tsm4_gui_factory_default")
    args = parse_args()
    ok = run_factory_default(args)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
