"""Shared SSH helpers for Booster/RE access.

This module keeps SSH login / command execution and serial-based RE IP discovery
in one place so onboarding checks and environment collection do not duplicate
SSH logic.
"""
import re
import time

from . import config as cfg
from .logger import log_progress
# 【關鍵修正】必須在此精準導入 get_serial_for_command，徹底根除 NameError
from .serial_console import receive_monitor, get_serial_for_command, _SERIAL_IO_LOCK

IFCONFIG_BEGIN_MARKER = "__ARC_IFCONFIG_BEGIN__"
IFCONFIG_END_MARKER = "__ARC_IFCONFIG_END__"

_CACHED_SSH_HOST = None
_LAST_SSH_DISCOVER_TIME = 0


def run_ssh_command(host, command, timeout=None):
    """Run a command on the Booster/RE through SSH."""
    timeout = cfg.ONBOARDING_SSH_TIMEOUT if timeout is None else timeout
    try:
        import paramiko
    except Exception as e:
        return False, "", f"paramiko import failed: {type(e).__name__}: {e}"

    client = None
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host,
            port=int(cfg.ONBOARDING_SSH_PORT),
            username=cfg.ONBOARDING_SSH_USERNAME,
            password=cfg.ONBOARDING_SSH_PASSWORD,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        stdin, stdout, stderr = client.exec_command(command, timeout=max(timeout, 2) + 5)
        out = stdout.read().decode("utf-8", errors="ignore")
        err = stderr.read().decode("utf-8", errors="ignore")
        return True, out + ("\n" + err if err.strip() else ""), "None"
    except Exception as e:
        return False, "", f"{type(e).__name__}: {e}"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _extract_between_markers(output, begin_marker, end_marker):
    if not output:
        return ""
    text = output.replace("\r", "")
    if begin_marker not in text or end_marker not in text:
        return text
    start = text.find(begin_marker) + len(begin_marker)
    end = text.find(end_marker, start)
    if end < 0:
        return text[start:]
    return text[start:end]


def parse_ip_from_ifconfig(output):
    """逐行掃描 ifconfig 輸出，排除 IPv6，只抓取 192.168.0.x 形式的 IPv4 位址。"""
    body = _extract_between_markers(output, IFCONFIG_BEGIN_MARKER, IFCONFIG_END_MARKER)
    if not body:
        body = output

    for raw_line in body.replace("\r", "").split("\n"):
        line = raw_line.strip()
        if "inet6" in line.lower():
            continue
        m = re.search(r"inet\s+(?:addr:)?([0-9.]+)", line)
        if m:
            ip_candidate = m.group(1).strip()
            if ip_candidate.startswith(cfg.ONBOARDING_SSH_IP_PREFIX):
                return ip_candidate
        m2 = re.search(r"addr:([0-9.]+)", line)
        if m2:
            ip_candidate = m2.group(1).strip()
            if ip_candidate.startswith(cfg.ONBOARDING_SSH_IP_PREFIX):
                return ip_candidate
    return None


def get_cached_ssh_host():
    return cfg.ONBOARDING_SSH_HOST or _CACHED_SSH_HOST


def clear_cached_ssh_host():
    global _CACHED_SSH_HOST, _LAST_SSH_DISCOVER_TIME
    _CACHED_SSH_HOST = None
    _LAST_SSH_DISCOVER_TIME = 0


def discover_ssh_host_by_serial(ser=None, *, force=False, log_prefix="[SSH]"):
    """Discover RE br-lan IP through serial console."""
    global _CACHED_SSH_HOST, _LAST_SSH_DISCOVER_TIME

    if cfg.ONBOARDING_SSH_HOST:
        return cfg.ONBOARDING_SSH_HOST
    if _CACHED_SSH_HOST and not force:
        return _CACHED_SSH_HOST

    now = time.time()
    if not force and now - _LAST_SSH_DISCOVER_TIME < cfg.ONBOARDING_SSH_DISCOVER_INTERVAL:
        return None
    _LAST_SSH_DISCOVER_TIME = now

    close_after_use = False
    try:
        if ser is None:
            ser, close_after_use = get_serial_for_command()
        if ser is None:
            return None

        interface = cfg.ONBOARDING_SSH_DISCOVER_INTERFACE
        prefix = cfg.ONBOARDING_SSH_IP_PREFIX
        read_time = getattr(cfg, "ONBOARDING_SSH_DISCOVER_READ_TIME", 8)

        cmd_text = (
            f"echo {IFCONFIG_BEGIN_MARKER}; "
            f"ifconfig {interface}; "
            f"echo {IFCONFIG_END_MARKER}\n"
        )

        with _SERIAL_IO_LOCK:
            ser.write(b"\r\n")
            receive_monitor(0.5, ser)
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
            ser.write(cmd_text.encode("utf-8"))
            output = receive_monitor(read_time, ser)

        ip = parse_ip_from_ifconfig(output)
        if ip:
            _CACHED_SSH_HOST = ip
            return ip

        return None
    except Exception as e:
        log_progress(f"{log_prefix} serial discover RE IP 發生異常: {type(e).__name__}: {e}")
        return None
    finally:
        if close_after_use and ser is not None:
            try:
                ser.close()
            except Exception:
                pass