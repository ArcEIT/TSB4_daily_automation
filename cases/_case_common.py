"""Shared CLI helpers for modular case entry scripts."""
import argparse
from testlib import config as cfg


def add_common_args(parser: argparse.ArgumentParser):
    parser.add_argument("--loops", type=int, default=cfg.TOTAL_LOOPS)
    parser.add_argument("--booster-port", default=cfg.BOOSTER_PORT)
    parser.add_argument("--relay-port", default=cfg.RELAY_PORT)
    parser.add_argument("--threshold", type=int, default=cfg.ONBOARDING_THRESHOLD)
    parser.add_argument("--reset-threshold", type=int, default=cfg.RESET_ONBOARDING_THRESHOLD)
    parser.add_argument("--pass-cooldown-time", type=int, default=cfg.PASS_COOLDOWN_TIME)
    parser.add_argument("--loop-eth-restore-wait", type=int, default=cfg.LOOP_ETH_RESTORE_WAIT)
    parser.add_argument("--check-re-status-script", default=cfg.CHECK_RE_STATUS_SCRIPT)
    parser.add_argument("--check-re-status-com-port", default=None)
    parser.add_argument("--check-re-status-com-port-arg", default=cfg.CHECK_RE_STATUS_COM_PORT_ARG)
    parser.add_argument("--enable-fail-reboot-recovery", action="store_true", default=cfg.FAIL_RECOVERY_REBOOT_ENABLE, help="Enable optional RE reboot -f during fail recovery. Default is off.")
    parser.add_argument("--onboarding-check-mode", choices=["ssh_first", "serial", "ssh_only"], default=cfg.ONBOARDING_CHECK_MODE, help="Transport used for onboarding PASS/FAIL decision. Default: ssh_first.")
    parser.add_argument("--onboarding-ssh-host", default=cfg.ONBOARDING_SSH_HOST, help="RE SSH host for onboarding check. Default: auto-discover from serial br-lan IP.")
    parser.add_argument("--onboarding-ssh-timeout", type=int, default=cfg.ONBOARDING_SSH_TIMEOUT)
    parser.add_argument("--disable-onboarding-serial-fallback", action="store_true", help="When using ssh_first, do not fallback to serial command output for PASS/FAIL.")


def apply_common_args(args):
    cfg.TOTAL_LOOPS = args.loops
    cfg.BOOSTER_PORT = args.booster_port
    cfg.RELAY_PORT = args.relay_port
    cfg.ONBOARDING_THRESHOLD = args.threshold
    cfg.RESET_ONBOARDING_THRESHOLD = args.reset_threshold
    cfg.PASS_COOLDOWN_TIME = args.pass_cooldown_time
    cfg.LOOP_ETH_RESTORE_WAIT = args.loop_eth_restore_wait
    cfg.CHECK_RE_STATUS_SCRIPT = args.check_re_status_script
    cfg.CHECK_RE_STATUS_COM_PORT = args.check_re_status_com_port or cfg.BOOSTER_PORT
    cfg.CHECK_RE_STATUS_COM_PORT_ARG = args.check_re_status_com_port_arg or ""
    cfg.FAIL_RECOVERY_REBOOT_ENABLE = args.enable_fail_reboot_recovery
    cfg.ONBOARDING_CHECK_MODE = args.onboarding_check_mode
    cfg.ONBOARDING_SSH_HOST = args.onboarding_ssh_host
    cfg.ONBOARDING_SSH_TIMEOUT = args.onboarding_ssh_timeout
    cfg.ONBOARDING_SERIAL_FALLBACK_ENABLE = not args.disable_onboarding_serial_fallback
