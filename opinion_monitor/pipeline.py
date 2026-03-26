from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .data_processing import NewsDataProcessor
from .email_dispatcher import EmailDispatcher, build_email_body, build_email_subject
from .entity_filters import should_search_entity
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
        start_time = end_time - timedelta(days=self.settings.search_lookback_days)
        output_dir = self.settings.build_run_output_dir(run_time)
        input_path = excel_source or self.settings.excel_input_path

        reader = ExcelWatchlistReader(
            input_path=input_path,
            target_column_letter=self.settings.excel_target_column_letter,
        )
        entity_names = reader.read_entities()
        search_entity_names: list[str] = []
        skipped_entities: list[tuple[str, str]] = []
        for entity_name in entity_names:
            should_search, reason = should_search_entity(entity_name)
            if should_search:
                search_entity_names.append(entity_name)
            else:
                skipped_entities.append((entity_name, reason))

        if not search_entity_names:
            raise ValueError("读取到的主体均不适合直接搜索，请检查 Excel 是否混入债券简称或无效条目。")

        search_client = build_search_client(self.settings)
        news_items = []
        total_entities = len(entity_names)
        searchable_entity_count = len(search_entity_names)
        matched_entities = 0
        failed_entities = 0
        warnings: list[str] = []

        if skipped_entities:
            warnings.append(
                f"已跳过 {len(skipped_entities)} 个疑似债券简称、代码或无效主体，避免浪费搜索配额。"
            )
        if (
            self.settings.search_provider == "tavily"
            and self.settings.tavily_api_key.startswith("tvly-dev-")
            and searchable_entity_count > 200
        ):
            warnings.append(
                "当前使用 Tavily 开发 Key 且待搜索主体较多，运行中可能触发限流，建议拆分名单或更换正式 Key。"
            )
        for warning in warnings:
            logger.warning(warning)

        for index, entity_name in enumerate(search_entity_names, start=1):
            try:
                entity_items = search_client.search(entity_name, start_time, end_time)
                news_items.extend(entity_items)
                if entity_items:
                    matched_entities += 1
                logger.info(
                    "主体抓取完成 [%s/%s] %s，新增 %s 条。",
                    index,
                    searchable_entity_count,
                    entity_name,
                    len(entity_items),
                )
            except SearchClientError as exc:
                failed_entities += 1
                logger.exception("主体 %s 抓取失败，已跳过：%s", entity_name, exc)
            except Exception as exc:
                failed_entities += 1
                logger.exception("主体 %s 抓取出现未知异常，已跳过：%s", entity_name, exc)

            if index < searchable_entity_count:
                time.sleep(self.settings.request_delay_seconds)

        processor = NewsDataProcessor()
        try:
            dataframe = processor.to_dataframe(news_items)
            data_file_path = processor.export_to_excel(dataframe, output_dir, run_time)
        except Exception as exc:
            raise RuntimeError(f"数据整理导出阶段失败（{type(exc).__name__}）：{exc}") from exc

        try:
            report_generator = LLMReportGenerator(self.settings)
            report_file_path, core_summary = report_generator.generate_report(
                dataframe=dataframe,
                run_time=run_time,
                start_time=start_time,
                end_time=end_time,
                output_dir=output_dir,
            )
        except Exception as exc:
            raise RuntimeError(f"报告生成阶段失败（{type(exc).__name__}）：{exc}") from exc

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
            "流程执行完成：读取主体 %s 个，实际搜索 %s 个，命中主体 %s 个，跳过主体 %s 个，搜索失败 %s 个，落库舆情 %s 条，输出目录 %s，邮件发送状态 %s",
            total_entities,
            searchable_entity_count,
            matched_entities,
            len(skipped_entities),
            failed_entities,
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
            searched_entity_count=searchable_entity_count,
            matched_entity_count=matched_entities,
            skipped_entity_count=len(skipped_entities),
            failed_entity_count=failed_entities,
            warnings=tuple(warnings),
        )
