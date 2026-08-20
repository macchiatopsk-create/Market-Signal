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
HOURS = range(12, 22)          # UTC. DST 양쪽 커버 (ET 07~17시)
DELAY = 0.6
RETRY = 4
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


def main():
    os.makedirs(DATA, exist_ok=True)
    today = dt.date.today()

    # 대상 월 = 최근 24개월 중 파일이 없는 가장 오래된 달
    months = []
    y, m = today.year, today.month
    for _ in range(MONTHS_BACK):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    target = None
    for (yy, mm) in months:
        if not os.path.exists(f"{DATA}/QQQ_{yy}-{mm:02d}.csv.gz"):
            target = (yy, mm)
            break
    if target is None:
        log(f"최근 {MONTHS_BACK}개월 모두 확보 완료. 남은 작업 없음")
        have = sorted(os.listdir(DATA))
        log(f"보유 파일 {len(have)}개: {have[0]} ~ {have[-1]}")
        return OUT

    yy, mm = target
    log(f"대상 월: {yy}-{mm:02d}")

    # 해당 월 거래일 (yfinance 일봉 기준 = 실제 개장일)
    s = dt.date(yy, mm, 1)
    e = (dt.date(yy + (mm == 12), (mm % 12) + 1, 1))
    dd = yf.download("QQQ", start=s.isoformat(), end=e.isoformat(),
                     interval="1d", auto_adjust=False, progress=False)
    if isinstance(dd.columns, pd.MultiIndex):
        dd.columns = dd.columns.get_level_values(0)
    dd = dd.dropna()
    try:
        dd.index = dd.index.tz_localize(None)
    except Exception:
        pass
    tdays = [pd.Timestamp(x).date() for x in dd.index]
    log(f"거래일 {len(tdays)}일: {tdays[0] if tdays else '-'} ~ {tdays[-1] if tdays else '-'}")
    log("")

    frames, report = [], []
    t0 = time.time()
    for d in tdays:
        bars, fails = day_bars(d)
        if len(bars) == 0:
            report.append(f"  {d}  틱없음 (실패시간대 {fails})")
            continue
        ref = dd.loc[dd.index.date == d] if hasattr(dd.index, "date") else None
        chk = ""
        if ref is not None and len(ref):
            rc = float(ref["Close"].iloc[0]); rh = float(ref["High"].iloc[0])
            rl = float(ref["Low"].iloc[0])
            dc = float(bars["Close"].iloc[-1]); dh = float(bars["High"].max())
            dl = float(bars["Low"].min())
            chk = (f"종가차 {dc-rc:+.3f}  고가차 {dh-rh:+.3f}  저가차 {dl-rl:+.3f}")
            if abs(dc - rc) > 0.5 or abs(dh - rh) > 0.8 or abs(dl - rl) > 0.8:
                chk += "  ⚠괴리"
        report.append(f"  {d}  봉 {len(bars):3d}  실패 {fails}  {chk}")
        bars = bars.copy()
        bars.insert(0, "ts", bars.index.strftime("%Y-%m-%d %H:%M"))
        frames.append(bars.reset_index(drop=True))

    if not frames:
        log("수집 실패 — 파일 생성 안 함")
        for r in report:
            log(r)
        return OUT

    allb = pd.concat(frames, ignore_index=True)
    path = f"{DATA}/QQQ_{yy}-{mm:02d}.csv.gz"
    allb.to_csv(path, index=False, compression="gzip")
    sz = os.path.getsize(path)
    log(f"저장 {path}  {len(allb):,d}봉  {sz/1024:.0f}KB  "
        f"소요 {time.time()-t0:.0f}초")
    log("")
    for r in report:
        log(r)
    log("")
    done = len([f for f in os.listdir(DATA) if f.endswith('.csv.gz')])
    log(f"진행: {done}/{MONTHS_BACK}개월 확보")
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
