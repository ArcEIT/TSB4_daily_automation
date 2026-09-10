#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collect /tmp/diagnosticcomlog.tgz from Booster/RE after a case FAIL.

Flow:
  1. SSH login Booster/RE.
  2. Run /usr/scripts/diagnosticcomlog.sh.
  3. Download only /tmp/diagnosticcomlog.tgz.
  4. Save it as <case_title>_diagnosticcomlog.tgz.

No email, no SFTP upload, no zip packaging, no diag.tgz, no pcap.
File transfer uses SCP, not SFTP.
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import paramiko
from scp import SCPClient

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
except Exception:
    pass

DEFAULT_SSH_PORT = 22
DEFAULT_SSH_USERNAME = "25g5@rIj2Z"
DEFAULT_SSH_PASSWORD = "x@u4194j042u/4m,4@"
DEFAULT_SSH_TIMEOUT = 15

REMOTE_DIAGNOSTIC_SCRIPT = "/usr/scripts/diagnosticcomlog.sh"
REMOTE_DIAGNOSTIC_COMLOG = "/tmp/diagnosticcomlog.tgz"
DEFAULT_OUTPUT_DIR = "RE_fail_logs"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def sanitize_windows_filename(value: str) -> str:
    """Keep readable names but remove Windows-invalid filename characters."""
    value = str(value or "").strip()
    value = re.sub(r'[<>:"/\\|?*]+', "_", value)
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" ._")
    return value or "unknown_case"


def parse_hosts(raw_hosts: str) -> list[str]:
    hosts: list[str] = []
    for item in re.split(r"[,;\s]+", raw_hosts or ""):
        host = item.strip()
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def ssh_connect(host: str, port: int, username: str, password: str, timeout: int) -> paramiko.SSHClient:
    log(f"SSH connect to Booster/RE: {host}:{port}, user={username}")
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
    log(f"SSH login PASS: {host}")
    return ssh


def ssh_exec(ssh: paramiko.SSHClient, cmd: str, timeout: int) -> tuple[int, str, str]:
    log(f"[SSH CMD] {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="ignore").strip()
    err = stderr.read().decode("utf-8", errors="ignore").strip()
    exit_code = stdout.channel.recv_exit_status()
    if out:
        log("[STDOUT]")
        print(out, flush=True)
    if err:
        log("[STDERR]")
        print(err, flush=True)
    log(f"[SSH CMD] exit_code={exit_code}")
    return exit_code, out, err


def remote_file_exists(ssh: paramiko.SSHClient, remote_path: str) -> bool:
    """Check remote file existence via SSH command, not SFTP.

    Some Booster/RE firmware supports SSH/SCP but does not expose an SFTP
    subsystem. Avoid ssh.open_sftp() here so the existence check works on
    Dropbear/embedded targets.
    """
    cmd = f'test -f "{remote_path}" && echo EXISTS || echo MISSING'
    exit_code, out, err = ssh_exec(ssh, cmd, timeout=10)
    return exit_code == 0 and "EXISTS" in out


def scp_download(ssh: paramiko.SSHClient, remote_path: str, local_path: Path) -> None:
    """Download remote file by SCP.

    RE SSH access should use SCP for file transfer. Do not use SFTP because
    some RE images close SFTP negotiation with EOF even though SSH commands
    work normally.
    """
    log(f"SCP download: {remote_path} -> {local_path}")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with SCPClient(ssh.get_transport()) as scp:
        scp.get(remote_path, str(local_path))
    log(f"Download PASS: {local_path}")


def collect_from_host(args: argparse.Namespace, host: str, local_path: Path, pcap_local_path: Path | None = None) -> int:
    ssh = None
    try:
        ssh = ssh_connect(host, args.port, args.username, args.password, args.timeout)
        log("Run diagnosticcomlog.sh on Booster/RE...")
        ssh_exec(ssh, args.diagnostic_script, timeout=args.run_timeout)

        if not remote_file_exists(ssh, args.remote_file):
            log(f"ERROR: remote file not found: {args.remote_file}")
            return 1

        scp_download(ssh, args.remote_file, local_path)

        # Also download WiFi BH tcpdump pcap if requested (same SSH session, no extra connect)
        if args.tcpdump_pcap and pcap_local_path:
            if remote_file_exists(ssh, args.tcpdump_pcap):
                log(f"[TCPDUMP] Downloading pcap: {args.tcpdump_pcap}")
                scp_download(ssh, args.tcpdump_pcap, pcap_local_path)
                ssh_exec(ssh, f"rm -f {args.tcpdump_pcap}", timeout=10)
                log(f"[TCPDUMP] Deleted {args.tcpdump_pcap} from RE")
            else:
                log(f"[TCPDUMP] Pcap not found on RE: {args.tcpdump_pcap} (skipped)")

        return 0
    finally:
        if ssh is not None:
            try:
                ssh.close()
                log("SSH connection closed")
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect only /tmp/diagnosticcomlog.tgz after automation case FAIL.")
    parser.add_argument("--hosts", default="192.168.1.253", help="Comma/space separated host list. First successful host is used.")
    parser.add_argument("--case-title", default="unknown_case", help="Readable test case title for output filename, e.g. case1_Factory Default Onboarding")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Local output folder.")
    parser.add_argument("--username", default=DEFAULT_SSH_USERNAME)
    parser.add_argument("--password", default=DEFAULT_SSH_PASSWORD)
    parser.add_argument("--port", type=int, default=DEFAULT_SSH_PORT)
    parser.add_argument("--timeout", type=int, default=DEFAULT_SSH_TIMEOUT)
    parser.add_argument("--run-timeout", type=int, default=180)
    parser.add_argument("--diagnostic-script", default=REMOTE_DIAGNOSTIC_SCRIPT)
    parser.add_argument("--remote-file", default=REMOTE_DIAGNOSTIC_COMLOG)
    parser.add_argument("--tcpdump-pcap", default="", help="Optional remote pcap path to also download (e.g. /tmp/wifi_bh_dhcp.pcap). Downloaded in the same SSH session as diagnosticcomlog.tgz.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    hosts = parse_hosts(args.hosts)
    if not hosts:
        log("ERROR: no hosts specified")
        return 2

    safe_title = sanitize_windows_filename(args.case_title)
    local_path = Path(args.output_dir) / f"{safe_title}_diagnosticcomlog.tgz"
    pcap_local_path: Path | None = None
    if args.tcpdump_pcap:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pcap_local_path = Path(args.output_dir) / f"{safe_title}_ath1_dhcp_{ts}.pcap"

    log("=" * 80)
    log("Collect diagnosticcomlog.tgz after FAIL")
    log(f"Case_Title : {args.case_title}")
    log(f"Hosts      : {', '.join(hosts)}")
    log(f"Remote_File: {args.remote_file}")
    log(f"Local_File : {local_path.resolve()}")
    if args.tcpdump_pcap:
        log(f"Tcpdump_Pcap: {args.tcpdump_pcap} -> {pcap_local_path}")
    log("=" * 80)

    last_error = ""
    for host in hosts:
        try:
            rc = collect_from_host(args, host, local_path, pcap_local_path)
            if rc == 0:
                log("=" * 80)
                log(f"Collect PASS. Host used: {host}")
                log(f"Local file: {local_path.resolve()}")
                log("=" * 80)
                return 0
            last_error = f"remote file missing or collect rc={rc}"
            log(f"WARNING: collect from {host} failed: {last_error}")
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            log(f"WARNING: collect from {host} failed: {last_error}")

    log("=" * 80)
    log(f"ERROR: all hosts failed. Last error: {last_error or 'unknown'}")
    log("=" * 80)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
