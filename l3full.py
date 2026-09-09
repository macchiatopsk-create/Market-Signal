#!/usr/bin/env python3
"""
l3full · 3-LAYER 현행 라이브 스펙 그대로 백테스트 (수정 없음)
  L1 VIX9D/VIX3M 백분위 >= 50
  L2 프리마켓 위치 > 0.5   (시간외 1시간봉으로 프리마켓 고저 복원)
  L3 VWAP -1sigma 최초 터치 (09:45~14:30)
  진입 0.5% ITM 콜 (BSM) · 1회/일 · 스프레드 2.2%
  청산 TP1 VWAP 50% -> 러너 +1sigma · 손절 진입전 당일저점 · 14:30 컷
  L2 적용/미적용, 연도별 분해 함께 출력
"""
import datetime as dt, json, math
from statistics import NormalDist
import numpy as np, pandas as pd, yfinance as yf
from combsim import load_1m

RFR, SPREAD, ITM_PCT, KSIG = 0.043, 2.2, 0.5, 1.00
ENTRY_FROM, CUT = dt.time(9, 45), dt.time(14, 30)
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


def premarket_pos():
    """시간외 1시간봉으로 프리마켓 고저 복원 → 09:30 시가의 상대위치"""
    try:
        h = yf.download("QQQ", period="2y", interval="1h", prepost=True,
                        auto_adjust=False, progress=False)
        if isinstance(h.columns, pd.MultiIndex):
            h.columns = h.columns.get_level_values(0)
        h.index = pd.to_datetime(h.index)
        h.index = (h.index.tz_convert("America/New_York") if h.index.tz is not None
                   else h.index.tz_localize("UTC").tz_convert("America/New_York"))
        h.index = h.index.tz_localize(None)
    except Exception as e:
        print("프리마켓 조회 실패:", e)
        return {}
    out = {}
    for d, g in h.groupby(h.index.date):
        pm = g[(g.index.time >= dt.time(4, 0)) & (g.index.time < dt.time(9, 30))]
        rt = g[g.index.time >= dt.time(9, 30)]
        if len(pm) < 2 or len(rt) == 0:
            continue
        hi, lo = float(pm["High"].max()), float(pm["Low"].min())
        if hi <= lo:
            continue
        out[d] = (float(rt["Open"].iloc[0]) - lo) / (hi - lo)
    return out


def run(df, days, l1, ivm, vopen, pmm, use_l2):
    trades = []
    for d in days:
        p = l1.get(d)
        if p is not None and p < 50:
            continue
        if use_l2:
            pm = pmm.get(d)
            if pm is None or pm <= 0.5:
                continue
        g = df[df.index.date == d]
        if len(g) < 200:
            continue
        c, vol = g["Close"], g.get("Volume")
        tp = (g["High"] + g["Low"] + g["Close"]) / 3
        vwap = ((tp * vol).cumsum() / vol.cumsum()) if (vol is not None and float(vol.sum()) > 0) \
            else tp.expanding().mean()
        sig = (c - vwap).expanding().std().bfill()
        iv = ivm.get(d) or (vopen.get(d, 16.0) * 1.15 / 100)
        band = vwap - KSIG * sig
        hit = None
        for i in range(len(g)):
            t = g.index[i]
            if t.time() < ENTRY_FROM or t.time() >= CUT:
                continue
            if float(g["Low"].iloc[i]) <= float(band.iloc[i]):
                hit = i
                break
        if hit is None:
            continue
        t0, ep = g.index[hit], float(band.iloc[hit])
        K = round(ep * (1 - ITM_PCT / 100))
        tau0 = max(16.0 - (t0.hour + t0.minute / 60), 0.05) / 24 / 365
        p0 = bsm(ep, K, tau0, iv)
        if p0 <= 0.05:
            continue
        stop = float(g["Low"].iloc[:hit + 1].min())
        tail = g.iloc[hit + 1:]
        parts, ex = [], None
        for j in range(len(tail)):
            t = tail.index[j]
            hi, lo = float(tail["High"].iloc[j]), float(tail["Low"].iloc[j])
            vw = float(vwap.iloc[hit + 1 + j])
            up = vw + float(sig.iloc[hit + 1 + j])
            if lo <= stop:
                ex = (j, stop, "STOP")
                break
            if not parts and hi >= vw:
                parts.append((0.5, vw, t))
            if parts and hi >= up:
                ex = (j, up, "RUNNER")
                break
            if t.time() >= CUT:
                ex = (j, float(tail["Close"].iloc[j]), "CUT")
                break
        if ex is None:
            if not len(tail):
                continue
            ex = (len(tail) - 1, float(tail["Close"].iloc[-1]), "CUT")
        j, xp, why = ex
        tex = tail.index[j]
        tau1 = max(16.0 - (tex.hour + tex.minute / 60), 0.02) / 24 / 365
        tot = 0.0
        for w, px, tt in parts:
            tt1 = max(16.0 - (tt.hour + tt.minute / 60), 0.02) / 24 / 365
            tot += w * ((bsm(px, K, tt1, iv) - p0) / p0 * 100)
        tot += (1.0 - sum(w for w, _, _ in parts)) * ((bsm(xp, K, tau1, iv) - p0) / p0 * 100)
        trades.append(dict(d=d, pl=max(tot - SPREAD, -100.0), p0=p0, why=why,
                           pm=pmm.get(d)))
    return trades


