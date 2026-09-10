"""Raspi5 air capture helper – control Runtime_DumpPackets.sh on remote raspi5.

Lifecycle (used in case14):
  Before WPS press  → start(channel)
  WPS onboarding PASS → stop_and_delete()
  WPS onboarding FAIL → stop_and_fetch(local_dir)

Enable via config.py:
  RASPI5_AIR_CAPTURE_ENABLE = True
"""

import os
import re

from . import config as cfg
from .logger import log_progress

_pcap_path = None   # remote pcap path recorded at start time
_active = False     # whether a capture session is currently running


def _enabled():
    return bool(getattr(cfg, "RASPI5_AIR_CAPTURE_ENABLE", False))


def is_active():
    return _active


def reset():
    """Reset module state – call at the top of each test loop."""
    global _pcap_path, _active
    _pcap_path = None
    _active = False


# ---------------------------------------------------------------------------
# SSH / SFTP helpers
# ---------------------------------------------------------------------------

def _make_client():
    """Return a connected paramiko SSHClient, or raise on failure."""
    import paramiko
    host     = getattr(cfg, "RASPI5_SSH_HOST",     "192.168.0.173")
    port     = int(getattr(cfg, "RASPI5_SSH_PORT", 22))
    username = getattr(cfg, "RASPI5_SSH_USERNAME", "root")
    password = getattr(cfg, "RASPI5_SSH_PASSWORD", "arcadyan")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host, port=port,
        username=username, password=password,
        timeout=15, banner_timeout=15, auth_timeout=15,
        look_for_keys=False, allow_agent=False,
    )
    return client


def _ssh_run(command, timeout=30):
    """Run a shell command on raspi5. Returns (ok, stdout, reason)."""
    client = None
    try:
        client = _make_client()
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="ignore")
        err = stderr.read().decode("utf-8", errors="ignore")
        combined = out + ("\n" + err if err.strip() else "")
        return True, combined, "None"
    except Exception as exc:
        return False, "", f"{type(exc).__name__}: {exc}"
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


def _sftp_get(remote_path, local_path):
    """SFTP download from raspi5. Returns (ok, reason)."""
    client = None
    try:
        client = _make_client()
        sftp = client.open_sftp()
        sftp.get(remote_path, local_path)
        sftp.close()
        return True, "None"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start(channel):
    """SSH to raspi5 and start Runtime_DumpPackets.sh on <channel>.

    Records the remote pcap path for later cleanup/fetch.
    """
    global _pcap_path, _active
    _pcap_path = None
    _active = False

    if not _enabled():
        log_progress(f"[AIR_CAPTURE] Disabled – skip start CH{channel}")
        return

    script  = getattr(cfg, "RASPI5_AIR_CAPTURE_SCRIPT",  "/home/AirCapture/Runtime_DumpPackets.sh")
    out_dir = getattr(cfg, "RASPI5_AIR_CAPTURE_OUT_DIR",  "/home/AirCapture")
    bssid   = getattr(cfg, "RASPI5_AIR_CAPTURE_BSSID",    "").strip()

    cmd = f"OUT_DIR={out_dir} {script} on {channel}"
    if bssid:
        cmd += f" {bssid}"
    log_progress(f"[AIR_CAPTURE] Start CH{channel} on raspi5: {cmd}")

    ok, out, reason = _ssh_run(cmd, timeout=30)
    if not ok:
        log_progress(f"[AIR_CAPTURE] Start FAIL: {reason}")
        return

    for line in out.splitlines():
        log_progress(f"[AIR_CAPTURE] {line}")

    # Parse pcap path from "Output file: /path/CH100_....pcap"
    m = re.search(r"Output file:\s*(\S+\.pcap)", out)
    if m:
        _pcap_path = m.group(1).strip()
    else:
        # Fallback: read info file directly
        ok2, info, _ = _ssh_run("cat /tmp/runtime_airdump.info 2>/dev/null")
        if ok2:
            m2 = re.search(r"PCAP=(\S+)", info)
            if m2:
                _pcap_path = m2.group(1).strip()

    if _pcap_path:
        log_progress(f"[AIR_CAPTURE] Recording remote pcap: {_pcap_path}")
        _active = True
    else:
        log_progress("[AIR_CAPTURE] Could not determine pcap path – capture may have failed")


def stop_and_delete():
    """Stop capture on raspi5 and delete pcap (PASS path)."""
    global _active

    if not _enabled():
        return
    if not _active:
        log_progress("[AIR_CAPTURE] Not active – skip stop_and_delete")
        return

    script = getattr(cfg, "RASPI5_AIR_CAPTURE_SCRIPT", "/home/AirCapture/Runtime_DumpPackets.sh")

    delete_cmd = f"rm -f {_pcap_path} && echo '[AIR_CAPTURE] pcap deleted'" if _pcap_path else "true"
    cmd = f"{script} off ; {delete_cmd}"
    log_progress(f"[AIR_CAPTURE] PASS: stop + delete – {cmd}")

    ok, out, reason = _ssh_run(cmd, timeout=20)
    _active = False

    if ok:
        for line in out.splitlines():
            if line.strip():
                log_progress(f"[AIR_CAPTURE] {line}")
    else:
        log_progress(f"[AIR_CAPTURE] stop_and_delete FAIL: {reason}")


def stop_and_fetch(local_dir="."):
    """Stop capture on raspi5, SCP pcap to local_dir, then delete remote pcap (FAIL path)."""
    global _active

    if not _enabled():
        return
    if not _active:
        log_progress("[AIR_CAPTURE] Not active – skip stop_and_fetch")
        return

    script = getattr(cfg, "RASPI5_AIR_CAPTURE_SCRIPT", "/home/AirCapture/Runtime_DumpPackets.sh")

    log_progress("[AIR_CAPTURE] FAIL: stop dump on raspi5")
    ok, out, reason = _ssh_run(f"{script} off", timeout=20)
    _active = False

    if ok:
        for line in out.splitlines():
            if line.strip():
                log_progress(f"[AIR_CAPTURE] {line}")
    else:
        log_progress(f"[AIR_CAPTURE] Stop FAIL: {reason}")

    if not _pcap_path:
        log_progress("[AIR_CAPTURE] No pcap path recorded – skip fetch")
        return

    filename = os.path.basename(_pcap_path)
    local_path = os.path.join(local_dir, filename)
    log_progress(f"[AIR_CAPTURE] Fetching {_pcap_path} → {local_path}")

    ok2, reason2 = _sftp_get(_pcap_path, local_path)
    if ok2:
        log_progress(f"[AIR_CAPTURE] Fetch OK: {local_path}")
        ok3, _, reason3 = _ssh_run(f"rm -f {_pcap_path} && echo '[AIR_CAPTURE] remote pcap deleted'", timeout=15)
        if ok3:
            log_progress(f"[AIR_CAPTURE] Remote pcap deleted: {_pcap_path}")
        else:
            log_progress(f"[AIR_CAPTURE] Remote pcap delete FAIL: {reason3}")
    else:
        log_progress(f"[AIR_CAPTURE] Fetch FAIL: {reason2}")
