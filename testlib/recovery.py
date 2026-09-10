"""Fail diagnostic and recovery helpers."""
import datetime
import os
import subprocess
import sys
import time
import serial
from . import config as cfg
from .logger import log_progress, log_step, log_result, log_separator, append_summary_block
from .serial_console import receive_monitor, start_background_serial_logger, stop_background_serial_logger, is_background_serial_logger_running
from .relay import control_relay_channel, restore_eth_backhaul


def resolve_check_re_status_script():
    if os.path.isabs(cfg.CHECK_RE_STATUS_SCRIPT):
        return cfg.CHECK_RE_STATUS_SCRIPT
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(root_dir, cfg.CHECK_RE_STATUS_SCRIPT),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), cfg.CHECK_RE_STATUS_SCRIPT),
        os.path.join(os.getcwd(), cfg.CHECK_RE_STATUS_SCRIPT),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def infer_loop_interface(case_name):
    loop = "-"
    interface = "-"
    parts = str(case_name).split("_")
    for p in parts:
        if p.lower().startswith("loop"):
            loop = p.replace("Loop", "") or p
    if "WiFi_BH" in case_name or "WiFi" in case_name:
        interface = "WiFi BH"
    elif "ETH_BH" in case_name or "ETH" in case_name:
        interface = "ETH BH"
    return loop, interface



def _pause_background_serial_logger_for_check_re_status():
    """Release BOOSTER_PORT before launching check_RE_status.py.

    check_RE_status.py opens the same COM port in a child process. If the
    full-session background serial logger is still holding the handle, Windows
    returns PermissionError(13, 'Access is denied').
    """
    try:
        was_running = bool(is_background_serial_logger_running())
    except Exception:
        was_running = False

    if was_running:
        log_progress("[FAIL_DIAG] 暫停 full-session serial logger，釋放 COM port 給 check_RE_status.py")
        try:
            stop_background_serial_logger(close_serial=True)
            time.sleep(1)
        except Exception as e:
            log_progress(f"[FAIL_DIAG] 暫停 background serial logger 失敗: {type(e).__name__}: {e}")
    return was_running


def _resume_background_serial_logger_after_check_re_status(was_running):
    if not was_running:
        return
    try:
        log_progress("[FAIL_DIAG] check_RE_status.py 完成，重啟 full-session serial logger")
        start_background_serial_logger()
    except Exception as e:
        log_progress(f"[FAIL_DIAG] 重啟 background serial logger 失敗: {type(e).__name__}: {e}")

