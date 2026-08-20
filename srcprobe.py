"""키 없이 접근 가능한 과거 1분봉 소스 탐색.
가입/API키 없이 GitHub Actions 러너에서 바로 받을 수 있는 곳을 찾는다.
각 후보에 대해 HTTP 상태 / 응답 크기 / 앞부분 내용을 보고한다.
"""
import json, datetime as dt, traceback, urllib.request, urllib.error, urllib.parse, gzip, io

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126 Safari/537.36"}
OUT = []


def log(s):
    print(s)
    OUT.append(s)


def probe(name, url, note="", binary=False, timeout=25):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            code = r.status
            enc = r.headers.get("Content-Encoding", "")
        if enc == "gzip":
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
        n = len(raw)
        if binary:
            head = raw[:40].hex()
        else:
            head = raw[:260].decode("utf-8", "replace").replace("\n", " | ")
        log(f"  [{code}] {name:34s} {n:9,d}B  {head}")
        return code, n, raw
    except urllib.error.HTTPError as e:
        log(f"  [{e.code}] {name:34s} HTTPError {e.reason}  {note}")
    except Exception as e:
        log(f"  [ERR] {name:34s} {type(e).__name__}: {str(e)[:110]}  {note}")
    return None, 0, None


def main():
    today = dt.date.today()
    old = today - dt.timedelta(days=120)      # 4개월 전 (yfinance 1분봉 범위 밖)
    while old.weekday() >= 5:
        old -= dt.timedelta(days=1)
    log(f"목표: {old} (약 120일 전) QQQ 1분봉 · 오늘 {today}")
    log("")

    log("[1] Yahoo chart API 직접 — 1분봉 30일 한계가 진짜인지")
    p2 = int(dt.datetime.combine(old + dt.timedelta(days=1), dt.time(0)).timestamp())
    p1 = int(dt.datetime.combine(old, dt.time(0)).timestamp())
    for host in ("query1", "query2"):
        probe(f"yahoo {host} 1m@{old}",
              f"https://{host}.finance.yahoo.com/v8/finance/chart/QQQ"
              f"?period1={p1}&period2={p2}&interval=1m&includePrePost=false")
    log("")

    log("[2] Dukascopy freeserv (키 없음)")
    for inst in ("QQQ.US/USD", "QQQUSUSD", "USQQQ.USUSD"):
        probe(f"dukas freeserv {inst}",
              "https://freeserv.dukascopy.com/2.0/index.php?path=chart/json3"
              f"&instrument={urllib.parse.quote(inst)}&offer_side=B&interval=1MIN"
              "&splits=true&stocks=true&limit=5")
    log("")

    log("[3] Dukascopy datafeed bi5 (틱 단위, 키 없음)")
    for inst in ("QQQ.US.USD", "QQQUSUSD", "USQQQ.USUSD"):
        probe(f"dukas bi5 {inst}",
              f"https://datafeed.dukascopy.com/datafeed/{inst}/"
              f"{old.year}/{old.month-1:02d}/{old.day:02d}/14h_ticks.bi5", binary=True)
    log("")

    log("[4] Stooq (키 없음)")
    probe("stooq 5min qqq.us",
          "https://stooq.com/q/d/l/?s=qqq.us&i=5")
    probe("stooq daily qqq.us (대조군)",
          "https://stooq.com/q/d/l/?s=qqq.us&i=d")
    log("")

    log("[5] 기타 키없음 후보")
    probe("nasdaq.com QQQ intraday",
          "https://api.nasdaq.com/api/quote/QQQ/chart?assetclass=etf")
    probe("cboe delayed quote",
          "https://cdn.cboe.com/api/global/delayed_quotes/charts/QQQ.json")
    probe("investing.com tvc 1m",
          "https://tvc4.investing.com/1/1/1/1/1/history?symbol=QQQ&resolution=1"
          f"&from={p1}&to={p2}")
    log("")

    log("판정 기준: [200] 이면서 응답 크기가 크고 내용에 가격/타임스탬프가 보이면 후보")
    return OUT


if __name__ == "__main__":
    try:
        main()
    except Exception:
        OUT.append("실패:\n" + traceback.format_exc())
        print(OUT[-1])
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
               "report": "\n".join(OUT)},
              open("srcprobe_result.json", "w"), ensure_ascii=False, indent=1)
