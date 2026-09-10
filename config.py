"""Runtime configuration for modular onboarding test cases."""
import os

# =============================================================================
# GLOBAL TEST CONTROL
# =============================================================================
TOTAL_LOOPS = 1

# =============================================================================
# HARDWARE PORTS
# =============================================================================
BOOSTER_PORT             = "COM8"
RELAY_PORT               = "COM3"
BAUD_RATE                = 115200
RELAY_ETH_PORT           = 6   # Relay channel: ETH backhaul switch
TSM4_POWER_RELAY_PORT    = 2   # Relay channel: TSM4 power
RE_COLD_POWER_RELAY_PORT = 1   # Relay channel: RE cold-reboot power (Case4)

# =============================================================================
# WEB GUI / SELENIUM
# =============================================================================
GATEWAY_URL    = "http://192.168.0.1/"
ROUTER_USERNAME = "admin"
#ROUTER_PASSWORD = "ngcvgds6fv"
#dennis tsm4
ROUTER_PASSWORD = "jum8gf2zry"

CHROME_DRIVER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "chromedriver.exe"
)
WAIT_TIMEOUT          = 30   # Selenium explicit-wait timeout (s)
WEB_GUI_OPEN_RETRY    = 6
WEB_GUI_OPEN_RETRY_WAIT = 10

# =============================================================================
# SSH  (onboarding check & log collect share these credentials)
# =============================================================================
# ONBOARDING_CHECK_MODE:
#   ssh_first – SSH for PASS/FAIL; fallback to serial if SSH not ready.
#   serial    – serial marker command only.
#   ssh_only  – SSH only; no serial fallback.
ONBOARDING_CHECK_MODE             = "ssh_first"
ONBOARDING_SERIAL_FALLBACK_ENABLE = True
ONBOARDING_SSH_HOST               = None   # None = auto-discover via serial console
ONBOARDING_SSH_USERNAME           = "25g5@rIj2Z"
ONBOARDING_SSH_PASSWORD           = "x@u4194j042u/4m,4@"
ONBOARDING_SSH_PORT               = 22
ONBOARDING_SSH_TIMEOUT            = 5
ONBOARDING_SSH_DISCOVER_INTERFACE = "br-lan"
ONBOARDING_SSH_IP_PREFIX          = "192.168.0."
ONBOARDING_SSH_DISCOVER_INTERVAL  = 60

# =============================================================================
# SERIAL / DEBUG LOGGING
# =============================================================================
# Full-session serial logging: captures COM output across all phases
# (GUI, SSH, relay, wait, onboarding, recovery).
FULL_SESSION_SERIAL_LOG_ENABLE          = True
ONBOARDING_BACKGROUND_SERIAL_LOG_ENABLE = FULL_SESSION_SERIAL_LOG_ENABLE  # Backward-compat (v11)

# RD debug command dump during onboarding polling.
# Output goes to full console log only; does not affect PASS/FAIL.
RD_POLL_DEBUG_ENABLE         = True
RD_POLL_DEBUG_EVERY_N_ROUNDS = 1      # 1 = every round, 2 = every other round
RD_POLL_DEBUG_GROUP_MODE     = True   # Send all commands as one shell group
# Max timeout for the whole RD debug group (group mode).
# Keep comfortably below POLLING_INTERVAL to leave margin for onboarding
# check and serial/SSH overhead.
RD_POLL_DEBUG_READ_TIME  = 7     # (s); POLLING_INTERVAL is currently 10
RD_POLL_DEBUG_SLICE_TIME = 0.25  # How often Python polls for the done marker (s)
# 1 iw command + 3 RD scripts. Do not add sleeps; polling wait is in onboarding.py.
RD_POLL_DEBUG_COMMANDS = [
    "iw dev ath1 link",
    "DFS_CAC_chk.sh",
    "WiFi_inf_ChOnOff.sh",
    "chk_Status.sh",
]

# Log file placeholders – set by each case script before logger.init_log_filenames().
TEST_CASE_NAME   = "unknown_case"
CASE_ID          = TEST_CASE_NAME
FULL_CONSOLE_LOG = ""
SUMMARY_LOG      = ""

