"""갭 × 첫봉 커버율 종합 — 봉 크기별.

진입: 장 시작 첫봉 종가 (봉 크기 = 15분/30분/1시간)
초기손절: 첫봉 극점(반대편)
갭필 도달 후: 트레일링 스탑으로 전환 (여러 폭 비교)
측정: 표본 / 승률 / PF / 갭필률 / 갭필 이후 추가진행 / 다음날 양방향
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

def vixmap():
    v=norm(yf.Ticker("^VIX").history(period="2y")[["Open","Close"]].dropna())
    ch=(v["Open"]/v["Close"].shift(1)-1)*100
    return {str(pd.Timestamp(k).date()):float(x) for k,x in ch.dropna().items()}

def load(interval, period):
    df=yf.download("QQQ",period=period,interval=interval,prepost=False,
                   auto_adjust=False,progress=False)
    if isinstance(df.columns,pd.MultiIndex): df.columns=df.columns.get_level_values(0)
    df=df.dropna(); df.index=df.index.tz_convert("America/New_York")
    return df[(df.index.time>=dt.time(9,30))&(df.index.time<dt.time(16,0))]

def build(interval, period, vm, trail):
    df=load(interval,period)
    days=sorted(set(df.index.date)); rows=[]; pc=None
    need={"15m":20,"30m":10,"1h":5}[interval]
    for i,d in enumerate(days):
        g=df[df.index.date==d]
        if len(g)<need:
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
                    filled=False; ext=ep; res=None; pnl=None; fillbar=None
                    mae=0.0
                    for k in range(1,len(C)):
                        adv=(H[k]-ep)/ep*100 if sgn>0 else (ep-L[k])/ep*100
                        mae=max(mae,adv)
                        if not filled:
                            hit_t=(L[k]<=tgt) if sgn>0 else (H[k]>=tgt)
                            if hit_t:
                                filled=True; fillbar=k
                                ext=min(L[k],tgt) if sgn>0 else max(H[k],tgt)
                            continue
                        else:
                            ext=min(ext,L[k]) if sgn>0 else max(ext,H[k])
                            tp=ext*(1+trail/100) if sgn>0 else ext*(1-trail/100)
                            if (H[k]>=tp) if sgn>0 else (L[k]<=tp):
                                res="TRAIL"; pnl=(abs(ep-tp)/ep*100)*(1 if ((sgn>0 and tp<ep) or (sgn<0 and tp>ep)) else -1)
                                break
                    if res is None:
                        px=C[-1]
                        res="EOD" if filled else "EOD_NOFILL"
                        pnl=((ep-px)/ep*100) if sgn>0 else ((px-ep)/ep*100)
                    beyond=(abs(ext-tgt)/tgt*100) if filled else 0.0
                    r=dict(d=ds,gp=abs(gp),sgn=sgn,cover=cover,res=res,pnl=pnl,
                           filled=filled,room=abs(tgt-ep)/ep*100,beyond=beyond,mae=mae)
                    if i+1<len(days):
                        g2=df[df.index.date==days[i+1]]
                        if len(g2)>=need:
                            o2=float(g2["Open"].iloc[0]); c2=float(g2["Close"].iloc[-1])
                            h2=float(g2["High"].max()); l2=float(g2["Low"].min())
                            r["n_ret"]=(c2/o2-1)*100
                            r["n_up"]=(h2/o2-1)*100
                            r["n_dn"]=(1-l2/o2)*100
                    rows.append(r)
        pc=C[-1]
    return rows

def row(sel,lab,out):
    n=len(sel)
    if n<8:
        out.append(f"  {lab:14s} {n:4d}  {'표본부족':>60s}"); return
    w=sum(1 for r in sel if r["pnl"]>0)
    g=sum(r["pnl"] for r in sel if r["pnl"]>0); l=-sum(r["pnl"] for r in sel if r["pnl"]<=0)
    ci=wilson(w,n)
    fl=sum(1 for r in sel if r["filled"])
    by=[r["beyond"] for r in sel if r["filled"]]
    nx=[r for r in sel if "n_ret" in r]
    nu=[r["n_ret"] for r in nx if r["n_ret"]>0]; nd=[r["n_ret"] for r in nx if r["n_ret"]<=0]
    out.append(f"  {lab:14s} {n:4d}  {w/n*100:5.1f}% ({ci[0]:4.1f}~{ci[1]:4.1f})  "
               f"{g/l if l else 99:5.2f}  {np.mean([r['pnl'] for r in sel]):+6.3f}%  "
               f"{fl/n*100:5.1f}%  {np.mean(by) if by else 0:5.3f}%  "
               f"{np.mean([r['mae'] for r in sel]):5.3f}%  "
               f"{len(nu)/len(nx)*100 if nx else 0:4.0f}%↑{np.mean(nu) if nu else 0:+5.2f}%  "
               f"{len(nd)/len(nx)*100 if nx else 0:4.0f}%↓{np.mean(nd) if nd else 0:+5.2f}%")

def main():
    vm=vixmap()
    out=["갭 0.2~1.5% · VIX개장변화 |5%| 제외 · 진입=첫봉 종가 · 무손절",
         "손절 없음(갭필까지 홀드) · 갭필 도달 시 트레일링 전환 · MAE=최대 역행폭",""]
    CB=[(-9,0.25,"커버 <25%"),(0.25,0.50,"커버 25~50%"),(0.50,0.75,"커버 50~75%"),
        (0.75,1.00,"커버 75~100%"),(1.00,9,"커버 100%+"),
        (0.25,9,"[누적] 25%+"),(0.50,9,"[누적] 50%+"),(-9,9,"전체")]
    for iv,per,lab in (("15m","60d","15분봉(60일)"),("30m","60d","30분봉(60일)"),("1h","2y","1시간봉(2년)")):
        try: rows=build(iv,per,vm,0.3)
        except Exception as e:
            out.append(f"[{lab}] 실패 {type(e).__name__}: {e}"); continue
        out.append(f"━━ {lab} · 조건충족 {len(rows)}일 ━━")
        out.append(f"  {'구간':14s} {'표본':>4s}  {'승률':>16s}  {'PF':>5s}  {'평균':>7s}  "
                   f"{'갭필률':>6s}  {'갭필후':>6s}  {'MAE':>6s}  {'다음날 상승':>12s}  {'다음날 하락':>12s}")
        for lo,hi,cl in CB:
            row([r for r in rows if lo<=r["cover"]<hi],cl,out)
        out.append("")
    out.append("[트레일링 폭 비교 · 1시간봉 전체]")
    out.append(f"  {'트레일':14s} {'표본':>4s}  {'승률':>16s}  {'PF':>5s}  {'평균':>7s}  "
               f"{'갭필률':>6s}  {'갭필후':>6s}  {'MAE':>6s}  {'다음날 상승':>12s}  {'다음날 하락':>12s}")
    for t in (0.15,0.25,0.35,0.50,0.75):
        rows=build("1h","2y",vm,t)
        row(rows,f"트레일 {t}%",out)
    return out

if __name__=="__main__":
    try: r=main()
    except Exception: r=["실패:\n"+traceback.format_exc()]
    txt="\n".join(r); print(txt)
    json.dump({"at":dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),"report":txt},
              open("gapfull_result.json","w"),ensure_ascii=False,indent=1)
