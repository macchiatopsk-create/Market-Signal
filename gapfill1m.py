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
MIN_BARS = 200                  # 정규장 09:30~15:00 = 330봉. 여유 10봉
DELAY = 0.9
RETRY = 2
BUDGET_SEC = 2300
OUT = []


def log(s):
    print(s, flush=True)
    OUT.append(s)


def _read_capped(r, cap=10.0):
    """슬로우드립 스로틀 방어: 전체 읽기에 벽시계 상한."""
    buf, t0 = b"", time.time()
    while True:
        chunk = r.read(65536)
        if not chunk:
            return buf
        buf += chunk
        if time.time() - t0 > cap:
            raise TimeoutError("read cap")


LAST_ERR = [""]


def fetch_hour(day, h):
    url = (f"https://datafeed.dukascopy.com/datafeed/{INST}/"
           f"{day.year}/{day.month-1:02d}/{day.day:02d}/{h:02d}h_ticks.bi5")
    wait = 1.5
    for a in range(RETRY):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=6) as r:
                return _read_capped(r, cap=25.0)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return b""
            if a < RETRY - 1:
                time.sleep(wait)
                wait *= 1.8
                continue
            return None
        except Exception as e:
            LAST_ERR[0] = f"{type(e).__name__}:{str(e)[:40]}"
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


def day_hours(day):
    """ET 09:00~14:59를 그 날짜의 실제 UTC 오프셋으로 환산 (EDT 13~18 / EST 14~19)."""
    from zoneinfo import ZoneInfo
    off = int(dt.datetime(day.year, day.month, day.day, 12,
                          tzinfo=ZoneInfo("America/New_York"))
              .utcoffset().total_seconds() // 3600)      # -4 또는 -5
    return [h - off for h in range(9, 15)]


def day_bars(day, hours, deadline=None):
    rows, fails = [], []
    for h in hours:
        if deadline and time.time() > deadline:
            fails.append(h)
            continue
        if len(fails) >= 1 and not rows:
            fails.append(h)              # 스로틀 버스트 — 날 포기, 예산 절약
            continue
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
    """2022-01~ 일봉으로 갭 0.2~1.5% & 개장VIX|x|<5% 인 날 목록."""
    v = yf.Ticker("^VIX").history(start="2021-12-15")[["Open", "Close"]].dropna()
    try:
        v.index = v.index.tz_localize(None)
    except Exception:
        pass
    v.index = pd.to_datetime(v.index).normalize()
    vch = (v["Open"] / v["Close"].shift(1) - 1) * 100
    vm = {str(pd.Timestamp(k).date()): float(x) for k, x in vch.dropna().items()}

    d = yf.download("QQQ", start="2021-12-15", interval="1d", auto_adjust=False, progress=False)
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


def rebuild_status():
    """상태파일이 깨졌을 때 데이터 파일(진실원)에서 재구성."""
    st = {}
    if os.path.isdir(DATA):
        for f in sorted(os.listdir(DATA)):
            if f.endswith(".csv.gz"):
                try:
                    d = pd.read_csv(f"{DATA}/{f}", compression="gzip")
                    cnt = d.groupby(pd.to_datetime(d["ts"]).dt.date).size()
                    for day, n in cnt.items():
                        if n >= MIN_BARS:
                            st[str(day)] = dict(ok=True, bars=int(n), fails=[])
                except Exception:
                    pass
    return dict(sorted(st.items()))


def load_status():
    p = f"{DATA}/_status.json"
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p))
    except Exception:
        log("[경고] _status.json 파싱 실패 — 데이터 파일에서 재구성")
        st = rebuild_status()
        json.dump(st, open(p, "w"), ensure_ascii=False, indent=1)
        return st


def save_status(st):
    json.dump(st, open(f"{DATA}/_status.json", "w"), ensure_ascii=False, indent=1)


