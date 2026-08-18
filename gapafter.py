"""갭필 '이후'를 측정 — 갭필에서 끊지 말아야 하는가.

조건: 갭 0.2~1.0% (VIX 개장변화 |5%| 제외)
1) 갭필 도달 여부/시각
2) 갭필 이후 추가 진행폭 (같은 방향으로 얼마나 더 가나)
3) 갭필 이후 되돌림폭 (갭필에서 안 끊으면 얼마나 토해내나)
4) 당일 종가 / 다음날까지 홀드 결과
5) 최적 청산 시각 분포
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
    out=[]
    v=norm(yf.Ticker("^VIX").history(period="2y")[["Open","Close"]].dropna())
    vch=(v["Open"]/v["Close"].shift(1)-1)*100
    vm={str(pd.Timestamp(k).date()):float(x) for k,x in vch.dropna().items()}

    df=yf.download("QQQ",period="2y",interval="1h",prepost=False,auto_adjust=False,progress=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.dropna(); df.index=df.index.tz_convert("America/New_York")
    df=df[(df.index.time>=dt.time(9,30))&(df.index.time<dt.time(16,0))]

    days=sorted(set(df.index.date)); rows=[]; pc=None
    for i,d in enumerate(days):
        g=df[df.index.date==d]
        if len(g)<5:
            if len(g): pc=float(g["Close"].iloc[-1])
            continue
        ds=str(d)
        O=[float(x) for x in g["Open"]];H=[float(x) for x in g["High"]]
        L=[float(x) for x in g["Low"]];C=[float(x) for x in g["Close"]]
        T=[t.strftime("%H:%M") for t in g.index]
        if pc:
            vx=vm.get(ds)
            if vx is None or abs(vx)<5.0:
                gap=O[0]-pc; gp=gap/pc*100
                if 0.2<=abs(gp)<1.0:
                    sgn=1 if gap>0 else -1
                    ep=O[0]; tgt=pc
                    fb=None
                    for k in range(len(C)):
                        if (L[k]<=tgt) if sgn>0 else (H[k]>=tgt): fb=k; break
                    r=dict(d=ds,gp=abs(gp),sgn=sgn,ep=ep,tgt=tgt,
                           room=abs(tgt-ep)/ep*100, filled=(fb is not None),
                           fill_t=(T[fb] if fb is not None else None))
                    if fb is not None:
                        # 갭필 이후 같은 방향 추가 진행 / 반대 되돌림
                        after_lo=min(L[fb:]); after_hi=max(H[fb:])
                        r["beyond"]=((tgt-after_lo)/tgt*100) if sgn>0 else ((after_hi-tgt)/tgt*100)
                        r["giveback"]=((after_hi-tgt)/tgt*100) if sgn>0 else ((tgt-after_lo)/tgt*100)
                        # 최적 지점 시각
                        seq=[(T[k], (tgt-L[k])/tgt*100 if sgn>0 else (H[k]-tgt)/tgt*100)
                             for k in range(fb,len(C))]
                        bt,bv=max(seq,key=lambda x:x[1])
                        r["best_t"],r["best"]=bt,bv
                        # 진입가 기준 총 수익 (갭필+추가)
                        r["eod"]=((ep-C[-1])/ep*100) if sgn>0 else ((C[-1]-ep)/ep*100)
                        r["max_total"]=r["room"]+r["beyond"]
                    rows.append(r)
                    # 다음날
                    if i+1<len(days):
                        g2=df[df.index.date==days[i+1]]
                        if len(g2)>=5:
                            o2=float(g2["Open"].iloc[0]); c2=float(g2["Close"].iloc[-1])
                            rows[-1]["nxt"]=((o2-c2)/o2*100) if sgn>0 else ((c2-o2)/o2*100)
        pc=C[-1]

    F=[r for r in rows if r["filled"]]
    out.append(f"갭 0.2~1.0% {len(rows)}일 · 갭필 도달 {len(F)}일 ({len(F)/max(len(rows),1)*100:.1f}%)")
    out.append("")
    def stat(sel,lab):
        n=len(sel)
        if n<10: out.append(f"  {lab:22s} n={n:3d} 부족"); return
        by=[r["beyond"] for r in sel]; gb=[r["giveback"] for r in sel]
        out.append(f"  {lab:22s} n={n:3d} | 갭필거리 {np.mean([r['room'] for r in sel]):.3f}% "
                   f"| 이후 추가진행 {np.mean(by):.3f}% (중앙 {np.median(by):.3f}%) "
                   f"| 이후 되돌림 {np.mean(gb):.3f}% "
                   f"| 최대총이익 {np.mean([r['max_total'] for r in sel]):.3f}%")
    out.append("[갭필 이후 얼마나 더 가나]")
    stat(F,"전체")
    stat([r for r in F if r["sgn"]>0],"갭업(하락 진행)")
    stat([r for r in F if r["sgn"]<0],"갭다운(상승 진행)")
    out.append("")
    out.append("[청산 방식 비교 — 진입가 기준 %]")
    for lab,key in (("갭필에서 청산","room"),("종가까지 홀드","eod"),("최대치(사후최적)","max_total")):
        vals=[r[key] for r in F if key in r]
        w=sum(1 for x in vals if x>0); n=len(vals); ci=wilson(w,n)
        out.append(f"  {lab:18s} n={n:3d} 평균 {np.mean(vals):+.3f}% 중앙 {np.median(vals):+.3f}% "
                   f"승률 {w/n*100:5.1f}% CI({ci[0]:.0f}~{ci[1]:.0f}) 최악 {min(vals):+.3f}%")
    nx=[r["nxt"] for r in F if "nxt" in r]
    if nx:
        w=sum(1 for x in nx if x>0)
        out.append(f"  {'다음날 같은방향':18s} n={len(nx):3d} 평균 {np.mean(nx):+.3f}% "
                   f"승률 {w/len(nx)*100:5.1f}%")
    out.append("")
    out.append("[갭필 이후 추가진행 분포]")
    by=sorted(r["beyond"] for r in F)
    for p in (10,25,50,75,90):
        out.append(f"  {p}%tile {by[int(len(by)*p/100)]:.3f}%")
    out.append(f"  추가진행이 갭필거리보다 큰 경우: "
               f"{sum(1 for r in F if r['beyond']>r['room'])}/{len(F)} "
               f"({sum(1 for r in F if r['beyond']>r['room'])/len(F)*100:.1f}%)")
    out.append("")
    out.append("[최적 청산 시각 분포]")
    cnt={}
    for r in F: cnt[r["best_t"]]=cnt.get(r["best_t"],0)+1
    for t in sorted(cnt): out.append(f"  {t}  {cnt[t]:3d}건 ({cnt[t]/len(F)*100:4.1f}%)")
    out.append("")
    out.append("[갭필 시각 분포]")
    cnt2={}
    for r in F: cnt2[r["fill_t"]]=cnt2.get(r["fill_t"],0)+1
    for t in sorted(cnt2): out.append(f"  {t}  {cnt2[t]:3d}건 ({cnt2[t]/len(F)*100:4.1f}%)")
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("gapafter_result.json","w"),ensure_ascii=False,indent=1)
