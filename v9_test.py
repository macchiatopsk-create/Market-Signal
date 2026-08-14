"""
v9 — 상위 2층만 501거래일(2년, 1시간봉)로 확정 검증

  1층 크기 : 전날 VIX9D/VIX3M 백분위
  2층 방향 : 프리마켓(04:00~09:30) 위치 -> 그날 편향
             pos = (09:30시가 - PM저) / (PM고 - PM저)

3층(VWAP 타이밍)은 15분봉이 60일밖에 안 나오므로 여기서 제외.
타이밍은 진입가를 다듬는 층이지 엣지의 원천이 아니므로, 상위 2층이
진짜인지부터 확정한다.

측정: 편향 방향으로 당일 보유했을 때의 성과 (09:30 -> 종가)
      + 반반검증 + 연도별 + 상위2건 제외 PF
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd


def wilson(k, n):
    if n == 0: return (0.0, 0.0)
    p, z = k / n, 1.96; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h) * 100, 1), round(min(1, c + h) * 100, 1))


def _n(x):
    try: x.index = x.index.tz_localize(None)
    except (TypeError, AttributeError): pass
    x.index = pd.to_datetime(x.index).normalize()
    return x[~x.index.duplicated(keep="last")]


def vix_pct_map():
    a = _n(yf.Ticker("^VIX9D").history(period="3y")["Close"].dropna())
    b = _n(yf.Ticker("^VIX3M").history(period="3y")["Close"].dropna())
    ts = (a / b.reindex(a.index).ffill()).dropna()
    def _p(w):
        if len(w) < 2: return float("nan")
        return float((w[:-1] < w[-1]).sum()) / (len(w) - 1) * 100
    return {str(d.date()): float(v)
            for d, v in ts.rolling(252).apply(_p, raw=True).shift(1).dropna().items()}


def build(tk):
    df = yf.download(tk, period="2y", interval="1h", prepost=True,
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    df.index = df.index.tz_convert("America/New_York")

    rows = []
    for d in sorted(set(df.index.date)):
        g = df[df.index.date == d]
        pm = g[(g.index.time >= dt.time(4, 0)) & (g.index.time < dt.time(9, 30))]
        rt = g[(g.index.time >= dt.time(9, 30)) & (g.index.time < dt.time(16, 0))]
        if len(pm) < 3 or len(rt) < 5: continue
        pmh, pml = float(pm["High"].max()), float(pm["Low"].min())
        if pmh <= pml: continue
        op = float(rt["Open"].iloc[0]); cl = float(rt["Close"].iloc[-1])
        hi = float(rt["High"].max()); lo = float(rt["Low"].min())
        pos = (op - pml) / (pmh - pml)
        rows.append(dict(d=str(d), pos=pos, ret=(cl / op - 1) * 100,
                         rng=(hi - lo) / op * 100,
                         mfe_up=(hi / op - 1) * 100, mfe_dn=(1 - lo / op) * 100))
    return rows


def rep(tr, lab, out):
    n = len(tr)
    if n < 15:
        out.append(f"  {lab:32s} n={n:4d} 표본부족"); return
    w = sum(1 for t in tr if t["pnl"] > 0); ci = wilson(w, n)
    g = sum(t["pnl"] for t in tr if t["pnl"] > 0); l = -sum(t["pnl"] for t in tr if t["pnl"] <= 0)
    s2 = sorted(tr, key=lambda x: -x["pnl"])[2:]
    g2 = sum(t["pnl"] for t in s2 if t["pnl"] > 0); l2 = -sum(t["pnl"] for t in s2 if t["pnl"] <= 0)
    ds = sorted(t["d"] for t in tr); half = ds[len(ds)//2]
    def _pf(x):
        a = sum(t["pnl"] for t in x if t["pnl"] > 0); b = -sum(t["pnl"] for t in x if t["pnl"] <= 0)
        return (a/b) if b > 0 else 99.0
    nl = sum(1 for t in tr if t["dir"] > 0)
    out.append(f"  {lab:32s} n={n:4d}(롱{nl:3d}/숏{n-nl:3d}) 승률 {w/n*100:5.1f}% "
               f"CI({ci[0]:4.1f}~{ci[1]:4.1f}) PF {g/l if l else 99:5.2f} "
               f"|상위2제외 {g2/l2 if l2 else 99:5.2f} 평균 {sum(t['pnl'] for t in tr)/n:+.4f}% "
               f"| 반반 {_pf([t for t in tr if t['d']<half]):.2f}/{_pf([t for t in tr if t['d']>=half]):.2f}")


def main():
    out = []
    vmap = vix_pct_map()
    out.append(f"VIX 백분위 맵 {len(vmap)}일")
    for tk in ("SPY", "QQQ"):
        rows = build(tk)
        out.append(f"\n{'='*104}\n[{tk}] 1시간봉 2년 · {len(rows)}거래일 "
                   f"({rows[0]['d']}~{rows[-1]['d']}) · 09:30 진입 종가 청산\n{'='*104}")
        base = [dict(d=r["d"], dir=1, pnl=r["ret"]) for r in rows]
        rep(base, "무조건 롱 (벤치마크)", out)

        for vmin, vlab in ((0, "VIX 무관"), (33, "VIX≥33%"), (50, "VIX≥50%"), (67, "VIX≥67%")):
            sel = [r for r in rows if vmin == 0 or vmap.get(r["d"], -1) >= vmin]
            tr = [dict(d=r["d"], dir=(1 if r["pos"] > 0.5 else -1),
                       pnl=(r["ret"] if r["pos"] > 0.5 else -r["ret"])) for r in sel]
            rep(tr, f"{vlab} · 프리마켓 방향", out)

        out.append("  --- 프리마켓 위치 강도별 (VIX≥50%) ---")
        sel = [r for r in rows if vmap.get(r["d"], -1) >= 50]
        for lo_, hi_, lab in ((0.5, 1.01, "pos>0.5 롱"), (0.7, 1.01, "pos>0.7 강한롱"),
                              (1.0, 9.9, "pos>1.0 PM고점돌파"),
                              (-9.9, 0.5, "pos<0.5 숏"), (-9.9, 0.3, "pos<0.3 강한숏"),
                              (-9.9, 0.0, "pos<0.0 PM저점이탈")):
            ss = [r for r in sel if lo_ <= r["pos"] < hi_] if lo_ > -9 else [r for r in sel if r["pos"] < hi_]
            sgn = 1 if lo_ > -9 else -1
            tr = [dict(d=r["d"], dir=sgn, pnl=(r["ret"] * sgn)) for r in ss]
            rep(tr, lab, out)

        out.append("  --- 연도별 (VIX≥50% · 프리마켓 방향) ---")
        byyr = {}
        for r in sel:
            sgn = 1 if r["pos"] > 0.5 else -1
            byyr.setdefault(r["d"][:4], []).append(dict(d=r["d"], dir=sgn, pnl=r["ret"]*sgn))
        for yr in sorted(byyr): rep(byyr[yr], yr, out)
    return out


if __name__ == "__main__":
    try: r = main()
    except Exception: r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r); print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("v9_result.json", "w"), ensure_ascii=False, indent=1)
