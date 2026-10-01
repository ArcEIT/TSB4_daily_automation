"""Console.log analyzer: turn raw console evidence into a precise Fail_Reason.

Add a rule = write one function `rule_xxx(lines) -> Finding | None` and append it to RULES.
Rules only see the lines of the failed stage window and must never raise.
"""
import os
import re
from dataclasses import dataclass, field

_SWITCH_RE = re.compile(r"Switch backhaul: (ETH|WiFi) BH")
_POLL_TIMEOUT_RE = re.compile(r"PROGRESS-RESULT\] >>> .*: FAIL, timeout, max_total_limit=")
_FINAL_FAIL_RE = re.compile(r"PROGRESS-RESULT\] >>> .*: FAIL, Final Check Fail")
_RD_RE = re.compile(r"^\[RD DEBUG\]  (.*)$")
_STATE_RE = re.compile(r"Onboarding : (\w*)\s+Uptime: (\d{2}):(\d{2}):(\d{2})")
_PS_RE = re.compile(r"^(\d+) root\s+\d+\s+\S+\s+(.*)$")
_INITD_RE = re.compile(r"/etc/init\.d/(repacd|ezmesh) (start|restart|stop)")

MAX_REASON_LEN = 220


@dataclass
class Finding:
    rule_id: str
    summary: str
    evidence: list = field(default_factory=list)


def _read_lines(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read().replace("\x00", "")
    return text.splitlines()


def _stage_window(lines, stage="fail"):
    """Return (start, end) line indexes of the stage to analyze.

    fail: the stage that ended with the last 'FAIL, timeout' line (else up to EOF);
          start = last 'Switch backhaul' marker before that point.
    eth:  first ETH BH stage (control sample), up to the first WiFi BH marker.
    """
    n = len(lines)
    if stage == "eth":
        start = None
        for i, line in enumerate(lines):
            m = _SWITCH_RE.search(line)
            if not m:
                continue
            if start is None:
                if m.group(1) == "ETH":
                    start = i
            elif m.group(1) == "WiFi":
                return start, i
        return (start, n) if start is not None else (0, n)

    end = n
    for i in range(n - 1, -1, -1):
        if _POLL_TIMEOUT_RE.search(lines[i]):
            end = i + 1
            break
    start = 0
    for i in range(end - 1, -1, -1):
        if _SWITCH_RE.search(lines[i]):
            start = i
            break
    return start, end


def _clock(sec):
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


_POLL_HDR_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2}) 輪詢紀錄\]:")
_IP_RE = re.compile(r"^\s*> IP \(br-lan\)\s*:\s*(\S.*?)\s*$")
_BH_LINK_RE = re.compile(r"^\[RD DEBUG\]  (?:Connected to ([0-9a-fA-F:]{17}) \(on ath1\)|(Not connected))")
_STA_CONNECTING_RE = re.compile(r"Set to WiFi STA connecting (\d+) !!!")
_BRIDGE_MLD_RE = re.compile(r"^\[RD DEBUG\]\s+(mld1|mld1\.10)\s*$")
_NO_IP_VALUES = {"192.168.1.253", "No IP"}
_MARKER_TS_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} (\d{2}):(\d{2}):(\d{2})")


def _parse_poll_rounds(lines):
    """One dict per onboarding polling round: time (PC clock, s), br-lan ip, ath1 link state."""
    rounds = []
    cur = None
    in_final = False
    for raw in lines:
        h = _POLL_HDR_RE.match(raw)
        if h:
            cur = {"t": int(h.group(1)) * 3600 + int(h.group(2)) * 60 + int(h.group(3)),
                   "ip": None, "bssid": None, "connected": None}
            rounds.append(cur)
            in_final = False
            continue
        if cur is None:
            continue
        if "[Final Check]:" in raw:
            in_final = True
            continue
        if in_final:
            continue
        m = _IP_RE.match(raw)
        if m and cur["ip"] is None:
            cur["ip"] = m.group(1)
            continue
        b = _BH_LINK_RE.match(raw)
        if b and cur["connected"] is None:
            cur["connected"] = bool(b.group(1))
            cur["bssid"] = b.group(1)
    return rounds


def _bridge_counts(lines):
    """How many snapshots listed mld1 (no VLAN) / mld1.10 as a br-lan member."""
    mld1 = mld1_10 = 0
    for raw in lines:
        m = _BRIDGE_MLD_RE.match(raw)
        if m:
            if m.group(1) == "mld1":
                mld1 += 1
            else:
                mld1_10 += 1
    return mld1, mld1_10


