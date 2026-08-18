"""갭 진행률(MFE) 측정 — 완전 갭필이 아니라 '얼마나 가는가'.

조건: 갭 & 첫봉 커버 0.30~1.00 (상한으로 이미 메워진 날 제외)
측정: 진입(첫봉 종가) 후 14:30까지, 남은 갭(진입가->전날종가) 대비
      최대 진행률 MFE% = 얼마나 타깃 쪽으로 갔나
      50%/70%/100% 도달률 · 최대 역행 MAE · 갭 크기별 분해
VIX 개장변화 |x|>=5% 제외
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

def wilson(k,n):
    if n==0: return (0.0,0.0)
    p,z=k/n,1.96; d=1+z*z/n
    c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (round(max(0,c-h)*100,1),round(min(1,c+h)*100,1))

def vmap_open():
    import time
    for i in range(4):
        try:
            d=yf.Ticker("^VIX").history(period="3y")[["Open","Close"]].dropna()
            if len(d)>100:
                try: d.index=d.index.tz_localize(None)
                except: pass
                d.index=pd.to_datetime(d.index).normalize()
                d=d[~d.index.duplicated(keep="last")]
                ch=(d["Open"]/d["Close"].shift(1)-1)*100
                return {str(pd.Timestamp(k).date()):float(v) for k,v in ch.dropna().items()}
        except Exception: pass
        time.sleep(5*(2**i))
    return {}

def build(tk,vm):
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
        ds=str(d)
        O=[float(x) for x in g["Open"]];H=[float(x) for x in g["High"]]
        L=[float(x) for x in g["Low"]];C=[float(x) for x in g["Close"]]
        if pc:
            vx=vm.get(ds)
            if vx is not None and abs(vx)>=5.0: pc=C[-1]; continue
            gap=O[0]-pc; gp=gap/pc*100
            if abs(gp)>=0.05:
                sgn=1 if gap>0 else -1
                cover=((O[0]-C[0])/gap) if sgn>0 else ((C[0]-O[0])/abs(gap))
                if 0.30<=cover<1.00:                       # 상한으로 동어반복 제거
                    ep=C[0]; tgt=pc
                    room=abs(tgt-ep)                        # 남은 거리(절대)
                    if room<=0: pc=C[-1]; continue
                    best=0.0; worst=0.0
                    for i in range(1,min(5,len(C))):        # 14:30까지
                        fav=(ep-L[i]) if sgn>0 else (H[i]-ep)   # 타깃 쪽 진행
                        adv=(H[i]-ep) if sgn>0 else (ep-L[i])   # 역행
                        best=max(best,fav); worst=max(worst,adv)
                    rows.append(dict(d=ds,sgn=sgn,gp=abs(gp),cover=cover,
                                     room_pct=room/ep*100,
                                     prog=best/room*100,            # 남은갭 대비 진행률
                                     mfe_pct=best/ep*100,
                                     mae_pct=worst/ep*100))
        pc=C[-1]
    return rows

def rep(ss,lab,out):
    n=len(ss)
    if n<6: out.append(f"    {lab:20s} n={n:3d} 표본부족"); return
    p50=sum(1 for r in ss if r["prog"]>=50); p70=sum(1 for r in ss if r["prog"]>=70)
    p100=sum(1 for r in ss if r["prog"]>=100)
    c50,c70,c100=wilson(p50,n),wilson(p70,n),wilson(p100,n)
    med=sorted(r["prog"] for r in ss)[n//2]
    out.append(f"    {lab:20s} n={n:3d} 남은갭 {sum(r['room_pct'] for r in ss)/n:.3f}% "
               f"| 진행률중앙 {med:5.1f}% | 50%도달 {p50/n*100:5.1f}%({c50[0]:.0f}~{c50[1]:.0f}) "
               f"70% {p70/n*100:5.1f}%({c70[0]:.0f}~{c70[1]:.0f}) 100% {p100/n*100:5.1f}%({c100[0]:.0f}~{c100[1]:.0f}) "
               f"| MFE {sum(r['mfe_pct'] for r in ss)/n:.3f}% MAE {sum(r['mae_pct'] for r in ss)/n:.3f}%")

def main():
    vm=vmap_open()
    out=["커버 0.30~1.00 (이미 메운 날 제외) · 진입 첫봉종가 · 14:30까지",
         "진행률 = 남은갭(진입가→전날종가) 대비 최대 진행 %. 100% = 완전 갭필"]
    for tk in ("QQQ","SPY"):
        rows=build(tk,vm)
        out.append(f"\n{'='*126}\n[{tk}] 조건충족 {len(rows)}일\n{'='*126}")
        for sgn,nm in ((1,"갭업→숏"),(-1,"갭다운→롱")):
            ss=[r for r in rows if r["sgn"]==sgn]
            out.append(f"  ── {nm} (n={len(ss)}) ──")
            rep(ss,"전체",out)
            out.append("   [남은 갭 거리별]")
            for lo,hi,lab in ((0,0.15,"~0.15%"),(0.15,0.3,"0.15~0.3%"),
                              (0.3,0.5,"0.3~0.5%"),(0.5,99,"0.5%+")):
                rep([r for r in ss if lo<=r["room_pct"]<hi],lab,out)
            out.append("   [원래 갭 크기별]")
            for lo,hi,lab in ((0,0.3,"갭 ~0.3%"),(0.3,0.6,"갭 0.3~0.6%"),
                              (0.6,1.0,"갭 0.6~1.0%"),(1.0,99,"갭 1.0%+")):
                rep([r for r in ss if lo<=r["gp"]<hi],lab,out)
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("gapprog_result.json","w"),ensure_ascii=False,indent=1)
