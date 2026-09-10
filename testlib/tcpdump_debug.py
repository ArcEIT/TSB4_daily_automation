"""WiFi BH tcpdump debug helper – shared across all test cases.

Lifecycle (hooked automatically via relay.py):
  control_relay("off")                   → start_wifi_bh_tcpdump()
  restore_eth_backhaul(...)              → stop_and_cleanup_wifi_bh_tcpdump()
  restore_eth_backhaul_between_loops(N)  → stop_and_cleanup_wifi_bh_tcpdump()

On FAIL (hooked via recovery.py safe_handle_fail_recovery):
  1. stop_for_download()                 ← kill tcpdump before any collection
  2. collect_diagnosticcomlog_on_fail.py ← downloads tgz + pcap in same SSH session
  3. restore_eth_backhaul()              ← relay back to ETH BH

Enable / disable via config.py:
  WIFI_BH_TCPDUMP_ENABLE = True
"""

from . import config as cfg
from .logger import log_progress
from .serial_console import send_command

_DEFERRED_PID_FILE = "/tmp/tcpdump_deferred.pid"


def _enabled():
    return bool(getattr(cfg, "WIFI_BH_TCPDUMP_ENABLE", False))


# ---------------------------------------------------------------------------
# Start  (relay.py: control_relay("off") → WiFi BH switch)
# ---------------------------------------------------------------------------

def start_wifi_bh_tcpdump():
    """Start tcpdump on ath1 via serial.

    Filter: WIFI_BH_TCPDUMP_FILTER (default "port 67 or port 68"; "" = all traffic).
    Packet cap: WIFI_BH_TCPDUMP_MAX_PACKETS (default 300; 0 = no limit, stop via kill).

    Deferred mode (when RASPI5_AIR_CAPTURE_BSSID is configured):
      Sleeps WIFI_BH_TCPDUMP_BSSID_WAIT_DELAY seconds first to skip the transient
      first-connection window, then polls `iw dev ath1 link` until ath1 reports
      "Connected to <BSSID>" before starting tcpdump.  This avoids starting
      capture on the brief initial association that drops before the stable link.
    """
    if not _enabled():
        return
    iface      = getattr(cfg, "WIFI_BH_TCPDUMP_IFACE",                  "ath1")
    pcap       = getattr(cfg, "WIFI_BH_TCPDUMP_REMOTE_PATH",             "/tmp/wifi_bh_dhcp.pcap")
    max_pkt    = int(getattr(cfg, "WIFI_BH_TCPDUMP_MAX_PACKETS",         300))
    pkt_filter = getattr(cfg, "WIFI_BH_TCPDUMP_FILTER",                  "port 67 or port 68").strip()
    bssid      = getattr(cfg, "RASPI5_AIR_CAPTURE_BSSID",                "").strip()
    delay      = int(getattr(cfg, "WIFI_BH_TCPDUMP_BSSID_WAIT_DELAY",   60))
    poll_tmout = int(getattr(cfg, "WIFI_BH_TCPDUMP_BSSID_POLL_TIMEOUT", 500))

    filter_part = f" {pkt_filter}" if pkt_filter else ""
    cap_part    = f" -c {max_pkt}" if max_pkt > 0 else ""

    if bssid:
        # Deferred: skip the transient first-connection window, then wait for stable link
        log_progress(
            f"[TCPDUMP] Deferred start: sleep {delay}s, poll ath1 for "
            f"'Connected to {bssid}' (timeout={poll_tmout}s) -> {pcap}"
        )
        inner = (
            f'while ! iw dev {iface} link 2>/dev/null | grep -qi "Connected to {bssid}"; '
            f'do sleep 2; done; '
            f'kill $(pgrep tcpdump) 2>/dev/null; '
            f'rm -f {pcap}; '
            f'tcpdump -i {iface} -w {pcap}{cap_part}{filter_part}'
        )
        cmd = (
            f"( sleep {delay}; timeout {poll_tmout} sh -c '{inner}' ) & "
            f"echo $! > {_DEFERRED_PID_FILE}\n"
        )
    else:
        log_progress(f"[TCPDUMP] Start: tcpdump -i {iface}{cap_part}{filter_part} -> {pcap}")
        cmd = (
            f"kill $(pgrep tcpdump) 2>/dev/null; "
            f"rm -f {pcap}; "
            f"tcpdump -i {iface} -w {pcap}{cap_part}{filter_part} &\n"
        )

    send_command(cmd, wait_after=1.5)


