"""DUT health utilities: uptime check and onboarding-ready guard.

Provides lightweight pre-flight and in-wait DUT health checks so that
test cases can detect unexpected reboots early rather than failing
silently after a long wait.

Key functions
-------------
get_dut_uptime_seconds()
    Read /proc/uptime via serial. Returns int seconds or None.

wait_for_onboarding_if_recently_rebooted()
    Call at the START of each case. If DUT uptime < threshold (default 300 s),
    polls OnboardingDone=1 before proceeding. Overhead is < 1 s when DUT is
    already stable.

monitored_wait(wait_seconds, label)
    Drop-in replacement for receive_monitor() in long blind waits.
    Splits the wait into check_interval chunks and polls uptime after each
    chunk to detect unexpected reboots early.

check_uptime_reset_during_wait(baseline_uptime, label)
    Low-level helper: returns True when current uptime < baseline - 30 s,
    indicating a reboot since baseline was recorded.
"""
import time
from typing import Optional

from .serial_console import get_serial_for_command, receive_monitor, _SERIAL_IO_LOCK
from .logger import log_progress, log_step, log_result

_UPTIME_CMD = "awk '{print int($1)}' /proc/uptime"
_ONBOARDING_CMD = "uci get repacd.MAPConfig.OnboardingDone"

