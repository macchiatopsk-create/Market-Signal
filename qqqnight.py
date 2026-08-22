"""qqqnight — 트윗 검증 겸: 오버나이트 vs 장중 분해 (QQQ·MU), 비용 스트레스,
우리 갭일(0.2~1.5%)이 오버나이트 수익 분포에서 차지하는 위치.

오버나이트 = 전일 종가 매수 → 당일 시가 매도 (Open/PrevClose - 1)
장중       = 당일 시가 매수 → 당일 종가 매도 (Close/Open - 1)
비용: 왕복 bp를 오버나이트 수확 전략에 일할 차감 (매일 1왕복).
"""
import json, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np


def load(tk, period="max"):
    d = yf.download(tk, period=period, interval="1d", auto_adjust=True, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d[["Open", "Close"]].dropna()
    d["on"] = d["Open"] / d["Close"].shift(1) - 1          # 오버나이트
    d["id"] = d["Close"] / d["Open"] - 1                   # 장중
    return d.dropna()


def cum(x):
    return (np.prod(1 + x) - 1) * 100


def seg(d, tag):
    on, idr = d["on"].values, d["id"].values
    return (f"  {tag:<14s} n={len(d):5d}  오버나이트 {cum(on):+14,.0f}%  "
            f"장중 {cum(idr):+10.1f}%  (일평균 {on.mean()*1e4:+.2f}bp / {idr.mean()*1e4:+.2f}bp)")


def main():
    out = []
    q = load("QQQ")
    out.append("── QQQ 분해 ──")
    for tag, dd in [("전체(99~)", q), ("2010~", q[q.index >= "2010-01-01"]),
                    ("최근 2년", q[q.index >= str(dt.date.today() - dt.timedelta(days=730))])]:
        out.append(seg(dd, tag))
    out.append("")
    out.append("── QQQ 오버나이트 수확 전략 · 왕복비용 스트레스 (2010~) ──")
    d10 = q[q.index >= "2010-01-01"]
    on = d10["on"].values
    yrs = len(d10) / 252
    for bp in [0, 1, 2, 3, 5]:
        net = on - bp / 1e4
        cagr = ((np.prod(1 + net)) ** (1 / yrs) - 1) * 100
        out.append(f"  왕복 {bp}bp  누적 {cum(net):+12,.0f}%  CAGR {cagr:+6.2f}%")
    bh = (d10["Close"].iloc[-1] / d10["Close"].iloc[0]) ** (1 / yrs) - 1
    out.append(f"  (참고: 그냥 보유 CAGR {bh*100:+.2f}% — 왕복 몇 bp에 우위가 사라지는지 보세요)")
    out.append("")
    out.append("── 우리 갭일이 오버나이트 분포에서 차지하는 위치 (QQQ 2010~) ──")
    g = np.abs(d10["on"].values) * 100
    m_our = (g >= 0.2) & (g < 1.5)
    m_sml = g < 0.2
    m_big = g >= 1.5
    tot = np.abs(d10["on"].values).sum()
    for tag, m in [("갭 0.2~1.5%(우리)", m_our), ("갭 <0.2%", m_sml), ("갭 ≥1.5%", m_big)]:
        share = np.abs(d10["on"].values[m]).sum() / tot * 100
        out.append(f"  {tag:<16s} 일수 {m.sum():4d} ({m.mean()*100:4.1f}%)  "
                   f"|오버나이트 무브| 점유 {share:5.1f}%  "
                   f"다음 장중 평균 {d10['id'].values[m].mean()*1e4:+.2f}bp")
    out.append("")
    out.append("── MU (트윗 검증) ──")
    mu = load("MU")
    for tag, dd in [("전체", mu), ("최근 2년", mu[mu.index >= str(dt.date.today() - dt.timedelta(days=730))])]:
        out.append(seg(dd, tag))
    mo = mu["on"].values
    for bp in [0, 3, 5]:
        out.append(f"  MU 오버나이트 왕복 {bp}bp → 누적 {cum(mo - bp/1e4):+16,.0f}%")
    out.append("")
    out.append("※ 배당조정 종가 기준(auto_adjust) — 트윗 숫자와 절대값은 다를 수 있음, 구조 비교용")
    return out


if __name__ == "__main__":
    try:
        r = main()
    except Exception:
        r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r)
    print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("qqqnight_result.json", "w"), ensure_ascii=False, indent=1)
