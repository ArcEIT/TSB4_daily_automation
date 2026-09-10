"""Relay control helpers."""
import serial
from . import config as cfg
from .logger import log_progress, log_step, log_result
from .serial_console import receive_monitor


def control_relay_channel(channel, state):
    try:
        with serial.Serial(cfg.RELAY_PORT, cfg.BAUD_RATE, timeout=1) as ser:
            ser.write(f"relay {channel} {state}\r".encode("utf-8"))
        log_progress(f"[RELAY] 成功下達指令: relay {channel} {state}")
        if int(channel) == int(cfg.RELAY_ETH_PORT):
            if str(state).lower() == "on":
                log_step(f"Switch backhaul: ETH BH, relay {channel} on")
            elif str(state).lower() == "off":
                log_step(f"Switch backhaul: WiFi BH, relay {channel} off")
            else:
                log_step(f"Switch relay {channel}: {state}")
        else:
            log_step(f"Switch relay {channel}: {state}")
        return True
    except Exception as e:
        log_progress(f"[RELAY] 操作失敗: relay {channel} {state}, error={e}")
        log_result(f"Relay command FAIL: relay {channel} {state}, error={type(e).__name__}: {e}")
        return False


def control_relay(state):
    ok = control_relay_channel(cfg.RELAY_ETH_PORT, state)
    if ok and str(state).lower() == "off":
        # WiFi BH switch succeeded – start tcpdump on ath1 if enabled
        try:
            from .tcpdump_debug import start_wifi_bh_tcpdump
            start_wifi_bh_tcpdump()
        except Exception as e:
            log_progress(f"[TCPDUMP] start error (non-fatal): {type(e).__name__}: {e}")
    return ok


def restore_eth_backhaul(reason="case finished"):
    # Stop tcpdump and clean up pcap before switching relay back to ETH BH.
    # On PASS (RE has WiFi BH IP): SSH kill+rm.  On FAIL (no IP): serial kill only.
    try:
        from .tcpdump_debug import stop_and_cleanup_wifi_bh_tcpdump
        stop_and_cleanup_wifi_bh_tcpdump()
    except Exception as e:
        log_progress(f"[TCPDUMP] stop+cleanup error (non-fatal): {type(e).__name__}: {e}")
    try:
        log_step(f"Restore ETH BH: reason={reason}, wait={cfg.RESTORE_ETH_BH_WAIT}s, relay {cfg.RELAY_ETH_PORT} on")
        log_progress(f"[RESTORE] {reason}: 等待 {cfg.RESTORE_ETH_BH_WAIT} 秒後切回 ETH BH - relay {cfg.RELAY_ETH_PORT} on")
        receive_monitor(cfg.RESTORE_ETH_BH_WAIT)
        control_relay_channel(cfg.RELAY_ETH_PORT, "on")
        receive_monitor(cfg.RELAY_SETTLE_TIME)
        log_progress("[RESTORE] 已切回 ETH BH - relay 6 on")
        log_result("Restore ETH BH PASS")
    except Exception as e:
        log_progress(f"[RESTORE] 切回 ETH BH 失敗: {type(e).__name__}: {e}")
        log_result(f"Restore ETH BH FAIL: {type(e).__name__}: {e}")


def restore_eth_backhaul_between_loops(loop):
    """Switch back to ETH BH and wait before starting the next loop.

    This is used only between loops. The final test cleanup still uses
    restore_eth_backhaul().
    """
    if loop >= cfg.TOTAL_LOOPS:
        return
    # Stop tcpdump and clean up pcap before switching relay back to ETH BH
    try:
        from .tcpdump_debug import stop_and_cleanup_wifi_bh_tcpdump
        stop_and_cleanup_wifi_bh_tcpdump()
    except Exception as e:
        log_progress(f"[TCPDUMP] stop+cleanup error (non-fatal): {type(e).__name__}: {e}")
    try:
        log_step(f"Loop {loop}: restore ETH BH for next loop, relay {cfg.RELAY_ETH_PORT} on")
        log_progress(f"LOOP {loop} WiFi BH PASS，切回 ETH BH 準備下一輪 - relay {cfg.RELAY_ETH_PORT} on")
        control_relay_channel(cfg.RELAY_ETH_PORT, "on")
        log_step(f"Loop {loop} -> Loop {loop + 1}: ETH BH restore cooldown={cfg.LOOP_ETH_RESTORE_WAIT}s")
        log_progress(f"LOOP {loop} -> LOOP {loop + 1}: ETH BH restore cooldown = {cfg.LOOP_ETH_RESTORE_WAIT} 秒...")
        receive_monitor(cfg.LOOP_ETH_RESTORE_WAIT)
    except Exception as e:
        log_progress(f"LOOP {loop} 切回 ETH BH / cooldown 失敗: {type(e).__name__}: {e}")
        log_result(f"Loop {loop}: restore ETH BH FAIL: {type(e).__name__}: {e}")
