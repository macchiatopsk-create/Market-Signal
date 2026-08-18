"""8월 각 거래일 × 매크로 유사일 검색 → 다음날 결과 검증.

각 날 16:00 종가 기준 매크로 벡터(z-score)로 과거 2년에서 유사일을 찾고,
그 유사일들의 '다음날' QQQ 결과를 집계 → 실제 다음날과 대조.
유사도는 전체 거리 분포의 백분위로 환산 (100% = 가장 가까움).
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

FEATS={"^TNX":"10Y","^TYX":"30Y","^FVX":"5Y","CL=F":"WTI","DX-Y.NYB":"DXY",
       "^VIX":"VIX","GC=F":"GOLD","HG=F":"COPPER","HYG":"HY","TLT":"TLT",
       "EURUSD=X":"EUR","JPY=X":"JPY","^N225":"NIKKEI","^GDAXI":"DAX"}
TOPN=10

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
            cols[nm]=(d["Close"]/d["Close"].shift(1)-1)*100      # 종가 기준 일변화
        except Exception: pass
    F=pd.DataFrame(cols)
    cov=F.notna().mean(); keep=[c for c in F.columns if cov[c]>=0.90]
    F=F[keep]; Z=((F-F.mean())/F.std()).fillna(0.0)

    q=norm(yf.Ticker("QQQ").history(period="2y")[["Open","High","Low","Close"]].dropna())
    T=pd.DataFrame({
        "ret":(q["Close"]/q["Open"]-1)*100,
        "gap":(q["Open"]/q["Close"].shift(1)-1)*100,
        "d2d":(q["Close"]/q["Close"].shift(1)-1)*100,
        "lo":(q["Low"]/q["Open"]-1)*100,"hi":(q["High"]/q["Open"]-1)*100})
    df=Z.join(T,how="inner").dropna(subset=["ret","gap","d2d"])
    fc=[c for c in Z.columns if c in df.columns]
    # 다음날 결과 컬럼
    df["n_ret"]=df["ret"].shift(-1); df["n_gap"]=df["gap"].shift(-1)
    df["n_d2d"]=df["d2d"].shift(-1)

    aug=[d for d in df.index if str(d)[:7]=="2026-08"]
    out.append(f"피처 {len(fc)}개: {', '.join(fc)} · 과거 {len(df)}일 · 8월 {len(aug)}거래일")
    out.append("유사도% = 전체 거리 분포에서의 근접 백분위 (100%=최근접)")
    out.append("")
    hit=0; tot=0; allsim={}
    rows=[]
    for d0 in aug:
        v0=df.loc[d0,fc].values.astype(float)
        hist=df[(df.index<d0)].copy()
        hist=hist.dropna(subset=["n_ret"])
        if len(hist)<100: continue
        M=hist[fc].values.astype(float)
        dist=np.sqrt(((M-v0)**2).sum(axis=1))
        hist["dist"]=dist
        hist=hist.sort_values("dist")
        top=hist.head(TOPN)
        # 유사도% (거리 백분위)
        sim=[100*(1-(hist["dist"].values<x).mean()) for x in top["dist"].values]
        pred=top["n_ret"].mean()
        pup=(top["n_ret"]>0).mean()*100
        act=df.loc[d0,"n_ret"]
        ok = (not np.isnan(act)) and ((pred>0)==(act>0))
        if not np.isnan(act): tot+=1; hit+=1 if ok else 0
        for i2 in top.index: allsim[str(i2.date())]=allsim.get(str(i2.date()),0)+1
        rows.append(dict(d=str(d0.date()),sim=sim[0],pred=pred,pup=pup,act=act,
                         ok=ok,top=[(str(i2.date()),round(s,1),round(r["n_ret"],2))
                                    for (i2,r),s in zip(top.iterrows(),sim)]))
    out.append(f"{'날짜':11s} {'최근접':>6s} {'유사10일 다음날평균':>18s} {'상승비율':>8s} {'실제 다음날':>11s} 적중")
    for r in rows:
        a=f"{r['act']:+.2f}%" if not np.isnan(r["act"]) else "  진행중"
        out.append(f"{r['d']:11s} {r['sim']:5.1f}% {r['pred']:+17.3f}% {r['pup']:7.0f}% "
                   f"{a:>11s} {'O' if r['ok'] else ('-' if np.isnan(r['act']) else 'X')}")
    if tot:
        out.append(f"\n방향 적중 {hit}/{tot} = {hit/tot*100:.1f}%  (동전 50%)")
    out.append("\n[여러 날이 공통으로 지목한 과거일 — 3회 이상]")
    rep=sorted([(k,v) for k,v in allsim.items() if v>=3],key=lambda x:-x[1])
    for k,v in rep[:15]:
        nr=df.loc[pd.Timestamp(k),"n_ret"] if pd.Timestamp(k) in df.index else float("nan")
        out.append(f"  {k}  {v}회 지목 · 그 다음날 {nr:+.2f}%")
    out.append("\n[상세: 각 날짜의 유사일 상위 3개]")
    for r in rows:
        s=" · ".join(f"{d_}({sm:.0f}%→{nx:+.2f}%)" for d_,sm,nx in r["top"][:3])
        out.append(f"  {r['d']}  {s}")
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("augmatch_result.json","w"),ensure_ascii=False,indent=1)
