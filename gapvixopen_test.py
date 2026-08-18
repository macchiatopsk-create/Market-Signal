"""갭 전략 x VIX 변화율. 절대레벨이 아니라 '얼마나 튀었나'.

전날VIX변화 : 장 시작 전에 확정 -> 실전 신호로 사용 가능
당일VIX변화 : 동시점이라 신호로는 불가. 상관 확인용(참고)
조건: 갭 & 첫1시간 커버>=30% · 진입 첫봉종가 · 타깃 전날종가 · 손절 당일극점 · 컷14:30
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

def _grab_ohlc(tk,tries=4):
    import time
    for i in range(tries):
        try:
            d=yf.Ticker(tk).history(period="3y")[["Open","Close"]].dropna()
            if len(d)>100:
                try: d.index=d.index.tz_localize(None)
                except (TypeError,AttributeError): pass
                d.index=pd.to_datetime(d.index).normalize()
                return d[~d.index.duplicated(keep="last")]
        except Exception: pass
        time.sleep(5*(2**i))
    return None

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
                    beyond=((tgt-min(L))/tgt*100) if sgn>0 else ((max(H)-tgt)/tgt*100)
                    rows.append(dict(d=str(d),sgn=sgn,gp=abs(gp),room=abs(tgt-ep)/ep*100,
                                     res=res,pnl=pnl,mae=mae,beyond=max(0.0,beyond)))
        pc=C[-1]
    return rows

def rep(ss,lab,out):
    n=len(ss)
    if n<6: out.append(f"    {lab:22s} n={n:3d} 표본부족"); return
    t=[r for r in ss if r["res"]=="TGT"]
    w=sum(1 for r in ss if r["pnl"]>0); ci=wilson(w,n)
    g=sum(r["pnl"] for r in ss if r["pnl"]>0); l=-sum(r["pnl"] for r in ss if r["pnl"]<=0)
    s2=sorted(ss,key=lambda x:-x["pnl"])[2:]
    g2=sum(r["pnl"] for r in s2 if r["pnl"]>0); l2=-sum(r["pnl"] for r in s2 if r["pnl"]<=0)
    ds=sorted(r["d"] for r in ss); half=ds[len(ds)//2]
    def _pf(x):
        a=sum(r["pnl"] for r in x if r["pnl"]>0); b=-sum(r["pnl"] for r in x if r["pnl"]<=0)
        return (a/b) if b>0 else 99.0
    out.append(f"    {lab:22s} n={n:3d} 갭 {sum(r['gp'] for r in ss)/n:.3f}% "
               f"타깃거리 {sum(r['room'] for r in ss)/n:.3f}% 초과진행 {sum(r['beyond'] for r in ss)/n:.3f}% "
               f"| 도달 {len(t)/n*100:5.1f}% 승률 {w/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
               f"PF {g/l if l else 99:5.2f} |상위2 {g2/l2 if l2 else 99:5.2f} 평균 {sum(r['pnl'] for r in ss)/n:+.3f}% "
               f"| 반반 {_pf([r for r in ss if r['d']<half]):5.2f}/{_pf([r for r in ss if r['d']>=half]):5.2f}")

def main():
    v=_grab_ohlc("^VIX")
    if v is None: return ["^VIX 수집 실패"]
    # 09:30 개장 시점에 알 수 있는 값: 전일 종가 -> 당일 시가
    openchg=(v["Open"]/v["Close"].shift(1)-1)*100
    closechg=(v["Close"]/v["Close"].shift(1)-1)*100
    same={str(pd.Timestamp(d).date()):float(x) for d,x in openchg.dropna().items()}
    prev={str(pd.Timestamp(d).date()):float(x) for d,x in closechg.shift(1).dropna().items()}
    out=[f"^VIX 맵 {len(same)}일 · 개장변화=전일종가→당일시가(09:30 확정) · 전일변화=전일 종가기준",
         "조건: 갭 & 첫1시간 커버>=30% · 진입 첫봉종가 · 타깃 전날종가 · 손절 당일극점 · 컷14:30"]
    BINS=[(-99,-4,"≤-4%"),(-4,-3,"-4~-3%"),(-3,-2,"-3~-2%"),(-2,-1,"-2~-1%"),
          (-1,0,"-1~0%"),(0,1,"0~+1%"),(1,2,"+1~2%"),(2,3,"+2~3%"),
          (3,4,"+3~4%"),(4,99,"≥+4%")]
    CUM=[(-99,-2,"≤-2% (누적)"),(-99,-1,"≤-1% (누적)"),(-99,0,"<0% (누적)"),
         (0,99,">=0% (누적)"),(1,99,">=+1% (누적)"),(2,99,">=+2% (누적)"),(3,99,">=+3% (누적)")]
    for tk in ("QQQ","SPY"):
        rows=build(tk)
        out.append(f"\n{'='*134}\n[{tk}] 조건충족 {len(rows)}일\n{'='*134}")
        for sgn,nm in ((1,"갭업→숏"),(-1,"갭다운→롱")):
            ss=[r for r in rows if r["sgn"]==sgn]
            out.append(f"  ── {nm} (n={len(ss)}) ──")
            rep(ss,"전체",out)
            out.append("   [개장 VIX 변화율 · 1% 단위]")
            for lo,hi,lab in BINS:
                rep([r for r in ss if lo<=same.get(r["d"],0)<hi],lab,out)
            out.append("   [개장 VIX 변화율 · 누적 임계]")
            for lo,hi,lab in CUM:
                rep([r for r in ss if lo<=same.get(r["d"],0)<hi],lab,out)
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("gapvixopen_result.json","w"),ensure_ascii=False,indent=1)
