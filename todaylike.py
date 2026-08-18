"""오늘(2026-08-18)과 같은 조건의 과거 사례 탐색.

오늘 조건:
  · 갭다운 -1.32% (큰 갭다운, 1.0% 초과)
  · 프리마켓 위치 0.025 (레인지 최하단)
  · VIX 기간구조 백분위 17.9% (저구간)
  · RSI 극단 과매도

측정: 그런 날 이후 장중/다음날 어떻게 됐나 (롱/숏 양방향)
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

def wilson(k,n):
    if n==0: return (0.0,0.0)
    p,z=k/n,1.96; d=1+z*z/n
    c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (round(max(0,c-h)*100,1),round(min(1,c+h)*100,1))

def _n(x):
    try: x.index=x.index.tz_localize(None)
    except: pass
    x.index=pd.to_datetime(x.index).normalize()
    return x[~x.index.duplicated(keep="last")]

def _grab(tk,tries=4):
    import time
    for i in range(tries):
        try:
            s=yf.Ticker(tk).history(period="3y")["Close"].dropna()
            if len(s)>100: return _n(s)
        except Exception: pass
        time.sleep(5*(2**i))
    return None

def main():
    out=[]
    a=_grab("^VIX9D"); a = a if a is not None else _grab("^VIX")
    b=_grab("^VIX3M")
    vmap={}
    if a is not None and b is not None:
        ts=(a/b.reindex(a.index).ffill()).dropna()
        def _p(w):
            if len(w)<2: return float("nan")
            return float((w[:-1]<w[-1]).sum())/(len(w)-1)*100
        pct=ts.rolling(252).apply(_p,raw=True).shift(1)
        vmap={str(pd.Timestamp(k).date()):float(v) for k,v in pct.dropna().items()}

    df=yf.download("QQQ",period="2y",interval="1h",prepost=True,
                   auto_adjust=False,progress=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.dropna(); df.index=df.index.tz_convert("America/New_York")

    rows=[]; pc=None
    for d in sorted(set(df.index.date)):
        g=df[df.index.date==d]
        pm=g[(g.index.time>=dt.time(4,0))&(g.index.time<dt.time(9,30))]
        rt=g[(g.index.time>=dt.time(9,30))&(g.index.time<dt.time(16,0))]
        if len(rt)<5:
            if len(rt): pc=float(rt["Close"].iloc[-1])
            continue
        ds=str(d)
        O=[float(x) for x in rt["Open"]];H=[float(x) for x in rt["High"]]
        L=[float(x) for x in rt["Low"]];C=[float(x) for x in rt["Close"]]
        if pc and len(pm)>=3:
            pmh,pml=float(pm["High"].max()),float(pm["Low"].min())
            pos=(O[0]-pml)/(pmh-pml) if pmh>pml else 0.5
            gp=(O[0]-pc)/pc*100
            rows.append(dict(d=ds,gp=gp,pos=pos,pc=pc,op=O[0],
                             ret=(C[-1]/O[0]-1)*100,
                             lo=(min(L)/O[0]-1)*100, hi=(max(H)/O[0]-1)*100,
                             c1030=(C[0]/O[0]-1)*100,
                             vix=vmap.get(ds)))
        pc=C[-1]
    # 다음날 수익
    for i in range(len(rows)-1):
        rows[i]["nxt"]=rows[i+1]["ret"]
        rows[i]["nxt_gap"]=rows[i+1]["gp"]

    def rep(sel,lab):
        n=len(sel)
        if n<3: out.append(f"  {lab:32s} n={n:3d} 사례부족"); return
        up=sum(1 for r in sel if r["ret"]>0); ci=wilson(up,n)
        nxt=[r["nxt"] for r in sel if "nxt" in r]
        nup=sum(1 for x in nxt if x>0)
        out.append(f"  {lab:32s} n={n:3d} | 당일 09:30→종가 평균 {sum(r['ret'] for r in sel)/n:+.3f}% "
                   f"상승 {up/n*100:4.1f}%({ci[0]:.0f}~{ci[1]:.0f}) "
                   f"| 저점 {sum(r['lo'] for r in sel)/n:+.3f}% 고점 {sum(r['hi'] for r in sel)/n:+.3f}% "
                   f"| 다음날 평균 {(sum(nxt)/len(nxt) if nxt else 0):+.3f}% 상승 "
                   f"{(nup/len(nxt)*100 if nxt else 0):4.1f}%")

    out.append(f"QQQ 1시간봉 2년 · {len(rows)}일 · 오늘(08-18): 갭 -1.32% · PM위치 0.025 · VIX백분위 17.9%")
    out.append("")
    out.append("[단일 조건]")
    rep(rows,"전체 (베이스라인)")
    rep([r for r in rows if r["gp"]<=-1.0],"갭다운 -1.0% 이하")
    rep([r for r in rows if r["gp"]<=-1.2],"갭다운 -1.2% 이하")
    rep([r for r in rows if r["pos"]<=0.10],"PM위치 0.10 이하")
    rep([r for r in rows if r["vix"] is not None and r["vix"]<25],"VIX백분위 25 미만")
    out.append("")
    out.append("[복합 조건 — 오늘과 유사]")
    rep([r for r in rows if r["gp"]<=-1.0 and r["pos"]<=0.15],"갭다운1%+ & PM위치 0.15이하")
    rep([r for r in rows if r["gp"]<=-1.0 and r["pos"]<=0.15
         and r["vix"] is not None and r["vix"]<30],"위 + VIX백분위 30미만")
    out.append("")
    out.append("[가장 유사한 개별 사례]")
    cand=[r for r in rows if r["gp"]<=-0.9 and r["pos"]<=0.20]
    cand.sort(key=lambda r: abs(r["gp"]+1.32)+abs(r["pos"]-0.025)*3)
    out.append(f"  {'날짜':11s} {'갭%':>7s} {'PM':>5s} {'VIX%':>5s} {'09:30→종가':>10s} {'장중저점':>9s} {'장중고점':>9s} {'다음날':>8s}")
    for r in cand[:12]:
        out.append(f"  {r['d']:11s} {r['gp']:+7.2f} {r['pos']:5.2f} "
                   f"{(r['vix'] if r['vix'] is not None else -1):5.0f} {r['ret']:+10.2f}% "
                   f"{r['lo']:+8.2f}% {r['hi']:+8.2f}% {r.get('nxt',0):+7.2f}%")
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("todaylike_result.json","w"),ensure_ascii=False,indent=1)
