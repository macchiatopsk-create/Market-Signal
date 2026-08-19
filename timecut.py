"""시간 손절 비교 — 갭필 안 되면 몇 시에 끊을 것인가.

조건: 갭 0.2~1.5% & 첫봉 커버 40%+ · 가격손절 없음 · 갭필 후 트레일 0.15%
시간컷: 11:30 / 12:30 / 13:30 / 14:00 / 15:00 / 없음(종가)
※ 갭필 성공 후에는 트레일링이 관리하므로 시간컷은 '갭필 전'에만 적용하는 버전과
   무조건 적용하는 버전 둘 다 측정
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

TRAIL=0.15; DELTA=0.69; SPREAD=2.2; ITM_PCT=0.50; TV_RATIO=0.28; THETA_PER_HR=0.214
def wilson(k,n):
    if n==0: return (0.0,0.0)
    p,z=k/n,1.96; d=1+z*z/n
    c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (round(max(0,c-h)*100,1),round(min(1,c+h)*100,1))
def norm(d):
    try: d.index=d.index.tz_localize(None)
    except: pass
    d.index=pd.to_datetime(d.index).normalize()
    return d[~d.index.duplicated(keep="last")]

def opt(ep,ux,hold,vix):
    intr=ep*ITM_PCT/100; prem=intr/(1-TV_RATIO)*((vix/16.0)**0.5)
    gain=ep*ux/100*DELTA
    theta=THETA_PER_HR*max(hold,0.5)*(prem/4.75)
    net=max(gain-theta-prem*SPREAD/100,-prem)
    return prem, net/prem*100, net*100

def run(cut_h, only_unfilled):
    v=norm(yf.Ticker("^VIX").history(period="2y")[["Open","Close"]].dropna())
    vch=(v["Open"]/v["Close"].shift(1)-1)*100
    vm={str(pd.Timestamp(k).date()):float(x) for k,x in vch.dropna().items()}
    vlv={str(pd.Timestamp(k).date()):float(x) for k,x in v["Open"].items()}
    df=yf.download("QQQ",period="2y",interval="1h",prepost=False,auto_adjust=False,progress=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.dropna(); df.index=df.index.tz_convert("America/New_York")
    df=df[(df.index.time>=dt.time(9,30))&(df.index.time<dt.time(16,0))]
    days=sorted(set(df.index.date)); out=[]; pc=None
    for d in days:
        g=df[df.index.date==d]
        if len(g)<5:
            if len(g): pc=float(g["Close"].iloc[-1])
            continue
        ds=str(d)
        O=[float(x) for x in g["Open"]];H=[float(x) for x in g["High"]]
        L=[float(x) for x in g["Low"]];C=[float(x) for x in g["Close"]]
        T=[t.time() for t in g.index]
        if pc:
            vx=vm.get(ds)
            if vx is None or abs(vx)<5.0:
                gap=O[0]-pc; gp=gap/pc*100
                if 0.2<=abs(gp)<1.5:
                    sgn=1 if gap>0 else -1
                    cover=((O[0]-C[0])/gap) if sgn>0 else ((C[0]-O[0])/abs(gap))
                    if cover>=0.40:
                        ep=C[0]; tgt=pc; fl=False; ext=ep; res=None; ux=None; hold=0
                        for k in range(1,len(C)):
                            hold=k
                            if cut_h and T[k]>=cut_h and (not only_unfilled or not fl):
                                px=C[k]; res="TIMECUT"
                                ux=((ep-px)/ep*100) if sgn>0 else ((px-ep)/ep*100); break
                            if not fl:
                                if (L[k]<=tgt) if sgn>0 else (H[k]>=tgt):
                                    fl=True; ext=min(L[k],tgt) if sgn>0 else max(H[k],tgt)
                                continue
                            ext=min(ext,L[k]) if sgn>0 else max(ext,H[k])
                            tp=ext*(1+TRAIL/100) if sgn>0 else ext*(1-TRAIL/100)
                            if (H[k]>=tp) if sgn>0 else (L[k]<=tp):
                                res="TRAIL"; ux=((ep-tp)/ep*100) if sgn>0 else ((tp-ep)/ep*100); break
                        if res is None:
                            px=C[-1]; res="EOD"; ux=((ep-px)/ep*100) if sgn>0 else ((px-ep)/ep*100)
                        prem,pct,usd=opt(ep,ux,hold,vlv.get(ds,16.0))
                        out.append(dict(d=ds,ux=ux,pct=pct,usd=usd,res=res,filled=fl,prem=prem))
        pc=C[-1]
    return out

def sim(ts,frac=0.30,cap0=2000.0):
    cap=cap0;peak=cap;mdd=0.0
    for t in ts:
        cost=t["prem"]*100
        nc=max(int((cap*frac)//cost),0)
        if nc<1: continue
        cap+=t["usd"]*nc
        peak=max(peak,cap);mdd=max(mdd,(peak-cap)/peak*100)
        if cap<=0: break
    return cap,mdd

def main():
    out=["갭 0.2~1.5% & 커버40%+ · 가격손절 없음 · 갭필후 트레일0.15% · 옵션 델타0.69",
         "사이징 자본 30% · $2,000 시작",""]
    for only in (True,False):
        lab="갭필 전에만 시간컷" if only else "갭필 여부 무관 시간컷"
        out.append(f"━━ {lab} ━━")
        out.append(f"  {'시간컷':10s} {'n':>3s} {'승률':>16s} {'PF':>6s} {'평균%':>7s} "
                   f"{'대손실':>6s} {'최종자본':>11s} {'MDD':>6s}  청산구성")
        for ch,cl in ((dt.time(11,30),"11:30"),(dt.time(12,30),"12:30"),(dt.time(13,30),"13:30"),
                      (dt.time(14,0),"14:00"),(dt.time(15,0),"15:00"),(None,"없음")):
            ts=run(ch,only)
            n=len(ts); w=sum(1 for t in ts if t["usd"]>0)
            g=sum(t["usd"] for t in ts if t["usd"]>0); l=-sum(t["usd"] for t in ts if t["usd"]<=0)
            big=sum(1 for t in ts if t["pct"]<=-50)
            cap,mdd=sim(ts); ci=wilson(w,n)
            rc={}
            for t in ts: rc[t["res"]]=rc.get(t["res"],0)+1
            out.append(f"  {cl:10s} {n:3d} {w/n*100:5.1f}%({ci[0]:4.1f}~{ci[1]:4.1f}) "
                       f"{g/l if l else 99:6.2f} {np.mean([t['pct'] for t in ts]):+6.1f}% "
                       f"{big:5d}건 ${cap:10,.0f} {mdd:5.1f}%  "
                       f"{'/'.join(f'{k}{v}' for k,v in sorted(rc.items()))}")
        out.append("")
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("timecut_result.json","w"),ensure_ascii=False,indent=1)