def rule_repacd_restart_loop(lines):
    """ezmesh/repacd restarted again and again, so /tmp/arc_onboarding_state loops
    none/start/sync/done and never stays 'done' long enough for the Final Check."""
    states = []
    run_pids = []
    restart_pids = {"repacd": set(), "ezmesh": set()}
    final_fail = 0

    for raw in lines:
        if _FINAL_FAIL_RE.search(raw):
            final_fail += 1
            continue
        m = _RD_RE.match(raw)
        if not m:
            continue
        body = m.group(1)

        s = _STATE_RE.search(body)
        if s:
            sec = int(s.group(2)) * 3600 + int(s.group(3)) * 60 + int(s.group(4))
            states.append((s.group(1).lower() or "empty", sec))
            continue

        p = _PS_RE.match(body)
        if not p:
            continue
        pid, cmd = p.group(1), p.group(2)
        if "repacd-run.sh" in cmd and pid not in run_pids:
            run_pids.append(pid)
        i = _INITD_RE.search(cmd)
        if i:
            restart_pids[i.group(1)].add(pid)

    seen_done = False
    prev = None
    regress = 0
    for state, _ in states:
        if state == "done":
            seen_done = True
        elif seen_done and prev == "done":
            regress += 1
        prev = state

    restarts = len(restart_pids["repacd"]) + len(restart_pids["ezmesh"])
    if regress < 2 or (len(run_pids) < 3 and restarts < 2):
        return None

    first, last = states[0][1], states[-1][1]
    span = (last - first) % 86400

    seq = []
    for state, _ in states:
        if not seq or seq[-1] != state:
            seq.append(state)
    seq_text = ">".join(seq[:16]) + (">..." if len(seq) > 16 else "")

    rounds = [r for r in _parse_poll_rounds(lines) if r["ip"] not in (None, "N/A")]
    ip_ok = [r for r in rounds if r["ip"] not in _NO_IP_VALUES]
    has_ip = len(rounds) >= 3 and len(ip_ok) * 5 >= len(rounds) * 4
    ips_ok = sorted({r["ip"] for r in ip_ok})
    mld1_n, mld1_10_n = _bridge_counts(lines)
    no_vlan = mld1_n >= 3 and mld1_10_n == 0

    summary = (
        (f"br-lan 有 IP ({'/'.join(ips_ok)}), 但 " if has_ip else "")
        + f"repacd/ezmesh 反覆重啟 (>={regress} 次, {span}s), "
        f"onboarding state 在 none/start/sync/done 間循環, 無法維持 done"
        + ("; br-lan 成員為 mld1 (缺 mld1.10)" if no_vlan else "")
    )
    evidence = [
        f"onboarding state 由 done 掉回非 done: >={regress} 次, 觀察時間 {span}s (RE clock {_clock(first)} ~ {_clock(last)})",
        f"repacd-run.sh PID 更換: {len(run_pids)} 個不同 PID ({', '.join(run_pids[:8])}{'...' if len(run_pids) > 8 else ''})",
        f"init.d 重啟動作 (不同 process): repacd x{len(restart_pids['repacd'])}, ezmesh x{len(restart_pids['ezmesh'])}",
        f"polling Final Check Fail: {final_fail} 次",
        f"state 變化: {seq_text}",
    ]
    if has_ip:
        evidence.append(f"br-lan 有 IP {'/'.join(ips_ok)}: {len(ip_ok)}/{len(rounds)} 輪 (IP 正常, 問題在 onboarding 狀態不穩, 與 DHCP 無關)")
    if mld1_n or mld1_10_n:
        evidence.append(f"br-lan 成員快照: mld1 {mld1_n} 次, mld1.10 {mld1_10_n} 次 (PASS 的 WiFi BH 為 mld1.10, mld1 代表 VLAN 10 的 BH 介面沒有建立)")
    evidence.append("來源: Console.log 的 [RD DEBUG] ps 快照, 每約 10s 取樣一次, 次數為下限")
    return Finding("repacd_restart_loop", summary, evidence)


