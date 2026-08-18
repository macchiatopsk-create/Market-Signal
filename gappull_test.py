"""갭업 되돌림 진입 — 5분봉.

형님 관찰: 갭업 → 첫봉 음봉으로 밀림 → 다시 위로 되돌림 → 거기서 진짜 하락
따라서 09:30 즉시 진입은 되돌림에 털린다. 되돌림 고점에서 진입해야 한다.

진입 변형 (모두 09:30 이후 탐색):
  IMM   09:30 시가 즉시 (베이스라인)
  R30   첫 눌림 후 갭의 30% 재회복 지점
  R50   50% 재회복
  R70   70% 재회복
  VWU   VWAP +1σ 터치
  HOD   당일 고점 갱신 실패(직전고점 근처 도달 후 하락)
손절: 진입 후 당일고점 + 갭*0.3  (되돌림 위 여유)
타깃: 전날 종가
컷:   14:00
"""
import json, math, datetime as dt, traceback
import yfinance as yf
import pandas as pd

def wilson(k,n):
    if n==0: return (0.0,0.0)
    p,z=k/n,1.96; d=1+z*z/n
    c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return (round(max(0,c-h)*100,1),round(min(1,c+h)*100,1))

def load():
    v=yf.Ticker("^VIX").history(period="6mo")[["Open","Close"]].dropna()
    try: v.index=v.index.tz_localize(None)
    except: pass
    v.index=pd.to_datetime(v.index).normalize()
    vch=(v["Open"]/v["Close"].shift(1)-1)*100
    vm={str(pd.Timestamp(k).date()):float(x) for k,x in vch.dropna().items()}

    df=yf.download("QQQ",period="60d",interval="5m",prepost=False,auto_adjust=False,progress=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.dropna(); df.index=df.index.tz_convert("America/New_York")
    df=df[(df.index.time>=dt.time(9,30))&(df.index.time<dt.time(16,0))]
    days=[]; pc=None
    for d in sorted(set(df.index.date)):
        g=df[df.index.date==d]
        if len(g)<50:
            if len(g): pc=float(g["Close"].iloc[-1])
            continue
        ds=str(d)
        O=[float(x) for x in g["Open"]];H=[float(x) for x in g["High"]]
        L=[float(x) for x in g["Low"]];C=[float(x) for x in g["Close"]];V=[float(x) for x in g["Volume"]]
        T=[t.time() for t in g.index]
        if pc:
            vx=vm.get(ds)
            if vx is None or abs(vx)<5.0:
                gap=O[0]-pc; gp=gap/pc*100
                if 0.2<=gp<1.5:                 # 갭업만
                    days.append(dict(d=ds,gp=gp,pc=pc,O=O,H=H,L=L,C=C,V=V,T=T))
        pc=C[-1]
    return days

def bands(H,L,C,V):
    cv=cpv=cpv2=0.0; W=[];S=[]
    for i in range(len(C)):
        tp=(H[i]+L[i]+C[i])/3
        cv+=V[i]; cpv+=tp*V[i]; cpv2+=tp*tp*V[i]
        w=cpv/cv if cv else tp
        W.append(w); S.append(math.sqrt(max(cpv2/cv-w*w,0.0)) if cv else 0.0)
    return W,S

CUT=dt.time(14,0)
def entry_idx(day,mode):
    O,H,L,C,T=day["O"],day["H"],day["L"],day["C"],day["T"]
    gap=O[0]-day["pc"]
    if mode=="IMM": return 0,O[0]
    W,S=bands(H,L,C,day["V"])
    dipped=False; lo_after=O[0]
    for i in range(1,len(C)):
        if T[i]>=CUT: break
        lo_after=min(lo_after,L[i])
        if not dipped and L[i]<O[0]-gap*0.15:      # 최소 갭 15%는 밀려야 '눌림'
            dipped=True; continue
        if dipped:
            if mode in ("R30","R50","R70"):
                frac={"R30":0.30,"R50":0.50,"R70":0.70}[mode]
                lvl=lo_after+(O[0]-lo_after)*frac if O[0]>lo_after else O[0]
                if H[i]>=lvl: return i,lvl
            elif mode=="VWU":
                if S[i]>1e-9 and H[i]>=W[i]+S[i]: return i,W[i]+S[i]
            elif mode=="HOD":
                prev=max(H[:i])
                if H[i]>=prev*(1-0.0008) and H[i]<prev: return i,C[i]
    return None,None

def sim(day,mode):
    i,ep=entry_idx(day,mode)
    if i is None: return None
    O,H,L,C,T=day["O"],day["H"],day["L"],day["C"],day["T"]
    gap=O[0]-day["pc"]; tgt=day["pc"]
    stop=max(H[:i+1])+gap*0.3
    for j in range(i+1,len(C)):
        if H[j]>=stop: return dict(d=day["d"],res="STOP",ux=-(stop-ep)/ep*100,hold=(j-i)*5)
        if L[j]<=tgt:  return dict(d=day["d"],res="TGT", ux=(ep-tgt)/ep*100,hold=(j-i)*5)
        if T[j]>=CUT:  return dict(d=day["d"],res="CUT", ux=(ep-C[j])/ep*100,hold=(j-i)*5)
    return dict(d=day["d"],res="EOD",ux=(ep-C[-1])/ep*100,hold=(len(C)-1-i)*5)

def rep(res,lab,tot,out):
    n=len(res)
    if n<6: out.append(f"  {lab:6s} n={n:3d}/{tot} 표본부족"); return
    w=sum(1 for x in res if x["ux"]>0); ci=wilson(w,n)
    g=sum(x["ux"] for x in res if x["ux"]>0); l=-sum(x["ux"] for x in res if x["ux"]<=0)
    rc={}
    for x in res: rc[x["res"]]=rc.get(x["res"],0)+1
    out.append(f"  {lab:6s} n={n:3d}/{tot} 진입률 {n/tot*100:5.1f}% | 승률 {w/n*100:5.1f}%({ci[0]:.0f}~{ci[1]:.0f}) "
               f"PF {g/l if l else 99:5.2f} | 평균 {sum(x['ux'] for x in res)/n:+.3f}% "
               f"이익평균 {(g/w if w else 0):+.3f}% 손실평균 {-(l/(n-w) if n-w else 0):+.3f}% "
               f"| 보유 {sum(x['hold'] for x in res)/n:3.0f}분 | {'/'.join(f'{k}{v}' for k,v in sorted(rc.items()))}")

def main():
    days=load()
    out=[f"QQQ 갭업 0.2~1.5% · 5분봉 60일 · {len(days)}일",
         "손절=진입시점 당일고점+갭30% · 타깃=전날종가 · 컷 14:00",""]
    for mode in ("IMM","R30","R50","R70","VWU","HOD"):
        res=[x for x in (sim(d,mode) for d in days) if x]
        rep(res,mode,len(days),out)
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("gappull_result.json","w"),ensure_ascii=False,indent=1)
