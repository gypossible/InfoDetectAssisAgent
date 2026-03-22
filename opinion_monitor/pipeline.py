from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .data_processing import NewsDataProcessor
from .email_dispatcher import EmailDispatcher, build_email_body, build_email_subject
from .excel_reader import ExcelWatchlistReader
from .models import PipelineResult
from .report_generator import LLMReportGenerator
from .search_clients import SearchClientError, build_search_client

logger = logging.getLogger(__name__)


class PublicOpinionPipeline:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.settings.ensure_directories()

    def run(self, excel_source: Path | None = None) -> PipelineResult:
        run_time = datetime.now().astimezone()
        end_time = run_time.astimezone(timezone.utc)
        start_time = end_time - timedelta(hours=24)
        output_dir = self.settings.build_run_output_dir(run_time)
        input_path = excel_source or self.settings.excel_input_path

        reader = ExcelWatchlistReader(
            input_path=input_path,
            target_column_letter=self.settings.excel_target_column_letter,
        )
        entity_names = reader.read_entities()

        search_client = build_search_client(self.settings)
        news_items = []
        total_entities = len(entity_names)

        for index, entity_name in enumerate(entity_names, start=1):
            try:
                entity_items = search_client.search(entity_name, start_time, end_time)
                news_items.extend(entity_items)
                logger.info(
                    "主体抓取完成 [%s/%s] %s，新增 %s 条。",
                    index,
                    total_entities,
                    entity_name,
                    len(entity_items),
                )
            except SearchClientError as exc:
                logger.exception("主体 %s 抓取失败，已跳过：%s", entity_name, exc)
            except Exception as exc:
                logger.exception("主体 %s 抓取出现未知异常，已跳过：%s", entity_name, exc)

            if index < total_entities:
                time.sleep(self.settings.request_delay_seconds)

        processor = NewsDataProcessor()
        dataframe = processor.to_dataframe(news_items)
        data_file_path = processor.export_to_excel(dataframe, output_dir, run_time)

        report_generator = LLMReportGenerator(self.settings)
        report_file_path, core_summary = report_generator.generate_report(
            dataframe=dataframe,
            run_time=run_time,
            start_time=start_time,
            end_time=end_time,
            output_dir=output_dir,
        )

        dispatcher = EmailDispatcher(self.settings)
        email_sent = False
        try:
            dispatcher.send_email(
                subject=build_email_subject(run_time),
                body=build_email_body(run_time, core_summary),
                attachments=[data_file_path, report_file_path],
            )
            email_sent = True
        except Exception as exc:
            logger.exception("邮件发送失败，但已保留生成的数据与报告文件：%s", exc)

        logger.info(
            "流程执行完成：监测主体 %s 个，落库舆情 %s 条，输出目录 %s，邮件发送状态 %s",
            total_entities,
            len(dataframe),
            output_dir,
            email_sent,
        )
        return PipelineResult(
            entity_count=total_entities,
            article_count=int(len(dataframe)),
            data_file_path=data_file_path,
            report_file_path=report_file_path,
            email_sent=email_sent,
        )
