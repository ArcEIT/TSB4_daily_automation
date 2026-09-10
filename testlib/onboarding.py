"""Onboarding polling logic."""
import re
import time
from . import config as cfg
from .logger import log_progress, log_step, log_result, log_details, write_summary, summary_loop_display
from .serial_console import receive_monitor, _SERIAL_IO_LOCK, is_background_serial_logger_running, get_serial_for_command
from .ssh_client import run_ssh_command, discover_ssh_host_by_serial


STATE_BEGIN_MARKER = "__ARC_ONBOARDING_STATE_BEGIN__"
STATE_END_MARKER = "__ARC_ONBOARDING_STATE_END__"
IFCONFIG_BEGIN_MARKER = "__ARC_IFCONFIG_BEGIN__"
IFCONFIG_END_MARKER = "__ARC_IFCONFIG_END__"

MAP_BEGIN_MARKER = "__ARC_MAP_ONBOARDING_BEGIN__"
MAP_END_MARKER = "__ARC_MAP_ONBOARDING_END__"

IP_BEGIN_MARKER = "__ARC_IP_BEGIN__"
IP_END_MARKER = "__ARC_IP_END__"

_LAST_SSH_FAIL_LOG_TIME = 0


def _cfg_bool(name, default=False):
    value = getattr(cfg, name, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on", "enable", "enabled")
    return bool(value)


def _short_cmd(cmd, limit=160):
    text = str(cmd).replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        return text[:limit - 3] + "..."
    return text


def _line_has_done_marker(text, marker):
    if not text:
        return False
    for line in str(text).replace("\r", "").split("\n"):
        stripped = line.strip()
        if stripped == marker or stripped.startswith(marker + ":"):
            return True
    return False


def _receive_until_marker_or_timeout(ser, marker, timeout_sec, slice_sec=0.25):
    chunks = []
    timeout_sec = max(0.1, float(timeout_sec))
    slice_sec = max(0.05, min(float(slice_sec), timeout_sec))
    deadline = time.monotonic() + timeout_sec

    while time.monotonic() < deadline:
        wait_sec = min(slice_sec, max(0.01, deadline - time.monotonic()))
        chunk = receive_monitor(wait_sec, ser)
        if chunk:
            chunks.append(str(chunk))
            if _line_has_done_marker("".join(chunks[-8:]), marker):
                return True, "".join(chunks)

    return False, "".join(chunks)


def _build_rd_debug_group_command(commands, marker):
    clean_cmds = [str(cmd).strip() for cmd in commands if str(cmd).strip()]
    parts = ["echo __ARC_RD_DEBUG_GROUP_BEGIN__"]
    parts.extend(clean_cmds)
    parts.append(f"echo {marker}:$?")
    return "; ".join(parts)


def _is_background_serial_logger_running():
    return is_background_serial_logger_running()


def _monitor_wait(wait_seconds, ser=None):
    if wait_seconds <= 0:
        return ""
    return receive_monitor(wait_seconds, ser)


def build_onboarding_fail_reason(has_onboarding, has_ping, has_map_uci, has_ip=None, prefix="", state_value=None, map_uci_value=None):
    reasons = []
    if not has_onboarding:
        val = f"({state_value})" if state_value is not None else ""
        reasons.append(f"Onboarding State Fail{' ' + val if val else ''}")
    if not has_ping:
        if has_ip is False:
            reasons.append("No IP (br-lan)")
        else:
            reasons.append("Ping Fail")
    if not has_map_uci:
        val = f"({map_uci_value})" if map_uci_value is not None else ""
        reasons.append(f"repacd MAP Done Fail{' ' + val if val else ''}")
    if not reasons:
        reasons.append("Onboarding Timeout (條件均通過，但未達穩定閾值)")
    reason = " + ".join(reasons)
    return f"{prefix}: {reason}" if prefix else reason


def get_elapsed_duration(duration_start_time, fallback_start_time, fallback_init_wait_time):
    if duration_start_time is not None:
        return round(time.time() - duration_start_time, 2)
    return round(time.time() - fallback_start_time + fallback_init_wait_time, 2)


def build_onboarding_poll_cmd(ping_count=2):
    """Build a minimal serial onboarding polling command."""
    return (
        f"echo {STATE_BEGIN_MARKER}; "
        "cat /tmp/arc_onboarding_state 2>/dev/null; "
        f"echo {STATE_END_MARKER}; "
        f"echo {MAP_BEGIN_MARKER}; "
        "uci get repacd.MAPConfig.OnboardingDone 2>/dev/null; "
        f"echo {MAP_END_MARKER}; "
        f"echo {IP_BEGIN_MARKER}; "
        "ip addr show br-lan 2>/dev/null | grep 'inet ' | grep -o '[0-9]*\\.[0-9]*\\.[0-9]*\\.[0-9]*' | head -1; "
        f"echo {IP_END_MARKER}; "
        f"ping 192.168.0.1 -c {int(ping_count)}\n"
    ).encode("utf-8")


def build_ssh_onboarding_cmd(ping_count=2):
    """Build an SSH onboarding polling command with the same state markers."""
    return (
        f"echo {STATE_BEGIN_MARKER}; "
        "cat /tmp/arc_onboarding_state 2>/dev/null; "
        f"echo {STATE_END_MARKER}; "
        f"echo {MAP_BEGIN_MARKER}; "
        "uci get repacd.MAPConfig.OnboardingDone 2>/dev/null; "
        f"echo {MAP_END_MARKER}; "
        f"echo {IP_BEGIN_MARKER}; "
        "ip addr show br-lan 2>/dev/null | grep 'inet ' | grep -o '[0-9]*\\.[0-9]*\\.[0-9]*\\.[0-9]*' | head -1; "
        f"echo {IP_END_MARKER}; "
        f"ping 192.168.0.1 -c {int(ping_count)}"
    )


def _is_safe_state_line(line):
    low = line.lower().strip()
    known_states = {"done", "not done", "pending", "unknown", "fail", "failed", "1", "0"}
    if low in known_states:
        return True
    if not line or len(line) > 64:
        return False
    blocked_tokens = (
        "/bin/ash:", "root@", "echo ", "cat /tmp/arc_onboarding_state",
        "ping ", "64 bytes", "packet loss", "---", "===", "[", "]", "/tmp/", "br-lan", "checking", "uci get",
    )
    low_line = line.lower()
    if any(token.lower() in low_line for token in blocked_tokens):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_ -]+", line))