# =============================================================================
# ONBOARDING TIMERS – SHARED
# =============================================================================
POLLING_INTERVAL = 10  # Polling loop interval (s)

# Max-timeout buckets – pick one per case:
#   NORMAL_MAX_TOTAL_LIMIT – standard onboarding / reboot cases.
#   RESET_MAX_TOTAL_LIMIT  – factory-default / reset flows (need more time).
NORMAL_MAX_TOTAL_LIMIT = 600
RESET_MAX_TOTAL_LIMIT  = 960
MAX_TOTAL_LIMIT        = NORMAL_MAX_TOTAL_LIMIT  # Backward-compat alias

# PASS/FAIL consecutive-check thresholds:
#   ONBOARDING_THRESHOLD       – normal onboarding / reboot cases.
#   RESET_ONBOARDING_THRESHOLD – reset / factory-default cases.
ONBOARDING_THRESHOLD       = 3
RESET_ONBOARDING_THRESHOLD = 5

PASS_COOLDOWN_TIME          = 60
FINAL_ONBOARDING_CHECK_WAIT = 3
INIT_WAIT_TIME              = 120  # Generic init wait (s); most cases override this

# Relay settle / BH restore
RELAY_SETTLE_TIME     = 3     # Wait after relay switch before next action (s)
RESTORE_ETH_BH_WAIT   = 10   # Wait after restoring ETH BH relay (s)
LOOP_ETH_RESTORE_WAIT = 120  # Multi-loop cooldown: after WiFi BH PASS, restore relay → ETH BH

# =============================================================================
# XPATH – SHARED (Login / Nav / Modals)
# =============================================================================
# Login page
XPATH_LOGIN_USER = "/html/body/app-root/app-login/div/header/div[2]/form/div/div[1]/input"
XPATH_LOGIN_PASS = "/html/body/app-root/app-login/div/header/div[2]/form/div/div[2]/input"

# FW version label on login screen
XPATH_ROUTER_FW  = "/html/body/app-root/app-login/div/main/div[1]/div[1]/div[1]/span[2]"

# Top nav quick-links
XPATH_WIFI_SETTINGS = "/html/body/app-root/app-main-base/div/app-header/nav/div/div[2]/app-quick-links/div/div[3]/div/div/a/p"
XPATH_SETTINGS      = "/html/body/app-root/app-main-base/div/app-header/nav/div/div[2]/app-quick-links/div/div[4]/div/div/a/p"

# WiFi sub-tabs
XPATH_WIFI_MESH     = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/app-top-menu/nav/div/ul/li[4]/a"
XPATH_WIFI_BOOSTERS = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/app-top-menu/div[1]/nav/div/ul/li[2]/a"

# Modal dialogs
XPATH_CONFIRM_YES       = "/html/body/ngb-modal-window/div/div/app-generic-modal/div[3]/button[2]"
XPATH_DISCARD_CLOSE_BTN = "/html/body/ngb-modal-window/div/div/app-modal-discard-changes/div[3]/div/button[2]"

# WiFi Basic page – shared by Case 10, 11, 12
XPATH_WIRELESS_ENABLE_TOGGLE = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[2]/div[2]/div/div/div/app-label-toggle/div/div[2]/div"
XPATH_WIFI_BASIC_APPLY       = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[4]/div/button[2]"

# =============================================================================
# CASE 1 – Initial Onboarding (factory default or normal)
# =============================================================================
CASE1_FACTORY_DEFAULT_INIT_WAIT_TIME  = 250
CASE1_NORMAL_INIT_WAIT_TIME           = 90
# Both modes use RESET_MAX_TOTAL_LIMIT; factory default is a reset-style flow.
CASE1_FACTORY_DEFAULT_MAX_TOTAL_LIMIT = RESET_MAX_TOTAL_LIMIT
CASE1_NORMAL_MAX_TOTAL_LIMIT          = RESET_MAX_TOTAL_LIMIT

