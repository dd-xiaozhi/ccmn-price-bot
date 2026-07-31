#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置加载：YAML + ${ENV} 插值 + 热更新检测。"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.yaml"

# ${VAR} 或 ${VAR:-default}
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

DEFAULTS: dict[str, Any] = {
    "market": "长江现货",
    "markets_all": False,
    "output": {
        "dir": "output",
        "filename": "ccmn_{market}_{datetime}.xlsx",
        "keep_days": 7,
    },
    "schedule": {
        "cron": "*/30 * * * *",
        "timezone": "Asia/Shanghai",
        "run_on_start": False,
    },
    "wecom": {
        "enabled": False,
        "webhook": "",
        "send_summary": True,
        "send_file": True,
        "summary_top_n": 10,
        "mention_all": False,
        "notify_on_error": True,
    },
    "push": {
        "skip_if_unchanged": False,
        "state_file": ".push_state.json",
    },
    "log": {"file": "logs/ccmn.log", "level": "INFO"},
}


class ConfigError(RuntimeError):
    pass


def _interpolate(node: Any) -> Any:
    """递归把字符串里的 ${ENV} / ${ENV:-default} 换成环境变量值。"""
    if isinstance(node, str):

        def sub(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            val = os.environ.get(name)
            if val is None or val == "":
                if default is None:
                    raise ConfigError(
                        f"配置引用了环境变量 ${{{name}}}，但它未设置。"
                        f"请 export {name}=... 或改用 ${{{name}:-默认值}}"
                    )
                return default
            return val

        return _ENV_RE.sub(sub, node)
    if isinstance(node, dict):
        return {k: _interpolate(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_interpolate(v) for v in node]
    return node


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _resolve_path(cfg_path: Path | None) -> Path:
    if cfg_path:
        return Path(cfg_path).resolve()
    env = os.environ.get("CCMN_CONFIG")
    if env:
        return Path(env).resolve()
    return DEFAULT_CONFIG


def load(cfg_path: Path | None = None) -> dict:
    """读配置。每次调用都重新读盘 —— 调用方无需重启即可拿到最新配置。"""
    path = _resolve_path(cfg_path)
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"配置文件格式错误（顶层应为映射）: {path}")

    cfg = _deep_merge(DEFAULTS, raw)
    cfg = _interpolate(cfg)
    cfg["_path"] = str(path)
    cfg["_mtime"] = path.stat().st_mtime
    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    from croniter import croniter

    cron = cfg["schedule"]["cron"]
    if not croniter.is_valid(cron):
        raise ConfigError(f"schedule.cron 不是合法 cron 表达式: {cron!r}")

    if cfg["wecom"]["enabled"]:
        hook = (cfg["wecom"]["webhook"] or "").strip()
        if not hook.startswith("https://qyapi.weixin.qq.com/"):
            raise ConfigError(
                "wecom.enabled=true 但 wecom.webhook 非法（应为 "
                "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...）"
            )
        if "key=" not in hook:
            raise ConfigError("wecom.webhook 缺少 key 参数")


def mtime(cfg_path: Path | None = None) -> float:
    """当前配置文件 mtime，用于热更新检测。文件不存在返回 -1。"""
    path = _resolve_path(cfg_path)
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def setup_logging(cfg: dict) -> logging.Logger:
    level = getattr(logging, str(cfg["log"]["level"]).upper(), logging.INFO)
    log_file = cfg["log"].get("file")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        p = (ROOT / log_file) if not os.path.isabs(log_file) else Path(log_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(p, encoding="utf-8"))

    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    return logging.getLogger("ccmn")


def redact(url: str) -> str:
    """日志里隐去 webhook key。"""
    return re.sub(r"(key=)([^&]{4})[^&]*", r"\1\2****", url or "")
