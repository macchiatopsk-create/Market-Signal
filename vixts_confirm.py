"""
VIX 기간구조 확정 검증.
  1) 반반검증 (헌법 3조): 3년을 전반/후반으로 나눠 양쪽에서 같은 방향이 나오는가
  2) 연도별 분해: 특정 시기에만 몰린 착시인지
  3) 하위20%(콘탱고 깊음) 구간이 언제 발생했는지 분포
  4) 다른 만기 조합(9일/1개월/3개월/6개월)도 같이 비교
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

LOOKBACK = 252
HOLD = 5          # 앞선 측정에서 가장 강했던 구간


def wilson(k, n):
    if n == 0: return (0.0, 0.0)
    p, z = k / n, 1.96; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h) * 100, 1), round(min(1, c + h) * 100, 1))


def norm(s):
    s = s.copy()
    try: s.index = s.index.tz_localize(None)
    except (TypeError, AttributeError): pass
    s.index = pd.to_datetime(s.index).normalize()
    return s[~s.index.duplicated(keep="last")]


def stats(s):
    n = len(s)
    if n < 20: return None
    w = int((s > 0).sum()); ci = wilson(w, n)
    g = s[s > 0].sum(); l = -s[s <= 0].sum()
    srt = s.sort_values(ascending=False)
    g2 = srt[2:][srt[2:] > 0].sum(); l2 = -srt[2:][srt[2:] <= 0].sum()
    return dict(n=n, avg=s.mean(), win=w/n*100, ci=ci,
                pf=(g/l if l > 0 else 99), pf2=(g2/l2 if l2 > 0 else 99))


def line(lab, st):
    if st is None: return f"     {lab:22s} 표본부족"
    return (f"     {lab:22s} n={st['n']:4d} 평균 {st['avg']:+.3f}% 승률 {st['win']:5.1f}% "
            f"CI({st['ci'][0]:4.1f}~{st['ci'][1]:4.1f}) PF {st['pf']:5.2f} |상위2제외 {st['pf2']:5.2f}")


def main():
    out = []
    tks = {}
    for tk in ("^VIX", "^VIX9D", "^VIX3M", "^VIX6M"):
        try: tks[tk] = norm(yf.Ticker(tk).history(period="3y")["Close"].dropna())
        except Exception: pass
    px = {t: norm(yf.Ticker(t).history(period="3y")["Close"].dropna()) for t in ("SPY", "QQQ")}
    idx = px["SPY"].index

    PAIRS = [("^VIX", "^VIX3M"), ("^VIX9D", "^VIX3M"), ("^VIX", "^VIX6M"), ("^VIX9D", "^VIX")]
    for short_tk, long_tk in PAIRS:
        if short_tk not in tks or long_tk not in tks: continue
        ts = (tks[short_tk].reindex(idx).ffill() / tks[long_tk].reindex(idx).ffill()).dropna()
        df = pd.DataFrame({"ts": ts})

        def _pct(w):
            if len(w) < 2: return float("nan")
            return float((w[:-1] < w[-1]).sum()) / (len(w) - 1) * 100
        df["pct"] = df["ts"].rolling(LOOKBACK).apply(_pct, raw=True)
        for t in ("SPY", "QQQ"):
            p = px[t].reindex(df.index).ffill()
            df[t] = (p.shift(-HOLD) / p - 1) * 100
        d = df.dropna(subset=["pct"]).copy()
        if len(d) < 100: continue

        out.append(f"\n{'='*70}\n기간구조 = {short_tk} / {long_tk}   (홀드 {HOLD}일)\n{'='*70}")
        half = d.index[len(d)//2]
        out.append(f"  표본 {len(d)}일  전반~{half.date()}  후반{half.date()}~")

        for t in ("SPY", "QQQ"):
            sub = d.dropna(subset=[t])
            out.append(f"\n  [{t}]  전체 " + (line("", stats(sub[t])) or "").strip())
            buckets = [("하위20 (콘탱고깊음)", sub["pct"] < 20),
                       ("중간 20-80",          (sub["pct"] >= 20) & (sub["pct"] < 80)),
                       ("상위20 (스트레스)",    sub["pct"] >= 80)]
            for lab, m in buckets:
                whole = stats(sub[m][t])
                first = stats(sub[m & (sub.index < half)][t])
                second = stats(sub[m & (sub.index >= half)][t])
                out.append(line(lab + " 전체", whole))
                out.append(line("   └ 전반", first))
                out.append(line("   └ 후반", second))

        # 연도별 + 하위20 발생 분포
        out.append(f"\n  [연도별 하위20 발생일수 / 그해 거래일]")
        d["yr"] = d.index.year
        for yr, g in d.groupby("yr"):
            lo = (g["pct"] < 20).sum()
            sp = g[g["pct"] < 20]["SPY"].dropna()
            avg = f"{sp.mean():+.3f}%" if len(sp) >= 5 else "n/a"
            out.append(f"     {yr}  {lo:3d}/{len(g):3d}일 ({lo/len(g)*100:4.1f}%)  그 구간 SPY {HOLD}일 평균 {avg}")
    return out


if __name__ == "__main__":
    try: rep = main()
    except Exception: rep = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(rep); print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("vixts_confirm.json", "w"), ensure_ascii=False, indent=1)
