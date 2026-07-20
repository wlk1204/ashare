from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import date, datetime
from typing import Any, Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from app.config import get_settings

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


def _today_cn() -> date:
    return datetime.now(ZoneInfo(get_settings().tz)).date()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        if isinstance(value, str) and value.strip() in {"", "-", "--"}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


EM_HOSTS = (
    "https://push2delay.eastmoney.com",
    "https://push2.eastmoney.com",
)


def _curl_json(url: str, *, referer: str = "https://quote.eastmoney.com/") -> Optional[dict[str, Any]]:
    """
    部分环境（旧 LibreSSL）下 requests/httpx 访问东财 push2 会断连，
    用系统 curl 更稳；优先 push2delay。
    """
    urls = [url]
    if "push2.eastmoney.com" in url and "push2delay" not in url:
        urls.insert(0, url.replace("https://push2.eastmoney.com", EM_HOSTS[0]))

    last_err: Optional[Exception] = None
    for candidate in urls:
        try:
            raw = subprocess.check_output(
                [
                    "curl",
                    "-sS",
                    "--max-time",
                    "60",
                    "-H",
                    f"User-Agent: {UA}",
                    "-H",
                    f"Referer: {referer}",
                    candidate,
                ],
                stderr=subprocess.DEVNULL,
            )
            if not raw:
                continue
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            last_err = exc
            continue
    if last_err:
        logger.warning("curl 请求失败: %s (%s)", url[:120], last_err)
    return None


def _sina_hq(symbols: list[str]) -> dict[str, list[str]]:
    """新浪行情，返回 symbol -> 字段列表。"""
    if not symbols:
        return {}
    url = "https://hq.sinajs.cn/list=" + ",".join(symbols)
    headers = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn"}
    try:
        with httpx.Client(timeout=20, headers=headers) as client:
            resp = client.get(url)
            text = resp.content.decode("gbk", errors="ignore")
    except Exception:
        logger.exception("新浪行情失败")
        return {}

    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = re.match(r'var hq_str_([^=]+)="(.*)";?', line.strip())
        if not m:
            continue
        symbol, payload = m.group(1), m.group(2)
        if not payload:
            continue
        out[symbol] = payload.split(",")
    return out


def is_trading_day(day: Optional[date] = None) -> bool:
    day = day or _today_cn()
    if day.weekday() >= 5:
        return False
    # 用上证简况是否有有效数据粗判（节假日通常仍有缓存，但收盘后可用）
    data = _sina_hq(["s_sh000001"])
    fields = data.get("s_sh000001")
    if not fields or len(fields) < 4:
        return day.weekday() < 5
    # 成交量全 0 且涨跌为 0，可能非交易日
    try:
        vol = float(fields[4]) if len(fields) > 4 else 0
        pct = float(fields[3])
        if vol == 0 and pct == 0 and day == _today_cn():
            # 盘前也可能为 0，仅周末已排除；节假日保守返回 True 交由人工看
            return True
    except ValueError:
        pass
    return True


def _em_clist(fs: str, fields: str, *, pz: int = 100, pages: int = 1, fid: str = "f3") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pn in range(1, pages + 1):
        params = {
            "pn": pn,
            "pz": pz,
            "po": 1,
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fid": fid,
            "fs": fs,
            "fields": fields,
        }
        url = "https://push2.eastmoney.com/api/qt/clist/get?" + urlencode(params)
        payload = _curl_json(url)
        if not payload or not payload.get("data") or not payload["data"].get("diff"):
            break
        rows.extend(payload["data"]["diff"])
        total = int(payload["data"].get("total") or 0)
        if pn * pz >= total:
            break
    return rows


