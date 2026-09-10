"""Serial console helpers with optional full-session background logging."""
import datetime
import sys
import threading
import time

import serial

from . import config as cfg
from .logger import log_progress

_SERIAL_IO_LOCK = threading.RLock()
_SHARED_SERIAL = None
_BACKGROUND_SERIAL_LOGGER = None


def _write_serial_raw_to_log(raw_data):
    if not raw_data:
        return
    ts_raw = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")[:-3]
    with open(cfg.FULL_CONSOLE_LOG, "a", encoding="utf-8") as f:
        for line in raw_data.splitlines():
            if line.strip():
                formatted_log = f"{ts_raw} [SERIAL] {line.strip()}\n"
                f.write(formatted_log)
                sys.stdout.write(formatted_log)
    sys.stdout.flush()


def _open_serial():
    return serial.Serial(cfg.BOOSTER_PORT, cfg.BAUD_RATE, timeout=0.1)


def _get_shared_serial():
    global _SHARED_SERIAL
    if _SHARED_SERIAL is None or not getattr(_SHARED_SERIAL, "is_open", False):
        _SHARED_SERIAL = _open_serial()
    return _SHARED_SERIAL


def is_background_serial_logger_running():
    return _BACKGROUND_SERIAL_LOGGER is not None


class _BackgroundSerialLogger:
    """Continuously save COM console output during the whole case run.

    Serial command helpers and this background logger share the same serial object
    and lock. When a helper needs command output, it holds the lock; the background
    logger resumes immediately afterwards.
    """

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="full_session_serial_logger", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._thread.join(timeout=2)

    def _run(self):
        while not self._stop_event.is_set():
            try:
                with _SERIAL_IO_LOCK:
                    ser = _get_shared_serial()
                    if ser.in_waiting > 0:
                        raw_data = ser.read(ser.in_waiting).decode("utf-8", errors="ignore")
                        _write_serial_raw_to_log(raw_data)
            except Exception as e:
                log_progress(f"[SERIAL_LOG] background serial logger error: {type(e).__name__}: {e}")
                return
            time.sleep(0.1)


def start_background_serial_logger():
    """Start full-session COM console logging if enabled."""
    global _BACKGROUND_SERIAL_LOGGER
    if not getattr(cfg, "FULL_SESSION_SERIAL_LOG_ENABLE", True):
        return False
    if _BACKGROUND_SERIAL_LOGGER is not None:
        return True
    try:
        # Open once here so port errors appear near case start.
        with _SERIAL_IO_LOCK:
            _get_shared_serial()
        _BACKGROUND_SERIAL_LOGGER = _BackgroundSerialLogger()
        _BACKGROUND_SERIAL_LOGGER.start()
        log_progress(f"[SERIAL_LOG] 全流程同步保存 {cfg.BOOSTER_PORT} console log 已啟用。")
        return True
    except Exception as e:
        log_progress(f"[SERIAL_LOG] 無法啟用全流程 serial logger: {type(e).__name__}: {e}")
        _BACKGROUND_SERIAL_LOGGER = None
        return False


def stop_background_serial_logger(close_serial=True):
    """Stop full-session COM console logging and close shared serial."""
    global _BACKGROUND_SERIAL_LOGGER, _SHARED_SERIAL
    if _BACKGROUND_SERIAL_LOGGER is not None:
        try:
            _BACKGROUND_SERIAL_LOGGER.stop()
        finally:
            _BACKGROUND_SERIAL_LOGGER = None
    if close_serial and _SHARED_SERIAL is not None:
        try:
            if _SHARED_SERIAL.is_open:
                _SHARED_SERIAL.close()
        except Exception:
            pass
        finally:
            _SHARED_SERIAL = None



def get_serial_for_command():
    """Return (ser, close_after_use) for helpers that need direct serial access."""
    if is_background_serial_logger_running():
        return _get_shared_serial(), False
    return _open_serial(), True


def receive_monitor(wait_seconds, ser=None):
    start_time = time.time()
    collected_output = ""
    close_after_use = False

    try:
        if ser is None:
            if is_background_serial_logger_running():
                ser = _get_shared_serial()
            else:
                ser = _open_serial()
                close_after_use = True

        while (time.time() - start_time) < float(wait_seconds):
            with _SERIAL_IO_LOCK:
                if ser.in_waiting > 0:
                    raw_data = ser.read(ser.in_waiting).decode("utf-8", errors="ignore")
                    collected_output += raw_data
                    _write_serial_raw_to_log(raw_data)
            time.sleep(0.1)

    except Exception as e:
        log_progress(f"Serial Monitor Error: {e}")

    finally:
        if close_after_use and ser:
            try:
                ser.close()
            except Exception:
                pass

    return collected_output


def send_command(command, wait_after=0):
    ser = None
    close_after_use = False
    try:
        if is_background_serial_logger_running():
            ser = _get_shared_serial()
        else:
            ser = _open_serial()
            close_after_use = True

        with _SERIAL_IO_LOCK:
            ser.write(b"\r\n")
        receive_monitor(1, ser)
        if isinstance(command, str):
            command = command.encode("utf-8")
        with _SERIAL_IO_LOCK:
            ser.write(command)
        if wait_after > 0:
            receive_monitor(wait_after, ser)
        return True
    except Exception as e:
        log_progress(f"Serial command failed: {e}")
        return False
    finally:
        if close_after_use and ser is not None:
            try:
                ser.close()
            except Exception:
                pass


def send_command_with_timestamp(command, wait_after=0):
    ser = None
    close_after_use = False
    try:
        if is_background_serial_logger_running():
            ser = _get_shared_serial()
        else:
            ser = _open_serial()
            close_after_use = True

        with _SERIAL_IO_LOCK:
            ser.write(b"\r\n")
        receive_monitor(1, ser)
        if isinstance(command, str):
            command = command.encode("utf-8")
        with _SERIAL_IO_LOCK:
            ser.write(command)
            send_time = time.time()
        if wait_after > 0:
            receive_monitor(wait_after, ser)
        return True, send_time
    except Exception as e:
        log_progress(f"Serial command failed: {e}")
        return False, None
    finally:
        if close_after_use and ser is not None:
            try:
                ser.close()
            except Exception:
                pass
