"""Shared RE WiFi sync check for Case10 (main WiFi) and Case11 (guest WiFi).

After the TSM4 GUI changes SSID/key, the RE is polled over SSH until the new values appear:
  - UCI: every SSID command matches and every key group has a match (same rules as the old one-shot check);
  - live: the RE's WiFi_inf_ChOnOff.sh "active" column shows the new SSID on enough AP interfaces
    (proves the value is applied, not only written to UCI). Mode: off | warn | fail.
Polling stops at the first match (PASS, sync time known) or at the timeout (FAIL with a one-line summary).
"""
import re
import socket
import subprocess
import time
from dataclasses import dataclass, field

from .logger import log_progress, log_result, log_step
from .serial_console import receive_monitor
from .ssh_client import run_ssh_command

UCI_BEGIN = "__ARC_WIFISYNC_UCI_BEGIN__"
UCI_END = "__ARC_WIFISYNC_UCI_END__"
UCI_ITEM = "__ARC_WIFISYNC_UCI_ITEM__"
LIVE_BEGIN = "__ARC_WIFISYNC_LIVE_BEGIN__"
LIVE_END = "__ARC_WIFISYNC_LIVE_END__"
RE_BEGIN = "__ARC_WIFISYNC_RE_BEGIN__"
RE_END = "__ARC_WIFISYNC_RE_END__"

_LIVE_ROW_RE = re.compile(r"^\s*\d+\s+wifi\d+\s+(ap|sta)\s+")


@dataclass
class SyncSpec:
    tag: str                      # log tag, e.g. "CASE10"
    title: str                    # e.g. "Main WiFi"
    ssid_cmds: list
    key_cmds: list
    key_groups: list = None       # None -> derived from key_cmds (two bands, two candidates each)
    key_mode: str = "per_band_any"
    ssh_timeout: int = 15
    live_net: str = "lan"         # WiFi_inf_ChOnOff.sh "net" column: lan (main) / lan1 (guest)
    live_mode: str = "fail"       # off | warn | fail
    live_min: int = 2             # AP interfaces (2.4G + 5G) that must show the new SSID as active


@dataclass
class SyncResult:
    ok: bool = False
    uci_ok: bool = False
    ssid_ok: int = 0
    ssid_total: int = 0
    key_ok: int = 0
    key_total: int = 0
    live_checked: bool = False
    live_ok: int = 0
    live_pass: bool = True
    actual_ssids: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    ssh_error: str = ""


def _shell_sq(text):
    """Single-quote text for POSIX shell."""
    return "'" + str(text).replace("'", "'\"'\"'") + "'"


def build_cmd(spec, part="all"):
    """Shell command that reads the RE state. part: "all" (SSH), "uci" or "live" (serial: one short command each,
    so the typed echo plus the output stay within one read window)."""
    parts = []
    if part in ("all", "uci"):
        parts.append(f"echo {UCI_BEGIN}")
        for item_type, cmds in (("SSID", spec.ssid_cmds), ("KEY", spec.key_cmds)):
            for cmd in cmds:
                parts.append(
                    "printf " + _shell_sq(f"{UCI_ITEM}|{item_type}|{cmd}|") + "; " + f"{cmd} 2>/dev/null || true" + "; echo"
                )
        parts.append(f"echo {UCI_END}")
    if part in ("all", "live") and spec.live_mode != "off":
        parts.append(f"echo {LIVE_BEGIN}; WiFi_inf_ChOnOff.sh 2>/dev/null; echo {LIVE_END}")
    return "; ".join(parts)


def parse_output(output):
    """Return (uci_values {(type, cmd): value}, live_rows or None)."""
    values = {}
    live_lines = None
    section = None
    for raw in (output or "").replace("\r", "").split("\n"):
        line = raw.strip()
        if line == UCI_BEGIN:
            section = "uci"
            continue
        if line == UCI_END:
            section = None
            continue
        if line == LIVE_BEGIN:
            section = "live"
            live_lines = []
            continue
        if line == LIVE_END:
            section = None
            continue
        if section == "uci" and line.startswith(UCI_ITEM + "|"):
            parts = line.split("|", 3)
            if len(parts) == 4:
                values[(parts[1], parts[2])] = parts[3].strip()
        elif section == "live":
            live_lines.append(raw)
    return values, (parse_live_rows(live_lines) if live_lines is not None else None)


def parse_live_rows(lines):
    """Rows of WiFi_inf_ChOnOff.sh: idx dev mode mld ifnm mapBSS net ssid [active] hid dis disd ch rate."""
    rows = []
    for line in lines or []:
        if not _LIVE_ROW_RE.match(line):
            continue
        tok = line.split()
        if len(tok) < 13:
            continue
        rows.append({"mode": tok[2], "ifnm": tok[4], "net": tok[6], "ssid": tok[7],
                     "active": tok[8] if len(tok) >= 14 else ""})
    return rows


