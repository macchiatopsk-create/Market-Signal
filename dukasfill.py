"""Dukascopy QQQ 틱 → 1분봉 백필러.
실행할 때마다 '아직 없는 가장 오래된 달' 하나를 채운다. 반복 dispatch로 2년치 누적.

저장   data/1m/QQQ_YYYY-MM.csv.gz  (ET 타임스탬프, 정규장 09:30~15:59)
무결성 각 거래일의 1분봉 집계 OHLC를 yfinance 일봉과 대조해 괴리 보고
안정성 503 대비 지수 백오프 재시도 + 요청 간 딜레이
"""
import os, json, lzma, struct, time, gzip, datetime as dt, traceback
import urllib.request, urllib.error
import pandas as pd
import numpy as np
import yfinance as yf

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126 Safari/537.36"}
INST = "QQQUSUSD"
SCALE = 1000.0
DATA = "data/1m"
MONTHS_BACK = 24
HOURS = range(13, 21)          # UTC 13~20 = ET 09~16 (여름). 정규장만
DELAY = 0.25
RETRY = 3
DAYS_PER_RUN = 12              # 한 실행당 처리 거래일 수 (타임아웃 여유)
BUDGET_SEC = 1500              # 25분 넘으면 그때까지 받은 것 저장하고 종료
OUT = []


def log(s):
    print(s, flush=True)
    OUT.append(s)


def fetch_hour(day, h):
    url = (f"https://datafeed.dukascopy.com/datafeed/{INST}/"
           f"{day.year}/{day.month-1:02d}/{day.day:02d}/{h:02d}h_ticks.bi5")
    wait = 1.0
    for attempt in range(RETRY):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""                      # 그 시간대 데이터 없음 (정상)
            if e.code in (429, 503, 502, 500) and attempt < RETRY - 1:
                time.sleep(wait)
                wait *= 2.2
                continue
            return None
        except Exception:
            if attempt < RETRY - 1:
                time.sleep(wait)
                wait *= 2.2
                continue
            return None
    return None


def decode_bi5(raw):
    if not raw:
        return []
    data = None
    for fmt in (lzma.FORMAT_ALONE, lzma.FORMAT_AUTO):
        try:
            data = lzma.LZMADecompressor(format=fmt).decompress(raw)
            break
        except Exception:
            continue
    if data is None:
        try:
            data = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(
                raw[:5] + b"\xff" * 8 + raw[13:])
        except Exception:
            return []
    n = len(data) // 20
    return [struct.unpack(">iiiff", data[i*20:(i+1)*20]) for i in range(n)]


def day_bars(day):
    rows, fails = [], 0
    for h in HOURS:
        raw = fetch_hour(day, h)
        if raw is None:
            fails += 1
            continue
        base = dt.datetime.combine(day, dt.time(h), tzinfo=dt.timezone.utc)
        for (ms, ask, bid, av, bv) in decode_bi5(raw):
            rows.append((base + dt.timedelta(milliseconds=ms),
                         (ask + bid) / 2.0 / SCALE, av + bv))
        time.sleep(DELAY)
    if not rows:
        return pd.DataFrame(), fails
    df = pd.DataFrame(rows, columns=["t", "px", "vol"]).set_index("t").sort_index()
    g = df.resample("1min")
    out = pd.DataFrame({"Open": g["px"].first(), "High": g["px"].max(),
                        "Low": g["px"].min(), "Close": g["px"].last(),
                        "Volume": g["vol"].sum()}).dropna()
    out.index = out.index.tz_convert("America/New_York")
    out = out[(out.index.time >= dt.time(9, 30)) & (out.index.time < dt.time(16, 0))]
    return out.round(4), fails


def load_done():
    """이미 저장된 거래일 집합."""
    if not os.path.isdir(DATA):
        return set()
    out = set()
    for f in os.listdir(DATA):
        if f.startswith("QQQ_") and f.endswith(".csv.gz"):
            try:
                d = pd.read_csv(f"{DATA}/{f}", compression="gzip", usecols=["ts"])
                out |= set(pd.to_datetime(d["ts"]).dt.date.unique())
            except Exception:
                pass
    return out


