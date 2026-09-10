import os
import glob
import zipfile
import smtplib
import shutil
import paramiko
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')



def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_step(message):
    print(f"[{_ts()}] [PROGRESS-STEP] >>> {message}", flush=True)


def log_result(message):
    print(f"[{_ts()}] [PROGRESS-RESULT] >>> {message}", flush=True)


def log_progress(message):
    print(f"[{_ts()}] [PROGRESS] >>> {message}", flush=True)
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime

# ==================== SFTP Upload Setting ====================
# 對齊 Download_fw_then_upgrade.py 的 SFTP 設定。
SFTP_HOST = "arc-sftp.arcadyan.com.tw"
SFTP_PORT = 22
SFTP_USER = "arctaxbooster4"
SFTP_PASS = "%C82B5B3"
SFTP_UPLOAD_ROOT = "/TA_booster4/DailyBuild_Automation"
# ============================================================


def send_email(subject, body, attachments=None):
    log_step("Final collect: send email report")
    sender = 'arctest3903@gmail.com'
    # receiver = 'bill_chen@arcadyan.com'
    receivers = [
    'bill_chen@arcadyan.com',
    'zach_chu@arcadyan.com',
    'chocho_chen@arcadyan.com',
    'dennis_chiang@arcadyan.com',
    'quantum_wu@arcadyan.com',    
    ]
    app_password = 'apthsnwksezkwtbo'

    if attachments is None:
        attachments = []

    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = sender
   # msg['To'] = receiver
    msg['To'] = ', '.join(receivers)
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    for file_path in attachments:
        if os.path.exists(file_path):
            with open(file_path, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(file_path)}"')
            msg.attach(part)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, app_password)
            server.send_message(msg)
        log_result("Final collect: email send PASS")
        return True, "None"
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        log_result(f"Final collect: email send FAIL, reason={reason}")
        return False, reason


def sftp_mkdir_p(sftp, remote_dir):
    """遞迴建立 SFTP 遠端資料夾。"""
    parts = [p for p in remote_dir.replace("\\", "/").split("/") if p]
    current = "/" if remote_dir.startswith("/") else ""

    for part in parts:
        if current in ("", "/"):
            current = current + part if current == "/" else part
        else:
            current = current + "/" + part

        try:
            sftp.stat(current)
        except IOError:
            sftp.mkdir(current)


def upload_files_to_sftp(local_files, remote_dir):
    """將指定檔案上傳到 SFTP remote_dir。"""
    uploaded = []
    transport = None

    try:
        log_step(f"Final collect: connect SFTP {SFTP_HOST}:{SFTP_PORT}")
        transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
        transport.connect(username=SFTP_USER, password=SFTP_PASS)
        sftp = paramiko.SFTPClient.from_transport(transport)

        log_result("Final collect: SFTP login PASS")
        log_step(f"Final collect: ensure SFTP folder {remote_dir}")
        sftp_mkdir_p(sftp, remote_dir)

        for local_file in local_files:
            if not os.path.exists(local_file):
                log_progress(f"SFTP Skip，不存在: {local_file}")
                continue

            remote_path = remote_dir.rstrip("/") + "/" + os.path.basename(local_file)
            log_step(f"Final collect: upload {os.path.basename(local_file)} to SFTP")
            log_progress(f"SFTP Upload: {local_file} -> {remote_path}")
            sftp.put(local_file, remote_path)
            uploaded.append(remote_path)

        sftp.close()
        transport.close()
        log_result(f"Final collect: SFTP upload PASS, uploaded={len(uploaded)}")
        return True, uploaded, "None"

    except Exception as e:
        try:
            if transport:
                transport.close()
        except Exception:
            pass

        log_result(f"Final collect: SFTP upload FAIL, reason={type(e).__name__}: {e}")
        return False, uploaded, f"{type(e).__name__}: {e}"


