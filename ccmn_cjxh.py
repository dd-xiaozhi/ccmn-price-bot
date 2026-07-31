#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
长江有色网 (https://www.ccmn.cn/) 「长江现货」行情抓取 + Excel 导出。

数据来源：
    首页价格面板由 JS 异步请求 /shop/historyData/getCjysAndCjysw 获取，
    响应体中 showMarketList / showPriceList 为 AES-128-ECB(PKCS7) 密文，
    密钥硬编码在 https://static.ccmn.cn/js/ccmn-index.js。

既可作为库被 run.py 调用，也可独立 CLI 使用：
    python3 ccmn_cjxh.py                      # 导出「长江现货」
    python3 ccmn_cjxh.py --market 长江综合
    python3 ccmn_cjxh.py --all                # 全部市场，每市场一个 sheet
    python3 ccmn_cjxh.py -o out.xlsx
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

API_URL = "https://www.ccmn.cn/shop/historyData/getCjysAndCjysw"
AES_KEY = b"ccmnCjysw1455881"  # 见 ccmn-index.js: var keys = "ccmnCjysw1455881"
DEFAULT_MARKET = "长江现货"
TIMEOUT = 30
RETRIES = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.ccmn.cn/",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


def _num(v):
    """价格字段可能是 str 或 float，统一转数值；转不了（如升贴水 b340）原样返回。"""
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return v


def _url(path):
    if not path:
        return ""
    if path.startswith("//"):
        return "https:" + path
    if path.startswith("http"):
        return path
    return "https://www.ccmn.cn" + path


# (Excel 列名, 取值函数)
COLUMNS = [
    ("品名", lambda r: r.get("productSortName") or "-"),
    ("最低价", lambda r: _num(r.get("minPrice"))),
    ("最高价", lambda r: _num(r.get("maxPrice"))),
    ("价格区间", lambda r: f"{r.get('minPrice', '0')}-{r.get('maxPrice', '0')}"),
    ("均价", lambda r: _num(r.get("avgPrice"))),
    ("涨跌", lambda r: _num(r.get("highsLowsAmount"))),
    ("单位", lambda r: r.get("unit") or "-"),
    ("牌号", lambda r: r.get("trademark") or "-"),
    ("产地", lambda r: r.get("placeortrademark") or "-"),
    ("备注", lambda r: r.get("remark") or "-"),
    ("日期", lambda r: r.get("publishDate") or "-"),
    ("发布时间", lambda r: r.get("publishDateTime") or "-"),
    ("市场", lambda r: r.get("marketName") or "-"),
    ("数据来源", lambda r: r.get("stResource") or "-"),
    ("走势链接", lambda r: _url(r.get("htmlpath"))),
]
COL_WIDTHS = [22, 11, 11, 20, 11, 9, 10, 12, 10, 12, 12, 20, 12, 16, 60]


# ── 抓取 ────────────────────────────────────────────────────────────────


def decrypt_aes(b64_text: str) -> str:
    """AES-128-ECB + PKCS7，与站点 CryptoJS 实现一致。"""
    ct = base64.b64decode(b64_text)
    decryptor = Cipher(algorithms.AES(AES_KEY), modes.ECB()).decryptor()
    plain = decryptor.update(ct) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(plain) + unpadder.finalize()).decode("utf-8")