# =============================================================================
# CASE 2 – ETH / WiFi BH Switch
# =============================================================================
CASE2_ETH_ONBOARDING_INIT_WAIT_TIME  = 20
CASE2_WIFI_ONBOARDING_INIT_WAIT_TIME = 90
CASE2_ONBOARDING_INIT_WAIT_TIME      = CASE2_ETH_ONBOARDING_INIT_WAIT_TIME  # Backward-compat
CASE2_MAX_TOTAL_LIMIT                = NORMAL_MAX_TOTAL_LIMIT

# =============================================================================
# CASE 3 – RE Warm Reboot
# =============================================================================
RE_WARM_REBOOT_POST_WAIT             = 10   # Wait after reboot command sent (s)
RE_WARM_REBOOT_RELAY_POST_WAIT       = 15   # Wait after relay action (s)
RE_WARM_REBOOT_INIT_WAIT_TIME        = 150  # Backward-compat
CASE3_ETH_ONBOARDING_INIT_WAIT_TIME  = 60
CASE3_WIFI_ONBOARDING_INIT_WAIT_TIME = 90
CASE3_MAX_TOTAL_LIMIT                = NORMAL_MAX_TOTAL_LIMIT

# =============================================================================
# CASE 4 – RE Cold Reboot
# =============================================================================
RE_COLD_REBOOT_POWER_OFF_TIME        = 10   # Duration to cut RE power (s)
RE_COLD_REBOOT_POST_WAIT             = 10   # Wait after power restored (s)
RE_COLD_REBOOT_RELAY_POST_WAIT       = 15   # Wait after relay action (s)
RE_COLD_REBOOT_INIT_WAIT_TIME        = 140  # Backward-compat
CASE4_ETH_ONBOARDING_INIT_WAIT_TIME  = 90
CASE4_WIFI_ONBOARDING_INIT_WAIT_TIME = 160  # (was 191)
CASE4_MAX_TOTAL_LIMIT                = NORMAL_MAX_TOTAL_LIMIT
# NOTE: relay channel used by this case -> RE_COLD_POWER_RELAY_PORT (see HARDWARE PORTS)

# =============================================================================
# CASE 5 – TSM4 GUI Restart
# =============================================================================
TSM4_REBOOT_CHROME_CLOSE_WAIT        = 5    # Wait before closing Chrome (s)
TSM4_REBOOT_POST_WAIT                = 10   # Wait after restart triggered (s)
TSM4_REBOOT_RELAY_POST_WAIT          = 120  # Wait after relay restores power (s)
TSM4_RELAY_REBOOT_OFF_WAIT           = 8    # How long relay stays off when power-cycling TSM4 (s)
TSM4_GUI_RELAY_BOOT_WAIT             = 300  # Wait after relay on before GUI retry (s)
CASE5_ETH_ONBOARDING_INIT_WAIT_TIME  = 30
CASE5_WIFI_ONBOARDING_INIT_WAIT_TIME = 120
CASE5_MAX_TOTAL_LIMIT                = NORMAL_MAX_TOTAL_LIMIT

# XPATH – Case 5 (Settings → Maintenance → Restart)
XPATH_MAINTENANCE  = "/html/body/app-root/app-main-base/div/div/main/app-mybox-main/div/div/app-top-menu/nav/div/ul/li[8]/a"
XPATH_TSM4_RESTART = "/html/body/app-root/app-main-base/div/div/main/app-mybox-main/div/div/app-maintenace-main/div/div/app-maintenance-resets/form/div/div[1]/div[2]/button"

# =============================================================================
# CASE 6 – GW Reboot All RE
# =============================================================================
GW_FW_TO_GUI_ACTION_SLEEP            = 3    # Wait between FW-version fetch and GUI action (s)
REBOOT_SYNC_WAIT                     = 20   # Wait for GW to sync after reboot trigger (s)
REBOOT_INIT_WAIT_TIME                = 120  # Backward-compat
CASE6_ETH_ONBOARDING_INIT_WAIT_TIME  = 90
CASE6_WIFI_ONBOARDING_INIT_WAIT_TIME = 210  # (was 240)
CASE6_MAX_TOTAL_LIMIT                = NORMAL_MAX_TOTAL_LIMIT

