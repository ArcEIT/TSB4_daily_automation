"""Helpers for Web GUI actions that can fail sporadically (Case10 / Case11).

- describe_gui_exception(): one readable line (no Selenium stacktrace, no '|') saying which step failed.
- run_with_retry(): run a GUI action up to N times, waiting between attempts.
"""
from .logger import log_progress, log_result
from .serial_console import receive_monitor

MAX_REASON_LEN = 200


def _first_message_line(exc):
    """First useful line of an exception message: skips 'Message:' and the chromedriver stacktrace."""
    for raw in str(exc).replace("\r", "").split("\n"):
        line = raw.strip()
        if line.lower().startswith("message:"):
            line = line[len("message:"):].strip()
        if not line or line.startswith("Stacktrace") or "chromedriver!" in line or line.startswith("(Session info"):
            continue
        return line[:120]
    return ""


def describe_gui_exception(exc, step, wait_timeout=None):
    """One-line Fail_Reason such as
    "Web GUI FAIL at step 'navigate to WiFi Settings': TimeoutException, element not found within 30s".
    The Selenium stacktrace is left out (the email keeps only the first line and it told RD nothing)."""
    name = type(exc).__name__
    msg = _first_message_line(exc)
    if name == "TimeoutException":
        waited = f" within {wait_timeout}s" if wait_timeout else ""
        text = f"Web GUI FAIL at step '{step}': {name}, element not found{waited}"
        if msg:
            text += f" ({msg})"
    else:
        text = f"Web GUI FAIL at step '{step}': {name}" + (f": {msg}" if msg else "")
    text = text.replace("|", "/").replace("\n", " ")
    return text if len(text) <= MAX_REASON_LEN else text[: MAX_REASON_LEN - 3] + "..."


def run_with_retry(action, max_attempts, retry_wait, label):
    """Call action() -> (ok, reason) up to max_attempts times, waiting retry_wait seconds between attempts.

    A sporadic GUI failure (page not rendered, session dropped) should not fail a whole case. The actions used here are
    idempotent (setting the same SSID/key again has no side effect). Returns (ok, reason)."""
    max_attempts = max(int(max_attempts), 1)
    reason = "GUI action did not run"
    for attempt in range(1, max_attempts + 1):
        ok, reason = action()
        if ok:
            if attempt > 1:
                log_result(f"{label}: GUI action PASS on attempt {attempt}/{max_attempts}")
            return True, reason
        log_progress(f"[GUI_RETRY] {label}: attempt {attempt}/{max_attempts} failed: {reason}")
        if attempt < max_attempts:
            log_progress(f"[GUI_RETRY] {label}: wait {retry_wait}s, then retry ({attempt + 1}/{max_attempts})")
            receive_monitor(retry_wait)
    if max_attempts > 1:
        reason = f"{reason} (GUI attempt {max_attempts}/{max_attempts})"
    return False, reason