def extract_onboarding_state_value(output):
    if not output:
        return ""
    lines = [line.strip() for line in output.replace("\r", "").split("\n")]
    collecting = False
    marker_values = []

    for line in lines:
        if line == STATE_BEGIN_MARKER:
            collecting = True
            marker_values = []
            continue
        if line == STATE_END_MARKER:
            for value in marker_values:
                if _is_safe_state_line(value):
                    return value.strip()
            return ""
        if collecting:
            if not line:
                continue
            if _is_safe_state_line(line):
                marker_values.append(line)

    known_states = {"done", "not done", "pending", "unknown", "fail", "failed"}
    for line in lines:
        low = line.lower().strip()
        if low in known_states:
            return line.strip()
    return ""


# 【新增解析函式】從 Log 中精準提取並驗證 UCI 的回傳值是否為 1
def extract_map_config_uci_value(output):
    if not output:
        return ""
    lines = [line.strip() for line in output.replace("\r", "").split("\n")]
    collecting = False
    marker_values = []

    for line in lines:
        if line == MAP_BEGIN_MARKER:
            collecting = True
            marker_values = []
            continue
        if line == MAP_END_MARKER:
            for value in marker_values:
                if _is_safe_state_line(value):
                    return value.strip()
            return ""
        if collecting:
            if not line:
                continue
            if _is_safe_state_line(line):
                marker_values.append(line)
    return ""


def extract_ip_value(output):
    """Extract br-lan IPv4 address from polling output (between IP markers)."""
    if not output:
        return ""
    lines = [line.strip() for line in output.replace("\r", "").split("\n")]
    collecting = False
    for line in lines:
        if line == IP_BEGIN_MARKER:
            collecting = True
            continue
        if line == IP_END_MARKER:
            break
        if collecting and line:
            if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", line):
                return line
    return ""


_NO_IP_ADDRESSES = {"192.168.1.253"}

def parse_has_ip(output):
    """Return True if br-lan has a valid DHCP-assigned IP, False if empty or default fallback IP, None if marker not present."""
    if IP_BEGIN_MARKER not in str(output):
        return None
    ip = extract_ip_value(output).strip()
    if not ip or ip in _NO_IP_ADDRESSES:
        return False
    return True


