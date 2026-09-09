#!/usr/bin/env python3
"""
l3sig · 3-LAYER의 L3(VWAP -1σ 터치) 문턱 완화 사다리
  L1 VIX9D/VIX3M 백분위>=50 적용 · L2(프리마켓)는 정규장 데이터라 미적용 → 상대비교용
  진입: 09:45 이후 VWAP - k*sigma 최초 터치 (k = 0.00 ~ 1.50)
  옵션: 0.5% ITM 콜 BSM · 스프레드 2.2% 차감
  청산: TP1 VWAP 도달 시 50% · 러너 +1sigma · 손절 진입전 당일저점 · 14:30 컷
"""
import datetime as dt, json, math
from statistics import NormalDist
import numpy as np, pandas as pd, yfinance as yf
from combsim import load_1m

KS = [0.00, 0.25, 0.50, 0.75, 1.00, 1.25, 1.50]
RFR, SPREAD, ITM_PCT = 0.043, 2.2, 0.5
ENTRY_FROM, CUT = dt.time(9, 45), dt.time(14, 30)


def bsm(flag, S, K, tau, iv):
    if tau <= 0 or iv <= 0:
        return max(0.0, (S - K) if flag == "c" else (K - S))
    nd = NormalDist()
    d1 = (math.log(S / K) + (RFR + iv * iv / 2) * tau) / (iv * math.sqrt(tau))
    d2 = d1 - iv * math.sqrt(tau)
    if flag == "c":
        return S * nd.cdf(d1) - K * math.exp(-RFR * tau) * nd.cdf(d2)
    return K * math.exp(-RFR * tau) * nd.cdf(-d2) - S * nd.cdf(-d1)


def norm(d):
    d.index = pd.to_datetime(d.index).tz_localize(None)
    return d


def main():
    df = load_1m()
    days = sorted(set(df.index.date))
    # L1: VIX9D/VIX3M 비율의 1년 롤링 백분위
    try:
        a = norm(yf.Ticker("^VIX9D").history(period="5y")[["Close"]].dropna())
        b = norm(yf.Ticker("^VIX3M").history(period="5y")[["Close"]].dropna())
        r = (a["Close"] / b.reindex(a.index)["Close"]).dropna()
        pct = r.rolling(252).apply(lambda x: (x[-1] >= x).mean() * 100, raw=True)
        l1 = {k.date(): float(v) for k, v in pct.dropna().items()}
    except Exception as e:
        l1 = {}
        print("L1 조회 실패:", e)
    try:
        vx = norm(yf.Ticker("^VXN").history(period="5y")[["Open"]].dropna())
        ivm = {k.date(): float(v) / 100 for k, v in vx["Open"].items()}
    except Exception:
        ivm = {}
    v = norm(yf.Ticker("^VIX").history(period="5y")[["Open"]].dropna())
    vopen = {k.date(): float(x) for k, x in v["Open"].items()}

    res = {k: [] for k in KS}
    gated = 0
    for d in days:
        p = l1.get(d)
        if p is not None and p < 50:
            continue                      # L1 게이트
        gated += 1
        g = df[df.index.date == d]
        if len(g) < 200:
            continue
        c, vol = g["Close"], g.get("Volume")
        tp = (g["High"] + g["Low"] + g["Close"]) / 3
        if vol is None or float(vol.sum()) <= 0:
            vwap = tp.expanding().mean()
        else:
            vwap = (tp * vol).cumsum() / vol.cumsum()
        dev = (c - vwap)
        sig = dev.expanding().std().bfill()
        iv = ivm.get(d) or (vopen.get(d, 16.0) * 1.15 / 100)

        for k in KS:
            band = vwap - k * sig
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
            t0 = g.index[hit]
            ep = float(band.iloc[hit])
            K = round(ep * (1 - ITM_PCT / 100))
            h0 = t0.hour + t0.minute / 60
            tau0 = max(16.0 - h0, 0.05) / 24 / 365
            p0 = bsm("c", ep, K, tau0, iv)
            if p0 <= 0.05:
                continue
            stop = float(g["Low"].iloc[:hit + 1].min())
            tail = g.iloc[hit + 1:]
            half_done, pl_parts = False, []
            exit_i = None
            for j in range(len(tail)):
                t = tail.index[j]
                hi, lo = float(tail["High"].iloc[j]), float(tail["Low"].iloc[j])
                vw = float(vwap.iloc[hit + 1 + j])
                up = vw + float(sig.iloc[hit + 1 + j])
                if lo <= stop:
                    exit_i, xp = j, stop
                    break
                if not half_done and hi >= vw:
                    half_done = True
                    pl_parts.append((0.5, vw, t))
                if half_done and hi >= up:
                    exit_i, xp = j, up
                    break
                if t.time() >= CUT:
                    exit_i, xp = j, float(tail["Close"].iloc[j])
                    break
            if exit_i is None:
                if len(tail) == 0:
                    continue
                exit_i, xp = len(tail) - 1, float(tail["Close"].iloc[-1])
            tex = tail.index[exit_i]
            hx = tex.hour + tex.minute / 60
            tau1 = max(16.0 - hx, 0.02) / 24 / 365
            tot = 0.0
            for w, px, tt in pl_parts:
                hh = tt.hour + tt.minute / 60
                tt1 = max(16.0 - hh, 0.02) / 24 / 365
                pe = bsm("c", px, K, tt1, iv)
                tot += w * ((pe - p0) / p0 * 100)
            wrem = 1.0 - sum(w for w, _, _ in pl_parts)
            pe = bsm("c", xp, K, tau1, iv)
            tot += wrem * ((pe - p0) / p0 * 100)
            res[k].append(max(tot - SPREAD, -100.0))

    out = [f"l3sig · 데이터 {len(days)}일 · L1 통과 {gated}일 (L2 미적용)",
           "진입 09:45~ VWAP-kσ 최초터치 · TP1 VWAP 50% · 러너 +1σ · 손절 진입전저점 · 14:30컷", ""]
    out.append(" k(σ)   거래   승률    평균     PF    상위2외   최대손실")
    for k in KS:
        a = res[k]
        if not a:
            out.append(f" {k:4.2f}    0건 — 신호 없음")
            continue
        w = [x for x in a if x > 0]
        l = [-x for x in a if x <= 0]
        pf = (sum(w) / sum(l)) if l else 99.9
        b = sorted(a)[:-2] if len(a) > 2 else a
        w2 = [x for x in b if x > 0]
        l2 = [-x for x in b if x <= 0]
        pf2 = (sum(w2) / sum(l2)) if l2 else 99.9
        out.append(f" {k:4.2f}  {len(a):4d}건  {len(w)/len(a)*100:4.1f}%  "
                   f"{np.mean(a):+6.1f}%  {pf:5.2f}   {pf2:5.2f}   {min(a):+6.1f}%")
    out.append("")
    out.append("k=1.00 이 라이브 현행 · k 낮출수록 완화(거래 증가), 높일수록 엄격")
    return out


if __name__ == "__main__":
    rep = main()
    print("\n".join(rep))
    json.dump({"at": dt.datetime.utcnow().isoformat(), "report": "\n".join(rep)},
              open("l3sig_result.json", "w"), ensure_ascii=False, indent=1)
