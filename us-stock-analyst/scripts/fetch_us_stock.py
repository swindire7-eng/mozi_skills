#!/usr/bin/env python3
"""fetch_us_stock.py — 拉取美股快照数据（免登录公开 API，零第三方依赖）

用法: python fetch_us_stock.py AAPL
输出: 自包含数据报告（实时报价/公司档案/日线摘要/日内小时线/区间涨跌/交易时段）
所有请求带 5 分钟本地缓存（缓存目录系统 temp），全失败时退出码 1
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
CACHE_TTL = 300
TIMEOUT = 8


def _fetch(url: str) -> dict:
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


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _get(path: str) -> dict:
    try:
        data = _fetch(f"{API_BASE}{path}")
        if data.get("code") == 0:
            return data.get("data") or {}
    except Exception:
        pass
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker", help="US ticker, e.g. AAPL")
    args = ap.parse_args()
    t = args.ticker.strip().upper()

    # 1. 实时报价（含脏数据保护：|涨跌幅|>50% 视为后端脏值丢弃）
    quote = _get(f"/stock/detail/header?symbol={t}") or {}
    last = _f(quote.get("lastPrice"))
    pct = _f(quote.get("priceChangePercent"))
    if pct is not None and abs(pct) > 50:
        pct = None

    # 2. 日线 30 根（type=2）
    daily = _get(f"/stock/detail/kline?symbol={t}&type=2") or {}
    rows = daily.get("list") or []
    closes = [c for c in (_f(r.get("closePrice")) for r in rows) if c is not None]

    # 3. 区间涨跌（1日/7日/1月/1年，独立数据源，裸数字）
    roi = _get(f"/stock/detail/getReturnInvestment?symbol={t}") or {}

    # 4. 交易时段
    session = _get(f"/stock/search/session?symbol={t}") or {}

    if not quote and not closes and not roi:
        print(f"ERROR: no data for {t}. Check the ticker (e.g. AAPL, TSLA, NVDA).")
        sys.exit(1)

    print(f"# {t} 美股数据快照 / {t} US stock snapshot")
    print(f"fetched_at: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n")

    print("## 实时报价 / Realtime quote")
    if quote:
        print(f"- last_price: {last}")
        print(f"- change_pct: {pct}")
        print(f"- day_high: {_f(quote.get('highPrice'))}")
        print(f"- day_low: {_f(quote.get('lowPrice'))}")
        print(f"- volume: {_f(quote.get('volume'))}")
        print(f"- turnover: {_f(quote.get('quoteVolume'))}")
        print(f"- ts: {quote.get('ts')}")
    else:
        print("- (quote unavailable)")
    print()

    print("## 公司档案 / Profile")
    profile_keys = ("nameCn", "name", "sectorCn", "sector", "industry",
                    "marketCap", "week52High", "week52Low", "beta",
                    "averageVolume", "peRatio", "ipoDate")
    for k in profile_keys:
        v = quote.get(k)
        if v not in (None, ""):
            print(f"- {k}: {v}")
    desc = quote.get("descriptionCn") or quote.get("description") or ""
    if desc:
        print(f"- description: {str(desc)[:120]}")
    print()

    print("## 日线摘要(近30天) / Daily kline (30d)")
    if closes:
        first, lastc = closes[0], closes[-1]
        print(f"- latest_close: {lastc}")
        print(f"- range_change_pct: {round((lastc - first) / first * 100, 2) if first else None}")
        print(f"- range_high: {max(closes)}")
        print(f"- range_low: {min(closes)}")
        print(f"- last10_closes: {[{'date': r.get('dt'), 'close': _f(r.get('closePrice'))} for r in rows[-10:]]}")
    else:
        print("- (no daily kline)")
    print()

    print("## 区间涨跌(独立数据源) / Returns (1D/7D/1M/1Y, unit: %)")
    if roi:
        for k in ("priceChange1Day", "priceChange7Day", "priceChange1Month", "priceChange1Year"):
            if roi.get(k) is not None:
                print(f"- {k}: {roi[k]}")
    else:
        print("- (unavailable)")
    print()

    print("## 交易时段 / Session")
    if session:
        print(json.dumps(session, ensure_ascii=False)[:400])
    else:
        print("- (unavailable)")


if __name__ == "__main__":
    main()
