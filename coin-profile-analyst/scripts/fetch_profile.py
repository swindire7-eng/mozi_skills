#!/usr/bin/env python3
"""fetch_profile.py — 代币项目档案快照（项目介绍/代币经济学/团队/官方链接）

用法: python fetch_profile.py OGN
输出: 自包含数据报告（项目名片/介绍/代币经济学含流通率与ATH-ATL/基本信息/团队/链接）
退出码: 0=正常  1=无数据(符号无效或该币无档案)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", help="coin symbol, e.g. OGN")
    args = ap.parse_args()
    s = args.symbol.strip().upper()

    p = _get(f"/coin/profile?symbol={s}")
    if not p.get("symbol") and not p.get("name") and not (p.get("description") or {}).get("zh") \
            and not (p.get("description") or {}).get("en"):
        print(f"ERROR: no profile data for {s}. Check the symbol (e.g. BTC, ETH, OGN).")
        sys.exit(1)

    print(f"# {s} 项目档案 / {s} coin profile")
    print(f"fetched_at: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n")

    # 1. 项目名片
    print("## 项目名片 / Identity")
    print(f"- symbol: {p.get('symbol')}  name: {p.get('name')}")
    print(f"- 市值排名: {p.get('mktCapRank')}")
    cats = [c for c in (p.get("categories") or []) if c]
    print(f"- 分类: {', '.join(cats) if cats else '(none)'}\n")

    # 2. 项目介绍（zh 优先，缺失用 en）
    print("## 项目介绍 / Description")
    desc = (p.get("description") or {}).get("zh") or (p.get("description") or {}).get("en")
    print(desc or "- (no description)")
    print()

    # 3. 代币经济学 + 当前价定位
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

    # 4. 基本信息（多数山寨币为空，有值才输出）
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

    # 5. 团队
    team = [t for t in (p.get("team") or []) if isinstance(t, dict) and t.get("name")]
    print("## 团队 / Team")
    if team:
        for t in team[:10]:
            print(f"- {t.get('name')} — {t.get('position') or '(职位未提供)'}")
    else:
        print("- (数据源未提供)")
    print()

    # 6. 官方链接（只列有值的）
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


def _num(v):
    if v is None:
        return "(unavailable)"
    if v >= 1_000_000_000:
        return f"{round(v / 1_000_000_000, 2)}B"
    if v >= 1_000_000:
        return f"{round(v / 1_000_000, 2)}M"
    return f"{round(v, 2):,}" if v >= 1 else str(v)


if __name__ == "__main__":
    main()
