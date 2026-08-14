import yfinance as yf, pandas as pd
for iv, per in (("5m","60d"),("15m","60d"),("15m","1y"),("15m","2y"),
                ("30m","1y"),("30m","2y"),("1h","2y"),("1h","5y")):
    try:
        d = yf.download("SPY", period=per, interval=iv, prepost=True,
                        auto_adjust=False, progress=False)
        if isinstance(d.columns, pd.MultiIndex): d.columns = d.columns.get_level_values(0)
        d = d.dropna()
        if len(d)==0: print(f"  {iv:4s} {per:4s} 빈 데이터"); continue
        d.index = d.index.tz_convert("America/New_York")
        days = sorted(set(d.index.date))
        pm = d[(d.index.time>=pd.Timestamp('04:00').time())&(d.index.time<pd.Timestamp('09:30').time())]
        pmd = sorted(set(pm.index.date))
        print(f"  {iv:4s} {per:4s} 봉 {len(d):6d} 거래일 {len(days):4d} "
              f"({days[0]}~{days[-1]}) 프리마켓있는날 {len(pmd):4d} 프리마켓봉/일 {len(pm)/max(len(pmd),1):.1f}")
    except Exception as e:
        print(f"  {iv:4s} {per:4s} 실패 {type(e).__name__}: {e}")
