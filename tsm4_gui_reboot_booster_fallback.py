#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tsm4_gui_reboot_booster_fallback.py

Relay-only reboot for RE + TSM4. No SSH/serial dependency.

Flow:
    1. RE power-cycle    (relay 1: off → wait → on)
    2. Wait between_wait seconds
    3. TSM4 power-cycle  (relay 2: off → wait → on)

Dependency:
    pip install pyserial
"""

from __future__ import annotations

import argparse
import datetime
import sys
import time

try:
    import serial
except ImportError:
    serial = None

# ==========================================================
# Default config
# ==========================================================
RELAY_PORT          = "COM3"
RELAY_BAUD_RATE     = 115200
RE_RELAY_CHANNEL    = 1
TSM4_RELAY_CHANNEL  = 2
RELAY_OFF_WAIT      = 8     # seconds to keep relay off before turning on
BETWEEN_WAIT        = 5     # seconds between RE relay-on and TSM4 relay-off

LOG_FILE = ""


def init_log_file(test_name: str) -> None:
    global LOG_FILE
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    LOG_FILE = f"{ts}_{test_name}.log"


def log(message: str) -> None:
    ts = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S.%f]")[:-3]
    line = f"{ts} [GUI] {message}"
    print(line, flush=True)
    if LOG_FILE:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def log_separator(message: str) -> None:
    border = "=" * 70
    log(border)
    log(message)
    log(border)


def relay_power_cycle(label: str, channel: int, args: argparse.Namespace) -> bool:
    if serial is None:
        log("ERROR: pyserial 未安裝，無法控制 relay board。")
        return False

    off_wait = args.relay_off_wait
    log_separator(f"{label} Relay Power-Cycle (channel {channel})")
    log(f"Relay port: {args.relay_port}, channel: {channel}, off_wait: {off_wait}s")

    try:
        with serial.Serial(args.relay_port, args.relay_baud_rate, timeout=1) as ser:
            log(f"relay {channel} off")
            ser.write(f"relay {channel} off\r".encode("utf-8"))
        log(f"relay {channel} off 已送出，等待 {off_wait} 秒...")
        time.sleep(off_wait)
        with serial.Serial(args.relay_port, args.relay_baud_rate, timeout=1) as ser:
            log(f"relay {channel} on")
            ser.write(f"relay {channel} on\r".encode("utf-8"))
        log(f"relay {channel} on 已送出")
        log_separator(f"{label} Relay Power-Cycle Done")
        return True
    except Exception as e:
        log(f"{label} relay power-cycle 失敗: {type(e).__name__}: {e}")
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Relay-only RE + TSM4 power-cycle")
    parser.add_argument("--relay-port", default=RELAY_PORT, help="Relay board serial port, e.g. COM3")
    parser.add_argument("--relay-baud-rate", type=int, default=RELAY_BAUD_RATE)
    parser.add_argument("--re-relay-channel", type=int, default=RE_RELAY_CHANNEL, help="Relay channel for RE power")
    parser.add_argument("--tsm4-relay-channel", type=int, default=TSM4_RELAY_CHANNEL, help="Relay channel for TSM4 power")
    parser.add_argument("--relay-off-wait", type=float, default=RELAY_OFF_WAIT, help="Seconds to keep relay off before turning on")
    parser.add_argument("--between-wait", type=float, default=BETWEEN_WAIT, help="Seconds to wait between RE relay-on and TSM4 relay-off")
    return parser.parse_args()


def main() -> int:
    init_log_file("tsm4_gui_reboot_booster")
    args = parse_args()

    re_ok = relay_power_cycle("RE", args.re_relay_channel, args)

    if args.between_wait > 0:
        log(f"RE relay-on 完成，等待 {args.between_wait} 秒後執行 TSM4 power-cycle...")
        time.sleep(args.between_wait)

    tsm4_ok = relay_power_cycle("TSM4", args.tsm4_relay_channel, args)

    return 0 if (re_ok and tsm4_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
