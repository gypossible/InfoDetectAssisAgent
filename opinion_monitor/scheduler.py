from __future__ import annotations

import logging
import time

import schedule

from .config import Settings
from .pipeline import PublicOpinionPipeline

logger = logging.getLogger(__name__)


def _safe_run_pipeline(settings: Settings) -> None:
    try:
        PublicOpinionPipeline(settings).run()
    except Exception as exc:
        logger.exception("定时任务执行失败：%s", exc)


def run_daily_scheduler(settings: Settings | None = None) -> None:
    runtime_settings = settings or Settings()
    schedule.clear("public-opinion-monitor")
    schedule.every().day.at(runtime_settings.schedule_time).do(
        _safe_run_pipeline,
        settings=runtime_settings,
    ).tag("public-opinion-monitor")

    logger.info("定时调度已启动，每天 %s 执行一次。", runtime_settings.schedule_time)
    logger.info(
        "Linux crontab 示例：50 6 * * * cd %s && /usr/bin/python3 main.py >> logs/cron.log 2>&1",
        runtime_settings.project_root,
    )
    logger.info(
        "Windows 任务计划程序示例：schtasks /Create /SC DAILY /TN PublicOpinionMonitor "
        '/TR "python C:\\path\\to\\main.py" /ST 06:50'
    )

    while True:
        schedule.run_pending()
        time.sleep(30)