# XPATH – Case 6 / 7 / 8 / 9 (Mesh extender reboot / reset buttons, shared)
XPATH_REBOOT_ALL = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-mesh/div/app-wifi-mesh-extenders/div/div/div[1]/button[1]"
XPATH_RESET_ALL  = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-mesh/div/app-wifi-mesh-extenders/div/div/div[1]/button[2]"
XPATH_REBOOT_RE  = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-mesh/div/app-wifi-mesh-extenders/div/div/div[1]/button[3]"
XPATH_RESET_RE   = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-mesh/div/app-wifi-mesh-extenders/div/div/div[1]/button[4]"

# =============================================================================
# CASE 7 – GW Reset All RE
# =============================================================================
RESET_INIT_WAIT_TIME                 = 180  # Backward-compat
CASE7_ETH_ONBOARDING_INIT_WAIT_TIME  = 230
CASE7_WIFI_ONBOARDING_INIT_WAIT_TIME = 500
CASE7_MAX_TOTAL_LIMIT                = RESET_MAX_TOTAL_LIMIT

# =============================================================================
# CASE 8 – GW Reboot Single RE
# =============================================================================
CASE8_ETH_ONBOARDING_INIT_WAIT_TIME  = 110  # (was 130)
CASE8_WIFI_ONBOARDING_INIT_WAIT_TIME = 180  # (was 200)
CASE8_MAX_TOTAL_LIMIT                = NORMAL_MAX_TOTAL_LIMIT

# =============================================================================
# CASE 9 – GW Reset Single RE
# =============================================================================
CASE9_ETH_ONBOARDING_INIT_WAIT_TIME  = 180  # (was 201)
CASE9_WIFI_ONBOARDING_INIT_WAIT_TIME = 450  # (was 491)
CASE9_MAX_TOTAL_LIMIT                = RESET_MAX_TOTAL_LIMIT

# =============================================================================
# CASE 10 – Main WiFi SSID / Key Modify
# =============================================================================
# Monitor time for RE sync after GUI Apply.
CASE10_ETH_AFTER_GUI_APPLY_MONITOR_TIME  = 120
CASE10_WIFI_AFTER_GUI_APPLY_MONITOR_TIME = 180

# Random profile for Case10
CASE10_ETH_SSID_PREFIX = "ETHSYNC"
CASE10_WIFI_SSID_PREFIX = "WIFISYNC"
CASE10_WIFI_KEY_PREFIX = "K"
CASE10_SSID_RANDOM_LEN = 8
CASE10_WIFI_KEY_RANDOM_LEN = 14
CASE10_SPECIAL_CHARS = ""
CASE10_KEY_SPECIAL_CHARS = "!@#%^&*_-+=?"

# Pre-GUI wait for WiFi BH stage: let RE establish WiFi BH and get DHCP before changing SSID.
# In normal use, the RE is already connected when the user changes SSID.
# Without this wait, the SSID change happens before RE gets DHCP, causing DHCP failure.
CASE10_WIFI_BH_PRE_GUI_WAIT = 60

# GUI wait for Case10
CASE10_WIFI_PAGE_WAIT = 10
CASE10_BEFORE_APPLY_WAIT = 2
CASE10_AFTER_APPLY_WAIT = 5

# SSH UCI check
CASE10_SSH_UCI_TIMEOUT = 15

# UCI commands – Case 10 Main WiFi VAP index validation
CASE10_SSID_UCI_CMDS = [
    "uci get wireless.@wifi-iface[2].ssid",
    "uci get wireless.@wifi-iface[5].ssid",
    "uci get wireless.mld3.mld_ssid",
]
CASE10_KEY_UCI_CMDS = [
    "uci get wireless.@wifi-iface[2].key",
    "uci get wireless.@wifi-iface[2].sae_password",
    "uci get wireless.@wifi-iface[5].key",
    "uci get wireless.@wifi-iface[5].sae_password",
]

