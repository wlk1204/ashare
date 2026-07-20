from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.data_fetcher import is_trading_day
from app.report import generate_report, list_report_dates, load_report
from app.wechat import WeChatError, push_daily_report


def _latest_or_generate(day: date) -> dict[str, Any]:
    report = load_report(day)
    if report is not None and (report.get("indices") or not report.get("is_trading_day", True)):
        return report
    history = list_report_dates(limit=5)
    for item in history:
        if item == day.isoformat():
            continue
        prev = load_report(date.fromisoformat(item))
        if prev and prev.get("indices"):
            return prev
    if report is not None:
        return report
    return generate_report(day, force=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone=get_settings().tz)
templates = Jinja2Templates(directory="app/templates")


def _parse_day(day: Optional[str]) -> date:
    settings = get_settings()
    if not day:
        return datetime.now(ZoneInfo(settings.tz)).date()
    try:
        return date.fromisoformat(day)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from exc


def _run_daily_job() -> None:
    settings = get_settings()
    today = datetime.now(ZoneInfo(settings.tz)).date()
    logger.info("定时任务触发: %s", today)
    if not is_trading_day(today):
        logger.info("非交易日，跳过生成与推送")
        return
    try:
        report = generate_report(today, force=True)
        push_daily_report(report)
    except Exception:
        logger.exception("定时复盘/推送失败")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    trigger = CronTrigger(
        hour=settings.cron_hour,
        minute=settings.cron_minute,
        day_of_week="mon-fri",
        timezone=settings.tz,
    )
    scheduler.add_job(_run_daily_job, trigger, id="daily_review", replace_existing=True)
    scheduler.start()
    logger.info(
        "调度已启动: 工作日 %02d:%02d %s",
        settings.cron_hour,
        settings.cron_minute,
        settings.tz,
    )
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="A股日复盘", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def _render_review(request: Request, report: dict[str, Any]) -> HTMLResponse:
    return templates.TemplateResponse(
        "review.html",
        {
            "request": request,
            "report": report,
            "history": list_report_dates(),
            "base_url": get_settings().base_url,
        },
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    today = datetime.now(ZoneInfo(get_settings().tz)).date()
    report = _latest_or_generate(today)
    return _render_review(request, report)


@app.get("/review/{day}", response_class=HTMLResponse)
async def review_by_day(request: Request, day: str):
    d = _parse_day(day)
    report = load_report(d)
    if report is None:
        report = generate_report(d, force=False)
    return _render_review(request, report)


@app.get("/api/report")
async def api_report(day: Optional[str] = None):
    d = _parse_day(day)
    report = load_report(d)
    if report is None:
        raise HTTPException(status_code=404, detail="该日复盘不存在，请先生成")
    return report


@app.post("/api/generate")
async def api_generate(
    day: Optional[str] = None,
    push: bool = Query(False, description="生成后是否推送微信"),
    force: bool = Query(True),
):
    d = _parse_day(day)
    report = generate_report(d, force=force)
    push_result: Optional[dict[str, Any]] = None
    if push:
        try:
            push_result = push_daily_report(report)
        except WeChatError as exc:
            return JSONResponse(
                status_code=502,
                content={"report": report, "push_error": str(exc)},
            )
    return {"report": report, "push": push_result}


@app.post("/api/push")
async def api_push(day: Optional[str] = None):
    d = _parse_day(day)
    report = load_report(d)
    if report is None:
        raise HTTPException(status_code=404, detail="该日复盘不存在，请先 /api/generate")
    try:
        result = push_daily_report(report)
    except WeChatError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return result


@app.get("/health")
async def health():
    return {"ok": True}
