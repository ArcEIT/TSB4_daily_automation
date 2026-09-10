import paramiko
import serial
import requests
import re
import time
import os  # 用於處理本地檔案刪除
import datetime
import sys
import io
import traceback

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
except Exception:
    pass

# ==================== 參數設定區域 ====================
# SFTP 設定 (對齊您跑通的 TSB4_fw_download.py 設定)
SFTP_HOST = "arc-sftp.arcadyan.com.tw"
SFTP_PORT = 22
SFTP_USER = "arctaxbooster4"
SFTP_PASS = "%C82B5B3"
BASE_ROOT = "/TA_booster4/DailyBuild_SPF12.5/"

# Serial & 登入設定
COM_PORT = 'COM8'
BAUD_RATE = 115200
WEB_USER = "rootadmin"
WEB_PASS = "root!@#"

LOCAL_FW_NAME = "TSB4_daily_fw.bin"

# Log 檔設定
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = SCRIPT_DIR
RUN_LOG_PATH = None
_LOG_FH = None
# ======================================================


def _ts():
    now = datetime.datetime.now()
    return now.strftime("[%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}]"


def init_log_file():
    """建立每次執行專用 log 檔，位置固定在 automation root/script 同層。"""
    global RUN_LOG_PATH, _LOG_FH

    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    RUN_LOG_PATH = os.path.join(LOG_DIR, f"fw_upgrade_{timestamp}.log")
    _LOG_FH = open(RUN_LOG_PATH, "a", encoding="utf-8", buffering=1)
    return RUN_LOG_PATH


def close_log_file():
    """關閉 log file handle。"""
    global _LOG_FH

    if _LOG_FH:
        try:
            _LOG_FH.flush()
            _LOG_FH.close()
        except Exception:
            pass
        _LOG_FH = None


def _write_log(level, message):
    """同一行 log 同步輸出到 console 與檔案。"""
    line = f"{_ts()} [{level}] >>> {message}"
    print(line, flush=True)

    if _LOG_FH:
        try:
            _LOG_FH.write(line + "\n")
            _LOG_FH.flush()
        except Exception:
            # 避免 log 檔寫入失敗影響 FW upgrade 主流程。
            pass


def log_progress(message):
    _write_log("PROGRESS", message)


def log_step(message):
    _write_log("PROGRESS-STEP", message)


def log_result(message):
    _write_log("PROGRESS-RESULT", message)


def _is_valid_ipv4(ip):
    try:
        parts = [int(x) for x in str(ip).split('.')]
    except Exception:
        return False
    if len(parts) != 4:
        return False
    if any(p < 0 or p > 255 for p in parts):
        return False
    if parts[3] in (0, 255):
        return False
    return True


def step1_sftp_download():
    """下載前先刪除舊檔，再從 SFTP 下載最新 FW。"""
    log_step(f"FW upgrade: prepare local firmware file ({LOCAL_FW_NAME})")

    if os.path.exists(LOCAL_FW_NAME):
        try:
            log_step(f"FW upgrade: remove old local firmware file ({LOCAL_FW_NAME})")
            os.remove(LOCAL_FW_NAME)
            log_result(f"FW upgrade: old firmware file removed ({LOCAL_FW_NAME})")
        except Exception as e:
            log_result(f"FW upgrade FAIL: remove old firmware file failed, reason={type(e).__name__}: {e}")
            return False

    transport = None
    try:
        log_step(f"FW upgrade: connect SFTP server ({SFTP_HOST}:{SFTP_PORT})")
        transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
        transport.connect(username=SFTP_USER, password=SFTP_PASS)
        sftp = paramiko.SFTPClient.from_transport(transport)
        log_result("FW upgrade: SFTP login PASS")

        log_step(f"FW upgrade: find latest daily build folder under {BASE_ROOT}")
        dirs = sftp.listdir(BASE_ROOT)
        date_dirs = [d for d in dirs if d.startswith("202")]
        if not date_dirs:
            log_result("FW upgrade FAIL: no daily build date folder found")
            return False

        def _folder_sort_key(name):
            # New format: 2026-08-20_03-00-01 -> comparable as-is (ISO-like)
            m = re.match(r'^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})$', name)
            if m:
                return name
            # Old format: 2026-0819 -> normalize to 2026-08-19_ (underscore ensures old < new same day)
            m = re.match(r'^(\d{4})-(\d{2})(\d{2})$', name)
            if m:
                return f"{m.group(1)}-{m.group(2)}-{m.group(3)}_"
            return name

        date_dirs = sorted(date_dirs, key=_folder_sort_key)
        latest_dir = date_dirs[-1]
        log_result(f"FW upgrade: latest daily build folder = {latest_dir}")

        date_folder = f"{BASE_ROOT}{latest_dir}/"
        log_step(f"FW upgrade: list subfolders under {date_folder}")
        sub_dirs = sftp.listdir(date_folder)
        fw_sub = next((d for d in sub_dirs if "FW" in d), None)
        if not fw_sub:
            log_result(f"FW upgrade FAIL: no FW subfolder found under {date_folder}, found={sub_dirs}")
            return False
        target_path = f"{date_folder}{fw_sub}/"
        log_step(f"FW upgrade: search ArcSigned firmware under {target_path}")
        files = sftp.listdir(target_path)
        fw_files = [f for f in files if "ArcSigned" in f and f.endswith(".bin")]

        if not fw_files:
            log_result("FW upgrade FAIL: no ArcSigned .bin firmware found")
            return False

        target_fw = fw_files[0]
        log_result(f"FW upgrade: target firmware found = {target_fw}")

        log_step(f"FW upgrade: download firmware to {LOCAL_FW_NAME}")
        sftp.get(f"{target_path}{target_fw}", LOCAL_FW_NAME)

        sftp.close()
        transport.close()
        transport = None
        log_result(f"FW upgrade: firmware download PASS ({LOCAL_FW_NAME})")
        return True
    except Exception as e:
        log_result(f"FW upgrade FAIL: SFTP download failed, reason={type(e).__name__}: {e}")
        if transport:
            try:
                transport.close()
            except Exception:
                pass
        return False


# Serial IP discovery tuning
SERIAL_DISCOVERY_RETRY = 3
SERIAL_COMMAND_WAIT_SEC = 3.0
SERIAL_READ_INTERVAL_SEC = 0.1


def _strip_ansi(text):
    """Remove common ANSI escape sequences from serial console output."""
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text or "")


