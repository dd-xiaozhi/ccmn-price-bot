#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""常驻定时调度器：按 config.yaml 的 cron 执行，并热加载配置。

    python3 scheduler.py              # 前台运行
    nohup python3 scheduler.py &      # 后台运行

热更新：每 2 秒检查一次 config.yaml 的 mtime，改动后立即重载并按新 cron 重排，
        无需重启进程。配置写坏时保留上一份可用配置继续运行。
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from croniter import croniter

import config as cfgmod
import run as runner

POLL_SECONDS = 2.0
_stop = False


def _on_signal(signum, _frame):
    global _stop
    _stop = True
    print(f"\n收到信号 {signum}，退出中…", flush=True)


def _tz(cfg: dict) -> ZoneInfo:
    name = cfg["schedule"]["timezone"] or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — 时区名写错时退回上海，不让调度挂掉
        print(f"[warn] 未知时区 {name!r}，退回 Asia/Shanghai", file=sys.stderr)
        return ZoneInfo("Asia/Shanghai")


def _next_fire(cfg: dict, after: datetime | None = None) -> datetime:
    tz = _tz(cfg)
    base = after or datetime.now(tz)
    return croniter(cfg["schedule"]["cron"], base).get_next(datetime)


def _notify_error(cfg: dict, err: Exception, log) -> None:
    wc = cfg["wecom"]
    if not (wc["enabled"] and wc.get("notify_on_error", True)):
        return
    try:
        import wecom

        wecom.send_markdown(
            wc["webhook"],
            "## ⚠️ 长江现货抓取任务失败\n"
            f"> 时间：{datetime.now(_tz(cfg)).strftime('%Y-%m-%d %H:%M:%S')}\n"
            f'> 原因：<font color="warning">{type(err).__name__}: {err}</font>\n'
            f"> 详见服务器日志 {cfg['log'].get('file')}",
        )
    except Exception as e:  # noqa: BLE001 — 告警失败不能再拖垮调度
        log.warning("失败告警推送不出去: %s", e)


def main() -> int:
    ap = argparse.ArgumentParser(description="长江现货定时抓取推送调度器")
    ap.add_argument("-c", "--config", help="配置文件路径（默认 config.yaml）")
    args = ap.parse_args()
    cfg_path = Path(args.config) if args.config else None

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        cfg = cfgmod.load(cfg_path)
    except cfgmod.ConfigError as e:
        print(f"[error] 配置错误: {e}", file=sys.stderr)
        return 2

    log = cfgmod.setup_logging(cfg)
    last_mtime = cfg["_mtime"]
    tz = _tz(cfg)
    nxt = _next_fire(cfg)

    log.info("调度器启动 | cron=%r tz=%s", cfg["schedule"]["cron"], cfg["schedule"]["timezone"])
    log.info("配置文件: %s（热加载已开启）", cfg["_path"])
    log.info("下次执行: %s", nxt.strftime("%Y-%m-%d %H:%M:%S %Z"))

    if cfg["schedule"]["run_on_start"]:
        log.info("run_on_start=true，先执行一次")
        try:
            runner.run_once(cfg)
        except Exception as e:  # noqa: BLE001 — 单次失败不终止调度
            log.exception("首次执行失败: %s", e)
            _notify_error(cfg, e, log)

    while not _stop:
        # ── 热加载 ──
        m = cfgmod.mtime(cfg_path)
        if m > 0 and m != last_mtime:
            try:
                new_cfg = cfgmod.load(cfg_path)
            except cfgmod.ConfigError as e:
                log.error("配置重载失败，继续沿用旧配置: %s", e)
                last_mtime = m  # 避免每 2 秒刷屏，等下次改动再试
            else:
                old_cron = cfg["schedule"]["cron"]
                old_tz = cfg["schedule"]["timezone"]
                cfg, last_mtime = new_cfg, new_cfg["_mtime"]
                log = cfgmod.setup_logging(cfg)
                tz = _tz(cfg)
                log.info("🔄 配置已热加载")
                if (cfg["schedule"]["cron"], cfg["schedule"]["timezone"]) != (old_cron, old_tz):
                    nxt = _next_fire(cfg)
                    log.info(
                        "cron 变更 %r(%s) → %r(%s)，下次执行: %s",
                        old_cron,
                        old_tz,
                        cfg["schedule"]["cron"],
                        cfg["schedule"]["timezone"],
                        nxt.strftime("%Y-%m-%d %H:%M:%S %Z"),
                    )

        # ── 到点执行 ──
        now = datetime.now(tz)
        if now >= nxt:
            try:
                runner.run_once(cfg)
            except Exception as e:  # noqa: BLE001 — 单次失败不终止调度
                log.exception("本轮执行失败: %s", e)
                _notify_error(cfg, e, log)
            nxt = _next_fire(cfg, datetime.now(tz))
            log.info("下次执行: %s", nxt.strftime("%Y-%m-%d %H:%M:%S %Z"))

        time.sleep(POLL_SECONDS)

    log.info("调度器已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
