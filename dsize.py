#!/usr/bin/env python3
"""
dsize · 갭필 챔피언 스펙(09:45 · 커버≥0.40 · 트레일 0.15 · 11:30컷) 델타 0.60 사이징 시뮬
  스트라이크 = BSM 델타 0.60 역산 ($1 라운딩) — 기존 0.5% ITM 고정과 별개
  계좌 $2,000 · 사이징 30/40/50/60/70% · 정수계약(현실) + 이상형(분수) 병기
  1년차 / 2년차 / 전체 26개월 분리 — 각각 최종잔고 · MDD · 거래수
"""
import datetime as dt, json, math
import pandas as pd, yfinance as yf
from minres2 import load_1m, agg, run, norm, COVER_MIN, SPREAD, RFR, K_MAIN
try:
    from py_vollib.black_scholes import black_scholes as _bs
except Exception:
    _bs = None
from statistics import NormalDist

DELTA_TGT = 0.60
TRAIL = 0.15
CAPITAL = 2000.0
SIZES = [0.30, 0.40, 0.50, 0.60, 0.70]
Y1_END = dt.date(2025, 8, 22)


def strike_for_delta(sgn, S, tau, iv):
    """|delta|=0.60 스트라이크. call: N(d1)=0.6 · put: N(d1)=0.4"""
    z = NormalDist().inv_cdf(0.60 if sgn < 0 else 0.40)  # 갭다운→콜, 갭업→풋
    K = S * math.exp((RFR + iv * iv / 2) * tau - z * iv * math.sqrt(tau))
    return round(K)


def opt_pl(sgn, ep, exit_px, t0, hold, iv):
    """(순손익%, 진입프리미엄$) — 델타 타겟 스트라이크로 BSM 재평가"""
    flag = "p" if sgn > 0 else "c"
    t0h = t0.hour + t0.minute / 60
    tau0 = max(16.0 - t0h, 0.05) / 24 / 365
    tau1 = max(16.0 - t0h - hold, 0.02) / 24 / 365
    K = strike_for_delta(sgn, ep, tau0, iv)
    p0 = _bs(flag, ep, K, tau0, RFR, iv)
    p1 = _bs(flag, exit_px, K, tau1, RFR, iv)
    if p0 <= 0.01:
        return None, None
    return max((p1 - p0) / p0 * 100 - SPREAD, -100.0), p0


def main():
    if _bs is None:
        return ["py_vollib 미설치"]
    v = norm(yf.Ticker("^VIX").history(period="26mo")[["Open", "Close"]].dropna())
    ch = (v["Open"] / v["Close"].shift(1) - 1) * 100
    vm = {str(pd.Timestamp(k).date()): float(x) for k, x in ch.dropna().items()}
    try:
        x = norm(yf.Ticker("^VXN").history(period="26mo")[["Open"]].dropna())
        ivm = {str(pd.Timestamp(k).date()): float(r) / 100 * K_MAIN for k, r in x["Open"].items()}
    except Exception:
        ivm = {}
    vix_open = {str(pd.Timestamp(k).date()): float(r) for k, r in v["Open"].items()}
    dd = norm(yf.download("QQQ", period="26mo", interval="1d", auto_adjust=False, progress=False))
    if isinstance(dd.columns, pd.MultiIndex):
        dd.columns = dd.columns.get_level_values(0)
    closes = {pd.Timestamp(k).date(): float(r) for k, r in dd["Close"].items()}
    dl = sorted(closes)
    prevc = {dl[i]: closes[dl[i - 1]] for i in range(1, len(dl))}

    df = load_1m()
    days = sorted(set(df.index.date))
    trades = []
    for d in days:
        pc = prevc.get(d)
        if pc is None:
            continue
        vx = vm.get(str(d))
        if vx is not None and abs(vx) >= 5.0:
            continue
        g = df[df.index.date == d]
        b1 = [(t, float(r["High"]), float(r["Low"]), float(r["Close"])) for t, r in g.iterrows()]
        b5 = agg(b1, 5)
        if len(b5) < 5:
            continue
        O0 = float(g["Open"].iloc[0])
        gap = O0 - pc
        gp = gap / pc * 100
        if not (0.2 <= abs(gp) < 1.5):
            continue
        sgn = 1 if gap > 0 else -1
        iv = ivm.get(str(d)) or (vix_open.get(str(d), 16.0) * 1.15 * K_MAIN / 100)
        ep = b5[2][3]                                    # 15m 기준선 = 09:45 종가
        cov = ((O0 - ep) / gap) if sgn > 0 else ((ep - O0) / abs(gap))
        if not (COVER_MIN <= cov < 1.0):
            continue
        t_entry = b5[2][0]
        tail = [x for x in b1 if x[0] > t_entry]
        r_, px, hold = run(tail, t_entry.time(), ep, pc, sgn, TRAIL)
        pl, p0 = opt_pl(sgn, ep, px, t_entry.time(), hold, iv)
        if pl is None:
            continue
        trades.append(dict(d=d, pl=pl, p0=p0, exit=r_))

    out = [f"dsize · 갭필 09:45 · 커버>={COVER_MIN} · 트레일 {TRAIL}% · 델타 {DELTA_TGT} 타겟",
           f"데이터 {len(days)}일 · 체결 {len(trades)}건 · IV=VXN시가x{K_MAIN} · 스프레드 {SPREAD}%", ""]

    def sim(trs, frac, integer):
        eq, peak, mdd = CAPITAL, CAPITAL, 0.0
        n_exec = 0
        for t in trs:
            alloc = eq * frac
            if integer:
                cost1 = t["p0"] * 100
                n = int(alloc // cost1)
                if n == 0:
                    continue
                prem = n * cost1
            else:
                prem = alloc
            eq += prem * t["pl"] / 100
            n_exec += 1
            peak = max(peak, eq)
            mdd = max(mdd, (peak - eq) / peak * 100)
            if eq <= 0:
                return 0.0, 100.0, n_exec
        return eq, mdd, n_exec

    segs = [("1년차 24-08~25-08", [t for t in trades if t["d"] <= Y1_END]),
            ("2년차 25-08~26-08", [t for t in trades if t["d"] > Y1_END]),
            ("2026 YTD 1~8월", [t for t in trades if t["d"] >= dt.date(2026, 1, 1)]),
            ("전체 26개월", trades)]
    for name, trs in segs:
        wins = sum(1 for t in trs if t["pl"] > 0)
        avg_p0 = sum(t["p0"] for t in trs) / max(len(trs), 1)
        out.append(f"[{name}] 신호 {len(trs)}건 · 승 {wins} · 평균프리미엄 ${avg_p0*100:.0f}/계약")
        out.append("  사이징 | 정수계약: 잔고 / MDD / 체결  || 분수(이상형): 잔고 / MDD")
        for f in SIZES:
            e1, m1, n1 = sim(trs, f, True)
            e2, m2, _ = sim(trs, f, False)
            out.append(f"   {int(f*100)}%   |  ${e1:>7.0f} / {m1:4.1f}% / {n1:>2}건  ||  ${e2:>7.0f} / {m2:4.1f}%")
        out.append("")
    out.append("거래별: " + ", ".join(f"{t['d']}({t['pl']:+.1f}%)" for t in trades))
    return out


if __name__ == "__main__":
    rep = main()
    json.dump({"at": dt.datetime.utcnow().isoformat(), "report": "\n".join(rep)},
              open("dsize_result.json", "w"), ensure_ascii=False, indent=1)
    print("\n".join(rep))
