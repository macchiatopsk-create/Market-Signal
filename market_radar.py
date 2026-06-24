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
ALERT_PATH = os.path.join(BASE, "telegram_alerts.json")

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
                close=None, close_pos=0.5, open=None, high=None, low=None, atr=None,
                prev_high=None, prev_low=None, prev_close=None, cond={})

def _empty_inst():
    return dict(tier="NEUTRAL", stress=0, cat={},
                cta=dict(tier="CTA NEUTRAL", score=50, posture=0, effective=0.0, factors=[]))

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

    # 신용·VIX·소형주 플래그는 '다음날 Bias / Institutional' 에서만 쓰고, Risk Score 엔 안 넣음 (구조 균열 전용)
    cond["iwm_weak"]   = bool(market.get("iwm_weak"))
    cond["iwm_strong"] = bool(market.get("iwm_strong"))
    cond["credit_off"] = bool(market.get("credit_off"))
    cond["credit_on"]  = bool(market.get("credit_on"))
    cond["vix_up"]     = market.get("vix_up")
    cond["vix_up_red"] = bool(market.get("vix_up")) and red

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
    atr_pct = round(float((df["High"] - df["Low"]).tail(14).mean()) / close * 100, 3) if close else None
    return dict(score=score, tier=risk_tier(score), factors=factors,
                change=round(chg, 2), zone=zone, trend=st["trend"],
                close=round(close, 2), close_pos=round(close_pos, 2), open=round(today_open, 2),
                high=round(today_high, 2), low=round(today_low, 2), atr=atr_pct,
                prev_high=round(prev_high, 2), prev_low=round(prev_low, 2), prev_close=round(prev_close, 2),
                cond=cond)

# ===========================================================================
# CTA Pressure (가격기반 추세추종 프록시) + Institutional Composite
# ===========================================================================
def _ratio_down(a, b, n=20):
    try: return trend(a, b, n) < 0
    except Exception: return None

def realized_vol_spike(df):
    try:
        ret = df["Close"].pct_change().dropna()
        rv20 = float(ret.tail(20).std())
        rv_series = ret.rolling(20).std().dropna()
        med = float(rv_series.tail(100).median())
        return rv20 > 1.5 * med if med > 0 else False
    except Exception:
        return False

def compute_cta_tier(df):
    """(가) 다기간 수익률 모멘텀 + 변동성 타게팅 CTA 프록시."""
    px = df["Close"]; n = len(px)
    def mom(k):
        return float(px.iloc[-1] / px.iloc[-1 - k] - 1) * 100 if n >= k + 1 else 0.0
    mom20, mom60, mom120 = mom(20), mom(60), mom(120)
    posture = sum(1 if m > 0 else -1 for m in (mom20, mom60, mom120))   # -3 ~ +3
    ret = px.pct_change().dropna()
    rv20 = float(ret.tail(20).std())
    rv_series = ret.rolling(20).std().dropna()
    rv100_med = float(rv_series.tail(100).median()) if len(rv_series) else rv20
    vol_mult = min(1.0, rv100_med / rv20) if rv20 > 0 else 1.0           # 변동성 오르면 사이즈 ↓
    effective = posture * vol_mult
    vol_spike = rv20 > rv100_med * 1.5

    # FORCED 만 raw posture 기준 (vol targeting 이 effective 를 줄여 자기상쇄하는 것 방지)
    if posture <= -2 and vol_spike:  tier = "CTA FORCED SELLING"
    elif effective <= -2:            tier = "CTA SELLING"
    elif effective >= 2:             tier = "CTA LONG"
    else:                            tier = "CTA NEUTRAL"
    score = {"CTA LONG": 15, "CTA NEUTRAL": 50, "CTA SELLING": 75, "CTA FORCED SELLING": 95}[tier]
    factors = [f"20일 {mom20:+.1f}%", f"60일 {mom60:+.1f}%", f"120일 {mom120:+.1f}%",
               f"posture {posture:+d}", f"vol×{vol_mult:.2f}", f"eff {effective:+.2f}"]
    return dict(tier=tier, score=score, posture=posture, effective=round(effective, 2),
                rv20=round(rv20, 4), rv100_median=round(rv100_med, 4),
                mom20=round(mom20, 2), mom60=round(mom60, 2), mom120=round(mom120, 2), factors=factors)

def compute_market_stress(macro, spy_df):
    """CTA 제외한 시장공통 스트레스(신용·변동성·폭·방어순환·금융). 지수 무관."""
    stress = 0; cat = {}
    credit = sum(1 for a, b in (("HYG", "IEF"), ("LQD", "IEF"), ("HYG", "LQD")) if _ratio_down(a, b))
    stress += credit; cat["신용 회피"] = credit

    vol = 0
    vix = macro.get("vix"); vix3m = macro.get("vix3m")
    if vix is not None:
        vol += 2 if vix > 25 else 1 if vix > 20 else 0
    if vix is not None and vix3m is not None and vix > vix3m: vol += 1
    if realized_vol_spike(spy_df): vol += 1
    stress += vol; cat["변동성"] = vol

    breadth = sum(1 for a, b in (("RSP", "SPY"), ("IWM", "SPY"), ("QQEW", "QQQ")) if _ratio_down(a, b))
    stress += breadth; cat["폭 약화"] = breadth

    defen = 0
    if _ratio_down("XLY", "XLP"): defen += 1
    if _ratio_down("SPY", "XLU"): defen += 1
    if _ratio_down("SPY", "XLP"): defen += 1
    stress += defen; cat["방어 순환"] = defen

    fin = 0
    if _ratio_down("KRE", "XLF"): fin += 1
    if _ratio_down("XLF", "SPY"): fin += 1
    stress += fin; cat["금융 스트레스"] = fin
    return dict(stress_base=stress, cat=cat)

def finalize_institutional(market, df):
    """시장공통 stress + 해당 지수의 CTA → Institutional tier. (SPY/QQQ 각각 자기 CTA)"""
    cta = compute_cta_tier(df)
    stress = market["stress_base"] + {"CTA LONG": -1, "CTA NEUTRAL": 0,
                                      "CTA SELLING": 1, "CTA FORCED SELLING": 2}[cta["tier"]]
    if   stress <= 0: tier = "RISK ON"
    elif stress <= 4: tier = "NEUTRAL"
    elif stress <= 8: tier = "RISK OFF"
    else:             tier = "STRESS"
    cat = dict(market["cat"]); cat["CTA"] = cta["tier"]
    return dict(tier=tier, stress=stress, cta=cta, cat=cat)

