"""persist 단계용: 로컬 _status.json과 origin/main 버전을 무손실 병합.

두 샤드가 동시에 status를 갱신해도 서로의 진전을 잃지 않게 —
키별로 더 '진전된' 항목(ok 우선 > hours_ok 많은 쪽 > tries 큰 쪽)을 채택.
"""
import json
import subprocess

P = "data/1m/_status.json"


def better(a, b):
    if not isinstance(a, dict):
        return b
    if not isinstance(b, dict):
        return a
    if bool(a.get("ok")) != bool(b.get("ok")):
        return a if a.get("ok") else b
    ha, hb = len(a.get("hours_ok", [])), len(b.get("hours_ok", []))
    if ha != hb:
        return a if ha > hb else b
    return a if a.get("tries", 0) >= b.get("tries", 0) else b


def main():
    loc = json.load(open(P))
    try:
        raw = subprocess.run(["git", "show", f"origin/main:{P}"],
                             capture_output=True, text=True, check=True).stdout
        rem = json.loads(raw)
    except Exception:
        rem = {}
    out = dict(rem)
    for k, v in loc.items():
        out[k] = better(v, rem[k]) if k in rem else v
    json.dump(out, open(P, "w"), indent=1)
    ok = sum(1 for v in out.values() if isinstance(v, dict) and v.get("ok"))
    print(f"status merge: local {len(loc)} · remote {len(rem)} → {len(out)} (ok {ok})")


if __name__ == "__main__":
    main()