def _key_groups(spec):
    if spec.key_groups:
        return spec.key_groups
    cmds = list(spec.key_cmds)
    return [cmds[:2], cmds[2:4]] if len(cmds) >= 4 else [[c] for c in cmds]


def evaluate(spec, output, expected_ssid, expected_key):
    """Compare one SSH output with the expected values."""
    values, live_rows = parse_output(output)
    res = SyncResult()

    res.ssid_total = len(spec.ssid_cmds)
    actuals = []
    for cmd in spec.ssid_cmds:
        actual = values.get(("SSID", cmd), "")
        actuals.append(actual)
        if actual == expected_ssid:
            res.ssid_ok += 1
        else:
            res.failures.append(f"{cmd}: expected='{expected_ssid}', actual='{actual or '<empty>'}'")
    res.actual_ssids = sorted({a for a in actuals if a})

    if spec.key_mode == "all":
        res.key_total = len(spec.key_cmds)
        for cmd in spec.key_cmds:
            if values.get(("KEY", cmd), "") == expected_key:
                res.key_ok += 1
            else:
                res.failures.append(f"{cmd}: expected='<hidden>', actual='<hidden or empty>'")
    else:
        groups = [g for g in _key_groups(spec) if g]
        res.key_total = len(groups)
        for idx, group in enumerate(groups, start=1):
            if any(values.get(("KEY", cmd), "") == expected_key for cmd in group):
                res.key_ok += 1
            else:
                res.failures.append(f"KEY group {idx}: no matching key UCI among {group}")

    res.uci_ok = res.ssid_ok == res.ssid_total and res.key_ok == res.key_total

    if spec.live_mode != "off":
        res.live_checked = live_rows is not None
        if live_rows is not None:
            res.live_ok = sum(
                1 for r in live_rows if r["mode"] == "ap" and r["net"] == spec.live_net and r["active"] == expected_ssid
            )
        res.live_pass = res.live_ok >= spec.live_min
        if not res.live_pass:
            if live_rows is None:
                res.failures.append("live SSID not read (WiFi_inf_ChOnOff.sh output missing)")
            else:
                res.failures.append(
                    f"live SSID (WiFi_inf_ChOnOff.sh net={spec.live_net}): {res.live_ok}/{spec.live_min} AP interfaces show '{expected_ssid}'"
                )

    res.ok = res.uci_ok and (res.live_pass or spec.live_mode != "fail")
    return res


def _progress_line(spec, n, elapsed, res):
    live = f", live {res.live_ok}/{spec.live_min}" if res.live_checked else ""
    seen = f" (RE SSID: {', '.join(res.actual_ssids) or '<empty>'})" if not res.uci_ok else ""
    if spec.live_mode != "off" and not res.live_checked:
        live = ", live not read"
    return (f"[{spec.tag}][SYNC] check #{n} t={elapsed:.0f}s: SSID {res.ssid_ok}/{res.ssid_total}, "
            f"KEY {res.key_ok}/{res.key_total}{live}{seen}")


def _query_re_state(host, timeout=10):
    """Best-effort: onboarding state / MAP Done of the RE, to explain a sync failure."""
    cmd = (f"echo {RE_BEGIN}; echo STATE=$(cat /tmp/arc_onboarding_state 2>/dev/null); "
           f"echo MAP=$(uci get repacd.MAPConfig.OnboardingDone 2>/dev/null); echo {RE_END}")
    try:
        ok, out, _ = run_ssh_command(host, cmd, timeout=timeout)
    except Exception:
        return ""
    if not ok:
        return ""
    state = map_done = ""
    for line in (out or "").replace("\r", "").split("\n"):
        line = line.strip()
        if line.startswith("STATE="):
            state = line[6:]
        elif line.startswith("MAP="):
            map_done = line[4:]
    return f"RE onboarding state={state or 'N/A'}, MAP Done={map_done or 'N/A'}"


