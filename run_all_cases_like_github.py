#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run TSB4 automation cases sequentially, similar to GitHub Actions daily.yml.

Key behavior in this version:
  - Run all enabled cases in order.
  - If a case returns non-zero, run a GW+Booster reboot workaround.
  - Wait FAIL_WORKAROUND_WAIT seconds after workaround.
  - Continue to the next case by default.

Usage examples:
  python run_all_cases_like_github.py
  python run_all_cases_like_github.py --dry-run
  python run_all_cases_like_github.py --stop-on-fail
  python run_all_cases_like_github.py --start-from case5
  python run_all_cases_like_github.py --only case7
  python run_all_cases_like_github.py --only case1,case2,case11,final_factory
  python run_all_cases_like_github.py --only case2 --loops 5
  python run_all_cases_like_github.py --only case1,case2 --loops 3
  python run_all_cases_like_github.py --loops 10   (override DEFAULT_LOOPS for ALL cases)

Note: --loops overrides the value after --loops in each step's command.
Steps whose command does not contain --loops (collect, final_factory, etc.)
are left unchanged.

Edit build_steps() to insert/remove sleep or commands.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# User editable defaults
# ---------------------------------------------------------------------------

# Set True if you want firmware upgrade before testing.
ENABLE_FW_UPGRADE = True
#ENABLE_FW_UPGRADE = False

# Default: continue all remaining cases even if one case fails.
# When a case fails, the runner will run FAIL_WORKAROUND_COMMAND first.
CONTINUE_ON_FAIL_DEFAULT = True

# Default loop count passed to Case1~Case13 modular scripts.
DEFAULT_LOOPS = 1

# Conditional workaround after factory-default case failure only.
# Policy:
#   - Only factory-default test case failure should trigger GW+Booster reboot workaround.
#   - Other case failures, script/config/import errors, collect failures, or final_factory failures
#     do NOT trigger this workaround.
FAIL_WORKAROUND_ENABLE = True
FAIL_WORKAROUND_COMMAND = [sys.executable, "tsm4_gui_reboot_booster_fallback.py"]
FAIL_WORKAROUND_WAIT = 300

# Step keys that trigger GW+Booster reboot workaround on failure.
# case1 = RE factory default onboarding
# case7 = Reset Router + Boosters via TSM4 GUI
# case9 = Reset Boosters via TSM4 GUI
# All three involve factory-default/reset flows that may leave the system in a
# broken state requiring a full reboot to recover.
FAIL_WORKAROUND_CASE_KEYS = {"case1", "case7", "case9"}


@dataclass
class Step:
    key: str
    name: str
    command: Optional[list[str]] = None
    sleep_s: int = 0
    enabled: bool = True


def py(script: str, *args: str) -> list[str]:
    """Build a Python command using the current interpreter."""
    return [sys.executable, script, *args]


