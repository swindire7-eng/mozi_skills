#!/usr/bin/env python3
"""fetch_crypto.py — 加密货币行情 + 大单主力 + 衍生品情绪快照（支持支付宝 Machine Pay 按量付费）

用法: python fetch_crypto.py BTC
      python fetch_crypto.py BTC --payment-proof "<Payment-Proof>"   # 402 账单支付后携凭证重试
输出: 自包含数据报告。免费段（实时行情/日线/区间涨跌）始终交付；
      付费段（大单主力/多空比/资金费率/恐惧贪婪）被 402 门禁时标记 🔒 并在末尾出账单。
退出码: 0=正常  1=无数据  2=待支付(账单已打印)  3=支付凭证无效或已使用
5 分钟本地缓存（仅缓存 200 成功响应，402 不缓存 → 支付后重跑即解锁）
"""
import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request

API_BASE = os.environ.get("MOZI_API_BASE", "https://moziinnovations.com")
DERIV_BASE = f"{API_BASE}/derivatives"
BIGORDER_BASE = os.environ.get("MOZI_BIGORDER_BASE", "https://askmozi.com/bigorder/v1")
CACHE_TTL = 300
TIMEOUT = 8

# Payment-Proof 支付凭证（支付宝 Machine Pay 按量付费），main() 从 --payment-proof/环境变量注入
PROOF = None


class PayNeeded(Exception):
    """服务端 402 账单下发（支付宝 Machine Pay）。

    had_proof=True 表示已带凭证仍被 402 → 凭证无效/过期/已履约，需重新支付。
    """

    def __init__(self, bill: dict, had_proof: bool):
        self.bill = bill
        self.had_proof = had_proof
        super().__init__("402 Payment-Needed")