def run_cmd_via_serial(cmd, end_marker, timeout=25):
    """Type the same command on the RE serial console and return (ok, output, reason).

    Used when the PC cannot reach the RE over SSH (seen in WiFi BH: SSH timed out for 261s while the RE had already applied
    the new SSID). Typed on serial, so the Console.log shows the command and the RE's own answer as [SERIAL] lines."""
    from .serial_console import get_serial_for_command, _SERIAL_IO_LOCK
    from .onboarding import _receive_until_marker_or_timeout

    ser = None
    close_after = False
    try:
        ser, close_after = get_serial_for_command()
        if ser is None:
            return False, "", "serial port not available"
        with _SERIAL_IO_LOCK:
            ser.reset_input_buffer()
            ser.write(b"\r\n")
        receive_monitor(0.5, ser)
        with _SERIAL_IO_LOCK:
            ser.reset_input_buffer()
            ser.write((cmd + "\n").encode("utf-8"))
            _, text = _receive_until_marker_or_timeout(ser, end_marker, timeout)
        if not str(text).strip():
            return False, "", "no output on serial"
        return True, text, ""
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}"
    finally:
        if close_after and ser is not None:
            try:
                ser.close()
            except Exception:
                pass


DIAG_BEGIN = "__ARC_SSHDIAG_BEGIN__"
DIAG_END = "__ARC_SSHDIAG_END__"


def pc_local_ip(host):
    """IP address the PC uses to reach `host` (no packet is sent for a UDP connect). Returns "" on failure."""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect((host, 22))
        return sock.getsockname()[0]
    except Exception:
        return ""
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def build_re_diag_cmd(pc_ip):
    """RE-side evidence for "SSH to the RE times out although the RE is up": can the RE reach the PC, what does its
    bridge know, is sshd listening."""
    parts = [f"echo {DIAG_BEGIN}"]
    if pc_ip:
        parts.append(f"ping -c 2 -W 2 {pc_ip}")
        parts.append(f"ip neigh show | grep {pc_ip}")
    parts += [
        "brctl showmacs br-lan | head -20",
        "netstat -ltn | grep ':22 '",
        "ps | grep [d]ropbear",
        f"echo {DIAG_END}",
    ]
    return "; ".join(parts)


def summarize_re_diag(text, pc_ip):
    """One line from the RE-side diagnostic output."""
    t = str(text or "").replace("\r", "")
    loss = re.search(r"(\d+)% packet loss", t)
    ping = f"RE->PC {pc_ip} ping {loss.group(1)}% loss" if loss else (f"RE->PC {pc_ip} ping no result" if pc_ip else "PC ip unknown")
    sshd = "sshd listening" if re.search(r":22\s", t) or "dropbear" in t.split(DIAG_BEGIN)[-1] else "sshd not seen"
    return f"{ping}; {sshd}"


def collect_ssh_failure_diag(host, tag):
    """Evidence when SSH to the RE fails. PC side (ping/arp, run by this script) and RE side (typed on serial, so the
    Console.log shows the commands and the RE's answers as [SERIAL] lines). Never raises; returns a one-line summary."""
    try:
        pc_ip = pc_local_ip(host)
        log_progress(f"[{tag}][SSH_DIAG] SSH to {host} failed; PC ip={pc_ip or 'unknown'}; collecting evidence")
        for cmd in (["ping", "-n", "2", "-w", "1500", host], ["arp", "-a"]):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, errors="ignore").stdout
                lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
                if cmd[0] == "arp":
                    lines = [ln for ln in lines if host in ln] or ["(no arp entry for RE)"]
                else:
                    lines = lines[-4:]  # ping summary lines; the text is localized, so no keyword filter
                log_progress(f"[{tag}][SSH_DIAG][PC-side {cmd[0]}] " + " / ".join(lines)[:300])
            except Exception as e:
                log_progress(f"[{tag}][SSH_DIAG][PC-side {cmd[0]}] failed: {type(e).__name__}: {e}")
        ok, text, reason = run_cmd_via_serial(build_re_diag_cmd(pc_ip), DIAG_END, timeout=30)
        summary = summarize_re_diag(text, pc_ip) if ok else f"RE-side diag not read ({reason})"
        log_progress(f"[{tag}][SSH_DIAG][RE-side, serial] {summary} (details: the [SERIAL] lines above)")
        return summary
    except Exception as e:
        log_progress(f"[{tag}][SSH_DIAG] error: {type(e).__name__}: {e}")
        return ""


def format_failure(spec, res, timeout, attempts, re_state=""):
    """One-line Fail_Reason (the email keeps only that line)."""
    if res is None:
        return f"{spec.title} sync timeout {timeout}s: RE SSH unreachable ({attempts} attempts)"
    if res.ssh_error and not res.uci_ok and res.ssid_ok == 0 and res.key_ok == 0 and not res.actual_ssids:
        head = f"{spec.title} sync timeout {timeout}s: SSH and serial check failed ({res.ssh_error})"
    elif res.uci_ok and not res.live_checked:
        head = f"{spec.title} UCI synced but the live SSID could not be read (WiFi_inf_ChOnOff.sh output missing)"
    elif res.uci_ok:
        head = (f"{spec.title} UCI synced but new SSID not applied on the RE radios "
                f"(live {res.live_ok}/{spec.live_min})")
    else:
        head = (f"{spec.title} not synced to RE in {timeout}s: SSID {res.ssid_ok}/{res.ssid_total}, "
                f"KEY {res.key_ok}/{res.key_total}")
        if res.live_checked:
            head += f", live {res.live_ok}/{spec.live_min}"
        head += f"; RE SSID values: {', '.join(res.actual_ssids) or '<empty>'}"
    tail = f"; checked {attempts} times"
    if re_state:
        tail += f"; {re_state}"
    return head + tail


