#!/usr/bin/env python3
"""
avfill · Alpha Vantage 월단위 1분봉 수확기 (Dukascopy 대역차단 우회)
  TIME_SERIES_INTRADAY month=YYYY-MM → 한 요청에 한 달 전체
  미확보일만 채움 · yfinance 일봉 고저 대조 검증 · 월파일 병합 · 무제한 멱등 재실행
"""
import datetime as dt, gzip, io, json, os, time, urllib.request
import pandas as pd, yfinance as yf

DATA = "data/1m"
STATUS = f"{DATA}/_status.json"
MIN_BARS = 200
TOL = 0.005          # 일봉 고저 대조 허용오차 0.5%


def load_status():
    return json.load(open(STATUS))


def save_status(st):
    json.dump(st, open(STATUS, "w"), ensure_ascii=False, indent=0, sort_keys=True)


def fetch_month(mo, key):
    url = ("https://www.alphavantage.co/query?function=TIME_SERIES_INTRADAY"
           f"&symbol=QQQ&interval=1min&month={mo}&outputsize=full"
           f"&extended_hours=false&datatype=csv&apikey={key}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    head = raw[:200].decode("utf-8", "ignore")
    if head.lstrip().startswith("{"):
        return None, head          # JSON = 리밋/프리미엄/에러 통지
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = [c.strip().lower() for c in df.columns]
    df["ts"] = pd.to_datetime(df["timestamp"])
    return df, None


def main():
    key = os.environ.get("ALPHAVANTAGE_KEY", "")
    rep = []
    if not key:
        return ["ALPHAVANTAGE_KEY 미설정 — 레포 시크릿에 추가 필요"]
    st = load_status()
    days = {k: v for k, v in st.items() if not k.startswith("_")}
    missing = sorted(k for k, v in days.items()
                     if not v.get("ok") and v.get("reason") != "halfday")
    if not missing:
        return ["미확보일 없음 — 완주 상태"]
    months = sorted({k[:7] for k in missing})
    rep.append(f"미확보 {len(missing)}일 / {len(months)}개월: {months[0]}~{months[-1]}")

    dd = yf.download("QQQ", start="2021-12-01", interval="1d",
                     auto_adjust=False, progress=False)
    if isinstance(dd.columns, pd.MultiIndex):
        dd.columns = dd.columns.get_level_values(0)
    dd.index = pd.to_datetime(dd.index).tz_localize(None)
    ref = {str(k.date()): (float(r["High"]), float(r["Low"])) for k, r in dd.iterrows()}

    got = 0
    for mo in months:
        av, err = fetch_month(mo, key)
        if av is None:
            rep.append(f"[{mo}] 중단 통지: {err[:160]}")
            break
        mo_days = [k for k in missing if k.startswith(mo)]
        path = f"{DATA}/QQQ_{mo}.csv.gz"
        try:
            old = pd.read_csv(path)
        except Exception:
            old = pd.DataFrame(columns=["ts", "Open", "High", "Low", "Close", "Volume"])
        saved = []
        for key_d in mo_days:
            d = av[(av["ts"].dt.strftime("%Y-%m-%d") == key_d)
                   & (av["ts"].dt.time >= dt.time(9, 30))
                   & (av["ts"].dt.time <= dt.time(14, 59))].sort_values("ts")
            n = len(d)
            if n < MIN_BARS:
                if n > 0:
                    st[key_d] = dict(ok=False, bars=n, reason="av_thin",
                                     tries=days[key_d].get("tries", 0) + 1)
                continue
            hi, lo = float(d["high"].max()), float(d["low"].min())
            rh, rl = ref.get(key_d, (None, None))
            if rh and (abs(hi - rh) / rh > TOL or abs(lo - rl) / rl > TOL):
                st[key_d] = dict(ok=False, bars=n, reason="av_range",
                                 hi=round(hi - rh, 3), lo=round(lo - rl, 3),
                                 tries=days[key_d].get("tries", 0) + 1)
                continue
            rows = pd.DataFrame({
                "ts": d["ts"].dt.strftime("%Y-%m-%d %H:%M"),
                "Open": d["open"], "High": d["high"],
                "Low": d["low"], "Close": d["close"], "Volume": d["volume"]})
            old = old[~old["ts"].str.startswith(key_d)]
            old = pd.concat([old, rows], ignore_index=True)
            st[key_d] = dict(ok=True, bars=n, hi=round(hi - (rh or hi), 3),
                             lo=round(lo - (rl or lo), 3), src="av")
            saved.append(key_d)
            got += 1
        if saved or len(mo_days):
            old = old.sort_values("ts").reset_index(drop=True)
            with gzip.open(path, "wt") as f:
                old.to_csv(f, index=False)
            save_status(st)
        rep.append(f"[{mo}] 대상 {len(mo_days)}일 → 저장 {len(saved)}일")
        time.sleep(1.0)

    ok_n = sum(1 for v in st.values() if isinstance(v, dict) and v.get("ok"))
    rep.append(f"이번 실행 +{got}일 · 누적 확보 {ok_n}")
    return rep


if __name__ == "__main__":
    r = main()
    json.dump({"at": dt.datetime.utcnow().isoformat(), "report": "\n".join(r)},
              open("avfill_result.json", "w"), ensure_ascii=False, indent=1)
    print("\n".join(r))
