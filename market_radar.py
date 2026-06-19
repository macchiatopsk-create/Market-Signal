#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
시장 레이더 — 위기감지 + S&P500 + 나스닥 시그널 (자동 수집)
탭 3개 HTML을 만들고, 매일 시그널을 저장해 다음날 실제 결과로 승률을 자동 채점한다.

  · 위험감지: 5개 선행 신호 신호등
  · S&P500 / 나스닥: 추세+환경+폭+리더십 종합 → Strong Buy ~ Strong Sell
  · 승률 검증: 어제 시그널 vs 오늘 종가변화로 적중 누적 (페이퍼 검증용)

데이터: yfinance(키 불필요) + FRED(HY 스프레드·10Y-2Y, 무료 키)
실행: python market_radar.py   →  dashboard.html + radar_history.json
"""
import os, sys, json, datetime as dt
import requests
try:
    import yfinance as yf
except ImportError:
    print("필요: pip install yfinance requests"); sys.exit(1)

FRED_API_KEY = os.environ.get("FRED_API_KEY", "여기에_FRED_키_붙여넣기")
BASE = os.path.dirname(os.path.abspath(__file__))
OUT_HTML = os.path.join(BASE, "dashboard.html")
HIST_PATH = os.path.join(BASE, "radar_history.json")

# --- PWA 자산 (아이콘 base64 내장: 별도 파일 업로드 불필요) ---
ICON_192 = "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"
ICON_512 = "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"
ICON_180 = "iVBORw0KGgoAAAANSUhEUgAAALQAAAC0CAIAAACyr5FlAAAHkUlEQVR4nO2dPW7cOhSFmYdUgZEq6dIYyCq8DiNryRKylsDr8CoMuHHnNgtIQTxCoeZqNNL9OZc8XxX4Pcsc8dO5JEVpPnz6/KUQcon/ohtAcKEcRIRyEBHKQUQoBxGhHESEchARykFEKAcRoRxEhHIQEcpBRCgHEaEcRIRyEBHKQUQoBxGhHP/w9dv36CYAQTmICOUgIh+jGxCDVD7ufv+6u/Tz14dH0/ZgMoUc50cS989P3U9m0GVYObaFeH97ufjzi7FxkaUuo4oylBySEJIKWowqyiByrLWwFkKiijKGIrnlwHGio2VJakuyytFpAeLEmtRBkk+OpRawTnQkVSSTHBm1WJJOkRxyZNdiSSJFEiyfNzPe316ym9FYr6oBAp0cSy1iW2IBfoTgJsfYZjSQIwQxOSbRogEbIXDJMZsZDcAIwZJjWjMqaH6glJXJtWhAlRiI5KAZHSAREi8HzbgIgh/BctCMDcL9iJSDZlwl1o8wOWjGTgL9iJmtIJiR6Pml++enkPlLgBxRZiSyYU2IH95y+Jtx067j/bvP/fH3I6asOJgBu700Ea5y1A6z7qQs20sP4BwefrMVBzO+fvve7RkbyYyK5+TFKTmsB4Mj7SO8ilt+uJYVo25DmBgPiUdZsSsoyzoylRk+xcVcDlMz6j+GHFtcxcGP+Luyx5gzMJyxlcMiNlopmTMwlliHh6EcRmbUf0yuRcPUj0xlhWY4YzWVVY8NmrHm9eGxFlmjc5IjOWjGGod1MJPk0I0NmtGx1OL97cUuPFAeTbgKzSjujyzolxWL2KAZRTajnhyLu1fQyUEzKlHPOCnLodidqXf1abFTC6ORB3RyFPedQVCEPxQJKodpQUEWohKuRUVTDq0eteu8qxtLwzcYH9bCorKAJkcxuyljcXAVQNJiidpUVutyt75dh3kv98+Pn7onUAXl5EA77/irqzUwVHq0Vpbzx2lglZWp1t3dVsEPk+PG2wESmQGLTnKoXPGKkYi8tLqtxcnw0E0grLJS9GbCgGZsa6E+YjjPaGUF7fw2UtSRDpTk0L3coWJjvxZow1IFOXBiHKcllZC0UDRsnLJCM9SBkAOtX89z5hZJgRk5oYw5TgKiV90OHtsGRSCSYwwGqCMdIyRH+MW61gJnxnGGs8kBkudRbXh9eOzMwDkV56+Z+OTA0esm7IoIzmoHxxxHGG94cZH45DiJVvDsfFx9jxY4l/5J0suhwh4zJkmLJSwrNENk9uS4asacWlSYHFvMbEYp5cOnz18O/Fr4upMWd79/bfzXPz9+urXEmgOj44Nlpf2l85OFk0c4+evbTzFFtSr8rFZmLysbhWPymlIoRxEkoBmFclQ6FWhGJf1UVms5sj15NvD291thchCReDmgNsYhgBM8Z+XA6VqENhSMZmjpFZ8c50G4yDoAm3SAEeQgRgwiB0h1wxkuqAAhB0jXIgClF4QcKoQbBtWvKijIEd4rCC3BMUOxJSjJoduvzn4gvyzvDChyaBF4ZnE6VQs4Oc5fhc7FBf/VvIfRkUOlPxSvvNYe0zO+/J5KrWOqvFdNqz1wyaFFt1dNHfC3FaqAJYduRbDzQ90MtKFoRXk/B9pjXl19URwZQH3Mivo1oJYcuu960/2cywg5NhDpftHi1eyAL8vD3Ql2Uwh1zyat9/l1zu3s5qtfwXEewElK4+BzKxK66b3zOBefWtvYB3qsP0y/GQhzbyKoHPsPtfE84559wtui+HyJmOfpugncslLZLi7bT7rePz9d9aM7uOeAGrmgVJSnsorDScDpgAWwsVHQ1jk6cO73qoO5sNGhL4fFQtZgflh855CFZ9DJsWQYPxJ9EBM53FbBt8ebgE815lp3z5EcB/yYwQxrrORQHyvc5MckZliPatHXOZa0L7par0YA2rAkXWZUDMuK9S00xcOakuJ23UWUl8/XGH0GuzOuHvvF7ONb51COAeka/AhJWkqWmMtht4rV7dJQP/5h7HZ+tOMbHbnDIzlM/UCLkKUWqc0ozmXFqP9AIsQ6MIr7BeA0lX03/rrlZTg5F/vuczn8UbdBjPlsZYlPJJ7prZtmK85a+N/IdZWjOH7CY9s/98jhsLFU+qPOE5+YFVKHDVfrgerhC10qiMPvGfNOjhK3AKB+l9iHwPWSADkKxgLRflcQGhnShhg5SvTHloB6Yi/8FIUtn6MtXqERbkaJvbdCPyQQzCjhN97oxxoQM0q4HIV+/AuOGQVBjkI//gfKjIKzTVD9RRq5QNOiApEcjTkjBNOMgiZHmc8PWDMKTllZMkmJQdaiApccjbEjBN+MgpkcjSEjJIUWFdzkaIDsAjyPwz5CXaCTo7HeBZji5DaWTidqeQ45KhkVSapFJZMclcC9xPvx33VsQT45KsfeK2pNyPZSO7LKUdnYKOrZK4M50cgtR2P9UJ11sQ/fdezAIHJUlh0jibL+P/ewPYUeSYglYXtIPTF9jHtgppBjza26zKDCmknlkIDafR5OguVzEgXlICKUg4hwzEFEmBxEhHIQEcpBRCgHEaEcRIRyEBHKQUQoBxGhHESEchARykFEKAcRoRxEhHIQEcpBRCgHEfkLHhZcWrAO5OcAAAAASUVORK5CYII="
MANIFEST = """{
  "name": "시장 레이더",
  "short_name": "레이더",
  "start_url": ".",
  "scope": ".",
  "display": "standalone",
  "background_color": "#0a0e14",
  "theme_color": "#0a0e14",
  "icons": [
    {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
    {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
  ]
}"""

def write_pwa_assets():
    import base64 as _b64
    for name, data in (("icon-192.png", ICON_192), ("icon-512.png", ICON_512), ("icon-180.png", ICON_180)):
        with open(os.path.join(BASE, name), "wb") as f:
            f.write(_b64.b64decode(data))
    with open(os.path.join(BASE, "manifest.webmanifest"), "w", encoding="utf-8") as f:
        f.write(MANIFEST)


# ===========================================================================
# 데이터 헬퍼
# ===========================================================================
def fred_latest(series_id):
    url = "https://api.stlouisfed.org/fred/series/observations"
    p = dict(series_id=series_id, api_key=FRED_API_KEY, file_type="json",
             sort_order="desc", limit=15)
    r = requests.get(url, params=p, timeout=30); r.raise_for_status()
    for o in r.json().get("observations", []):
        if o["value"] not in (".", "", None):
            return float(o["value"])
    raise ValueError(series_id)

def yf_hist(ticker, period="1y"):
    h = yf.Ticker(ticker).history(period=period)["Close"].dropna()
    if len(h) < 2: raise ValueError(ticker)
    return h

def last(ticker):
    return float(yf_hist(ticker, "1mo").iloc[-1])

def ma(series, n):
    return float(series.tail(n).mean()) if len(series) >= n else float(series.mean())

def trend(ticker_a, ticker_b, n=20):
    """A/B 비율의 n일 추세 부호 (+이면 A가 상대적으로 강해지는 중)."""
    a = yf_hist(ticker_a, "3mo"); b = yf_hist(ticker_b, "3mo")
    m = min(len(a), len(b)); a, b = a.tail(m), b.tail(m)
    ratio = (a.values / b.values)
    if len(ratio) < n+1: n = len(ratio)-1
    return ratio[-1] - ratio[-1-n]

def series_trend(ticker, n=20):
    """단일 종목 n일 가격 변화(절대)."""
    h = yf_hist(ticker, "3mo")
    if len(h) < n+1: n = len(h)-1
    return float(h.iloc[-1] - h.iloc[-1-n])

def rsi(series, n=14):
    d = series.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / dn.replace(0, 1e-9)
    return float((100 - 100/(1+rs)).iloc[-1])

def momentum(series, n=20):
    if len(series) < n+1: n = len(series)-1
    return float((series.iloc[-1]/series.iloc[-1-n] - 1) * 100)

def fred_change(series_id, days=5):
    url = "https://api.stlouisfed.org/fred/series/observations"
    p = dict(series_id=series_id, api_key=FRED_API_KEY, file_type="json",
             sort_order="desc", limit=40)
    r = requests.get(url, params=p, timeout=30); r.raise_for_status()
    vals = [float(o["value"]) for o in r.json().get("observations", [])
            if o["value"] not in (".", "", None)]
    if len(vals) < days+1: raise ValueError(series_id)
    return round(vals[0] - vals[days], 2)

# ===========================================================================
# 환경 데이터 (S&P·나스닥 공용)
# ===========================================================================
def collect_macro():
    d, e = {}, {}
    for key, fn in {
        "vix":   lambda: last("^VIX"),
        "vix3m": lambda: last("^VIX3M"),
        "move":  lambda: last("^MOVE"),
    }.items():
        try: d[key] = round(fn(), 2)
        except Exception as ex: e[key] = str(ex)
    try: d["hyoas"] = round(fred_latest("BAMLH0A0HYM2"), 2)
    except Exception as ex: e["hyoas"] = str(ex)
    try: d["t10y2y"] = round(fred_latest("T10Y2Y"), 2)
    except Exception as ex: e["t10y2y"] = str(ex)
    try: d["real_chg"] = fred_change("DFII10", 5)   # 10Y 실질금리 5일 변화
    except Exception as ex: e["real_chg"] = str(ex)
    return d, e

# ===========================================================================
# 위험감지 5신호 (수집 + 판정)
# ===========================================================================
RISK = [
    dict(key="hyoas", name="HY 크레딧 스프레드", unit="%", dir="high", green=3.5, red=5.0),
    dict(key="vix",   name="VIX",               unit="pt",dir="high", green=20,  red=30),
    dict(key="dxy",   name="달러 5일 변화",       unit="%", dir="risePct", green=1.5, red=3.0),
    dict(key="ten",   name="10년물 5일 변화",     unit="bps",dir="dropBps",green=-30, red=-50),
    dict(key="gold",  name="금 1일 변화",         unit="%", dir="dropPct", green=-2, red=-4),
]
def risk_status(ind, v):
    if v is None: return "none"
    if ind["dir"] in ("high","risePct"):
        return "green" if v<ind["green"] else ("amber" if v<ind["red"] else "red")
    return "green" if v>ind["green"] else ("amber" if v>ind["red"] else "red")

def collect_risk(macro):
    vals = {"hyoas": macro.get("hyoas"), "vix": macro.get("vix")}
    try:
        h = yf_hist("DX-Y.NYB","1mo"); vals["dxy"] = round((h.iloc[-1]-h.iloc[-6])/h.iloc[-6]*100,2)
    except: vals["dxy"]=None
    try:
        h = yf_hist("^TNX","1mo"); vals["ten"] = round((h.iloc[-1]-h.iloc[-6])*100,1)
    except: vals["ten"]=None
    try:
        h = yf_hist("GC=F","1mo"); vals["gold"] = round((h.iloc[-1]-h.iloc[-2])/h.iloc[-2]*100,2)
    except: vals["gold"]=None
    red=amber=0; st={}
    for ind in RISK:
        s = risk_status(ind, vals.get(ind["key"])); st[ind["key"]]=s
        if s=="red": red+=1
        elif s=="amber": amber+=1
    lvl = "crisis" if red>=3 else "alert" if red==2 else "watch" if (red==1 or amber>=2) else "calm"
    return vals, st, lvl, red, amber

# ===========================================================================
# 추세 시그널 (S&P / 나스닥)
# ===========================================================================
def signal_for(index_ticker, etf_ticker, macro, is_nasdaq=False):
    """종합 점수 -> (label, score_ratio, detail, close). 지표 다수 종합."""
    detail = {}; score = 0.0; maxs = 0.0
    def add(name, good, weight, gtxt, btxt):
        nonlocal score, maxs
        s = weight if good else -weight
        score += s; maxs += weight
        detail[name] = (gtxt if good else btxt, round(s, 2))

    px = yf_hist(index_ticker, "1y")
    price = float(px.iloc[-1]); close = round(price, 2)
    ma50, ma200 = ma(px, 50), ma(px, 200)

    # --- 추세 (코어) ---
    add("200일선", price > ma200, 2.0, "위", "아래")
    add("50일선",  price > ma50,  1.0, "위", "아래")
    add("50/200",  ma50 > ma200,  1.0, "골든크로스", "데드크로스")
    try:
        rv = rsi(px)
        rtxt = f"{rv:.0f}" + (" 과매수" if rv > 70 else " 과매도" if rv < 30 else "")
        add("RSI", rv > 50, 0.5, rtxt, rtxt)
    except Exception: pass
    try:
        mo = momentum(px, 20)
        add("20일 모멘텀", mo > 0, 1.0, f"+{mo:.1f}%", f"{mo:.1f}%")
    except Exception: pass

    # --- 환경 ---
    vix = macro.get("vix"); vix3m = macro.get("vix3m")
    if vix is not None:
        s = 1.0 if vix < 20 else (-1.5 if vix > 30 else 0.0)
        score += s; maxs += 1.0
        detail["VIX"] = (f"{vix}", round(s, 2))
    if vix is not None and vix3m is not None:
        add("VIX 기간구조", vix < vix3m, 1.0, "콘탱고(안정)", "백워데이션(스트레스)")
    mv = macro.get("move")
    if mv is not None:
        s = 0.5 if mv < 100 else (-0.5 if mv > 130 else 0.0)
        score += s; maxs += 0.5
        detail["MOVE(채권 변동성)"] = (f"{mv}", round(s, 2))
    hy = macro.get("hyoas")
    if hy is not None:
        s = 0.5 if hy < 3.5 else (-1.0 if hy > 5 else 0.0)
        score += s; maxs += 0.5
        detail["HY 크레딧 스프레드"] = (f"{hy}%", round(s, 2))
    cv = macro.get("t10y2y")
    if cv is not None:
        add("10Y-2Y 커브", cv > 0, 0.3, f"{cv} 정상", f"{cv} 역전")
    rr = macro.get("real_chg")
    if rr is not None:
        w = 0.8 if is_nasdaq else 0.5   # 성장주(나스닥) 가중 ↑
        add("10Y 실질금리(5일)", rr < 0, w, f"{rr:+.2f}%p 하락(우호)", f"{rr:+.2f}%p 상승(역풍)")
    try:
        dxt = series_trend("DX-Y.NYB", 20)
        add("달러 추세", dxt < 0, 0.3, "약세(위험선호)", "강세(역풍)")
    except Exception: pass

    # --- 시장 폭 ---
    try:
        add("시장 폭(RSP/SPY)", trend("RSP", "SPY", 20) > 0, 0.7, "광범위", "소수 주도")
    except Exception: pass

    if not is_nasdaq:
        # --- S&P 특화: 경기(구리/금) ---
        try:
            add("경기(구리/금)", trend("HG=F", "GC=F", 20) > 0, 0.5, "확장", "둔화")
        except Exception: pass
    else:
        # --- 나스닥 특화 ---
        try:
            add("반도체 리더십(SOXX/QQQ)", trend("SOXX", "QQQ", 20) > 0, 0.7, "주도", "약세")
        except Exception: pass
        try:
            # QQEW(동일가중) vs QQQ : 동일가중이 강하면 광범위, QQQ만 강하면 메가캡 집중(취약)
            add("메가캡 집중도(QQEW/QQQ)", trend("QQEW", "QQQ", 20) > 0, 0.5, "광범위", "소수 빅테크 집중")
        except Exception: pass
        try:
            add("위험선호(BTC)", series_trend("BTC-USD", 20) > 0, 0.4, "상승(위험선호)", "하락(위험회피)")
        except Exception: pass
        try:
            add("고베타 성장(ARKK/QQQ)", trend("ARKK", "QQQ", 20) > 0, 0.4, "선호", "회피")
        except Exception: pass

    ratio = score / maxs if maxs else 0
    score100 = round((ratio + 1) * 50)   # -1~+1 -> 0~100
    if score100 >= 73: label = "STRONG BUY"
    elif score100 >= 59: label = "BUY"
    elif score100 > 41: label = "NEUTRAL"
    elif score100 > 27: label = "SELL"
    else: label = "STRONG SELL"
    return label, score100, detail, close

SIG_COLOR = {"STRONG BUY":"#2bd47e","BUY":"#3fb950","NEUTRAL":"#8b95a5",
             "SELL":"#f0813f","STRONG SELL":"#f04747"}
SIG_KO = {"STRONG BUY":"강한 불장","BUY":"불장","NEUTRAL":"중립",
          "SELL":"물장","STRONG SELL":"강한 물장"}
def bullish(label): return label in ("STRONG BUY","BUY")
def bearish(label): return label in ("SELL","STRONG SELL")

# ===========================================================================
# 승률 검증 (어제 시그널 vs 오늘 종가변화)
# ===========================================================================
def load_hist():
    try:
        with open(HIST_PATH, encoding="utf-8") as f: return json.load(f)
    except: return []

def grade_and_record(hist, today, sp, nq):
    """어제 미채점 기록을 오늘 종가로 채점 + 오늘 기록 추가."""
    sp_label, sp_ratio, _, sp_close = sp
    nq_label, nq_ratio, _, nq_close = nq
    if hist:
        prev = hist[-1]
        if prev.get("graded") is False and prev["date"] != today:
            for mk, close in (("sp", sp_close), ("nq", nq_close)):
                pl, pc = prev[mk+"_label"], prev[mk+"_close"]
                if pc and close:
                    chg = close - pc
                    if pl in ("STRONG BUY","BUY","SELL","STRONG SELL"):
                        up = chg > 0
                        hit = (up and pl in ("STRONG BUY","BUY")) or ((not up) and pl in ("SELL","STRONG SELL"))
                        prev[mk+"_hit"] = bool(hit)
            prev["graded"] = True
    hist.append(dict(date=today, graded=False,
                     sp_label=sp_label, sp_ratio=sp_ratio, sp_close=sp_close, sp_hit=None,
                     nq_label=nq_label, nq_ratio=nq_ratio, nq_close=nq_close, nq_hit=None))
    return hist[-180:]

def winrate(hist, mk):
    graded = [h for h in hist if h.get(mk+"_hit") is not None]
    if not graded: return None, 0, 0
    hits = sum(1 for h in graded if h[mk+"_hit"])
    return round(hits/len(graded)*100,1), hits, len(graded)

# ===========================================================================
# HTML
# ===========================================================================
def detail_rows(detail):
    rows=""
    for k,(txt,sc) in detail.items():
        col = "#3fb950" if sc>0 else ("#f04747" if sc<0 else "#8b95a5")
        sign = f"+{sc}" if sc>0 else f"{sc}"
        rows += f'<div class="drow"><span>{k}</span><span class="dval">{txt}</span><span class="dsc" style="color:{col}">{sign}</span></div>'
    return rows

def signal_tab(name, sig, macro, winr):
    label, score100, detail, close = sig
    color = SIG_COLOR[label]; ko = SIG_KO[label]
    wr, hits, tot = winr
    wr_txt = f"{wr}% ({hits}/{tot})" if wr is not None else "검증 누적 중 — 기록 쌓이면 표시"
    # 점수 게이지 위치 (0~100)
    return f"""
    <div class="sig-hero" style="--c:{color}">
      <div class="hero-label">{name} · 오늘 시그널</div>
      <div class="sig-label" style="color:{color}">{label}</div>
      <div class="sig-ko">{ko}</div>
      <div class="score-big" style="color:{color}">{score100}<span class="score-max">/ 100</span></div>
      <div class="score-gauge"><div class="score-fill" style="width:{score100}%;background:{color}"></div>
        <div class="score-mark" style="left:50%"></div></div>
      <div class="score-scale"><span>0 강한물장</span><span>50 중립</span><span>100 강한불장</span></div>
      <div class="sig-meta">종가 {close}</div>
    </div>
    <div class="winrate">
      <span class="wr-label">페이퍼 승률 (다음날 방향 적중)</span>
      <span class="wr-val">{wr_txt}</span>
    </div>
    <div class="detail">{detail_rows(detail)}</div>
    <div class="note">시그널은 지표 종합 점수이지 예측이 아닙니다. 승률이 충분히 쌓여 우위가 확인되기 전까지는 페이퍼(모의)로만 검증하세요.</div>
    """

def risk_tab(rvals, rst, lvl, red, amber):
    LV = {"calm":("평시","#3fb950"),"watch":("주의","#d8a322"),
          "alert":("경보","#f0813f"),"crisis":("위기","#f04747")}
    nm, col = LV[lvl]
    bt = {"green":"안정","amber":"주의","red":"점등","none":"—"}
    segs = "".join(f'<div class="sigseg {rst[i["key"]]}"></div>' for i in RISK)
    cards=""
    for ind in RISK:
        s = rst[ind["key"]]; v = rvals.get(ind["key"])
        cards += f"""<div class="rcard {s if s!='none' else ''}">
          <div class="rc-top"><span class="rc-name">{ind['name']}</span>
          <span class="badge {s}">{bt[s]}</span></div>
          <div class="rc-val">{'—' if v is None else v}<span class="u">{ind['unit']}</span></div>
          <div class="rc-thr">녹 {ind['green']} / 적 {ind['red']}</div></div>"""
    pb = {"calm":"관찰만. 트리거 미발동.","watch":"추적 강화. 매일 확인.",
          "alert":"대응 준비. 정한 매수레벨·분할 점검. 반등 이유 있는 것만. (도박 제외, 사이즈 금지)",
          "crisis":"대응 모드. 정한 트리거대로 분할. 패닉·본전심리 차단."}[lvl]
    return f"""
    <div class="sig-hero" style="--c:{col}">
      <div class="hero-label">위험 감지 · 종합 단계</div>
      <div class="sig-label" style="color:{col}">{nm}</div>
      <div class="sig-meta">점등 {red} 적색 · {amber} 황색 / 5</div>
      <div class="sigbar">{segs}</div>
    </div>
    <div class="playbook" style="border-color:{col}"><span class="pk">행동 지침</span>{pb}</div>
    <div class="rcards">{cards}</div>
    """

def render(rtab, sptab, nqtab, now, errors):
    err = ""
    if errors:
        err = '<div class="errbar">일부 수집 실패: ' + ", ".join(errors.keys()) + '</div>'
    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>시장 레이더</title>
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="#0a0e14">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="시장 레이더">
<link rel="apple-touch-icon" href="icon-180.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&family=Inter:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{{--bg:#0a0e14;--surface:#111722;--s2:#161d2a;--border:#1f2a38;--text:#c9d4e0;--muted:#6b7889;--dim:#46505f;--teal:#2bd4c0;}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;padding:0 0 60px;background-image:radial-gradient(circle at 50% -10%,rgba(43,212,192,.06),transparent 55%);min-height:100vh;}}
.wrap{{max-width:760px;margin:0 auto;padding:0 16px;}}
header{{padding:24px 0 14px;}}
.eyebrow{{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.28em;text-transform:uppercase;color:var(--teal);margin-bottom:6px;}}
h1{{font-family:'Oswald',sans-serif;font-weight:500;font-size:clamp(26px,7vw,38px);text-transform:uppercase;}}
.stamp{{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--muted);margin-top:6px;}}
.errbar{{background:rgba(240,71,71,.1);color:#f04747;font-family:'IBM Plex Mono',monospace;font-size:11px;padding:8px 12px;border-radius:8px;margin-bottom:12px;}}
.tabs{{display:flex;gap:6px;margin:14px 0 20px;}}
.tab{{flex:1;font-family:'Oswald',sans-serif;font-size:15px;text-transform:uppercase;letter-spacing:.05em;text-align:center;padding:12px 6px;background:var(--surface);border:1px solid var(--border);border-radius:10px;cursor:pointer;color:var(--muted);transition:all .2s;}}
.tab.active{{color:var(--text);border-color:var(--teal);background:var(--s2);}}
.panel{{display:none;}} .panel.active{{display:block;}}
.sig-hero{{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:22px;margin-bottom:12px;position:relative;overflow:hidden;}}
.sig-hero::before{{content:"";position:absolute;inset:0;background:var(--c);opacity:.06;}}
.hero-label{{position:relative;font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin-bottom:8px;}}
.sig-label{{position:relative;font-family:'Oswald',sans-serif;font-weight:600;font-size:clamp(34px,10vw,56px);line-height:1;text-transform:uppercase;}}
.sig-ko{{position:relative;font-family:'Oswald',sans-serif;font-size:20px;color:var(--text);margin-top:4px;}}
.sig-meta{{position:relative;font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--muted);margin-top:10px;}}
.score-big{{position:relative;font-family:'Oswald',sans-serif;font-weight:600;font-size:46px;line-height:1;margin-top:10px;}}
.score-max{{font-size:16px;color:var(--muted);margin-left:6px;}}
.score-gauge{{position:relative;height:9px;background:var(--s2);border:1px solid var(--border);border-radius:5px;margin-top:10px;overflow:hidden;}}
.score-fill{{position:absolute;left:0;top:0;bottom:0;border-radius:5px;opacity:.85;}}
.score-mark{{position:absolute;top:-2px;bottom:-2px;width:1px;background:var(--muted);}}
.score-scale{{position:relative;display:flex;justify-content:space-between;font-family:'IBM Plex Mono',monospace;font-size:9.5px;color:var(--dim);margin-top:5px;}}
.sigbar{{position:relative;display:flex;gap:6px;margin-top:16px;}}
.sigseg{{flex:1;height:8px;border-radius:3px;background:var(--s2);border:1px solid var(--border);}}
.sigseg.green{{background:#3fb950;border-color:#3fb950;}} .sigseg.amber{{background:#d8a322;border-color:#d8a322;}} .sigseg.red{{background:#f04747;border-color:#f04747;}}
.winrate{{display:flex;justify-content:space-between;align-items:center;background:var(--s2);border:1px solid var(--border);border-radius:10px;padding:13px 16px;margin-bottom:12px;}}
.wr-label{{font-size:12px;color:var(--muted);}} .wr-val{{font-family:'IBM Plex Mono',monospace;font-weight:600;color:var(--teal);}}
.detail{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:8px 16px;}}
.drow{{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--border);font-size:13px;}}
.drow:last-child{{border-bottom:none;}}
.drow > span:first-child{{color:var(--muted);flex:1;}}
.dval{{font-family:'IBM Plex Mono',monospace;color:var(--text);}}
.dsc{{font-family:'IBM Plex Mono',monospace;font-size:12px;width:48px;text-align:right;}}
.note{{font-size:11.5px;color:var(--dim);line-height:1.6;margin-top:14px;}}
.playbook{{background:var(--s2);border-left:3px solid;border-radius:0 8px 8px 0;padding:13px 16px;margin-bottom:18px;font-size:13.5px;}}
.pk{{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);display:block;margin-bottom:4px;}}
.rcards{{display:grid;grid-template-columns:1fr 1fr;gap:10px;}}
.rcard{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px;}}
.rcard.green{{border-color:rgba(63,185,80,.4);}} .rcard.amber{{border-color:rgba(216,163,34,.45);}} .rcard.red{{border-color:rgba(240,71,71,.5);}}
.rc-top{{display:flex;justify-content:space-between;align-items:center;gap:8px;}}
.rc-name{{font-family:'Oswald',sans-serif;font-size:15px;}}
.badge{{font-family:'IBM Plex Mono',monospace;font-size:9px;font-weight:600;letter-spacing:.1em;padding:3px 7px;border-radius:5px;text-transform:uppercase;}}
.badge.green{{background:rgba(63,185,80,.12);color:#3fb950;}} .badge.amber{{background:rgba(216,163,34,.12);color:#d8a322;}} .badge.red{{background:rgba(240,71,71,.12);color:#f04747;}} .badge.none{{background:var(--s2);color:var(--dim);}}
.rc-val{{font-family:'IBM Plex Mono',monospace;font-size:24px;font-weight:600;margin:8px 0 2px;}} .rc-val .u{{font-size:12px;color:var(--muted);margin-left:4px;}}
.rc-thr{{font-family:'IBM Plex Mono',monospace;font-size:10px;color:var(--dim);}}
.foot{{margin-top:26px;padding-top:16px;border-top:1px solid var(--border);font-size:11.5px;color:var(--dim);line-height:1.6;}}
@media(max-width:480px){{.rcards{{grid-template-columns:1fr;}}}}
</style></head><body><div class="wrap">
<header><div class="eyebrow">Market Radar · Auto</div><h1>시장 레이더</h1><div class="stamp">자동 수집 · {now}</div></header>
{err}
<div class="tabs">
  <div class="tab active" data-t="risk">위험감지</div>
  <div class="tab" data-t="sp">S&amp;P 500</div>
  <div class="tab" data-t="nq">나스닥</div>
</div>
<div class="panel active" id="p-risk">{rtab}</div>
<div class="panel" id="p-sp">{sptab}</div>
<div class="panel" id="p-nq">{nqtab}</div>
<div class="foot"><b>시그널은 지표 종합 점수이며 매매 신호가 아닙니다.</b> 충분한 페이퍼 검증으로 승률·우위가 확인되기 전엔 실거래에 쓰지 마세요. 종가 기준이라 장중 실시간과 차이가 있습니다.</div>
</div>
<script>
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>{{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');
  document.getElementById('p-'+t.dataset.t).classList.add('active');
}}));
</script></body></html>"""

# ===========================================================================
def main():
    print("시장 레이더 수집 중...")
    macro, merr = collect_macro()
    rvals, rst, lvl, red, amber = collect_risk(macro)
    errors = dict(merr)
    try:
        sp = signal_for("SPY", "SPY", macro, is_nasdaq=False)
    except Exception as ex:
        errors["sp"]=str(ex); sp=("NEUTRAL",0,{},None)
    try:
        nq = signal_for("QQQ", "QQQ", macro, is_nasdaq=True)
    except Exception as ex:
        errors["nq"]=str(ex); nq=("NEUTRAL",0,{},None)

    today = dt.datetime.now().strftime("%Y-%m-%d")
    hist = load_hist()
    hist = grade_and_record(hist, today, sp, nq)
    with open(HIST_PATH,"w",encoding="utf-8") as f: json.dump(hist,f,ensure_ascii=False,indent=1)

    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    rtab  = risk_tab(rvals, rst, lvl, red, amber)
    sptab = signal_tab("S&P 500 · SPY", sp, macro, winrate(hist,"sp"))
    nqtab = signal_tab("나스닥 100 · QQQ", nq, macro, winrate(hist,"nq"))
    html = render(rtab, sptab, nqtab, now, errors)
    with open(OUT_HTML,"w",encoding="utf-8") as f: f.write(html)
    write_pwa_assets()

    print(f"  위험: {LEVELS_NM(lvl)} (적{red}/황{amber})")
    print(f"  S&P: {sp[0]} ({sp[1]:+}) / 나스닥: {nq[0]} ({nq[1]:+})")
    if errors: print(f"  실패: {list(errors.keys())}")
    print(f"  생성: {OUT_HTML}")

def LEVELS_NM(l): return {"calm":"평시","watch":"주의","alert":"경보","crisis":"위기"}[l]

if __name__ == "__main__":
    main()
