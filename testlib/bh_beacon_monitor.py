"""Background BH beacon monitor for case6/case7.

Runs Auto_Scan_BSSID_DumpPackets_AllCH.sh on raspi5 in the background
(parallel to the existing onboarding pre-check flow).

Lifecycle:
  relay switches to WiFi BH → start(bssid, duration)
  pre-check ends (PASS)      → stop_and_cleanup()   [delete pcap + log]
  pre-check ends (FAIL)      → stop_and_check()      [returns found, log_text]
                             → fetch_log(local_path) [save log locally]

Result written to summary via the caller (execute_one_backhaul_test).

SSH fallback:
  When GW reboots, LAN may be down and raspi5 SSH times out.
  In that case, pcap deletion falls back to raspi5 serial port (COM5).
"""
import os
import time

import serial

from . import config as cfg
from .logger import log_progress

_bssid      = None
_bssid_safe = None
_active     = False

_SCAN_SCRIPT    = "/home/AirCapture/Auto_Scan_BSSID_DumpPackets_AllCH.sh"
_SCAN_LOG       = "/tmp/bh_beacon_monitor.log"
_PID_FILE       = "/tmp/bh_beacon_monitor.pid"
_PCAP_DIR       = "/home/AirCapture"
_FOUND_MARKER   = "發現目標"
_SSH_RETRIES    = 3
_SSH_RETRY_INT  = 5

# raspi5 serial port (fallback when SSH is unreachable due to GW reboot)
_RASPI5_SERIAL_PORT = "COM5"
_RASPI5_BAUD_RATE   = 115200


def is_active():
    return _active


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


def _ssh(cmd, timeout=30):
    client = None
    try:
        client = _make_client()
        _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="ignore")
        err = stderr.read().decode("utf-8", errors="ignore")
        return True, out + ("\n" + err if err.strip() else "")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


def _ssh_retry(cmd, timeout=30):
    for i in range(1, _SSH_RETRIES + 1):
        ok, out = _ssh(cmd, timeout=timeout)
        if ok:
            return True, out
        log_progress(f"[BH_MONITOR] SSH attempt {i}/{_SSH_RETRIES} failed: {out.strip()} – retry in {_SSH_RETRY_INT}s")
        time.sleep(_SSH_RETRY_INT)
    return False, "SSH failed after all retries"


def _sftp_get(remote, local):
    client = None
    try:
        client = _make_client()
        client.open_sftp().get(remote, local)
        return True, None
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

def start(bssid, duration):
    """Start background beacon scan on raspi5.

    Args:
        bssid:    TSM4 BH BSSID (e.g. "3E:D5:ED:9A:97:67")
        duration: max scan time in seconds (should cover precheck_max_limit)
    """
    global _bssid, _bssid_safe, _active
    _bssid      = None
    _bssid_safe = None
    _active     = False

    scan_script = getattr(cfg, "RASPI5_BH_BEACON_SCAN_SCRIPT", _SCAN_SCRIPT)

    # Kill any leftover
    _ssh(
        f"[ -f {_PID_FILE} ] && kill $(cat {_PID_FILE}) 2>/dev/null; "
        f"pkill -f 'Auto_Scan_BSSID_DumpPackets' 2>/dev/null; "
        f"pkill -f 'tcpdump.*wlan0mon' 2>/dev/null; "
        f"rm -f {_PID_FILE} {_SCAN_LOG}",
        timeout=10,
    )

    cmd = (
        f"nohup timeout {duration} {scan_script} --bssid {bssid} "
        f"> {_SCAN_LOG} 2>&1 & echo $! > {_PID_FILE}"
    )
    log_progress(f"[BH_MONITOR] Start: bssid={bssid}, duration={duration}s, log={_SCAN_LOG}")
    ok, out = _ssh_retry(cmd, timeout=15)
    if not ok:
        log_progress(f"[BH_MONITOR] Start FAIL: {out}")
        return

    _bssid      = bssid
    _bssid_safe = bssid.lower().replace(":", "-")   # tshark outputs lowercase MAC → pcap filename is lowercase
    _active     = True
    log_progress(f"[BH_MONITOR] Scan running in background on raspi5")