def fetch_indices() -> list[dict[str, Any]]:
    mapping = [
        ("s_sh000001", "上证指数"),
        ("s_sz399001", "深证成指"),
        ("s_sz399006", "创业板指"),
    ]
    hq = _sina_hq([m[0] for m in mapping])
    results: list[dict[str, Any]] = []
    for symbol, name in mapping:
        fields = hq.get(symbol)
        if not fields or len(fields) < 4:
            continue
        # s_ 简况: 名称,现价,涨跌额,涨跌幅,成交量(手),成交额(万)
        close = _safe_float(fields[1])
        change = _safe_float(fields[2])
        pct = _safe_float(fields[3])
        results.append(
            {
                "name": name,
                "close": round(close, 2),
                "change": round(change, 2),
                "pct": round(pct, 2),
            }
        )
    return results


def _fetch_a_spot_rows() -> list[dict[str, Any]]:
    fields = "f12,f14,f2,f3,f4,f5,f6,f8"
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
    return _em_clist(fs, fields, pz=100, pages=60, fid="f12")


def fetch_market_breadth(rows: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    rows = rows if rows is not None else _fetch_a_spot_rows()
    if not rows:
        return {"up": 0, "down": 0, "flat": 0, "limit_up": 0, "limit_down": 0, "total": 0}

    up = down = flat = limit_up = limit_down = 0
    for row in rows:
        raw_pct = row.get("f3")
        raw_price = row.get("f2")
        # 停牌、退市、无行情等：东财返回 "-" / None，不能当作平盘
        if raw_pct in (None, "", "-") or raw_price in (None, "", "-"):
            continue
        try:
            pct = float(raw_pct)
        except (TypeError, ValueError):
            continue

        if pct > 0:
            up += 1
        elif pct < 0:
            down += 1
        else:
            flat += 1
        if pct >= 9.5:
            limit_up += 1
        if pct <= -9.5:
            limit_down += 1
    return {
        "up": up,
        "down": down,
        "flat": flat,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "total": up + down + flat,
    }


def fetch_northbound(day: Optional[date] = None) -> dict[str, Any]:
    day = day or _today_cn()
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get?"
        + urlencode(
            {
                "sortColumns": "TRADE_DATE",
                "sortTypes": "-1",
                "pageSize": "10",
                "pageNumber": "1",
                "reportName": "RPT_MUTUAL_DEAL_HISTORY",
                "columns": "ALL",
                "source": "WEB",
                "client": "WEB",
                "filter": '(MUTUAL_TYPE="001")',
            }
        )
    )
    # datacenter 可用 httpx
    try:
        with httpx.Client(timeout=20, headers={"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}) as client:
            payload = client.get(url).json()
    except Exception:
        logger.exception("北向资金接口失败，尝试 curl")
        payload = _curl_json(url, referer="https://data.eastmoney.com/")

    records = (((payload or {}).get("result") or {}).get("data")) or []
    chosen = None
    for item in records:
        trade = str(item.get("TRADE_DATE", ""))[:10]
        if trade == day.isoformat():
            chosen = item
            break
    if chosen is None and records:
        chosen = records[0]

    if not chosen:
        return {"date": day.isoformat(), "net_inflow": None, "unit": "亿元"}

    net = chosen.get("NET_DEAL_AMT")
    trade_date = str(chosen.get("TRADE_DATE", day.isoformat()))[:10]
    # 当日净买入有时盘后延迟入库；无有效净值时返回 None，避免误用额度字段
    if net is None:
        return {"date": trade_date, "net_inflow": None, "unit": "亿元"}

    net_f = _safe_float(net)
    # 历史接口单位多为万元 → 亿元
    if abs(net_f) > 1000:
        net_f = net_f / 10000
    return {
        "date": trade_date,
        "net_inflow": round(net_f, 2),
        "unit": "亿元",
    }


def _market_symbol(code: str) -> str:
    code = code.zfill(6)
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def fetch_watchlist(codes: Optional[list[str]] = None) -> list[dict[str, Any]]:
    codes = codes or get_settings().watchlist_codes
    if not codes:
        return []
    symbols = [_market_symbol(c) for c in codes]
    hq = _sina_hq(symbols)
    items: list[dict[str, Any]] = []
    for code, symbol in zip(codes, symbols):
        code = code.zfill(6)
        fields = hq.get(symbol)
        if not fields or len(fields) < 32:
            items.append({"code": code, "name": code, "close": None, "pct": None, "error": True})
            continue
        # 详细行情: 名称,今开,昨收,现价,最高,最低,...,日期,时间
        name = re.sub(r"\s+", "", fields[0]).strip()
        prev = _safe_float(fields[2])
        close = _safe_float(fields[3])
        amount = _safe_float(fields[9]) / 1e8
        pct = round((close - prev) / prev * 100, 2) if prev else 0.0
        change = round(close - prev, 2)
        items.append(
            {
                "code": code,
                "name": name,
                "close": round(close, 2),
                "pct": pct,
                "change": change,
                "amount": round(amount, 2),
                "turnover": None,
            }
        )
    return items


def fetch_sector_rank(top_n: int = 8) -> dict[str, list[dict[str, Any]]]:
    fields = "f12,f14,f2,f3,f128"
    fs = "m:90+t:2+f:!50"

    def _pack(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for row in rows[:top_n]:
            out.append(
                {
                    "name": str(row.get("f14", "")),
                    "pct": round(_safe_float(row.get("f3")), 2),
                    "leader": str(row.get("f128", "") or ""),
                }
            )
        return out

    # po=1 降序涨幅榜；po=0 升序跌幅榜
    gain_rows = _em_clist(fs, fields, pz=top_n, pages=1, fid="f3")
    # 单独请求升序
    params = {
        "pn": 1,
        "pz": top_n,
        "po": 0,
        "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": fs,
        "fields": fields,
    }
    url = "https://push2.eastmoney.com/api/qt/clist/get?" + urlencode(params)
    lose_payload = _curl_json(url)
    lose_rows = (((lose_payload or {}).get("data") or {}).get("diff")) or []

    # 若降序接口因 po 默认已排好，gain_rows 直接可用
    if gain_rows and _safe_float(gain_rows[0].get("f3")) < _safe_float(gain_rows[-1].get("f3")):
        gain_rows = sorted(gain_rows, key=lambda r: _safe_float(r.get("f3")), reverse=True)

    return {"gainers": _pack(gain_rows), "losers": _pack(lose_rows)}


def fetch_hot_spots(
    top_n: int = 10,
    rows: Optional[list[dict[str, Any]]] = None,
) -> dict[str, list[dict[str, Any]]]:
    rows = rows if rows is not None else _fetch_a_spot_rows()
    if not rows:
        return {"amount": [], "turnover": []}

    liquid = []
    for row in rows:
        amount = _safe_float(row.get("f6"))
        if amount < 1e8:
            continue
        liquid.append(
            {
                "code": str(row.get("f12", "")).zfill(6),
                "name": str(row.get("f14", "")),
                "pct": round(_safe_float(row.get("f3")), 2),
                "amount": round(amount / 1e8, 2),
                "turnover": round(_safe_float(row.get("f8")), 2),
            }
        )

    by_amount = sorted(liquid, key=lambda x: x["amount"], reverse=True)[:top_n]
    by_turnover = sorted(liquid, key=lambda x: x["turnover"], reverse=True)[:top_n]
    return {"amount": by_amount, "turnover": by_turnover}


def fetch_all(day: Optional[date] = None) -> dict[str, Any]:
    day = day or _today_cn()
    a_rows = _fetch_a_spot_rows()
    return {
        "date": day.isoformat(),
        "generated_at": datetime.now(ZoneInfo(get_settings().tz)).isoformat(),
        "indices": fetch_indices(),
        "breadth": fetch_market_breadth(a_rows),
        "northbound": fetch_northbound(day),
        "watchlist": fetch_watchlist(),
        "sectors": fetch_sector_rank(),
        "hot": fetch_hot_spots(rows=a_rows),
    }
