#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
시장 레이더 — 위기감지 + S&P500 + 나스닥 (자동 수집)
A안: 각 종목 탭에 'TODAY RISK BIAS' 카드를 얹고, 위험이 추세 점수를 게이트한다.
  · 추세 점수(0~100): 높을수록 불장 (기존 로직 유지)
  · 위험 점수(0~100): 높을수록 위험 (전일저가 이탈/1D Bearish CHoCH/상대약세/VIX/당일하락폭/Premium거부)
  · 최종 판정 = 추세 라벨에 위험 게이트 적용
        LOW(0~30)      → 추세 그대로
        ELEVATED(31~60)→ 매수 라벨 한 단계 강등 + 주의
        HIGH(61~80)    → 매수 차단 → NO ENTRY
        SELL ONLY(81+) → 무조건 SELL ONLY

데이터: yfinance(키 불필요) + FRED(무료 키)
실행: python market_radar.py  →  dashboard.html + radar_history.json
"""
import os, sys, json, time, struct, zlib, datetime as dt
from zoneinfo import ZoneInfo
import requests
try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("필요: pip install yfinance requests pandas"); sys.exit(1)

FRED_API_KEY = os.environ.get("FRED_API_KEY", "여기에_FRED_키_붙여넣기")
NY = ZoneInfo("America/New_York")          # GitHub 러너는 UTC라 거래일 기준은 뉴욕으로
BASE = os.path.dirname(os.path.abspath(__file__))
OUT_HTML = os.path.join(BASE, "dashboard.html")
HIST_PATH = os.path.join(BASE, "radar_history.json")

# ===========================================================================
# PWA 자산 — 단색 PNG를 코드로 생성 (형 원본 아이콘 쓰려면 아래 _png 대신 base64 사용)
# ===========================================================================
def _png(size, rgb=(43, 212, 192)):
    """의존성 없는 최소 PNG 생성 (단색)."""
    w = h = size
    raw = bytearray()
    row = bytes(rgb) * w
    for _ in range(h):
        raw.append(0)            # filter type 0
        raw.extend(row)
    def chunk(typ, data):
        body = typ + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)
    sig  = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)   # 8-bit RGB
    idat = zlib.compress(bytes(raw), 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")

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
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("icon-180.png", 180)):
        with open(os.path.join(BASE, name), "wb") as f:
            f.write(_png(size))
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

def fred_change(series_id, days=5):
    url = "https://api.stlouisfed.org/fred/series/observations"
    p = dict(series_id=series_id, api_key=FRED_API_KEY, file_type="json",
             sort_order="desc", limit=40)
    r = requests.get(url, params=p, timeout=30); r.raise_for_status()
    vals = [float(o["value"]) for o in r.json().get("observations", [])
            if o["value"] not in (".", "", None)]
    if len(vals) < days + 1: raise ValueError(series_id)
    return round(vals[0] - vals[days], 2)

def yf_hist(ticker, period="1y", tries=3):
    err = None
    for i in range(tries):
        try:
            h = yf.Ticker(ticker).history(period=period)["Close"].dropna()
            if len(h) >= 2:
                return h
        except Exception as ex:
            err = ex
        time.sleep(2 * (i + 1))
    raise ValueError(f"{ticker}: {err}")

def yf_ohlc(ticker, period="1y", tries=3):
    """위험 모델용 OHLCV (전일저가/스윙/Premium/거래량 필터에 필요)."""
    err = None
    for i in range(tries):
        try:
            df = yf.Ticker(ticker).history(period=period)[["Open", "High", "Low", "Close", "Volume"]].dropna()
            if len(df) >= 30:
                return df
        except Exception as ex:
            err = ex
        time.sleep(2 * (i + 1))
    raise ValueError(f"{ticker} OHLC: {err}")

def last(ticker):
    return float(yf_hist(ticker, "1mo").iloc[-1])

def ma(series, n):
    return float(series.tail(n).mean()) if len(series) >= n else float(series.mean())

def trend(ticker_a, ticker_b, n=20):
    """A/B 비율의 n일 추세 부호 (+이면 A가 상대적으로 강해지는 중)."""
    a = yf_hist(ticker_a, "3mo"); b = yf_hist(ticker_b, "3mo")
    m = min(len(a), len(b)); a, b = a.tail(m), b.tail(m)
    ratio = (a.values / b.values)
    if len(ratio) < n + 1: n = len(ratio) - 1
    return ratio[-1] - ratio[-1 - n]

def series_trend(ticker, n=20):
    h = yf_hist(ticker, "3mo")
    if len(h) < n + 1: n = len(h) - 1
    return float(h.iloc[-1] - h.iloc[-1 - n])

def rsi(series, n=14):
    d = series.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / dn.replace(0, 1e-9)
    return float((100 - 100 / (1 + rs)).iloc[-1])

def momentum(series, n=20):
    if len(series) < n + 1: n = len(series) - 1
    return float((series.iloc[-1] / series.iloc[-1 - n] - 1) * 100)

# ===========================================================================
# SMC 구조: 스윙 피벗 → BOS / CHoCH (일봉)
# ===========================================================================
def _pivots(df, L=2):
    H = df["High"].values; Lw = df["Low"].values
    n = len(df); highs = []; lows = []
    for i in range(L, n - L):
        if H[i] == max(H[i - L:i + L + 1]):  highs.append((i, float(H[i])))
        if Lw[i] == min(Lw[i - L:i + L + 1]): lows.append((i, float(Lw[i])))
    return highs, lows

def compute_structure(df):
    """반환: trend(up/down/range), bearish_choch, bearish_bos, 직전 스윙 저/고점."""
    out = dict(trend="range", bearish_choch=False, bearish_bos=False,
               last_swing_low=None, last_swing_high=None)
    highs, lows = _pivots(df, 2)
    if len(highs) < 2 or len(lows) < 2:
        return out
    sh = [v for _, v in highs]; sl = [v for _, v in lows]
    out["last_swing_high"] = sh[-1]; out["last_swing_low"] = sl[-1]
    higher = sh[-1] > sh[-2] and sl[-1] > sl[-2]
    lower  = sh[-1] < sh[-2] and sl[-1] < sl[-2]
    out["trend"] = "up" if higher else "down" if lower else "range"
    close = float(df["Close"].iloc[-1])
    if close < sl[-1]:                       # 종가가 직전 스윙 저점 하향 돌파
        if out["trend"] in ("up", "range"):
            out["bearish_choch"] = True       # 상승/횡보 중 이탈 = 전환
        else:
            out["bearish_bos"] = True         # 하락 중 이탈 = 지속
    return out

def premium_zone(df, lookback=60):
    seg = df.tail(lookback)
    hi = float(seg["High"].max()); lo = float(seg["Low"].min())
    close = float(df["Close"].iloc[-1])
    if hi == lo: return "eq", 0.5
    pos = (close - lo) / (hi - lo)
    z = "premium" if pos >= 0.66 else ("discount" if pos <= 0.33 else "eq")
    return z, pos

def liquidity_sweep(df, lookback=20):
    """Buy-side 유동성 스윕: 최근 고점 위로 뚫었다가 종가가 다시 그 아래로 마감 (가짜 돌파/롱 청산)."""
    if len(df) < lookback + 1:
        return False
    prior_high = float(df["High"].iloc[-(lookback + 1):-1].max())   # 오늘 제외 직전 N일 고가
    today_high = float(df["High"].iloc[-1])
    today_close = float(df["Close"].iloc[-1])
    return today_high > prior_high and today_close < prior_high

def weekly_structure(df_daily):
    """일봉을 주봉으로 리샘플해 Weekly BOS/CHoCH 판정."""
    w = (df_daily.resample("W-FRI")
                 .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
                 .dropna())
    if len(w) < 10:
        return dict(trend="range", bearish_choch=False, bearish_bos=False,
                    last_swing_low=None, last_swing_high=None)
    return compute_structure(w)

# ===========================================================================
# 시장 내부 (선행 신호 · 두 탭 공통, 1회 계산)
# ===========================================================================
def collect_market_internals():
    """VIX보다 먼저 깨지는 선행 지표들 (전부 yfinance 무료)."""
    m = dict(iwm_weak=False, iwm_strong=False, credit_off=False, credit_on=False,
             vix_up=None, iwm_val=None, credit_val=None)
    try:                                  # 소형주 상대강도 (내부 균열)
        v = trend("IWM", "SPY", 20); m["iwm_val"] = round(v, 4)
        m["iwm_weak"] = v < 0; m["iwm_strong"] = v > 0
    except Exception: pass
    try:                                  # 신용 (정크 vs 국채) — HY OAS보다 빠름
        v = trend("HYG", "IEF", 20); m["credit_val"] = round(v, 4)
        m["credit_off"] = v < 0; m["credit_on"] = v > 0
    except Exception: pass
    try:                                  # VIX 방향 (절대값보다 방향이 중요)
        h = yf_hist("^VIX", "1mo"); m["vix_up"] = bool(h.iloc[-1] > h.iloc[-2])
    except Exception: pass
    return m

# ===========================================================================
# 위험 Bias (종목별) + 게이트
# ===========================================================================
RISK_TIER_COLOR = {"LOW": "#3fb950", "ELEVATED": "#d8a322", "HIGH": "#f0813f", "SELL ONLY": "#f04747"}

def _empty_risk():
    return dict(score=0, tier="LOW", factors=[], change=0.0, zone="eq", trend="range",
                close=None, close_pos=0.5, cond={})

def risk_tier(score):
    return ("SELL ONLY" if score >= 81 else "HIGH" if score >= 61
            else "ELEVATED" if score >= 31 else "LOW")

def compute_risk_bias(ticker, macro, market):
    df = yf_ohlc(ticker, "2y")
    close = float(df["Close"].iloc[-1])
    prev_low = float(df["Low"].iloc[-2])
    prev_close = float(df["Close"].iloc[-2])
    today_open = float(df["Open"].iloc[-1])
    today_high = float(df["High"].iloc[-1])
    today_low = float(df["Low"].iloc[-1])
    chg = (close / prev_close - 1) * 100
    red = close < today_open
    rng = today_high - today_low
    close_pos = (close - today_low) / rng if rng > 0 else 0.5     # 0=저가권, 1=고가권
    score = 0; factors = []; cond = {}

    # === 선행(leading) 지표 중심 ===
    cond["prev_low_break"] = close < prev_low
    if cond["prev_low_break"]:
        score += 25; factors.append(("전일 저가 이탈", f"{close:.2f} < {prev_low:.2f}", 25))

    cond["sweep"] = liquidity_sweep(df)
    if cond["sweep"]:
        score += 15; factors.append(("Buy-side 유동성 스윕", "전고 돌파 후 회귀", 15))

    st = compute_structure(df)
    cond["d_bear"] = st["bearish_choch"] or st["bearish_bos"]
    if st["bearish_choch"]:
        score += 25; factors.append(("1D Bearish CHoCH", f"스윙저점 {st['last_swing_low']:.2f} 이탈", 25))
    elif st["bearish_bos"]:
        score += 15; factors.append(("1D Bearish BOS", "하락추세 지속", 15))

    wk = weekly_structure(df)
    cond["w_bear"] = wk["bearish_choch"] or wk["bearish_bos"]
    if wk["bearish_choch"]:
        score += 40; factors.append(("Weekly Bearish CHoCH", "주간 구조 전환", 40))
    elif wk["bearish_bos"]:
        score += 20; factors.append(("Weekly Bearish BOS", "주간 하락 지속", 20))

    cond["iwm_weak"]   = bool(market.get("iwm_weak"))
    cond["iwm_strong"] = bool(market.get("iwm_strong"))
    if cond["iwm_weak"]:
        score += 10; factors.append(("IWM/SPY 소형주 약세", "내부 균열", 10))

    cond["credit_off"] = bool(market.get("credit_off"))
    cond["credit_on"]  = bool(market.get("credit_on"))
    if cond["credit_off"]:
        score += 10; factors.append(("HYG/IEF 신용위험", "정크본드 회피", 10))

    vix = macro.get("vix")
    cond["vix_up"] = market.get("vix_up")
    cond["vix_up_red"] = bool(market.get("vix_up")) and red
    if vix is not None:
        if vix > 25:   score += 20; factors.append(("VIX > 25", f"{vix}", 20))
        elif vix > 20: score += 10; factors.append(("VIX > 20", f"{vix}", 10))

    zone, pos = premium_zone(df)
    cond["premium_reject"] = (zone == "premium" and red)
    if cond["premium_reject"]:
        score += 10; factors.append(("Premium 거부", "상단존 음봉 마감", 10))

    # 종가 위치 + 추가 필터 — 점수엔 안 넣고 '다음날 엣지' 조건으로만 사용
    cond["close_low"]  = close_pos <= 0.25
    cond["close_high"] = close_pos >= 0.75
    cond["red"]  = red
    cond["zone"] = zone
    prev_high = float(df["High"].iloc[-2])
    cond["inside_day"] = (today_high < prev_high) and (today_low > prev_low)        # 변동성 수축 = 정보 없는 날
    cond["mid_recovery"] = (chg < 0 or red) and close_pos >= 0.5                     # 하락했는데 중간값 위 회복 (강한 반대)
    if "Volume" in df.columns:
        vol = float(df["Volume"].iloc[-1]); vol_ma = float(df["Volume"].tail(20).mean())
        cond["weak_vol_down"] = (red and chg <= -1.0 and vol_ma > 0 and vol < 0.8 * vol_ma)  # 장대음봉인데 거래량 약함
    else:
        cond["weak_vol_down"] = False

    score = min(score, 100)
    return dict(score=score, tier=risk_tier(score), factors=factors,
                change=round(chg, 2), zone=zone, trend=st["trend"],
                close=round(close, 2), close_pos=round(close_pos, 2), cond=cond)

def gate_label(trend_label, tier):
    """추세 라벨에 위험 게이트 적용 → (최종 판정, 사유)."""
    if tier == "SELL ONLY":
        return "SELL ONLY", "위험 최고조 — 매수 금지"
    if tier == "HIGH":
        if trend_label in ("STRONG BUY", "BUY", "NEUTRAL"):
            return "NO ENTRY", "위험 높음 — 진입 보류"
        return trend_label, "위험 높음"
    if tier == "ELEVATED":
        downgrade = {"STRONG BUY": "BUY", "BUY": "NEUTRAL"}
        if trend_label in downgrade:
            return downgrade[trend_label] + " ⚠", "위험 상승 — 한 단계 강등"
        return trend_label, "위험 상승 — 주의"
    return trend_label, ""   # LOW

# ===========================================================================
# NEXT DAY EDGE — 컨플루언스(조건 조합) 기반. 반대 조건은 net 을 깎아 등급을 낮춘다.
#   · 가짜 % 안 씀: net(조건 차이) + 확신도 등급만. 실측 % 는 history 채점 후.
#   · Inside Day → 기권 / 거래량 안 붙은 음봉 → 약세 확신 −1 / 중간값 회복 → 약세 High 금지
#   · OPP_W 를 1.5 로 올리면 더 깐깐(빈도↓ 정확도↑)
# ===========================================================================
OPP_W = 1.0          # 일반 반대 조건 가중 (형 예시 4-clean=High 에 맞춰 1.0; 1.5 로 올리면 더 엄격)
STRONG_OPP_W = 2.0   # 강한 반대(중간값 회복) 가중

def compute_next_day_edge(risk):
    c = risk.get("cond", {})
    if c.get("inside_day"):                          # 변동성 수축 = 정보 없는 날 → 기권
        return dict(show=False, direction="NO EDGE", confidence="Inside Day",
                    action="관망 (변동성 수축)", matched=[], bear=0, bull=0, net=0)

    bear, bull, strong = [], [], []
    if c.get("prev_low_break"):              bear.append("전일저가 이탈")
    if c.get("d_bear"):                      bear.append("Daily 하락구조")
    if c.get("w_bear"):                      bear.append("Weekly 하락구조")
    if c.get("close_low") and c.get("red"):  bear.append("종가 저가권 마감")
    if c.get("iwm_weak"):                    bear.append("IWM/SPY 약세")
    if c.get("credit_off"):                  bear.append("HYG/IEF 위험회피")
    if c.get("vix_up_red"):                  bear.append("VIX 상승+주가 하락")
    if c.get("premium_reject"):              bear.append("Premium 거부")
    if c.get("sweep"):                       bear.append("유동성 스윕")
    if c.get("close_high"):                  bull.append("종가 고가권 마감")
    if (c.get("vix_up") is False) and c.get("red"): bull.append("VIX 하락(과매도 반등)")
    if c.get("zone") == "discount":          bull.append("Discount 구간")
    if c.get("iwm_strong"):                  bull.append("IWM/SPY 강세")
    if c.get("credit_on"):                   bull.append("신용 우호(HYG)")
    if c.get("mid_recovery"):                strong.append("종가 중간값 회복")

    nb = len(bear); nl = len(bull); ns = len(strong)
    opp_total = nl + ns                       # 표시용 반대 개수
    opp_pts = OPP_W * nl + STRONG_OPP_W * ns  # 반대 가중합 (net 깎기)

    bearish = nb >= opp_total and nb >= 3
    bullish = opp_total > nb and opp_total >= 3
    if bearish:
        net = nb - opp_pts
        direction = "BEARISH"; matched = bear
    elif bullish:
        net = opp_total - OPP_W * nb
        direction = "BULLISH"; matched = bull + strong
    else:
        return dict(show=False, direction="NO EDGE", confidence="—",
                    action="관망 (엣지 없음)", matched=[], bear=nb, bull=opp_total, net=0)

    tier = 2 if net >= 3 else 1 if net >= 2 else 0          # 2=High, 1=Medium, 0=없음
    if direction == "BEARISH" and c.get("mid_recovery"):    # 중간값 회복 → 약세 High 금지
        tier = min(tier, 1)
    if direction == "BEARISH" and c.get("weak_vol_down"):   # 거래량 안 붙은 음봉 → 확신 한 칸 ↓
        tier -= 1
    if tier <= 0:
        return dict(show=False, direction="NO EDGE", confidence="저확신/충돌",
                    action="관망 (반대신호로 상쇄)", matched=matched, bear=nb, bull=opp_total, net=round(net, 1))
    conf = "High" if tier == 2 else "Medium"

    if direction == "BEARISH":
        action = "No Long / Sell Rally" if conf == "High" else "롱 자제 / 관망"
    else:
        action = "반등 후보 / 분할 관심" if conf == "High" else "관망 (약한 반등)"
    return dict(show=True, direction=direction, confidence=conf, action=action,
                matched=matched, bear=nb, bull=opp_total, net=round(net, 1))

# ===========================================================================
# 환경 데이터
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
    try: d["real_chg"] = fred_change("DFII10", 5)
    except Exception as ex: e["real_chg"] = str(ex)
    return d, e

# ===========================================================================
# 위험감지 5신호 (기존)
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
    if ind["dir"] in ("high", "risePct"):
        return "green" if v < ind["green"] else ("amber" if v < ind["red"] else "red")
    return "green" if v > ind["green"] else ("amber" if v > ind["red"] else "red")

def collect_risk(macro):
    vals = {"hyoas": macro.get("hyoas"), "vix": macro.get("vix")}
    try:
        h = yf_hist("DX-Y.NYB", "1mo"); vals["dxy"] = round((h.iloc[-1] - h.iloc[-6]) / h.iloc[-6] * 100, 2)
    except: vals["dxy"] = None
    try:
        h = yf_hist("^TNX", "1mo"); vals["ten"] = round((h.iloc[-1] - h.iloc[-6]) * 100, 1)
    except: vals["ten"] = None
    try:
        h = yf_hist("GC=F", "1mo"); vals["gold"] = round((h.iloc[-1] - h.iloc[-2]) / h.iloc[-2] * 100, 2)
    except: vals["gold"] = None
    red = amber = 0; st = {}
    for ind in RISK:
        s = risk_status(ind, vals.get(ind["key"])); st[ind["key"]] = s
        if s == "red": red += 1
        elif s == "amber": amber += 1
    lvl = "crisis" if red >= 3 else "alert" if red == 2 else "watch" if (red == 1 or amber >= 2) else "calm"
    return vals, st, lvl, red, amber

# ===========================================================================
# 추세 시그널 (기존 로직 유지)
# ===========================================================================
def signal_for(index_ticker, etf_ticker, macro, is_nasdaq=False):
    detail = {}; score = 0.0; maxs = 0.0
    def add(name, good, weight, gtxt, btxt):
        nonlocal score, maxs
        s = weight if good else -weight
        score += s; maxs += weight
        detail[name] = (gtxt if good else btxt, round(s, 2))

    px = yf_hist(index_ticker, "1y")
    price = float(px.iloc[-1]); close = round(price, 2)
    ma50, ma200 = ma(px, 50), ma(px, 200)

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

    vix = macro.get("vix"); vix3m = macro.get("vix3m")
    if vix is not None:
        s = 1.0 if vix < 20 else (-1.5 if vix > 30 else 0.0)
        score += s; maxs += 1.0; detail["VIX"] = (f"{vix}", round(s, 2))
    if vix is not None and vix3m is not None:
        add("VIX 기간구조", vix < vix3m, 1.0, "콘탱고(안정)", "백워데이션(스트레스)")
    mv = macro.get("move")
    if mv is not None:
        s = 0.5 if mv < 100 else (-0.5 if mv > 130 else 0.0)
        score += s; maxs += 0.5; detail["MOVE(채권 변동성)"] = (f"{mv}", round(s, 2))
    hy = macro.get("hyoas")
    if hy is not None:
        s = 0.5 if hy < 3.5 else (-1.0 if hy > 5 else 0.0)
        score += s; maxs += 0.5; detail["HY 크레딧 스프레드"] = (f"{hy}%", round(s, 2))
    cv = macro.get("t10y2y")
    if cv is not None:
        add("10Y-2Y 커브", cv > 0, 0.3, f"{cv} 정상", f"{cv} 역전")
    rr = macro.get("real_chg")
    if rr is not None:
        w = 0.8 if is_nasdaq else 0.5
        add("10Y 실질금리(5일)", rr < 0, w, f"{rr:+.2f}%p 하락(우호)", f"{rr:+.2f}%p 상승(역풍)")
    try:
        dxt = series_trend("DX-Y.NYB", 20)
        add("달러 추세", dxt < 0, 0.3, "약세(위험선호)", "강세(역풍)")
    except Exception: pass
    try:
        add("시장 폭(RSP/SPY)", trend("RSP", "SPY", 20) > 0, 0.7, "광범위", "소수 주도")
    except Exception: pass

    if not is_nasdaq:
        try: add("경기(구리/금)", trend("HG=F", "GC=F", 20) > 0, 0.5, "확장", "둔화")
        except Exception: pass
    else:
        try: add("반도체 리더십(SOXX/QQQ)", trend("SOXX", "QQQ", 20) > 0, 0.7, "주도", "약세")
        except Exception: pass
        try: add("메가캡 집중도(QQEW/QQQ)", trend("QQEW", "QQQ", 20) > 0, 0.5, "광범위", "소수 빅테크 집중")
        except Exception: pass
        try: add("위험선호(BTC)", series_trend("BTC-USD", 20) > 0, 0.4, "상승(위험선호)", "하락(위험회피)")
        except Exception: pass
        try: add("고베타 성장(ARKK/QQQ)", trend("ARKK", "QQQ", 20) > 0, 0.4, "선호", "회피")
        except Exception: pass

    ratio = score / maxs if maxs else 0
    score100 = round((ratio + 1) * 50)
    if score100 >= 73:   label = "STRONG BUY"
    elif score100 >= 59: label = "BUY"
    elif score100 > 41:  label = "NEUTRAL"
    elif score100 > 27:  label = "SELL"
    else:                label = "STRONG SELL"
    return label, score100, detail, close

SIG_COLOR = {"STRONG BUY":"#2bd47e","BUY":"#3fb950","NEUTRAL":"#8b95a5",
             "SELL":"#f0813f","STRONG SELL":"#f04747"}
SIG_KO = {"STRONG BUY":"강한 불장","BUY":"불장","NEUTRAL":"중립",
          "SELL":"물장","STRONG SELL":"강한 물장"}

def verdict_color(v):
    if v.startswith("NO ENTRY"):  return "#f0813f"
    if v.startswith("SELL ONLY"): return "#f04747"
    return SIG_COLOR.get(v.replace(" ⚠", ""), "#8b95a5")

# ===========================================================================
# 승률 검증 (기존)
# ===========================================================================
def load_hist():
    try:
        with open(HIST_PATH, encoding="utf-8") as f: return json.load(f)
    except: return []

def grade_and_record(hist, today, sp, nq, edges):
    sp_label, sp_ratio, _, sp_close = sp
    nq_label, nq_ratio, _, nq_close = nq
    if hist:
        prev = hist[-1]
        if prev.get("graded") is False and prev["date"] != today:
            for mk, close in (("sp", sp_close), ("nq", nq_close)):
                pl, pc = prev[mk + "_label"], prev[mk + "_close"]
                if pc and close:
                    chg = close - pc
                    if pl in ("STRONG BUY", "BUY", "SELL", "STRONG SELL"):
                        up = chg > 0
                        hit = (up and pl in ("STRONG BUY", "BUY")) or ((not up) and pl in ("SELL", "STRONG SELL"))
                        prev[mk + "_hit"] = bool(hit)
                # NEXT DAY EDGE 채점 (NO EDGE 는 채점 안 함)
                ed = prev.get(mk + "_edge")
                if pc and close and ed in ("BEARISH", "BULLISH"):
                    up = close > pc
                    prev[mk + "_edge_hit"] = bool((up and ed == "BULLISH") or ((not up) and ed == "BEARISH"))
            prev["graded"] = True
    today_row = dict(date=today, graded=False,
                     sp_label=sp_label, sp_ratio=sp_ratio, sp_close=sp_close, sp_hit=None,
                     nq_label=nq_label, nq_ratio=nq_ratio, nq_close=nq_close, nq_hit=None,
                     sp_edge=edges.get("sp", "NO EDGE"), sp_edge_conf=edges.get("sp_conf", "—"), sp_edge_hit=None,
                     nq_edge=edges.get("nq", "NO EDGE"), nq_edge_conf=edges.get("nq_conf", "—"), nq_edge_hit=None)
    if hist and hist[-1].get("date") == today:
        hist[-1].update(today_row)     # 같은 날 재실행이면 덮어쓰기 (cron 2회/일 중복 방지)
    else:
        hist.append(today_row)
    return hist[-180:]

def winrate(hist, mk):
    graded = [h for h in hist if h.get(mk + "_hit") is not None]
    if not graded: return None, 0, 0
    hits = sum(1 for h in graded if h[mk + "_hit"])
    return round(hits / len(graded) * 100, 1), hits, len(graded)

def edge_winrate(hist, mk, conf=None):
    graded = [h for h in hist if h.get(mk + "_edge_hit") is not None
              and (conf is None or h.get(mk + "_edge_conf") == conf)]
    if not graded: return None, 0, 0
    hits = sum(1 for h in graded if h[mk + "_edge_hit"])
    return round(hits / len(graded) * 100, 1), hits, len(graded)

# ===========================================================================
# HTML
# ===========================================================================
def detail_rows(detail):
    rows = ""
    for k, (txt, sc) in detail.items():
        col = "#3fb950" if sc > 0 else ("#f04747" if sc < 0 else "#8b95a5")
        sign = f"+{sc}" if sc > 0 else f"{sc}"
        rows += f'<div class="drow"><span>{k}</span><span class="dval">{txt}</span><span class="dsc" style="color:{col}">{sign}</span></div>'
    return rows

def build_tab_data(ticker, trend_sig, risk):
    label, score100, detail, close = trend_sig
    verdict, reason = gate_label(label, risk["tier"])
    edge = compute_next_day_edge(risk)
    return dict(label=label, score=score100, detail=detail, close=close,
                risk=risk, verdict=verdict, reason=reason, vcolor=verdict_color(verdict),
                edge=edge)

def edge_card(edge, ewr_high, ewr_all):
    """NEXT DAY EDGE 카드. 가짜 % 안 씀 — 조건/확신도만. 실측 승률은 High/전체 분리 표시."""
    hw, hh, ht = ewr_high; aw, ah, at = ewr_all
    parts = []
    if hw is not None and ht >= 10: parts.append(f"고확신 {hw}% ({hh}/{ht})")
    if aw is not None and at >= 10: parts.append(f"전체 {aw}% ({ah}/{at})")
    wr_txt = " · ".join(parts) if parts else (f"누적 {at}건 — 10건↑ 쌓이면 실측 승률" if at else "누적 시작 — 채점 전")
    if not edge["show"]:
        return f"""<div class="edge-hero noedge">
          <div class="hero-label">NEXT DAY EDGE</div>
          <div class="edge-dir" style="color:#6b7889">NO EDGE</div>
          <div class="edge-meta">조건 {edge['bear']}↓ / {edge['bull']}↑ — 엣지 없음, 관망</div>
          <div class="edge-wr">{wr_txt}</div>
        </div>"""
    col = "#f04747" if edge["direction"] == "BEARISH" else "#2bd47e"
    ms = "".join(f'<li>{m}</li>' for m in edge["matched"])
    return f"""<div class="edge-hero" style="--c:{col}">
      <div class="hero-label">NEXT DAY EDGE</div>
      <div class="edge-dir" style="color:{col}">{edge['direction']}</div>
      <div class="edge-meta">Confidence: {edge['confidence']} · 조건 {edge['bear']}↓ / {edge['bull']}↑ (net {edge['net']})</div>
      <div class="edge-act">Action: {edge['action']}</div>
      <ul class="edge-list">{ms}</ul>
      <div class="edge-wr">{wr_txt}</div>
    </div>"""

def signal_tab(name, d, winr, ewr_high, ewr_all):
    label, score100, detail = d["label"], d["score"], d["detail"]
    risk, verdict, reason, vcol = d["risk"], d["verdict"], d["reason"], d["vcolor"]
    tcol = SIG_COLOR.get(label, "#8b95a5")
    rtcol = RISK_TIER_COLOR[risk["tier"]]
    wr, hits, tot = winr
    wr_txt = f"{wr}% ({hits}/{tot})" if wr is not None else "검증 누적 중 — 기록 쌓이면 표시"
    if risk["factors"]:
        rfac = "".join(
            f'<div class="drow"><span>{n}</span><span class="dval">{v}</span>'
            f'<span class="dsc" style="color:#f0813f">+{s}</span></div>'
            for (n, v, s) in risk["factors"])
    else:
        rfac = '<div class="drow"><span>위험 요인 없음</span><span class="dval">—</span><span class="dsc"></span></div>'
    reason_html = f'<div class="vreason">{reason}</div>' if reason else ''
    return f"""
    {edge_card(d['edge'], ewr_high, ewr_all)}
    <div class="verdict-hero" style="--c:{vcol}">
      <div class="hero-label">{name} · 최종 판정 (위험 반영)</div>
      <div class="sig-label" style="color:{vcol}">{verdict}</div>
      {reason_html}
      <div class="dual">
        <div class="dual-item"><span class="di-k">추세 점수</span><span class="di-v" style="color:{tcol}">{label} · {score100}</span></div>
        <div class="dual-item"><span class="di-k">위험 점수</span><span class="di-v" style="color:{rtcol}">{risk['tier']} · {risk['score']}</span></div>
      </div>
      <div class="sig-meta">종가 {risk['close']} · 당일 {risk['change']:+.1f}% · zone {risk['zone']}</div>
    </div>
    <div class="risk-card">
      <div class="rc-head" style="color:{rtcol}">위험 요인 — {risk['tier']} {risk['score']}/100</div>
      {rfac}
    </div>
    <div class="winrate">
      <span class="wr-label">추세 라벨 승률 (다음날 방향)</span>
      <span class="wr-val">{wr_txt}</span>
    </div>
    <details class="trend-fold"><summary>추세 점수 디테일 보기 ({label} {score100}/100)</summary>
      <div class="detail">{detail_rows(detail)}</div>
    </details>
    <div class="note">NEXT DAY EDGE = 조건 컨플루언스(애매하면 NO EDGE 기권). 최종 판정 = 추세 점수에 위험 게이트 적용.
    표시된 확신도는 휴리스틱이며, 실측 승률은 기록이 쌓인 뒤 표시됩니다.</div>
    """

def risk_tab(rvals, rst, lvl, red, amber):
    LV = {"calm":("평시","#3fb950"),"watch":("주의","#d8a322"),
          "alert":("경보","#f0813f"),"crisis":("위기","#f04747")}
    nm, col = LV[lvl]
    bt = {"green":"안정","amber":"주의","red":"점등","none":"—"}
    segs = "".join(f'<div class="sigseg {rst[i["key"]]}"></div>' for i in RISK)
    cards = ""
    for ind in RISK:
        s = rst[ind["key"]]; v = rvals.get(ind["key"])
        cards += f"""<div class="rcard {s if s!='none' else ''}">
          <div class="rc-top"><span class="rc-name">{ind['name']}</span>
          <span class="badge {s}">{bt[s]}</span></div>
          <div class="rc-val">{'—' if v is None else v}<span class="u">{ind['unit']}</span></div>
          <div class="rc-thr">녹 {ind['green']} / 적 {ind['red']}</div></div>"""
    pb = {"calm":"관찰만. 트리거 미발동.","watch":"추적 강화. 매일 확인.",
          "alert":"대응 준비. 정한 매수레벨·분할 점검. 반등 이유 있는 것만.",
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
.sig-hero,.verdict-hero{{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:22px;margin-bottom:12px;position:relative;overflow:hidden;}}
.sig-hero::before,.verdict-hero::before{{content:"";position:absolute;inset:0;background:var(--c);opacity:.06;}}
.hero-label{{position:relative;font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin-bottom:8px;}}
.sig-label{{position:relative;font-family:'Oswald',sans-serif;font-weight:600;font-size:clamp(32px,9vw,52px);line-height:1;text-transform:uppercase;}}
.vreason{{position:relative;font-family:'IBM Plex Mono',monospace;font-size:12.5px;color:var(--text);margin-top:6px;}}
.dual{{position:relative;display:flex;gap:10px;margin-top:14px;}}
.dual-item{{flex:1;background:var(--s2);border:1px solid var(--border);border-radius:10px;padding:10px 12px;}}
.di-k{{display:block;font-size:10.5px;color:var(--muted);font-family:'IBM Plex Mono',monospace;text-transform:uppercase;letter-spacing:.12em;margin-bottom:4px;}}
.di-v{{font-family:'Oswald',sans-serif;font-size:18px;}}
.sig-meta{{position:relative;font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--muted);margin-top:10px;}}
.sigbar{{position:relative;display:flex;gap:6px;margin-top:16px;}}
.sigseg{{flex:1;height:8px;border-radius:3px;background:var(--s2);border:1px solid var(--border);}}
.sigseg.green{{background:#3fb950;border-color:#3fb950;}} .sigseg.amber{{background:#d8a322;border-color:#d8a322;}} .sigseg.red{{background:#f04747;border-color:#f04747;}}
.edge-hero{{background:var(--surface);border:1px solid var(--border);border-left:4px solid var(--c);border-radius:14px;padding:18px 20px;margin-bottom:12px;position:relative;overflow:hidden;}}
.edge-hero::before{{content:"";position:absolute;inset:0;background:var(--c);opacity:.05;}}
.edge-hero.noedge{{border-left-color:#46505f;opacity:.8;}}
.edge-dir{{position:relative;font-family:'Oswald',sans-serif;font-weight:600;font-size:30px;line-height:1;text-transform:uppercase;margin-top:4px;}}
.edge-meta{{position:relative;font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--muted);margin-top:6px;}}
.edge-act{{position:relative;font-family:'Oswald',sans-serif;font-size:16px;color:var(--text);margin-top:8px;}}
.edge-list{{position:relative;list-style:none;display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;}}
.edge-list li{{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--text);background:var(--s2);border:1px solid var(--border);border-radius:6px;padding:4px 8px;}}
.edge-wr{{position:relative;font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:var(--dim);margin-top:10px;}}
.risk-card{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:8px 16px;margin-bottom:12px;}}
.rc-head{{font-family:'Oswald',sans-serif;font-size:14px;letter-spacing:.04em;padding:10px 0 6px;border-bottom:1px solid var(--border);text-transform:uppercase;}}
.winrate{{display:flex;justify-content:space-between;align-items:center;background:var(--s2);border:1px solid var(--border);border-radius:10px;padding:13px 16px;margin-bottom:12px;}}
.wr-label{{font-size:12px;color:var(--muted);}} .wr-val{{font-family:'IBM Plex Mono',monospace;font-weight:600;color:var(--teal);}}
.trend-fold{{background:var(--surface);border:1px solid var(--border);border-radius:12px;margin-bottom:12px;overflow:hidden;}}
.trend-fold summary{{cursor:pointer;padding:13px 16px;font-family:'Oswald',sans-serif;font-size:14px;color:var(--muted);list-style:none;}}
.trend-fold summary::-webkit-details-marker{{display:none;}}
.trend-fold[open] summary{{border-bottom:1px solid var(--border);color:var(--text);}}
.detail{{padding:4px 16px;}}
.drow{{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--border);font-size:13px;}}
.drow:last-child{{border-bottom:none;}}
.drow > span:first-child{{color:var(--muted);flex:1;}}
.dval{{font-family:'IBM Plex Mono',monospace;color:var(--text);}}
.dsc{{font-family:'IBM Plex Mono',monospace;font-size:12px;width:54px;text-align:right;}}
.note{{font-size:11.5px;color:var(--dim);line-height:1.6;margin-top:8px;}}
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
<div class="foot"><b>최종 판정은 추세 점수에 위험 게이트를 적용한 값이며 매매 신호가 아닙니다.</b> 위험모델은 페이퍼 검증 전입니다. 종가 기준이라 장중 실시간과 차이가 있습니다.</div>
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

    try: sp = signal_for("SPY", "SPY", macro, is_nasdaq=False)
    except Exception as ex: errors["sp"] = str(ex); sp = ("NEUTRAL", 0, {}, None)
    try: nq = signal_for("QQQ", "QQQ", macro, is_nasdaq=True)
    except Exception as ex: errors["nq"] = str(ex); nq = ("NEUTRAL", 0, {}, None)

    try: market = collect_market_internals()
    except Exception as ex: errors["internals"] = str(ex); market = {}

    try: sp_risk = compute_risk_bias("SPY", macro, market)
    except Exception as ex: errors["sp_risk"] = str(ex); sp_risk = _empty_risk()
    try: nq_risk = compute_risk_bias("QQQ", macro, market)
    except Exception as ex: errors["nq_risk"] = str(ex); nq_risk = _empty_risk()

    spd = build_tab_data("SPY", sp, sp_risk)
    nqd = build_tab_data("QQQ", nq, nq_risk)
    edges = {"sp": spd["edge"]["direction"], "sp_conf": spd["edge"]["confidence"],
             "nq": nqd["edge"]["direction"], "nq_conf": nqd["edge"]["confidence"]}

    today = dt.datetime.now(NY).strftime("%Y-%m-%d")          # 거래일 기준 뉴욕
    hist = load_hist()
    hist = grade_and_record(hist, today, sp, nq, edges)
    with open(HIST_PATH, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)

    now = dt.datetime.now(NY).strftime("%Y-%m-%d %H:%M ET")
    rtab  = risk_tab(rvals, rst, lvl, red, amber)
    sptab = signal_tab("S&P 500 · SPY", spd, winrate(hist, "sp"),
                       edge_winrate(hist, "sp", "High"), edge_winrate(hist, "sp"))
    nqtab = signal_tab("나스닥 100 · QQQ", nqd, winrate(hist, "nq"),
                       edge_winrate(hist, "nq", "High"), edge_winrate(hist, "nq"))
    html = render(rtab, sptab, nqtab, now, errors)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    write_pwa_assets()

    print(f"  위험단계: {LEVELS_NM(lvl)} (적{red}/황{amber})")
    print(f"  SPY 추세 {sp[0]} {sp[1]} / 위험 {sp_risk['tier']} {sp_risk['score']} → {spd['verdict']} / EDGE {spd['edge']['direction']}")
    print(f"  QQQ 추세 {nq[0]} {nq[1]} / 위험 {nq_risk['tier']} {nq_risk['score']} → {nqd['verdict']} / EDGE {nqd['edge']['direction']}")
    if errors: print(f"  실패: {list(errors.keys())}")
    print(f"  생성: {OUT_HTML}")

def LEVELS_NM(l): return {"calm":"평시","watch":"주의","alert":"경보","crisis":"위기"}[l]

if __name__ == "__main__":
    main()