def fetch(timeout: int = TIMEOUT, retries: int = RETRIES) -> tuple[list, list]:
    """返回 (市场列表, 价格分组列表)，两者按下标一一对应。"""
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(API_URL, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success") or not data.get("body"):
                raise RuntimeError(f"接口返回失败: {data.get('msg')}")
            body = data["body"]
            markets = json.loads(decrypt_aes(body["showMarketList"]))
            prices = json.loads(decrypt_aes(body["showPriceList"]))
            if len(markets) != len(prices):
                print(
                    f"[warn] 市场数({len(markets)}) 与价格分组数({len(prices)}) "
                    f"不一致，按下标对齐",
                    file=sys.stderr,
                )
            return markets, prices
        except Exception as e:  # noqa: BLE001 — 重试后仍失败会原样抛出
            last_err = e
            if attempt < retries:
                print(f"[warn] 第 {attempt}/{retries} 次请求失败: {e}，重试中", file=sys.stderr)
    raise RuntimeError(f"抓取失败（已重试 {retries} 次）: {last_err}") from last_err


def market_names(markets: list) -> list[str]:
    return [m.get("marketName", f"市场{i}") for i, m in enumerate(markets)]


def collect(market: str | None = None, all_markets: bool = False) -> dict[str, list]:
    """抓取并按 {市场名: 行列表} 返回。"""
    markets, prices = fetch()
    names = market_names(markets)
    if all_markets:
        idxs = list(range(min(len(markets), len(prices))))
    else:
        target = market or DEFAULT_MARKET
        if target not in names:
            raise ValueError(f"未找到市场「{target}」，可选: {', '.join(names)}")
        idxs = [names.index(target)]
    return {names[i]: (prices[i] or []) for i in idxs}


def fingerprint(sheets: dict[str, list]) -> str:
    """对行情内容做稳定指纹，用于「数据未变则跳过推送」。

    只取业务字段（品名/价格/涨跌/发布时间），忽略 media_id 之类的易变项。
    """
    keys = (
        "productSortName",
        "minPrice",
        "maxPrice",
        "avgPrice",
        "highsLowsAmount",
        "publishDateTime",
    )
    payload = {
        name: [[str(r.get(k, "")) for k in keys] for r in rows]
        for name, rows in sorted(sheets.items())
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def data_date(sheets: dict[str, list]) -> str:
    """取数据中出现的最新 publishDate。"""
    dates = {
        r.get("publishDate") for rows in sheets.values() for r in rows if r.get("publishDate")
    }
    return max(dates) if dates else datetime.now().strftime("%Y-%m-%d")


# ── 导出 ────────────────────────────────────────────────────────────────


def _write_sheet(ws, rows: list) -> None:
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="4F6F8F")
    up_font = Font(color="C00000")  # 涨：红
    down_font = Font(color="008000")  # 跌：绿

    ws.append([c[0] for c in COLUMNS])
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for r in rows:
        ws.append([fn(r) for _, fn in COLUMNS])
        chg = _num(r.get("highsLowsAmount")) or 0
        cell = ws.cell(row=ws.max_row, column=6)
        if isinstance(chg, (int, float)):
            if chg > 0:
                cell.font = up_font
            elif chg < 0:
                cell.font = down_font

    for i, w in enumerate(COL_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{ws.max_row}"


def export_excel(sheets: dict[str, list], path: str | Path) -> Path:
    """写出 xlsx，每个市场一个 sheet。返回实际路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        _write_sheet(wb.create_sheet(title=name[:31]), rows)
    wb.save(path)
    return path


# ── CLI ─────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取长江有色网「长江现货」行情到 Excel")
    ap.add_argument("--market", default=DEFAULT_MARKET, help=f"市场名，默认 {DEFAULT_MARKET}")
    ap.add_argument("--all", action="store_true", help="导出全部市场，每市场一个 sheet")
    ap.add_argument("-o", "--output", help="输出 xlsx 路径")
    args = ap.parse_args()

    print(f"请求 {API_URL} ...")
    try:
        sheets = collect(market=args.market, all_markets=args.all)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1

    for name, rows in sheets.items():
        print(f"  [{name}] {len(rows)} 行")

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    default_name = "ccmn_全部市场" if args.all else f"ccmn_{args.market}"
    out = args.output or f"{default_name}_{stamp}.xlsx"
    path = export_excel(sheets, out)

    print(f"\n✅ 已导出 {sum(len(v) for v in sheets.values())} 行 → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
