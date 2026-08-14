"""
프리마켓 위치 게이트 재검증.

정의 (이전 검증에서 확정된 것):
  프리마켓(04:00~09:30) 고-저 레인지 안에서 09:30 시가의 위치
  pos = (open - pm_low) / (pm_high - pm_low)
  pos > 0.5 -> 강세 편향(롱만),  pos < 0.5 -> 약세 편향(숏만)

전략: 오프닝 레인지 돌파 (게이트를 붙였던 원래 전략)
  OR = 09:30~10:00 고저
  OR 상단 돌파 -> 롱 / 하단 이탈 -> 숏
  게이트 ON 이면 프리마켓 위치와 방향이 일치할 때만 진입

이전 검증: PF 1.31 -> 1.51, 승률 55% -> 58% (약 100일, 5개월 중 2개월 마이너스)
이번 목적: 더 긴 표본 + 반반검증 + 상위건 제외 PF + VIX 게이트 결합
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

OR_MIN = 30                       # 오프닝 레인지 길이(분)
CUT = dt.time(14, 0)              # 진입 마감
EXIT = dt.time(15, 45)            # 강제 청산
STOP_R = 1.0                      # OR 폭의 배수로 손절
TP_R = 1.5                        # OR 폭의 배수로 익절


def wilson(k, n):
    if n == 0: return (0.0, 0.0)
    p, z = k / n, 1.96; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h) * 100, 1), round(min(1, c + h) * 100, 1))


def vix_dead():
    try:
        def _n(x):
            try: x.index = x.index.tz_localize(None)
            except (TypeError, AttributeError): pass
            x.index = pd.to_datetime(x.index).normalize()
            return x[~x.index.duplicated(keep="last")]
        a = _n(yf.Ticker("^VIX9D").history(period="2y")["Close"].dropna())
        b = _n(yf.Ticker("^VIX3M").history(period="2y")["Close"].dropna())
        ts = (a / b.reindex(a.index).ffill()).dropna()
        def _p(w):
            if len(w) < 2: return float("nan")
            return float((w[:-1] < w[-1]).sum()) / (len(w) - 1) * 100
        pct = ts.rolling(252).apply(_p, raw=True).shift(1)
        return set(str(d.date()) for d in pct[pct < 20].index)
    except Exception as e:
        print("VIX 실패", e); return set()


def run(tk, gate=True, vix=False, dead=frozenset()):
    df = yf.download(tk, period="60d", interval="5m", prepost=True,
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    try: df.index = df.index.tz_convert("America/New_York")
    except Exception: pass

    trades = []
    for d in sorted(set(df.index.date)):
        if vix and str(d) in dead: continue
        g = df[df.index.date == d]
        pm = g[(g.index.time >= dt.time(4, 0)) & (g.index.time < dt.time(9, 30))]
        rt = g[(g.index.time >= dt.time(9, 30)) & (g.index.time < dt.time(16, 0))]
        if len(pm) < 6 or len(rt) < 60: continue

        pmh, pml = float(pm["High"].max()), float(pm["Low"].min())
        if pmh <= pml: continue
        op = float(rt["Open"].iloc[0])
        pos = (op - pml) / (pmh - pml)          # 프리마켓 위치
        bias = 1 if pos > 0.5 else -1

        nb = OR_MIN // 5
        orh = float(rt["High"].iloc[:nb].max()); orl = float(rt["Low"].iloc[:nb].min())
        w = orh - orl
        if w <= 0: continue

        H = [float(x) for x in rt["High"]]; L = [float(x) for x in rt["Low"]]
        C = [float(x) for x in rt["Close"]]; O = [float(x) for x in rt["Open"]]
        T = [t.time() for t in rt.index]

        pos_open = None
        for i in range(nb, len(C)):
            if pos_open is None:
                if T[i] >= CUT: break
                sig = 1 if H[i] >= orh else (-1 if L[i] <= orl else 0)
                if sig == 0: continue
                if gate and sig != bias: continue
                ep = orh if sig > 0 else orl
                pos_open = dict(dir=sig, ep=ep, i=i, pos=round(pos, 3))
            else:
                s = pos_open["dir"]; ep = pos_open["ep"]
                tp = ep + TP_R * w * s
                sl = ep - STOP_R * w * s
                hit_tp = (H[i] >= tp) if s > 0 else (L[i] <= tp)
                hit_sl = (L[i] <= sl) if s > 0 else (H[i] >= sl)
                if hit_sl:   px, r = sl, "STOP"
                elif hit_tp: px, r = tp, "TP"
                elif T[i] >= EXIT: px, r = O[i], "TIME"
                else: continue
                pnl = ((px / ep - 1) * 100) * s
                trades.append(dict(d=str(d), dir=s, pos=pos_open["pos"], reason=r,
                                   pnl=round(pnl, 4), bars=i - pos_open["i"]))
                pos_open = None
                break                       # 하루 1회
        if pos_open is not None:
            s = pos_open["dir"]; ep = pos_open["ep"]
            pnl = ((C[-1] / ep - 1) * 100) * s
            trades.append(dict(d=str(d), dir=s, pos=pos_open["pos"], reason="EOD",
                               pnl=round(pnl, 4), bars=len(C) - 1 - pos_open["i"]))
    return trades


def rep(tr, lab, out):
    n = len(tr)
    if n < 10:
        out.append(f"  {lab:28s} n={n} 표본부족"); return
    w = sum(1 for t in tr if t["pnl"] > 0); ci = wilson(w, n)
    g = sum(t["pnl"] for t in tr if t["pnl"] > 0); l = -sum(t["pnl"] for t in tr if t["pnl"] <= 0)
    s2 = sorted(tr, key=lambda x: -x["pnl"])[2:]
    g2 = sum(t["pnl"] for t in s2 if t["pnl"] > 0); l2 = -sum(t["pnl"] for t in s2 if t["pnl"] <= 0)
    ds = sorted(set(t["d"] for t in tr)); half = ds[len(ds)//2]
    f1 = [t for t in tr if t["d"] < half]; f2 = [t for t in tr if t["d"] >= half]
    def _pf(x):
        a = sum(t["pnl"] for t in x if t["pnl"] > 0); b = -sum(t["pnl"] for t in x if t["pnl"] <= 0)
        return (a/b) if b > 0 else 99.0
    rc = {}
    for t in tr: rc[t["reason"]] = rc.get(t["reason"], 0) + 1
    out.append(f"  {lab:28s} n={n:3d}/{len(ds):2d}일 승률 {w/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
               f"PF {g/l if l else 99:5.2f} |상위2제외 {g2/l2 if l2 else 99:5.2f} "
               f"합계 {sum(t['pnl'] for t in tr):+6.2f}% | 반반 {_pf(f1):.2f}/{_pf(f2):.2f} "
               f"| {'/'.join(f'{k}{v}' for k, v in sorted(rc.items()))}")


def main():
    out = []
    dead = vix_dead()
    out.append(f"VIX DEAD 판정일 {len(dead)}일 확보")
    for tk in ("SPY", "QQQ"):
        out.append(f"\n{'='*100}\n[{tk}] 오프닝레인지 {OR_MIN}분 돌파 · TP {TP_R}R / SL {STOP_R}R · 진입마감 {CUT} · 청산 {EXIT}\n{'='*100}")
        rep(run(tk, gate=False), "게이트 없음 (베이스라인)", out)
        rep(run(tk, gate=True),  "프리마켓 위치 게이트", out)
        rep(run(tk, gate=False, vix=True, dead=dead), "VIX 게이트만", out)
        rep(run(tk, gate=True,  vix=True, dead=dead), "프리마켓 + VIX 둘 다", out)
    return out


if __name__ == "__main__":
    try: r = main()
    except Exception: r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r); print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("premarket_result.json", "w"), ensure_ascii=False, indent=1)
