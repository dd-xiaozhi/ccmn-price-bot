#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企业微信群机器人推送：markdown 摘要 + 文件上传。

官方文档：https://developer.work.weixin.qq.com/document/path/91770

要点：
  * 发文件必须两步：先 upload_media 换 media_id，再用 media_id 发 file 消息
  * media_id 有效期 3 天；文件 5B ~ 20MB
  * markdown content 上限 4096 字节，且不支持表格
  * 单机器人 20 条/分钟
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

log = logging.getLogger("ccmn.wecom")

UPLOAD_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media"
TIMEOUT = 60
RETRIES = 3
MAX_FILE_BYTES = 20 * 1024 * 1024
MIN_FILE_BYTES = 5
MAX_MD_BYTES = 4096


class WeComError(RuntimeError):
    pass


def _key_of(webhook: str) -> str:
    key = parse_qs(urlparse(webhook).query).get("key", [""])[0]
    if not key:
        raise WeComError("webhook URL 中缺少 key 参数")
    return key


def _post(url: str, *, retries: int = RETRIES, **kw) -> dict:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, timeout=TIMEOUT, **kw)
            resp.raise_for_status()
            data = resp.json()
            if data.get("errcode") != 0:
                # 业务错误通常重试也无用，直接抛
                raise WeComError(f"企微返回 errcode={data.get('errcode')} {data.get('errmsg')}")
            return data
        except WeComError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                wait = 2**attempt
                log.warning("企微请求失败（%s/%s）: %s，%ss 后重试", attempt, retries, e, wait)
                time.sleep(wait)
    raise WeComError(f"企微请求失败（已重试 {retries} 次）: {last}") from last


def _truncate_md(content: str) -> str:
    b = content.encode("utf-8")
    if len(b) <= MAX_MD_BYTES:
        return content
    return b[: MAX_MD_BYTES - 32].decode("utf-8", errors="ignore") + "\n…（内容过长已截断）"


def send_markdown(webhook: str, content: str) -> None:
    payload = {"msgtype": "markdown", "markdown": {"content": _truncate_md(content)}}
    _post(webhook, json=payload)
    log.info("企微 markdown 摘要已发送（%s 字节）", len(content.encode("utf-8")))


def send_text(webhook: str, content: str, mention_all: bool = False) -> None:
    text: dict = {"content": content}
    if mention_all:
        text["mentioned_list"] = ["@all"]
    _post(webhook, json={"msgtype": "text", "text": text})
    log.info("企微文本已发送")


def upload_file(webhook: str, path: str | Path) -> str:
    """上传文件换 media_id（有效期 3 天）。"""
    path = Path(path)
    if not path.exists():
        raise WeComError(f"待上传文件不存在: {path}")
    size = path.stat().st_size
    if size < MIN_FILE_BYTES:
        raise WeComError(f"文件过小（{size}B < {MIN_FILE_BYTES}B）: {path}")
    if size > MAX_FILE_BYTES:
        raise WeComError(f"文件超过企微 20MB 上限（{size / 1024 / 1024:.1f}MB）: {path}")

    url = f"{UPLOAD_URL}?key={_key_of(webhook)}&type=file"
    with path.open("rb") as f:
        files = {
            "media": (
                path.name,
                f,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        }
        data = _post(url, files=files)
    media_id = data.get("media_id")
    if not media_id:
        raise WeComError(f"上传成功但未返回 media_id: {data}")
    log.info("文件已上传企微: %s (%.1f KB) media_id=%s…", path.name, size / 1024, media_id[:12])
    return media_id


def send_file(webhook: str, path: str | Path) -> None:
    media_id = upload_file(webhook, path)
    _post(webhook, json={"msgtype": "file", "file": {"media_id": media_id}})
    log.info("企微文件消息已发送: %s", Path(path).name)


# ── 摘要生成 ────────────────────────────────────────────────────────────


def _fmt_price(v) -> str:
    if v is None or v == "":
        return "-"
    try:
        f = float(v)
        return f"{int(f)}" if f == int(f) else f"{f:g}"
    except (TypeError, ValueError):
        return str(v)


def build_summary(
    sheets: dict[str, list],
    *,
    top_n: int = 10,
    filename: str | None = None,
    mention_all: bool = False,
) -> str:
    """生成 markdown 摘要：数据日期 + 涨跌幅最大的 N 个品名。

    企微 markdown 不支持表格，用行内 font color 表达涨跌。
    """
    lines: list[str] = []
    total = sum(len(v) for v in sheets.values())
    dates = {
        r.get("publishDate") for rows in sheets.values() for r in rows if r.get("publishDate")
    }
    date_str = max(dates) if dates else "-"
    title = " / ".join(sheets.keys())

    lines.append(f"## 📊 {title}行情（{date_str}）")
    lines.append(f"> 共 **{total}** 个品名 · 数据源：长江有色网")
    lines.append("")

    for market, rows in sheets.items():
        if not rows:
            continue
        if len(sheets) > 1:
            lines.append(f"**【{market}】**")

        def chg(r):
            try:
                return abs(float(r.get("highsLowsAmount") or 0))
            except (TypeError, ValueError):
                return 0.0

        picked = sorted(rows, key=chg, reverse=True)[:top_n]
        for r in picked:
            name = r.get("productSortName", "-")
            avg = _fmt_price(r.get("avgPrice"))
            unit = r.get("unit", "")
            try:
                d = float(r.get("highsLowsAmount") or 0)
            except (TypeError, ValueError):
                d = 0.0
            if d > 0:
                mark = f'<font color="warning">▲{_fmt_price(d)}</font>'
            elif d < 0:
                mark = f'<font color="info">▼{_fmt_price(abs(d))}</font>'
            else:
                mark = '<font color="comment">— 持平</font>'
            lines.append(f"> {name}：**{avg}** {unit}　{mark}")
        lines.append("")

    flat = sum(
        1
        for rows in sheets.values()
        for r in rows
        if not (float(r.get("highsLowsAmount") or 0))
    )
    lines.append(f'<font color="comment">另有 {flat} 个品名持平，完整数据见附件</font>')
    if filename:
        lines.append(f'<font color="comment">📎 {filename}</font>')
    if mention_all:
        lines.append("<@all>")
    return "\n".join(lines)


def push(
    webhook: str,
    sheets: dict[str, list],
    file_path: str | Path | None,
    *,
    send_summary: bool = True,
    send_file_flag: bool = True,
    top_n: int = 10,
    mention_all: bool = False,
) -> None:
    """推送摘要 + 文件。摘要失败不阻断文件推送。"""
    if send_summary:
        md = build_summary(
            sheets,
            top_n=top_n,
            filename=Path(file_path).name if file_path else None,
            mention_all=mention_all,
        )
        try:
            send_markdown(webhook, md)
        except WeComError as e:
            log.error("摘要推送失败（继续尝试推送文件）: %s", e)
        time.sleep(1)  # 避开 20 条/分钟 限流

    if send_file_flag and file_path:
        send_file(webhook, file_path)
