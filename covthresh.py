"""커버율 5% 단위 스윕 — 승률 80% 넘는 임계 찾기.
손절 없음(갭필까지 홀드) · 갭필 후 트레일링 0.15% · 1시간봉 2년
구간별(5%)과 누적(x% 이상) 둘 다.
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

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
    v=norm(yf.Ticker("^VIX").history(period="2y")[["Open","Close"]].dropna())
    ch=(v["Open"]/v["Close"].shift(1)-1)*100
    vm={str(pd.Timestamp(k).date()):float(x) for k,x in ch.dropna().items()}

    df=yf.download("QQQ",period="2y",interval="1h",prepost=False,auto_adjust=False,progress=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.dropna(); df.index=df.index.tz_convert("America/New_York")
    df=df[(df.index.time>=dt.time(9,30))&(df.index.time<dt.time(16,0))]

    days=sorted(set(df.index.date)); rows=[]; pc=None
    TRAIL=0.15
    for i,d in enumerate(days):
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
                    ep=C[0]; tgt=pc
                    filled=False; ext=ep; res=None; pnl=None; mae=0.0
                    for k in range(1,len(C)):
                        adv=(H[k]-ep)/ep*100 if sgn>0 else (ep-L[k])/ep*100
                        mae=max(mae,adv)
                        if not filled:
                            if (L[k]<=tgt) if sgn>0 else (H[k]>=tgt):
                                filled=True
                                ext=min(L[k],tgt) if sgn>0 else max(H[k],tgt)
                            continue
                        ext=min(ext,L[k]) if sgn>0 else max(ext,H[k])
                        tp=ext*(1+TRAIL/100) if sgn>0 else ext*(1-TRAIL/100)
                        if (H[k]>=tp) if sgn>0 else (L[k]<=tp):
                            res="TRAIL"; pnl=((ep-tp)/ep*100) if sgn>0 else ((tp-ep)/ep*100); break
                    if res is None:
                        px=C[-1]; res="EOD"
                        pnl=((ep-px)/ep*100) if sgn>0 else ((px-ep)/ep*100)
                    rows.append(dict(d=ds,cover=cover,pnl=pnl,filled=filled,mae=mae,
                                     room=abs(tgt-ep)/ep*100))
        pc=C[-1]

    out=[f"갭 0.2~1.5% · 무손절 · 트레일 {TRAIL}% · n={len(rows)}",""]
    def rep(sel,lab):
        n=len(sel)
        if n<8: out.append(f"  {lab:16s} {n:4d}   표본부족"); return
        w=sum(1 for r in sel if r["pnl"]>0); ci=wilson(w,n)
        g=sum(r["pnl"] for r in sel if r["pnl"]>0); l=-sum(r["pnl"] for r in sel if r["pnl"]<=0)
        fl=sum(1 for r in sel if r["filled"])
        mark="★" if ci[0]>=70 and w/n>=0.80 else (" " if w/n<0.80 else "·")
        out.append(f"  {lab:16s} {n:4d}  {w/n*100:5.1f}% ({ci[0]:4.1f}~{ci[1]:4.1f})  "
                   f"{g/l if l else 99:5.2f}  {np.mean([r['pnl'] for r in sel]):+6.3f}%  "
                   f"{fl/n*100:5.1f}%  {np.mean([r['mae'] for r in sel]):.3f}% {mark}")
    hdr=f"  {'구간':16s} {'표본':>4s}  {'승률':>16s}  {'PF':>5s}  {'평균':>7s}  {'갭필률':>6s}  {'MAE':>6s}"
    out.append("[5% 단위 구간]"); out.append(hdr)
    e=0.05
    b=-1.0
    while b<1.5:
        sel=[r for r in rows if b<=r["cover"]<b+e]
        if len(sel)>=8:
            rep(sel,f"{b*100:.0f}~{(b+e)*100:.0f}%")
        b+=e
    out.append("")
    out.append("[누적 — x% 이상]"); out.append(hdr)
    for t in [x/100 for x in range(0,105,5)]:
        rep([r for r in rows if r["cover"]>=t],f"{t*100:.0f}%+")
    out.append("")
    out.append("[누적 상한 1.0 — x%~100%]"); out.append(hdr)
    for t in [x/100 for x in range(0,100,5)]:
        rep([r for r in rows if t<=r["cover"]<1.0],f"{t*100:.0f}~100%")
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("covthresh_result.json","w"),ensure_ascii=False,indent=1)