def _serial_rm_pcap():
    """Fallback: delete pcap via raspi5 serial port (COM5) when SSH is unreachable."""
    if not _bssid_safe:
        return
    port = getattr(cfg, "RASPI5_SERIAL_PORT", _RASPI5_SERIAL_PORT)
    baud = getattr(cfg, "RASPI5_BAUD_RATE",   _RASPI5_BAUD_RATE)
    cmd  = f"rm -f {_PCAP_DIR}/capture_{_bssid_safe}_*.pcap\r\n"
    log_progress(f"[BH_MONITOR] SSH unavailable – fallback to serial {port} for pcap cleanup")
    ser = None
    try:
        ser = serial.Serial(port, baud, timeout=1)
        ser.write(b"\r\n")
        time.sleep(1)
        ser.read(ser.in_waiting)       # flush prompt
        ser.write(cmd.encode())
        time.sleep(2)
        out = ser.read(ser.in_waiting).decode("utf-8", errors="ignore")
        log_progress(f"[BH_MONITOR] Serial rm result: {out.strip()!r}")
    except Exception as exc:
        log_progress(f"[BH_MONITOR] Serial fallback FAIL: {type(exc).__name__}: {exc}")
    finally:
        if ser and ser.is_open:
            try:
                ser.close()
            except Exception:
                pass


def _kill_and_cleanup():
    """Kill scan process and delete pcap files. Falls back to serial if SSH fails."""
    pcap_rm = f"rm -f {_PCAP_DIR}/capture_{_bssid_safe}_*.pcap 2>/dev/null" if _bssid_safe else "true"
    ok, _ = _ssh(
        f"[ -f {_PID_FILE} ] && kill $(cat {_PID_FILE}) 2>/dev/null; "
        f"pkill -f 'Auto_Scan_BSSID_DumpPackets' 2>/dev/null; "
        f"pkill -9 -f 'tcpdump.*wlan0mon' 2>/dev/null; "
        f"rm -f {_PID_FILE}; sleep 2; "
        f"{pcap_rm}",
        timeout=20,
    )
    if not ok:
        # SSH unreachable (GW just rebooted, LAN down) – use serial fallback
        _serial_rm_pcap()


def stop_and_cleanup():
    """PASS path: stop scan, delete pcap and log, no download."""
    global _active
    if not _active:
        return
    log_progress("[BH_MONITOR] PASS: stop + cleanup")
    _kill_and_cleanup()
    _ssh(f"rm -f {_SCAN_LOG}", timeout=10)
    _active = False


def stop_and_check():
    """FAIL path: stop scan, read log, check if BSSID was seen.

    Returns:
        (found: bool, log_text: str)
    """
    global _active
    if not _active:
        return False, ""

    log_progress("[BH_MONITOR] FAIL: stop + check log")
    ok, log_text = _ssh(f"cat {_SCAN_LOG} 2>/dev/null || echo ''", timeout=10)
    found = ok and _FOUND_MARKER in log_text

    _kill_and_cleanup()
    _active = False

    if found:
        log_progress(f"[BH_MONITOR] BSSID ({_bssid}) WAS detected by raspi5 during WiFi BH window")
    else:
        log_progress(f"[BH_MONITOR] BSSID ({_bssid}) was NOT detected by raspi5 during WiFi BH window")

    return found, log_text


def fetch_pcap(local_dir):
    """FAIL path: download air pcap from raspi5 before _kill_and_cleanup deletes it.

    Must be called BEFORE stop_and_check() because _kill_and_cleanup() (called inside
    stop_and_check) deletes the pcap from raspi5.
    """
    if not _active or not _bssid_safe:
        log_progress("[BH_MONITOR] fetch_pcap: not active or no bssid – skip")
        return
    ok, out = _ssh(
        f"ls -t {_PCAP_DIR}/capture_{_bssid_safe}_*.pcap 2>/dev/null | head -1",
        timeout=10,
    )
    if not ok or not out.strip():
        log_progress("[BH_MONITOR] fetch_pcap: no pcap found on raspi5")
        return
    remote_path = out.strip()
    filename = os.path.basename(remote_path)
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, filename)
    log_progress(f"[BH_MONITOR] Fetching air pcap: {remote_path} → {local_path}")
    ok2, reason = _sftp_get(remote_path, local_path)
    if ok2:
        log_progress(f"[BH_MONITOR] Air pcap saved → {local_path}")
    else:
        log_progress(f"[BH_MONITOR] Air pcap fetch FAIL: {reason}")


def fetch_log(local_path):
    """Download scan log from raspi5 for analysis."""
    ok, reason = _sftp_get(_SCAN_LOG, local_path)
    if ok:
        log_progress(f"[BH_MONITOR] Scan log saved → {local_path}")
    else:
        log_progress(f"[BH_MONITOR] Log fetch FAIL: {reason}")
    _ssh(f"rm -f {_SCAN_LOG}", timeout=10)