# ---------------------------------------------------------------------------
# Stop + cleanup  (relay.py: restore_eth_backhaul / between_loops → PASS path)
# ---------------------------------------------------------------------------

def stop_and_cleanup_wifi_bh_tcpdump():
    """Kill tcpdump and delete pcap when switching back to ETH BH.

    PASS (RE has 192.168.0.x via WiFi BH DHCP): SSH kill + rm pcap.
    FAIL (RE has no 192.168.0.x):               serial kill only.
        Pcap stays on RE; collect_diagnosticcomlog_on_fail.py
        will SCP it via 192.168.1.253 in the same SSH session as tgz.
    """
    if not _enabled():
        return

    pcap = getattr(cfg, "WIFI_BH_TCPDUMP_REMOTE_PATH", "/tmp/wifi_bh_dhcp.pcap")

    # Only try 192.168.0.x – a successful SSH here means PASS → delete pcap.
    # Do NOT try 192.168.1.253: that would wrongly delete the pcap on a FAIL path.
    host = _discover_re_host()
    if host:
        from .ssh_client import run_ssh_command
        ok, _, reason = run_ssh_command(
            host,
            f"kill $(pgrep tcpdump) 2>/dev/null; rm -f {pcap}; echo DONE",
            timeout=10,
        )
        if ok:
            log_progress(f"[TCPDUMP] PASS cleanup via SSH (host={host}): kill+rm OK")
            return
        log_progress(f"[TCPDUMP] SSH cleanup failed ({reason}), fallback to serial kill")

    # No 192.168.0.x found → FAIL path; serial kill, keep pcap for collection
    log_progress("[TCPDUMP] Stop via serial (pcap kept for collect_diagnosticcomlog)")
    send_command(
        f"[ -f {_DEFERRED_PID_FILE} ] && kill $(cat {_DEFERRED_PID_FILE}) 2>/dev/null; "
        f"rm -f {_DEFERRED_PID_FILE}; "
        f"kill $(pgrep tcpdump) 2>/dev/null\n",
        wait_after=1,
    )


# ---------------------------------------------------------------------------
# Stop before collection  (recovery.py: before check_RE_status → FAIL path)
# ---------------------------------------------------------------------------

def stop_for_download():
    """Kill tcpdump (and any deferred starter) via serial so pcap is complete before SCP."""
    if not _enabled():
        return
    log_progress("[TCPDUMP] Stop tcpdump via serial before FAIL collection")
    send_command(
        f"[ -f {_DEFERRED_PID_FILE} ] && kill $(cat {_DEFERRED_PID_FILE}) 2>/dev/null; "
        f"rm -f {_DEFERRED_PID_FILE}; "
        f"kill $(pgrep tcpdump) 2>/dev/null\n",
        wait_after=1,
    )


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _discover_re_host():
    """Discover RE br-lan IPv4 (192.168.0.x) via serial. Returns None if not found."""
    from .serial_console import get_serial_for_command
    from .ssh_client import discover_ssh_host_by_serial
    ser, close_after = None, False
    try:
        ser, close_after = get_serial_for_command()
        return discover_ssh_host_by_serial(ser, force=True, log_prefix="[TCPDUMP]")
    except Exception as exc:
        log_progress(f"[TCPDUMP] host discovery error: {type(exc).__name__}: {exc}")
        return None
    finally:
        if close_after and ser is not None:
            try:
                ser.close()
            except Exception:
                pass