def parse_onboarding_state(output):
    """Return True when /tmp/arc_onboarding_state reports exactly 'done'."""
    return extract_onboarding_state_value(output).strip().lower() == "done"


# 【新增條件判定】判斷提取出的 MAP UCI 值是否等於 1
def parse_map_config_uci_ok(output):
    """Return True when uci get repacd.MAPConfig.OnboardingDone reports exactly '1'."""
    return extract_map_config_uci_value(output).strip() == "1"


def parse_ping_ok(output):
    """Return True when ping output shows reply from gateway."""
    if not output:
        return False
    low = output.lower()
    return "64 bytes from" in low or "bytes from 192.168.0.1" in low or "0% packet loss" in low


def _rate_limited_ssh_fail_log(message, interval=30):
    global _LAST_SSH_FAIL_LOG_TIME
    now = time.time()
    if now - _LAST_SSH_FAIL_LOG_TIME >= interval:
        log_progress(message)
        _LAST_SSH_FAIL_LOG_TIME = now


def perform_onboarding_check(ser, ping_count=2):
    mode = str(getattr(cfg, "ONBOARDING_CHECK_MODE", "serial")).lower()
    if mode not in ("serial", "ssh_first", "ssh_only"):
        mode = "serial"

    if mode in ("ssh_first", "ssh_only"):
        host = discover_ssh_host_by_serial(ser, log_prefix="[ONBOARDING][SSH]")
        if host:
            ok, output, reason = run_ssh_command(host, build_ssh_onboarding_cmd(ping_count=ping_count))
            if ok:
                return output, "SSH", host, "None"
            _rate_limited_ssh_fail_log(f"[ONBOARDING][SSH] SSH check not ready ({host}): {reason}")
        elif mode == "ssh_only":
            _rate_limited_ssh_fail_log("[ONBOARDING][SSH] ssh_only 但尚未取得 RE SSH host。")

        if mode == "ssh_only" or not cfg.ONBOARDING_SERIAL_FALLBACK_ENABLE:
            return "", "SSH", host or "N/A", "SSH unavailable"

        _rate_limited_ssh_fail_log("[ONBOARDING][SSH] fallback serial marker check for this polling round.")

    try:
        with _SERIAL_IO_LOCK:
            ser.write(build_onboarding_poll_cmd(ping_count=ping_count))
            output = receive_monitor(5, ser)
        return output, "SERIAL", "N/A", "None"
    except Exception as e:
        return "", "SERIAL", "N/A", f"{type(e).__name__}: {e}"


def final_onboarding_check(ser, interface_name):
    log_step(f"{interface_name}: final onboarding check, wait={cfg.FINAL_ONBOARDING_CHECK_WAIT}s")
    log_progress(f"[{cfg.BOOSTER_PORT} - {interface_name}] 最後確認 onboarding 狀態，等待 {cfg.FINAL_ONBOARDING_CHECK_WAIT} 秒...")
    _monitor_wait(cfg.FINAL_ONBOARDING_CHECK_WAIT, ser)

    output, source, host, reason = perform_onboarding_check(ser, ping_count=1)

    has_onboarding = parse_onboarding_state(output)
    has_ping = parse_ping_ok(output)
    has_map_uci = parse_map_config_uci_ok(output)
    has_ip = parse_has_ip(output)

    state_value = extract_onboarding_state_value(output)
    map_uci_value = extract_map_config_uci_value(output)
    ip_value = extract_ip_value(output)

    log_details("-" * 50)
    log_details("[Final Check]:")
    log_details(f"  > Check Source     : {source}" + (f" ({host})" if source == "SSH" and host not in (None, "N/A") else ""))
    if reason != "None":
        log_details(f"  > Check Reason     : {reason}")
    log_details(f"  > Onboarding State: {state_value}")
    log_details(f"  > repacd MAP Done : {map_uci_value}")
    log_details(f"  > IP (br-lan)     : {ip_value if ip_value else ('N/A' if has_ip is None else 'No IP')}")
    log_details(f"  > Ping GW         : {'SUCCESS' if has_ping else 'FAIL/TIMEOUT'}")
    log_details("-" * 50)

    # 三維指標必須全部為 True 才判定最終過關
    return has_onboarding and has_ping and has_map_uci, has_onboarding, has_ping, has_map_uci, has_ip, state_value, map_uci_value