CASE_DESCRIPTIONS = {
    "case1_Factory Default Onboarding":
        "Factory reset the Booster to default settings via serial command, then verify it re-onboards via ETH BH and WiFi BH.",
    "case2_ETH-WiFi-ETH BH Onboarding":
        "Verify Booster completes onboarding via ETH BH, then switch to WiFi BH and verify onboarding recovers, then switch back to ETH BH and verify onboarding recovers again.",
    "case3_RE Warm Reboot Onboarding":
        "Send software reboot command to Booster via serial console (warm reboot), then verify it reconnects and completes onboarding via ETH BH and WiFi BH.",
    "case4_RE Cold Reboot Onboarding":
        "Power-cycle the Booster by cutting and restoring power (cold reboot), then verify it reconnects and completes onboarding via ETH BH and WiFi BH.",
    "case5_TSM4 Restart Onboarding":
        "Trigger full TSM4 reboot via GUI Restart button, then verify the Booster reconnects and completes onboarding after TSM4 comes back online.",
    "case6_Reboot GW+RE Onboarding":
        "Trigger simultaneous reboot of GW and Booster via TSM4 GUI Mesh page 'Reboot GW+RE' button, then verify Mesh topology restores and onboarding completes.",
    "case7_Reset Router+Boosters Onboarding":
        "Factory reset both GW and Booster via TSM4 GUI Mesh page 'Reset Router+Boosters' button, then verify Mesh network rebuilds from scratch and onboarding completes.",
    "case8_Reboot RE Onboarding":
        "Trigger Booster reboot via TSM4 GUI Mesh page 'Reboot RE' button, then verify the Booster reconnects and completes onboarding via ETH BH and WiFi BH.",
    "case9_Reset RE Onboarding":
        "Factory reset the Booster via TSM4 GUI Mesh page 'Reset RE' button, then verify the Booster re-onboards to the Mesh network via ETH BH and WiFi BH.",
    "case10_Main_WiFi_Random_SSID_Key_Sync_SpecialChar":
        "Change Main Wi-Fi SSID to a random value and password to a random value with special characters via TSM4 GUI, then verify the settings sync correctly from GW to Booster.",
    "case11_Guest_WiFi_Random_SSID_Key_Sync_SpecialChar":
        "Change Guest Wi-Fi SSID to a random value and password to a random value with special characters via TSM4 GUI, then verify the settings sync correctly from GW to Booster.",
    "case12_TSM4_Wireless_FH_Disable_Enable_Sync_Check":
        "Disable Main Wi-Fi (Fronthaul) from TSM4 GUI and verify Booster BH SSID changes to BH-prefixed pattern; re-enable Main Wi-Fi and verify Booster BH SSID restores correctly.",
    "case13_BH_Random_SSID_Lost_Connect_Check":
        "Randomly change the Backhaul SSID on TSM4 and verify the Booster detects the disconnection and successfully reconnects with the new BH SSID.",
    "case14_TSM4_WPS_RE_Onboarding":
        "Trigger TSM4 5GHz WPS push button from GUI and run WPS PBC command on Booster simultaneously, then verify WiFi BH onboarding completes via WPS pairing.",
}


# ==================== Crash / Kernel Panic Scanner ====================
_CRASH_PRIMARY   = ["Kernel panic", "Fatal exception"]
_CRASH_SECONDARY = [
    "Unable to handle kernel", "Rebooting in", "CRASHED",
    "Crash shutdown", "Oops:", "platform_mlo.c",
]
_CRASH_EXCLUDE_PATTERN = "adfs"   # filter out adfs debug lines

import re as _re


def _line_is_crash(line, keywords, exclude=None):
    low = line.lower()
    if exclude and exclude in low:
        return False
    for kw in keywords:
        if kw.lower() in low:
            return kw
    return None


