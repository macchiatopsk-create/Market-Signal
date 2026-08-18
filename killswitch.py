"""매크로 유사일 신호의 '언제 믿을 수 있나' 탐색.

전체 2년 각 거래일에 대해 유사일 TOP10을 찾고 다음날 방향을 예측한 뒤,
조건별(VIX 레벨/변화, 합의도, 유사도)로 적중률이 갈리는지 측정.
목적: 킬스위치(이 조건이면 신호 무시) 또는 셋업(이 조건에서만 진입) 도출.
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

FEATS={"^TNX":"10Y","^TYX":"30Y","^FVX":"5Y","CL=F":"WTI","DX-Y.NYB":"DXY",
       "^VIX":"VIX","GC=F":"GOLD","HG=F":"COPPER","HYG":"HY","TLT":"TLT",
       "EURUSD=X":"EUR","JPY=X":"JPY","^N225":"NIKKEI","^GDAXI":"DAX"}
TOPN=10; MINHIST=150

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

def main():
    out=[]
    cols={}
    for tk,nm in FEATS.items():
        try:
            d=norm(yf.Ticker(tk).history(period="2y")[["Close"]].dropna())
            cols[nm]=(d["Close"]/d["Close"].shift(1)-1)*100
        except Exception: pass
    F=pd.DataFrame(cols); cov=F.notna().mean()
    F=F[[c for c in F.columns if cov[c]>=0.90]]
    Z=((F-F.mean())/F.std()).fillna(0.0)

    vix=norm(yf.Ticker("^VIX").history(period="2y")[["Close"]].dropna())["Close"]
    vlvl=vix.rename("vlvl"); vchg=((vix/vix.shift(1)-1)*100).rename("vchg")

    q=norm(yf.Ticker("QQQ").history(period="2y")[["Open","Close"]].dropna())
    T=pd.DataFrame({"ret":(q["Close"]/q["Open"]-1)*100})
    df=Z.join(T,how="inner").join(vlvl,how="left").join(vchg,how="left").dropna(subset=["ret"])
    fc=[c for c in Z.columns if c in df.columns]
    df["n_ret"]=df["ret"].shift(-1)
    df=df.dropna(subset=["n_ret","vlvl","vchg"])

    idx=list(df.index); recs=[]
    for i in range(MINHIST,len(idx)):
        d0=idx[i]
        v0=df.loc[d0,fc].values.astype(float)
        hist=df.iloc[:i]
        M=hist[fc].values.astype(float)
        dist=np.sqrt(((M-v0)**2).sum(axis=1))
        ordr=np.argsort(dist)[:TOPN]
        nx=hist["n_ret"].values[ordr]
        pred=nx.mean(); upr=(nx>0).mean()*100
        agree=max(upr,100-upr)                       # 합의도
        recs.append(dict(d=str(d0.date()),pred=pred,upr=upr,agree=agree,
                         d1=float(dist[ordr[0]]),dmean=float(dist[ordr].mean()),
                         act=float(df.loc[d0,"n_ret"]),
                         vlvl=float(df.loc[d0,"vlvl"]),vchg=float(df.loc[d0,"vchg"])))
    R=pd.DataFrame(recs)
    R["ok"]=((R["pred"]>0)==(R["act"]>0))
    # 방향 예측 + 그 방향으로 베팅했을 때 수익
    R["pnl"]=np.where(R["pred"]>0,R["act"],-R["act"])

    def rep(sel,lab):
        n=len(sel)
        if n<15: out.append(f"  {lab:30s} n={n:4d} 표본부족"); return
        k=int(sel["ok"].sum()); ci=wilson(k,n)
        g=sel.loc[sel["pnl"]>0,"pnl"].sum(); l=-sel.loc[sel["pnl"]<=0,"pnl"].sum()
        out.append(f"  {lab:30s} n={n:4d} 적중 {k/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
                   f"평균 {sel['pnl'].mean():+.3f}% PF {g/l if l else 99:5.2f}")

    out.append(f"매크로 유사일 TOP{TOPN} 다음날 방향 예측 · 검증 {len(R)}일 · 피처 {len(fc)}개")
    out.append("")
    out.append("[베이스라인]"); rep(R,"전체")
    out.append("")
    out.append("[VIX 절대레벨]")
    for lo,hi in ((0,15),(15,18),(18,22),(22,99)):
        rep(R[(R.vlvl>=lo)&(R.vlvl<hi)],f"VIX {lo}~{hi}")
    out.append("")
    out.append("[VIX 당일 변화율]")
    for lo,hi in ((-99,-5),(-5,-2),(-2,2),(2,5),(5,10),(10,99)):
        rep(R[(R.vchg>=lo)&(R.vchg<hi)],f"VIX변화 {lo}~{hi}%")
    out.append("")
    out.append("[유사일 합의도 — 10개 중 몇 개가 같은 방향]")
    for lo,hi in ((50,60),(60,70),(70,80),(80,101)):
        rep(R[(R.agree>=lo)&(R.agree<hi)],f"합의 {lo}~{hi}%")
    out.append("")
    out.append("[유사도 — 최근접 거리]")
    qs=R["d1"].quantile([.25,.5,.75]).values
    rep(R[R.d1<=qs[0]],f"최근접 거리 하위25% (<{qs[0]:.2f})")
    rep(R[(R.d1>qs[0])&(R.d1<=qs[1])],"25~50%")
    rep(R[(R.d1>qs[1])&(R.d1<=qs[2])],"50~75%")
    rep(R[R.d1>qs[2]],f"상위25% (>{qs[2]:.2f})")
    out.append("")
    out.append("[복합 — 합의 70%+ 조건에서 VIX별]")
    H=R[R.agree>=70]
    for lo,hi in ((0,18),(18,99)):
        rep(H[(H.vlvl>=lo)&(H.vlvl<hi)],f"합의70+ & VIX {lo}~{hi}")
    for lo,hi in ((-99,2),(2,99)):
        rep(H[(H.vchg>=lo)&(H.vchg<hi)],f"합의70+ & VIX변화 {lo}~{hi}%")
    out.append("")
    out.append("[예측 방향별]")
    rep(R[R.pred>0],"상승 예측"); rep(R[R.pred<=0],"하락 예측")
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("killswitch_result.json","w"),ensure_ascii=False,indent=1)
