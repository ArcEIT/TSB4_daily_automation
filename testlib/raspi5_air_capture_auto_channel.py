"""Raspi5 air capture helper – auto-scan BSSID and dump packets.

Uses Auto_Scan_BSSID_DumpPackets.sh (auto channel detection via BSSID scan).
Designed for reset cases where TSM4 reboots to auto channel.

Lifecycle:
  After Reset button pressed → start(bssid)   [SSH retry until TSM4 LAN recovers]
  Onboarding PASS           → stop_and_delete()
  Onboarding FAIL           → stop_and_fetch(local_dir)

Enable via config.py:
  RASPI5_AIR_CAPTURE_ENABLE = True
  RASPI5_AIR_CAPTURE_BSSID  = "AA:BB:CC:DD:EE:FF"
"""

import os
import time

from . import config as cfg
from .logger import log_progress

_bssid_safe = None   # bssid with : replaced by - for pcap filename matching
_active = False      # whether a capture session is currently running

_SCAN_LOG  = "/tmp/auto_scan_bssid.log"
_PID_FILE  = "/tmp/auto_scan_bssid.pid"
_SSH_RETRY_INTERVAL = 10   # seconds between SSH retries
_SSH_MAX_RETRIES    = 18   # 18 × 10s = 3 minutes max


def _enabled():
    return bool(getattr(cfg, "RASPI5_AIR_CAPTURE_ENABLE", False))


def is_active():
    return _active


def reset():
    """Reset module state – call at the top of each test loop."""
    global _bssid_safe, _active
    _bssid_safe = None
    _active = False


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

def _make_client():
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
        timeout=10, banner_timeout=10, auth_timeout=10,
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


def _ssh_run_with_retry(command, timeout=30):
    """Retry SSH until connection succeeds (TSM4 LAN may be recovering)."""
    for attempt in range(1, _SSH_MAX_RETRIES + 1):
        ok, out, reason = _ssh_run(command, timeout=timeout)
        if ok:
            return True, out, reason
        log_progress(
            f"[AIR_CAPTURE_AUTO] SSH attempt {attempt}/{_SSH_MAX_RETRIES} failed: {reason} "
            f"– retry in {_SSH_RETRY_INTERVAL}s"
        )
        time.sleep(_SSH_RETRY_INTERVAL)
    return False, "", f"SSH failed after {_SSH_MAX_RETRIES} retries"


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

def start(bssid):
    """SSH to raspi5 (with retry) and start Auto_Scan_BSSID_DumpPackets.sh --bssid <bssid>.

    Retries SSH every 10s (up to 3 min) to handle TSM4 LAN recovery after reset.
    The scan script runs in background and auto-detects channel from BSSID beacons.
    """
    global _bssid_safe, _active
    _bssid_safe = None
    _active = False

    if not _enabled():
        log_progress(f"[AIR_CAPTURE_AUTO] Disabled – skip start (bssid={bssid})")
        return

    if not bssid:
        log_progress("[AIR_CAPTURE_AUTO] No BSSID configured – skip start")
        return

    scan_script = getattr(
        cfg, "RASPI5_AUTO_SCAN_SCRIPT",
        "/home/AirCapture/Auto_Scan_BSSID_DumpPackets.sh"
    )
    out_dir = getattr(cfg, "RASPI5_AIR_CAPTURE_OUT_DIR", "/home/AirCapture")

    # Kill any leftover process from previous run
    _ssh_run(f"[ -f {_PID_FILE} ] && kill $(cat {_PID_FILE}) 2>/dev/null; rm -f {_PID_FILE}", timeout=10)

    cmd = (
        f"nohup {scan_script} --bssid {bssid} "
        f"> {_SCAN_LOG} 2>&1 & echo $! > {_PID_FILE}"
    )
    log_progress(f"[AIR_CAPTURE_AUTO] SSH retry start – bssid={bssid}")
    ok, out, reason = _ssh_run_with_retry(cmd, timeout=15)
    if not ok:
        log_progress(f"[AIR_CAPTURE_AUTO] Start FAIL after retries: {reason}")
        return

    safe = bssid.replace(":", "-")
    _bssid_safe = safe
    _active = True
    log_progress(
        f"[AIR_CAPTURE_AUTO] Scan started in background – bssid={bssid}, "
        f"log={_SCAN_LOG}, pid_file={_PID_FILE}, pcap dir={out_dir}"
    )


