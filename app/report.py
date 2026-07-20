from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.data_fetcher import fetch_all, is_trading_day

logger = logging.getLogger(__name__)


def _today_cn() -> date:
    return datetime.now(ZoneInfo(get_settings().tz)).date()


def snapshot_path(day: date) -> Path:
    return get_settings().data_dir / f"{day.isoformat()}.json"


def build_commentary(data: dict[str, Any]) -> str:
    """基于规则生成简要文字点评。"""
    parts: list[str] = []

    indices = data.get("indices") or []
    sh = next((i for i in indices if i.get("name") == "上证指数"), None)
    if sh:
        pct = sh.get("pct") or 0
        if pct >= 1:
            parts.append(f"上证收涨 {pct:.2f}%，风险偏好回暖。")
        elif pct <= -1:
            parts.append(f"上证收跌 {pct:.2f}%，情绪偏谨慎。")
        else:
            parts.append(f"上证窄幅波动（{pct:+.2f}%），多空胶着。")

    breadth = data.get("breadth") or {}
    up, down = breadth.get("up", 0), breadth.get("down", 0)
    if up or down:
        if up > down * 1.3:
            parts.append(f"涨跌比 {up}:{down}，赚钱效应较好。")
        elif down > up * 1.3:
            parts.append(f"涨跌比 {up}:{down}，亏钱效应偏强。")
        else:
            parts.append(f"涨跌比 {up}:{down}，结构性行情。")
        lu = breadth.get("limit_up", 0)
        if lu:
            parts.append(f"涨停约 {lu} 家。")

    nb = data.get("northbound") or {}
    net = nb.get("net_inflow")
    if net is not None:
        direction = "净流入" if net >= 0 else "净流出"
        parts.append(f"北向资金{direction} {abs(net):.2f} 亿元。")

    gainers = (data.get("sectors") or {}).get("gainers") or []
    losers = (data.get("sectors") or {}).get("losers") or []
    if gainers:
        top = "、".join(g["name"] for g in gainers[:3])
        parts.append(f"强势板块：{top}。")
    if losers:
        weak = "、".join(g["name"] for g in losers[:2])
        parts.append(f"拖累板块：{weak}。")

    watch = data.get("watchlist") or []
    movers = [w for w in watch if w.get("pct") is not None]
    if movers:
        best = max(movers, key=lambda x: x["pct"])
        worst = min(movers, key=lambda x: x["pct"])
        parts.append(
            f"自选方面，{best['name']} {best['pct']:+.2f}%，"
            f"{worst['name']} {worst['pct']:+.2f}%。"
        )

    return "".join(parts) or "今日数据已生成，请结合盘面自行判断。"


def generate_report(day: date | None = None, *, force: bool = False) -> dict[str, Any]:
    day = day or _today_cn()
    path = snapshot_path(day)

    if path.exists() and not force:
        logger.info("复用已有快照 %s", path)
        return json.loads(path.read_text(encoding="utf-8"))

    if not force and not is_trading_day(day):
        report = {
            "date": day.isoformat(),
            "generated_at": datetime.now(ZoneInfo(get_settings().tz)).isoformat(),
            "is_trading_day": False,
            "commentary": "今日非交易日，无盘面复盘。",
            "indices": [],
            "breadth": {},
            "northbound": {},
            "watchlist": [],
            "sectors": {"gainers": [], "losers": []},
            "hot": {"amount": [], "turnover": []},
        }
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    raw = fetch_all(day)
    raw["is_trading_day"] = True
    raw["commentary"] = build_commentary(raw)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已写入复盘快照 %s", path)
    return raw


def load_report(day: date | None = None) -> dict[str, Any] | None:
    day = day or _today_cn()
    path = snapshot_path(day)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_report_dates(limit: int = 30) -> list[str]:
    files = sorted(get_settings().data_dir.glob("????-??-??.json"), reverse=True)
    return [f.stem for f in files[:limit]]
