#!/usr/bin/env python3
"""fetch_derivatives.py — 拉取加密衍生品情绪快照（免登录公开 API，零第三方依赖）

用法: python fetch_derivatives.py BTC
输出: 多空比两档/资金费率/恐惧贪婪；全失败退出码 1；5 分钟本地缓存
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.request

API_BASE = os.environ.get("MOZI_API_BASE", "https://moziinnovations.com")
DERIV_BASE = f"{API_BASE}/derivatives"
CACHE_TTL = 300
TIMEOUT = 8


def _fetch(url: str):
    cache = os.path.join(tempfile.gettempdir(), "mozi_skill_cache")
    os.makedirs(cache, exist_ok=True)
    key = os.path.join(cache, hashlib.md5(url.encode()).hexdigest() + ".json")
    if os.path.exists(key) and time.time() - os.path.getmtime(key) < CACHE_TTL:
        with open(key) as f:
            return json.load(f)
    req = urllib.request.Request(url, headers={"User-Agent": "mozi-skill/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode())
    with open(key, "w") as f:
        json.dump(data, f)
    return data


def _get(path: str, base: str = API_BASE) -> dict:
    try:
        data = _fetch(f"{base}{path}")
        if data.get("code") == 0:
            return data.get("data") or {}
    except Exception:
        pass
    return {}


def _longshort(symbol: str, ratio_type: str) -> dict:
    raw = _get(f"/longshort?coin={symbol}&type={ratio_type}", DERIV_BASE)
    out = {}
    for e in (raw or {}).get("list") or []:
        try:
            lng = float(str(e.get("long", "")).replace("%", "")) / 100
            sht = float(str(e.get("short", "")).replace("%", "")) / 100
            out[e["name"]] = {"long": round(lng, 4), "short": round(sht, 4)}
        except (ValueError, TypeError, KeyError):
            continue
    return out


def _fear_greed() -> dict:
    data = _get("/easy/getFearGreedIndex")
    if isinstance(data, dict) and data.get("value") is not None:
        return {"value": data.get("value"), "classification": data.get("category")}
    try:
        alt = _fetch("https://api.alternative.me/fng/?limit=1")
        entry = (alt.get("data") or [{}])[0]
        return {"value": entry.get("value"), "classification": entry.get("value_classification")}
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", help="coin symbol, e.g. BTC")
    args = ap.parse_args()
    s = args.symbol.strip().upper()

    global_ls = _longshort(s, "global_account_ratio")
    top_ls = _longshort(s, "top_account_ratio")
    funding = _get(f"/foundrate/forllm?coin={s}", DERIV_BASE).get("exchanges") or {}
    fg = _fear_greed()

    if not global_ls and not top_ls and not funding:
        print(f"ERROR: no data for {s}. Check the symbol (e.g. BTC, ETH, SOL).")
        sys.exit(1)

    print(f"# {s} 衍生品情绪快照 / {s} derivatives sentiment")
    print(f"fetched_at: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n")

    print("## 多空比(账户占比) / Long-short account ratios")
    print(f"- global_accounts(全体用户): {json.dumps(global_ls, ensure_ascii=False)}")
    print(f"- top_accounts(大户):       {json.dumps(top_ls, ensure_ascii=False)}\n")

    print("## 资金费率(最新) / Funding rates (latest)")
    for exchange, val in funding.items():
        print(f"- {exchange}: {val}")
    if not funding:
        print("- (unavailable)")
    print()

    print("## 恐惧贪婪指数 / Fear & Greed")
    print(f"- value: {fg.get('value')}  classification: {fg.get('classification')}")


if __name__ == "__main__":
    main()
