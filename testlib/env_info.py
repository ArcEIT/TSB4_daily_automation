"""Router/Booster firmware collection helpers."""
import datetime
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from . import config as cfg
from .logger import log_progress, log_step, log_result
from .serial_console import receive_monitor, get_serial_for_command, _SERIAL_IO_LOCK
from .ssh_client import run_ssh_command, discover_ssh_host_by_serial


def create_chrome_driver():
    options = webdriver.ChromeOptions()
    running_in_github = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    if running_in_github:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
    else:
        options.add_argument("--start-maximized")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    options.add_argument("--disable-gpu")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        driver_path = ChromeDriverManager().install()
        log_progress(f"[Chrome] 使用 webdriver-manager: {driver_path}")
        service = Service(executable_path=driver_path)
    except Exception as e:
        log_progress(f"[Chrome] webdriver-manager 失敗: {e}，嘗試本地 chromedriver")
        if os.path.exists(cfg.CHROME_DRIVER_PATH):
            log_progress(f"[Chrome] 使用本地 chromedriver: {cfg.CHROME_DRIVER_PATH}")
            service = Service(executable_path=cfg.CHROME_DRIVER_PATH)
        else:
            log_progress(f"[Chrome] 本地 chromedriver 不存在: {cfg.CHROME_DRIVER_PATH}")
            return None

    return webdriver.Chrome(service=service, options=options)


def safe_driver_get(driver, url, retry=None, retry_wait=None):
    retry = cfg.WEB_GUI_OPEN_RETRY if retry is None else retry
    retry_wait = cfg.WEB_GUI_OPEN_RETRY_WAIT if retry_wait is None else retry_wait
    last_error = None
    for attempt in range(1, retry + 1):
        try:
            log_progress(f"[Chrome] 開啟 Web GUI: {url}，attempt {attempt}/{retry}")
            driver.get(url)
            return True, "None"
        except Exception as e:
            last_error = e
            log_progress(f"[Chrome] Web GUI 開啟失敗 attempt {attempt}/{retry}: {type(e).__name__}: {e}")
            if attempt < retry:
                receive_monitor(retry_wait)
    return False, f"{type(last_error).__name__}: {last_error}"


def get_router_fw_version():
    log_step("收集環境資訊: 透過 Web GUI 獲取 Router Firmware Version")
    driver = create_chrome_driver()
    if driver is None:
        log_result("Router Firmware Version: Unknown_Router_FW (chromedriver not found)")
        return "Unknown_Router_FW", None

    wait = WebDriverWait(driver, cfg.WAIT_TIMEOUT)
    version = "Unknown_Router_FW"
    try:
        open_ok, open_reason = safe_driver_get(driver, cfg.GATEWAY_URL)
        if not open_ok:
            log_progress(f"[環境資訊] Web GUI 開啟失敗，略過 Router FW 取得: {open_reason}")
            log_result(f"Router Firmware Version: Unknown_Router_FW ({open_reason})")
            return "Unknown_Router_FW", driver

        receive_monitor(2)
        fw_element = wait.until(EC.presence_of_element_located((By.XPATH, cfg.XPATH_ROUTER_FW)))
        version_text = fw_element.text.strip()
        if version_text in ("Firmware:", ""):
            try:
                fw_element = driver.find_element(By.XPATH, cfg.XPATH_ROUTER_FW.replace("span[2]", "span[3]"))
                version_text = fw_element.text.strip()
            except Exception as inner_e:
                log_progress(f"[環境資訊] span[3] 失敗: {inner_e}")
        version = version_text.replace("Firmware:", "").strip()
        version = version if version else "Unknown_Router_FW"
        log_result(f"Router Firmware Version: {version}")
    except Exception as e:
        log_progress(f"[環境資訊] 獲取 Router FW 失敗: {type(e).__name__}: {e}")
        log_result(f"Router Firmware Version: Unknown_Router_FW ({type(e).__name__})")
        try:
            screenshot_name = f"Router_FW_Error_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            driver.save_screenshot(screenshot_name)
            log_progress(f"[環境資訊] Router FW 失敗截圖: {screenshot_name}")
        except Exception as se:
            log_progress(f"[環境資訊] Router FW 截圖失敗: {se}")
    return version if version else "Unknown_Router_FW", driver