# Default thresholds (all in seconds).
_DEFAULT_REBOOT_THRESHOLD = 300   # uptime < this → "just rebooted"
_DEFAULT_POLL_TIMEOUT = 300       # max time to wait for OnboardingDone=1
_DEFAULT_POLL_INTERVAL = 15       # interval between OnboardingDone polls
_DEFAULT_CHECK_INTERVAL = 30      # interval between uptime checks in monitored_wait


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _serial_one_line(command: str, read_time: float = 2.0) -> Optional[str]:
    """Run a single command on the DUT serial console.

    Returns the last meaningful non-empty output line, or None on failure.
    """
    try:
        ser, close_after = get_serial_for_command()
        if ser is None:
            return None
        try:
            with _SERIAL_IO_LOCK:
                ser.write(b"\r\n")
                receive_monitor(0.3, ser)
                try:
                    ser.reset_input_buffer()
                except Exception:
                    pass
                ser.write((command.strip() + "\n").encode("utf-8"))
                output = receive_monitor(read_time, ser)
        finally:
            if close_after:
                try:
                    ser.close()
                except Exception:
                    pass

        cmd_stripped = command.strip()
        lines = []
        for raw in output.replace("\r", "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if cmd_stripped in line:
                continue
            if line.startswith("root@") and "uci:" not in line:
                continue
            lines.append(line)
        return lines[-1] if lines else ""
    except Exception as exc:
        log_progress(f"[DUT_HEALTH] serial cmd failed: {type(exc).__name__}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_dut_uptime_seconds(log_prefix: str = "") -> Optional[int]:
    """Return DUT uptime in integer seconds via serial, or None on failure."""
    val = _serial_one_line(_UPTIME_CMD)
    if val is not None and str(val).strip().isdigit():
        uptime = int(val)
        if log_prefix:
            log_progress(f"{log_prefix} DUT uptime={uptime}s")
        return uptime
    if log_prefix:
        log_progress(f"{log_prefix} DUT uptime read failed (raw={val!r})")
    return None


def check_uptime_reset_during_wait(baseline_uptime: int, label: str = "") -> bool:
    """Return True when DUT appears to have rebooted since baseline was recorded.

    A reboot is inferred when current uptime < baseline_uptime - 30 s.
    The 30-second tolerance absorbs measurement jitter without hiding real reboots
    (after a reboot the uptime resets to near 0).
    """
    current = get_dut_uptime_seconds()
    if current is not None and current < baseline_uptime - 30:
        tag = f" in '{label}'" if label else ""
        log_result(
            f"[DUT_HEALTH] Unexpected DUT reboot detected{tag}: "
            f"uptime dropped {baseline_uptime}s -> {current}s"
        )
        return True
    return False


def wait_for_onboarding_if_recently_rebooted(
    uptime_threshold: int = _DEFAULT_REBOOT_THRESHOLD,
    poll_timeout: int = _DEFAULT_POLL_TIMEOUT,
    poll_interval: int = _DEFAULT_POLL_INTERVAL,
    log_prefix: str = "",
) -> bool:
    """Pre-flight guard: wait for OnboardingDone=1 only when DUT just rebooted.

    Normal path (DUT stable, uptime >= uptime_threshold):
        Returns True immediately. Overhead is one serial command (< 1 s).

    Recent-reboot path (uptime < uptime_threshold):
        Polls uci get repacd.MAPConfig.OnboardingDone every poll_interval
        seconds up to poll_timeout seconds. Returns True when OnboardingDone=1,
        False on timeout.

    Args:
        uptime_threshold: Treat DUT as "recently rebooted" if uptime is below this.
        poll_timeout:     Maximum seconds to wait for OnboardingDone=1.
        poll_interval:    Seconds between polls.
        log_prefix:       Tag prepended to all log lines (e.g. "[CASE13]").

    Returns:
        True  — DUT is ready to test.
        False — poll_timeout exceeded; OnboardingDone never became 1.
    """
    prefix = log_prefix or "[DUT_HEALTH]"
    uptime = get_dut_uptime_seconds(log_prefix=prefix)

    if uptime is None:
        log_progress(f"{prefix} uptime unreadable — assuming DUT stable, skipping onboarding wait")
        return True

    if uptime >= uptime_threshold:
        log_progress(f"{prefix} DUT uptime={uptime}s >= {uptime_threshold}s — DUT ready, no wait needed")
        return True

    log_step(
        f"{prefix} DUT uptime={uptime}s < {uptime_threshold}s — "
        f"waiting for OnboardingDone=1 (timeout={poll_timeout}s, interval={poll_interval}s)"
    )
    log_progress(
        f"{prefix} DUT recently rebooted (uptime={uptime}s). "
        f"Polling repacd.MAPConfig.OnboardingDone before proceeding..."
    )

    start = time.time()
    while time.time() - start < poll_timeout:
        val = _serial_one_line(_ONBOARDING_CMD)
        if val is not None and val.strip() == "1":
            elapsed = round(time.time() - start, 1)
            log_result(f"{prefix} OnboardingDone=1 after {elapsed}s — DUT ready")
            return True
        elapsed = round(time.time() - start, 1)
        log_progress(
            f"{prefix} OnboardingDone={val!r} [{elapsed:.0f}s/{poll_timeout}s], "
            f"retry in {poll_interval}s"
        )
        receive_monitor(poll_interval)

    log_result(f"{prefix} OnboardingDone poll TIMEOUT after {poll_timeout}s — DUT not ready")
    return False


def monitored_wait(
    wait_seconds: int,
    label: str,
    check_interval: int = _DEFAULT_CHECK_INTERVAL,
) -> bool:
    """Drop-in replacement for receive_monitor() in long blind waits.

    Splits wait_seconds into check_interval-sized chunks and polls uptime
    after each chunk to detect unexpected DUT reboots early.

    Usage — replace::
        receive_monitor(init_wait)
    with::
        if not monitored_wait(init_wait, "WiFi BH init"):
            return False, "Unexpected_Reboot_Stage", "DUT_Unexpected_Reboot_During_Wait"

    Args:
        wait_seconds:   Total wait time in seconds (same as receive_monitor arg).
        label:          Human-readable label for log messages.
        check_interval: How often (in seconds) to check DUT uptime during the wait.

    Returns:
        True  — wait completed, no reboot detected.
        False — reboot detected mid-wait; caller should FAIL immediately.
    """
    if wait_seconds <= 0:
        return True

    log_progress(
        f"[DUT_HEALTH] monitored_wait start: '{label}' total={wait_seconds}s "
        f"check_interval={check_interval}s"
    )

    baseline = get_dut_uptime_seconds(log_prefix=f"[DUT_HEALTH][{label}]")
    remaining = int(wait_seconds)

    while remaining > 0:
        chunk = min(check_interval, remaining)
        receive_monitor(chunk)
        remaining -= chunk
        if baseline is not None and check_uptime_reset_during_wait(baseline, label):
            return False

    log_progress(f"[DUT_HEALTH] monitored_wait done: '{label}' — no reboot detected")
    return True
