#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性任务：抓取 → 导出 Excel → 推送企微群。

    python3 run.py                 # 按 config.yaml 跑一次
    python3 run.py --dry-run       # 只抓取导出，不推送
    python3 run.py --force         # 忽略 skip_if_unchanged
    python3 run.py -c other.yaml   # 指定配置
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import ccmn_cjxh as scraper
import config as cfgmod
import wecom

ROOT = Path(__file__).resolve().parent


def _abs(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else (ROOT / p)


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _cleanup(out_dir: Path, keep_days: int, log) -> None:
    if keep_days <= 0 or not out_dir.exists():
        return
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for f in out_dir.glob("*.xlsx"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError as e:
            log.warning("清理旧文件失败 %s: %s", f.name, e)
    if removed:
        log.info("已清理 %s 个 %s 天前的旧文件", removed, keep_days)


def run_once(cfg: dict, *, dry_run: bool = False, force: bool = False) -> dict:
    """执行一轮。返回结果摘要 dict。异常向上抛给调用方（scheduler 负责兜住）。"""
    log = cfgmod.setup_logging(cfg)
    started = datetime.now()
    log.info("=" * 60)
    log.info("任务开始 | 配置: %s", cfg["_path"])

    # 1. 抓取
    market = cfg["market"]
    all_markets = bool(cfg["markets_all"])
    sheets = scraper.collect(market=None if all_markets else market, all_markets=all_markets)
    total = sum(len(v) for v in sheets.values())
    for name, rows in sheets.items():
        log.info("抓取 [%s] %s 行", name, len(rows))
    if total == 0:
        raise RuntimeError("抓取到 0 行数据，疑似接口异常或结构变更")

    # 2. 变更判定
    fp = scraper.fingerprint(sheets)
    state_path = _abs(cfg["push"]["state_file"])
    state = _load_state(state_path)
    unchanged = state.get("fingerprint") == fp
    if unchanged and cfg["push"]["skip_if_unchanged"] and not force:
        log.info("数据与上次推送一致（上次 %s），按配置跳过", state.get("pushed_at", "-"))
        return {"skipped": True, "rows": total, "fingerprint": fp}

    # 3. 导出
    out_dir = _abs(cfg["output"]["dir"])
    name_tpl = cfg["output"]["filename"]
    fname = name_tpl.format(
        market="全部市场" if all_markets else market,
        date=scraper.data_date(sheets),
        datetime=started.strftime("%Y%m%d_%H%M"),
    )
    path = scraper.export_excel(sheets, out_dir / fname)
    log.info("已导出 %s 行 → %s (%.1f KB)", total, path, path.stat().st_size / 1024)

    # 4. 推送
    pushed = False
    wc = cfg["wecom"]
    if dry_run:
        log.info("--dry-run：跳过企微推送")
    elif not wc["enabled"]:
        log.info("wecom.enabled=false：跳过企微推送")
    else:
        log.info("推送企微 → %s", cfgmod.redact(wc["webhook"]))
        wecom.push(
            wc["webhook"],
            sheets,
            path,
            send_summary=bool(wc["send_summary"]),
            send_file_flag=bool(wc["send_file"]),
            top_n=int(wc["summary_top_n"]),
            mention_all=bool(wc["mention_all"]),
        )
        pushed = True

    # 5. 收尾
    if pushed:
        _save_state(
            state_path,
            {
                "fingerprint": fp,
                "pushed_at": datetime.now().isoformat(timespec="seconds"),
                "file": str(path),
                "rows": total,
            },
        )
    _cleanup(out_dir, int(cfg["output"]["keep_days"]), log)

    cost = (datetime.now() - started).total_seconds()
    log.info("任务完成 | %s 行 | 推送=%s | 耗时 %.1fs", total, pushed, cost)
    return {"skipped": False, "rows": total, "file": str(path), "pushed": pushed}


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取长江现货行情并推送企微群")
    ap.add_argument("-c", "--config", help="配置文件路径（默认 config.yaml）")
    ap.add_argument("--dry-run", action="store_true", help="只抓取导出，不推送企微")
    ap.add_argument("--force", action="store_true", help="忽略 skip_if_unchanged 强制推送")
    args = ap.parse_args()

    try:
        cfg = cfgmod.load(Path(args.config) if args.config else None)
    except cfgmod.ConfigError as e:
        print(f"[error] 配置错误: {e}", file=sys.stderr)
        return 2

    try:
        run_once(cfg, dry_run=args.dry_run, force=args.force)
    except Exception as e:  # noqa: BLE001 — CLI 边界，打印后以非零退出
        import logging

        logging.getLogger("ccmn").exception("任务失败: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