def stats(a):
    if not a:
        return "0건"
    pl = [t["pl"] for t in a]
    w = [x for x in pl if x > 0]
    l = [-x for x in pl if x <= 0]
    pf = sum(w) / sum(l) if l else 99.9
    b = sorted(pl)[:-2] if len(pl) > 2 else pl
    w2 = [x for x in b if x > 0]
    l2 = [-x for x in b if x <= 0]
    pf2 = sum(w2) / sum(l2) if l2 else 99.9
    return (f"{len(pl):3d}건 · 승률 {len(w)/len(pl)*100:4.1f}% · 평균 {np.mean(pl):+6.1f}% · "
            f"PF {pf:4.2f} · 상위2외 {pf2:4.2f} · 최대손실 {min(pl):+6.1f}%")


def equity(a, frac):
    eq, peak, mdd, n = CAP0, CAP0, 0.0, 0
    for t in sorted(a, key=lambda x: x["d"]):
        cost = t["p0"] * 100
        k = int((eq * frac) // cost)
        if k == 0:
            continue
        eq += k * cost * t["pl"] / 100
        n += 1
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
        if eq <= 0:
            return 0.0, 100.0, n
    return eq, mdd, n


def main():
    df = load_1m()
    days = sorted(set(df.index.date))
    try:
        a = tzless(yf.Ticker("^VIX9D").history(period="5y")[["Close"]].dropna())
        b = tzless(yf.Ticker("^VIX3M").history(period="5y")[["Close"]].dropna())
        r = (a["Close"] / b.reindex(a.index)["Close"]).dropna()
        pc = r.rolling(252).apply(lambda x: (x[-1] >= x).mean() * 100, raw=True)
        l1 = {k.date(): float(v) for k, v in pc.dropna().items()}
    except Exception as e:
        l1 = {}
        print("L1 실패:", e)
    try:
        vx = tzless(yf.Ticker("^VXN").history(period="5y")[["Open"]].dropna())
        ivm = {k.date(): float(v) / 100 for k, v in vx["Open"].items()}
    except Exception:
        ivm = {}
    v = tzless(yf.Ticker("^VIX").history(period="5y")[["Open"]].dropna())
    vopen = {k.date(): float(x) for k, x in v["Open"].items()}
    pmm = premarket_pos()

    out = [f"l3full · 현행 스펙 그대로 · 1분 데이터 {len(days)}일",
           f"L1 백분위>=50 · L2 프리마켓>0.5 ({len(pmm)}일 복원) · L3 VWAP-{KSIG}σ",
           f"청산 TP1 VWAP 50% → 러너 +1σ · 손절 진입전저점 · 14:30컷 · 스프레드 {SPREAD}%", ""]

    full = run(df, days, l1, ivm, vopen, pmm, True)
    nol2 = run(df, days, l1, ivm, vopen, pmm, False)
    cov = [d for d in days if d in pmm]
    out.append(f"[현행 3층 전부]  {stats(full)}")
    out.append(f"[L2 없이 2층만]  {stats(nol2)}")
    out.append(f"  (프리마켓 복원 가능 구간: {min(cov) if cov else '-'} ~ {max(cov) if cov else '-'})")
    out.append("")
    if full:
        yrs = sorted({t['d'].year for t in full})
        for y in yrs:
            out.append(f"  {y}년  {stats([t for t in full if t['d'].year == y])}")
        out.append("")
        out.append(f"  청산 사유: " + ", ".join(
            f"{k} {sum(1 for t in full if t['why']==k)}건" for k in ("STOP", "RUNNER", "CUT")))
        out.append("")
        out.append(f"  사이징 시뮬 (시작 ${CAP0:.0f} · 정수계약)")
        for f in SIZES:
            e, m, n = equity(full, f)
            out.append(f"   {int(f*100)}%  최종 ${e:,.0f} · MDD {m:4.1f}% · 체결 {n}건")
    return out


if __name__ == "__main__":
    rep = main()
    print("\n".join(rep))
    json.dump({"at": dt.datetime.utcnow().isoformat(), "report": "\n".join(rep)},
              open("l3full_result.json", "w"), ensure_ascii=False, indent=1)