def run_check_re_status_diagnostic(case_name="", loop_str=None, interface_name=None):
    if not cfg.CHECK_RE_STATUS_ENABLE:
        log_step("Fail recovery: skip check_RE_status.py (CHECK_RE_STATUS_ENABLE=False)")
        log_progress("[FAIL_DIAG] CHECK_RE_STATUS_ENABLE=False，略過 check_RE_status.py")
        return ""

    script_path = resolve_check_re_status_script()
    script_dir = os.path.dirname(os.path.abspath(script_path))
    diag_com_port = cfg.CHECK_RE_STATUS_COM_PORT or cfg.BOOSTER_PORT
    inferred_loop, inferred_interface = infer_loop_interface(case_name)
    loop_str = loop_str or inferred_loop
    interface_name = interface_name or inferred_interface
    ts = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    log_step(f"Fail recovery: run check_RE_status.py (COM={diag_com_port}, interface={interface_name or '-'})")

    header = (
        f"Check_RE_Status[{ts}][Loop={loop_str or '-'}][{interface_name or '-'}]\n"
        f"    Case_Name: {case_name}\n"
        f"    Script: {script_path}\n"
        f"    COM_Port: {diag_com_port}\n"
    )

    was_logger_running = _pause_background_serial_logger_for_check_re_status()

    log_progress(f"[FAIL_DIAG] 準備執行 {cfg.CHECK_RE_STATUS_SCRIPT}")
    log_progress(f"[FAIL_DIAG] script_path={script_path}")
    log_progress(f"[FAIL_DIAG] com_port={diag_com_port}")

    if not os.path.exists(script_path):
        msg = header + f"    - SKIP: {cfg.CHECK_RE_STATUS_SCRIPT} not found. Expected path: {script_path}\n"
        append_summary_block(msg)
        log_progress(f"[FAIL_DIAG] {cfg.CHECK_RE_STATUS_SCRIPT} 不存在，略過執行")
        _resume_background_serial_logger_after_check_re_status(was_logger_running)
        return msg

    cmd = [sys.executable, script_path]
    if cfg.CHECK_RE_STATUS_COM_PORT_ARG:
        cmd.extend([cfg.CHECK_RE_STATUS_COM_PORT_ARG, diag_com_port])
    else:
        cmd.append(diag_com_port)

    log_progress(f"[FAIL_DIAG][CMD] {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=cfg.CHECK_RE_STATUS_TIMEOUT,
            cwd=script_dir,
        )
        log_progress(f"[FAIL_DIAG] {cfg.CHECK_RE_STATUS_SCRIPT} 執行完成 exit_code={proc.returncode}")
        log_result(f"Fail recovery: check_RE_status.py completed, rc={proc.returncode}")
        stdout_text = proc.stdout.strip()
        stderr_text = proc.stderr.strip()

        if stdout_text:
            for line in stdout_text.splitlines():
                log_progress(f"[FAIL_DIAG][STDOUT] {line}")
        else:
            log_progress("[FAIL_DIAG][STDOUT] <empty>")

        if stderr_text:
            for line in stderr_text.splitlines():
                log_progress(f"[FAIL_DIAG][STDERR] {line}")
        else:
            log_progress("[FAIL_DIAG][STDERR] <empty>")

        lines = [header.rstrip(), f"    Exit_Code: {proc.returncode}", "    STDOUT:"]
        lines.extend([f"      {line}" for line in stdout_text.splitlines()] if stdout_text else ["      <empty>"])
        lines.append("    STDERR:")
        lines.extend([f"      {line}" for line in stderr_text.splitlines()] if stderr_text else ["      <empty>"])
        msg = "\n".join(lines) + "\n"
        append_summary_block(msg)
        _resume_background_serial_logger_after_check_re_status(was_logger_running)
        return msg

    except subprocess.TimeoutExpired:
        msg = header + f"    - TIMEOUT: over {cfg.CHECK_RE_STATUS_TIMEOUT} seconds\n"
        append_summary_block(msg)
        log_progress(f"[FAIL_DIAG] {cfg.CHECK_RE_STATUS_SCRIPT} 執行逾時")
        log_result(f"Fail recovery: check_RE_status.py TIMEOUT, timeout={cfg.CHECK_RE_STATUS_TIMEOUT}s")
        _resume_background_serial_logger_after_check_re_status(was_logger_running)
        return msg
    except Exception as e:
        msg = header + f"    - ERROR: {type(e).__name__}: {e}\n"
        append_summary_block(msg)
        log_progress(f"[FAIL_DIAG] {cfg.CHECK_RE_STATUS_SCRIPT} 執行失敗: {type(e).__name__}: {e}")
        log_result(f"Fail recovery: check_RE_status.py ERROR: {type(e).__name__}: {e}")
        _resume_background_serial_logger_after_check_re_status(was_logger_running)
        return msg


def resolve_re_log_collect_script():
    script_name = getattr(cfg, "RE_LOG_COLLECT_SCRIPT", "collect_diagnosticcomlog_on_fail.py")
    if os.path.isabs(script_name):
        return script_name
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(root_dir, script_name),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), script_name),
        os.path.join(os.getcwd(), script_name),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def _is_valid_re_host(ip):
    """Return True for usable RE host IPs, excluding network/broadcast IPs."""
    try:
        parts = [int(x) for x in str(ip).split(".")]
    except Exception:
        return False
    if len(parts) != 4:
        return False
    if any(p < 0 or p > 255 for p in parts):
        return False
    # Do not try network or broadcast addresses such as 192.168.0.0 / 192.168.0.255.
    if parts[3] in (0, 255):
        return False
    return True