# ===========================================================================
# Final Signal — Trend 베이스 + Risk 감점 + Institutional 감점/가산 + CTA 소폭 + 하드게이트
# ===========================================================================
def compute_final(trend_score, risk_tier, inst):
    fs = trend_score
    fs += {"ELEVATED": -10, "HIGH": -25, "SELL ONLY": -40}.get(risk_tier, 0)
    itier = inst["tier"]
    fs += {"RISK OFF": -15, "STRESS": -30, "RISK ON": 8}.get(itier, 0)
    cta = inst["cta"]["tier"]
    fs += {"CTA LONG": 5, "CTA SELLING": -6, "CTA FORCED SELLING": -12}.get(cta, 0)
    fs = max(0, min(100, round(fs)))

    if fs >= 73:   sig = "STRONG BUY"
    elif fs >= 59: sig = "BUY"
    elif fs > 41:  sig = "NEUTRAL"
    elif fs > 27:  sig = "SELL"
    else:          sig = "STRONG SELL"

    # 하드 게이트
    if risk_tier == "SELL ONLY":
        sig, why = "SELL ONLY", "위험 최고조 — 매수 금지"
    elif risk_tier == "HIGH" and cta in ("CTA SELLING", "CTA FORCED SELLING"):
        sig, why = "SHORT ONLY", "위험 높음 + CTA 매도 — 숏 우위"
    elif itier == "STRESS" and cta == "CTA FORCED SELLING":
        sig, why = "SELL ONLY", "기관 STRESS + CTA 강제청산"
    else:
        why = f"Trend {trend_score} · Risk {risk_tier} · Inst {itier} · {cta}"
    return fs, sig, why

FINAL_COLOR = {"STRONG BUY":"#2bd47e","BUY":"#3fb950","NEUTRAL":"#8b95a5",
               "SELL":"#f0813f","STRONG SELL":"#f04747","NO LONG":"#f0813f","SELL ONLY":"#f04747",
               "SHORT ONLY":"#f04747","LONG ONLY":"#2bd47e"}
INST_COLOR = {"RISK ON":"#3fb950","NEUTRAL":"#8b95a5","RISK OFF":"#f0813f","STRESS":"#f04747"}
CTA_COLOR  = {"CTA LONG":"#3fb950","CTA NEUTRAL":"#8b95a5","CTA SELLING":"#f0813f","CTA FORCED SELLING":"#f04747"}

# ===========================================================================
# NEXT DAY BIAS SCORE — 0~100 점수형. 기권 안 함, 점수로만 표현.
#   50 기준. Bearish 조건 가산 / Bullish 조건 감산. Inside Day 는 50쪽으로 당김.
#   0~25 BULLISH EDGE · 26~45 MILD BULLISH · 46~55 NEUTRAL · 56~75 MILD BEARISH · 76~100 BEARISH EDGE
# ===========================================================================
def next_day_bias_label(score):
    if score <= 25:   return "BULLISH EDGE", "Long Favorable / Dip Buy Candidate"
    elif score <= 45: return "MILD BULLISH", "Long 가능하지만 사이즈 작게"
    elif score <= 55: return "NEUTRAL", "애매함 / 무리 금지"
    elif score <= 75: return "MILD BEARISH", "Long Caution / 관망 우선"
    else:             return "BEARISH EDGE", "No Long / Sell Rally"

def compute_next_day_bias(risk):
    c = risk.get("cond", {})
    score = 50
    # --- Bearish 가산 ---
    if c.get("prev_low_break"):              score += 12
    if c.get("d_bear"):                      score += 12
    if c.get("w_bear"):                      score += 18
    if c.get("close_low") and c.get("red"):  score += 10
    if c.get("iwm_weak"):                    score += 8
    if c.get("credit_off"):                  score += 8
    if c.get("vix_up_red"):                  score += 8
    if c.get("premium_reject"):              score += 6
    if c.get("sweep"):                       score += 8
    # 기관/CTA — 아직 미구현. cond 에 없으면 inert (나중에 그 카드 만들면 자동 반영)
    if c.get("institutional_tier") == "RISK OFF": score += 8
    if c.get("institutional_tier") == "STRESS":   score += 14
    if c.get("cta_tier") in ("CTA SELLING", "CTA FORCED SELLING"): score += 8
    # --- Bullish 감산 ---
    if c.get("close_high"):                  score -= 10
    if (c.get("vix_up") is False) and c.get("red"): score -= 8
    if c.get("zone") == "discount":          score -= 6
    if c.get("iwm_strong"):                  score -= 8
    if c.get("credit_on"):                   score -= 8
    if c.get("mid_recovery"):                score -= 12
    if c.get("institutional_tier") == "RISK ON": score -= 10
    if c.get("cta_tier") == "CTA LONG":      score -= 8
    # Inside Day → 기권 대신 50쪽으로 당겨 애매하게
    if c.get("inside_day"):
        score = round(score * 0.6 + 50 * 0.4)
    score = max(0, min(100, round(score)))
    label, action = next_day_bias_label(score)

    # 근거 리스트 (표시용)
    matched = []
    for key, txt in (("prev_low_break", "전일저가 이탈"), ("d_bear", "Daily 하락구조"),
                     ("w_bear", "Weekly 하락구조"), ("sweep", "유동성 스윕"),
                     ("iwm_weak", "IWM/SPY 약세"), ("credit_off", "HYG/IEF 위험회피"),
                     ("vix_up_red", "VIX 상승+하락"), ("premium_reject", "Premium 거부"),
                     ("close_high", "종가 고가권"), ("mid_recovery", "중간값 회복"),
                     ("iwm_strong", "IWM/SPY 강세"), ("credit_on", "신용 우호")):
        if c.get(key): matched.append(txt)
    if c.get("close_low") and c.get("red"): matched.append("종가 저가권")
    if c.get("zone") == "discount":         matched.append("Discount 구간")
    if c.get("inside_day"):                 matched.append("Inside Day(중립화)")
    return dict(score=score, label=label, action=action, matched=matched)

# 점수 구간 (구간별 승률용)
BIAS_BUCKETS = [(0, 25, "Bullish Edge"), (26, 45, "Mild Bullish"), (46, 55, "Neutral"),
                (56, 75, "Mild Bearish"), (76, 100, "Bearish Edge")]

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