def scan_console_for_crashes(console_files):
    """Scan *Console.log files and return a crash report dict.

    Returns:
        {
          "total_panic": int,          # Kernel panic / Fatal exception count
          "case_results": [            # one entry per file
            {
              "filename": str,
              "primary": [(lineno, ts, kw, preview), ...],
              "secondary": [(lineno, ts, kw, preview), ...],
            }, ...
          ]
        }
    """
    ts_re = _re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    case_results = []
    total_panic = 0

    for fpath in console_files:
        primary = []
        secondary = []
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                for lineno, raw in enumerate(f, 1):
                    line = raw.rstrip()
                    m = ts_re.search(line)
                    ts = m.group(1) if m else ""
                    preview = line.strip()[:150]

                    kw = _line_is_crash(line, _CRASH_PRIMARY)
                    if kw:
                        primary.append((lineno, ts, kw, preview))
                        continue
                    kw2 = _line_is_crash(line, _CRASH_SECONDARY, exclude=_CRASH_EXCLUDE_PATTERN)
                    if kw2:
                        secondary.append((lineno, ts, kw2, preview))
        except Exception as exc:
            primary.append((0, "", "READ_ERROR", str(exc)))

        total_panic += len(primary)
        case_results.append({
            "filename": os.path.basename(fpath),
            "primary": primary,
            "secondary": secondary,
        })

    # Sort: crash cases first
    case_results.sort(key=lambda r: (0 if r["primary"] else 1, r["filename"]))
    return {"total_panic": total_panic, "case_results": case_results}


def build_crash_summary_text(crash_report):
    """Build human-readable crash summary block for email / log."""
    lines = []
    sep = "=" * 72
    total = crash_report["total_panic"]

    lines.append(sep)
    lines.append("  DUT CRASH / KERNEL PANIC SUMMARY")
    lines.append(f"  Kernel panic / Fatal exception total: {total}")
    lines.append(sep)

    for r in crash_report["case_results"]:
        if not r["primary"] and not r["secondary"]:
            continue
        status = f"KERNEL PANIC x{len(r['primary'])}" if r["primary"] else "crash-related"
        lines.append(f"\n[{status}]  {r['filename']}")
        if r["primary"]:
            lines.append(f"  Primary ({len(r['primary'])} event(s)):")
            for lineno, ts, kw, preview in r["primary"]:
                ts_str = f"  {ts}" if ts else ""
                lines.append(f"    L{lineno:>6}{ts_str}  [{kw}]")
                lines.append(f"           {preview}")
        if r["secondary"]:
            lines.append(f"  Secondary context ({len(r['secondary'])} hit(s)):")
            for lineno, ts, kw, preview in r["secondary"][:10]:  # cap at 10 for readability
                ts_str = f"  {ts}" if ts else ""
                lines.append(f"    L{lineno:>6}{ts_str}  [{kw}]")
                lines.append(f"           {preview}")
            if len(r["secondary"]) > 10:
                lines.append(f"    ... and {len(r['secondary']) - 10} more secondary hits")

    lines.append(sep)
    return "\n".join(lines)


def build_crash_email_highlight(crash_report):
    """Short crash highlight block for email body (top section)."""
    total = crash_report["total_panic"]
    if total == 0:
        return "No Kernel panic / Fatal exception detected."

    lines = [f"⚠ Kernel panic / Fatal exception detected in {total} event(s):"]
    for r in crash_report["case_results"]:
        if r["primary"]:
            lines.append(f"  - {r['filename']}  →  PANIC x{len(r['primary'])}")
    return "\n".join(lines)
# ======================================================================


def normalize_case_name(raw_case_name):
    """Normalize the case name read from the first line of Summary.log."""
    case_name = raw_case_name.strip()
    case_name = case_name.lstrip("- ").strip()
    return case_name


def format_case_description(case_name):
    """Format case name and short purpose for the email body."""
    description = CASE_DESCRIPTIONS.get(
        case_name,
        "Run this test case and verify the expected onboarding or configuration result."
    )
    return f"{case_name}:\n  {description}"


