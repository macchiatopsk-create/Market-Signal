"""Dukascopy 1분봉 백필 — 갭 거래일만 (B안).
2년 전체가 아니라 '갭 조건을 만족하는 날'만 받는다. 약 88일.

완전성 게이트: 정규장 봉이 기준치에 미달하거나 실패 시간대가 있으면 저장하지 않고
              미완료로 남긴다. 구멍 뚫린 날이 '확보됨'으로 잡히는 것을 막는다.
이어받기:     매 실행마다 아직 완전하지 않은 날을 앞에서부터 처리.
"""
import os, json, lzma, struct, time, datetime as dt, traceback
import urllib.request, urllib.error
import pandas as pd
import numpy as np
import yfinance as yf

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126 Safari/537.36"}
INST = "QQQUSUSD"
SCALE = 1000.0
DATA = "data/1m"
HOURS = list(range(13, 19))     # UTC 13~18 = ET 09:00~14:59 (진입~14:00 최종컷 커버)
MIN_BARS = 320                  # 정규장 09:30~15:00 = 330봉. 여유 10봉
DELAY = 1.2
RETRY = 5
BUDGET_SEC = 1500
OUT = []


def log(s):
    print(s, flush=True)
    OUT.append(s)


def fetch_hour(day, h):
    url = (f"https://datafeed.dukascopy.com/datafeed/{INST}/"
           f"{day.year}/{day.month-1:02d}/{day.day:02d}/{h:02d}h_ticks.bi5")
    wait = 2.0
    for a in range(RETRY):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=35) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
            if a < RETRY - 1:
                time.sleep(wait)
                wait *= 1.8
                continue
            return None
        except Exception:
            if a < RETRY - 1:
                time.sleep(wait)
                wait *= 1.8
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
    return [struct.unpack(">iiiff", data[i*20:(i+1)*20]) for i in range(len(data)//20)]


def day_bars(day):
    rows, fails = [], []
    for h in HOURS:
        raw = fetch_hour(day, h)
        if raw is None:
            fails.append(h)
            time.sleep(DELAY)
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
    out = out[(out.index.time >= dt.time(9, 30)) & (out.index.time < dt.time(15, 0))]
    return out.round(4), fails


def gap_days():
    """2년 일봉으로 갭 0.2~1.5% & 개장VIX|x|<5% 인 날 목록."""
    v = yf.Ticker("^VIX").history(period="2y")[["Open", "Close"]].dropna()
    try:
        v.index = v.index.tz_localize(None)
    except Exception:
        pass
    v.index = pd.to_datetime(v.index).normalize()
    vch = (v["Open"] / v["Close"].shift(1) - 1) * 100
    vm = {str(pd.Timestamp(k).date()): float(x) for k, x in vch.dropna().items()}

    d = yf.download("QQQ", period="2y", interval="1d", auto_adjust=False, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.dropna()
    try:
        d.index = d.index.tz_localize(None)
    except Exception:
        pass
    out, pc = [], None
    for ts in d.index:
        o = float(d.loc[ts, "Open"]); c = float(d.loc[ts, "Close"])
        ds = str(pd.Timestamp(ts).date())
        if pc:
            gp = (o - pc) / pc * 100
            vx = vm.get(ds)
            if (vx is None or abs(vx) < 5.0) and 0.2 <= abs(gp) < 1.5:
                out.append((pd.Timestamp(ts).date(), round(gp, 3), pc,
                            float(d.loc[ts, "High"]), float(d.loc[ts, "Low"]), c))
        pc = c
    return out


def load_status():
    p = f"{DATA}/_status.json"
    return json.load(open(p)) if os.path.exists(p) else {}


def save_status(st):
    json.dump(st, open(f"{DATA}/_status.json", "w"), ensure_ascii=False, indent=1)


def save_day(d, bars):
    path = f"{DATA}/QQQ_{d.year}-{d.month:02d}.csv.gz"
    b = bars.copy()
    b.insert(0, "ts", b.index.strftime("%Y-%m-%d %H:%M"))
    b = b.reset_index(drop=True)
    if os.path.exists(path):
        old = pd.read_csv(path, compression="gzip")
        old = old[~pd.to_datetime(old["ts"]).dt.date.eq(d)]
        b = pd.concat([old, b], ignore_index=True)
    b = b.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    b.to_csv(path, index=False, compression="gzip")
    return path, len(b)


def main():
    os.makedirs(DATA, exist_ok=True)
    t0 = time.time()
    gd = gap_days()
    st = load_status()
    log(f"갭 거래일 {len(gd)}일 ({gd[0][0]} ~ {gd[-1][0]})")
    ok_n = sum(1 for k, v in st.items() if v.get("ok"))
    log(f"확보 완료 {ok_n}일 · 남음 {len(gd)-ok_n}일")
    log("")

    todo = [g for g in gd if not st.get(str(g[0]), {}).get("ok")]
    todo = sorted(todo, key=lambda g: g[0], reverse=True)
    got, rep = 0, []
    for (d, gp, pc, rh, rl, rc) in todo:
        if time.time() - t0 > BUDGET_SEC:
            log(f"  [시간예산 초과 — {d} 이전 중단]")
            break
        bars, fails = day_bars(d)
        n = len(bars)
        key = str(d)
        if n < MIN_BARS or fails:
            st[key] = dict(ok=False, bars=n, fails=fails,
                           tries=st.get(key, {}).get("tries", 0) + 1)
            rep.append(f"  {d} 갭{gp:+.2f}%  봉 {n:3d}  실패시간 {fails}  → 미완료(재시도 대상)")
            save_status(st)
            continue
        dh, dl = float(bars["High"].max()), float(bars["Low"].min())
        dev = max(abs(dh - rh), abs(dl - rl))
        # 일봉 대비 고저 괴리 (15:00 컷이라 종가는 비교 안 함)
        if dh > rh + 0.05 or dl < rl - 0.05:
            rep.append(f"  {d} 갭{gp:+.2f}%  봉 {n:3d}  일봉범위 이탈 고{dh-rh:+.3f} 저{dl-rl:+.3f} → 보류")
            st[key] = dict(ok=False, bars=n, fails=[], reason="range",
                           tries=st.get(key, {}).get("tries", 0) + 1)
            save_status(st)
            continue
        path, tot = save_day(d, bars)
        st[key] = dict(ok=True, bars=n, fails=[], hi=round(dh - rh, 3), lo=round(dl - rl, 3))
        save_status(st)
        rep.append(f"  {d} 갭{gp:+.2f}%  봉 {n:3d}  고{dh-rh:+.3f} 저{dl-rl:+.3f}  저장")
        got += 1

    el = time.time() - t0
    log(f"이번 실행 {got}일 확보 · 소요 {el:.0f}초")
    ok_n = sum(1 for k, v in st.items() if v.get("ok"))
    left = len(gd) - ok_n
    rate = (got / el * BUDGET_SEC) if el > 0 and got else 0
    eta = (left / rate) if rate else 0
    log(f"누적 {ok_n}/{len(gd)}일 ({ok_n/len(gd)*100:.1f}%) · 남음 {left}일 · "
        f"이 속도면 {eta:.0f}회 실행 (35분 간격 → 약 {eta*35/60:.1f}시간)")
    log("")
    for r in rep:
        log(r)
    return OUT


if __name__ == "__main__":
    try:
        main()
    except Exception:
        OUT.append("실패:\n" + traceback.format_exc())
        print(OUT[-1])
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
               "report": "\n".join(OUT)},
              open("gapfill1m_result.json", "w"), ensure_ascii=False, indent=1)