def save_month(yy, mm, frames):
    """해당 월 파일에 병합 저장 (중복 제거)."""
    path = f"{DATA}/QQQ_{yy}-{mm:02d}.csv.gz"
    new = pd.concat(frames, ignore_index=True)
    if os.path.exists(path):
        old = pd.read_csv(path, compression="gzip")
        new = pd.concat([old, new], ignore_index=True)
    new = new.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    new.to_csv(path, index=False, compression="gzip")
    return path, len(new), os.path.getsize(path)


def main():
    os.makedirs(DATA, exist_ok=True)
    t0 = time.time()
    today = dt.date.today()
    start = today - dt.timedelta(days=MONTHS_BACK * 31)

    dd = yf.download("QQQ", start=start.isoformat(), end=today.isoformat(),
                     interval="1d", auto_adjust=False, progress=False)
    if isinstance(dd.columns, pd.MultiIndex):
        dd.columns = dd.columns.get_level_values(0)
    dd = dd.dropna()
    try:
        dd.index = dd.index.tz_localize(None)
    except Exception:
        pass
    daily = {pd.Timestamp(x).date(): dd.loc[x] for x in dd.index}
    alldays = sorted(daily.keys())

    done = load_done()
    todo = [d for d in alldays if d not in done]
    log(f"전체 거래일 {len(alldays)}  확보 {len(done)}  남음 {len(todo)}")
    if not todo:
        log("백필 완료. 남은 작업 없음")
        return OUT
    # 최신부터 채운다 (최근 데이터가 먼저 쓸모 있으므로)
    todo = sorted(todo, reverse=True)[:DAYS_PER_RUN]
    log(f"이번 실행 대상 {len(todo)}일: {todo[-1]} ~ {todo[0]}")
    log("")

    bucket, report, got = {}, [], 0
    for d in sorted(todo):
        if time.time() - t0 > BUDGET_SEC:
            log(f"  [시간예산 초과 — {d} 이후 중단]")
            break
        bars, fails = day_bars(d)
        if len(bars) == 0:
            report.append(f"  {d}  틱없음 (실패 {fails})")
            continue
        ref = daily.get(d)
        chk = ""
        if ref is not None:
            rc, rh, rl = float(ref["Close"]), float(ref["High"]), float(ref["Low"])
            dc = float(bars["Close"].iloc[-1])
            dh, dl = float(bars["High"].max()), float(bars["Low"].min())
            chk = f"종가차 {dc-rc:+.3f} 고가차 {dh-rh:+.3f} 저가차 {dl-rl:+.3f}"
            if abs(dc - rc) > 0.5 or abs(dh - rh) > 0.8 or abs(dl - rl) > 0.8:
                chk += " WARN"
        report.append(f"  {d}  봉 {len(bars):3d}  실패 {fails}  {chk}")
        b = bars.copy()
        b.insert(0, "ts", b.index.strftime("%Y-%m-%d %H:%M"))
        bucket.setdefault((d.year, d.month), []).append(b.reset_index(drop=True))
        got += 1

    for (yy, mm), fr in bucket.items():
        path, n, sz = save_month(yy, mm, fr)
        log(f"저장 {path}  누적 {n:,d}봉  {sz/1024:.0f}KB")
    log("")
    for r in report:
        log(r)
    log("")
    remain = len(todo) and (len(alldays) - len(done) - got)
    log(f"이번 실행 {got}일 확보 · 소요 {time.time()-t0:.0f}초 · 남은 거래일 약 {remain}일")
    return OUT


if __name__ == "__main__":
    try:
        main()
    except Exception:
        OUT.append("실패:\n" + traceback.format_exc())
        print(OUT[-1])
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
               "report": "\n".join(OUT)},
              open("dukasfill_result.json", "w"), ensure_ascii=False, indent=1)