# XPATH – Case 10 (Main WiFi SSID / Key Modify)
# NOTE: also uses shared XPATH_WIRELESS_ENABLE_TOGGLE / XPATH_WIFI_BASIC_APPLY above.
XPATH_MAIN_WIFI_SSID_INPUT = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[2]/div[8]/app-label-input/div/div[2]/input"
XPATH_MAIN_WIFI_KEY_INPUT  = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[3]/div[8]/div/div/div/input"

# =============================================================================
# CASE 11 – Guest WiFi SSID / Key Modify
# =============================================================================
# Monitor time for RE sync after GUI Apply.
CASE11_ETH_AFTER_GUI_APPLY_MONITOR_TIME  = 120
CASE11_WIFI_AFTER_GUI_APPLY_MONITOR_TIME = 200

# Random profile for Case11
CASE11_ETH_GUEST_SSID_PREFIX  = "ETHGUEST"
CASE11_WIFI_GUEST_SSID_PREFIX = "WIFIGUEST"
CASE11_GUEST_WIFI_KEY_PREFIX  = "K"
CASE11_SSID_RANDOM_LEN        = 8
CASE11_WIFI_KEY_RANDOM_LEN    = 14
CASE11_SPECIAL_CHARS          = ""
CASE11_KEY_SPECIAL_CHARS      = "!@#%^&*_-+=?"

# Pre-GUI wait for WiFi BH stage: let RE establish WiFi BH and get DHCP before changing SSID.
CASE11_WIFI_BH_PRE_GUI_WAIT = 70

# GUI wait for Case11
CASE11_GUI_OPEN_WAIT = 2
CASE11_GUI_AFTER_LOGIN_INPUT_WAIT = 0.5
CASE11_GUI_AFTER_LOGIN_WAIT = 2
CASE11_GUI_WIFI_PAGE_WAIT = 10
CASE11_GUI_GUEST_PAGE_WAIT = 5
CASE11_GUI_FIELD_SCROLL_WAIT = 0.5
CASE11_GUI_BEFORE_APPLY_WAIT = 1.5
CASE11_GUI_AFTER_APPLY_CLICK_WAIT = 1
CASE11_GUI_AFTER_APPLY_DONE_WAIT = 2
CASE11_GUI_BEFORE_QUIT_WAIT = 3
CASE11_GUI_TOGGLE_WAIT = 2
CASE11_GUI_DISCARD_MODAL_WAIT = 1.5

# SSH UCI check for Case11
CASE11_SSH_UCI_TIMEOUT = 15
CASE11_KEY_MATCH_MODE = "per_band_any"
CASE11_KEY_UCI_GROUPS = [
    ["uci get wireless.@wifi-iface[7].sae_password", "uci get wireless.@wifi-iface[7].key"],
    ["uci get wireless.@wifi-iface[8].sae_password", "uci get wireless.@wifi-iface[8].key"],
]

# Cleanup after Case11 PASS
CASE11_CLEANUP_DISABLE_GUEST_WIFI = True

# UCI commands – Case 11 Guest WiFi VAP index validation
CASE11_GUEST_SSID_UCI_CMDS = [
    "uci get wireless.@wifi-iface[7].ssid",
    "uci get wireless.mld8.mld_ssid",
    "uci get wireless.@wifi-iface[8].ssid",
]
CASE11_GUEST_KEY_UCI_CMDS = [
    "uci get wireless.@wifi-iface[7].sae_password",
    "uci get wireless.@wifi-iface[7].key",
    "uci get wireless.@wifi-iface[8].sae_password",
    "uci get wireless.@wifi-iface[8].key",
]

# XPATH – Case 11 (Guest WiFi SSID / Key Modify)
XPATH_GUEST_WIFI_TAB           = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/app-top-menu/nav/div/ul/li[10]/a"
XPATH_GUEST_WIFI_ENABLE_TOGGLE = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[2]/div[2]/div/div/div/app-label-toggle/div/div[2]/div"
XPATH_GUEST_WIFI_SSID_INPUT    = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[2]/div[8]/app-label-input/div/div[2]/input"
XPATH_GUEST_WIFI_KEY_INPUT     = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[3]/div[8]/div/div/div/input"
XPATH_GUEST_WIFI_APPLY_BTN     = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[5]/div/button[2]"
XPATH_GUEST_WIFI_DISCARD_YES   = "/html/body/ngb-modal-window/div/div/app-modal-discard-changes/div[3]/div/button[2]"

