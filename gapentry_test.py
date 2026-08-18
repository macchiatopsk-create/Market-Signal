"""진입 시점 비교 — 커버 조건 없이 갭 크기만으로.

조건: 갭 0.2~1.0% (VIX 개장변화 |x|>=5% 제외)
진입 변형:
  A 09:30 시가 즉시        (첫봉 안 기다림)
  B 첫봉(09:30~10:30) 종가  (기존)
  C 첫봉 종가 + 커버>=0.30  (참고: 조건 유지 시)
타깃: 남은갭 50% / 70% / 100%
손절: 당일 극점 기준 / 없음(14:30 컷) 두 변형
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
            if vx is None or abs(vx)<5.0:
                gap=O[0]-pc; gp=gap/pc*100
                if 0.2<=abs(gp)<1.0:
                    sgn=1 if gap>0 else -1
                    cover=((O[0]-C[0])/gap) if sgn>0 else ((C[0]-O[0])/abs(gap))
                    rows.append(dict(d=ds,sgn=sgn,gp=abs(gp),cover=cover,pc=pc,
                                     O=O,H=H,L=L,C=C))
        pc=C[-1]
    return rows

def sim(r, mode, tgt_frac, use_stop):
    sgn,pc=r["sgn"],r["pc"]; O,H,L,C=r["O"],r["H"],r["L"],r["C"]
    if mode=="A": ep,i0=O[0],0
    else:         ep,i0=C[0],0
    if mode!="A": i0=0                       # 첫봉 종가 = index0 종가, 이후 index1부터 관찰
    start=1
    room=abs(pc-ep)
    if room<=0: return None
    tgt = ep - sgn*room*tgt_frac             # 갭업(sgn=1)이면 아래로
    stop = (max(H[:1]) if sgn>0 else min(L[:1])) if mode!="A" else (O[0]+sgn*abs(gap_pad(r)))
    hitT=hitS=None
    for i in range(start,min(5,len(C))):
        if use_stop and hitS is None:
            if (H[i]>=stop) if sgn>0 else (L[i]<=stop): hitS=i
        if hitT is None:
            if (L[i]<=tgt) if sgn>0 else (H[i]>=tgt): hitT=i
        if hitT is not None or hitS is not None: break
    if hitT is not None and (hitS is None or hitT<=hitS):
        return dict(pnl=abs(tgt-ep)/ep*100,res="TGT")
    if hitS is not None:
        return dict(pnl=-abs(stop-ep)/ep*100,res="STOP")
    idx=min(4,len(C)-1); px=C[idx]
    return dict(pnl=(((ep-px)/ep*100) if sgn>0 else ((px-ep)/ep*100)),res="CUT")

def gap_pad(r):
    return abs(r["pc"]*r["gp"]/100)*0.5      # A모드 손절: 갭의 50% 역행

def rep(res,lab,out):
    n=len(res)
    if n<8: out.append(f"    {lab:26s} n={n:3d} 표본부족"); return
    w=sum(1 for x in res if x["pnl"]>0); ci=wilson(w,n)
    g=sum(x["pnl"] for x in res if x["pnl"]>0); l=-sum(x["pnl"] for x in res if x["pnl"]<=0)
    s2=sorted(res,key=lambda x:-x["pnl"])[2:]
    g2=sum(x["pnl"] for x in s2 if x["pnl"]>0); l2=-sum(x["pnl"] for x in s2 if x["pnl"]<=0)
    ds=sorted(x["d"] for x in res); half=ds[len(ds)//2]
    def _pf(z):
        a=sum(x["pnl"] for x in z if x["pnl"]>0); b=-sum(x["pnl"] for x in z if x["pnl"]<=0)
        return (a/b) if b>0 else 99.0
    rc={}
    for x in res: rc[x["res"]]=rc.get(x["res"],0)+1
    out.append(f"    {lab:26s} n={n:3d} 승률 {w/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
               f"PF {g/l if l else 99:6.2f} |상위2 {g2/l2 if l2 else 99:6.2f} "
               f"평균 {sum(x['pnl'] for x in res)/n:+.3f}% 합계 {sum(x['pnl'] for x in res):+6.1f}% "
               f"| 반반 {_pf([x for x in res if x['d']<half]):5.2f}/{_pf([x for x in res if x['d']>=half]):5.2f} "
               f"| {'/'.join(f'{k}{v}' for k,v in sorted(rc.items()))}")

def main():
    vm=vmap_open()
    out=["갭 0.2~1.0% · VIX개장변화 |5%| 제외 · 14:30 컷",
         "A=09:30 시가 진입(손절 갭50% 역행) · B=첫봉종가 진입(손절 첫봉극점) · C=B+커버>=0.30"]
    for tk in ("QQQ","SPY"):
        rows=build(tk,vm)
        out.append(f"\n{'='*128}\n[{tk}] 갭 0.2~1.0% 발생 {len(rows)}일\n{'='*128}")
        for sgn,nm in ((1,"갭업→숏"),(-1,"갭다운→롱")):
            ss=[r for r in rows if r["sgn"]==sgn]
            out.append(f"  ══ {nm} (n={len(ss)}) ══")
            for tf,tlab in ((0.5,"타깃 갭50%"),(0.7,"타깃 갭70%"),(1.0,"타깃 갭100%")):
                out.append(f"   ── {tlab} ──")
                for mode,mlab,sel in (("A","A 09:30시가",ss),("B","B 첫봉종가",ss),
                                      ("C","C 첫봉+커버30",[r for r in ss if r["cover"]>=0.30])):
                    md = "B" if mode=="C" else mode
                    res=[]
                    for r in sel:
                        x=sim(r,md,tf,True)
                        if x: x["d"]=r["d"]; res.append(x)
                    rep(res,mlab,out)
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("gapentry_result.json","w"),ensure_ascii=False,indent=1)