def _read_serial_for(ser, wait_sec):
    """Read serial data for a fixed period instead of only reading ser.in_waiting once."""
    end_time = time.monotonic() + wait_sec
    chunks = []

    while time.monotonic() < end_time:
        waiting = ser.in_waiting
        if waiting:
            chunks.append(ser.read(waiting))
        else:
            time.sleep(SERIAL_READ_INTERVAL_SEC)

    return _strip_ansi(b"".join(chunks).decode("utf-8", errors="ignore"))


def _send_serial_command(ser, command, wait_sec=SERIAL_COMMAND_WAIT_SEC):
    """Send one console command and collect its output."""
    try:
        ser.reset_input_buffer()
    except Exception:
        pass

    ser.write(b"\r\n")
    time.sleep(0.2)
    _read_serial_for(ser, 0.3)  # drain prompt/echo before command

    ser.write((command + "\r\n").encode("utf-8"))
    return _read_serial_for(ser, wait_sec)


def _extract_ipv4_candidates(output):
    """Extract IPv4 from ip/ifconfig/uci command output."""
    ipv4_candidates = []

    for line in (output or "").replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue

        patterns = [
            r"\binet\s+addr:(\d{1,3}(?:\.\d{1,3}){3})",       # BusyBox ifconfig old format
            r"\binet\s+(\d{1,3}(?:\.\d{1,3}){3})(?:/\d+)?",  # ip addr/new ifconfig format
            r"^'(\d{1,3}(?:\.\d{1,3}){3})'$",                    # uci quoted output
            r"^(\d{1,3}(?:\.\d{1,3}){3})$",                      # uci plain output
        ]

        for pattern in patterns:
            match = re.search(pattern, line)
            if not match:
                continue
            ip = match.group(1)
            if _is_valid_ipv4(ip) and ip not in ipv4_candidates:
                ipv4_candidates.append(ip)

    return ipv4_candidates


