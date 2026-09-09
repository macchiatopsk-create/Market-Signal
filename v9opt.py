#!/usr/bin/env python3
"""
v9opt · v9 원형(2층 · 주식 시가→종가)을 0DTE ITM 콜로 환산
  L1 VIX9D/VIX3M 백분위>=50 · L2 프리마켓 위치>0.5 → 09:30 시가에 0.5% ITM 콜 매수
  청산 A: 만기(16:00) = 내재가치 확정 (모델 무관)
  청산 B: 14:30 · 청산 C: 11:30  (1시간봉 시가, BSM 잔여시간 평가)
  손절 없음 · 스프레드 2.2% · 유니버스 = v9와 동일(1시간봉 2년, 전 거래일)
"""
import datetime as dt, json, math
from statistics import NormalDist
import numpy as np, pandas as pd, yfinance as yf

RFR, SPREAD, ITM_PCT = 0.043, 2.2, 0.5
CAP0, SIZES = 3000.0, [0.30, 0.40, 0.50, 0.60, 0.70]


def bsm(S, K, tau, iv):
    if tau <= 0 or iv <= 0:
        return max(0.0, S - K)
    nd = NormalDist()
    d1 = (math.log(S / K) + (RFR + iv * iv / 2) * tau) / (iv * math.sqrt(tau))
    return S * nd.cdf(d1) - K * math.exp(-RFR * tau) * nd.cdf(d1 - iv * math.sqrt(tau))


def tzless(d):
    d.index = pd.to_datetime(d.index).tz_localize(None)
    return d


def stats(pl):
    if not pl:
        return "0건"
    w = [x for x in pl if x > 0]
    l = [-x for x in pl if x <= 0]
    pf = sum(w) / sum(l) if l else 99.9
    b = sorted(pl)[:-2] if len(pl) > 2 else pl
    w2 = [x for x in b if x > 0]
    l2 = [-x for x in b if x <= 0]
    pf2 = sum(w2) / sum(l2) if l2 else 99.9
    return (f"{len(pl):3d}건 · 승률 {len(w)/len(pl)*100:4.1f}% · 평균 {np.mean(pl):+6.1f}% · "
            f"PF {pf:4.2f} · 상위2외 {pf2:4.2f} · 최악 {min(pl):+6.1f}%")