def _regrade(hist):
    """전체 hist 를 훑어 아직 비어있는 채점값을 채운다 (ret2/ret5 누락 방지)."""
    for i, p in enumerate(hist):
        for mk in ("sp", "nq"):
            pc = p.get(mk + "_close")
            if not pc:
                continue
            for h, rk in ((1, "ret1"), (2, "ret2"), (5, "ret5")):     # 다기간 수익률(거래일)
                j = i + h
                if j < len(hist) and p.get(mk + "_" + rk) is None:
                    cf = hist[j].get(mk + "_close")
                    if cf:
                        p[mk + "_" + rk] = round((cf / pc - 1) * 100, 3)
            if i + 1 < len(hist):                                     # 1일 상세
                nxt = hist[i + 1]
                cf = nxt.get(mk + "_close"); op = nxt.get(mk + "_open")
                hi = nxt.get(mk + "_high"); lo = nxt.get(mk + "_low")
                ns = p.get(mk + "_next_score")
                if cf and op and p.get(mk + "_oc1") is None:          # 다음날 시가→종가 (실매매 구간)
                    p[mk + "_oc1"] = round((cf / op - 1) * 100, 3)
                if cf and ns is not None and p.get(mk + "_dir_hit") is None:
                    ret = (cf / pc - 1) * 100
                    if ns >= 56 or ns <= 45:
                        pb = ns <= 45; dr = ret if pb else -ret
                        p[mk + "_dir_hit"] = bool(dr > 0)
                        atr = p.get(mk + "_atr")
                        if atr: p[mk + "_r"] = round(dr / atr, 3)
                        if hi is not None and lo is not None:
                            up = (hi / pc - 1) * 100; dn = (pc - lo) / pc * 100
                            p[mk + "_mfe1"] = round(up if pb else dn, 3)
                            p[mk + "_mae1"] = round(dn if pb else up, 3)
                    if ns >= 56:   p[mk + "_next_hit"] = bool(cf < pc)
                    elif ns <= 45: p[mk + "_next_hit"] = bool(cf > pc)

def grade_and_record(hist, today, pred):
    row = {"date": today, "graded": True}
    for mk in ("sp", "nq"):
        d = pred[mk]
        row.update({
            mk + "_next_score": d["next_score"], mk + "_next_label": d["next_label"],
            mk + "_trend_score": d["trend_score"], mk + "_risk_score": d["risk_score"],
            mk + "_inst_tier": d["inst_tier"], mk + "_inst_stress": d["inst_stress"],
            mk + "_cta_score": d["cta_score"], mk + "_cta_tier": d["cta_tier"], mk + "_final": d["final"],
            mk + "_close": d["close"], mk + "_open": d["open"], mk + "_high": d["high"], mk + "_low": d["low"],
            mk + "_atr": d["atr"],
            mk + "_next_hit": None, mk + "_dir_hit": None, mk + "_r": None,
            mk + "_ret1": None, mk + "_ret2": None, mk + "_ret5": None,
            mk + "_oc1": None, mk + "_mfe1": None, mk + "_mae1": None,
            mk + "_trade_r": None, mk + "_tp1_hit": None, mk + "_tp2_hit": None, mk + "_sl_hit": None,
        })
    if hist and hist[-1].get("date") == today:
        hist[-1].update(row)                # 같은 날 재실행 → 덮어쓰기
    else:
        hist.append(row)
    _regrade(hist)                          # 매 실행마다 전체 sweep
    return hist[-180:]

def bias_bucket_stats(hist, mk):
    """점수 구간별 (적중수, 표본수). Neutral 은 채점 제외라 자동으로 0건."""
    out = []
    for lo, hi, name in BIAS_BUCKETS:
        rows = [h for h in hist if h.get(mk + "_next_hit") is not None
                and h.get(mk + "_next_score") is not None and lo <= h[mk + "_next_score"] <= hi]
        hits = sum(1 for h in rows if h[mk + "_next_hit"])
        out.append((name, lo, hi, hits, len(rows)))
    return out

# --- Validation (샘플 1건부터 표시) ---
def SAMPLE_STATUS(n):
    return ("Building history" if n < 10 else "Early read" if n < 30
            else "Usable sample" if n < 100 else "Strong sample")

def _observations(hist, only=None):
    obs = []
    for h in hist:
        for mk in ("sp", "nq"):
            if only and mk != only:
                continue
            if h.get(mk + "_ret1") is None:
                continue
            obs.append(dict(score=h.get(mk + "_next_score"), ret=h.get(mk + "_ret1"),
                            oc=h.get(mk + "_oc1"), atr=h.get(mk + "_atr"),
                            cta=h.get(mk + "_cta_tier"), inst=h.get(mk + "_inst_tier")))
    return obs

def _vstats(obs, dir_fn):
    """방향함수 기준 (표본, 승률, 평균C2C(raw,부호), 평균O2C(raw,부호), 평균R)."""
    n = 0; wins = 0; c2c = []; o2c = []; rs = []
    for o in obs:
        d = dir_fn(o)
        if d is None or o["ret"] is None:
            continue
        n += 1
        c2c.append(o["ret"])                          # raw 종가→종가 (부호 그대로)
        if o.get("oc") is not None: o2c.append(o["oc"])
        dr = o["ret"] if d == "bull" else -o["ret"]   # 방향보정 (승/ R 용)
        if dr > 0: wins += 1
        if o.get("atr"): rs.append(dr / o["atr"])
    wr = round(wins / n * 100, 1) if n else None
    ac = round(sum(c2c) / len(c2c), 2) if c2c else None
    ao = round(sum(o2c) / len(o2c), 2) if o2c else None
    rr = round(sum(rs) / len(rs), 2) if rs else None
    return n, wr, ac, ao, rr

def _bias_dir(o):
    s = o["score"]
    return None if s is None else ("bear" if s >= 56 else "bull" if s <= 45 else None)
def _cta_dir(o):
    t = o["cta"]
    return "bear" if t in ("CTA SELLING", "CTA FORCED SELLING") else "bull" if t == "CTA LONG" else None
def _inst_dir(o):
    t = o["inst"]
    return "bear" if t in ("RISK OFF", "STRESS") else "bull" if t == "RISK ON" else None

