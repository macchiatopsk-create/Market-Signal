"""3층(v9) 전략 사이징 비교 — 갭 트랙과 동일 기준으로.

진입: VIX9D/VIX3M 백분위>=50 & 프리마켓 위치>0.5 & VWAP -1σ 터치 → ITM CALL
청산: TP1 VWAP(50%) → 러너 +1σ / 손절 당일저점 / 14:30 컷
옵션: 실측 파라미터 (델타0.69, 스프레드2.2%, 세타 $1.39/일, ITM 0.5%)
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

DELTA=0.69; SPREAD=2.2; ITM_PCT=0.50; TV_RATIO=0.28; THETA_PER_HR=0.214
def norm(d):
    try: d.index=d.index.tz_localize(None)
    except: pass
    d.index=pd.to_datetime(d.index).normalize()
    return d[~d.index.duplicated(keep="last")]
def grab(tk,tries=6):
    import time
    for i in range(tries):
        for fn in (lambda: yf.Ticker(tk).history(period="3y")["Close"].dropna(),
                   lambda: yf.download(tk,period="3y",progress=False)["Close"].dropna()):
            try:
                s=fn()
                if isinstance(s,pd.DataFrame): s=s.iloc[:,0]
                if len(s)>100: return norm(s)
            except Exception: pass
        time.sleep(8*(i+1))
    return None

def trades():
    a=grab("^VIX9D"); b=grab("^VIX3M"); v=grab("^VIX")
    if v is None: raise RuntimeError("^VIX 수집 실패")
    if a is None or b is None:
        print(f"  VIX9D={a is not None} VIX3M={b is not None} — 게이트 없이 전체 실행")
        vmap=None
    else:
        ts=(a/b.reindex(a.index).ffill()).dropna()
        def _p(w):
            if len(w)<2: return float("nan")
            return float((w[:-1]<w[-1]).sum())/(len(w)-1)*100
        pct=ts.rolling(252).apply(_p,raw=True).shift(1)
        vmap={str(pd.Timestamp(k).date()):float(x) for k,x in pct.dropna().items()}
    vlv={str(pd.Timestamp(k).date()):float(x) for k,x in v.items()}

    df=yf.download("QQQ",period="60d",interval="15m",prepost=True,
                   auto_adjust=False,progress=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.dropna(); df.index=df.index.tz_convert("America/New_York")
    out=[]
    for d in sorted(set(df.index.date)):
        ds=str(d)
        if vmap is not None and vmap.get(ds,-1)<50: continue
        g=df[df.index.date==d]
        pm=g[(g.index.time>=dt.time(4,0))&(g.index.time<dt.time(9,30))]
        rt=g[(g.index.time>=dt.time(9,30))&(g.index.time<dt.time(16,0))]
        if len(pm)<3 or len(rt)<20: continue
        pmh,pml=float(pm["High"].max()),float(pm["Low"].min())
        if pmh<=pml: continue
        op=float(rt["Open"].iloc[0])
        if (op-pml)/(pmh-pml)<=0.5: continue
        H=[float(x) for x in rt["High"]];L=[float(x) for x in rt["Low"]]
        C=[float(x) for x in rt["Close"]];O=[float(x) for x in rt["Open"]]
        V=[float(x) for x in rt["Volume"]];T=[t.time() for t in rt.index]
        cv=cpv=cpv2=0.0; W=[];S=[]
        for i in range(len(C)):
            tp=(H[i]+L[i]+C[i])/3
            cv+=V[i];cpv+=tp*V[i];cpv2+=tp*tp*V[i]
            w=cpv/cv if cv else tp
            W.append(w);S.append(math.sqrt(max(cpv2/cv-w*w,0.0)) if cv else 0.0)
        ei=ep=None
        for i in range(1,len(C)):
            if T[i]>=dt.time(14,0): break
            if S[i]<=1e-9: continue
            if L[i]<=W[i]-S[i]: ei,ep=i,W[i]-S[i]; break
        if ei is None: continue
        out.append(dict(d=ds,ei=ei,ep=ep,H=H,L=L,C=C,O=O,W=W,S=S,T=T,
                        vix=vlv.get(ds,16.0)))
    return out

def exitsim(r, trail, use_stop, mode="trail", cut=dt.time(14,30)):
    """mode: trail=트레일링만 / tp1=기존 분할청산"""
    ei,ep=r["ei"],r["ep"]; H,L,C,O,W,S,T=r["H"],r["L"],r["C"],r["O"],r["W"],r["S"],r["T"]
    stop=min(L[:ei+1])*(1-0.0005) if use_stop else None
    if mode=="tp1":
        half=False; tp1=None
        for j in range(ei+1,len(C)):
            hold=(j-ei)*15/60
            if not half and H[j]>=W[j]: tp1,half=W[j],True
            run=W[j]+S[j]
            if stop and L[j]<=stop:
                px=stop
                return (0.5*((tp1/ep-1)*100)+0.5*((px/ep-1)*100)) if half else ((px/ep-1)*100), hold, "STOP"
            if half and H[j]>=run:
                return 0.5*((tp1/ep-1)*100)+0.5*((run/ep-1)*100), hold, "RUN"
            if T[j]>=cut:
                px=O[j]
                return (0.5*((tp1/ep-1)*100)+0.5*((px/ep-1)*100)) if half else ((px/ep-1)*100), hold, "CUT"
        px=C[-1]; hold=(len(C)-1-ei)*15/60
        return ((0.5*((tp1/ep-1)*100)+0.5*((px/ep-1)*100)) if half else ((px/ep-1)*100)), hold, "EOD"
    # 트레일링: 최고가 대비 trail% 하락 시 청산
    peak=ep
    for j in range(ei+1,len(C)):
        hold=(j-ei)*15/60
        if stop and L[j]<=stop:
            return (stop/ep-1)*100, hold, "STOP"
        peak=max(peak,H[j])
        tp=peak*(1-trail/100)
        if L[j]<=tp and peak>ep:
            return (tp/ep-1)*100, hold, "TRAIL"
        if T[j]>=cut:
            return (O[j]/ep-1)*100, hold, "CUT"
    return (C[-1]/ep-1)*100, (len(C)-1-ei)*15/60, "EOD"

def to_opt(t):
    intr=t["spot"]*ITM_PCT/100
    prem=intr/(1-TV_RATIO)*((t["vix"]/16.0)**0.5)
    gain=t["spot"]*t["ux"]/100*DELTA
    theta=THETA_PER_HR*max(t["hold"],0.5)*(prem/4.75)
    net=max(gain-theta-prem*SPREAD/100,-prem)
    return prem,net*100

def sim(ts,frac,cap0=2000.0):
    cap=cap0;peak=cap;mdd=0.0;pcts=[];ncs=[];st=0;mx=0
    for t in ts:
        prem,usd=to_opt(t); cost=prem*100
        nc=1 if frac is None else int((cap*frac)//cost)
        if nc<1: continue
        before=cap; cap+=usd*nc
        pcts.append(usd*nc/before*100); ncs.append(nc)
        if usd<0: st+=1; mx=max(mx,st)
        else: st=0
        peak=max(peak,cap);mdd=max(mdd,(peak-cap)/peak*100)
    return dict(cap=cap,mdd=mdd,worst=min(pcts) if pcts else 0,mx=mx,
                n=len(ncs),max_nc=max(ncs) if ncs else 0)

def build(raw, trail, use_stop, mode, cut=dt.time(14,30)):
    out=[]
    for r in raw:
        ux,hold,res=exitsim(r,trail,use_stop,mode,cut)
        out.append(dict(d=r["d"],ux=ux,spot=r["ep"],hold=hold,vix=r["vix"],res=res))
    return out

def main():
    raw=trades()
    if not raw: return ["3층 조건 충족일 없음 (15분봉 60일)"]
    out=[f"3층(v9) · 15분봉 60일 · 조건충족 {len(raw)}건 · 청산 방식 비교",""]
    out.append(f"  {'방식':22s} {'승률':>6s} {'PF':>6s} {'계약당':>9s} {'기초평균':>9s} {'보유h':>6s}  구성")
    cfgs=[("기존 TP1+러너 (손절O)",None,True,"tp1"),
          ("기존 TP1+러너 (손절X)",None,False,"tp1")]
    for tr in (0.15,0.25,0.35,0.50,0.75,1.00):
        cfgs.append((f"트레일 {tr}% (손절O)",tr,True,"trail"))
    for tr in (0.15,0.25,0.35,0.50,0.75,1.00):
        cfgs.append((f"트레일 {tr}% (손절X)",tr,False,"trail"))
    best=None
    for lab,tr,us,md in cfgs:
        ts=build(raw,tr,us,md)
        opts=[to_opt(t) for t in ts]
        w=sum(1 for _,u in opts if u>0)
        g=sum(u for _,u in opts if u>0); l=-sum(u for _,u in opts if u<=0)
        pf=g/l if l else 99
        rc={}
        for t in ts: rc[t["res"]]=rc.get(t["res"],0)+1
        out.append(f"  {lab:22s} {w/len(ts)*100:5.1f}% {pf:6.2f} "
                   f"${np.mean([u for _,u in opts]):+8.2f} "
                   f"{np.mean([t['ux'] for t in ts]):+8.3f}% {np.mean([t['hold'] for t in ts]):5.1f}  "
                   f"{'/'.join(f'{k}{v}' for k,v in sorted(rc.items()))}")
        if best is None or pf>best[1]: best=(lab,pf,ts)
    out.append("")
    out.append("[트레일 폭 × 시간컷 교차 · 손절 유지]")
    for tr in (0.5, 0.75, 1.0, 1.5, 2.0, None):
        lab = f"트레일 {tr}%" if tr else "트레일 없음(홀드)"
        out.append(f"  ── {lab} ──")
        out.append(f"    {'시간컷':8s} {'승률':>6s} {'PF':>6s} {'계약당':>9s} {'기초':>8s} {'보유h':>5s}  구성")
        for ch,cl in ((dt.time(11,0),"11:00"),(dt.time(12,0),"12:00"),(dt.time(13,0),"13:00"),
                      (dt.time(14,0),"14:00"),(dt.time(14,30),"14:30"),(dt.time(15,55),"종가")):
            ts2=build(raw, tr if tr else 99.0, True, "trail", ch)
            o2=[to_opt(t) for t in ts2]
            w2=sum(1 for _,u in o2 if u>0)
            g2=sum(u for _,u in o2 if u>0); l2=-sum(u for _,u in o2 if u<=0)
            rc2={}
            for t in ts2: rc2[t["res"]]=rc2.get(t["res"],0)+1
            out.append(f"    {cl:8s} {w2/len(ts2)*100:5.1f}% {g2/l2 if l2 else 99:6.2f} "
                       f"${np.mean([u for _,u in o2]):+8.2f} {np.mean([t['ux'] for t in ts2]):+7.3f}% "
                       f"{np.mean([t['hold'] for t in ts2]):4.1f}  "
                       f"{'/'.join(f'{k}{v}' for k,v in sorted(rc2.items()))}")
    out.append("")
    out.append(f"[최적 방식: {best[0]}] 사이징 비교")
    ts=best[2]
    opts=[to_opt(t) for t in ts]
    w=sum(1 for _,u in opts if u>0)
    out.append(f"  {'사이징':12s} {'최종자본':>11s} {'수익률':>8s} {'MDD':>7s} {'최악1회':>7s} {'최장연패':>7s} {'최대계약':>7s}")
    out.append(f"  {'고정 1계약':12s} " + (lambda r: f"${r['cap']:10,.0f} {(r['cap']/2000-1)*100:+7.0f}% "
               f"{r['mdd']:6.1f}% {r['worst']:6.1f}% {r['mx']:7d} {r['max_nc']:7d}")(sim(ts,None)))
    for f in (0.30,0.40,0.50,0.60,0.70):
        r=sim(ts,f)
        out.append(f"  자본 {f*100:3.0f}%     ${r['cap']:10,.0f} {(r['cap']/2000-1)*100:+7.0f}% "
                   f"{r['mdd']:6.1f}% {r['worst']:6.1f}% {r['mx']:7d} {r['max_nc']:7d}")
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("l3size_result.json","w"),ensure_ascii=False,indent=1)