def _decode_bill(raw: str) -> dict:
    """解析 Payment-Needed 响应头：Base64URL 编码的 JSON 账单（自动补 padding）。"""
    try:
        padded = raw + "=" * (-len(raw) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        return {}


def _fetch(url: str):
    cache = os.path.join(tempfile.gettempdir(), "mozi_skill_cache")
    os.makedirs(cache, exist_ok=True)
    key = os.path.join(cache, hashlib.md5(url.encode()).hexdigest() + ".json")
    if os.path.exists(key) and time.time() - os.path.getmtime(key) < CACHE_TTL:
        with open(key) as f:
            return json.load(f)
    headers = {"User-Agent": "mozi-skill/1.1"}
    if PROOF:
        headers["Payment-Proof"] = PROOF  # 携带凭证重试
    data = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            if e.code == 402:
                # 402 账单下发：无凭证=引导支付；带凭证仍 402=服务端验付未通过
                bill = _decode_bill(e.headers.get("Payment-Needed", ""))
                raise PayNeeded(bill, had_proof=bool(PROOF))
            if attempt:
                raise
            time.sleep(1)
        except Exception:
            # 后端偶发返回空 body，重试一次
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
    except PayNeeded:
        raise  # 付费流程异常不能吞，向上冒泡给 main 统一处理
    except Exception:
        pass
    return {}


LOCKED = "🔒 本段为付费内容，支付解锁后重跑可见（账单见末尾）"

_bill = None  # 首个 402 账单：免费段照常交付，付费段锁定并在末尾统一出账单


def _paid(fn, *args, **kwargs):
    """执行数据拉取；402 时记下账单并返回 None（已带凭证仍 402 = 凭证失效，抛给上层）。"""
    global _bill
    try:
        return fn(*args, **kwargs)
    except PayNeeded as pn:
        if pn.had_proof:
            raise
        _bill = _bill or pn
        return None


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
    """直接 GET 返回完整 JSON body（大单端点无 code 包裹）。402 正常冒泡走付费流程。"""
    try:
        return _fetch(f"{base}{path}")
    except PayNeeded:
        raise
    except Exception:
        return None


def _bigorder(s: str) -> dict:
    """大单与主力资金（mozi 私有侦测引擎，付费核心段）。

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


def _print_bill_and_exit(pn: PayNeeded):
    proto = pn.bill.get("protocol") or {}
    method = pn.bill.get("method") or {}
    if pn.had_proof:
        print()
        print("PAYMENT-PROOF-INVALID (exit 3)")
        print("凭证无效、过期或已被使用（服务端验付未通过）。请重新支付获取新凭证；")
        print("同一订单凭证只履约一次，禁止拿失效凭证反复重试。")
        sys.exit(3)
    print()
    print("┌────────────────────────────────────────────────────────")
    print("│ 🔒 付费解锁 · 支付宝按量付费（Machine Pay / 402）")
    print("├────────────────────────────────────────────────────────")
    print(f"│ 商品       {method.get('goods_name')}")
    print(f"│ 金额       {proto.get('amount')} {proto.get('currency')}")
    print(f"│ 商户       {method.get('seller_name')}")
    print(f"│ 订单号     {proto.get('out_trade_no')}")
    print(f"│ 支付截止   {proto.get('pay_before')}")
    print(f"│ 资源       {proto.get('resource_id')}")
    print("├────────────────────────────────────────────────────────")
    print("│ 支付完成后，把平台返回的支付凭证发回来，携凭证重试即可解锁：")
    print('│   python scripts/fetch_crypto.py <SYMBOL> --payment-proof "<Payment-Proof>"')
    print("│ （或设置环境变量 MOZI_PAYMENT_PROOF 后重跑）")
    print("│ · 账单有 pay_before 时效，过期作废、需重新下单，不会莫名扣款")
    print("│ · 订单幂等：同一凭证只履约一次，网络重试不会重复扣费")
    print("└────────────────────────────────────────────────────────")
    sys.exit(2)


def main():
    global PROOF
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", help="coin symbol, e.g. BTC")
    ap.add_argument("--payment-proof", default=os.environ.get("MOZI_PAYMENT_PROOF"),
                    help="支付宝 Machine Pay 支付凭证，402 账单支付后获得，经 Payment-Proof 头携带重试")
    args = ap.parse_args()
    PROOF = args.payment_proof
    s = args.symbol.strip().upper()

    try:
        _report(s)
    except PayNeeded as pn:
        _print_bill_and_exit(pn)


def _report(s: str):
    # ── 免费段：价格结构 ──────────────────────────────────────────
    # 1. 实时行情（脏数据保护：|24h涨跌幅|>200% 丢弃）
    header = _paid(_get, f"/detail/header?symbol={s}") or {}
    price = header.get("currentPrice")
    chg24 = _f(header.get("priceChangePercentage_24h"))
    if chg24 is not None and abs(chg24) > 200:
        chg24 = None

    # 2. 日线（type=2）
    daily = _paid(_get, f"/detail/kline?symbol={s}&type=2") or {}
    values = daily.get("values") or []
    closes = []
    for v in values[-30:]:
        try:
            closes.append(float(v[1]))  # [open, close, low, high]
        except (TypeError, ValueError, IndexError):
            pass

    # 3. 区间涨跌（带 % 字符串）
    roi_raw = _paid(_get, f"/easy/getReturnInvestment?symbol={s}")
    roi = {}
    if isinstance(roi_raw, list) and roi_raw:
        roi = {k: v for k, v in roi_raw[0].items() if k != "symbol"}

    if not header and not closes and not roi:
        if _bill:
            _print_bill_and_exit(_bill)  # 全付费部署：没有可交付的免费段，直接出账单
        print(f"ERROR: no data for {s}. Check the symbol (e.g. BTC, ETH, SOL).")
        sys.exit(1)

    print(f"# {s} 加密市场快照 / {s} crypto snapshot")
    print(f"fetched_at: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n")

    print("## 实时行情(免费段) / Realtime (free)")
    if header:
        print(f"- price: {price}")
        print(f"- change_24h_pct: {chg24}")
        print(f"- high_24h: {header.get('high_24h')}")
        print(f"- low_24h: {header.get('low_24h')}")
        print(f"- volume_24h: {header.get('totalVolume') or header.get('volume')}")
        print(f"- market_cap: {header.get('marketCap')}")
    else:
        print(LOCKED)
    print()

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

    # ── 付费段：谁在动价格 + 杠杆温度 + 情绪 ──────────────────────
    print("## 大单与主力资金(近30分钟,付费段) / Big orders & smart money (premium)")
    bo = _paid(_bigorder, s)
    if bo is None:
        print(LOCKED)
    elif bo:
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

    print("## 多空比(账户比,最新,付费段) / Long-short account ratios (premium)")
    ls_g = _paid(_longshort, s, "global_account_ratio")
    ls_t = _paid(_longshort, s, "top_account_ratio")
    if ls_g is None or ls_t is None:
        print(LOCKED)
    else:
        print(f"- global_accounts: {json.dumps(ls_g, ensure_ascii=False)}")
        print(f"- top_accounts:    {json.dumps(ls_t, ensure_ascii=False)}")
    print()

    print("## 资金费率(最新,付费段) / Funding rates (premium)")
    fr = _paid(_funding, s)
    if fr is None:
        print(LOCKED)
    elif fr:
        for exchange, val in fr.items():
            print(f"- {exchange}: {val}")
    else:
        print("- (unavailable)")
    print()

    print("## 恐惧贪婪指数(付费段) / Fear & Greed (premium)")
    fg = _paid(_fear_greed)
    if fg is None:
        print(LOCKED)
    else:
        print(f"- value: {fg.get('value')}  classification: {fg.get('classification')}  source: {fg.get('source')}")

    if _bill:
        print()
        _print_bill_and_exit(_bill)


if __name__ == "__main__":
    main()