def equity(rows, frac):
    eq, peak, mdd, n = CAP0, CAP0, 0.0, 0
    for r in rows:
        cost = r["p0"] * 100
        k = int((eq * frac) // cost)
        if k == 0:
            continue
        eq += k * cost * r["pl"] / 100
        n += 1
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
    return eq, mdd, n


def main():
    h = yf.download("QQQ", period="2y", interval="1h", prepost=True,
                    auto_adjust=False, progress=False)
    if isinstance(h.columns, pd.MultiIndex):
        h.columns = h.columns.get_level_values(0)
    h.index = pd.to_datetime(h.index)
    h.index = (h.index.tz_convert("America/New_York") if h.index.tz is not None
               else h.index.tz_localize("UTC").tz_convert("America/New_York")).tz_localize(None)
    dd = yf.download("QQQ", period="2y", interval="1d", auto_adjust=False, progress=False)
    if isinstance(dd.columns, pd.MultiIndex):
        dd.columns = dd.columns.get_level_values(0)
    dclose = {pd.Timestamp(k).date(): float(v) for k, v in dd["Close"].items()}

    a = tzless(yf.Ticker("^VIX9D").history(period="5y")[["Close"]].dropna())
    b = tzless(yf.Ticker("^VIX3M").history(period="5y")[["Close"]].dropna())
    r = (a["Close"] / b.reindex(a.index)["Close"]).dropna()
    pc = r.rolling(252).apply(lambda x: (x[-1] >= x).mean() * 100, raw=True).shift(1)
    l1 = {k.date(): float(v) for k, v in pc.dropna().items()}
    try:
        vx = tzless(yf.Ticker("^VXN").history(period="5y")[["Open"]].dropna())
        ivm = {k.date(): float(v) / 100 for k, v in vx["Open"].items()}
    except Exception:
        ivm = {}
    v = tzless(yf.Ticker("^VIX").history(period="5y")[["Open"]].dropna())
    vopen = {k.date(): float(x) for k, x in v["Open"].items()}

    rows = []
    for d, g in h.groupby(h.index.date):
        pm = g[(g.index.time >= dt.time(4, 0)) & (g.index.time < dt.time(9, 30))]
        rt = g[(g.index.time >= dt.time(9, 30)) & (g.index.time < dt.time(16, 0))]
        if len(pm) < 3 or len(rt) < 5 or d not in dclose:
            continue
        pmh, pml = float(pm["High"].max()), float(pm["Low"].min())
        if pmh <= pml:
            continue
        pos = (float(rt["Open"].iloc[0]) - pml) / (pmh - pml)
        p = l1.get(d)
        if p is None or p < 50 or pos <= 0.5:
            continue
        op = float(rt["Open"].iloc[0])
        cl = dclose[d]
        iv = ivm.get(d) or (vopen.get(d, 16.0) * 1.15 / 100)
        K = round(op * (1 - ITM_PCT / 100))
        p0 = bsm(op, K, 6.5 / 24 / 365, iv)
        if p0 <= 0.05:
            continue
        def at(tm, hrs_left):
            bar = rt[rt.index.time == tm]
            if not len(bar):
                return None
            px = float(bar["Open"].iloc[0])
            return max((bsm(px, K, hrs_left / 24 / 365, iv) - p0) / p0 * 100 - SPREAD, -100.0)
        rows.append(dict(d=d, p0=p0, stock=(cl / op - 1) * 100,
                         A=max((max(cl - K, 0) - p0) / p0 * 100 - SPREAD, -100.0),
                         B=at(dt.time(14, 30), 1.5), C=at(dt.time(11, 30), 4.5)))

    rows.sort(key=lambda x: x["d"])
    out = [f"v9opt · 유니버스 {len(set(h.index.date))}거래일 · 2층 통과 {len(rows)}일",
           f"09:30 시가 0.5% ITM 콜 매수 · 손절 없음 · 스프레드 {SPREAD}% · IV=VXN시가", ""]
    out.append(f"[주식 원형 재현]  {stats([r['stock'] for r in rows])}")
    out.append(f"[옵션 A 만기청산]  {stats([r['A'] for r in rows])}")
    out.append(f"[옵션 B 14:30컷]  {stats([r['B'] for r in rows if r['B'] is not None])}")
    out.append(f"[옵션 C 11:30컷]  {stats([r['C'] for r in rows if r['C'] is not None])}")
    out.append("")
    for y in sorted({r["d"].year for r in rows}):
        ys = [r for r in rows if r["d"].year == y]
        out.append(f"  {y}년 주식 {stats([r['stock'] for r in ys])}")
        out.append(f"        옵션A {stats([r['A'] for r in ys])}")
    out.append("")
    out.append(f"  사이징 시뮬 · 옵션 A · 시작 ${CAP0:.0f} · 정수계약")
    for f in SIZES:
        e, m, n = equity([dict(p0=r["p0"], pl=r["A"]) for r in rows], f)
        out.append(f"   {int(f*100)}%  최종 ${e:,.0f} · MDD {m:4.1f}% · 체결 {n}건")
    out.append("")
    out.append("  [민감도] 옵션 B(14:30컷) · IV배수 × 스프레드  → PF / 상위2외 / 승률 / 평균")
    for ivm_ in (1.0, 1.5, 2.0, 2.5):
        for sp in (2.2, 5.0):
            pls = []
            for r in rows:
                iv = (ivm.get(r["d"]) or (vopen.get(r["d"], 16.0) * 1.15 / 100)) * ivm_
                g = h[h.index.date == r["d"]]
                rt = g[(g.index.time >= dt.time(9, 30)) & (g.index.time < dt.time(16, 0))]
                op = float(rt["Open"].iloc[0]); K = round(op * (1 - ITM_PCT / 100))
                p0 = bsm(op, K, 6.5 / 24 / 365, iv)
                bar = rt[rt.index.time == dt.time(14, 30)]
                if not len(bar) or p0 <= 0.05:
                    continue
                px = float(bar["Open"].iloc[0])
                pls.append(max((bsm(px, K, 1.5 / 24 / 365, iv) - p0) / p0 * 100 - sp, -100.0))
            w = [x for x in pls if x > 0]; l = [-x for x in pls if x <= 0]
            b = sorted(pls)[:-2]; w2 = [x for x in b if x > 0]; l2 = [-x for x in b if x <= 0]
            pf = sum(w) / sum(l) if l else 99; pf2 = sum(w2) / sum(l2) if l2 else 99
            out.append(f"   IV×{ivm_:.1f} 스프레드{sp:.1f}%  →  {pf:4.2f} / {pf2:4.2f} / "
                       f"{len(w)/len(pls)*100:4.1f}% / {np.mean(pls):+6.1f}%  (평균프리미엄 계산 포함)")
    out.append("")
    out.append(f"  평균 진입 프리미엄 ${np.mean([r['p0'] for r in rows]):.2f}/주 · "
               f"평균 기초 수익 {np.mean([r['stock'] for r in rows]):+.3f}%")
    return out


if __name__ == "__main__":
    rep = main()
    print("\n".join(rep))
    json.dump({"at": dt.datetime.utcnow().isoformat(), "report": "\n".join(rep)},
              open("v9opt_result.json", "w"), ensure_ascii=False, indent=1)
