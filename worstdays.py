"""갭 전략 최악의 날 분석 — 프리미엄이 크게 날아간 날의 공통점.

조건: 갭 0.2~1.5% & 첫봉 커버 40%+ · 무손절 · 갭필 후 트레일 0.15%
옵션 환산 후 손실 큰 날들의 특징을 비교: 갭 크기/방향, 커버율, VIX, 요일,
갭필 여부, 최대 역행폭, 그날 QQQ 등락, 전일 등락
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

TRAIL=0.15; DELTA=0.69; SPREAD=2.2; ITM_PCT=0.50; TV_RATIO=0.28; THETA_PER_HR=0.214
def norm(d):
    try: d.index=d.index.tz_localize(None)
    except: pass
    d.index=pd.to_datetime(d.index).normalize()
    return d[~d.index.duplicated(keep="last")]

def main():
    v=norm(yf.Ticker("^VIX").history(period="2y")[["Open","Close"]].dropna())
    vch=(v["Open"]/v["Close"].shift(1)-1)*100
    vm={str(pd.Timestamp(k).date()):float(x) for k,x in vch.dropna().items()}
    vlv={str(pd.Timestamp(k).date()):float(x) for k,x in v["Open"].items()}
    vcl={str(pd.Timestamp(k).date()):float(x) for k,x in ((v["Close"]/v["Close"].shift(1)-1)*100).dropna().items()}

    df=yf.download("QQQ",period="2y",interval="1h",prepost=False,auto_adjust=False,progress=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.dropna(); df.index=df.index.tz_convert("America/New_York")
    df=df[(df.index.time>=dt.time(9,30))&(df.index.time<dt.time(16,0))]
    days=sorted(set(df.index.date)); rows=[]; pc=None; prev_d2d=None
    for d in days:
        g=df[df.index.date==d]
        if len(g)<5:
            if len(g): pc=float(g["Close"].iloc[-1])
            continue
        ds=str(d)
        O=[float(x) for x in g["Open"]];H=[float(x) for x in g["High"]]
        L=[float(x) for x in g["Low"]];C=[float(x) for x in g["Close"]]
        d2d=(C[-1]/pc-1)*100 if pc else 0.0
        if pc:
            vx=vm.get(ds)
            if vx is None or abs(vx)<5.0:
                gap=O[0]-pc; gp=gap/pc*100
                if 0.2<=abs(gp)<1.5:
                    sgn=1 if gap>0 else -1
                    cover=((O[0]-C[0])/gap) if sgn>0 else ((C[0]-O[0])/abs(gap))
                    if cover>=0.40:
                        ep=C[0]; tgt=pc; fl=False; ext=ep; res=None; ux=None; mae=0.0; hold=0
                        for k in range(1,len(C)):
                            hold=k
                            adv=(H[k]-ep)/ep*100 if sgn>0 else (ep-L[k])/ep*100
                            mae=max(mae,adv)
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
                        # 옵션 환산
                        vix=vlv.get(ds,16.0)
                        intr=ep*ITM_PCT/100; prem=intr/(1-TV_RATIO)*((vix/16.0)**0.5)
                        gain=ep*ux/100*DELTA
                        theta=THETA_PER_HR*max(hold,0.5)*(prem/4.75)
                        net=max(gain-theta-prem*SPREAD/100, -prem)
                        rows.append(dict(d=ds,wd="월화수목금"[dt.date.fromisoformat(ds).weekday()],
                                         gp=gp,sgn=sgn,cover=cover,ux=ux,mae=mae,hold=hold,
                                         filled=fl,res=res,vix=vix,vixchg=vx,
                                         vixcl=vcl.get(ds,0),prev=prev_d2d,d2d=d2d,
                                         prem=prem,net=net,pct=net/prem*100))
        pc=C[-1]; prev_d2d=d2d

    R=pd.DataFrame(rows)
    out=[f"거래 {len(R)}건 · 옵션 환산 손익 기준"]
    out.append(f"전체 평균 {R.pct.mean():+.1f}% · 승률 {(R.net>0).mean()*100:.1f}%")
    out.append("")
    W=R.nsmallest(15,"pct")
    out.append("[최악 15일]")
    out.append(f"  {'날짜':11s}{'요일':3s} {'손익%':>7s} {'갭%':>7s} {'커버':>5s} {'기초%':>7s} "
               f"{'MAE':>6s} {'갭필':>4s} {'보유h':>5s} {'VIX':>5s} {'VIX개장':>7s} {'VIX종가':>7s} {'당일QQQ':>7s}")
    for _,r in W.iterrows():
        out.append(f"  {r.d:11s}{r.wd:3s} {r.pct:+6.1f}% {r.gp:+6.2f}% {r.cover:5.2f} {r.ux:+6.2f}% "
                   f"{r.mae:5.2f}% {'O' if r.filled else 'X':>4s} {r.hold:5d} {r.vix:5.1f} "
                   f"{r.vixchg:+6.1f}% {r.vixcl:+6.1f}% {r.d2d:+6.2f}%")
    B=R.nlargest(15,"pct")
    out.append("")
    out.append("[최고 15일 — 대조군]")
    for _,r in B.iterrows():
        out.append(f"  {r.d:11s}{r.wd:3s} {r.pct:+6.1f}% {r.gp:+6.2f}% {r.cover:5.2f} {r.ux:+6.2f}% "
                   f"{r.mae:5.2f}% {'O' if r.filled else 'X':>4s} {r.hold:5d} {r.vix:5.1f} "
                   f"{r.vixchg:+6.1f}% {r.vixcl:+6.1f}% {r.d2d:+6.2f}%")
    out.append("")
    L=R[R.pct<=-50]; G=R[R.pct>0]
    out.append(f"[대손실(-50% 이하) {len(L)}건 vs 이익 {len(G)}건 — 평균 비교]")
    for c,lab in (("gp","갭 크기%"),("cover","커버율"),("vix","VIX 레벨"),
                  ("vixchg","VIX 개장변화%"),("vixcl","VIX 당일변화%"),
                  ("mae","최대역행%"),("hold","보유시간"),("d2d","당일 QQQ%"),("prev","전일 QQQ%")):
        lv=L[c].mean() if len(L) else float("nan"); gv=G[c].mean()
        out.append(f"  {lab:14s} 대손실 {lv:+7.2f}  이익 {gv:+7.2f}  차이 {lv-gv:+7.2f}")
    out.append(f"  {'갭필 비율':14s} 대손실 {L.filled.mean()*100 if len(L) else 0:6.1f}%  이익 {G.filled.mean()*100:6.1f}%")
    out.append(f"  {'갭업 비율':14s} 대손실 {(L.sgn>0).mean()*100 if len(L) else 0:6.1f}%  이익 {(G.sgn>0).mean()*100:6.1f}%")
    out.append("")
    out.append("[요일별]")
    for wd in "월화수목금":
        s=R[R.wd==wd]
        if len(s)>=5:
            out.append(f"  {wd}  n={len(s):3d} 평균 {s.pct.mean():+6.1f}% 승률 {(s.net>0).mean()*100:5.1f}% "
                       f"대손실 {(s.pct<=-50).mean()*100:5.1f}%")
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("worstdays_result.json","w"),ensure_ascii=False,indent=1)
