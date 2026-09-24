#!/usr/bin/env python3
"""fetch_brief.py — 拉取每日快报原始数据（免登录公开 API，零第三方依赖）

用法: python fetch_brief.py
输出: BTC/ETH 行情 + 恐惧贪婪 + BTC 多空比两档 + 资金费率（供 SKILL.md 排版成群发日报）
5 分钟本地缓存；行情类全失败时退出码 1
"""
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
    # 后端偶发返回空 body，重试一次
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mozi-skill/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
            break
        except Exception:
            if attempt:
                raise
            time.sleep(1)
    with open(key, "w") as f:
        json.dump(data, f)
    return data


def _f(v):
    try:
        return float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _get(path: str, base: str = API_BASE) -> dict:
    try:
        data = _fetch(f"{base}{path}")
        if data.get("code") == 0:
            return data.get("data") or {}
    except Exception:
        pass
    return {}


def _header(symbol: str) -> dict:
    h = _get(f"/detail/header?symbol={symbol}") or {}
    chg = _f(h.get("priceChangePercentage_24h"))
    if chg is not None and abs(chg) > 200:
        chg = None
    return {"price": h.get("currentPrice"), "chg_24h_pct": chg}


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


def _avg_long(ls: dict):
    if not ls:
        return None
    vals = [v["long"] for v in ls.values()]
    return round(sum(vals) / len(vals), 4)


def _avg_funding(funding: dict):
    vals = []
    for v in funding.values():
        try:
            vals.append(float(str(v).replace("%", "")))
        except (TypeError, ValueError):
            continue
    return round(sum(vals) / len(vals), 4) if vals else None


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
    btc, eth = _header("BTC"), _header("ETH")
    global_ls = _longshort("BTC", "global_account_ratio")
    top_ls = _longshort("BTC", "top_account_ratio")
    funding = _get("/foundrate/forllm?coin=BTC", DERIV_BASE).get("exchanges") or {}
    fg = _fear_greed()

    if not btc and not eth and not global_ls and not funding:
        print("ERROR: no data available. Try again later.")
        sys.exit(1)

    print(f"# 每日快报原始数据 / daily brief raw data")
    print(f"fetched_at: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n")

    print("## 行情 / Prices")
    print(f"- BTC: price={btc.get('price')}  chg_24h_pct={btc.get('chg_24h_pct')}")
    print(f"- ETH: price={eth.get('price')}  chg_24h_pct={eth.get('chg_24h_pct')}\n")

    print("## 恐惧贪婪 / Fear & Greed")
    print(f"- value: {fg.get('value')}  classification: {fg.get('classification')}\n")

    print("## BTC 多空比(账户占比) / BTC long-short ratios")
    print(f"- global_avg_long: {_avg_long(global_ls)}")
    print(f"- top_avg_long: {_avg_long(top_ls)}")
    print(f"- global_by_exchange: {json.dumps(global_ls, ensure_ascii=False)}")
    print(f"- top_by_exchange: {json.dumps(top_ls, ensure_ascii=False)}\n")

    print("## BTC 资金费率 / BTC funding rates")
    print(f"- avg_pct: {_avg_funding(funding)}")
    for exchange, val in funding.items():
        print(f"- {exchange}: {val}")


if __name__ == "__main__":
    main()
