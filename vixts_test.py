"""
VIX 기간구조 검증.
  1) 어떤 티커가 실제로 수집되는지 진단 (^VIX3M 수집 실패 원인)
  2) 기간구조 = VIX / VIX3M 을 레짐 분류기로 쓸 수 있는지 측정

검증 기준 (오늘 합의):
  - look-ahead 금지: 임계값을 전체 표본에서 뽑지 않고, 그날까지의 rolling 백분위만 사용
  - 상위 1~2건 제외 PF 병기
  - 거래일 수 명시
  - CI가 겹치면 예측력 없음으로 기각 (F&G 때와 동일 기준)
"""
import json, math, datetime as dt
import yfinance as yf
import pandas as pd

CANDIDATES = ["^VIX", "^VIX3M", "^VIX9D", "^VXV", "^VIX6M", "^MOVE", "^VVIX"]
LOOKBACK = 252          # rolling 백분위 창
HORIZONS = [1, 3, 5]    # 다음 N일 수익률


def wilson(k, n):
    if n == 0: return (0.0, 0.0)
    p, z = k / n, 1.96; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h) * 100, 1), round(min(1, c + h) * 100, 1))


def diagnose():
    out = ["===== 티커 수집 진단 ====="]
    got = {}
    for tk in CANDIDATES:
        try:
            s = yf.Ticker(tk).history(period="3y")["Close"].dropna()
            if len(s) == 0:
                out.append(f"  {tk:9s} 빈 시리즈")
            else:
                out.append(f"  {tk:9s} OK  n={len(s):4d}  {s.index[0].date()}~{s.index[-1].date()}  최근 {s.iloc[-1]:.2f}")
                got[tk] = s
        except Exception as e:
            out.append(f"  {tk:9s} 실패 {type(e).__name__}: {e}")
    return out, got


def backtest(got):
    out = []
    if "^VIX" not in got:
        return out + ["  ^VIX 자체가 없어 백테스트 불가"]
    # 3개월물 후보 중 잡히는 것 사용
    long_tk = next((t for t in ("^VIX3M", "^VXV", "^VIX6M") if t in got), None)
    if long_tk is None:
        return out + ["  3개월물(^VIX3M/^VXV/^VIX6M) 전부 수집 실패 -> 기간구조 계산 불가"]
    out.append(f"\n===== 기간구조 = ^VIX / {long_tk} =====")

    def norm(s):
        s = s.copy()
        try: s.index = s.index.tz_localize(None)
        except (TypeError, AttributeError): pass
        s.index = pd.to_datetime(s.index).normalize()
        return s[~s.index.duplicated(keep="last")]

    px = {}
    for tk in ("SPY", "QQQ"):
        px[tk] = norm(yf.Ticker(tk).history(period="3y")["Close"].dropna())
    idx = px["SPY"].index
    vix = norm(got["^VIX"]).reindex(idx).ffill()
    vl = norm(got[long_tk]).reindex(idx).ffill()
    ts = (vix / vl).dropna()
    out.append(f"  [진단] SPY {len(idx)}일 / VIX 정합 {vix.notna().sum()} / "
               f"{long_tk} 정합 {vl.notna().sum()} / 기간구조 {len(ts)}")
    if len(ts) < LOOKBACK + 60:
        return out + [f"  기간구조 표본 {len(ts)} 부족 (필요 {LOOKBACK+60})"]

    df = pd.DataFrame({"ts": ts})
    # look-ahead 없는 rolling 백분위: 오늘 값이 과거 LOOKBACK 안에서 몇 %인지
    def _pct(w):
        if len(w) < 2: return float("nan")
        return float((w[:-1] < w[-1]).sum()) / (len(w) - 1) * 100
    df["pct"] = df["ts"].rolling(LOOKBACK).apply(_pct, raw=True)
    df["backw"] = (df["ts"] > 1.0).astype(int)      # 백워데이션 여부

    for tk in ("SPY", "QQQ"):
        p = px[tk].reindex(df.index).ffill()
        for h in HORIZONS:
            df[f"{tk}_r{h}"] = (p.shift(-h) / p - 1) * 100

    d = df.dropna(subset=["pct"]).copy()
    if len(d) == 0:
        return out + ["  rolling 백분위가 전부 NaN -> 계산 불가"]
    out.append(f"  표본 {len(d)}일  ({d.index[0].date()}~{d.index[-1].date()})")
    out.append(f"  기간구조 값: 중앙 {d['ts'].median():.3f}  백워데이션(>1.0) {d['backw'].mean()*100:.1f}%")

    for tk in ("SPY", "QQQ"):
        for h in HORIZONS:
            col = f"{tk}_r{h}"
            sub = d.dropna(subset=[col])
            base = sub[col]
            bw, bci = (base > 0).mean() * 100, wilson(int((base > 0).sum()), len(base))
            out.append(f"\n  [{tk}] 다음 {h}일 수익 — 전체 n={len(base)} "
                       f"평균 {base.mean():+.3f}% 승률 {bw:.1f}% CI({bci[0]}~{bci[1]})")
            for lab, m in (("백분위 0-20 (구조 안정)", sub["pct"] < 20),
                           ("백분위 20-80",            (sub["pct"] >= 20) & (sub["pct"] < 80)),
                           ("백분위 80-100 (스트레스)", sub["pct"] >= 80),
                           ("백워데이션(ts>1.0)",       sub["backw"] == 1)):
                s = sub[m][col]
                if len(s) < 20:
                    out.append(f"     {lab:24s} n={len(s):4d} 표본부족"); continue
                w = int((s > 0).sum()); ci = wilson(w, len(s))
                g = s[s > 0].sum(); l = -s[s <= 0].sum()
                srt = s.sort_values(ascending=False)
                g2 = srt[2:][srt[2:] > 0].sum(); l2 = -srt[2:][srt[2:] <= 0].sum()
                out.append(f"     {lab:24s} n={len(s):4d} 평균 {s.mean():+.3f}% "
                           f"승률 {w/len(s)*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
                           f"PF {g/l if l else 99:5.2f} |상위2제외 {g2/l2 if l2 else 99:5.2f}")
    return out


if __name__ == "__main__":
    rep, got = diagnose()
    try:
        rep += backtest(got)
    except Exception as e:
        rep.append(f"백테스트 실패: {type(e).__name__}: {e}")
    txt = "\n".join(rep)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("vixts_result.json", "w"), ensure_ascii=False, indent=1)