def save_day(d, bars, replace=True):
    """봉을 월파일에 기록. replace=False면 그 날 기존 봉과 병합(시간대 캐시).
    반환: (그 날 봉수, 그 날 High, 그 날 Low)."""
    path = f"{DATA}/QQQ_{d.year}-{d.month:02d}.csv.gz"
    b = bars.copy()
    if len(b):
        b.insert(0, "ts", b.index.strftime("%Y-%m-%d %H:%M"))
        b = b.reset_index(drop=True)
    else:
        b = pd.DataFrame(columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    if os.path.exists(path):
        old = pd.read_csv(path, compression="gzip")
        if replace:
            old = old[~pd.to_datetime(old["ts"]).dt.date.eq(d)]
        b = pd.concat([old, b], ignore_index=True)
    b = b.drop_duplicates(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    b.to_csv(path, index=False, compression="gzip")
    m = b[pd.to_datetime(b["ts"]).dt.date.eq(d)]
    if not len(m):
        return 0, 0.0, 0.0
    return len(m), float(m["High"].max()), float(m["Low"].min())


def main():
    os.makedirs(DATA, exist_ok=True)
    t0 = time.time()
    import signal
    def _watchdog(signum, frame):
        import traceback as _tb
        OUT.append("⏰ 워치독 발동 — 행 지점 스택:\n" + "".join(_tb.format_stack(frame))[-1500:])
        json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                   "report": "\n".join(OUT)},
                  open("gapfill1m_result.json", "w"), ensure_ascii=False, indent=1)
        os._exit(0)
    signal.signal(signal.SIGALRM, _watchdog)
    signal.alarm(BUDGET_SEC + 300)
    gd = gap_days()
    gd_all = gd
    shard = os.environ.get("SHARD", "")
    if shard == "0":
        gd = [g for g in gd if str(g[0]) >= "2023-05-01"]
    elif shard == "1":
        gd = [g for g in gd if str(g[0]) < "2023-05-01"]
    if shard:
        log(f"샤드 {shard}: 담당 {len(gd)}일 / 전체 {len(gd_all)}일")
    st = load_status()
    log(f"갭 거래일 {len(gd)}일 ({gd[0][0]} ~ {gd[-1][0]})")
    ok_n = sum(1 for k, v in st.items() if v.get("ok"))
    log(f"확보 완료 {ok_n}일 · 남음 {len(gd)-ok_n}일")
    log("")

    todo = [g for g in gd if not st.get(str(g[0]), {}).get("ok")]
    todo = sorted(todo, key=lambda g: (st.get(str(g[0]), {}).get("tries", 0),
                                       -g[0].toordinal()))  # 신규(tries=0) 우선, 그 안에선 최신 우선
    got, rep = 0, []
    for (d, gp, pc, rh, rl, rc) in todo:
        if time.time() - t0 > BUDGET_SEC:
            log(f"  [시간예산 초과 — {d} 이전 중단]")
            break
        key = str(d)
        prev = st.get(key, {})
        if prev.get("reason") in ("range", "halfday") and prev.get("tries", 0) >= 3:
            continue
        if prev.get("tries", 0) >= 8 and not prev.get("fails"):
            continue                     # 만성 결손일 파킹 — 종료 후 수동 검토
        need_all = day_hours(d)
        hok = [h for h in prev.get("hours_ok", []) if h in need_all]
        need = [h for h in need_all if h not in hok]
        bars, fails = day_bars(d, need, deadline=t0 + BUDGET_SEC + 240)
        hok = sorted(set(hok) | (set(need) - set(fails)))
        n, dh, dl = save_day(d, bars, replace=(not prev.get("hours_ok")))
        if not fails and n < 250 and set(hok) == set(need_all):
            st[key] = dict(ok=False, bars=n, fails=[], reason="halfday",
                           tries=prev.get("tries", 0) + 3)
            rep.append(f"  {d} 갭{gp:+.2f}%  봉 {n:3d}  조기폐장 추정 → 유니버스 퇴출")
            save_status(st)
            continue
        if not fails and not need and n < MIN_BARS:
            hok = []                     # 캐시 불일치 → 다음 런 전체 재수집
        if n < MIN_BARS or fails:
            st[key] = dict(ok=False, bars=n, fails=fails, hours_ok=hok,
                           tries=prev.get("tries", 0) + 1)
            rep.append(f"  {d} 갭{gp:+.2f}%  봉 {n:3d}  실패 {fails}  캐시 {len(hok)}/{len(need_all)}h  {LAST_ERR[0]}")
            save_status(st)
            continue
        # 일봉 대비 고저 괴리 — save_day가 반환한 병합 후 그 날 고저 사용
        if dh > rh + 0.05 or dl < rl - 0.05:
            rep.append(f"  {d} 갭{gp:+.2f}%  봉 {n:3d}  일봉범위 이탈 고{dh-rh:+.3f} 저{dl-rl:+.3f} → 보류")
            st[key] = dict(ok=False, bars=n, fails=[], reason="range",
                           tries=st.get(key, {}).get("tries", 0) + 1)
            save_status(st)
            continue
        st[key] = dict(ok=True, bars=n, fails=[], hi=round(dh - rh, 3), lo=round(dl - rl, 3))
        save_status(st)
        rep.append(f"  {d} 갭{gp:+.2f}%  봉 {n:3d}  고{dh-rh:+.3f} 저{dl-rl:+.3f}  저장")
        got += 1

    el = time.time() - t0
    log(f"이번 실행 {got}일 확보 · 소요 {el:.0f}초")
    ok_n = sum(1 for k, v in st.items() if isinstance(v, dict) and v.get("ok"))
    left = len(gd_all) - ok_n
    rate = (got / el * BUDGET_SEC) if el > 0 and got else 0
    eta = (left / rate) if rate else 0
    log(f"누적 {ok_n}/{len(gd_all)}일 (전역 {ok_n/len(gd_all)*100:.1f}%) · 남음 {left}일 · "
        f"이 속도면 {eta:.0f}회 실행 (35분 간격 → 약 {eta*35/60:.1f}시간)")
    if left <= 5 and not st.get("_recheck", {}).get("fired"):
        tok = os.environ.get("GH_TOKEN", "")
        if tok:
            try:
                req = urllib.request.Request(
                    "https://api.github.com/repos/macchiatopsk-create/"
                    "Market-Signal/actions/workflows/research.yml/dispatches",
                    data=json.dumps({"ref": "main",
                                     "inputs": {"target": "recheck"}}).encode(),
                    headers={"Authorization": f"Bearer {tok}",
                             "Accept": "application/vnd.github+json"})
                urllib.request.urlopen(req, timeout=15)
                st["_recheck"] = {"fired": True}
                save_status(st)
                log("★ 백필 사실상 완료 — 재심 배터리(recheck) 자동 발사")
            except Exception as e:
                log(f"recheck 자동발사 실패: {e}")
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
