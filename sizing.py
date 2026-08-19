"""사이징 비교 — $1000 시작, 0DTE ITM 델타0.7 옵션.

전략: 갭 0.2~1.5% & 첫봉 커버 40%+ → 첫봉 종가 진입
      갭필 후 트레일 0.15% · 손절 [무손절 / 0.9%]
사이징: 고정1계약 / 자본의 25% / 50% / 90% / 하프켈리
옵션 환산: 기초 X% → 프리미엄 X% × (스팟/프리미엄) × 델타 − 세타 − 스프레드
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

TRAIL=0.15; DELTA=0.7; SPREAD=1.0   # 왕복 스프레드 %
def norm(d):
    try: d.index=d.index.tz_localize(None)
    except: pass
    d.index=pd.to_datetime(d.index).normalize()
    return d[~d.index.duplicated(keep="last")]

def trades(stop_pct):
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
        if pc:
            vx=vm.get(ds)
            if vx is None or abs(vx)<5.0:
                gap=O[0]-pc; gp=gap/pc*100
                if 0.2<=abs(gp)<1.5:
                    sgn=1 if gap>0 else -1
                    cover=((O[0]-C[0])/gap) if sgn>0 else ((C[0]-O[0])/abs(gap))
                    if cover>=0.40:
                        ep=C[0]; tgt=pc; fl=False; ext=ep; res=None; ux=None
                        stop_px=(ep*(1+stop_pct/100) if sgn>0 else ep*(1-stop_pct/100)) if stop_pct else None
                        hold=0
                        for k in range(1,len(C)):
                            hold=k
                            if stop_px is not None and ((H[k]>=stop_px) if sgn>0 else (L[k]<=stop_px)):
                                res,ux="STOP",-stop_pct; break
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
                        out.append(dict(d=ds,ux=ux,spot=ep,hold=hold,vix=vlv.get(ds,16.0),res=res))
        pc=C[-1]
    return out

def to_opt(t):
    """기초 변동 % -> 옵션 프리미엄 % (델타/세타/스프레드 반영)"""
    spot=t["spot"]; vix=t["vix"]
    T=6.5/(6.5*252)
    atm=spot*(vix/100)*math.sqrt(T)*0.8
    intr=spot*0.02*(DELTA-0.5)*2          # 델타0.7 -> 스팟 대비 0.8% ITM
    prem=intr+atm*(1-(DELTA-0.5)*1.6)
    move=spot*t["ux"]/100
    gain=move*DELTA
    tv=prem-intr
    hrs=t["hold"]
    tv_left=max(0.0,tv*math.sqrt(max(6.5-hrs,0)/6.5))
    theta=tv-tv_left
    cost=prem*SPREAD/100
    net=gain-theta-cost
    return prem, net/prem*100, net*100      # 프리미엄$, 손익%, 손익$/계약

def sim(ts, mode, cap0=1000.0):
    cap=cap0; peak=cap; mdd=0.0; curve=[]; nc_hist=[]
    for t in ts:
        prem,pct,usd=to_opt(t)
        cost=prem*100
        if mode=="fixed": nc=1
        else:
            frac={"def":0.25,"mid":0.50,"agg":0.90}.get(mode)
            if mode=="kelly":
                frac=0.18                    # 하프켈리 근사 (승률75%, 손익비1.4 기준)
            nc=int((cap*frac)//cost)
        if nc<1:
            nc_hist.append(0); curve.append(cap); continue
        cap+=usd*nc
        nc_hist.append(nc)
        peak=max(peak,cap); mdd=max(mdd,(peak-cap)/peak*100)
        curve.append(cap)
        if cap<=0: break
    return dict(cap=cap,mdd=mdd,n=len([x for x in nc_hist if x>0]),
                avg_nc=np.mean([x for x in nc_hist if x>0]) if any(nc_hist) else 0,
                max_nc=max(nc_hist) if nc_hist else 0,curve=curve)

def main():
    out=[]
    for sp,slab in ((None,"무손절"),(0.9,"손절 0.9%")):
        ts=trades(sp)
        opts=[to_opt(t) for t in ts]
        w=sum(1 for _,p,_ in opts if p>0)
        g=sum(u for _,_,u in opts if u>0); l=-sum(u for _,_,u in opts if u<=0)
        out.append(f"━━ {slab} · 거래 {len(ts)}건 (2년, 연 {len(ts)/2:.0f}회) ━━")
        out.append(f"  옵션 환산: 승률 {w/len(ts)*100:.1f}% · 계약당 평균 ${np.mean([u for _,_,u in opts]):+.2f} "
                   f"· PF {g/l if l else 99:.2f} · 평균 프리미엄 ${np.mean([p for p,_,_ in opts]):.2f}")
        out.append(f"  {'사이징':16s} {'최종자본':>10s} {'수익률':>8s} {'MDD':>7s} {'평균계약':>8s} {'최대계약':>8s}")
        for m,ml in (("fixed","고정 1계약"),("def","수비적 25%"),("kelly","하프켈리 18%"),
                     ("mid","중립 50%"),("agg","공격적 90%")):
            r=sim(ts,m)
            out.append(f"  {ml:16s} ${r['cap']:9,.0f} {(r['cap']/1000-1)*100:+7.0f}% "
                       f"{r['mdd']:6.1f}% {r['avg_nc']:8.1f} {r['max_nc']:8d}")
        out.append("")
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("sizing_result.json","w"),ensure_ascii=False,indent=1)
