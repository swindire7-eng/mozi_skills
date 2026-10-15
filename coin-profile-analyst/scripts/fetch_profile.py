#!/usr/bin/env python3
"""fetch_profile.py — 资产档案快照：代币项目档案 / 美股公司档案

用法: python fetch_profile.py OGN     （代币）
      python fetch_profile.py NVDA    （美股，自动识别：先查美股接口，无数据再查代币档案）
输出: 自包含数据报告
      代币: 项目名片/介绍/代币经济学(流通率+ATH-ATL)/基本信息/团队/官方链接
      美股: 公司名片/公司介绍/交易与估值(市值/52周区间/Beta/股息)
退出码: 0=正常  1=无数据(符号无效)
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
CACHE_TTL = 300
TIMEOUT = 8


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
            req = urllib.request.Request(url, headers={"User-Agent": "mozi-skill/1.0"})
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


def _get(path: str) -> dict:
    try:
        data = _fetch(f"{API_BASE}{path}")
        if data.get("code") == 0:
            return data.get("data") or {}
    except Exception:
        pass
    return {}


def _fmt_date(s):
    return str(s or "")[:10]


def _f(v):
    try:
        return float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _num(v):
    if v is None:
        return "(unavailable)"
    if v >= 1_000_000_000_000:
        return f"{round(v / 1_000_000_000_000, 2)}T"
    if v >= 1_000_000_000:
        return f"{round(v / 1_000_000_000, 2)}B"
    if v >= 1_000_000:
        return f"{round(v / 1_000_000, 2)}M"
    return f"{round(v, 2):,}" if v >= 1 else str(v)


def _coin_ok(p: dict) -> bool:
    desc = p.get("description") or {}
    return bool(p.get("symbol") or p.get("name") or desc.get("zh") or desc.get("en"))


def coin_report(s: str, p: dict):
    print(f"# {s} 项目档案 / {s} coin profile")
    print(f"fetched_at: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n")

    print("## 项目名片 / Identity")
    print(f"- symbol: {p.get('symbol')}  name: {p.get('name')}")
    print(f"- 市值排名: {p.get('mktCapRank')}")
    cats = [c for c in (p.get("categories") or []) if c]
    print(f"- 分类: {', '.join(cats) if cats else '(none)'}\n")

    print("## 项目介绍 / Description")
    desc = (p.get("description") or {}).get("zh") or (p.get("description") or {}).get("en")
    print(desc or "- (no description)")
    print()

    tok = p.get("tokenomics") or {}
    print("## 代币经济学 / Tokenomics")
    total = _f(tok.get("totalSupply"))
    maxs = _f(tok.get("maxSupply"))
    circ = _f(tok.get("circulatingSupply"))
    ath = _f(tok.get("ath"))
    atl = _f(tok.get("atl"))
    print(f"- 总量: {_num(total)}  最大量: {_num(maxs) if maxs else '(无上限/未提供)'}  流通量: {_num(circ)}")
    if total and circ:
        print(f"- 流通率: {round(circ / total * 100, 1)}%")
    if ath:
        print(f"- 历史高点 ATH: {ath} ({_fmt_date(tok.get('athDate'))})")
    if atl:
        print(f"- 历史低点 ATL: {atl} ({_fmt_date(tok.get('atlDate'))})")

    header = _get(f"/detail/header?symbol={s}")
    price = _f(header.get("currentPrice")) if isinstance(header, dict) else None
    if price:
        print(f"- 当前价: {price}")
        if ath:
            print(f"- 距 ATH: {round((price - ath) / ath * 100, 1)}%")
        if atl:
            print(f"- 距 ATL: +{round((price - atl) / atl * 100, 1)}%")
    print()

    basics = p.get("basics") or {}
    filled = {k: v for k, v in basics.items() if v}
    labels = {"genesisDate": "创世日期", "platform": "发行平台", "orgStructure": "组织结构",
              "proofType": "共识机制", "hashAlgorithm": "哈希算法", "devStatus": "开发状态"}
    print("## 基本信息 / Basics")
    if filled:
        for k, v in filled.items():
            print(f"- {labels.get(k, k)}: {v}")
    else:
        print("- (数据源未提供)")
    print()

    team = [t for t in (p.get("team") or []) if isinstance(t, dict) and t.get("name")]
    print("## 团队 / Team")
    if team:
        for t in team[:10]:
            print(f"- {t.get('name')} — {t.get('position') or '(职位未提供)'}")
    else:
        print("- (数据源未提供)")
    print()

    links = p.get("links") or {}
    link_labels = {"website": "官网", "twitter": "Twitter/X", "telegram": "Telegram",
                   "github": "GitHub", "reddit": "Reddit", "discord": "Discord",
                   "forum": "论坛", "explorer": "区块浏览器", "whitepaper": "白皮书"}
    print("## 官方链接 / Official links")
    shown = [(link_labels.get(k, k), v) for k, v in links.items() if v]
    if shown:
        for label, v in shown:
            print(f"- {label}: {v}")
    else:
        print("- (数据源未提供)")


def stock_report(s: str, st: dict):
    print(f"# {s} 公司档案 / {s} stock profile")
    print(f"fetched_at: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n")

    print("## 公司名片 / Identity")
    name_cn = f"（{st['nameCn']}）" if st.get("nameCn") else ""
    print(f"- {st.get('symbol') or s} · {st.get('name')}{name_cn}")
    market = st.get("listingMarket") or ""
    if st.get("listingMarketCn"):
        market = f"{st['listingMarketCn']}" + (f" ({market})" if market else "")
    print(f"- 上市市场: {market or '(未提供)'} · 国家: {st.get('country') or '(未提供)'}")
    sector = st.get("sectorCn") or st.get("sector") or "(未提供)"
    if st.get("industry"):
        sector += f" · {st['industry']}"
    print(f"- 板块/行业: {sector}")
    emp = st.get("fullTimeEmployees")
    emp_s = f"{int(emp):,}" if emp else "(未提供)"
    print(f"- CEO: {st.get('ceo') or '(未提供)'} · 员工: {emp_s} · IPO: {_fmt_date(st.get('ipoDate')) or '(未提供)'}")
    if st.get("website"):
        print(f"- 官网: {st['website']}")
    print()

    print("## 公司介绍 / Description")
    desc = st.get("descriptionCn") or st.get("description")
    print(desc or "- (no description)")
    print()

    print("## 交易与估值 / Trading & valuation")
    price = _f(st.get("lastPrice"))
    chg = _f(st.get("priceChangePercent"))
    cur = st.get("currency") or "USD"
    if price:
        line = f"- 最新价: {price} {cur}"
        if chg is not None:
            line += f" · 24h: {chg}%"
        print(line)
    mcap = _f(st.get("marketCap"))
    if mcap:
        print(f"- 市值: ${_num(mcap)}")
    rng = _f_range(st.get("priceRange52w"))
    if rng and price:
        lo, hi = rng
        pos = round((price - lo) / (hi - lo) * 100, 1) if hi > lo else None
        print(f"- 52周区间: {lo} - {hi} · 当前位于 {pos}% 位")
    beta = st.get("beta")
    div = st.get("lastDividend")
    avg_vol = _f(st.get("averageVolume"))
    extras = []
    if beta:
        extras.append(f"Beta {beta}")
    if div:
        extras.append(f"最近股息 {div}")
    if avg_vol:
        extras.append(f"平均成交量 {_num(avg_vol)}")
    if extras:
        print(f"- {' · '.join(extras)}")
    if st.get("maxExchange") and st.get("minExchange"):
        print(f"- 跨所报价: {st.get('maxExchange')} {st.get('maxPrice')}"
              f" ~ {st.get('minExchange')} {st.get('minPrice')}"
              f"（价差 {round(_f(st.get('spreadPct')) or 0, 2)}%）")


def _f_range(v):
    """'164.27-243.37' → (164.27, 243.37)"""
    try:
        lo, hi = str(v).split("-")
        return float(lo), float(hi)
    except (TypeError, ValueError, AttributeError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", help="coin or stock symbol, e.g. OGN / NVDA")
    args = ap.parse_args()
    s = args.symbol.strip().upper()

    # 美股优先：NVDA 等代币化股票在币档案库也存在（Robinhood Token），先查美股接口避免误路由
    st = _get(f"/stock/detail/header?symbol={s}")
    if st.get("symbol") or st.get("name"):
        stock_report(s, st)
        return

    p = _get(f"/coin/profile?symbol={s}")
    if _coin_ok(p):
        coin_report(s, p)
        return

    print(f"ERROR: no profile data for {s}. Check the symbol (e.g. BTC, OGN, NVDA, AAPL).")
    sys.exit(1)


if __name__ == "__main__":
    main()