def build_steps() -> list[Step]:
    """Edit this list to adjust order, command, or sleep time."""
    loops_arg = ["--loops", str(DEFAULT_LOOPS)]

    selected_cases = [
        Step("case1", "Case 1 Factory Default Onboarding", py("cases/case1_re_factory_default_modular.py", *loops_arg), 300),
        #Step("case1", "Case 1 Factory Default Onboarding", py("cases/case1_re_factory_default_modular.py", "--loops", "5"), 300),
        Step("case2", "Case 2 Standard Onboarding", py("cases/case2_eth_wifi_onboarding_modular.py", *loops_arg), 300),
        Step("case3", "Case 3 Warm Reboot Onboarding", py("cases/case3_re_warm_reboot_modular.py", *loops_arg), 300),
        Step("case4", "Case 4 Cold Reboot Onboarding", py("cases/case4_re_cold_reboot_modular.py", *loops_arg), 300),
        Step("case5", "Case 5 TSM4 GUI Reboot", py("cases/case5_tsm4_restart_modular.py", *loops_arg), 300),
        Step("case6", "Case 6 Reboot Router + Boosters via TSM4 GUI", py("cases/case6_reboot_gw_re_modular.py", *loops_arg), 300),
        Step("case7", "Case 7 Reset Router + Boosters via TSM4 GUI", py("cases/case7_reset_router_boosters_modular.py", *loops_arg), 500),
        Step("case8", "Case 8 Reboot Boosters via TSM4 GUI", py("cases/case8_reboot_re_modular.py", *loops_arg), 300),
        Step("case9", "Case 9 Reset Boosters via TSM4 GUI", py("cases/case9_reset_re_modular.py", *loops_arg), 500),
        Step("case10", "Case 10 Main WiFi Random SSID/Key Sync", py("cases/case10_main_wifi_modify_ssid_key_sync_check_modular.py", *loops_arg), 300),
        Step("case11", "Case 11 Guest WiFi Random SSID/Key Sync", py("cases/case11_guest_wifi_modify_ssid_key_sync_check_modular.py", *loops_arg), 300),
        Step("case12", "Case 12 TSM4 Wireless FH Disable/Enable Check", py("cases/case12_tsm4_wireless_fh_disable_enable_sync_check_modular.py", *loops_arg), 300),
        Step("case13", "Case 13 BH Random SSID Lost Connect Check", py("cases/case13_bh_random_ssid_lost_connect_check_modular.py", *loops_arg), 300),
        Step("case14", "Case 14 TSM4 WPS + RE WPS Onboarding", py("cases/case14_tsm4_wps_button_re_wps_onboarding_modular.py", *loops_arg), 300),
    ]

    return [
        Step("initial_wait", "Initial wait", sleep_s=10),
        Step("fw_upgrade", "Firmware upgrade", py("Download_fw_then_upgrade.py"), 300, enabled=ENABLE_FW_UPGRADE),
        *selected_cases,
        Step("crash_summary", "Scan Console logs for Kernel panic / crash (standalone)", py("post_run_crash_summary.py"), 60),
        Step("collect", "Collect all log / diag then email (main)", py("TSB4_collect_zip_upload_sftp_then_email_v8_clean_sftp_email.py"), 60),
        Step("collect_bill", "Collect all log / diag then email (bill)", py("TSB4_collect_zip_upload_sftp_then_email_v8_clean_sftp_email_bill.py"), 60),
        Step("final_factory", "Final TSM4 GUI Factory Default", py("tsm4_gui_factory_default_standalone.py"), 60),
    ]


# ---------------------------------------------------------------------------
# Runner implementation
# ---------------------------------------------------------------------------

def ts() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def print_banner(text: str) -> None:
    line = "=" * 90
    print(f"\n{line}\n[{ts()}] {text}\n{line}", flush=True)


def cmd_to_text(command: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(x)) for x in command)


def sleep_with_countdown(seconds: int, dry_run: bool) -> None:
    if seconds <= 0:
        return
    print(f"[{ts()}] Sleep {seconds} seconds", flush=True)
    if dry_run:
        return
    remain = int(seconds)
    while remain > 0:
        step = min(30, remain)
        time.sleep(step)
        remain -= step
        if remain > 0:
            print(f"[{ts()}] Sleep remaining {remain} seconds", flush=True)


def run_command(command: list[str], dry_run: bool) -> int:
    print(f"[{ts()}] CMD: {cmd_to_text(command)}", flush=True)
    if dry_run:
        print(f"[{ts()}] EXIT_CODE: 0 (dry-run)", flush=True)
        return 0
    completed = subprocess.run(command, cwd=os.getcwd())
    print(f"[{ts()}] EXIT_CODE: {completed.returncode}", flush=True)
    return completed.returncode


def run_step(step: Step, dry_run: bool) -> int:
    print_banner(f"START: {step.key} - {step.name}")

    rc = 0
    if step.command:
        rc = run_command(step.command, dry_run)
    else:
        print(f"[{ts()}] No command for this step", flush=True)

    if rc == 0 and step.sleep_s > 0:
        sleep_with_countdown(step.sleep_s, dry_run)
    elif rc != 0:
        print(f"[{ts()}] Skip normal post-step sleep because command failed", flush=True)

    print_banner(f"END: {step.key} - rc={rc}")
    return rc


def should_run_fail_workaround(step: Step) -> bool:
    """Return True only for factory-default case failures.

    This intentionally does NOT use prefix matching. A Python/import/config error
    in case2/case10/etc. should not reboot GW+Booster.
    """
    if not FAIL_WORKAROUND_ENABLE:
        return False
    return step.key.lower() in {key.lower() for key in FAIL_WORKAROUND_CASE_KEYS}


def run_fail_workaround(failed_step: Step, dry_run: bool) -> int:
    print_banner(f"FAIL WORKAROUND after {failed_step.key}: Reboot GW+Booster")
    rc = run_command(FAIL_WORKAROUND_COMMAND, dry_run)
    if rc == 0:
        sleep_with_countdown(FAIL_WORKAROUND_WAIT, dry_run)
    else:
        print(f"[{ts()}] Fail workaround returned rc={rc}; continue policy still handled by runner", flush=True)
    print_banner(f"END FAIL WORKAROUND after {failed_step.key} - rc={rc}")
    return rc