def extract_last_diagnostic_summary(lines):
    """Extract the last DIAGNOSTIC SUMMARY table from a Summary.log file."""
    summary_indexes = [idx for idx, line in enumerate(lines) if "DIAGNOSTIC SUMMARY" in line]
    if not summary_indexes:
        return ""

    header_idx = summary_indexes[-1]
    start_idx = header_idx

    # Include the separator line right before DIAGNOSTIC SUMMARY, if it exists.
    for idx in range(header_idx - 1, -1, -1):
        stripped = lines[idx].strip()
        if stripped and set(stripped) <= {"="}:
            start_idx = idx
            break
        if stripped and set(stripped) <= {"-"}:
            continue
        if header_idx - idx > 5:
            break

    end_idx = len(lines)
    for idx in range(header_idx + 1, len(lines)):
        if ">>> [RESULT]" in lines[idx]:
            end_idx = idx + 1
            break

    return "".join(lines[start_idx:end_idx]).rstrip()


def main():
    log_step("Final collect start: scan Summary/Console/diagnosticcomlog files")
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    remote_dir = SFTP_UPLOAD_ROOT.rstrip("/")

    summary_files = sorted(glob.glob("*Summary.log"))
    console_files = sorted(glob.glob("*Console.log"))

    # Diagnostic tgz files are produced by per-case fail recovery.
    # This final collect script must NOT SSH/login/download diagnosticcomlog.tgz.
    # It only packages existing local *_diagnosticcomlog.tgz files into the final ZIP.
    diagnostic_files = sorted(set(
        glob.glob("*diagnosticcomlog.tgz") +
        glob.glob(os.path.join("RE_fail_logs", "*diagnosticcomlog.tgz")) +
        glob.glob(os.path.join("RE_fail_logs", "**", "*diagnosticcomlog.tgz"), recursive=True)
    ))

    tsm4_gui_files = sorted(set(
        glob.glob("*_tsm4_gui_factory_default.log") +
        glob.glob("*_tsm4_gui_reboot_booster.log")
    ))

    screenshot_files = sorted(glob.glob("*.png"))

    log_result(f"Final collect: found Summary={len(summary_files)}, Console={len(console_files)}, diagnostic={len(diagnostic_files)}, tsm4_gui_log={len(tsm4_gui_files)}, screenshot={len(screenshot_files)}")

    # ---- Crash scan ----
    log_step("Final collect: scan Console logs for Kernel panic / Fatal exception")
    crash_report = scan_console_for_crashes(console_files)
    log_result(f"Final collect: crash scan done, total_panic={crash_report['total_panic']}")

    if not summary_files:
        log_result("Final collect FAIL: no Summary.log files found")
        return 1

    fw_version = "Unknown_FW"
    with open(summary_files[0], "r", encoding="utf-8") as f:
        for line in f:
            if "Booster Firmware Version" in line:
                fw_version = line.split(":")[-1].strip().replace("/", "_")
                break
            if "Firmware version:" in line and "root@" not in line:
                fw_version = line.split(":")[-1].strip().replace("/", "_")
                break

    log_result(f"Final collect: Booster FW version = {fw_version}")

    target_folder = f"{fw_version}"
    if not os.path.exists(target_folder):
        log_step(f"Final collect: create local report folder {target_folder}")
        os.makedirs(target_folder)
        log_result(f"Final collect: local report folder ready = {target_folder}")

    all_summary_name = os.path.join(target_folder, f"{fw_version}_all_case_summary.log")
    zip_name = os.path.join(target_folder, f"{fw_version}_TestReport_{now_str}.zip")

    critical_issues = []
    failed_diagnostic_summaries = []
    latest_diagnostic_summary = ""
    fail_count = 0
    pass_count = 0
    case_list = []

    for f_path in summary_files:
        has_real_fail = False
        fname = os.path.basename(f_path)
        with open(f_path, "r", encoding="utf-8") as infile:
            lines = infile.readlines()
            diagnostic_summary = extract_last_diagnostic_summary(lines)
            if diagnostic_summary:
                latest_diagnostic_summary = diagnostic_summary

            for i, line in enumerate(lines):
                parts = [p.strip().upper() for p in line.split("|")]
                if len(parts) >= 5 and ("FAIL" in parts or "TIMEOUT" in parts):
                    issue_text = line.strip()
                    for j in range(i + 1, min(i + 6, len(lines))):
                        next_line = lines[j].strip()
                        if not next_line:
                            continue
                        if next_line.startswith("Fail_Reason"):
                            if next_line.startswith("Fail_Reason:"):
                                issue_text += f"\n    {next_line}"
                            else:
                                # "Fail_Reason ( ctx )" + reason on next line (case12 with status)
                                issue_text += f"\n    {next_line}"
                                for k in range(j + 1, min(j + 3, len(lines))):
                                    reason_line = lines[k].strip()
                                    if reason_line and "|" not in reason_line and not reason_line.startswith(("=", "-", "備註")):
                                        issue_text += f"\n    {reason_line}"
                                        break
                            break
                        if "|" in next_line or next_line.startswith(("=", "-")):
                            break
                    critical_issues.append(f"[ISSUE in {fname}]: {issue_text}")
                    has_real_fail = True

            if lines:
                case_name = normalize_case_name(lines[0])
                case_list.append(format_case_description(case_name))

        if has_real_fail:
            fail_count += 1
            if diagnostic_summary:
                failed_diagnostic_summaries.append(f">> FILE: {fname}\n{diagnostic_summary}")
        else:
            pass_count += 1

    log_step(f"Final collect: build all-case summary {all_summary_name}")
    with open(all_summary_name, "w", encoding="utf-8") as outfile:
        outfile.write("=" * 95 + "\n")
        outfile.write(f" AUTOMATION TEST REPORT - {fw_version}\n")
        outfile.write(f" Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        outfile.write("=" * 95 + "\n\n")
        outfile.write(
            f"[ Statistics ]\n"
            f" Status: {'PASS' if fail_count == 0 else 'FAIL'}\n"
            f" Total: {len(summary_files)}\n"
            f" Pass: {pass_count}\n"
            f" Fail: {fail_count}\n\n"
        )

        outfile.write("[ SFTP Upload Target ]\n")
        outfile.write(f"  - {remote_dir}\n\n")

        if diagnostic_files:
            outfile.write("[ Diagnostic Files Included in ZIP ]\n")
            for diag_f in diagnostic_files:
                outfile.write(f"  - {os.path.basename(diag_f)}\n")
            outfile.write("\n")

        if tsm4_gui_files:
            outfile.write("[ TSM4 GUI Logs Included in ZIP ]\n")
            for gui_log in tsm4_gui_files:
                outfile.write(f"  - {os.path.basename(gui_log)}\n")
            outfile.write("\n")

        if screenshot_files:
            outfile.write("[ GUI Screenshots Included in ZIP ]\n")
            for ss in screenshot_files:
                outfile.write(f"  - {os.path.basename(ss)}\n")
            outfile.write("\n")

        if critical_issues:
            outfile.write("[ CRITICAL ISSUES FOUND ]\n")
            for issue in critical_issues:
                outfile.write(f"!! {issue}\n")
            outfile.write("-" * 50 + "\n\n")

        # Crash summary section
        crash_text = build_crash_summary_text(crash_report)
        outfile.write("[ DUT Crash / Kernel Panic Summary ]\n")
        outfile.write(crash_text + "\n\n")

        outfile.write("[ Detailed Logs ]\n")
        for f_path in summary_files:
            outfile.write(f"\n>> FILE: {os.path.basename(f_path)}\n")
            with open(f_path, "r", encoding="utf-8") as infile:
                outfile.write(infile.read())
            outfile.write("\n" + "=" * 95 + "\n")

    log_result(f"Final collect: all-case summary generated, pass={pass_count}, fail={fail_count}")

    # Write standalone crash summary log
    crash_summary_file = os.path.join(target_folder, f"{fw_version}_all_crash_summary.log")
    with open(crash_summary_file, "w", encoding="utf-8") as cf:
        cf.write(f"POST-RUN CRASH SUMMARY\n")
        cf.write(f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        cf.write(f"FW        : {fw_version}\n\n")
        cf.write(build_crash_summary_text(crash_report) + "\n")
    log_result(f"Final collect: crash summary written to {crash_summary_file}")

    log_step(f"Final collect: create ZIP report {zip_name}")
    files_to_zip = [all_summary_name, crash_summary_file] + summary_files + console_files + diagnostic_files + tsm4_gui_files + screenshot_files

    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        for log_f in files_to_zip:
            if os.path.exists(log_f):
                zf.write(log_f, os.path.basename(log_f))

    log_result(f"Final collect: ZIP created, files_included={len(files_to_zip)}")

    # all_case_summary.log 已放進 zip，SFTP 只上傳 zip。
    upload_files = [zip_name]
    sftp_ok, uploaded_paths, sftp_error = upload_files_to_sftp(upload_files, remote_dir)

    status = "PASS" if fail_count == 0 else "FAIL"
    case_str = "\n\n".join(case_list)

    if critical_issues:
        issue_highlight = "[ CRITICAL ISSUES FOUND ]\n"
        for issue in critical_issues:
            issue_highlight += f"!! {issue}\n"
    else:
        issue_highlight = "None"

    if failed_diagnostic_summaries:
        diagnostic_summary_text = "\n\n".join(failed_diagnostic_summaries)
        diagnostic_summary_block = f"""
[ Diagnostic Summary for Failed Case ]
{diagnostic_summary_text}
"""
    else:
        diagnostic_summary_text = ""
        diagnostic_summary_block = ""

    diag_highlight = "\n".join(f"  - {os.path.basename(f)}" for f in diagnostic_files) if diagnostic_files else "None"
    tsm4_gui_highlight = "\n".join(f"  - {os.path.basename(f)}" for f in tsm4_gui_files) if tsm4_gui_files else "None"
    screenshot_highlight = "\n".join(f"  - {os.path.basename(f)}" for f in screenshot_files) if screenshot_files else "None"
    sftp_uploaded_text = "\n".join(f"  - {p}" for p in uploaded_paths) if uploaded_paths else "None"

    subject = f"[{status}] TSB4 Automation Test Report - {fw_version} - {now_str}"
    crash_highlight = build_crash_email_highlight(crash_report)

    body = f"""Firmware: {fw_version}
Test Status: {status}

[ DUT Crash / Kernel Panic ]
{crash_highlight}

[ Critical Issue Highlight ]
{issue_highlight}

{case_str}

Please download the ZIP report from the following SFTP path:
{sftp_uploaded_text}

[ Diagnostic Files Included in ZIP ]
{diag_highlight}

[ TSM4 GUI Logs Included in ZIP ]
{tsm4_gui_highlight}
"""

    email_attachments = [] if sftp_ok else [all_summary_name]
    email_ok, email_error = send_email(subject, body, email_attachments)

    log_step("Final collect: cleanup original Summary/Console/diagnostic files")
    # 只清理工作目錄下的原始個別檔案；保留 target_folder 內的 all_summary 與 zip。
    for f in summary_files + console_files + diagnostic_files + tsm4_gui_files + screenshot_files:
        try:
            os.remove(f)
        except Exception:
            pass

    log_result(f"Final collect: cleanup completed, local_folder={target_folder}")
    log_progress(f"SFTP 目標資料夾: {remote_dir}")

    if sftp_ok and email_ok:
        log_result(f"Final collect PASS: status={status}, zip={zip_name}")
        return 0

    fail_reasons = []
    if not sftp_ok:
        fail_reasons.append(f"SFTP={sftp_error}")
    if not email_ok:
        fail_reasons.append(f"EMAIL={email_error}")
    log_result(f"Final collect FAIL: {'; '.join(fail_reasons)}")
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