def _parse_booster_fw_version(output):
    """Extract Booster firmware version from command output."""
    if not output:
        return None
    for raw_line in output.replace("\r", "").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if "uci get" in line or line.startswith("root@") or line.startswith("#"):
            continue
        # Current TSB4 firmware format, e.g. TSB4.2.06.00_RD-260610daily.
        if "TSB" in line:
            return line
    return None


def _get_booster_fw_version_via_serial():
    log_step(f"收集環境資訊: SSH 取得 Booster FW 未成功，fallback Serial ({cfg.BOOSTER_PORT})")
    ser = None
    close_after_use = False
    try:
        ser, close_after_use = get_serial_for_command()
        # Hold the serial lock across write + read so the background serial logger
        # does not consume the firmware response before this parser sees it.
        with _SERIAL_IO_LOCK:
            ser.write(b"\r\n")
            receive_monitor(1, ser)
            ser.write(b"uci get glb-cfg.device_info.SoftwareVersion\n")
            raw_data = receive_monitor(3, ser)
        version = _parse_booster_fw_version(raw_data)
        if version:
            log_result(f"Booster Firmware Version via Serial: {version}")
            return version
        log_result("Booster Firmware Version: Unknown_Booster_FW (serial parse failed)")
        return "Unknown_Booster_FW"
    except Exception as e:
        log_progress(f"[環境資訊] Serial 獲取 Booster FW 失敗: {type(e).__name__}: {e}")
        log_result(f"Booster Firmware Version: Unknown_Booster_FW ({type(e).__name__})")
        return "Unknown_Booster_FW"
    finally:
        if close_after_use and ser is not None:
            try:
                ser.close()
            except Exception:
                pass


def get_booster_fw_version():
    """Get Booster firmware version by SSH first, then serial fallback.

    SSH is preferred because the full-session background serial logger may also
    be reading COM for RD debug logs. SSH output is isolated and therefore more
    reliable for Summary.log environment information.
    """
    log_step("收集環境資訊: 透過 SSH 優先取得 Booster Firmware Version")

    host = None
    try:
        log_step("收集環境資訊: 透過 serial 取得 Booster br-lan IPv4")
        host = discover_ssh_host_by_serial(force=True, log_prefix="[ENV][SSH]")
        if host:
            ok, output, reason = run_ssh_command(
                host,
                "uci get glb-cfg.device_info.SoftwareVersion",
                timeout=getattr(cfg, "ONBOARDING_SSH_TIMEOUT", 5),
            )
            if ok:
                version = _parse_booster_fw_version(output)
                if version:
                    log_result(f"Booster Firmware Version via SSH ({host}): {version}")
                    return version
                log_progress(f"[環境資訊] SSH ({host}) 已連線但未解析到 Booster FW。")
            else:
                log_progress(f"[環境資訊] SSH 取得 Booster FW 失敗 ({host}): {reason}")
        else:
            log_progress("[環境資訊] 尚未取得 RE SSH IPv4，無法用 SSH 取得 Booster FW。")
    except Exception as e:
        log_progress(f"[環境資訊] SSH 獲取 Booster FW 發生異常: {type(e).__name__}: {e}")

    return _get_booster_fw_version_via_serial()


def get_environment_fw_versions_close_browser():
    log_step("收集環境資訊: start")
    router_fw = "Unknown_Router_FW"
    driver = None
    try:
        router_fw, driver = get_router_fw_version()
    finally:
        if driver is not None:
            try:
                driver.quit()
                log_progress("[環境資訊] GW FW 取得完成，Chrome 已關閉。")
            except Exception as e:
                log_progress(f"[環境資訊] 關閉 Chrome 失敗: {e}")
    booster_fw = get_booster_fw_version()
    log_result(f"環境資訊完成: Router_FW={router_fw}, Booster_FW={booster_fw}")
    return router_fw, booster_fw