def override_loops_in_steps(steps: list[Step], loops: int) -> None:
    """Replace the value after --loops in each step's command with *loops*.

    Only steps whose command already contains --loops are affected.
    Steps without --loops (legacy scripts, collect, final_factory …) are left
    untouched so we don't accidentally inject an unknown argument.
    Modifies step.command in-place.
    """
    for step in steps:
        if not step.command:
            continue
        cmd = step.command
        try:
            idx = cmd.index("--loops")
            if idx + 1 < len(cmd):
                cmd[idx + 1] = str(loops)
        except ValueError:
            pass  # no --loops in this command, skip


def filter_steps(steps: list[Step], start_from: Optional[str], only: Optional[str]) -> list[Step]:
    steps = [s for s in steps if s.enabled]

    if only:
        wanted = {x.strip().lower() for x in only.split(",") if x.strip()}
        return [s for s in steps if s.key.lower() in wanted]

    if start_from:
        key = start_from.lower()
        for i, step in enumerate(steps):
            if step.key.lower() == key:
                return steps[i:]
        raise SystemExit(f"Unknown --start-from key: {start_from}")

    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TSB4 daily automation steps like GitHub Actions.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands and sleeps without executing.")
    fail_group = parser.add_mutually_exclusive_group()
    fail_group.add_argument("--continue-on-fail", action="store_true", help="Continue next step if one command fails.")
    fail_group.add_argument("--stop-on-fail", action="store_true", help="Stop immediately if one command fails.")
    parser.add_argument("--start-from", help="Start from a step key, e.g. case5, case10, collect.")
    parser.add_argument("--only", help="Run only selected step keys, comma separated, e.g. case7 or case1,case2.")
    parser.add_argument(
        "--loops", type=int, default=None,
        help=(
            "Override the --loops value passed to every case script that accepts it. "
            "e.g. --only case2 --loops 5  runs case2 with 5 loops. "
            "Steps whose command does not contain --loops are unaffected. "
            "If omitted, each step uses its own configured loop count."
        ),
    )
    args = parser.parse_args()

    continue_on_fail = CONTINUE_ON_FAIL_DEFAULT
    if args.continue_on_fail:
        continue_on_fail = True
    if args.stop_on_fail:
        continue_on_fail = False

    steps = filter_steps(build_steps(), args.start_from, args.only)

    if args.loops is not None:
        override_loops_in_steps(steps, args.loops)
        print(f"[{ts()}] --loops override active: all case scripts will use --loops {args.loops}", flush=True)

    print_banner("TSB4 Python Daily Runner Start")
    print(f"[{ts()}] CONTINUE_ON_FAIL = {continue_on_fail}", flush=True)
    print(f"[{ts()}] DRY_RUN = {args.dry_run}", flush=True)
    print(f"[{ts()}] FAIL_WORKAROUND_ENABLE = {FAIL_WORKAROUND_ENABLE}", flush=True)
    print(f"[{ts()}] FAIL_WORKAROUND_WAIT = {FAIL_WORKAROUND_WAIT}", flush=True)
    loops_display = args.loops if args.loops is not None else f"{DEFAULT_LOOPS} (default, per-step)"
    print(f"[{ts()}] LOOPS = {loops_display}", flush=True)
    print(f"[{ts()}] Steps = {', '.join(s.key for s in steps)}", flush=True)

    failed: list[tuple[str, int]] = []
    workaround_failed: list[tuple[str, int]] = []

    for step in steps:
        rc = run_step(step, args.dry_run)
        if rc != 0:
            failed.append((step.key, rc))

            if should_run_fail_workaround(step):
                wr_rc = run_fail_workaround(step, args.dry_run)
                if wr_rc != 0:
                    workaround_failed.append((step.key, wr_rc))
            else:
                print(
                    f"[{ts()}] Skip fail workaround after {step.key}: not a factory-default/reset case",
                    flush=True,
                )

            if not continue_on_fail:
                print(f"[{ts()}] Stop on fail: {step.key} rc={rc}", flush=True)
                break

            print(f"[{ts()}] Continue next step after fail: {step.key} rc={rc}", flush=True)

    print_banner("TSB4 Python Daily Runner Summary")
    if failed:
        for key, rc in failed:
            print(f"[FAIL] {key}: rc={rc}", flush=True)
        if workaround_failed:
            for key, rc in workaround_failed:
                print(f"[FAIL_WORKAROUND_FAIL] after {key}: rc={rc}", flush=True)
        return 1

    print("[PASS] All executed steps completed with rc=0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