def _dedupe_keep_order(items):
    result = []
    for item in items:
        item = str(item or "").strip()
        if item and _is_valid_re_host(item) and item not in result:
            result.append(item)
    return result


def _extract_candidate_re_hosts(text):
    """Extract possible Booster/RE IPv4 hosts from check_RE_status.py output.

    Filters out broadcast/network addresses such as 192.168.0.255 so the
    collect script only attempts usable hosts. Preferred order is:
      1. Valid IPs detected from check_RE_status.py output
      2. Configured fallback hosts
      3. RE_LOG_COLLECT_FALLBACK_HOST, default 192.168.1.253
    """
    import re
    ips = re.findall(r"\b(?:192\.168\.(?:0|1)\.\d{1,3})\b", text or "")

    preferred = []
    preferred.extend(ips)

    configured_hosts = getattr(cfg, "RE_LOG_COLLECT_HOSTS", [])
    if isinstance(configured_hosts, str):
        configured_hosts = [x.strip() for x in configured_hosts.replace(";", ",").split(",")]
    preferred.extend(configured_hosts)
    preferred.append(getattr(cfg, "RE_LOG_COLLECT_FALLBACK_HOST", "192.168.1.253"))
    return _dedupe_keep_order(preferred)


def run_re_log_collect_diagnostic(case_name="", check_status_text=""):
    """Collect /tmp/diagnosticcomlog.tgz from Booster/RE after any case FAIL.

    This intentionally downloads only diagnosticcomlog.tgz. It does not collect
    diag.tgz, pcap, send email, upload SFTP, or package zip.
    """
    if not getattr(cfg, "RE_LOG_COLLECT_ENABLE", False):
        log_step("Fail recovery: skip diagnosticcomlog.tgz collect (RE_LOG_COLLECT_ENABLE=False)")
        log_progress("[RE_LOG_COLLECT] RE_LOG_COLLECT_ENABLE=False，略過 diagnosticcomlog.tgz 收集")
        return ""

    script_path = resolve_re_log_collect_script()
    script_dir = os.path.dirname(os.path.abspath(script_path))
    hosts = _extract_candidate_re_hosts(check_status_text)
    case_title = getattr(cfg, "TEST_CASE_NAME", case_name or "unknown_case")
    ts = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    header = (
        f"Collect_DiagnosticComLog[{ts}]\n"
        f"    Case_Name: {case_name}\n"
        f"    Case_Title: {case_title}\n"
        f"    Script: {script_path}\n"
        f"    Hosts: {', '.join(hosts) if hosts else '<empty>'}\n"
        f"    Remote_File: /tmp/diagnosticcomlog.tgz\n"
    )

    log_step(f"Fail recovery: collect diagnosticcomlog.tgz (hosts={', '.join(hosts) if hosts else '<empty>'})")
    log_progress("[RE_LOG_COLLECT] 準備收集 /tmp/diagnosticcomlog.tgz")
    log_progress(f"[RE_LOG_COLLECT] script_path={script_path}")
    log_progress(f"[RE_LOG_COLLECT] hosts={', '.join(hosts) if hosts else '<empty>'}")

    if not os.path.exists(script_path):
        msg = header + f"    - SKIP: collect script not found. Expected path: {script_path}\n"
        append_summary_block(msg)
        log_progress("[RE_LOG_COLLECT] collect script 不存在，略過")
        return msg

    cmd = [
        sys.executable,
        script_path,
        "--hosts", ",".join(hosts),
        "--case-title", str(case_title),
        "--output-dir", getattr(cfg, "RE_LOG_COLLECT_OUTPUT_DIR", "RE_fail_logs"),
        "--username", getattr(cfg, "RE_LOG_COLLECT_USERNAME", getattr(cfg, "ONBOARDING_SSH_USERNAME", "25g5@rIj2Z")),
        "--password", getattr(cfg, "RE_LOG_COLLECT_PASSWORD", getattr(cfg, "ONBOARDING_SSH_PASSWORD", "x@u4194j042u/4m,4@")),
        "--port", str(getattr(cfg, "RE_LOG_COLLECT_SSH_PORT", 22)),
        "--timeout", str(getattr(cfg, "RE_LOG_COLLECT_SSH_TIMEOUT", 15)),
        "--run-timeout", str(getattr(cfg, "RE_LOG_COLLECT_RUN_TIMEOUT", 180)),
    ]
    # If WiFi BH tcpdump debug is enabled, download the pcap in the same SSH session
    try:
        from .tcpdump_debug import _enabled as _tcpdump_enabled
        if _tcpdump_enabled():
            pcap_path = getattr(cfg, "WIFI_BH_TCPDUMP_REMOTE_PATH", "/tmp/wifi_bh_dhcp.pcap")
            cmd.extend(["--tcpdump-pcap", pcap_path])
    except Exception:
        pass

    log_progress(f"[RE_LOG_COLLECT][CMD] {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=getattr(cfg, "RE_LOG_COLLECT_TIMEOUT", 300),
            cwd=script_dir,
        )
        log_progress(f"[RE_LOG_COLLECT] collect 完成 exit_code={proc.returncode}")
        if proc.returncode == 0:
            log_result(f"Collect diagnosticcomlog.tgz PASS: case={case_title}")
        else:
            log_result(f"Collect diagnosticcomlog.tgz FAIL: rc={proc.returncode}, case={case_title}")
        stdout_text = proc.stdout.strip()
        stderr_text = proc.stderr.strip()

        if stdout_text:
            for line in stdout_text.splitlines():
                log_progress(f"[RE_LOG_COLLECT][STDOUT] {line}")
        else:
            log_progress("[RE_LOG_COLLECT][STDOUT] <empty>")

        if stderr_text:
            for line in stderr_text.splitlines():
                log_progress(f"[RE_LOG_COLLECT][STDERR] {line}")
        else:
            log_progress("[RE_LOG_COLLECT][STDERR] <empty>")

        lines = [header.rstrip(), f"    Exit_Code: {proc.returncode}", "    STDOUT:"]
        lines.extend([f"      {line}" for line in stdout_text.splitlines()] if stdout_text else ["      <empty>"])
        lines.append("    STDERR:")
        lines.extend([f"      {line}" for line in stderr_text.splitlines()] if stderr_text else ["      <empty>"])
        msg = "\n".join(lines) + "\n"
        append_summary_block(msg)
        return msg

    except subprocess.TimeoutExpired:
        msg = header + f"    - TIMEOUT: over {getattr(cfg, 'RE_LOG_COLLECT_TIMEOUT', 300)} seconds\n"
        append_summary_block(msg)
        log_progress("[RE_LOG_COLLECT] collect 執行逾時")
        log_result(f"Collect diagnosticcomlog.tgz TIMEOUT: timeout={getattr(cfg, 'RE_LOG_COLLECT_TIMEOUT', 300)}s, case={case_title}")
        return msg
    except Exception as e:
        msg = header + f"    - ERROR: {type(e).__name__}: {e}\n"
        append_summary_block(msg)
        log_progress(f"[RE_LOG_COLLECT] collect 執行失敗: {type(e).__name__}: {e}")
        log_result(f"Collect diagnosticcomlog.tgz ERROR: {type(e).__name__}: {e}")
        return msg


