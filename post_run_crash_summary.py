#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_run_crash_summary.py

Post-run crash / reboot summary scanner.

Usage:
    python post_run_crash_summary.py
    python post_run_crash_summary.py --log-dir E:\\script\\console_log
    python post_run_crash_summary.py --log-dir E:\\script\\console_log --out my_crash.log

Scans every *_Console.log in log-dir for DUT crash / reboot keywords and
writes a human-readable all_crash_summary.log showing:
  - Which case had a crash
  - Timestamp + line number of each hit
  - Total crash count per case
  - Final PASS/FAIL-with-crash table at the bottom
"""

import argparse
import glob
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Crash keywords to search for (case-insensitive)
# Add or remove keywords here as needed.
# ---------------------------------------------------------------------------
CRASH_KEYWORDS = [
    # ── Primary (always reported) ──────────────────────────────────────────
    "Kernel panic",           # Kernel panic - not syncing: ...
    "Fatal exception",        # Fatal exception / Fatal exception in interrupt

    # ── Secondary (also captured for context) ─────────────────────────────
    "Unable to handle kernel",   # precedes most Kernel panic lines
    "Rebooting in",              # "Rebooting in 3 seconds.."
    "CRASHED",                   # cnss_pci CRASHED
    "Crash shutdown",            # cnss Crash shutdown device
    r"BUG: (?!.*adfs)",          # kernel BUG at ... (exclude adfs debug lines)
    "Oops:",                     # Internal error: Oops:
    "platform_mlo.c.*Assertion.*failed",  # MLO assertion (regex)
]

# Primary keywords — these alone determine the CRASH status in the summary table.
# Secondary hits are shown in the detail section but do NOT count as a new crash event.
PRIMARY_CRASH_KEYWORDS = {"Kernel panic", "Fatal exception"}

# Keywords that indicate the script itself detected/handled a reboot
# (lower priority — shown separately so you can distinguish FW crash vs script detection)
SCRIPT_REBOOT_KEYWORDS = [
    "DUT_Unexpected_Reboot",
    "Unexpected DUT reboot detected",
    "uptime dropped",
]

# ---------------------------------------------------------------------------

def _matches_any(line: str, keywords: list) -> str:
    """Return the first matching keyword (string or regex) or empty string."""
    import re
    low = line.lower()
    for kw in keywords:
        try:
            if re.search(kw, line, re.IGNORECASE):
                return kw
        except re.error:
            if kw.lower() in low:
                return kw
    return ""


def scan_log(log_path: str):
    """Scan a single log file. Returns (crash_hits, script_hits).

    Each hit is a dict: {lineno, timestamp, keyword, line_preview}
    """
    crash_hits = []
    script_hits = []
    ts_re = __import__("re").compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for lineno, raw in enumerate(f, 1):
                line = raw.rstrip()
                m = ts_re.search(line)
                ts = m.group(1) if m else ""

                kw = _matches_any(line, CRASH_KEYWORDS)
                if kw:
                    crash_hits.append({
                        "lineno": lineno,
                        "ts": ts,
                        "keyword": kw,
                        "primary": kw in PRIMARY_CRASH_KEYWORDS,
                        "preview": line.strip()[:160],
                    })
                    continue  # don't double-count

                kw2 = _matches_any(line, SCRIPT_REBOOT_KEYWORDS)
                if kw2:
                    script_hits.append({
                        "lineno": lineno,
                        "ts": ts,
                        "keyword": kw2,
                        "preview": line.strip()[:160],
                    })

    except Exception as exc:
        crash_hits.append({
            "lineno": 0,
            "ts": "",
            "keyword": "READ_ERROR",
            "preview": str(exc),
        })

    return crash_hits, script_hits


def extract_case_name(filename: str) -> str:
    """Extract case label from filename like 20260831_0645_case12_..._Console.log"""
    base = os.path.splitext(os.path.basename(filename))[0]
    # Remove trailing _Console
    if base.endswith("_Console"):
        base = base[: -len("_Console")]
    # Try to pull caseNN_ portion
    import re
    m = re.search(r"(case\d+_.+)", base, re.IGNORECASE)
    return m.group(1) if m else base


def main():
    parser = argparse.ArgumentParser(description="Scan console logs for DUT crash/reboot events.")
    parser.add_argument(
        "--log-dir",
        default=".",
        help="Directory containing *_Console.log files (default: current working directory)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output summary filename (default: all_crash_summary.log inside --log-dir)",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Omit 'clean' (no crash) cases from the detail section",
    )
    args = parser.parse_args()

    log_dir = args.log_dir
    out_path = args.out or os.path.join(log_dir, "all_crash_summary.log")

    # glob handles filenames with spaces correctly on all platforms
    logs = sorted(glob.glob(os.path.join(log_dir, "*Console.log")))

    if not logs:
        print(f"[WARN] No *_Console.log files found in: {log_dir}")
        sys.exit(0)

    print(f"Scanning {len(logs)} log file(s) in: {log_dir}")

    results = []
    for log_path in logs:
        crash_hits, script_hits = scan_log(log_path)
        results.append({
            "path": log_path,
            "case": extract_case_name(log_path),
            "filename": os.path.basename(log_path),
            "crash_hits": crash_hits,
            "script_hits": script_hits,
        })

    # Attach primary crash count to each result
    for r in results:
        r["primary_count"] = sum(1 for h in r["crash_hits"] if h.get("primary"))

    # Sort: primary crash first → any crash → clean
    results.sort(key=lambda r: (0 if r["primary_count"] else (1 if r["crash_hits"] else 2), r["filename"]))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep = "=" * 72

    with open(out_path, "w", encoding="utf-8") as out:
        primary_crash_cases = sum(1 for r in results if r["primary_count"])
        out.write(f"{sep}\n")
        out.write(f"  POST-RUN CRASH SUMMARY\n")
        out.write(f"  Generated      : {now}\n")
        out.write(f"  Log dir        : {log_dir}\n")
        out.write(f"  Files scanned  : {len(logs)}\n")
        out.write(f"  Kernel panic / Fatal exception : {primary_crash_cases} / {len(logs)} cases\n")
        out.write(f"{sep}\n\n")

        # ---- Detail section ----
        for r in results:
            primary_hits = [h for h in r["crash_hits"] if h.get("primary")]
            secondary_hits = [h for h in r["crash_hits"] if not h.get("primary")]
            has_script = bool(r["script_hits"])

            if args.no_clean and not r["crash_hits"] and not has_script:
                continue

            if primary_hits:
                status_tag = f"*** KERNEL PANIC / FATAL EXCEPTION x{len(primary_hits)} ***"
            elif secondary_hits:
                status_tag = f"crash-related ({len(secondary_hits)} secondary hit(s))"
            elif has_script:
                status_tag = "script-detected reboot"
            else:
                status_tag = "clean"

            out.write(f"{sep}\n")
            out.write(f"[{status_tag}]\n")
            out.write(f"  {r['filename']}\n")
            out.write(f"{sep}\n")

            if primary_hits:
                out.write(f"  PRIMARY (Kernel panic / Fatal exception)  — {len(primary_hits)} event(s):\n")
                for h in primary_hits:
                    ts_str = f"  {h['ts']}" if h["ts"] else ""
                    out.write(f"    L{h['lineno']:>6}{ts_str}  [{h['keyword']}]\n")
                    out.write(f"           {h['preview']}\n")
                out.write("\n")

            if secondary_hits:
                out.write(f"  SECONDARY (context / related events)  — {len(secondary_hits)} hit(s):\n")
                for h in secondary_hits:
                    ts_str = f"  {h['ts']}" if h["ts"] else ""
                    out.write(f"    L{h['lineno']:>6}{ts_str}  [{h['keyword']}]\n")
                    out.write(f"           {h['preview']}\n")
                out.write("\n")

            if has_script:
                out.write(f"  SCRIPT-DETECTED REBOOT  — {len(r['script_hits'])} hit(s):\n")
                for h in r["script_hits"]:
                    ts_str = f"  {h['ts']}" if h["ts"] else ""
                    out.write(f"    L{h['lineno']:>6}{ts_str}  [{h['keyword']}]\n")
                    out.write(f"           {h['preview']}\n")
                out.write("\n")

            if not r["crash_hits"] and not has_script:
                out.write("  (no crash or reboot events found)\n\n")

        # ---- Summary table ----
        out.write(f"\n{sep}\n")
        out.write(f"  SUMMARY TABLE\n")
        out.write(f"{sep}\n")
        out.write(f"  {'CASE':<52} {'PANIC':>6}  {'OTHER':>6}  {'SCRIPT':>6}  STATUS\n")
        out.write(f"  {'-'*52} {'-'*6}  {'-'*6}  {'-'*6}  {'-'*20}\n")
        for r in results:
            np_ = r["primary_count"]
            no_ = len(r["crash_hits"]) - np_
            ns = len(r["script_hits"])
            if np_:
                status = "KERNEL PANIC / FATAL EX"
            elif no_:
                status = "crash-related"
            elif ns:
                status = "reboot(script)"
            else:
                status = "clean"
            out.write(f"  {r['case']:<52} {np_:>6}  {no_:>6}  {ns:>6}  {status}\n")
        out.write(f"{sep}\n")

    print(f"Done. Summary written to:\n  {out_path}")

    # Also print the table to console
    panic_cases = [r for r in results if r["primary_count"]]
    if panic_cases:
        print(f"\n  *** {len(panic_cases)} case(s) had Kernel panic / Fatal exception: ***")
        for r in panic_cases:
            print(f"    [panic x{r['primary_count']}] {r['filename']}")
    else:
        print("\n  No Kernel panic / Fatal exception detected in any log.")


if __name__ == "__main__":
    main()
