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

TRAIL=0.15
# ── 실측 기준 (2026-08-18 QQQ 721P 0DTE, 로빈후드) ──
#   Mark $4.75 · Bid/Ask 4.68/4.82 (스프레드 2.9%) · Delta 0.685 · Theta -1.34/일 · IV 20.6%
#   QQQ 718 기준 3pt ITM = 0.42% ITM 에서 델타 0.685
#   콜 714C $5.03 델타0.698 세타-1.44 / 풋 721P $4.75 델타0.685 세타-1.34
DELTA=0.69
SPREAD=2.2          # 왕복 스프레드 % (콜 2.0 / 풋 2.9 평균)
ITM_PCT=0.50        # 스팟 대비 ITM 폭 %
TV_RATIO=0.28       # 시간가치 비중
THETA_PER_HR=0.214  # 시간당 세타 ($) — 1.39/6.5
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
    """실측 기준 옵션 환산. 프리미엄 = 내재가치 + 시간가치, 세타는 실측 시간당."""
    spot=t["spot"]; vix=t["vix"]
    intr=spot*ITM_PCT/100                       # 내재가치
    prem=intr/(1-TV_RATIO)                      # 시간가치 비율 역산
    prem*= (vix/16.0)**0.5                      # VIX 수준으로 보정 (기준 16)
    move=spot*t["ux"]/100
    gain=move*DELTA
    theta=THETA_PER_HR*max(t["hold"],0.5)*(prem/4.75)   # 프리미엄 규모 비례
    cost=prem*SPREAD/100
    net=gain-theta-cost
    # 옵션 매수 최대손실 = 프리미엄 전액 (감마로 델타가 줄어 그 이하로 못 감)
    net=max(net,-prem)
    return prem, net/prem*100, net*100

def sim(ts, mode, cap0=2000.0):
    cap=cap0; peak=cap; mdd=0.0; curve=[]; nc_hist=[]; pnls=[]; pnl_pcts=[]
    for t in ts:
        prem,pct,usd=to_opt(t)
        cost=prem*100
        if mode=="fixed": nc=1
        else:
            frac=float(mode)/100.0
            nc=int((cap*frac)//cost)
        if nc<1:
            nc_hist.append(0); curve.append(cap); continue
        before=cap
        cap+=usd*nc
        pnls.append(usd*nc); pnl_pcts.append(usd*nc/before*100)
        nc_hist.append(nc)
        peak=max(peak,cap); mdd=max(mdd,(peak-cap)/peak*100)
        curve.append(cap)
        if cap<=0: break
    q=[x for x in nc_hist if x>0]
    # 연속 손실 최장 / 최악 단일 손실(자본대비 %)
    streak=mx=0; worst=0.0
    c=cap0
    for x in pnls:
        if x<0:
            streak+=1; mx=max(mx,streak)
        else: streak=0
    worst=min(pnl_pcts) if pnl_pcts else 0.0
    return dict(cap=cap,mdd=mdd,n=len(q),
                avg_nc=np.mean(q) if q else 0,
                max_nc=max(nc_hist) if nc_hist else 0,
                med_nc=np.median(q) if q else 0,
                last_nc=(q[-1] if q else 0),
                maxstreak=mx, worst=worst,
                curve=curve)

def main():
    out=["실측 기준: 프리미엄≈스팟의 0.42% ITM · 델타 0.685 · 스프레드 2.9% · 세타 $1.34/일",
         "자본 $2,000 시작",""]
    for sp,slab in ((None,"무손절"),):
        ts=trades(sp)
        opts=[to_opt(t) for t in ts]
        w=sum(1 for _,p,_ in opts if p>0)
        g=sum(u for _,_,u in opts if u>0); l=-sum(u for _,_,u in opts if u<=0)
        out.append(f"━━ {slab} · 거래 {len(ts)}건 (2년, 연 {len(ts)/2:.0f}회) ━━")
        out.append(f"  옵션 환산: 승률 {w/len(ts)*100:.1f}% · 계약당 평균 ${np.mean([u for _,_,u in opts]):+.2f} "
                   f"· PF {g/l if l else 99:.2f} · 평균 프리미엄 ${np.mean([p for p,_,_ in opts]):.2f}")
        out.append(f"  {'사이징':14s} {'최종자본':>12s} {'수익률':>9s} {'MDD':>7s} "
                   f"{'최악1회':>7s} {'최장연패':>7s} {'중앙계약':>8s} {'최대계약':>8s}")
        modes=[("fixed","고정 1계약")]+[(str(p),f"자본 {p}%") for p in
               (20,25,30,35,40,45,50,60,75,90)]
        for m,ml in modes:
            r=sim(ts,m)
            out.append(f"  {ml:14s} ${r['cap']:11,.0f} {(r['cap']/2000-1)*100:+8.0f}% "
                       f"{r['mdd']:6.1f}% {r['worst']:6.1f}% {r['maxstreak']:7d} "
                       f"{r['med_nc']:8.0f} {r['max_nc']:8d}")
        out.append("")
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("sizing_result.json","w"),ensure_ascii=False,indent=1)
