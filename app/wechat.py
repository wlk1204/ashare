from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


class WeChatError(RuntimeError):
    pass


def get_access_token(*, force: bool = False) -> str:
    settings = get_settings()
    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise WeChatError("未配置 WECHAT_APP_ID / WECHAT_APP_SECRET")

    now = time.time()
    if (
        not force
        and _token_cache["token"]
        and now < float(_token_cache["expires_at"]) - 120
    ):
        return str(_token_cache["token"])

    url = "https://api.weixin.qq.com/cgi-bin/token"
    params = {
        "grant_type": "client_credential",
        "appid": settings.wechat_app_id,
        "secret": settings.wechat_app_secret,
    }
    with httpx.Client(timeout=20) as client:
        resp = client.get(url, params=params)
        data = resp.json()

    if "access_token" not in data:
        raise WeChatError(f"获取 access_token 失败: {data}")

    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + int(data.get("expires_in", 7200))
    return str(data["access_token"])


def build_push_text(report: dict[str, Any], page_url: str) -> str:
    date_str = report.get("date", "")
    commentary = report.get("commentary") or "今日复盘已生成。"
    # 群发文本不宜过长，截断点评
    if len(commentary) > 120:
        commentary = commentary[:117] + "..."
    return (
        f"【A股日复盘】{date_str}\n"
        f"{commentary}\n"
        f"查看完整复盘：{page_url}"
    )


def mass_send_text(content: str) -> dict[str, Any]:
    """
    认证订阅号：按标签群发文本（is_to_all=true 发给全部粉丝）。
    每天仅可成功调用 1 次。
    """
    token = get_access_token()
    url = f"https://api.weixin.qq.com/cgi-bin/message/mass/sendall?access_token={token}"
    payload = {
        "filter": {"is_to_all": True},
        "text": {"content": content},
        "msgtype": "text",
        "clientmsgid": f"ashare-{int(time.time())}",
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json=payload)
        data = resp.json()

    if data.get("errcode", 0) != 0:
        raise WeChatError(f"群发失败: {data}")
    logger.info("微信群发成功: %s", data)
    return data


def push_daily_report(report: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    if not settings.wechat_push_enabled:
        logger.info("WECHAT_PUSH_ENABLED=false，跳过推送")
        return {"skipped": True, "reason": "push_disabled"}

    date_str = report.get("date")
    page_url = f"{settings.base_url.rstrip('/')}/review/{date_str}"
    text = build_push_text(report, page_url)
    return mass_send_text(text)