def _vsection(hist, mk, name):
    obs = _observations(hist, mk)
    def fmt(sub):
        n, wr, ac, ao, rr = sub
        wrs = f"{wr}%" if wr is not None else "—"
        acs = f"{ac:+.2f}%" if ac is not None else "—"
        aos = f"{ao:+.2f}%" if ao is not None else "—"
        rrs = f"{rr:+.2f}" if rr is not None else "—"
        return f'<span class="dval">n{n}·승{wrs}·C2C{acs}·O2C{aos}·R{rrs}</span>'
    def line(label, sub):
        return f'<div class="drow"><span>{label}</span>{fmt(sub)}</div>'
    n, wr, ac, ao, rr = _vstats(obs, _bias_dir)
    score_rows = "".join(line(f"{nm} ({lo}~{hi})",
                          _vstats([o for o in obs if o["score"] is not None and lo <= o["score"] <= hi], _bias_dir))
                          for lo, hi, nm in BIAS_BUCKETS if nm != "Neutral")
    cta_rows = "".join(line(t, _vstats([o for o in obs if o["cta"] == t], _cta_dir))
                       for t in ("CTA LONG", "CTA SELLING", "CTA FORCED SELLING"))
    inst_rows = "".join(line(t, _vstats([o for o in obs if o["inst"] == t], _inst_dir))
                        for t in ("RISK ON", "RISK OFF", "STRESS"))
    return f"""
    <div class="edge-hero" style="--c:#46b1c9">
      <div class="hero-label">{name} · VALIDATION (Bias 방향)</div>
      <div class="bias-score" style="color:#46b1c9">{(str(wr)+"%") if wr is not None else "—"}<span class="bias-max"> 승률</span></div>
      <div class="edge-meta">표본 {n} · C2C {("%+.2f%%"%ac) if ac is not None else "—"} · O2C {("%+.2f%%"%ao) if ao is not None else "—"} · R {("%+.2f"%rr) if rr is not None else "—"} · <b>{SAMPLE_STATUS(n)}</b></div>
    </div>
    <div class="risk-card"><div class="rc-head" style="color:#46b1c9">{name} · 점수 구간별 (실제 이동%)</div>{score_rows}</div>
    <div class="risk-card"><div class="rc-head" style="color:#46b1c9">{name} · CTA</div>{cta_rows}</div>
    <div class="risk-card"><div class="rc-head" style="color:#46b1c9">{name} · Institutional</div>{inst_rows}</div>
    """

def validation_tab(hist):
    return f"""
    {_vsection(hist, "sp", "S&P 500 · SPY")}
    {_vsection(hist, "nq", "나스닥 · QQQ")}
    <div class="note">SPY / QQQ 각각 따로 채점합니다. 방향: Bias≥56 · CTA SELLING/FORCED · Inst RISK OFF/STRESS = 하락 예측.
    C2C=종가→종가 · O2C=다음날 시가→종가(부호 그대로 — 하락예측이면 −%가 적중). trade_r/TP/SL 채점은 Trade Plan Box 데이터 쌓이면 활성화.
    지수별로 나누면 표본이 절반씩이라 수렴은 느리지만 더 정확합니다(30건↑부터 의미).</div>
    """

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

def build_tab_data(ticker, trend_sig, risk, inst):
    label, score100, detail, close = trend_sig
    bias = compute_next_day_bias(risk)
    fscore, fsig, why = compute_final(score100, risk["tier"], inst)
    return dict(ticker=ticker, label=label, score=score100, detail=detail, close=close, risk=risk,
                inst=inst, bias=bias, fscore=fscore, fsig=fsig, why=why,
                fcolor=FINAL_COLOR.get(fsig, "#8b95a5"))

def bias_card(bias, buckets):
    s = bias["score"]
    col = ("#f04747" if s >= 76 else "#f0813f" if s >= 56 else "#8b95a5" if s >= 46
           else "#3fb950" if s >= 26 else "#2bd47e")
    ms = "".join(f'<li>{m}</li>' for m in bias["matched"]) or "<li>조건 없음</li>"
    rows = ""
    for name, lo, hi, hits, tot in buckets:
        if name == "Neutral" or tot == 0:
            continue
        pct = round(hits / tot * 100, 1)
        rows += f'<div class="drow"><span>{name} ({lo}~{hi})</span><span class="dval">{hits}/{tot} · {pct}%</span></div>'
    bk = (f'<div class="bucket"><div class="bk-h">점수 구간별 누적 승률 (1건부터)</div>{rows}</div>'
          if rows else '<div class="edge-wr">구간별 승률 — 채점 누적 중</div>')
    return f"""<div class="edge-hero" style="--c:{col}">
      <div class="hero-label">NEXT DAY BIAS SCORE</div>
      <div class="bias-score" style="color:{col}">{s}<span class="bias-max"> /100</span></div>
      <div class="bias-bar"><div class="bias-fill" style="width:{s}%;background:{col}"></div><div class="bias-mid"></div></div>
      <div class="edge-dir" style="color:{col};font-size:23px;">{bias['label']}</div>
      <div class="edge-act">Action: {bias['action']}</div>
      <ul class="edge-list">{ms}</ul>
      {bk}
    </div>"""

def institutional_card(inst):
    tcol = INST_COLOR.get(inst["tier"], "#8b95a5")
    cta = inst["cta"]; ccol = CTA_COLOR.get(cta["tier"], "#8b95a5")
    cats = "".join(
        f'<div class="drow"><span>{k}</span><span class="dval">{("점등 "+str(v)) if v else "—"}</span></div>'
        for k, v in inst.get("cat", {}).items() if k != "CTA")
    ctaf = " · ".join(cta.get("factors", []))
    return f"""<div class="risk-card">
      <div class="rc-head" style="color:{tcol}">INSTITUTIONAL COMPOSITE — {inst['tier']} (stress {inst['stress']})</div>
      <div class="drow"><span>CTA Pressure</span><span class="dval" style="color:{ccol}">{cta['tier']} · {cta['score']}</span></div>
      {cats}
      <div class="edge-wr">{ctaf}</div>
    </div>"""