def step2_get_ip():
    """透過 Serial 獲取 br-lan IPv4。"""
    log_step(f"FW upgrade: discover DUT br-lan IPv4 via serial ({COM_PORT})")

    commands = [
        "ip -4 -o addr show dev br-lan",
        "ifconfig br-lan",
        "uci -q get network.lan.ipaddr",
    ]

    last_serial_output = ""

    for attempt in range(1, SERIAL_DISCOVERY_RETRY + 1):
        try:
            log_step(f"FW upgrade: serial IPv4 discovery attempt {attempt}/{SERIAL_DISCOVERY_RETRY}")
            with serial.Serial(COM_PORT, BAUD_RATE, timeout=0.2, write_timeout=2) as ser:
                time.sleep(0.3)

                for command in commands:
                    log_step(f"FW upgrade: serial command: {command}")
                    output = _send_serial_command(ser, command)
                    last_serial_output = output

                    ipv4_candidates = _extract_ipv4_candidates(output)
                    if ipv4_candidates:
                        dut_ip = ipv4_candidates[0]
                        log_result(f"FW upgrade: DUT br-lan IPv4 = {dut_ip}")
                        return dut_ip

                    compact_output = " | ".join(
                        line.strip() for line in output.replace("\r", "").split("\n") if line.strip()
                    )
                    if compact_output:
                        log_progress(f"FW upgrade: no IPv4 parsed from output: {compact_output[:300]}")
                    else:
                        log_progress("FW upgrade: serial command returned empty output")

            time.sleep(1)

        except Exception as e:
            log_result(f"FW upgrade FAIL: serial IPv4 discover failed, reason={type(e).__name__}: {e}")
            return None

    if last_serial_output:
        compact_output = " | ".join(
            line.strip() for line in last_serial_output.replace("\r", "").split("\n") if line.strip()
        )
        log_result(f"FW upgrade FAIL: DUT br-lan IPv4 not found from serial output, last_output={compact_output[:500]}")
    else:
        log_result("FW upgrade FAIL: DUT br-lan IPv4 not found from serial output, last_output=<empty>")

    return None


def step3_web_upgrade(ip):
    """執行 Web 升級流程。"""
    session = requests.Session()
    login_url = f"http://{ip}/cgi-bin/luci/admin/index.htm"
    upload_url = f"http://{ip}/cgi-bin/luci/admin/upload_binary"

    try:
        log_step(f"FW upgrade: login Web UI ({ip})")
        login_resp = session.post(login_url, data={'luci_username': WEB_USER, 'luci_password': WEB_PASS}, timeout=10)
        log_result(f"FW upgrade: Web UI login request completed, http_status={login_resp.status_code}")

        if not os.path.exists(LOCAL_FW_NAME):
            log_result(f"FW upgrade FAIL: local firmware file not found ({LOCAL_FW_NAME})")
            return False

        log_step("FW upgrade: upload firmware and trigger upgrade")
        with open(LOCAL_FW_NAME, 'rb') as f:
            files = {'file': (LOCAL_FW_NAME, f, 'application/octet-stream')}
            response = session.post(upload_url, files=files, timeout=600)

        if response.status_code == 200:
            log_result("FW upgrade PASS: upgrade command accepted, device rebooting")
            return True

        log_result(f"FW upgrade FAIL: upgrade upload rejected, http_status={response.status_code}")
        return False
    except Exception as e:
        log_result(f"FW upgrade FAIL: Web upgrade exception, reason={type(e).__name__}: {e}")
        return False


def _run_fw_upgrade_flow():
    log_step("FW upgrade start")
    log_step(f"FW upgrade: log file = {RUN_LOG_PATH}")

    if not step1_sftp_download():
        log_result("FW upgrade FAIL: stop at SFTP download stage")
        return 1

    dut_ip = step2_get_ip()
    if not dut_ip:
        log_result("FW upgrade FAIL: stop at DUT IP discovery stage")
        return 1

    if not step3_web_upgrade(dut_ip):
        log_result("FW upgrade FAIL: stop at Web upgrade stage")
        return 1

    log_result("FW upgrade completed successfully")
    return 0


def main():
    init_log_file()
    rc = 1
    try:
        rc = _run_fw_upgrade_flow()
        return rc
    except Exception as e:
        log_result(f"FW upgrade FAIL: unhandled exception, reason={type(e).__name__}: {e}")
        for line in traceback.format_exc().rstrip().splitlines():
            log_result(f"traceback: {line}")
        return 1
    finally:
        log_step(f"FW upgrade: log saved to {RUN_LOG_PATH}")
        close_log_file()


if __name__ == "__main__":
    raise SystemExit(main())
