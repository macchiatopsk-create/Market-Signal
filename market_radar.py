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

    print(f"  위험: {LEVELS_NM(lvl)} (적{red}/황{amber})")
    print(f"  S&P: {sp[0]} ({sp[1]:+}) / 나스닥: {nq[0]} ({nq[1]:+})")
    if errors: print(f"  실패: {list(errors.keys())}")
    print(f"  생성: {OUT_HTML}")

def LEVELS_NM(l): return {"calm":"평시","watch":"주의","alert":"경보","crisis":"위기"}[l]

if __name__ == "__main__":
    main()
