#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 config.example.yaml 的 schedule.cron 同步进 GitHub Actions workflow。

GitHub Actions 的 `on.schedule.cron` 必须写死在 workflow 文件里，且只认 UTC，
运行时读不到 yaml。本脚本让 config 保持单一事实源：

    python3 scripts/sync_workflow_cron.py            # 写入
    python3 scripts/sync_workflow_cron.py --check    # 只校验（CI 用，不一致则退出码 1）

时区换算：
  * 小时位是 `*` 或 `*/n` → 与时区无关，原样使用
  * 小时位是具体值 → 按配置时区的 UTC 偏移整体前移
  * 换算跨天且限定了星期/日期 → 无法用单条 cron 精确表达，报警并要求人工确认
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.example.yaml"
WORKFLOW = ROOT / ".github" / "workflows" / "ccmn-push.yml"
BEGIN = "# cron-sync:begin"
END = "# cron-sync:end"


def utc_offset_hours(tz_name: str) -> float:
    tz = ZoneInfo(tz_name)
    off = datetime.now(tz).utcoffset()
    return (off.total_seconds() / 3600) if off else 0.0


def to_utc_cron(cron: str, tz_name: str) -> tuple[str, list[str]]:
    """返回 (UTC cron, 警告列表)。"""
    warns: list[str] = []
    fields = cron.split()
    if len(fields) != 5:
        raise SystemExit(f"[error] 只支持标准 5 段 cron，收到: {cron!r}")
    minute, hour, dom, month, dow = fields

    offset = utc_offset_hours(tz_name)
    if offset == 0:
        return cron, warns
    if hour == "*" or hour.startswith("*/"):
        # 间隔型，与时区无关
        return cron, warns
    if offset != int(offset):
        warns.append(f"时区 {tz_name} 偏移 {offset}h 非整小时，cron 无法精确表达，已取整")

    off = int(offset)
    shifted: list[int] = []
    wrapped = False
    for part in hour.split(","):
        if not re.fullmatch(r"\d{1,2}", part):
            warns.append(f"小时位 {part!r} 结构复杂，未做时区换算，请人工确认")
            return cron, warns
        h = int(part)
        u = h - off
        if u < 0 or u > 23:
            wrapped = True
        shifted.append(u % 24)

    if wrapped and (dom != "*" or dow != "*"):
        warns.append(
            f"{tz_name} {hour}时 换算到 UTC 后跨天，而 cron 限定了日期/星期"
            f"（dom={dom} dow={dow}），单条 cron 无法精确表达 —— 请人工核对"
        )
    return " ".join([minute, ",".join(str(h) for h in shifted), dom, month, dow]), warns


def build_block(utc_cron: str, src_cron: str, tz_name: str) -> str:
    note = "" if utc_cron == src_cron else "（已按时区换算为 UTC）"
    return (
        f'    - cron: "{utc_cron}"   '
        f'# config.schedule.cron="{src_cron}" tz={tz_name}{note}'
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="同步 config cron 到 GitHub Actions workflow")
    ap.add_argument("--check", action="store_true", help="只校验一致性，不写文件")
    args = ap.parse_args()

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    sched = cfg.get("schedule", {})
    src_cron = sched.get("cron", "*/30 * * * *")
    tz_name = sched.get("timezone", "Asia/Shanghai")

    utc_cron, warns = to_utc_cron(src_cron, tz_name)
    for w in warns:
        print(f"[warn] {w}", file=sys.stderr)

    text = WORKFLOW.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"({re.escape(BEGIN)}\n)(.*?)(\s*{re.escape(END)})", re.DOTALL
    )
    if not pattern.search(text):
        raise SystemExit(f"[error] workflow 中找不到 {BEGIN} / {END} 标记: {WORKFLOW}")

    block = build_block(utc_cron, src_cron, tz_name)
    new_text = pattern.sub(lambda m: f"{m.group(1)}{block}{m.group(3)}", text)

    if args.check:
        if new_text != text:
            print(
                f"[error] workflow cron 与 config 不一致。\n"
                f"        config: {src_cron} ({tz_name}) → 期望 UTC cron: {utc_cron}\n"
                f"        请执行: python3 scripts/sync_workflow_cron.py",
                file=sys.stderr,
            )
            return 1
        print(f"✅ 一致: config {src_cron} ({tz_name}) == workflow UTC {utc_cron}")
        return 0

    if new_text == text:
        print(f"✅ 无需改动: {utc_cron}")
        return 0
    WORKFLOW.write_text(new_text, encoding="utf-8")
    print(f"✅ 已同步 → {WORKFLOW.relative_to(ROOT)}")
    print(f"   config: {src_cron} ({tz_name})  →  workflow(UTC): {utc_cron}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
