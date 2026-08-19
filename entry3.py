"""세 갈래 진입 비교 — 같은 날짜 페어.

조건: 갭 0.2~1.5% & 첫봉 커버 40%+ (VIX개장 |5%| 제외)
진입 변형:
  A  첫봉 종가 즉시
  B  VWAP 역방향 밴드(+1σ/-1σ) 터치 대기 — 안 오면 미진입(기회 놓침)
  C  VWAP 중간선 되돌림 터치 대기
공통: 갭필까지 홀드 → 갭필 후 트레일 0.15% / 손절 0.6·0.7·0.8% 비교
기록: 진입시각·진입시 σ위치·갭필시각·MFE와 시각·MAE·종가홀드 결과
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd
import numpy as np

TRAIL=0.15
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

def bands(H,L,C,V):
    cv=cpv=cpv2=0.0; W=[];S=[]
    for i in range(len(C)):
        tp=(H[i]+L[i]+C[i])/3
        cv+=V[i]; cpv+=tp*V[i]; cpv2+=tp*tp*V[i]
        w=cpv/cv if cv else tp
        W.append(w); S.append(math.sqrt(max(cpv2/cv-w*w,0.0)) if cv else 0.0)
    return W,S

def run(interval, period, stop_pct=None):
    v=norm(yf.Ticker("^VIX").history(period="2y")[["Open","Close"]].dropna())
    ch=(v["Open"]/v["Close"].shift(1)-1)*100
    vm={str(pd.Timestamp(k).date()):float(x) for k,x in ch.dropna().items()}
    df=yf.download("QQQ",period=period,interval=interval,prepost=False,auto_adjust=False,progress=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.dropna(); df.index=df.index.tz_convert("America/New_York")
    df=df[(df.index.time>=dt.time(9,30))&(df.index.time<dt.time(16,0))]
    need={"15m":20,"30m":10,"1h":5}[interval]
    days=sorted(set(df.index.date)); out=[]; pc=None
    for d in days:
        g=df[df.index.date==d]
        if len(g)<need:
            if len(g): pc=float(g["Close"].iloc[-1])
            continue
        ds=str(d)
        O=[float(x) for x in g["Open"]];H=[float(x) for x in g["High"]]
        L=[float(x) for x in g["Low"]];C=[float(x) for x in g["Close"]]
        V=[float(x) for x in g["Volume"]];T=[t.strftime("%H:%M") for t in g.index]
        if pc:
            vx=vm.get(ds)
            if vx is None or abs(vx)<5.0:
                gap=O[0]-pc; gp=gap/pc*100
                if 0.2<=abs(gp)<1.5:
                    sgn=1 if gap>0 else -1
                    cover=((O[0]-C[0])/gap) if sgn>0 else ((C[0]-O[0])/abs(gap))
                    if cover>=0.40:
                        W,S=bands(H,L,C,V)
                        tgt=pc
                        # 갭필 시각
                        fb=None
                        for k in range(len(C)):
                            if (L[k]<=tgt) if sgn>0 else (H[k]>=tgt): fb=k; break
                        rec=dict(d=ds,gp=abs(gp),sgn=sgn,cover=cover,
                                 fill_t=(T[fb] if fb is not None else None),
                                 over100=(cover>=1.0))
                        for mode in ("A","B","C"):
                            ei=ep=None
                            if mode=="A": ei,ep=0,C[0]
                            else:
                                for k in range(1,len(C)):
                                    if S[k]<=1e-9: continue
                                    if mode=="B":
                                        lvl=W[k]+sgn*S[k]
                                        if (H[k]>=lvl) if sgn>0 else (L[k]<=lvl): ei,ep=k,lvl; break
                                    else:
                                        if (H[k]>=W[k]) if sgn>0 else (L[k]<=W[k]): ei,ep=k,W[k]; break
                            if ei is None:
                                rec[mode]=None; continue
                            sig=(ep-W[ei])/S[ei] if S[ei]>1e-9 else 0.0
                            filled=(fb is not None and fb>=ei)
                            ext=ep; res=None; pnl=None; mae=0.0; mfe=0.0; mfe_t=T[ei]
                            fl=False
                            stop_px=(ep*(1+stop_pct/100) if sgn>0 else ep*(1-stop_pct/100)) if stop_pct else None
                            for k in range(ei+1,len(C)):
                                adv=(H[k]-ep)/ep*100 if sgn>0 else (ep-L[k])/ep*100
                                fav=(ep-L[k])/ep*100 if sgn>0 else (H[k]-ep)/ep*100
                                mae=max(mae,adv)
                                if fav>mfe: mfe,mfe_t=fav,T[k]
                                if stop_px is not None:
                                    if (H[k]>=stop_px) if sgn>0 else (L[k]<=stop_px):
                                        res,pnl="STOP",-stop_pct
                                        # 손절 안 했으면 이후 갭필했는지
                                        for k2 in range(k+1,len(C)):
                                            if (L[k2]<=tgt) if sgn>0 else (H[k2]>=tgt):
                                                res="STOP_RECOV"; break
                                        break
                                if not fl:
                                    if (L[k]<=tgt) if sgn>0 else (H[k]>=tgt):
                                        fl=True; ext=min(L[k],tgt) if sgn>0 else max(H[k],tgt)
                                    continue
                                ext=min(ext,L[k]) if sgn>0 else max(ext,H[k])
                                tp=ext*(1+TRAIL/100) if sgn>0 else ext*(1-TRAIL/100)
                                if (H[k]>=tp) if sgn>0 else (L[k]<=tp):
                                    res="TRAIL"; pnl=((ep-tp)/ep*100) if sgn>0 else ((tp-ep)/ep*100)
                                    rec[mode+"_exit_t"]=T[k]; break
                            if res is None:
                                px=C[-1]; res="EOD"
                                pnl=((ep-px)/ep*100) if sgn>0 else ((px-ep)/ep*100)
                                rec[mode+"_exit_t"]=T[-1]
                            rec[mode]=dict(t=T[ei],ep=ep,sig=sig,res=res,pnl=pnl,
                                           mae=mae,mfe=mfe,mfe_t=mfe_t,filled=fl)
                        out.append(rec)
        pc=C[-1]
    return out

def rep(rows,mode,lab,out):
    sel=[r[mode] for r in rows if r.get(mode)]
    miss=sum(1 for r in rows if not r.get(mode))
    n=len(sel)
    if n<8: out.append(f"  {lab:22s} n={n:3d} 표본부족"); return
    w=sum(1 for x in sel if x["pnl"]>0); ci=wilson(w,n)
    g=sum(x["pnl"] for x in sel if x["pnl"]>0); l=-sum(x["pnl"] for x in sel if x["pnl"]<=0)
    rc={}
    for x in sel: rc[x["res"]]=rc.get(x["res"],0)+1
    rcv=rc.get("STOP_RECOV",0); stp=rc.get("STOP",0)+rcv
    out.append(f"  {lab:22s} n={n:3d}(놓침{miss:2d}) 승률 {w/n*100:5.1f}%({ci[0]:.0f}~{ci[1]:.0f}) "
               f"PF {g/l if l else 99:6.2f} 평균 {np.mean([x['pnl'] for x in sel]):+.3f}% "
               f"| 진입σ {np.mean([x['sig'] for x in sel]):+.2f} MFE {np.mean([x['mfe'] for x in sel]):.3f}% "
               f"MAE {np.mean([x['mae'] for x in sel]):.3f}% | 손절{stp}건"
               f"{f'(그중 {rcv}건은 안했으면 갭필됨)' if stp else ''} "
               f"| {'/'.join(f'{k}{v}' for k,v in sorted(rc.items()))}")

def main():
    out=[]
    for iv,per,lab in (("1h","2y","1시간봉 2년"),("30m","60d","30분봉 60일"),("15m","60d","15분봉 60일")):
        rows=run(iv,per,None)
        if not rows: out.append(f"[{lab}] 데이터 없음"); continue
        o100=[r for r in rows if r["over100"]]
        out.append(f"━━ {lab} · 커버40%+ {len(rows)}일 (그중 100%+ {len(o100)}일) · 무손절+트레일{TRAIL}% ━━")
        for m,ml in (("A","A 첫봉종가 즉시"),("B","B VWAP역밴드 대기"),("C","C VWAP중간선 대기")):
            rep(rows,m,ml,out)
        if len(o100)>=8:
            out.append("  -- 커버 100%+ 만 --")
            for m,ml in (("A","A 첫봉종가"),("B","B VWAP역밴드"),("C","C VWAP중간선")):
                rep(o100,m,"   "+ml,out)
        out.append("")
    out.append("[손절 폭 비교 · 1시간봉 · A진입]")
    for sp in (0.6,0.7,0.8,0.9,1.0,1.2,1.5,None):
        rows=run("1h","2y",sp)
        rep(rows,"A",f"손절 {sp if sp else '없음'}%",out)
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("entry3_result.json","w"),ensure_ascii=False,indent=1)