def run_rd_poll_debug_dump(ser, interface_name, round_index, poll_status="UNKNOWN"):
    if not getattr(cfg, "RD_POLL_DEBUG_ENABLE", False):
        return "DISABLED"

    every_n = int(getattr(cfg, "RD_POLL_DEBUG_EVERY_N_ROUNDS", 1) or 1)
    if every_n > 1 and (round_index % every_n) != 0:
        return "SKIPPED"

    commands = [str(cmd).strip() for cmd in getattr(cfg, "RD_POLL_DEBUG_COMMANDS", []) if str(cmd).strip()]
    if not commands:
        return "NO_COMMAND"

    read_time = float(getattr(cfg, "RD_POLL_DEBUG_READ_TIME", 12))
    slice_time = float(getattr(cfg, "RD_POLL_DEBUG_SLICE_TIME", 0.25))
    group_mode = _cfg_bool("RD_POLL_DEBUG_GROUP_MODE", True)
    marker_text = "NOT_RUN"

    cmd_list = " | ".join(commands)
    log_details(f"[RD DEBUG] {'=' * 60}")
    log_details(f"[RD DEBUG] Round={round_index}  Status={poll_status}  Interface={interface_name}")
    log_details(f"[RD DEBUG] CMD: {cmd_list}")
    log_details(f"[RD DEBUG] {'-' * 60}")

    try:
        with _SERIAL_IO_LOCK:
            if group_mode:
                marker = f"__ARC_RD_DEBUG_GROUP_DONE_{round_index}_{int(time.time() * 1000)}__"
                group_cmd = _build_rd_debug_group_command(commands, marker)
                ser.write((group_cmd + "\n").encode("utf-8"))
                done_seen, raw_output = _receive_until_marker_or_timeout(ser, marker, read_time, slice_time)
                marker_text = "DONE" if done_seen else "TIMEOUT"
                for line in raw_output.replace("\r", "").split("\n"):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if stripped == "__ARC_RD_DEBUG_GROUP_BEGIN__" or stripped.startswith("__ARC_RD_DEBUG_GROUP_DONE_"):
                        continue
                    log_details(f"[RD DEBUG]  {stripped}")
            else:
                marker_text = "FIXED_WAIT"
                for cmd_index, cmd in enumerate(commands, start=1):
                    log_details(f"[RD DEBUG] [{cmd_index}/{len(commands)}] {_short_cmd(cmd)}")
                    ser.write((cmd + "\n").encode("utf-8"))
                    receive_monitor(read_time, ser)
    except Exception as e:
        marker_text = f"ERROR:{type(e).__name__}"
        log_details(f"[RD DEBUG] ERROR: {type(e).__name__}: {e}")
    log_details(f"[RD DEBUG] END  marker={marker_text}")
    log_details(f"[RD DEBUG] {'=' * 60}")
    log_details("")
    return marker_text


