#!/usr/bin/env python3
"""
rsit · RSI+BB 존 상태의 '하루 예측력' 즉결 심판
  1분 RSI14 → EMA20±2σ 밴드, 중립지대 ±0.1
  워밍업 후 첫 30분(10:05~10:35) 존 상태 → 나머지(10:35~14:59) 움직임 예측?
  피처×타깃 스피어만 ρ · 기준선 = 개장VIX 레벨
"""
import datetime as dt, json
import numpy as np, pandas as pd, yfinance as yf
from combsim import load_1m
from scipy.stats import spearmanr


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / (dn + 1e-12))


def main():
    df = load_1m()
    v = yf.Ticker("^VIX").history(period="5y")[["Open"]].dropna()
    vix = {str(pd.Timestamp(k).date()): float(r) for k, r in v["Open"].items()}
    rows = []
    for d in sorted(set(df.index.date)):
        g = df[df.index.date == d]
        if len(g) < 300:
            continue
        c = g["Close"]
        r = rsi(c)
        basis = r.ewm(span=20, adjust=False).mean()
        dev = 2 * r.rolling(20).std()
        du, dl = basis + (2 * dev) * 0.1 / 2, basis - (2 * dev) * 0.1 / 2
        # 워밍업 35봉 → 피처창 = 35~65봉(10:05~10:35), 타깃창 = 65봉~끝
        f, t = slice(35, 65), slice(65, None)
        rf, duf, dlf = r.iloc[f], du.iloc[f], dl.iloc[f]
        green = (rf > duf).mean()
        red = (rf < dlf).mean()
        cross = int(((rf > duf) != (rf > duf).shift()).sum())
        drift_f = abs(c.iloc[64] / c.iloc[35] - 1) * 100
        ct = c.iloc[t]
        rng = (ct.max() - ct.min()) / ct.iloc[0] * 100
        drift_t = abs(ct.iloc[-1] / ct.iloc[0] - 1) * 100
        path = ct.diff().abs().sum()
        persist = abs(ct.iloc[-1] - ct.iloc[0]) / (path + 1e-9)
        rows.append(dict(d=str(d), green=green, red=red, onesided=max(green, red),
                         cross=cross, drift_f=drift_f, vix=vix.get(str(d), np.nan),
                         rng=rng, drift_t=drift_t, persist=persist))
    x = pd.DataFrame(rows).dropna()
    out = [f"rsit · n={len(x)}일 (1분 데이터 보유일 전체)", ""]
    feats = ["onesided", "green", "red", "cross", "drift_f", "vix"]
    tgts = ["rng", "drift_t", "persist"]
    out.append("피처\\타깃      잔여레인지  잔여드리프트  추세지속성")
    for ft in feats:
        line = f"  {ft:10s}"
        for tg in tgts:
            rho, p = spearmanr(x[ft], x[tg])
            line += f"  ρ{rho:+.2f}{'**' if p < 0.01 else '* ' if p < 0.05 else '  '}"
        out.append(line)
    out.append("")
    out.append("기준선 vix = 개장VIX 단독 · drift_f = 순수 가격드리프트(지표 없이)")
    out.append("판정: |ρ|가 vix·drift_f 기준선을 유의미하게 넘어야 지표의 부가가치")
    return out


if __name__ == "__main__":
    rep = main()
    json.dump({"at": dt.datetime.utcnow().isoformat(), "report": "\n".join(rep)},
              open("rsit_result.json", "w"), ensure_ascii=False, indent=1)
    print("\n".join(rep))
