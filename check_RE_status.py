#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_RE_status.py

Clean summary-oriented RE diagnostic for fail recovery.

Design goals:
  - Keep Summary.log readable: print compact diagnostic data only.
  - Avoid dumping asynchronous kernel/console spam into Summary.log.
  - Run each serial command independently to avoid concatenated commands.
  - Use grep-based ifconfig checks so br-lan output is limited to IPv4/global IPv6 lines.
  - Auto-switch to SSH when br-lan is 192.168.0.x, otherwise use serial.
"""

import argparse
import re
import socket
import sys
import time
from typing import Dict, Tuple, Optional

import serial
import paramiko


BAUD_RATE = 115200
TIMEOUT = 2

SSH_USERNAME = "25g5@rIj2Z"
SSH_PASSWORD = "x@u4194j042u/4m,4@"
SSH_PORT = 22
SSH_TIMEOUT = 10

SSH_REQUIRED_IP_PREFIX = "192.168.0."
SSH_DISCOVER_INTERFACE = "br-lan"

# Precheck commands are kept, but their output is summarized instead of dumped raw.
PRECHECK_COMMANDS = {
    "WiFi link": "iw dev ath1 link",
    # "DFS CAC": "DFS_CAC_chk.sh",
    "wsplcd": "ps | grep wsplcd",
    "WiFi interfaces": "WiFi_inf_ChOnOff.sh",
    "LED/status": "chk_Status.sh",
}

ETH_BH_BRIDGE_IFS = ["mld3", "mld39.10"]
WIFI_BH_BRIDGE_IFS = ["mld1.10", "mld3", "mld39.10"]


# ==========================================================
# Output helpers: ASCII-only labels to avoid cp950/utf-8 mojibake in Summary.log.
# ==========================================================

def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def info(msg: str) -> None:
    print(msg)


def short(value: str, max_len: int = 96) -> str:
    value = str(value or "").replace("\r", "").replace("\n", " / ").strip()
    return value if len(value) <= max_len else value[: max_len - 3] + "..."


def trim_lines(text: str, max_lines: int = 80, max_width: int = 140):
    """Return readable multiline output without collapsing everything into one line."""
    lines = []
    for raw in (text or "").replace("\r", "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if len(line) > max_width:
            line = line[: max_width - 3] + "..."
        lines.append(line)
    if len(lines) > max_lines:
        omitted = len(lines) - max_lines
        lines = lines[:max_lines] + [f"... <{omitted} lines omitted>"]
    return lines


def print_block(title: str, text: str, max_lines: int = 80) -> None:
    print(f"\n--- {title} ---")
    lines = trim_lines(text, max_lines=max_lines)
    if not lines:
        print("  <empty>")
        return
    for line in lines:
        print(f"  {line}")


# ==========================================================
# Parsers / cleaners
# ==========================================================

def parse_ping_result(output: str) -> Tuple[str, str]:
    if not output or output == "None":
        return "No output", "FAIL"

    low = output.lower()
    m_loss = re.search(r"(\d+)%\s*packet loss", low)
    if m_loss:
        loss = int(m_loss.group(1))
        return f"packet loss {loss}%", "PASS" if loss == 0 else "FAIL"

    if "bytes from" in low or "icmp_seq" in low:
        return "Reply detected", "PASS"

    fail_keywords = [
        "bad address",
        "unknown host",
        "network is unreachable",
        "destination host unreachable",
        "100% packet loss",
        "name or service not known",
    ]
    if any(k in low for k in fail_keywords):
        return "No reply", "FAIL"

    return "Unknown", "FAIL"


def parse_ipv4(output: str) -> Optional[str]:
    patterns = [
        r"inet addr:(\d+\.\d+\.\d+\.\d+)",
        r"\binet\s+(?:addr:)?(\d+\.\d+\.\d+\.\d+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, output or "")
        if m:
            return m.group(1)
    return None


def parse_ipv6(output: str) -> str:
    matches = []
    for line in (output or "").replace("\r", "").splitlines():
        if "inet6" not in line.lower():
            continue
        m = re.search(r"inet6(?: addr:)?\s*([0-9a-fA-F:]+(?:/\d+)?)", line)
        if not m:
            continue
        addr = m.group(1).strip()
        if addr.lower().startswith("fe80"):
            continue
        if not addr.startswith("2"):
            continue
        matches.append(addr)
    return ", ".join(matches)


def parse_eth_uplink(output: str) -> Tuple[str, str, str]:
    values = re.findall(r"\b[01]\b", output or "")
    if not values:
        return "unknown", "Unknown", "FAIL"
    value = values[-1]
    if value == "1":
        return "eth0", "eth0 BH", "PASS"
    if value == "0":
        return "wifi", "WiFi BH", "PASS"
    return "unknown", f"Unknown value: {value}", "FAIL"


def check_bridge_ifs(output: str, required_ifs) -> Tuple[str, str]:
    missing = [iface for iface in required_ifs if iface not in (output or "")]
    if not missing:
        return f"OK ({', '.join(required_ifs)})", "PASS"
    return f"Missing: {', '.join(missing)}", "FAIL"


# 【新增解析器】驗證 repacd MAP Onboarding Done 是否等於 1
def parse_map_config_uci(output: str) -> Tuple[str, str]:
    if not output:
        return "No output", "FAIL"
    cleaned = str(output).strip().lower()
    # 過濾常見的錯誤 spams 或是提示字元
    if "uci get" in cleaned or "not found" in cleaned or "error" in cleaned:
        return f"Error: {short(output, 24)}", "FAIL"
    if cleaned == "1":
        return "1 (Done)", "PASS"
    return f"Value: {short(output, 24)}", "FAIL"


def is_noise_line(line: str, command: str = "") -> bool:
    line = (line or "").strip()
    if not line:
        return True
    if command and line == command:
        return True
    if command and line.endswith("# " + command):
        return True
    if re.match(r"^root@.*[#]$", line):
        return True
    if re.match(r"^root@.*[#]\s*$", line):
        return True
    if re.match(r"^\[\s*\d+\.\d+\]", line):
        return True
    if line.startswith("Hotplug:"):
        return True
    if "hyfi_netlink_receive" in line:
        return True
    if "hyd: ERROR: Bridge is not attached" in line:
        return True
    return False


def clean_serial_output(raw: str, cmd: str) -> str:
    lines = []
    for line in (raw or "").replace("\r", "").split("\n"):
        line = line.strip()
        if is_noise_line(line, cmd):
            continue
        lines.append(line)
    return "\n".join(lines).strip() if lines else "None"


def summarize_wifi_link(output: str) -> str:
    text = output or ""
    if "Not connected" in text:
        return "Not connected"
    m = re.search(r"Connected to\s+([0-9a-fA-F:]{17})", text)
    if m:
        return f"Connected to {m.group(1)}"
    return short(text, 80) if text and text != "None" else "No output"


def summarize_wifi_inf(output: str) -> str:
    keep = []
    for line in (output or "").splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if "backhaul" in s or "fh_" in low or re.match(r"^\d+\s+wifi", s):
            keep.append(s)
    return " / ".join(keep[:8]) if keep else "No key lines"


def summarize_chk_status(output: str) -> str:
    patterns = (
        "cur_led", "last_led", "uplink", "rssi", "wificonnectedto",
        "tmpip", "onboarding", "date:", "error on", "connected on",
    )
    keep = []
    for line in (output or "").splitlines():
        s = line.strip()
        low = s.lower()
        if any(p in low for p in patterns):
            keep.append(s)
    return " / ".join(keep[:12]) if keep else "No key lines"


def summarize_dfs(output: str) -> str:
    if not output or output == "None":
        return "No output"
    lines = [x.strip() for x in output.splitlines() if x.strip()]
    return " / ".join(lines[:6]) if lines else "No output"


# ==========================================================
# Serial commands
# ==========================================================

def drain_serial(ser, duration: float = 0.4) -> None:
    end = time.time() + max(0.0, duration)
    while time.time() < end:
        try:
            ser.read_all()
        except Exception:
            pass
        time.sleep(0.05)


def read_serial_for(ser, wait_time: float) -> str:
    end = time.time() + max(0.2, float(wait_time))
    chunks = []
    while time.time() < end:
        try:
            data = ser.read_all()
            if data:
                chunks.append(data.decode("utf-8", errors="ignore"))
        except Exception:
            pass
        time.sleep(0.1)
    return "".join(chunks)


def send_command_serial(ser, cmd: str, wait_time: float = 1) -> str:
    info(f">>> [SERIAL CMD] {cmd}")
    try:
        ser.write(b"\x03")
        ser.flush()
        time.sleep(0.3)
        ser.write(b"\r\n")
        ser.flush()
        time.sleep(0.3)
        try:
            ser.reset_input_buffer()
        except Exception:
            drain_serial(ser, 0.3)

        ser.write((cmd.strip() + "\r\n").encode("utf-8", errors="ignore"))
        ser.flush()
        raw = read_serial_for(ser, wait_time)
        cleaned = clean_serial_output(raw, cmd)
        info("    -> done")
        return cleaned
    except Exception as e:
        msg = f"SERIAL_ERROR: {type(e).__name__}: {e}"
        info(f"    -> {msg}")
        return msg


def discover_ip_by_serial(ser, interface_name: str):
    cmd = f"ifconfig {interface_name} | grep -i inet"
    out = send_command_serial(ser, cmd, wait_time=2)
    ip = parse_ipv4(out)
    ipv6 = parse_ipv6(out)
    info(f"[DISCOVER] {interface_name} IPv4 = {ip or 'None'}")
    info(f"[DISCOVER] {interface_name} IPv6 = {ipv6 or ''}")
    return ip, ipv6, out


# ==========================================================
# SSH commands
# ==========================================================

def create_ssh_client(host, username, password, port=22, timeout=10):
    info(f"[SSH] connect host={host}, port={port}, user={username}")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        hostname=host,
        port=port,
        username=username,
        password=password,
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
        look_for_keys=False,
        allow_agent=False,
    )
    info("[SSH] connected")
    return ssh


def send_command_ssh(ssh, cmd: str, wait_time: float = 1) -> str:
    info(f">>> [SSH CMD] {cmd}")
    timeout = max(int(wait_time) + 5, SSH_TIMEOUT)
    try:
        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
        stdout.channel.settimeout(timeout)
        out = stdout.read().decode("utf-8", errors="ignore").strip()
        err = stderr.read().decode("utf-8", errors="ignore").strip()
        exit_code = stdout.channel.recv_exit_status()
    except socket.timeout:
        out = ""
        err = f"Command timeout after {timeout}s"
        exit_code = 124
    combined = "\n".join(x for x in [out, err] if x).strip() or "None"
    info(f"    -> rc={exit_code}")
    return combined


# ==========================================================
# Diagnostic flow
# ==========================================================

def run_prechecks(command_runner) -> Dict[str, str]:
    section("PRECHECK COMMAND OUTPUT")
    outputs = {}
    for name, cmd in PRECHECK_COMMANDS.items():
        wait_time = 5 if name in ("WiFi interfaces", "LED/status") else 3
        output = command_runner(cmd, wait_time=wait_time)
        outputs[name] = output

        if name == "WiFi link":
            print_block(f"{name}: {cmd}", summarize_wifi_link(output), max_lines=8)
        elif name == "wsplcd":
            print_block(f"{name}: {cmd}", output, max_lines=20)
        elif name == "WiFi interfaces":
            print_block(f"{name}: {cmd}", output, max_lines=80)
        elif name == "LED/status":
            print_block(f"{name}: {cmd}", output, max_lines=90)
        else:
            print_block(f"{name}: {cmd}", output, max_lines=80)

    return outputs


def run_status_checks(command_runner, initial_ip=None, initial_ipv6=None, initial_ifconfig_output=None, transport="serial"):
    results = {}
    details = {}

    section(f"RE STATUS CHECK - transport={transport}")

    prechecks = run_prechecks(command_runner)
    details["prechecks"] = prechecks

    if initial_ifconfig_output:
        out1 = initial_ifconfig_output
        ip = initial_ip
        ipv6 = initial_ipv6 or parse_ipv6(out1)
        print(f"\n>>> [INFO] Reuse discovery br-lan inet lines")
        print(f"    IPv4: {ip or 'None'}")
        print(f"    IPv6: {ipv6 or ''}")
    else:
        out1 = command_runner(f"ifconfig {SSH_DISCOVER_INTERFACE} | grep -i inet", wait_time=2)
        ip = parse_ipv4(out1)
        ipv6 = parse_ipv6(out1)

    if ip:
        results["IP Address"] = (ip, "PASS" if ip.startswith(SSH_REQUIRED_IP_PREFIX) else "FAIL (Wrong Subnet)")
    else:
        results["IP Address"] = ("None", "FAIL (No IPv4)")
    results["IPv6 Address"] = (ipv6 or "", "PASS" if ipv6 else "SKIP")

    # 2. Backhaul.
    out_bh = command_runner("cat /tmp/eth_uplink", wait_time=1)
    bh_mode, bh_value, bh_status = parse_eth_uplink(out_bh)
    results["Backhaul"] = (bh_value, bh_status)

    # 3. WiFi Status only meaningful in WiFi BH.
    if bh_mode == "wifi":
        out2 = prechecks.get("WiFi link") or command_runner("iw dev ath1 link", wait_time=2)
        results["WiFi Status"] = (summarize_wifi_link(out2), "PASS" if "Connected" in out2 else "FAIL")
    elif bh_mode == "eth0":
        results["WiFi Status"] = ("Skip in eth0 BH", "SKIP")
    else:
        results["WiFi Status"] = ("Skip: unknown backhaul", "SKIP")

    # 4. Onboarding.
    out3 = command_runner("cat /tmp/arc_onboarding_state", wait_time=1)
    state = short(out3 if out3 else "none", 80)
    results["Onboarding"] = (f"Status: {state}", "PASS" if "done" in (out3 or "").lower() else "FAIL")

    # 【新需求注入】多出一個條件檢查：uci get repacd.MAPConfig.OnboardingDone 是否等於 1，排在 Onboarding 下方
    out_map = command_runner("uci get repacd.MAPConfig.OnboardingDone", wait_time=1)
    results["repacd MAP Done"] = parse_map_config_uci(out_map)

    # 5. Bridge IFs.
    out4 = command_runner("brctl show", wait_time=2)
    if bh_mode == "eth0":
        required = ETH_BH_BRIDGE_IFS
    elif bh_mode == "wifi":
        required = WIFI_BH_BRIDGE_IFS
    else:
        required = WIFI_BH_BRIDGE_IFS
    results["Bridge IFs"] = check_bridge_ifs(out4, required)

    # 6. Ping tests.
    out5 = command_runner("ping www.google.com -4 -c 3", wait_time=8)
    results["IPv4 Ping"] = parse_ping_result(out5)

    out6 = command_runner("ping www.google.com -6 -c 3", wait_time=8)
    results["IPv6 Ping"] = parse_ping_result(out6)

    return results


def print_summary(results, transport, ssh_host=None) -> int:
    section("DIAGNOSTIC SUMMARY")
    print(f"TRANSPORT                    | {transport}" + (f" ({ssh_host})" if ssh_host else ""))
    print("-" * 92)
    header = f"{'CHECK ITEM':<28} | {'VALUE':<48} | {'STATUS'}"
    print(header)
    print("-" * len(header))
    for item, (val, status) in results.items():
        print(f"{item:<28} | {short(val, 48):<48} | {status}")
    print("-" * len(header))
    fails = [k for k, v in results.items() if "FAIL" in v[1]]
    if not fails:
        print(">>> [RESULT]: ALL CHECK ITEMS NORMAL (PASS)")
        return 0
    print(f">>> [RESULT]: DETECTED FAILURES IN: {', '.join(fails)}")
    return 1


# ==========================================================
# Args / main
# ==========================================================

def parse_args():
    parser = argparse.ArgumentParser(description="RE status diagnostic. Auto SSH when br-lan is 192.168.0.x, fallback serial otherwise.")
    parser.add_argument("com_port", nargs="?", help="Serial COM port, e.g. COM4")
    parser.add_argument("--com-port", dest="com_port_opt", help="Serial COM port, e.g. COM4")
    parser.add_argument("--ssh-username", default=SSH_USERNAME, help="RE SSH username")
    parser.add_argument("--ssh-password", default=SSH_PASSWORD, help="RE SSH password")
    parser.add_argument("--ssh-port", type=int, default=SSH_PORT, help="RE SSH port")
    parser.add_argument("--ssh-timeout", type=int, default=SSH_TIMEOUT, help="RE SSH timeout seconds")
    parser.add_argument("--force-serial", action="store_true", help="Always run diagnostic through serial, do not auto SSH")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_port = args.com_port_opt or args.com_port
    if not target_port:
        print("ERROR: COM port is required")
        print("Example 1: python check_RE_status.py COM4")
        print("Example 2: python check_RE_status.py --com-port COM4")
        return 2

    target_port = target_port.upper()
    ser = None
    ssh = None

    try:
        section("RE STATUS DIAGNOSTIC START")
        print(f"COM_Port: {target_port}")
        print("Mode: serial discovery first; use SSH only when br-lan IPv4 is 192.168.0.x")

        ser = serial.Serial(target_port, BAUD_RATE, timeout=TIMEOUT)

        discover_ip, discover_ipv6, discover_output = discover_ip_by_serial(ser, SSH_DISCOVER_INTERFACE)
        use_ssh = bool(discover_ip and discover_ip.startswith(SSH_REQUIRED_IP_PREFIX) and not args.force_serial)

        if use_ssh:
            section("TRANSPORT SELECTED: SSH")
            print(f"br-lan IPv4 is {discover_ip}; switch diagnostic to SSH")
            try:
                ser.close()
                ser = None
                print(f"[SERIAL] closed {target_port}")
            except Exception as close_error:
                print(f"[SERIAL] close failed: {close_error}")

            ssh = create_ssh_client(
                host=discover_ip,
                username=args.ssh_username,
                password=args.ssh_password,
                port=args.ssh_port,
                timeout=args.ssh_timeout,
            )
            command_runner = lambda cmd, wait_time=1: send_command_ssh(ssh, cmd, wait_time=wait_time)
            results = run_status_checks(
                command_runner,
                initial_ip=discover_ip,
                initial_ipv6=discover_ipv6,
                initial_ifconfig_output=discover_output,
                transport="ssh",
            )
            return print_summary(results, transport="SSH", ssh_host=discover_ip)

        reason = "--force-serial enabled" if args.force_serial else f"br-lan IPv4 is not {SSH_REQUIRED_IP_PREFIX}x"
        section("TRANSPORT SELECTED: SERIAL")
        print(reason)
        command_runner = lambda cmd, wait_time=1: send_command_serial(ser, cmd, wait_time=wait_time)
        results = run_status_checks(
            command_runner,
            initial_ip=discover_ip,
            initial_ipv6=discover_ipv6,
            initial_ifconfig_output=discover_output,
            transport="serial",
        )
        return print_summary(results, transport="SERIAL")

    except Exception as e:
        print(f"\n[ERROR]: diagnostic failed - {type(e).__name__}: {e}")
        return 1

    finally:
        if ssh:
            try:
                ssh.close()
                print("[SSH] connection closed")
            except Exception:
                pass
        if ser:
            try:
                ser.close()
                print(f"[SERIAL] closed {target_port}")
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())