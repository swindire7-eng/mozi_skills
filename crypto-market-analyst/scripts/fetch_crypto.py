#!/usr/bin/env python3
"""fetch_crypto.py — 加密货币行情 + 大单主力 + 衍生品情绪快照（免费，每用户 7 次额度）

用法: python fetch_crypto.py BTC
输出: 自包含数据报告（实时行情/日线/区间涨跌/大单主力/多空比两档/资金费率/恐惧贪婪）
      每次报告末尾显示剩余免费额度
退出码: 0=正常  1=无数据(符号无效)  4=免费额度已用完（已打印引流卡片）
额度: 本地计数（仅成功分析计 1 次，符号无效不扣），7 次后引导官网/TG
5 分钟本地缓存（成功响应才缓存）
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
BIGORDER_BASE = os.environ.get("MOZI_BIGORDER_BASE", "https://askmozi.com/bigorder/v1")
CACHE_TTL = 300
TIMEOUT = 8

FREE_QUOTA = 7
USAGE_FILE = os.path.join(tempfile.gettempdir(), "mozi_skill_usage.json")

SITE = "https://moziai.xyz"
TG = "@Moziinovations_bot"


def _usage_count() -> int:
    try:
        with open(USAGE_FILE) as f:
            return int(json.load(f).get("count", 0))
    except Exception:
        return 0


def _usage_add() -> int:
    n = _usage_count() + 1
    try:
        with open(USAGE_FILE, "w") as f:
            json.dump({"count": n}, f)
    except Exception:
        pass
    return n


def _fetch(url: str):
    cache = os.path.join(tempfile.gettempdir(), "mozi_skill_cache")
    os.makedirs(cache, exist_ok=True)
    key = os.path.join(cache, hashlib.md5(url.encode()).hexdigest() + ".json")
    if os.path.exists(key) and time.time() - os.path.getmtime(key) < CACHE_TTL:
        with open(key) as f:
            return json.load(f)
    data = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mozi-skill/2.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
            break
        except Exception:
            # 后端偶发返回空 body / 瞬断，重试一次
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


def _get_raw(path: str, base: str):
    """直接 GET 返回完整 JSON body（大单端点无 code 包裹）。"""
    try:
        return _fetch(f"{base}{path}")
    except Exception:
        return None


def _bigorder(s: str) -> dict:
    """大单与主力资金（mozi 私有侦测引擎，核心差异化数据）。

    汇总三个端点：异动评分（score.level/total_score + 净流入）、
    30 分钟主力资金流（buy/sell/net/buy_ratio）、Top5 大单明细。
    """
    out = {}
    sig = _get_raw(f"/coin/{s}/signal", BIGORDER_BASE)
    if isinstance(sig, dict) and isinstance(sig.get("score"), dict):
        sc = sig["score"]
        out["anomaly"] = {"total_score": sc.get("total_score"), "level": sc.get("level"),
                          "net_flow": sig.get("net_flow"),
                          "price_change_pct": sig.get("price_change_pct")}
    flow = _get_raw(f"/coin/{s}/flow?window=30", BIGORDER_BASE)
    if isinstance(flow, dict):
        if flow.get("buy_amount") is not None:  # Redis 缓存形态：顶层平铺
            out["flow_30m"] = {k: flow.get(k) for k in
                               ("buy_amount", "sell_amount", "net_flow", "buy_ratio")}
        elif isinstance(flow.get("exchanges"), dict) and flow["exchanges"]:  # 实时计算形态：按所聚合
            buys = sum(e.get("buy_amount") or 0 for e in flow["exchanges"].values())
            sells = sum(e.get("sell_amount") or 0 for e in flow["exchanges"].values())
            if buys or sells:
                out["flow_30m"] = {"buy_amount": round(buys, 2), "sell_amount": round(sells, 2),
                                   "net_flow": round(buys - sells, 2),
                                   "buy_ratio": round(buys / (buys + sells), 4)}
    orders = _get_raw(f"/coin/{s}/orders?top=5", BIGORDER_BASE)
    if isinstance(orders, dict) and orders.get("orders"):
        out["top_orders"] = [{k: o.get(k) for k in ("exchange", "side", "amount", "deal_price", "deal_time")}
                             for o in orders["orders"][:5]]
    return out


def _print_quota_exhausted():
    used = _usage_count()
    print("╭────────────────────────────────────────────────────────")
    print(f"│ 🎁 免费额度已用完（{used}/{FREE_QUOTA} 次）")
    print("├────────────────────────────────────────────────────────")
    print("│ 感谢体验 mozi 加密行情解读！")
    print("│ 继续使用完整功能（实时行情 / 大单主力监控 / 多空比 / 资金费率）：")
    print(f"│   🌐 {SITE}")
    print(f"│   ✈️  Telegram {TG}")
    print("╰────────────────────────────────────────────────────────")
    sys.exit(4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", help="coin symbol, e.g. BTC")
    args = ap.parse_args()
    s = args.symbol.strip().upper()

    if _usage_count() >= FREE_QUOTA:
        _print_quota_exhausted()

    _report(s)

    used = _usage_add()
    remain = FREE_QUOTA - max(used, 1)
    print()
    if remain > 0:
        print(f"🎁 免费额度：剩 {remain}/{FREE_QUOTA} 次 · 用完后续航：{SITE} · TG {TG}")
    else:
        print(f"🎁 免费额度已用完（{FREE_QUOTA}/{FREE_QUOTA}）· 继续使用：{SITE} · TG {TG}")


def _report(s: str):
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

    print("## 大单与主力资金(近30分钟) / Big orders & smart money")
    bo = _bigorder(s)
    if bo:
        anom = bo.get("anomaly")
        if anom:
            print(f"- 异动评分: {anom.get('total_score')} (level={anom.get('level')})")
            print(f"- 异动窗主力净流入: {anom.get('net_flow')}  窗内价格变化: {anom.get('price_change_pct')}%")
        fl = bo.get("flow_30m")
        if fl:
            print(f"- 30min 主力资金: 买 {fl.get('buy_amount')} / 卖 {fl.get('sell_amount')}"
                  f" / 净 {fl.get('net_flow')} (买方占比 {fl.get('buy_ratio')})")
        for i, o in enumerate(bo.get("top_orders") or [], 1):
            when = f" {o.get('deal_time')}" if o.get("deal_time") else ""
            print(f"  top{i}: {o.get('side', '?')} {o.get('amount')} @ {o.get('deal_price')}"
                  f" [{o.get('exchange')}{when}]")
    else:
        print("- (暂无大单数据：该币当前无活跃异动)")
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