def reboot_tsm4_and_re():
    """Optional FAIL recovery: reboot RE via serial only.

    This function name is kept for backward compatibility with older code.
    It does not touch TSM4 power relay. It only sends `reboot -f` to RE
    when cfg.FAIL_RECOVERY_REBOOT_ENABLE is enabled.

    Wait timer after reboot is cfg.FAIL_RECOVERY_REBOOT_WAIT.
    Do not reuse TSM4_REBOOT_MONITOR_TIME here because that name is
    ambiguous with TSM4 GUI reboot flow.
    """
    log_separator("RE reboot recovery")

    log_step("Fail recovery: send RE reboot -f via serial")
    log_progress("STEP 1: 透過 RE serial console 送出 reboot -f")

    try:
        with serial.Serial(cfg.BOOSTER_PORT, cfg.BAUD_RATE, timeout=0.1) as ser:
            for _ in range(3):
                ser.write(b"\r\n")
                time.sleep(0.5)
            ser.write(b"reboot -f\n")
            time.sleep(2)
            log_progress("RE reboot -f 指令已送出")
            log_result("Fail recovery: RE reboot -f command sent")
    except Exception as e:
        log_progress(f"RE reboot 指令送出失敗: {e}，後續仍會 restore ETH BH")

    reboot_wait = getattr(cfg, "FAIL_RECOVERY_REBOOT_WAIT", getattr(cfg, "RE_RECOVERY_REBOOT_WAIT", 120))
    log_step(f"Fail recovery: wait after RE reboot, wait={reboot_wait}s")
    log_progress(f"STEP 2: RE reboot recovery wait = {reboot_wait} 秒，等待 Booster / RE 開機穩定")
    receive_monitor(reboot_wait)
    log_progress("RE reboot recovery wait 完成")

