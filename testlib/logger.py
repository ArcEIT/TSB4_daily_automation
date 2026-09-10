"""Logging and summary helpers."""
import datetime
import os
import sys
import io
from . import config as cfg

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
except Exception:
    pass


def init_log_filenames():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    cfg.CASE_ID = cfg.TEST_CASE_NAME.replace("+", "_").replace(" ", "_")
    cfg.FULL_CONSOLE_LOG = f"{ts}_{cfg.TEST_CASE_NAME}_Console.log"
    cfg.SUMMARY_LOG = f"{ts}_{cfg.TEST_CASE_NAME}_Summary.log"


def _write_console(line: str):
    if cfg.FULL_CONSOLE_LOG:
        with open(cfg.FULL_CONSOLE_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    sys.stdout.write(line)
    sys.stdout.flush()


def _log_with_tag(tag, message):
    ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")[:-3]
    _write_console(f"{ts} [{tag}] >>> {message}\n")


def log_progress(message):
    _log_with_tag("PROGRESS", message)


def log_step(message):
    """High-level flow marker for major automation steps."""
    _log_with_tag("PROGRESS-STEP", message)


def log_result(message):
    """High-level result marker for PASS/FAIL/TIMEOUT outcomes."""
    _log_with_tag("PROGRESS-RESULT", message)


def log_separator(message):
    ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")[:-3]
    border = "=" * 70
    _write_console(f"\n{ts} {border}\n{ts} [PHASE START] >>> {message}\n{ts} {border}\n")


def log_details(message, to_console=True):
    if cfg.FULL_CONSOLE_LOG:
        with open(cfg.FULL_CONSOLE_LOG, "a", encoding="utf-8") as f:
            f.write(str(message) + "\n")
    if to_console:
        print(message)


def init_summary_log(router_fw, booster_fw):
    write_header = not os.path.exists(cfg.SUMMARY_LOG) or os.path.getsize(cfg.SUMMARY_LOG) == 0
    with open(cfg.SUMMARY_LOG, "a", encoding="utf-8") as f:
        if write_header:
            f.write(f"{cfg.TEST_CASE_NAME}\n")
            f.write(f"Router Firmware Version : {router_fw}\n")
            f.write(f"Booster Firmware Version: {booster_fw}\n")
            f.write("-" * 70 + "\n")
            f.write(
                f"{'Time':<20} | {'Loop':<8} | {'Interface':<12} | "
                f"{'Duration':<10} | {'Result':<8}\n"
            )
            f.write("-" * 70 + "\n")


def write_summary(loop_str, interface_name, duration, result, reason, status=""):
    ts = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    line = (
        f"{ts:<20} | {loop_str:<8} | {interface_name:<12} | "
        f"{duration:<10} | {result:<8}\n"
    )
    with open(cfg.SUMMARY_LOG, "a", encoding="utf-8") as f:
        f.write(line)
        if result == "FAIL" and reason and reason not in ("None", "N/A", ""):
            if status:
                # status format: "ETH BH Enable" / "WiFi BH Disable"
                # → "Fail_Reason ( TSM4 WiFi radio: Enable, ETH BH )"
                parts = status.rsplit(" ", 1)
                if len(parts) == 2:
                    ctx = f"TSM4 WiFi radio: {parts[1]}, {parts[0]}"
                else:
                    ctx = status
                f.write(f"\nFail_Reason ( {ctx} )\n{reason}\n")
            else:
                f.write(f"\nFail_Reason: {reason}\n")


def append_summary_block(text):
    if not text:
        return
    with open(cfg.SUMMARY_LOG, "a", encoding="utf-8") as f:
        if not text.startswith("\n"):
            f.write("\n")
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def summary_loop_display(loop_str, interface_name):
    if str(interface_name).strip().lower() == "wifi bh":
        return ""
    return str(loop_str)


def write_recovery_note(interface_name, recovery_action=None):
    if recovery_action is None:
        recovery_action = cfg.FAIL_RECOVERY_REASON_SUFFIX
    ts = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    append_summary_block(
        "備註:\n"
        f"- {ts} {interface_name} FAIL 後已執行 {recovery_action}；已收集 fail diagnostic 並 restore ETH BH。\n"
    )
