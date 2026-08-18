"""
갭필 전략 선물 실행 시뮬레이션.

신호는 현물(QQQ/SPY)에서 생성 — 갭은 전날 16:00 종가 vs 09:30 시가에만 존재하고
선물은 그 시간에도 거래되므로 갭이 없다. 실행만 선물로 한다.

  QQQ 신호 -> MNQ (NDX 1pt = $2)
  SPY 신호 -> MES (SPX 1pt = $5)

규칙: 첫봉(09:30~10:30) 커버>=30% -> 10:30 종가 진입
      TP=전날종가 / SL=첫봉극점 / 14:30 시간청산
비용: 왕복 수수료 + 슬리피지 1틱 (MNQ 0.25pt=$0.50, MES 0.25pt=$1.25) 진입·청산 각각

자본 $1,000 시작. 최근 1년(252거래일). 1계약 고정.
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

COVER_MIN = 0.30
START_CAP = 5000.0
SPECS = {  # 신호티커: (선물명, 지수티커, 틱당달러, 포인트당달러, 왕복수수료, 슬리피지틱)
    "QQQ": ("MNQ", "^NDX",  0.50, 2.0, 1.24, 1),
    "SPY": ("MES", "^GSPC", 1.25, 5.0, 1.24, 1),
}


def _n(x):
    try: x.index = x.index.tz_localize(None)
    except (TypeError, AttributeError): pass
    x.index = pd.to_datetime(x.index).normalize()
    return x[~x.index.duplicated(keep="last")]


def trades_for(tk):
    df = yf.download(tk, period="2y", interval="1h", prepost=False,
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    df.index = df.index.tz_convert("America/New_York")
    df = df[(df.index.time >= dt.time(9, 30)) & (df.index.time < dt.time(16, 0))]

    out = []; prev_close = None
    for d in sorted(set(df.index.date)):
        g = df[df.index.date == d]
        if len(g) < 5:
            if len(g): prev_close = float(g["Close"].iloc[-1])
            continue
        O = [float(x) for x in g["Open"]]; H = [float(x) for x in g["High"]]
        L = [float(x) for x in g["Low"]];  C = [float(x) for x in g["Close"]]
        if prev_close:
            gap = O[0] - prev_close; gp = gap / prev_close * 100
            if abs(gp) >= 0.05:
                sgn = 1 if gap > 0 else -1
                cover = ((O[0] - C[0]) / gap) if sgn > 0 else ((C[0] - O[0]) / abs(gap))
                if cover >= COVER_MIN:
                    ep = C[0]; tgt = prev_close; stop = H[0] if sgn > 0 else L[0]
                    fill = stopb = None; mae = 0.0
                    for i in range(1, len(C)):
                        adv = (H[i]-ep)/ep*100 if sgn > 0 else (ep-L[i])/ep*100
                        mae = max(mae, adv)
                        if stopb is None and ((H[i] >= stop) if sgn > 0 else (L[i] <= stop)): stopb = i
                        if fill is None and ((L[i] <= tgt) if sgn > 0 else (H[i] >= tgt)): fill = i
                        if fill is not None or (stopb is not None and stopb <= 4): break
                    if fill is not None and (stopb is None or fill <= stopb) and fill <= 4:
                        pct, res = abs(tgt-ep)/ep*100, "FILL"
                    elif stopb is not None and stopb <= 4:
                        pct, res = -abs(stop-ep)/ep*100, "STOP"
                    else:
                        idx = min(4, len(C)-1); px = C[idx]
                        pct = ((ep-px)/ep*100) if sgn > 0 else ((px-ep)/ep*100)
                        res = "CUT"
                    out.append(dict(d=str(d), sgn=sgn, pct=pct, res=res, mae=mae))
        prev_close = C[-1]
    return out


def simulate(tk, trs, idx_close, days=252, contracts=1, margin=None):
    name, itk, tickusd, ptusd, comm, sliptick = SPECS[tk]
    trs = [t for t in trs if t["d"] >= str(dt.date.today() - dt.timedelta(days=int(days*1.45)))]
    cap = START_CAP; peak = cap; mdd = 0.0; curve = []; rows = []
    wins = 0
    for t in trs:
        lvl = idx_close.get(t["d"])
        if lvl is None: continue
        nc = contracts
        if margin:                                   # 자본 비례: 마진 기준 계약 수
            nc = max(0, int(cap // margin))
            if nc == 0: continue
        pts = t["pct"] / 100 * lvl                  # 지수 포인트 이동
        gross = pts * ptusd * nc
        cost = (comm + 2 * sliptick * tickusd) * nc
        net = gross - cost
        cap += net
        peak = max(peak, cap); mdd = max(mdd, (peak - cap) / peak * 100)
        if net > 0: wins += 1
        curve.append(cap)
        rows.append(dict(d=t["d"], res=t["res"], nc=nc, pct=round(t["pct"], 3),
                         pts=round(pts, 1), net=round(net, 2), cap=round(cap, 2)))
        if cap <= 0: break
    n = len(rows)
    if n == 0: return None
    g = sum(r["net"] for r in rows if r["net"] > 0)
    l = -sum(r["net"] for r in rows if r["net"] <= 0)
    return dict(name=name, n=n, wins=wins, cap=round(cap, 2), mdd=round(mdd, 1),
                pf=round(g/l, 2) if l else 99.0, rows=rows,
                best=max(r["net"] for r in rows), worst=min(r["net"] for r in rows),
                avg=round(sum(r["net"] for r in rows)/n, 2))


def main():
    out = []
    for tk in ("QQQ", "SPY"):
        name, itk, tickusd, ptusd, comm, sliptick = SPECS[tk]
        idx = _n(yf.Ticker(itk).history(period="2y")["Close"].dropna())
        idx_close = {str(d.date()): float(v) for d, v in idx.items()}
        trs = trades_for(tk)
        MARGIN = {"MNQ": 2500.0, "MES": 2500.0}[name]   # 보수적 데이마진 가정
        scen = [("1계약 고정", dict(contracts=1)),
                (f"자본비례(마진 ${MARGIN:.0f}/계약)", dict(margin=MARGIN))]
        base = simulate(tk, trs, idx_close, **scen[0][1])
        if not base:
            out.append(f"[{tk}] 트레이드 없음"); continue
        r = base
        cost = comm + 2*sliptick*tickusd
        out.append(f"\n{'='*104}\n[{tk} 신호 → {name} 선물] 최근 1년 · 1계약 · 자본 ${START_CAP:.0f} 시작"
                   f"\n지수 {itk} · 1pt=${ptusd} · 왕복비용 ${cost:.2f}\n{'='*104}")
        out.append(f"  거래 {r['n']}건 · 승률 {r['wins']/r['n']*100:.1f}% · PF {r['pf']}")
        out.append(f"  최종 자본 ${r['cap']:,.0f}  (수익 ${r['cap']-START_CAP:+,.0f} · "
                   f"{(r['cap']/START_CAP-1)*100:+.0f}%)")
        out.append(f"  건당 평균 ${r['avg']:+,.2f} · 최대이익 ${r['best']:+,.0f} · 최대손실 ${r['worst']:+,.0f}")
        out.append(f"  최대낙폭(MDD) {r['mdd']}%")
        out.append(f"  ※ 명목가치 = 지수 × ${ptusd} ≈ ${idx.iloc[-1]*ptusd:,.0f} → 자본 대비 레버리지 "
                   f"{idx.iloc[-1]*ptusd/START_CAP:.0f}배")
        r2 = simulate(tk, trs, idx_close, **scen[1][1])
        if r2:
            out.append(f"  [{scen[1][0]}] 최종 ${r2['cap']:,.0f} ({(r2['cap']/START_CAP-1)*100:+.0f}%) "
                       f"· MDD {r2['mdd']}% · 최대손실 ${r2['worst']:+,.0f} · 최종계약수 {r2['rows'][-1]['nc']}")
        # 손실 상위 5건
        worst5 = sorted(r["rows"], key=lambda x: x["net"])[:5]
        out.append("  손실 상위 5건: " + " · ".join(f"{w['d'][5:]} {w['res']} ${w['net']:+,.0f}" for w in worst5))
        # 월별
        bym = {}
        for row in r["rows"]: bym.setdefault(row["d"][:7], []).append(row["net"])
        out.append("  월별 손익:")
        for m in sorted(bym):
            v = bym[m]
            out.append(f"    {m}  {len(v):2d}건  ${sum(v):+8,.0f}")
    return out


if __name__ == "__main__":
    try: r = main()
    except Exception: r = ["실패:\n" + traceback.format_exc()]
    txt = "\n".join(r); print(txt)
    json.dump({"at": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "report": txt},
              open("futures_result.json", "w"), ensure_ascii=False, indent=1)
