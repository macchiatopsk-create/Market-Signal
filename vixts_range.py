"""
0DTE용: 전날 종가 VIX 기간구조가 '다음날 얼마나 움직일지'를 예측하는가.

방향이 아니라 크기를 잰다 (우리 신호는 방향 ρ≈0, 크기 ρ=0.42).
핵심 이점: 기간구조는 전날 종가에 확정 -> 장 열기 전에 안다.
반면 밴드폭은 10:30까지 기다려야 나온다.

측정
  1) 전날 TS 백분위 -> 다음날 장중 레인지 (고-저)/시가 %   [3년 일봉]
  2) 버킷별 레인지 분포 + 반반검증
  3) 스피어만 상관
  4) '큰 날'(레인지 상위 1/3) 적중률: 전날 밤에 골라낼 수 있는가
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

LOOKBACK = 252


def wilson(k, n):
    if n == 0: return (0.0, 0.0)
    p, z = k / n, 1.96; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h) * 100, 1), round(min(1, c + h) * 100, 1))


def spearman(a, b):
    ra, rb = pd.Series(a).rank(), pd.Series(b).rank()
    return float(ra.corr(rb))


def norm(s):
    s = s.copy()
    try: s.index = s.index.tz_localize(None)
    except (TypeError, AttributeError): pass
    s.index = pd.to_datetime(s.index).normalize()
    return s[~s.index.duplicated(keep="last")]


def main():
    out = []
    vt = {}
    for tk in ("^VIX", "^VIX9D", "^VIX3M"):
        vt[tk] = norm(yf.Ticker(tk).history(period="3y")["Close"].dropna())

    for tk in ("SPY", "QQQ"):
        h = yf.Ticker(tk).history(period="3y")[["Open", "High", "Low", "Close"]].dropna()
        try: h.index = h.index.tz_localize(None)
        except (TypeError, AttributeError): pass
        h.index = pd.to_datetime(h.index).normalize()
        h = h[~h.index.duplicated(keep="last")]
        # 당일 장중 레인지 (%)
        h["rng"] = (h["High"] - h["Low"]) / h["Open"] * 100

        for s_tk, l_tk in (("^VIX", "^VIX3M"), ("^VIX9D", "^VIX3M")):
            ts = (vt[s_tk].reindex(h.index).ffill() / vt[l_tk].reindex(h.index).ffill())
            df = pd.DataFrame({"ts": ts, "rng": h["rng"]})

            def _pct(w):
                if len(w) < 2: return float("nan")
                return float((w[:-1] < w[-1]).sum()) / (len(w) - 1) * 100
            df["pct"] = df["ts"].rolling(LOOKBACK).apply(_pct, raw=True)
            # 전날 기간구조 -> 다음날 레인지 (shift 로 미래참조 차단)
            df["pct_prev"] = df["pct"].shift(1)
            d = df.dropna(subset=["pct_prev", "rng"]).copy()
            if len(d) < 150: continue

            out.append(f"\n{'='*72}\n[{tk}]  전날 {s_tk}/{l_tk} 백분위  ->  다음날 장중 레인지\n{'='*72}")
            out.append(f"  표본 {len(d)}일  전체 레인지 중앙값 {d['rng'].median():.3f}%  평균 {d['rng'].mean():.3f}%")
            rho = spearman(d["pct_prev"], d["rng"])
            out.append(f"  스피어만 상관 (전날 백분위 vs 다음날 레인지) = {rho:+.3f}")

            big_cut = d["rng"].quantile(2/3)
            out.append(f"  '큰 날' 기준 = 레인지 상위 1/3 = {big_cut:.3f}% 이상")
            half = d.index[len(d)//2]

            buckets = [("하위20 (콘탱고깊음)", d["pct_prev"] < 20),
                       ("중간 20-80",          (d["pct_prev"] >= 20) & (d["pct_prev"] < 80)),
                       ("상위20 (스트레스)",    d["pct_prev"] >= 80)]
            out.append(f"\n  {'구간':22s} {'n':>4s}  레인지중앙  큰날비율        (반반검증)")
            for lab, m in buckets:
                ss = d[m]
                if len(ss) < 20:
                    out.append(f"  {lab:22s} {len(ss):4d}  표본부족"); continue
                k = int((ss["rng"] >= big_cut).sum()); ci = wilson(k, len(ss))
                f1 = ss[ss.index < half]; f2 = ss[ss.index >= half]
                r1 = (f1["rng"] >= big_cut).mean()*100 if len(f1) >= 20 else float("nan")
                r2 = (f2["rng"] >= big_cut).mean()*100 if len(f2) >= 20 else float("nan")
                out.append(f"  {lab:22s} {len(ss):4d}  {ss['rng'].median():8.3f}%  "
                           f"{k/len(ss)*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f})  "
                           f"전반 {r1:5.1f}% / 후반 {r2:5.1f}%")

            # 연도별
            d["yr"] = d.index.year
            out.append(f"\n  [연도별 하위20 구간의 다음날 레인지 중앙값]")
            for yr, g in d.groupby("yr"):
                lo = g[g["pct_prev"] < 20]; hi = g[g["pct_prev"] >= 80]
                s1 = f"{lo['rng'].median():.3f}%" if len(lo) >= 5 else "n/a"
                s2 = f"{hi['rng'].median():.3f}%" if len(hi) >= 5 else "n/a"
                out.append(f"     {yr}  하위20 {len(lo):3d}일 중앙 {s1:>8s}   |  "
                           f"상위20 {len(hi):3d}일 중앙 {s2:>8s}   |  전체 중앙 {g['rng'].median():.3f}%")
    return out


if __name__ == "__main__":
    try: rep = main()
    except Exception: rep = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(rep); print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("vixts_range.json", "w"), ensure_ascii=False, indent=1)
