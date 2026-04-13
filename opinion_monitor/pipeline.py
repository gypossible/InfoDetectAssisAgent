from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .data_processing import NewsDataProcessor
from .email_dispatcher import EmailDispatcher, build_email_body, build_email_subject
from .entity_filters import should_search_entity
from .excel_reader import ExcelWatchlistReader
from .models import PipelineProgress, PipelineResult
from .report_generator import LLMReportGenerator
from .search_clients import SearchClientError, build_search_client
from .workbook_exporter import AnnotatedWorkbookExporter

logger = logging.getLogger(__name__)


class PublicOpinionPipeline:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.settings.ensure_directories()

    def run(
        self,
        excel_source: Path | None = None,
        progress_callback: Callable[[PipelineProgress], None] | None = None,
    ) -> PipelineResult:
        run_time = datetime.now().astimezone()
        end_time = run_time.astimezone(timezone.utc)
        start_time = end_time - timedelta(days=self.settings.search_lookback_days)
        output_dir = self.settings.build_run_output_dir(run_time)
        input_path = excel_source or self.settings.excel_input_path
        self._emit_progress(progress_callback, 3, "prepare", "正在读取 Excel 监测名单...")

        reader = ExcelWatchlistReader(
            input_path=input_path,
            target_column_letter=self.settings.excel_target_column_letter,
        )
        entity_names = reader.read_entities()
        self._emit_progress(
            progress_callback,
            8,
            "read_watchlist",
            f"已读取 {len(entity_names)} 个监测主体，正在筛选有效搜索项...",
        )
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

        self._emit_progress(
            progress_callback,
            12,
            "build_search",
            "正在初始化搜索来源...",
            completed_entities=0,
            total_entities=len(search_entity_names),
        )
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
            "tavily" in self.settings.search_providers
            and self.settings.tavily_api_key.startswith("tvly-dev-")
            and searchable_entity_count > 200
        ):
            warnings.append(
                "当前使用 Tavily 开发 Key 且待搜索主体较多，运行中可能触发限流，建议拆分名单或更换正式 Key。"
            )
        if "qcc" in self.settings.search_providers or "qichacha" in self.settings.search_providers:
            warnings.append(
                "当前搜索链路已包含企查查新闻接口。企查查开放平台通常按次计费，请确认 APPKEY、SecretKey 与账户额度后再批量执行。"
            )
            if not self.settings.qcc_app_key or not self.settings.qcc_secret_key:
                warnings.append(
                    "企查查新闻接口尚未完全启用：官方调用需要 QCC_APP_KEY 与 QCC_SECRET_KEY。仅填写登录账号或单一 key 通常不足以完成鉴权。"
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

            search_progress = 12 + int(index / max(searchable_entity_count, 1) * 58)
            self._emit_progress(
                progress_callback,
                search_progress,
                "search",
                f"正在抓取舆情：{index}/{searchable_entity_count}，当前主体：{entity_name}",
                completed_entities=index,
                total_entities=searchable_entity_count,
            )
            if index < searchable_entity_count:
                time.sleep(self.settings.request_delay_seconds)

        processor = NewsDataProcessor()
        try:
            self._emit_progress(
                progress_callback,
                74,
                "export_raw",
                "正在整理舆情数据并导出原始结果...",
                completed_entities=searchable_entity_count,
                total_entities=searchable_entity_count,
            )
            dataframe = processor.to_dataframe(news_items)
            data_file_path = processor.export_to_excel(dataframe, output_dir, run_time)
        except Exception as exc:
            raise RuntimeError(f"数据整理导出阶段失败（{type(exc).__name__}）：{exc}") from exc

        annotated_data_file_path: Path | None = None
        if input_path.is_file():
            try:
                self._emit_progress(
                    progress_callback,
                    82,
                    "export_annotated",
                    "正在把近一年舆情写回原始 Excel...",
                    completed_entities=searchable_entity_count,
                    total_entities=searchable_entity_count,
                )
                workbook_exporter = AnnotatedWorkbookExporter(
                    target_column_letter=self.settings.excel_target_column_letter,
                    news_limit=self.settings.annotated_excel_news_limit,
                )
                entity_news_map = workbook_exporter.build_entity_news_map(dataframe)
                annotated_data_file_path = workbook_exporter.export(
                    source_workbook_path=input_path,
                    output_dir=output_dir,
                    run_time=run_time,
                    entity_news_map=entity_news_map,
                )
            except Exception as exc:
                warning = f"写回舆情增强 Excel 失败：{exc}"
                warnings.append(warning)
                logger.exception(warning)
        else:
            warnings.append("当前输入为目录模式，已跳过“写回舆情后的 Excel”导出。上传单个 Excel 时可生成该文件。")

        try:
            self._emit_progress(
                progress_callback,
                90,
                "report",
                "正在生成 AI 深度分析报告...",
                completed_entities=searchable_entity_count,
                total_entities=searchable_entity_count,
            )
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
            self._emit_progress(
                progress_callback,
                97,
                "email",
                "正在发送邮件...",
                completed_entities=searchable_entity_count,
                total_entities=searchable_entity_count,
            )
            dispatcher.send_email(
                subject=build_email_subject(run_time),
                body=build_email_body(run_time, core_summary),
                attachments=[
                    path
                    for path in [data_file_path, report_file_path, annotated_data_file_path]
                    if path is not None
                ],
            )
            email_sent = True
        except Exception as exc:
            logger.exception("邮件发送失败，但已保留生成的数据与报告文件：%s", exc)

        self._emit_progress(
            progress_callback,
            100,
            "done",
            "执行完成，结果文件已生成。",
            completed_entities=searchable_entity_count,
            total_entities=searchable_entity_count,
        )
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
            annotated_data_file_path=annotated_data_file_path,
            email_sent=email_sent,
            searched_entity_count=searchable_entity_count,
            matched_entity_count=matched_entities,
            skipped_entity_count=len(skipped_entities),
            failed_entity_count=failed_entities,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _emit_progress(
        progress_callback: Callable[[PipelineProgress], None] | None,
        percent: int,
        stage: str,
        message: str,
        completed_entities: int = 0,
        total_entities: int = 0,
    ) -> None:
        if progress_callback is None:
            return
        progress_callback(
            PipelineProgress(
                percent=max(0, min(100, int(percent))),
                stage=stage,
                message=message,
                completed_entities=completed_entities,
                total_entities=total_entities,
            )
        )