def signal_tab(name, d, buckets):
    label, score100, detail = d["label"], d["score"], d["detail"]
    risk, inst = d["risk"], d["inst"]
    fsig, fscore, why, fcol = d["fsig"], d["fscore"], d["why"], d["fcolor"]
    rtcol = RISK_TIER_COLOR[risk["tier"]]
    tcol = SIG_COLOR.get(label, "#8b95a5")
    if risk["factors"]:
        rfac = "".join(
            f'<div class="drow"><span>{n}</span><span class="dval">{v}</span>'
            f'<span class="dsc" style="color:#f0813f">+{s}</span></div>'
            for (n, v, s) in risk["factors"])
    else:
        rfac = '<div class="drow"><span>구조 위험 요인 없음</span><span class="dval">—</span><span class="dsc"></span></div>'
    return f"""
    {bias_card(d['bias'], buckets)}
    <div class="verdict-hero" style="--c:{fcol}">
      <div class="hero-label">{name} · FINAL SIGNAL</div>
      <div class="sig-label" style="color:{fcol}">{fsig}</div>
      <div class="vreason">{why}</div>
      <div class="dual">
        <div class="dual-item"><span class="di-k">추세 점수</span><span class="di-v" style="color:{tcol}">{label} · {score100}</span></div>
        <div class="dual-item"><span class="di-k">최종 점수</span><span class="di-v" style="color:{fcol}">{fscore}/100</span></div>
      </div>
      <div class="sig-meta">위험 {risk['tier']} {risk['score']} · 기관 {inst['tier']} · {inst['cta']['tier']} · 종가 {risk['close']} ({risk['change']:+.1f}%)</div>
    </div>
    <div class="tradeplan" id="tp-{d['ticker']}">
      <div class="tp-bar"><span class="hero-label">TRADE PLAN — Live Intraday</span><button class="tp-enable">🔔 알림</button></div>
      <div class="tp-body"><div class="tp-note">장중 데이터 불러오는 중…</div></div>
      <div class="tp-foot">규칙 기반 실행 가이드일 뿐 보장된 신호가 아닙니다. 포지션 사이즈·손절 필수.</div>
    </div>
    <div class="risk-card">
      <div class="rc-head" style="color:{rtcol}">TODAY RISK BIAS — {risk['tier']} {risk['score']}/100 (구조 균열)</div>
      {rfac}
    </div>
    {institutional_card(inst)}
    <details class="trend-fold"><summary>추세 점수 디테일 ({label} {score100}/100)</summary>
      <div class="detail">{detail_rows(detail)}</div>
    </details>
    <div class="note">FINAL = Trend 베이스 − Risk − Institutional ± CTA + 하드게이트(SELL ONLY / SHORT ONLY).
    Bias Score는 다음날 방향(46~55 채점 제외). 위험·기관 모델은 페이퍼 검증 전이니 참고용입니다.</div>
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

TRADEPLAN_JS = r"""
const DASHBOARD_BIAS = __DASHBOARD_BIAS__;
const TP_TICKERS = Object.keys(DASHBOARD_BIAS);
const PROXIES = [
  u => u,
  u => "https://api.allorigins.win/raw?url=" + encodeURIComponent(u),
  u => "https://api.codetabs.com/v1/proxy/?quest=" + encodeURIComponent(u)
];
const NYFMT = new Intl.DateTimeFormat("en-CA", {timeZone:"America/New_York", year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", hour12:false});
function nyParts(ts){
  const o = {}; NYFMT.formatToParts(new Date(ts*1000)).forEach(p => o[p.type]=p.value);
  let h = parseInt(o.hour,10); if (h===24) h=0;
  return {date:o.year+"-"+o.month+"-"+o.day, min:h*60+parseInt(o.minute,10)};
}
function isRegularSession(){
  const d = new Date();
  const wd = new Intl.DateTimeFormat("en-US",{timeZone:"America/New_York",weekday:"short"}).format(d);
  if (wd==="Sat" || wd==="Sun") return false;
  const m = nyParts(Math.floor(d.getTime()/1000)).min;
  return m >= 570 && m <= 960;   // 09:30~16:00 ET
}
async function fetchChart(ticker){
  const url = "https://query1.finance.yahoo.com/v8/finance/chart/"+ticker+"?range=5d&interval=5m&includePrePost=true";
  for (const wrap of PROXIES){
    try{
      const r = await fetch(wrap(url), {cache:"no-store"});
      if (!r.ok) continue;
      const j = await r.json();
      const res = j && j.chart && j.chart.result && j.chart.result[0];
      if (res && res.timestamp && res.indicators && res.indicators.quote) return res;
    }catch(e){}
  }
  return null;
}
function buildSession(res){
  const ts = res.timestamp, q = res.indicators.quote[0], bars = [];
  for (let i=0;i<ts.length;i++){
    if (q.close[i]==null) continue;
    const p = nyParts(ts[i]);
    bars.push({date:p.date, min:p.min, o:q.open[i], h:q.high[i], l:q.low[i], c:q.close[i], v:q.volume[i]||0});
  }
  if (!bars.length) return null;
  const reg = bars.filter(b => b.min>=570 && b.min<=960);
  const dates = [...new Set(reg.map(b=>b.date))].sort();
  if (!dates.length) return null;
  const today = dates[dates.length-1];
  const todayReg = reg.filter(b => b.date===today);
  if (!todayReg.length) return null;
  const orBars = todayReg.filter(b => b.min>=570 && b.min<600);
  const nowMin = nyParts(Math.floor(Date.now()/1000)).min;
  let vSum=0, pvSum=0;
  todayReg.forEach(b => { const tp=(b.h+b.l+b.c)/3; vSum+=b.v; pvSum+=tp*b.v; });
  const vwap = vSum>0 ? pvSum/vSum : todayReg[todayReg.length-1].c;
  const ranges = dates.slice(-5).map(d => {
    const ds = reg.filter(b=>b.date===d);
    return Math.max(...ds.map(b=>b.h)) - Math.min(...ds.map(b=>b.l));
  });
  const avgRange = ranges.reduce((a,b)=>a+b,0)/ranges.length;
  let prevHigh=null, prevLow=null, prevClose=null;
  if (dates.length>=2){
    const pr = reg.filter(b=>b.date===dates[dates.length-2]);
    if (pr.length){ prevHigh=Math.max(...pr.map(b=>b.h)); prevLow=Math.min(...pr.map(b=>b.l)); prevClose=pr[pr.length-1].c; }
  }
  return {
    price: bars[bars.length-1].c, open: todayReg[0].o, vwap,
    orHigh: orBars.length ? Math.max(...orBars.map(b=>b.h)) : null,
    orLow:  orBars.length ? Math.min(...orBars.map(b=>b.l)) : null,
    orReady: nowMin >= 600, avgRange, nowMin,
    prevHigh, prevLow, prevClose
  };
}
function r2(x){ return (x==null||isNaN(x)) ? "—" : (+x).toFixed(2); }
function statusColor(st){
  if (st.indexOf("CHASE")>=0) return "#f0813f";
  if (st.indexOf("SHORT")>=0) return "#f04747";
  if (st.indexOf("LONG")>=0) return "#2bd47e";
  return "#8b95a5";
}
function computePlan(ticker, s){
  const b = DASHBOARD_BIAS[ticker];
  const score=b.nextScore;
  const usingIntraday = s.prevClose!=null;
  const prevClose = usingIntraday ? s.prevClose : b.prevClose;
  const prevHigh  = s.prevHigh!=null  ? s.prevHigh  : b.prevHigh;
  const prevLow   = s.prevLow!=null   ? s.prevLow   : b.prevLow;
  const out = {ticker, score, label:b.nextLabel, price:s.price, vwap:s.vwap, orHigh:s.orHigh, orLow:s.orLow,
               prevHigh, prevLow, prevClose, gap: prevClose? (s.open/prevClose-1)*100 : null,
               direction:"NO TRADE", status:"WAIT", entry:null, stop:null, tp1:null, tp2:null, tp3:null,
               invalid:"", invalidation:"", notes:[]};
  if (!usingIntraday) out.notes.push("전일값 = EOD 참조 (" + (b.refDate||"") + ")");
  if (score>=76) out.direction="SHORT ONLY";
  else if (score>=56) out.direction="SHORT PREFERRED";
  else if (score>=46) out.direction="BOTH ALLOWED";
  else if (score>=26) out.direction="LONG PREFERRED";
  else out.direction="LONG ONLY";
  if (!s.orReady){ out.status="OR BUILDING"; out.notes.push("Opening Range 형성 중 (~10:00 ET)"); return out; }
  const price=s.price, vwap=s.vwap, orH=s.orHigh, orL=s.orLow, gap=out.gap;
  const shortC = [price<vwap, orL!=null&&price<orL, price<prevLow].filter(Boolean).length;
  const longC  = [price>vwap, orH!=null&&price>orH, price>prevClose].filter(Boolean).length;
  function shortPlan(){
    if (gap!=null && gap<=-0.8){ out.status="CHASE WARNING"; out.notes.push("갭다운 선반영 — 추격숏 금지"); }
    else if (shortC>=2) out.status="SHORT SETUP ACTIVE";
    else if (shortC===1) out.status="SHORT WATCH";
    else out.status="WAIT";
    const entry = orL!=null?orL:vwap, stop = orH!=null?orH:vwap, risk = stop-entry;
    if (risk>0){ out.entry=entry; out.stop=stop; out.tp1=entry-risk; out.tp2=entry-2*risk; out.tp3=entry-s.avgRange*0.75; }
    else out.invalid="Invalid setup (risk<=0)";
    out.notes.push("Entry: OR Low 이탈 또는 VWAP 거부");
    out.invalidation="VWAP 위 5분봉 종가 회복 또는 OR High 돌파";
  }
  function longPlan(){
    if (gap!=null && gap>=0.8){ out.status="CHASE WARNING"; out.notes.push("갭업 선반영 — 추격롱 금지, 눌림 대기"); }
    else if (longC>=2) out.status="LONG SETUP ACTIVE";
    else if (longC===1) out.status="LONG WATCH";
    else out.status="WAIT";
    const entry = orH!=null?orH:vwap, stop = orL!=null?orL:vwap, risk = entry-stop;
    if (risk>0){ out.entry=entry; out.stop=stop; out.tp1=entry+risk; out.tp2=entry+2*risk; out.tp3=entry+s.avgRange*0.75; }
    else out.invalid="Invalid setup (risk<=0)";
    out.notes.push("Entry: OR High 돌파 또는 VWAP 눌림 반등");
    out.invalidation="VWAP 아래 5분봉 종가 이탈 또는 OR Low 이탈";
  }
  if (score>=56) shortPlan();
  else if (score<=45) longPlan();
  else {
    if (price>vwap && orH!=null && price>orH) longPlan();
    else if (price<vwap && orL!=null && price<orL) shortPlan();
    else { out.status="NO TRADE"; out.notes.push("VWAP/OR 돌파 대기"); }
  }
  return out;
}
function renderPlan(p){
  const sc = statusColor(p.status);
  const rows = [["Price",r2(p.price)],["VWAP",r2(p.vwap)],["OR High",r2(p.orHigh)],["OR Low",r2(p.orLow)],
    ["Prev High",r2(p.prevHigh)],["Prev Low",r2(p.prevLow)],["Prev Close",r2(p.prevClose)],
    ["Gap %", p.gap==null?"—":((p.gap>=0?"+":"")+p.gap.toFixed(2)+"%")]]
    .map(x=>'<div class="tp-row"><span>'+x[0]+'</span><span class="tp-v">'+x[1]+'</span></div>').join("");
  let plan="";
  if (p.entry!=null){
    plan='<div class="tp-plan">'
      +'<div class="tp-row"><span>Entry</span><span class="tp-v">'+r2(p.entry)+'</span></div>'
      +'<div class="tp-row"><span>Stop</span><span class="tp-v">'+r2(p.stop)+'</span></div>'
      +'<div class="tp-row"><span>TP1 (1R)</span><span class="tp-v">'+r2(p.tp1)+'</span></div>'
      +'<div class="tp-row"><span>TP2 (2R)</span><span class="tp-v">'+r2(p.tp2)+'</span></div>'
      +'<div class="tp-row"><span>TP3</span><span class="tp-v">'+r2(p.tp3)+'</span></div></div>';
  } else if (p.invalid){ plan='<div class="tp-note">'+p.invalid+'</div>'; }
  const inval = p.invalidation ? '<div class="tp-note">Invalidation: '+p.invalidation+'</div>' : "";
  const notes = p.notes.length ? '<div class="tp-note">'+p.notes.join(" · ")+'</div>' : "";
  return '<div class="tp-head" style="color:'+sc+'">'+p.status+'</div>'
    +'<div class="tp-sub">Bias '+p.score+'/100 · '+p.label+' · '+p.direction+'</div>'+rows+plan+inval+notes;
}
function notifyLocal(ticker, status){
  const key = ticker+"_"+status+"_"+new Date().toISOString().slice(0,10);
  if (localStorage.getItem(key)) return;
  localStorage.setItem(key,"1");
  if ("Notification" in window && Notification.permission==="granted"){
    try{ new Notification(ticker+" "+status, {body:"Trade Plan 업데이트"}); }catch(e){}
  }
}
async function updateTradePlan(ticker){
  const box = document.getElementById("tp-"+ticker); if (!box) return;
  const body = box.querySelector(".tp-body");
  const res = await fetchChart(ticker);
  const s = res ? buildSession(res) : null;
  if (!s){ body.innerHTML = '<div class="tp-note">Intraday data unavailable. Use EOD bias only.</div>'; return; }
  const p = computePlan(ticker, s);
  body.innerHTML = renderPlan(p);
  box.style.borderLeftColor = statusColor(p.status);
  if (isRegularSession() && (p.status.indexOf("ACTIVE")>=0 || p.status.indexOf("CHASE")>=0)) notifyLocal(ticker, p.status);
}
function refreshTradePlans(){ TP_TICKERS.forEach(updateTradePlan); }
document.querySelectorAll(".tp-enable").forEach(btn=>btn.addEventListener("click",()=>{
  if ("Notification" in window) Notification.requestPermission();
}));
refreshTradePlans();
setInterval(refreshTradePlans, 60000);
"""

def tradeplan_script(tp_data):
    return "<script>" + TRADEPLAN_JS.replace("__DASHBOARD_BIAS__", json.dumps(tp_data)) + "</script>"

def render(rtab, sptab, nqtab, vtab, now, errors, tp_js=""):
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
.bias-score{{position:relative;font-family:'Oswald',sans-serif;font-weight:600;font-size:46px;line-height:1;margin-top:2px;}}
.bias-max{{font-size:16px;color:var(--muted);}}
.bias-bar{{position:relative;height:9px;background:var(--s2);border:1px solid var(--border);border-radius:5px;margin:10px 0;overflow:hidden;}}
.bias-fill{{position:absolute;left:0;top:0;bottom:0;border-radius:5px;opacity:.85;}}
.bias-mid{{position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--muted);}}
.bucket{{position:relative;margin-top:12px;border-top:1px solid var(--border);padding-top:6px;}}
.bk-h{{font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:2px;}}
.tradeplan{{background:var(--surface);border:1px solid var(--border);border-left:4px solid #46505f;border-radius:14px;padding:16px 18px;margin-bottom:12px;}}
.tp-bar{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;}}
.tp-enable{{background:var(--s2);border:1px solid var(--border);color:var(--muted);border-radius:8px;padding:5px 10px;font-size:11px;cursor:pointer;font-family:'IBM Plex Mono',monospace;}}
.tp-head{{font-family:'Oswald',sans-serif;font-weight:600;font-size:24px;text-transform:uppercase;line-height:1.1;}}
.tp-sub{{font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:var(--muted);margin:4px 0 10px;}}
.tp-row{{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:12.5px;color:var(--muted);}}
.tp-row:last-child{{border-bottom:none;}}
.tp-v{{font-family:'IBM Plex Mono',monospace;color:var(--text);}}
.tp-plan{{margin-top:8px;padding-top:6px;border-top:1px dashed var(--border);}}
.tp-plan .tp-v{{color:var(--teal);font-weight:600;}}
.tp-note{{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--dim);margin-top:8px;line-height:1.5;}}
.tp-foot{{font-size:10px;color:var(--dim);margin-top:10px;border-top:1px solid var(--border);padding-top:8px;}}
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
  <div class="tab" data-t="val">검증</div>
</div>
<div class="panel active" id="p-risk">{rtab}</div>
<div class="panel" id="p-sp">{sptab}</div>
<div class="panel" id="p-nq">{nqtab}</div>
<div class="panel" id="p-val">{vtab}</div>
<div class="foot"><b>최종 판정은 추세 점수에 위험 게이트를 적용한 값이며 매매 신호가 아닙니다.</b> 위험모델은 페이퍼 검증 전입니다. 종가 기준이라 장중 실시간과 차이가 있습니다.</div>
</div>
<script>
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>{{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');
  document.getElementById('p-'+t.dataset.t).classList.add('active');
}}));
</script>
{tp_js}
</body></html>"""

# ===========================================================================
# Telegram 알림 (EOD — GitHub Actions 실행 시 Python 에서 전송)
# ===========================================================================
def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN"); chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                     "disable_web_page_preview": True}, timeout=15)
        r.raise_for_status(); return True
    except Exception as ex:
        print(f"  Telegram 전송 실패: {ex}"); return False

def load_alerts():
    try:
        with open(ALERT_PATH, encoding="utf-8") as f: return json.load(f)
    except Exception:
        return {}

def save_alerts(alerts):
    with open(ALERT_PATH, "w", encoding="utf-8") as f:
        json.dump(alerts, f, ensure_ascii=False, indent=1)

def mark_alert(alerts, today, key):
    """오늘 이 key 를 아직 안 보냈으면 True(=보낼 것). 최근 10일만 보관. 호출부 dict 를 직접 수정."""
    day = alerts.setdefault(today, [])
    if key in day:
        return False
    day.append(key)
    keep = set(sorted(alerts.keys())[-10:])
    for k in list(alerts.keys()):
        if k not in keep:
            alerts.pop(k, None)
    return True

def build_eod_alerts(ticker, d):
    """우리 tab_data(d) 구조 기준 EOD 알림 목록. (NO LONG 은 방향중심 문구로 표기)"""
    out = []
    bias = d["bias"]; ns = bias["score"]; nl = bias["label"]
    fsig = d["fsig"]; risk = d["risk"]; inst = d["inst"]; cta = inst["cta"]
    cat = inst.get("cat", {})
    head = f"위험 {risk['tier']} {risk['score']} · 기관 {inst['tier']}(stress {inst['stress']}) · {cta['tier']} {cta['score']}"

    # 1) Next Day Bias — 극단 구간만 (76+ / 25-)
    if ns >= 76 or ns <= 25:
        out.append(dict(key=f"{ticker}_bias_{nl}",
            text=(f"🚨 <b>{ticker} NEXT DAY BIAS</b>\n"
                  f"Score: <b>{ns}/100</b> · Bias: <b>{nl}</b>\n"
                  f"Final: <b>{fsig}</b>\n{head}\nAction: {bias['action']}")))

    # 2) Final Signal — 게이트/극단만
    if fsig in ("SELL ONLY", "SHORT ONLY", "STRONG SELL", "STRONG BUY"):
        dir_txt = {"SELL ONLY": "매수 금지 / 숏 우위", "SHORT ONLY": "숏 우위 / 롱 금지",
                   "STRONG SELL": "SHORT 편향", "STRONG BUY": "LONG 편향"}[fsig]
        out.append(dict(key=f"{ticker}_final_{fsig}",
            text=(f"⚡ <b>{ticker} FINAL SIGNAL</b>\n"
                  f"Signal: <b>{fsig}</b> → {dir_txt}\n"
                  f"Bias {ns}/100 · {nl}\n{head}")))

    # 3) Institutional STRESS
    if inst["tier"] == "STRESS":
        out.append(dict(key=f"{ticker}_inst_STRESS",
            text=(f"🏦 <b>{ticker} INSTITUTIONAL STRESS</b>\n"
                  f"stress <b>{inst['stress']}</b> · {cta['tier']} {cta['score']}\n"
                  f"신용 {cat.get('신용 회피','—')} · 폭 {cat.get('폭 약화','—')} · 변동성 {cat.get('변동성','—')}")))

    # 4) CTA FORCED SELLING
    if cta["tier"] == "CTA FORCED SELLING":
        out.append(dict(key=f"{ticker}_cta_FORCED",
            text=(f"🤖 <b>{ticker} CTA FORCED SELLING</b>\n"
                  f"CTA {cta['score']}/100 · posture {cta.get('posture')} · eff {cta.get('effective')}\n"
                  f"RV20 {cta.get('rv20')} · RV100med {cta.get('rv100_median')}")))
    return out

def dispatch_eod_alerts(today, spd, nqd):
    alerts = load_alerts(); sent = 0
    for ticker, d in (("SPY", spd), ("QQQ", nqd)):
        for a in build_eod_alerts(ticker, d):
            if mark_alert(alerts, today, a["key"]):
                if send_telegram(a["text"]): sent += 1
    save_alerts(alerts)
    return sent

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

    try: spy_df = yf_ohlc("SPY", "2y")
    except Exception as ex: errors["spy_df"] = str(ex); spy_df = None
    try: qqq_df = yf_ohlc("QQQ", "2y")
    except Exception as ex: errors["qqq_df"] = str(ex); qqq_df = None
    try:
        mstress = compute_market_stress(macro, spy_df) if spy_df is not None else dict(stress_base=0, cat={})
        inst_sp = finalize_institutional(mstress, spy_df) if spy_df is not None else _empty_inst()
        inst_nq = finalize_institutional(mstress, qqq_df) if qqq_df is not None else inst_sp
    except Exception as ex:
        errors["inst"] = str(ex); inst_sp = _empty_inst(); inst_nq = _empty_inst()

    try: sp_risk = compute_risk_bias("SPY", macro, market)
    except Exception as ex: errors["sp_risk"] = str(ex); sp_risk = _empty_risk()
    try: nq_risk = compute_risk_bias("QQQ", macro, market)
    except Exception as ex: errors["nq_risk"] = str(ex); nq_risk = _empty_risk()

    # CTA/Institutional 을 Bias 점수에 반영 (build 전에 cond 주입)
    for risk_d, inst_d in ((sp_risk, inst_sp), (nq_risk, inst_nq)):
        risk_d["cond"]["institutional_tier"] = inst_d["tier"]
        risk_d["cond"]["cta_tier"] = inst_d["cta"]["tier"]

    spd = build_tab_data("SPY", sp, sp_risk, inst_sp)
    nqd = build_tab_data("QQQ", nq, nq_risk, inst_nq)

    def _pred(d, trend_sig, risk, inst):
        return dict(next_score=d["bias"]["score"], next_label=d["bias"]["label"],
                    trend_score=trend_sig[1], risk_score=risk["score"],
                    inst_tier=inst["tier"], inst_stress=inst["stress"],
                    cta_score=inst["cta"]["score"], cta_tier=inst["cta"]["tier"], final=d["fsig"],
                    close=risk["close"], open=risk["open"], high=risk["high"], low=risk["low"], atr=risk["atr"])
    pred = {"sp": _pred(spd, sp, sp_risk, inst_sp), "nq": _pred(nqd, nq, nq_risk, inst_nq)}

    today = dt.datetime.now(NY).strftime("%Y-%m-%d")          # 거래일 기준 뉴욕
    hist = load_hist()
    hist = grade_and_record(hist, today, pred)
    with open(HIST_PATH, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)

    now = dt.datetime.now(NY).strftime("%Y-%m-%d %H:%M ET")
    rtab  = risk_tab(rvals, rst, lvl, red, amber)
    sptab = signal_tab("S&P 500 · SPY", spd, bias_bucket_stats(hist, "sp"))
    nqtab = signal_tab("나스닥 100 · QQQ", nqd, bias_bucket_stats(hist, "nq"))
    vtab  = validation_tab(hist)
    tp_data = {
        "SPY": dict(nextScore=spd["bias"]["score"], nextLabel=spd["bias"]["label"],
                    prevHigh=sp_risk["high"], prevLow=sp_risk["low"], prevClose=sp_risk["close"], refDate=today),
        "QQQ": dict(nextScore=nqd["bias"]["score"], nextLabel=nqd["bias"]["label"],
                    prevHigh=nq_risk["high"], prevLow=nq_risk["low"], prevClose=nq_risk["close"], refDate=today),
    }
    tp_js = tradeplan_script(tp_data)
    html = render(rtab, sptab, nqtab, vtab, now, errors, tp_js)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    write_pwa_assets()

    sent = dispatch_eod_alerts(today, spd, nqd)

    print(f"  위험단계: {LEVELS_NM(lvl)} (적{red}/황{amber})")
    print(f"  기관 SPY {inst_sp['tier']}(s{inst_sp['stress']})·{inst_sp['cta']['tier']} | QQQ {inst_nq['tier']}(s{inst_nq['stress']})·{inst_nq['cta']['tier']}")
    print(f"  SPY 추세 {sp[0]} {sp[1]} / Risk {sp_risk['tier']} → FINAL {spd['fsig']} {spd['fscore']} / Bias {spd['bias']['score']}")
    print(f"  QQQ 추세 {nq[0]} {nq[1]} / Risk {nq_risk['tier']} → FINAL {nqd['fsig']} {nqd['fscore']} / Bias {nqd['bias']['score']}")
    if errors: print(f"  실패: {list(errors.keys())}")
    print(f"  텔레그램 알림: {sent}건 전송")
    print(f"  생성: {OUT_HTML}")

def LEVELS_NM(l): return {"calm":"평시","watch":"주의","alert":"경보","crisis":"위기"}[l]

if __name__ == "__main__":
    main()