def poll_booster_console(loop_str, interface_name, init_wait_time=None, threshold=None, max_total_limit=None, duration_start_time=None, write_summary_on_pass=True, write_summary_on_fail=True, show_loop_number=False, reason_out=None):
    init_wait_time = cfg.INIT_WAIT_TIME if init_wait_time is None else init_wait_time
    threshold = cfg.ONBOARDING_THRESHOLD if threshold is None else threshold
    max_total_limit = cfg.NORMAL_MAX_TOTAL_LIMIT if max_total_limit is None else max_total_limit

    log_step(
        f"{interface_name}: start onboarding check "
        f"(init_wait={init_wait_time}s, threshold={threshold}, max_total_limit={max_total_limit}s, mode={cfg.ONBOARDING_CHECK_MODE})"
    )
    log_progress(f"[{cfg.BOOSTER_PORT} - {interface_name}] 初始等待 {init_wait_time} 秒...")
    receive_monitor(init_wait_time)

    ser = None
    close_after_use = False
    try:
        ser, close_after_use = get_serial_for_command()
        log_progress(f"[{cfg.BOOSTER_PORT}] 送出 Enter 鍵以喚醒 Console 介面...")
        with _SERIAL_IO_LOCK:
            ser.reset_input_buffer()
            ser.write(b"\r\n")
        receive_monitor(1, ser)

        if str(getattr(cfg, "ONBOARDING_CHECK_MODE", "serial")).lower() in ("ssh_first", "ssh_only"):
            discover_ssh_host_by_serial(ser, log_prefix="[ONBOARDING][SSH]")

        consecutive_count = 0
        last_fail_reason = "Onboarding Timeout: 末次輪詢條件達標，但連續次數不足"
        last_state_value = None
        last_map_uci_value = None
        saw_valid_polling_output = False
        poll_start_time = time.time()
        poll_round = 0
        
        log_progress(
            f"[{cfg.BOOSTER_PORT}] 開始輪詢 (mode={cfg.ONBOARDING_CHECK_MODE}, 目標：連續成功計數 0/{threshold})..."
        )
        while (time.time() - poll_start_time) < (max_total_limit - init_wait_time):
            loop_start = time.time()
            next_poll_time = loop_start + cfg.POLLING_INTERVAL
            poll_round += 1
            try:
                check_start = time.monotonic()
                output, source, host, reason = perform_onboarding_check(ser, ping_count=2)
                check_elapsed = time.monotonic() - check_start

                has_onboarding = parse_onboarding_state(output)
                has_ping = parse_ping_ok(output)
                has_map_uci = parse_map_config_uci_ok(output)
                has_ip = parse_has_ip(output)

                state_value = extract_onboarding_state_value(output)
                map_uci_value = extract_map_config_uci_value(output)
                ip_value = extract_ip_value(output)

                poll_status = "PASS" if (has_onboarding and has_ping and has_map_uci) else "FAIL"

                log_details("-" * 50)
                ts = time.strftime("%H:%M:%S")
                log_details(f"[{ts} 輪詢紀錄]:")
                log_details(f"  > Check Source     : {source}" + (f" ({host})" if source == "SSH" and host not in (None, "N/A") else ""))
                if reason != "None":
                    log_details(f"  > Check Reason     : {reason}")
                log_details(f"  > Onboarding State: {state_value}")
                log_details(f"  > repacd MAP Done : {map_uci_value}")
                log_details(f"  > IP (br-lan)     : {ip_value if ip_value else ('N/A' if has_ip is None else 'No IP')}")
                log_details(f"  > Ping GW         : {'SUCCESS' if has_ping else 'FAIL/TIMEOUT'}")
                log_details(f"  > Check Time      : {check_elapsed:.2f}s")

                if output.strip():
                    saw_valid_polling_output = True
                    last_state_value = state_value
                    last_map_uci_value = map_uci_value
                    if not (has_onboarding and has_ping and has_map_uci):
                        last_fail_reason = build_onboarding_fail_reason(has_onboarding, has_ping, has_map_uci, has_ip=has_ip, state_value=state_value, map_uci_value=map_uci_value)

                # 【修改成功判定】
                if has_onboarding and has_ping and has_map_uci:
                    consecutive_count += 1
                    log_details(f"  >>> 成功計數: {consecutive_count}/{threshold}")
                    log_progress(f"[{cfg.BOOSTER_PORT} - {interface_name}] Onboarding check PASS: {consecutive_count}/{threshold} ({source})")
                else:
                    if consecutive_count > 0:
                        log_details(f"  >>> 條件中斷，計數器由 {consecutive_count}/{threshold} 歸零。")
                        log_progress(f"[{cfg.BOOSTER_PORT} - {interface_name}] Onboarding check reset: 0/{threshold}")
                    else:
                        log_details(f"  >>> 條件尚未達成，持續監控中: 0/{threshold}")
                    consecutive_count = 0
                log_details("-" * 50)

                run_rd_poll_debug_dump(
                    ser,
                    interface_name,
                    poll_round,
                    poll_status=poll_status,
                )

                if consecutive_count >= threshold:
                    onboarding_duration = get_elapsed_duration(duration_start_time, poll_start_time, init_wait_time)
                    log_step(
                        f"{interface_name}: threshold reached {threshold}/{threshold}, "
                        f"duration={onboarding_duration}s, cooldown={cfg.PASS_COOLDOWN_TIME}s"
                    )
                    log_progress(
                        f"[{cfg.BOOSTER_PORT} - {interface_name}] 已達 {threshold}/{threshold}，"
                        f"Onboarding Duration={onboarding_duration}s，進入冷卻觀察 {cfg.PASS_COOLDOWN_TIME} 秒..."
                    )
                    _monitor_wait(cfg.PASS_COOLDOWN_TIME, ser)
                    log_progress(f"[{cfg.BOOSTER_PORT} - {interface_name}] 冷卻完成，執行最後一次 onboarding 確認...")

                    final_ok, final_has_onboarding, final_has_ping, final_has_map_uci, final_has_ip, final_state_value, final_map_uci_value = final_onboarding_check(ser, interface_name)
                    if not final_ok:
                        last_fail_reason = build_onboarding_fail_reason(final_has_onboarding, final_has_ping, final_has_map_uci, has_ip=final_has_ip, prefix="Final Check Fail", state_value=final_state_value, map_uci_value=final_map_uci_value)
                        log_result(f"{interface_name}: FAIL, {last_fail_reason}")
                        log_progress(f"[{cfg.BOOSTER_PORT} - {interface_name}] {last_fail_reason}，計數器歸零並重新開始 polling。")
                        consecutive_count = 0
                        continue

                    log_result(f"{interface_name}: PASS, duration={onboarding_duration}s")
                    log_progress(f"[{cfg.BOOSTER_PORT}] >>> PASS！Onboarding Duration: {onboarding_duration}s <<<")
                    if write_summary_on_pass:
                        _loop_display = loop_str if show_loop_number else summary_loop_display(loop_str, interface_name)
                        write_summary(_loop_display, interface_name, f"{onboarding_duration}s", "PASS", "None")
                    if close_after_use and ser is not None:
                        try:
                            ser.close()
                        except Exception:
                            pass
                    return True

                remain = next_poll_time - time.time()
                if remain > 0:
                    _monitor_wait(remain, ser)
            except Exception as e:
                log_result(f"{interface_name}: FAIL, polling exception {type(e).__name__}: {e}")
                log_progress(f"[{cfg.BOOSTER_PORT}] Polling 異常: {type(e).__name__}: {e}")
                break

    except Exception as e:
        log_result(f"{interface_name}: FAIL, COM Error: {e}")
        log_progress(f"[{cfg.BOOSTER_PORT}] 無法開啟 Serial Port: {e}")
        if write_summary_on_fail:
            _loop_display = loop_str if show_loop_number else summary_loop_display(loop_str, interface_name)
            write_summary(_loop_display, interface_name, "N/A", "FAIL", f"COM Error: {e}")
        if reason_out is not None:
            reason_out.clear()
            reason_out.append(f"COM Error: {e}")
        if close_after_use and ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        return False

    log_result(f"{interface_name}: FAIL, timeout, max_total_limit={max_total_limit}s")
    log_progress(f"[{cfg.BOOSTER_PORT}] >>> 輪詢超時 (FAIL, MAX_TOTAL_LIMIT={max_total_limit} 秒)！ <<<")
    if not locals().get("saw_valid_polling_output", False):
        last_fail_reason = f"No Valid Onboarding Output ({max_total_limit}s timeout, Booster 無回應)"
    if reason_out is not None:
        reason_out.clear()
        reason_out.append(last_fail_reason)
    if write_summary_on_fail:
        _loop_display = loop_str if show_loop_number else summary_loop_display(loop_str, interface_name)
        write_summary(_loop_display, interface_name, f"Timeout({max_total_limit}s)", "FAIL", last_fail_reason)
    if close_after_use and ser is not None:
        try:
            ser.close()
        except Exception:
            pass
    return False


def run_polling_or_recover(loop, interface_name, init_wait_time, threshold, case_name_suffix, duration_start_time=None, max_total_limit=None, restore_eth_bh=True, show_loop_number=False):
    from .recovery import safe_handle_fail_recovery
    from .logger import write_recovery_note

    result = poll_booster_console(
        str(loop),
        interface_name,
        init_wait_time,
        threshold,
        max_total_limit=max_total_limit,
        duration_start_time=duration_start_time,
        show_loop_number=show_loop_number,
    )
    if not result:
        log_step(f"{interface_name}: fail detected, start fail diagnostic / recovery")
        log_progress(f"!! {interface_name} 判定失敗，執行 fail diagnostic / recovery !!")
        write_recovery_note(interface_name)
        safe_handle_fail_recovery(f"Loop{loop}_{cfg.CASE_ID}_{case_name_suffix}", restore_eth_bh=restore_eth_bh)
        return False
    return True