# =============================================================================
# CASE 12 – TSM4 Wireless FH Disable / Enable Sync Check
# =============================================================================
CASE12_ETH_BH_INIT_WAIT     = 20
CASE12_WIFI_BH_INIT_WAIT    = 240
CASE12_WIRELESS_SYNC_WAIT   = 300
CASE12_ENABLE_RECOVERY_WAIT = 60
CASE12_FINAL_ENABLE_WAIT    = 10

CASE12_GUI_MAX_ATTEMPTS     = 2
CASE12_GUI_RETRY_WAIT       = 30

CASE12_FH_24G_DISABLED_CMD  = "uci get wireless.@wifi-iface[2].disabled"
CASE12_FH_5G_DISABLED_CMD   = "uci get wireless.@wifi-iface[5].disabled"

# WiFi_inf_ChOnOff active beacon check
CASE12_WIFI_INF_CHECK_ENABLE = True
CASE12_WIFI_INF_CHONOFF_CMD = "WiFi_inf_ChOnOff.sh"
CASE12_WIFI_INF_TARGET_INDEXES = ["2", "5"]

# XPATH – CASE 12  (TSM4 Wireless FH Disable / Enable Sync Check)
CASE12_XPATH_WIRELESS_ENABLE_TOGGLE = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[2]/div[2]/div/div/div/app-label-toggle/div/div[2]/div"
CASE12_XPATH_WIFI_BASIC_APPLY       = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-basic/form/div[4]/div/button[2]"

# =============================================================================
# CASE 13 – BH Random SSID Lost Connect Check
# =============================================================================
CASE13_ETH_ONBOARDING_INIT_WAIT_TIME = 20
CASE13_MAX_TOTAL_LIMIT = NORMAL_MAX_TOTAL_LIMIT
CASE13_ONBOARDING_THRESHOLD = ONBOARDING_THRESHOLD

# TSM4 lost-connect timing
CASE13_TSM4_POWER_OFF_WAIT = 30
CASE13_TSM4_POWER_RESTORE_WAIT = 120

# Random BH SSID validation
CASE13_EXPECTED_RANDOM_PREFIX = "BH_5_"
CASE13_ARC_FH_RANDOM_SSID_CMD = "uci get wireless.@wifi-iface[4].ArcFHRandomSSID"
CASE13_BH_SSID_CMD = "uci get wireless.@wifi-iface[4].ssid"
CASE13_UCI_CHECK_READ_TIME = 3

# =============================================================================
# CASE 14 – TSM4 WPS Button + RE WPS Onboarding
# =============================================================================
CASE14_RE_FACTORY_DEFAULT_CMD = "factory_default"
CASE14_RE_FACTORY_DEFAULT_POST_WAIT = 40
CASE14_WIFI_BH_PRE_WPS_WAIT = 240
CASE14_AFTER_TSM4_WPS_WAIT = 3
CASE14_RE_WPS_CMD = "wpa_cli -p /var/run/wpa_supplicant-ath1 wps_pbc multi_ap=2"
CASE14_RE_WPS_READ_TIME = 3
CASE14_WPS_ONBOARDING_INIT_WAIT = 300
CASE14_MAX_TOTAL_LIMIT = NORMAL_MAX_TOTAL_LIMIT
CASE14_ONBOARDING_THRESHOLD = ONBOARDING_THRESHOLD
CASE14_WPS_BROWSER_CLOSE_WAIT = 5

