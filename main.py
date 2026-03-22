from __future__ import annotations

import argparse
import logging
from pathlib import Path

from opinion_monitor.config import Settings
from opinion_monitor.logging_utils import setup_logging
from opinion_monitor.pipeline import PublicOpinionPipeline
from opinion_monitor.scheduler import run_daily_scheduler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="自动化舆情监测与报告生成 Agent")
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="以常驻进程模式运行，每天在设定时间自动执行。",
    )
    parser.add_argument(
        "--excel-path",
        type=str,
        default="",
        help="覆盖默认 Excel 文件或目录路径。",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    settings = Settings()
    setup_logging(settings.log_dir)

    if args.schedule:
        run_daily_scheduler(settings)
        return

    excel_path = Path(args.excel_path).expanduser() if args.excel_path else None
    result = PublicOpinionPipeline(settings).run(excel_source=excel_path)
    logging.getLogger(__name__).info(
        "单次任务执行成功：主体 %s 个，舆情 %s 条，数据文件 %s，报告文件 %s，邮件发送 %s",
        result.entity_count,
        result.article_count,
        result.data_file_path,
        result.report_file_path,
        result.email_sent,
    )


if __name__ == "__main__":
    main()
