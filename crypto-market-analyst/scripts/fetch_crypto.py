#!/usr/bin/env python3
"""fetch_crypto.py — 拉取加密货币行情+衍生品情绪快照（免登录公开 API，零第三方依赖）

用法: python fetch_crypto.py BTC
输出: 自包含数据报告（实时行情/日线摘要/区间涨跌/多空比两档/资金费率/恐惧贪婪）
5 分钟本地缓存；行情类全失败时退出码 1
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


def _longshort(symbol: str, ratio_type: str) -> dict:
    """各所多空比。data.list = [{name, long: "47%", short: "53%"}] → {交易所: {long,short}} 小数"""
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


def _funding(symbol: str) -> dict:
    """资金费率。data = {coin, metric, timestamp, exchanges: {交易所: "0.0052%"}}"""
    raw = _get(f"/foundrate/forllm?coin={symbol}", DERIV_BASE)
    return raw.get("exchanges") or {} if isinstance(raw, dict) else {}


def _fear_greed() -> dict:
    data = _get("/easy/getFearGreedIndex")
    if isinstance(data, dict) and data.get("value") is not None:
        return {"value": data.get("value"), "classification": data.get("category"),
                "source": "backend"}
    try:
        alt = _fetch("https://api.alternative.me/fng/?limit=1")
        entry = (alt.get("data") or [{}])[0]
        return {"value": entry.get("value"), "classification": entry.get("value_classification"),
                "source": "alternative.me"}
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", help="coin symbol, e.g. BTC")
    args = ap.parse_args()
    s = args.symbol.strip().upper()

    # 1. 实时行情（脏数据保护：|24h涨跌幅|>200% 丢弃）
    header = _get(f"/detail/header?symbol={s}") or {}
    price = header.get("currentPrice")
    chg24 = _f(header.get("priceChangePercentage_24h"))
    if chg24 is not None and abs(chg24) > 200:
        chg24 = None

    # 2. 日线（type=2）
    daily = _get(f"/detail/kline?symbol={s}&type=2") or {}
    values = daily.get("values") or []
    closes = []
    for v in values[-30:]:
        try:
            closes.append(float(v[1]))  # [open, close, low, high]
        except (TypeError, ValueError, IndexError):
            pass

    # 3. 区间涨跌（带 % 字符串）
    roi_raw = _get(f"/easy/getReturnInvestment?symbol={s}")
    roi = {}
    if isinstance(roi_raw, list) and roi_raw:
        roi = {k: v for k, v in roi_raw[0].items() if k != "symbol"}

    if not header and not closes and not roi:
        print(f"ERROR: no data for {s}. Check the symbol (e.g. BTC, ETH, SOL).")
        sys.exit(1)

    print(f"# {s} 加密市场快照 / {s} crypto snapshot")
    print(f"fetched_at: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n")

    print("## 实时行情 / Realtime")
    print(f"- price: {price}")
    print(f"- change_24h_pct: {chg24}")
    print(f"- high_24h: {header.get('high_24h')}")
    print(f"- low_24h: {header.get('low_24h')}")
    print(f"- volume_24h: {header.get('totalVolume') or header.get('volume')}")
    print(f"- market_cap: {header.get('marketCap')}\n")

    print("## 日线摘要(近30天) / Daily kline (30d)")
    if closes:
        first, last = closes[0], closes[-1]
        print(f"- latest_close: {last}")
        print(f"- range_30d_pct: {round((last - first) / first * 100, 2) if first else None}")
        print(f"- range_high: {max(closes)}")
        print(f"- range_low: {min(closes)}")
    else:
        print("- (no daily kline)")
    print()

    print("## 区间涨跌(独立数据源) / Returns 1D/7D/1M/1Y")
    if roi:
        for k, v in roi.items():
            print(f"- {k}: {v}")
    else:
        print("- (unavailable)")
    print()

    print("## 多空比(账户比,最新) / Long-short account ratios")
    print(f"- global_accounts: {json.dumps(_longshort(s, 'global_account_ratio'), ensure_ascii=False)}")
    print(f"- top_accounts:    {json.dumps(_longshort(s, 'top_account_ratio'), ensure_ascii=False)}\n")

    print("## 资金费率(最新) / Funding rates (latest)")
    fr = _funding(s)
    if fr:
        for exchange, val in fr.items():
            print(f"- {exchange}: {val}")
    else:
        print("- (unavailable)")
    print()

    print("## 恐惧贪婪指数 / Fear & Greed")
    fg = _fear_greed()
    print(f"- value: {fg.get('value')}  classification: {fg.get('classification')}  source: {fg.get('source')}")


if __name__ == "__main__":
    main()