# XPATH – Case 14 (TSM4 WPS 5GHz Push Button)
XPATH_WPS_TAB = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/app-top-menu/nav/div/ul/li[5]/a"
XPATH_WPS_5G_TAB = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/app-top-menu/div[1]/nav/div/ul/li[2]/a"
XPATH_WPS_5G_PUSH_BUTTON = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-wps/div/div/form/div[3]/div/div/div[1]/div/div/ol/li[2]/button/div"
XPATH_WPS_5G_PUSH_BUTTON_FALLBACK = "/html/body/app-root/app-main-base/div/div/main/app-wifi-main/div/div/div/app-wifi-wps/div/div/form/div[3]/div/div/div[1]/div/div/ol/li[2]"
XPATH_DISCARD_CHANGES_YES = "/html/body/ngb-modal-window/div/div/app-modal-discard-changes/div[3]/div/button[2]"

# =============================================================================
# FAIL DIAGNOSTIC & RECOVERY (cross-case, shared)
# =============================================================================
# check_RE_status.py – runs on FAIL to capture RE state before recovery.
CHECK_RE_STATUS_ENABLE       = True
CHECK_RE_STATUS_SCRIPT       = "check_RE_status.py"
CHECK_RE_STATUS_TIMEOUT      = 120
CHECK_RE_STATUS_COM_PORT     = None
CHECK_RE_STATUS_COM_PORT_ARG = ""   # Set to "--com-port" if script requires named option

# Diagnostic log collect – gather /tmp/diagnosticcomlog.tgz from RE on FAIL.
# Triggered inside safe_handle_fail_recovery() after check_RE_status.py.
RE_LOG_COLLECT_ENABLE        = True
RE_LOG_COLLECT_SCRIPT        = "collect_diagnosticcomlog_on_fail.py"
# recovery.py parses candidate 192.168.0.x / 192.168.1.x IPs from
# check_RE_status output, then appends the fallback hosts below.
RE_LOG_COLLECT_HOSTS         = ["192.168.1.253"]
RE_LOG_COLLECT_FALLBACK_HOST = "192.168.1.253"
RE_LOG_COLLECT_USERNAME      = ONBOARDING_SSH_USERNAME
RE_LOG_COLLECT_PASSWORD      = ONBOARDING_SSH_PASSWORD
RE_LOG_COLLECT_SSH_PORT      = 22
RE_LOG_COLLECT_SSH_TIMEOUT   = 15
RE_LOG_COLLECT_RUN_TIMEOUT   = 180  # diagnosticcomlog.sh script timeout (s)
RE_LOG_COLLECT_TIMEOUT       = 300  # Total subprocess timeout (s)
# Project root so the zip script can glob("*diagnosticcomlog.tgz").
RE_LOG_COLLECT_OUTPUT_DIR    = "."

# Fail recovery reboot – send `reboot -f` to RE after diagnostics.
# Default OFF; enable only when explicitly needed.
FAIL_RECOVERY_REBOOT_ENABLE = False
FAIL_RECOVERY_REBOOT_WAIT   = 180  # Wait for RE to stabilise after reboot -f (s)
FAIL_RECOVERY_REASON_SUFFIX = "FailDiagnostic(check_RE_status_collect_diag_restore_ETH_BH)"

TSM4_REBOOT_MONITOR_TIME = 300  # Max monitor time after TSM4 recovery reboot (s)

# =============================================================================
# WIFI BH TCPDUMP DEBUG (cross-case, shared)
# =============================================================================
# Captures DHCP traffic on ath1 whenever any case switches to WiFi BH (relay off).
# Hooked automatically via relay.py — no per-case code changes needed.
#   On PASS: tcpdump killed + pcap deleted from RE (SSH).
#   On FAIL: tcpdump killed (serial), pcap downloaded to RE_LOG_COLLECT_OUTPUT_DIR
#            after ETH BH is restored and RE obtains a DHCP address.
WIFI_BH_TCPDUMP_ENABLE             = False
WIFI_BH_TCPDUMP_IFACE              = "ath1"
WIFI_BH_TCPDUMP_REMOTE_PATH        = "/tmp/wifi_bh_dhcp.pcap"
WIFI_BH_TCPDUMP_MAX_PACKETS        = 300   # -c limit; capture stops automatically
WIFI_BH_TCPDUMP_POST_RECOVERY_WAIT = 25    # (s) wait after ETH BH restore for RE DHCP before SCP
