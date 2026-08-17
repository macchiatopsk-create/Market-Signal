"""
갭 필 검증.

가설: 첫봉이 갭의 절반 이상을 되돌리면(갭업이면 음봉, 갭다운이면 양봉)
      그날 갭이 메워질(전날 종가 터치) 확률이 높다.

정의
  gap      = open - prev_close                      (갭업 gap>0 / 갭다운 gap<0)
  cover_c  = 첫봉 종가 기준 되돌림 비율 = (open - c1)/gap   [갭업]
             갭다운이면 (c1 - open)/|gap|
  cover_l  = 첫봉 극점 기준 (갭업: (open-low1)/gap, 갭다운: (high1-open)/|gap|)
  filled   = 당일 정규장 중 전날 종가 터치 (갭업: low <= prev_close)

측정: 조건 충족군 vs 베이스라인(전체 갭일) 갭필률 비교 + 반반검증 + 갭크기별
      첫봉 크기 5분/15분(60일) · 1시간(2년, 표본 8배)
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

TICKERS = ("QQQ", "SPY")
COVER_MIN = 0.5


def wilson(k, n):
    if n == 0: return (0.0, 0.0)
    p, z = k / n, 1.96; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(max(0, c - h) * 100, 1), round(min(1, c + h) * 100, 1))


def build(tk, interval, period):
    df = yf.download(tk, period=period, interval=interval, prepost=False,
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    df.index = df.index.tz_convert("America/New_York")
    df = df[(df.index.time >= dt.time(9, 30)) & (df.index.time < dt.time(16, 0))]

    rows = []; prev_close = None
    for d in sorted(set(df.index.date)):
        g = df[df.index.date == d]
        if len(g) < 4:
            if len(g): prev_close = float(g["Close"].iloc[-1])
            continue
        op = float(g["Open"].iloc[0])
        o1, h1, l1, c1 = (float(g["Open"].iloc[0]), float(g["High"].iloc[0]),
                          float(g["Low"].iloc[0]), float(g["Close"].iloc[0]))
        day_hi = float(g["High"].max()); day_lo = float(g["Low"].min())
        cl = float(g["Close"].iloc[-1])
        if prev_close:
            gap = op - prev_close
            gp = gap / prev_close * 100
            if abs(gp) >= 0.05:                       # 무시할 수준의 갭 제외
                if gap > 0:
                    cover_c = (op - c1) / gap
                    cover_l = (op - l1) / gap
                    filled = day_lo <= prev_close
                    # 갭필 시점까지 추가 하락폭(최대 되돌림)
                    beyond = (prev_close - day_lo) / prev_close * 100
                else:
                    cover_c = (c1 - op) / abs(gap)
                    cover_l = (h1 - op) / abs(gap)
                    filled = day_hi >= prev_close
                    beyond = (day_hi - prev_close) / prev_close * 100
                rows.append(dict(d=str(d), dir=(1 if gap > 0 else -1), gp=round(gp, 3),
                                 cover_c=round(cover_c, 3), cover_l=round(cover_l, 3),
                                 filled=bool(filled), beyond=round(beyond, 3),
                                 ret=round((cl / op - 1) * 100, 3)))
        prev_close = cl
    return rows


def rep(rows, lab, out, indent="    "):
    n = len(rows)
    if n < 10:
        out.append(f"{indent}{lab:34s} n={n:4d} 표본부족"); return
    k = sum(1 for r in rows if r["filled"]); ci = wilson(k, n)
    ds = sorted(r["d"] for r in rows); half = ds[len(ds) // 2]
    f1 = [r for r in rows if r["d"] < half]; f2 = [r for r in rows if r["d"] >= half]
    r1 = sum(1 for r in f1 if r["filled"]) / len(f1) * 100 if f1 else 0
    r2 = sum(1 for r in f2 if r["filled"]) / len(f2) * 100 if f2 else 0
    out.append(f"{indent}{lab:34s} n={n:4d} 갭필 {k/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
               f"반반 {r1:5.1f}/{r2:5.1f} | 평균갭 {sum(abs(r['gp']) for r in rows)/n:.2f}%")


def analyze(tk, rows, lab, out):
    out.append(f"\n{'='*96}\n[{tk}] 첫봉 = {lab} · 표본 {len(rows)}일\n{'='*96}")
    for sgn, nm in ((1, "갭업"), (-1, "갭다운")):
        ss = [r for r in rows if r["dir"] == sgn]
        if len(ss) < 10:
            out.append(f"  {nm}: 표본부족 ({len(ss)})"); continue
        out.append(f"  ── {nm} (n={len(ss)}) ──")
        rep(ss, "전체 (베이스라인)", out)
        for key, kn in (("cover_c", "종가기준"), ("cover_l", "극점기준")):
            hit = [r for r in ss if r[key] >= COVER_MIN]
            mis = [r for r in ss if r[key] < COVER_MIN]
            rep(hit, f"{kn} 커버 ≥50% (조건충족)", out)
            rep(mis, f"{kn} 커버 <50%", out)
        # 커버 강도별
        out.append("    -- 종가기준 커버 구간별 --")
        for lo, hi in ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0), (1.0, 99)):
            bb = [r for r in ss if lo <= r["cover_c"] < hi]
            rep(bb, f"커버 {lo:.2f}~{hi if hi < 9 else 999:.2f}", out, indent="      ")
        # 갭 크기별 (조건 충족군)
        out.append("    -- 갭 크기별 (종가기준 커버≥50%) --")
        hit = [r for r in ss if r["cover_c"] >= COVER_MIN]
        for lo, hi in ((0.05, 0.2), (0.2, 0.4), (0.4, 99)):
            bb = [r for r in hit if lo <= abs(r["gp"]) < hi]
            rep(bb, f"갭 {lo:.2f}~{hi if hi < 9 else 999:.1f}%", out, indent="      ")


def main():
    out = []
    cfgs = [("5m", "60d", "5분봉"), ("15m", "60d", "15분봉"), ("1h", "2y", "1시간봉")]
    for tk in TICKERS:
        for iv, per, lab in cfgs:
            try:
                rows = build(tk, iv, per)
                if rows: analyze(tk, rows, lab, out)
                else: out.append(f"[{tk}/{lab}] 데이터 없음")
            except Exception as e:
                out.append(f"[{tk}/{lab}] 실패 {type(e).__name__}: {e}")
    return out


if __name__ == "__main__":
    try: r = main()
    except Exception: r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r); print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("gapfill_result.json", "w"), ensure_ascii=False, indent=1)