def rule_wifi_connected_no_ip(lines):
    """ath1 stays associated to the TSM4 BH, but br-lan never leaves the fallback IP.

    Reports only the facts seen on the RE side; it does not say whether the RE sent no
    DHCP DISCOVER or the TSM4 did not answer (that needs a packet capture)."""
    rounds = [r for r in _parse_poll_rounds(lines) if r["ip"] not in (None, "N/A")]
    n = len(rounds)
    if n < 3:
        return None

    # Never got a TSM4-assigned IP in any round, and ath1 was associated for most of the window.
    # The last rounds may be Not connected (RE re-initialising WiFi after a long no-IP period).
    if not all(r["ip"] in _NO_IP_VALUES for r in rounds):
        return None
    connected_idx = [i for i, r in enumerate(rounds) if r["connected"] is True]
    connected_n = len(connected_idx)
    if connected_n < 3 or connected_n * 2 < n:
        return None

    ips = sorted({r["ip"] for r in rounds})
    bssids = sorted({r["bssid"].lower() for r in rounds if r["bssid"]})
    first, last = rounds[0]["t"], rounds[-1]["t"]
    span = (last - first) % 86400
    tail_not_connected = 0
    for r in reversed(rounds):
        if r["connected"] is True:
            break
        tail_not_connected += 1

    sta_max = None
    for raw in lines:
        for m in _STA_CONNECTING_RE.finditer(raw):
            v = int(m.group(1))
            sta_max = v if sta_max is None else max(sta_max, v)

    relay_off_t = None
    if lines and "Switch backhaul: WiFi BH" in lines[0]:
        ts = _MARKER_TS_RE.match(lines[0])
        if ts:
            relay_off_t = int(ts.group(1)) * 3600 + int(ts.group(2)) * 60 + int(ts.group(3))

    summary = (
        f"WiFi BH 已連上 (ath1 Connected {connected_n}/{n} 輪) 但 br-lan 一直是 {'/'.join(ips)}, "
        f"未取得 DHCP IP"
    )
    evidence = [
        f"ath1 在 {connected_n}/{n} 輪 polling 為 Connected to {', '.join(bssids) or '?'}",
        f"br-lan IP 全部 {n} 輪都是備用 IP {'/'.join(ips)} (非 TSM4 發放), 觀察時間 {span}s (PC clock {_clock(first)} ~ {_clock(last)})",
    ]
    if relay_off_t is not None:
        dt = (rounds[connected_idx[0]]["t"] - relay_off_t) % 86400
        if connected_idx[0] == 0:
            evidence.append(f"polling 開始時 (relay OFF 後約 {dt}s) ath1 已連上, 實際連上時間更早")
        else:
            evidence.append(f"polling 首次看到 ath1 連上: relay OFF 後約 {dt}s (RE 找到 BH 的時間上限, 之前輪次為 Not connected)")
    if tail_not_connected:
        evidence.append(f"最後 {tail_not_connected} 輪 ath1 不是 Connected (Not connected 或無輸出), 可能是 RE 長時間沒 IP 後自行重啟 WiFi, 需以 RE logread 確認")
    if sta_max is not None:
        evidence.append(f"RE 自己的 no-IP 計數 Set to WiFi STA connecting: 最大 {sta_max} (約每 10s 加 1)")
    evidence.append("說明: 僅表示 RE 端沒取得 IP, 尚無法判斷是 RE 未送 DHCP DISCOVER 或 TSM4 未回應, 需 RE 端或空中封包確認")
    evidence.append("來源: Console.log 的 polling 紀錄 (IP br-lan) 與 [RD DEBUG] iw dev ath1 link")
    return Finding("wifi_connected_no_ip", summary, evidence)


RULES = [rule_repacd_restart_loop, rule_wifi_connected_no_ip]


def analyze_lines(lines, stage="fail"):
    start, end = _stage_window(lines, stage)
    window = lines[start:end]
    findings = []
    for rule in RULES:
        try:
            f = rule(window)
        except Exception:
            f = None
        if f:
            findings.append(f)
    return findings


def analyze(path, stage="fail"):
    """Analyze a Console.log. Returns [] on any problem (missing file, read error)."""
    try:
        if not path or not os.path.exists(path):
            return []
        return analyze_lines(_read_lines(path), stage)
    except Exception:
        return []


def format_reason_suffix(findings, sep=" | "):
    """Single-line suffix for Fail_Reason (the email parser keeps only that one line)."""
    if not findings:
        return ""
    text = "; ".join(f.summary for f in findings).replace("\n", " ")
    suffix = f"{sep}Console: {text}"
    return suffix if len(suffix) <= MAX_REASON_LEN else suffix[: MAX_REASON_LEN - 3] + "..."


def format_detail_block(findings):
    if not findings:
        return ""
    out = ["Console_Analysis:"]
    for f in findings:
        out.append(f"- [{f.rule_id}] {f.summary}")
        out.extend(f"    {line}" for line in f.evidence)
    return "\n".join(out) + "\n"
