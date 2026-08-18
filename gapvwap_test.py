"""갭 반전 신호 + VWAP 밴드 진입 타이밍.

조건: 갭 발생 & 첫 1시간 커버>=30% (= 87% 갭필 확률 구간)
진입: 첫봉 이후 VWAP +1σ 터치(갭업, 숏) / -1σ 터치(갭다운, 롱)
타깃: 전날 종가(갭필)   손절: 당일 극점 / +2σ 두 변형   컷: 14:30
비교군: 첫봉 종가 즉시 진입 (기존 방식)
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

def wilson(k,n):
    if n==0: return (0.0,0.0)
    p,z=k/n,1.96; d=1+z*z/n
    c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (round(max(0,c-h)*100,1),round(min(1,c+h)*100,1))

def bands(H,L,C,V):
    cv=cpv=cpv2=0.0; W=[];S=[]
    for i in range(len(C)):
        tp=(H[i]+L[i]+C[i])/3
        cv+=V[i]; cpv+=tp*V[i]; cpv2+=tp*tp*V[i]
        w=cpv/cv if cv else tp
        W.append(w); S.append(math.sqrt(max(cpv2/cv-w*w,0.0)) if cv else 0.0)
    return W,S

def run(tk, interval, period, stop_mode="extreme"):
    df=yf.download(tk,period=period,interval=interval,prepost=False,auto_adjust=False,progress=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.dropna(); df.index=df.index.tz_convert("America/New_York")
    df=df[(df.index.time>=dt.time(9,30))&(df.index.time<dt.time(16,0))]
    nb={"15m":4,"1h":1}[interval]          # 첫 1시간에 해당하는 봉 수
    cut={"15m":20,"1h":4}[interval]        # 14:30
    need={"15m":15,"1h":5}[interval]
    base=[]; vw=[]; pc=None
    for d in sorted(set(df.index.date)):
        g=df[df.index.date==d]
        if len(g)<need:
            if len(g): pc=float(g["Close"].iloc[-1])
            continue
        O=[float(x) for x in g["Open"]];H=[float(x) for x in g["High"]]
        L=[float(x) for x in g["Low"]];C=[float(x) for x in g["Close"]];V=[float(x) for x in g["Volume"]]
        if pc:
            gap=O[0]-pc; gp=gap/pc*100
            if abs(gp)>=0.05:
                sgn=1 if gap>0 else -1
                c1=C[nb-1]                                  # 첫 1시간 종가
                cover=((O[0]-c1)/gap) if sgn>0 else ((c1-O[0])/abs(gap))
                if cover>=0.30:
                    W,S=bands(H,L,C,V)
                    tgt=pc
                    # (a) 기존: 첫봉 종가 즉시 진입
                    ep0=c1
                    def sim(ep,i0,stop):
                        hitT=hitS=None
                        for i in range(i0+1,min(cut+1,len(C))):
                            if hitS is None and ((H[i]>=stop) if sgn>0 else (L[i]<=stop)): hitS=i
                            if hitT is None and ((L[i]<=tgt) if sgn>0 else (H[i]>=tgt)): hitT=i
                            if hitT is not None or hitS is not None: break
                        if hitT is not None and (hitS is None or hitT<=hitS):
                            return abs(tgt-ep)/ep*100,"TGT"
                        if hitS is not None:
                            return -abs(stop-ep)/ep*100,"STOP"
                        idx=min(cut,len(C)-1)
                        return (((ep-C[idx])/ep*100) if sgn>0 else ((C[idx]-ep)/ep*100)),"CUT"
                    st0=(max(H[:nb]) if sgn>0 else min(L[:nb]))
                    p0,r0=sim(ep0,nb-1,st0)
                    base.append(dict(d=str(d),sgn=sgn,pnl=p0,res=r0))
                    # (b) VWAP 밴드 터치 대기
                    ei=None
                    for i in range(nb,min(cut+1,len(C))):
                        if S[i]<=1e-9: continue
                        if (H[i]>=W[i]+S[i]) if sgn>0 else (L[i]<=W[i]-S[i]):
                            ei=i; ep=(W[i]+S[i]) if sgn>0 else (W[i]-S[i]); break
                    if ei is not None:
                        stop=(max(H[:ei+1]) if sgn>0 else min(L[:ei+1])) if stop_mode=="extreme" \
                             else ((W[ei]+2*S[ei]) if sgn>0 else (W[ei]-2*S[ei]))
                        p1,r1=sim(ep,ei,stop)
                        vw.append(dict(d=str(d),sgn=sgn,pnl=p1,res=r1,bar=ei))
        pc=C[-1]
    return base,vw

def rep(ss,lab,out):
    n=len(ss)
    if n<8: out.append(f"    {lab:30s} n={n:3d} 표본부족"); return
    w=sum(1 for r in ss if r["pnl"]>0); ci=wilson(w,n)
    g=sum(r["pnl"] for r in ss if r["pnl"]>0); l=-sum(r["pnl"] for r in ss if r["pnl"]<=0)
    s2=sorted(ss,key=lambda x:-x["pnl"])[2:]
    g2=sum(r["pnl"] for r in s2 if r["pnl"]>0); l2=-sum(r["pnl"] for r in s2 if r["pnl"]<=0)
    ds=sorted(r["d"] for r in ss); half=ds[len(ds)//2]
    def _pf(x):
        a=sum(r["pnl"] for r in x if r["pnl"]>0); b=-sum(r["pnl"] for r in x if r["pnl"]<=0)
        return (a/b) if b>0 else 99.0
    rc={}
    for r in ss: rc[r["res"]]=rc.get(r["res"],0)+1
    out.append(f"    {lab:30s} n={n:3d} 승률 {w/n*100:5.1f}% CI({ci[0]:4.1f}~{ci[1]:4.1f}) "
               f"PF {g/l if l else 99:5.2f} |상위2제외 {g2/l2 if l2 else 99:5.2f} "
               f"평균 {sum(r['pnl'] for r in ss)/n:+.3f}% | 반반 {_pf([r for r in ss if r['d']<half]):5.2f}/"
               f"{_pf([r for r in ss if r['d']>=half]):5.2f} | {'/'.join(f'{k}{v}' for k,v in sorted(rc.items()))}")

def main():
    out=["갭 + 첫1시간 커버>=30% 구간에서, VWAP 밴드 터치를 진입 타이밍으로 쓰면?",
         "타깃=전날종가(갭필) · 컷 14:30"]
    for tk in ("QQQ","SPY"):
        for iv,per in (("1h","2y"),("15m","60d")):
            for sm in ("extreme","2sigma"):
                b,v=run(tk,iv,per,sm)
                if sm=="extreme":
                    out.append(f"\n{'='*112}\n[{tk}] {iv} {per} · 조건충족 {len(b)}일 · VWAP밴드 터치 {len(v)}일 "
                               f"({len(v)/max(1,len(b))*100:.0f}%)\n{'='*112}")
                    for sgn,nm in ((1,"갭업→숏"),(-1,"갭다운→롱")):
                        out.append(f"  ── {nm} ──")
                        rep([r for r in b if r["sgn"]==sgn],"첫봉종가 즉시진입(비교군)",out)
                        rep([r for r in v if r["sgn"]==sgn],"VWAP밴드 터치 · 손절=극점",out)
                else:
                    for sgn,nm in ((1,"갭업→숏"),(-1,"갭다운→롱")):
                        rep([r for r in v if r["sgn"]==sgn],f"VWAP밴드 터치 · 손절=2σ ({nm})",out)
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("gapvwap_result.json","w"),ensure_ascii=False,indent=1)
