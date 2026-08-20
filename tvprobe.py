"""TradingView 1분봉 접근 가능성 탐색 (Actions 러너에서).
tvdatafeed(익명 세션)로 QQQ 1분봉을 얼마나 과거까지 받을 수 있는지 확인하고,
yfinance 1분봉과 정확도를 대조한다.
"""
import json, subprocess, sys, datetime as dt, traceback
OUT = []


def log(s):
    print(s, flush=True)
    OUT.append(s)


def pipi(spec):
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                        "--no-cache-dir", spec],
                       capture_output=True, text=True, timeout=300)
    log(f"  pip {spec.split('/')[-1][:40]} rc={r.returncode} "
        f"{(r.stderr or '')[-160:].strip()}")
    return r.returncode == 0


def main():
    log("[설치]")
    ok = pipi("git+https://github.com/rongardF/tvdatafeed.git")
    if not ok:
        ok = pipi("tvdatafeed")
    if not ok:
        log("설치 실패 — 이 경로 불가")
        return OUT
    log("")

    try:
        from tvDatafeed import TvDatafeed, Interval
    except Exception:
        try:
            from tvdatafeed import TvDatafeed, Interval
        except Exception:
            log("import 실패:\n" + traceback.format_exc())
            return OUT

    log("[익명 세션 연결]")
    try:
        tv = TvDatafeed()
        log("  연결 OK")
    except Exception:
        log("  연결 실패:\n" + traceback.format_exc())
        return OUT
    log("")

    log("[1분봉 확보 한계 탐색]")
    best = None
    for n in (5000, 20000, 60000, 200000):
        try:
            df = tv.get_hist(symbol="QQQ", exchange="NASDAQ",
                             interval=Interval.in_1_minute, n_bars=n)
        except Exception as e:
            log(f"  n_bars={n:7d}  예외 {type(e).__name__}: {str(e)[:90]}")
            continue
        if df is None or len(df) == 0:
            log(f"  n_bars={n:7d}  결과 없음")
            continue
        span = (df.index[-1] - df.index[0]).days
        log(f"  n_bars={n:7d}  받음 {len(df):7,d}봉  "
            f"{df.index[0]} ~ {df.index[-1]}  ({span}일치)")
        best = df
    log("")

    if best is None:
        log("1분봉 확보 실패")
        return OUT

    log("[정확도 대조 — yfinance 1분봉]")
    try:
        import pandas as pd, yfinance as yf
        y = yf.download("QQQ", period="5d", interval="1m", prepost=False,
                        auto_adjust=False, progress=False)
        if isinstance(y.columns, pd.MultiIndex):
            y.columns = y.columns.get_level_values(0)
        y = y.dropna()
        y.index = y.index.tz_convert("America/New_York").tz_localize(None)

        t = best.copy()
        t.index = pd.to_datetime(t.index)
        # tvdatafeed 인덱스는 보통 거래소 시각(ET). 봉 라벨 규약 차이를 양쪽으로 시험
        for shift, lab in ((0, "라벨 그대로"), (1, "1분 시프트")):
            tt = t.copy()
            tt.index = tt.index + pd.Timedelta(minutes=shift)
            j = y.join(tt, how="inner", lsuffix="_y", rsuffix="_t")
            if len(j) < 30:
                log(f"  {lab}: 교집합 {len(j)}봉 — 비교 불가")
                continue
            d = (j["close"].astype(float) - j["Close_y"].astype(float))
            dl = (j["low"].astype(float) - j["Low_y"].astype(float))
            log(f"  {lab}: 교집합 {len(j)}봉  종가 절대평균 {d.abs().mean():.4f} "
                f"상관 {j['close'].astype(float).corr(j['Close_y'].astype(float)):.6f}  "
                f"저가 절대평균 {dl.abs().mean():.4f}")
    except Exception:
        log("  대조 실패:\n" + traceback.format_exc())

    log("")
    log("판정: 1분봉이 2년(약 500거래일 = 195,000봉)까지 나오고 상관 0.999+ 면 채택")
    return OUT


if __name__ == "__main__":
    try:
        main()
    except Exception:
        OUT.append("실패:\n" + traceback.format_exc())
        print(OUT[-1])
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
               "report": "\n".join(OUT)},
              open("tvprobe_result.json", "w"), ensure_ascii=False, indent=1)
