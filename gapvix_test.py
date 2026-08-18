"""갭 전략 × VIX: VIX 수준에 따라 움직임이 큰가/빠른가.

측정: VIX 기간구조 백분위 및 VIX 절대레벨 구간별로
      갭 크기 / 타깃거리 / 갭필 도달률 / 소요시간 / MAE / 갭필 후 추가진행 / PF
조건: 갭>=0.05% & 첫1시간 커버>=30% (반전 구간) · 진입 첫봉종가 · 손절 당일극점 · 컷 14:30
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
    except (TypeError,AttributeError): pass
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

def vix_maps():
    a=_grab("^VIX9D");  a = a if a is not None else _grab("^VIX")
    b=_grab("^VIX3M");  v=_grab("^VIX")
    if a is None or b is None: return {},{}
    ts=(a/b.reindex(a.index).ffill()).dropna()
    def _p(w):
        if len(w)<2: return float("nan")
        return float((w[:-1]<w[-1]).sum())/(len(w)-1)*100
    pct=ts.rolling(252).apply(_p,raw=True).shift(1)
    pm={str(pd.Timestamp(d).date()):float(x) for d,x in pct.dropna().items()}
    vl={str(pd.Timestamp(d).date()):float(x) for d,x in (v.shift(1).dropna().items() if v is not None else [])}
    return pm,vl

def build(tk):
    df=yf.download(tk,period="2y",interval="1h",prepost=False,auto_adjust=False,progress=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.dropna(); df.index=df.index.tz_convert("America/New_York")
    df=df[(df.index.time>=dt.time(9,30))&(df.index.time<dt.time(16,0))]
    rows=[]; pc=None
    for d in sorted(set(df.index.date)):
        g=df[df.index.date==d]
        if len(g)<5:
            if len(g): pc=float(g["Close"].iloc[-1])
            continue
        O=[float(x) for x in g["Open"]];H=[float(x) for x in g["High"]]
        L=[float(x) for x in g["Low"]];C=[float(x) for x in g["Close"]]
        if pc:
            gap=O[0]-pc; gp=gap/pc*100
            if abs(gp)>=0.05:
                sgn=1 if gap>0 else -1
                cover=((O[0]-C[0])/gap) if sgn>0 else ((C[0]-O[0])/abs(gap))
                if cover>=0.30:
                    ep=C[0]; tgt=pc; stop=H[0] if sgn>0 else L[0]; cut=4
                    hitT=hitS=None; mae=0.0
                    for i in range(1,min(cut+1,len(C))):
                        adv=(H[i]-ep)/ep*100 if sgn>0 else (ep-L[i])/ep*100
                        mae=max(mae,adv)
                        if hitS is None and ((H[i]>=stop) if sgn>0 else (L[i]<=stop)): hitS=i
                        if hitT is None and ((L[i]<=tgt) if sgn>0 else (H[i]>=tgt)): hitT=i
                        if hitT is not None or hitS is not None: break
                    if hitT is not None and (hitS is None or hitT<=hitS): pnl,res=abs(tgt-ep)/ep*100,"TGT"
                    elif hitS is not None: pnl,res=-abs(stop-ep)/ep*100,"STOP"
                    else:
                        idx=min(cut,len(C)-1)
                        pnl=((ep-C[idx])/ep*100) if sgn>0 else ((C[idx]-ep)/ep*100); res="CUT"
                    # 갭필 후 추가 진행 (그날 극점이 타깃을 얼마나 넘었나)
                    beyond=((tgt-min(L))/tgt*100) if sgn>0 else ((max(H)-tgt)/tgt*100)
                    rows.append(dict(d=str(d),sgn=sgn,gp=abs(gp),room=abs(tgt-ep)/ep*100,
                                     res=res,pnl=pnl,bar=hitT,mae=mae,beyond=max(0.0,beyond)))
        pc=C[-1]
    return rows

def rep(ss,lab,out):
    n=len(ss)
    if n<8: out.append(f"    {lab:20s} n={n:3d} 표본부족"); return
    t=[r for r in ss if r["res"]=="TGT"]
    bars=sorted(r["bar"] for r in t if r["bar"] is not None)
    w=sum(1 for r in ss if r["pnl"]>0); ci=wilson(len(t),n)
    g=sum(r["pnl"] for r in ss if r["pnl"]>0); l=-sum(r["pnl"] for r in ss if r["pnl"]<=0)
    out.append(f"    {lab:20s} n={n:3d} | 갭 {sum(r['gp'] for r in ss)/n:.3f}% "
               f"타깃거리 {sum(r['room'] for r in ss)/n:.3f}% | 도달 {len(t)/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
               f"소요 {bars[len(bars)//2]*60 if bars else 0:3d}분 | MAE {sum(r['mae'] for r in ss)/n:.3f}% "
               f"초과진행 {sum(r['beyond'] for r in ss)/n:.3f}% | 승률 {w/n*100:5.1f}% PF {g/l if l else 99:5.2f} "
               f"평균 {sum(r['pnl'] for r in ss)/n:+.3f}%")

def main():
    pm,vl=vix_maps()
    out=[f"VIX 백분위맵 {len(pm)}일 · VIX레벨맵 {len(vl)}일",
         "조건: 갭 & 첫1시간 커버>=30% · 진입 첫봉종가 · 타깃 전날종가 · 손절 당일극점 · 컷 14:30"]
    for tk in ("QQQ","SPY"):
        rows=build(tk)
        out.append(f"\n{'='*130}\n[{tk}] 조건충족 {len(rows)}일\n{'='*130}")
        for sgn,nm in ((1,"갭업→숏"),(-1,"갭다운→롱")):
            ss=[r for r in rows if r["sgn"]==sgn]
            out.append(f"  ── {nm} (n={len(ss)}) ──")
            rep(ss,"전체",out)
            out.append("   [VIX 기간구조 백분위]")
            for lo,hi in ((0,25),(25,50),(50,75),(75,101)):
                sel=[r for r in ss if lo<=pm.get(r["d"],-1)<hi]
                rep(sel,f"백분위 {lo}~{hi}",out)
            if vl:
                out.append("   [VIX 절대레벨]")
                for lo,hi in ((0,14),(14,17),(17,20),(20,99)):
                    sel=[r for r in ss if lo<=vl.get(r["d"],-1)<hi]
                    rep(sel,f"VIX {lo}~{hi}",out)
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("gapvix_result.json","w"),ensure_ascii=False,indent=1)
