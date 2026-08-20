"""Dukascopy QQQ CFD 틱 → 1분봉 변환 및 yfinance 1분봉 대조 검증.
백필 전에 반드시 통과해야 하는 관문. CFD 피드가 실제 QQQ와 일치하는지 확인한다.

검증 항목
  1) 봉 개수 / 세션 범위
  2) 가격 스케일 (point factor 자동 추정)
  3) 봉별 종가 절대오차 · 상관 · 최대괴리
  4) 저가/고가 일치 (트레일 판정에 직결되므로 가장 중요)
"""
import json, lzma, struct, datetime as dt, traceback, urllib.request, urllib.error
import pandas as pd
import numpy as np
import yfinance as yf

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126 Safari/537.36"}
INST = "QQQUSUSD"
OUT = []


def log(s):
    print(s)
    OUT.append(s)


def fetch_hour(day, hour_utc):
    url = (f"https://datafeed.dukascopy.com/datafeed/{INST}/"
           f"{day.year}/{day.month-1:02d}/{day.day:02d}/{hour_utc:02d}h_ticks.bi5")
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log(f"    {hour_utc:02d}h HTTP {e.code}")
        return None
    except Exception as e:
        log(f"    {hour_utc:02d}h {type(e).__name__}")
        return None


def decode_bi5(raw):
    """LZMA(alone) 해제 후 20바이트 레코드 파싱. (ms, ask, bid, askvol, bidvol) big-endian"""
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
        # 크기 필드가 unknown인 경우 대비
        try:
            patched = raw[:5] + b"\xff" * 8 + raw[13:]
            data = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(patched)
        except Exception:
            return []
    n = len(data) // 20
    return [struct.unpack(">iiiff", data[i*20:(i+1)*20]) for i in range(n)]


def ticks_to_1m(day, ticks_by_hour, scale):
    rows = []
    for hour_utc, ticks in ticks_by_hour:
        base = dt.datetime.combine(day, dt.time(hour_utc), tzinfo=dt.timezone.utc)
        for (ms, ask, bid, av, bv) in ticks:
            t = base + dt.timedelta(milliseconds=ms)
            mid = (ask + bid) / 2.0 / scale
            rows.append((t, mid, (av + bv)))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["t", "px", "vol"]).set_index("t").sort_index()
    g = df.resample("1min")
    out = pd.DataFrame({"Open": g["px"].first(), "High": g["px"].max(),
                        "Low": g["px"].min(), "Close": g["px"].last(),
                        "Volume": g["vol"].sum()}).dropna()
    out.index = out.index.tz_convert("America/New_York")
    return out


def main():
    # 최근 거래일 중 yfinance 1분봉이 있는 날
    yf_df = yf.download("QQQ", period="5d", interval="1m", prepost=False,
                        auto_adjust=False, progress=False)
    if isinstance(yf_df.columns, pd.MultiIndex):
        yf_df.columns = yf_df.columns.get_level_values(0)
    yf_df = yf_df.dropna()
    yf_df.index = yf_df.index.tz_convert("America/New_York")
    yf_df = yf_df[(yf_df.index.time >= dt.time(9, 30)) & (yf_df.index.time < dt.time(16, 0))]
    day = sorted(set(yf_df.index.date))[-1]
    ref = yf_df[yf_df.index.date == day]
    log(f"대조 기준일: {day} · yfinance 1분봉 {len(ref)}개 "
        f"({ref.index[0].strftime('%H:%M')}~{ref.index[-1].strftime('%H:%M')} ET)")
    log("")

    log("Dukascopy 틱 수집 (13~20h UTC = 09~16h ET):")
    tbh, total = [], 0
    for h in range(13, 21):
        raw = fetch_hour(day, h)
        tk = decode_bi5(raw)
        if tk:
            tbh.append((h, tk))
            total += len(tk)
        log(f"  {h:02d}h UTC  raw={len(raw) if raw else 0:8,d}B  틱={len(tk):7,d}")
    log(f"  합계 틱 {total:,d}")
    log("")

    if total == 0:
        log("틱 0개 — 이 경로로는 불가")
        return OUT

    # 스케일 자동 추정
    sample = np.median([(a + b) / 2.0 for (_, a, b, _, _) in tbh[0][1][:400]])
    target = float(ref["Close"].median())
    cand = [1, 10, 100, 1000, 10000, 100000]
    scale = min(cand, key=lambda s: abs(sample / s - target))
    log(f"스케일 추정: raw중앙값 {sample:,.0f} / 기준가 {target:.2f} → 나누기 {scale} "
        f"(→ {sample/scale:.2f})")
    log("")

    duk = ticks_to_1m(day, tbh, scale)
    duk = duk[(duk.index.time >= dt.time(9, 30)) & (duk.index.time < dt.time(16, 0))]
    log(f"Dukascopy 1분봉 {len(duk)}개 "
        f"({duk.index[0].strftime('%H:%M')}~{duk.index[-1].strftime('%H:%M')} ET)")
    log("")

    j = ref.join(duk, how="inner", lsuffix="_y", rsuffix="_d")
    log(f"[교집합 {len(j)}봉 대조]")
    if len(j) < 30:
        log("  겹치는 봉이 너무 적음 — 검증 불가")
        return OUT

    for col in ("Close", "Open", "High", "Low"):
        a = j[f"{col}_y"].astype(float)
        b = j[f"{col}_d"].astype(float)
        d = (b - a)
        log(f"  {col:6s} 평균오차 {d.mean():+.4f}  절대평균 {d.abs().mean():.4f}  "
            f"최대괴리 {d.abs().max():.4f}  상관 {a.corr(b):.6f}")
    log("")

    # 트레일 판정에 직결되는 항목
    rng_y = (j["High_y"].astype(float) - j["Low_y"].astype(float))
    rng_d = (j["High_d"].astype(float) - j["Low_d"].astype(float))
    log(f"  봉 레인지 평균  yfinance {rng_y.mean():.4f} / dukascopy {rng_d.mean():.4f} "
        f"(비율 {rng_d.mean()/rng_y.mean():.3f})")
    narrower = int((rng_d < rng_y * 0.8).sum())
    log(f"  Dukascopy 레인지가 20% 이상 좁은 봉: {narrower}/{len(j)} "
        f"({narrower/len(j)*100:.1f}%)")
    log("")

    tol = 0.05
    ok = (j["Close_y"].astype(float) - j["Close_d"].astype(float)).abs()
    log(f"  종가 오차 ${tol} 이내: {int((ok<=tol).sum())}/{len(j)} "
        f"({(ok<=tol).mean()*100:.1f}%)")
    log("")
    log("판정: 상관 0.999+ / 절대평균 오차 $0.05 이하 / 레인지 비율 0.9~1.1 이면 사용 가능")
    return OUT


if __name__ == "__main__":
    try:
        main()
    except Exception:
        OUT.append("실패:\n" + traceback.format_exc())
        print(OUT[-1])
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
               "report": "\n".join(OUT)},
              open("dukasval_result.json", "w"), ensure_ascii=False, indent=1)