def poll_until_synced(spec, get_host, expected_ssid, expected_key, timeout, interval, label,
                      health_interval=30):
    """Poll the RE until the new SSID/key are synced. Returns (ok, reason)."""
    from .dut_health import get_dut_uptime_seconds, check_uptime_reset_during_wait

    timeout = max(int(timeout), 1)
    interval = max(int(interval), 1)
    log_step(f"{spec.tag}: poll RE {spec.title} sync via SSH, serial fallback (timeout={timeout}s, interval={interval}s, live={spec.live_mode})")

    baseline = get_dut_uptime_seconds(log_prefix=f"[{spec.tag}][{label}]")
    start = time.monotonic()
    next_health = start + health_interval
    host = None
    last = None
    attempts = 0
    use_serial = False       # sticky: set after SSH failed twice in a row
    ssh_fail_streak = 0
    diag_done = False
    end_marker = LIVE_END if spec.live_mode != "off" else UCI_END

    while True:
        elapsed = time.monotonic() - start
        host = host or get_host()
        if not host:
            use_serial = True
        if True:
            attempts += 1
            cmd = build_cmd(spec)
            ok, output, reason = False, "", ""
            via_serial = use_serial
            if not use_serial:
                cmd_timeout = spec.ssh_timeout + (10 if spec.live_mode != "off" else 0)
                ok, output, reason = run_ssh_command(host, cmd, timeout=cmd_timeout)
                if ok:
                    ssh_fail_streak = 0
                else:
                    ssh_fail_streak += 1
                    use_serial = ssh_fail_streak >= 2
                    log_progress(f"[{spec.tag}][SYNC] check #{attempts} t={elapsed:.0f}s: SSH failed ({reason}); "
                                 f"this check goes through serial"
                                 + ("; serial for the rest of this poll" if use_serial else ""))
                    via_serial = True
                    if not diag_done:
                        diag_done = True
                        collect_ssh_failure_diag(host, spec.tag)
            if via_serial:
                ok, output, reason = run_cmd_via_serial(build_cmd(spec, "uci"), UCI_END, timeout=30)
                if ok and spec.live_mode != "off":
                    live_ok, live_out, live_reason = run_cmd_via_serial(build_cmd(spec, "live"), LIVE_END, timeout=30)
                    if live_ok:
                        output = output + "\n" + live_out
                    else:
                        log_progress(f"[{spec.tag}][SYNC] live table not read over serial ({live_reason})")
            if ok:
                last = evaluate(spec, output, expected_ssid, expected_key)
                log_progress(_progress_line(spec, attempts, elapsed, last) + (" [serial]" if via_serial else ""))
                if last.ok:
                    log_result(f"{spec.tag}: RE {spec.title} sync PASS after {elapsed:.0f}s ({attempts} checks)")
                    if spec.live_mode == "warn" and not last.live_pass:
                        log_progress(f"[{spec.tag}][WARN] UCI synced but live SSID check failed "
                                     f"({last.live_ok}/{spec.live_min}); live mode=warn, not failing")
                    return True, "None"
            else:
                if last is None:
                    last = SyncResult()
                last.ssh_error = str(reason)
                log_progress(f"[{spec.tag}][SYNC] check #{attempts} t={elapsed:.0f}s: "
                             f"{'serial' if via_serial else 'SSH'} check failed ({reason})")

        if elapsed >= timeout:
            break
        receive_monitor(min(interval, max(timeout - elapsed, 1)))

        if time.monotonic() >= next_health:
            next_health = time.monotonic() + health_interval
            if baseline is not None and check_uptime_reset_during_wait(baseline, f"{spec.tag} {label} sync poll"):
                return False, f"DUT_Unexpected_Reboot_During_{label.replace(' ', '_')}_Monitor_Wait"

    log_result(f"{spec.tag}: RE {spec.title} sync FAIL (timeout {timeout}s)")
    if last is not None:
        for failure in last.failures:
            log_progress(f"[{spec.tag}][CHECK][FAIL] {failure}")
    re_state = _query_re_state(host) if host else ""
    return False, format_failure(spec, last, timeout, attempts, re_state)