def _find_remote_pcap():
    """Find the most recent pcap file matching the BSSID on raspi5."""
    if not _bssid_safe:
        return None
    out_dir = getattr(cfg, "RASPI5_AIR_CAPTURE_OUT_DIR", "/home/AirCapture")
    pattern = f"{out_dir}/capture_{_bssid_safe}_*.pcap"
    ok, out, _ = _ssh_run(f"ls -t {pattern} 2>/dev/null | head -1", timeout=15)
    if ok and out.strip():
        return out.strip()
    return None


def _stop_process():
    """Kill the background scan + tcpdump, keep wlan0mon alive."""
    kill_cmd = (
        f"[ -f {_PID_FILE} ] && kill $(cat {_PID_FILE}) 2>/dev/null; "
        f"pkill -f 'tcpdump.*wlan0mon' 2>/dev/null; "
        f"rm -f {_PID_FILE}; "
        f"sleep 1"
    )
    ok, out, reason = _ssh_run(kill_cmd, timeout=20)
    if ok:
        for line in out.splitlines():
            if line.strip():
                log_progress(f"[AIR_CAPTURE_AUTO] {line}")
    else:
        log_progress(f"[AIR_CAPTURE_AUTO] Stop process FAIL: {reason}")


def stop_and_delete():
    """Stop capture on raspi5 and delete pcap (PASS path)."""
    global _active

    if not _enabled():
        return
    if not _active:
        log_progress("[AIR_CAPTURE_AUTO] Not active – skip stop_and_delete")
        return

    log_progress("[AIR_CAPTURE_AUTO] PASS: stop + delete pcap")
    pcap_path = _find_remote_pcap()
    _stop_process()
    _active = False

    if pcap_path:
        ok, _, reason = _ssh_run(
            f"rm -f {pcap_path} && echo '[AIR_CAPTURE_AUTO] pcap deleted'",
            timeout=15,
        )
        if ok:
            log_progress(f"[AIR_CAPTURE_AUTO] Deleted remote pcap: {pcap_path}")
        else:
            log_progress(f"[AIR_CAPTURE_AUTO] Delete FAIL: {reason}")
    else:
        log_progress("[AIR_CAPTURE_AUTO] No pcap found to delete (scan may not have locked channel yet)")


def stop_and_fetch(local_dir="."):
    """Stop capture on raspi5, SCP pcap to local_dir, delete remote pcap (FAIL path)."""
    global _active

    if not _enabled():
        return
    if not _active:
        log_progress("[AIR_CAPTURE_AUTO] Not active – skip stop_and_fetch")
        return

    log_progress("[AIR_CAPTURE_AUTO] FAIL: stop + fetch pcap")
    pcap_path = _find_remote_pcap()
    _stop_process()
    _active = False

    if not pcap_path:
        log_progress("[AIR_CAPTURE_AUTO] No pcap found to fetch (scan may not have locked channel yet)")
        return

    filename = os.path.basename(pcap_path)
    local_path = os.path.join(local_dir, filename)
    log_progress(f"[AIR_CAPTURE_AUTO] Fetching {pcap_path} → {local_path}")

    ok, reason = _sftp_get(pcap_path, local_path)
    if ok:
        log_progress(f"[AIR_CAPTURE_AUTO] Fetch OK: {local_path}")
        ok2, _, reason2 = _ssh_run(f"rm -f {pcap_path}", timeout=15)
        if ok2:
            log_progress(f"[AIR_CAPTURE_AUTO] Remote pcap deleted: {pcap_path}")
        else:
            log_progress(f"[AIR_CAPTURE_AUTO] Remote delete FAIL: {reason2}")
    else:
        log_progress(f"[AIR_CAPTURE_AUTO] Fetch FAIL: {reason}")