def safe_handle_fail_recovery(case_name, restore_eth_bh=True):
    reboot_recovery_enabled = False
    check_status_text = ""

    log_step(f"Fail recovery start: {case_name}")

    # Kill tcpdump first so the pcap file is complete before collect_diagnosticcomlog downloads it.
    try:
        from .tcpdump_debug import _enabled as _tcpdump_enabled, stop_for_download
        if _tcpdump_enabled():
            stop_for_download()
    except Exception as e:
        log_progress(f"[TCPDUMP] stop error (non-fatal): {type(e).__name__}: {e}")

    try:
        check_status_text = run_check_re_status_diagnostic(case_name=case_name)
        log_step("Fail recovery: check_RE_status.py completed; next collect diagnosticcomlog.tgz")
        log_progress("FAIL diagnostic 完成，後續會收集 diagnosticcomlog.tgz 並切回 ETH BH")
    except Exception as e:
        log_progress(f"check_RE_status diagnostic 發生異常，但後續仍會嘗試收集 diagnosticcomlog.tgz 並切回 ETH BH: {type(e).__name__}: {e}")

    try:
        run_re_log_collect_diagnostic(case_name=case_name, check_status_text=check_status_text)
    except Exception as e:
        log_progress(f"diagnosticcomlog.tgz 收集發生異常，但後續仍會切回 ETH BH: {type(e).__name__}: {e}")

    try:
        if getattr(cfg, "FAIL_RECOVERY_REBOOT_ENABLE", False):
            reboot_recovery_enabled = True
            log_step("Fail recovery: RE reboot enabled")
            log_progress("FAIL recovery RE reboot 已啟用，準備執行 RE reboot -f")
            reboot_tsm4_and_re()
        else:
            log_step("Fail recovery: RE reboot disabled; restore ETH BH only")
            log_progress("FAIL recovery RE reboot 已關閉，略過 RE reboot -f；只執行 ETH BH restore")
    except Exception as e:
        log_progress(f"FAIL recovery 發生異常，但後續仍會切回 ETH BH: {type(e).__name__}: {e}")
    finally:
        if restore_eth_bh:
            log_step("Fail recovery: restore ETH BH")
            if reboot_recovery_enabled:
                restore_eth_backhaul("FAIL recovery 完成")
            else:
                restore_eth_backhaul("FAIL diagnostic 完成")
        else:
            log_step("Fail recovery: skip restore ETH BH (restore_eth_bh=False)")
        log_result(f"Fail recovery completed: {case_name}")
