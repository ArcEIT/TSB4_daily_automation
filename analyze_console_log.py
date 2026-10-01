#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline console log analyzer (same rules as the in-process Fail_Reason hook).

Usage:
  python analyze_console_log.py console_log\\fail_cases
  python analyze_console_log.py "20261001_1128_case3_RE Warm Reboot Onboarding_Console.log"
  python analyze_console_log.py console_log\\fail_cases --stage eth     (control: ETH BH stage should report nothing)
"""
import argparse
import glob
import os
import sys

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from testlib import console_analyzer


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Analyze case Console.log files for known failure signatures.")
    parser.add_argument("path", help="A *_Console.log file, or a folder containing them")
    parser.add_argument(
        "--stage", choices=["fail", "eth"], default="fail",
        help="fail = the stage that failed (default); eth = first ETH BH stage (control sample)",
    )
    args = parser.parse_args()

    if os.path.isdir(args.path):
        files = sorted(glob.glob(os.path.join(args.path, "**", "*Console.log"), recursive=True))
    else:
        files = [args.path]
    if not files:
        print(f"No *Console.log found in: {args.path}")
        return 1

    for path in files:
        findings = console_analyzer.analyze(path, args.stage)
        print("=" * 78)
        print(os.path.basename(path))
        if not findings:
            print("  (no finding)")
            continue
        print(console_analyzer.format_detail_block(findings), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
