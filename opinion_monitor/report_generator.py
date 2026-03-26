from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from .config import Settings

logger = logging.getLogger(__name__)

REQUIRED_SECTIONS = ["核心摘要", "近一年舆情综述", "重点主体异动分析", "潜在风险预警", "应对建议"]


class ReportGenerationError(Exception):
    """报告生成异常。"""


def _trim_text(text: str, max_length: int = 140) -> str:
    clean_text = re.sub(r"\s+", " ", str(text)).strip()
    if len(clean_text) <= max_length:
        return clean_text
    return clean_text[: max_length - 3] + "..."


def extract_core_summary(report_markdown: str, max_chars: int = 220) -> str:
    match = re.search(r"#+\s*核心摘要\s*(.*?)(?=\n#+\s|\Z)", report_markdown, flags=re.S)
    if match:
        summary = re.sub(r"\s+", " ", match.group(1)).strip()
    else:
        paragraphs = [line.strip() for line in report_markdown.splitlines() if line.strip() and not line.startswith("#")]
        summary = paragraphs[0] if paragraphs else "今日舆情整体平稳，详细情况请见附件完整报告。"

    if len(summary) <= max_chars:
        return summary
    return summary[: max_chars - 3].rstrip("，、；： ") + "..."


class LLMReportGenerator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = self._normalize_base_url(settings.openai_base_url)
        self.session = requests.Session()

    def generate_report(
        self,
        dataframe: pd.DataFrame,
        run_time: datetime,
        start_time: datetime,
        end_time: datetime,
        output_dir: Path,
    ) -> tuple[Path, str]:
        if dataframe.empty:
            report_markdown = self._build_empty_report(run_time, start_time, end_time)
        else:
            if not self.settings.openai_api_key:
                raise ReportGenerationError("未配置 OPENAI_API_KEY，无法生成 AI 舆情分析报告。")
            report_markdown = self._generate_with_llm(dataframe, run_time, start_time, end_time)

        report_path = output_dir / f"每日舆情分析报告_{run_time.strftime('%Y%m%d')}.md"
        report_path.write_text(report_markdown, encoding="utf-8")
        summary = extract_core_summary(
            report_markdown,
            max_chars=self.settings.email_summary_char_limit,
        )
        return report_path, summary

    def _generate_with_llm(
        self,
        dataframe: pd.DataFrame,
        run_time: datetime,
        start_time: datetime,
        end_time: datetime,
    ) -> str:
        context = self._build_context(dataframe, run_time, start_time, end_time)

        instructions = (
            "你是一名高级舆情分析师，擅长对新闻、行业事件和社交讨论进行归纳、分层和风险研判。"
            "请仅基于输入数据进行分析，不要杜撰事实；若样本中信息不足，请明确指出。"
            "输出必须使用中文 Markdown。"
        )
        user_input = (
            "请基于下方 JSON 舆情数据，生成一份不少于 1500 字的深度舆情分析报告。"
            "请务必包含并按以下顺序输出：\n"
            "1. 一级标题：每日舆情分析报告\n"
            "2. 二级标题：核心摘要（约 180-220 字，便于直接放入邮件正文）\n"
            "3. 二级标题：近一年舆情综述\n"
            "4. 二级标题：重点主体异动分析\n"
            "5. 二级标题：潜在风险预警\n"
            "6. 二级标题：应对建议\n"
            "要求：\n"
            "- 核心摘要需要概括整体舆情温度、主要热点与风险级别。\n"
            "- 重点主体异动分析请结合声量、情绪标签、风险关键词与代表性事件展开。\n"
            "- 潜在风险预警请区分短期、中期风险，并说明可能的传播触发点。\n"
            "- 应对建议请尽量给出可执行动作。\n"
            "- 避免模板化空话，优先引用样本中的主体、标题、来源和现象。\n\n"
            f"舆情数据如下：\n```json\n{context}\n```"
        )

        first_pass = self._request_report(instructions=instructions, user_input=user_input)
        if self._needs_retry(first_pass):
            logger.warning("首次生成的报告长度或结构不满足要求，正在尝试扩展补全。")
            retry_input = (
                "请在不改变事实基础的前提下，扩展并修复下面这份 Markdown 报告，"
                "确保不少于 1500 字，且完整包含：核心摘要、近一年舆情综述、重点主体异动分析、潜在风险预警、应对建议。\n\n"
                f"{first_pass}"
            )
            second_pass = self._request_report(instructions=instructions, user_input=retry_input)
            if self._needs_retry(second_pass):
                logger.warning("二次生成的报告仍未完全满足长度或结构要求，将返回二次结果供人工复核。")
            return second_pass
        return first_pass

    def _request_report(self, instructions: str, user_input: str) -> str:
        if self._uses_chat_completions_api():
            return self._request_chat_completion(instructions, user_input)
        return self._request_responses_api(instructions, user_input)

    def _request_responses_api(self, instructions: str, user_input: str) -> str:
        payload = {
            "model": self.settings.llm_model,
            "instructions": instructions,
            "input": user_input,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            response = self.session.post(
                f"{self.base_url}/responses",
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=180,
            )
        except requests.RequestException as exc:
            raise ReportGenerationError(f"调用大模型接口失败：{exc}") from exc

        if not response.ok:
            error_text = response.text.strip()
            raise ReportGenerationError(
                f"大模型接口返回异常（HTTP {response.status_code}）：{error_text[:500]}"
            )

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise ReportGenerationError("大模型接口返回的不是合法 JSON。") from exc

        output_text = str(response_payload.get("output_text") or "").strip()
        if output_text:
            return output_text

        text_fragments: list[str] = []
        for item in response_payload.get("output", []) or []:
            for content in item.get("content", []) or []:
                text_value = content.get("text")
                if text_value:
                    text_fragments.append(str(text_value).strip())

        report_text = "\n".join(fragment for fragment in text_fragments if fragment).strip()
        if not report_text:
            raise ReportGenerationError("模型返回为空，无法生成舆情分析报告。")
        return report_text

    def _request_chat_completion(self, instructions: str, user_input: str) -> str:
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_input},
            ],
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=180,
            )
        except requests.RequestException as exc:
            raise ReportGenerationError(f"调用大模型接口失败：{exc}") from exc

        if not response.ok:
            error_text = response.text.strip()
            raise ReportGenerationError(
                f"大模型接口返回异常（HTTP {response.status_code}）：{error_text[:500]}"
            )

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise ReportGenerationError("大模型接口返回的不是合法 JSON。") from exc

        choices = response_payload.get("choices") or []
        if not choices:
            raise ReportGenerationError("模型未返回有效 choices，无法生成舆情分析报告。")

        message = choices[0].get("message") or {}
        content = str(message.get("content") or "").strip()
        if not content:
            raise ReportGenerationError("模型返回为空，无法生成舆情分析报告。")
        return content

    def _uses_chat_completions_api(self) -> bool:
        model_name = (self.settings.llm_model or "").strip().lower()
        return "deepseek" in self.base_url.lower() or model_name.startswith("deepseek-")

    @staticmethod
    def _normalize_base_url(raw_base_url: str) -> str:
        base_url = (raw_base_url or "").strip()
        if not base_url:
            return "https://api.openai.com/v1"
        if not base_url.startswith(("http://", "https://")):
            raise ReportGenerationError("OPENAI_BASE_URL 配置不合法，必须以 http:// 或 https:// 开头。")
        return base_url.rstrip("/")

    @staticmethod
    def _needs_retry(report_markdown: str) -> bool:
        plain_text = re.sub(r"[#*`>\-\n\r]", "", report_markdown)
        missing_sections = [section for section in REQUIRED_SECTIONS if section not in report_markdown]
        return len(plain_text) < 1500 or bool(missing_sections)

    def _build_context(
        self,
        dataframe: pd.DataFrame,
        run_time: datetime,
        start_time: datetime,
        end_time: datetime,
    ) -> str:
        working_df = dataframe.copy()
        if "published_at_dt" in working_df.columns:
            working_df = working_df.sort_values(
                by=["published_at_dt", "entity_name"],
                ascending=[False, True],
                na_position="last",
            )

        negative_df = working_df[working_df["sentiment_hint"] == "negative"].head(
            max(10, self.settings.llm_max_articles_for_prompt // 2)
        )
        remaining_df = working_df.drop(index=negative_df.index, errors="ignore")
        other_df = remaining_df.head(
            max(0, self.settings.llm_max_articles_for_prompt - len(negative_df))
        )
        prompt_df = (
            pd.concat([negative_df, other_df], ignore_index=True)
            .drop_duplicates(subset=["entity_name", "title", "url"])
            .head(self.settings.llm_max_articles_for_prompt)
        )

        entity_stats: list[dict[str, object]] = []
        for entity_name, group in working_df.groupby("entity_name"):
            top_titles = group["title"].head(3).tolist()
            risk_keywords = sorted(
                {
                    keyword.strip()
                    for value in group["risk_keywords"].fillna("")
                    for keyword in str(value).split("、")
                    if keyword.strip()
                }
            )
            entity_stats.append(
                {
                    "entity_name": entity_name,
                    "article_count": int(len(group)),
                    "negative_count": int((group["sentiment_hint"] == "negative").sum()),
                    "positive_count": int((group["sentiment_hint"] == "positive").sum()),
                    "neutral_count": int((group["sentiment_hint"] == "neutral").sum()),
                    "top_titles": [_trim_text(title, 80) for title in top_titles],
                    "risk_keywords": risk_keywords[:10],
                    "top_sources": group["source"].fillna("").replace("", "未知来源").value_counts().head(5).index.tolist(),
                }
            )

        entity_stats.sort(
            key=lambda item: (int(item["article_count"]), int(item["negative_count"])),
            reverse=True,
        )

        source_distribution = (
            working_df["source"]
            .fillna("")
            .replace("", "未知来源")
            .value_counts()
            .head(10)
            .to_dict()
        )
        sentiment_distribution = working_df["sentiment_hint"].value_counts().to_dict()

        sample_articles: list[dict[str, object]] = []
        for row in prompt_df.itertuples(index=False):
            sample_articles.append(
                {
                    "entity_name": row.entity_name,
                    "title": _trim_text(row.title, 120),
                    "published_at": row.published_at,
                    "source": row.source or "未知来源",
                    "snippet": _trim_text(row.snippet, 160),
                    "url": row.url,
                    "sentiment_hint": row.sentiment_hint,
                    "risk_keywords": row.risk_keywords,
                }
            )

        context = {
            "report_date": run_time.strftime("%Y-%m-%d"),
            "time_window": {
                "start": start_time.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
                "end": end_time.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
            },
            "overall_stats": {
                "entity_count": int(working_df["entity_name"].nunique()),
                "article_count": int(len(working_df)),
                "negative_article_count": int((working_df["sentiment_hint"] == "negative").sum()),
                "source_distribution": {str(key): int(value) for key, value in source_distribution.items()},
                "sentiment_distribution": {str(key): int(value) for key, value in sentiment_distribution.items()},
            },
            "focus_entities": entity_stats[:15],
            "sample_articles": sample_articles,
        }
        return json.dumps(context, ensure_ascii=False, indent=2)

    @staticmethod
    def _build_empty_report(run_time: datetime, start_time: datetime, end_time: datetime) -> str:
        start_text = start_time.astimezone().strftime("%Y-%m-%d %H:%M")
        end_text = end_time.astimezone().strftime("%Y-%m-%d %H:%M")
        return (
            f"# 每日舆情分析报告（{run_time.strftime('%Y-%m-%d')}，过去一年窗口）\n\n"
            "## 核心摘要\n"
            "过去一年内，监测名单未检索到明确的新增新闻舆情样本，整体舆情保持平稳。"
            "当前更适合继续关注主体是否出现突发事件、监管动态或集中讨论的苗头，并维持常规预警节奏。\n\n"
            "## 近一年舆情综述\n"
            f"本次监测时间窗为 {start_text} 至 {end_text}。在当前检索条件下，未发现与监测主体直接相关且可结构化落库的新增舆情。"
            "这通常意味着舆情热度较低，或者公开可抓取新闻信息较少。\n\n"
            "## 重点主体异动分析\n"
            "由于没有形成有效样本，本期未识别出声量显著上升或负面情绪突出的重点主体。建议继续结合业务台账、客服投诉、社交媒体口碑等内部外部数据源交叉验证。\n\n"
            "## 潜在风险预警\n"
            "整体风险相对可控，但仍需警惕突发事故、监管处罚、产品质量争议、经营传闻等事件导致舆情快速放大。"
            "若后续出现集中转载、意见领袖点评或行业类账号跟进，可能在较短时间内形成传播峰值。\n\n"
            "## 应对建议\n"
            "建议维持每日固定时点监测，完善重点主体关键词同义词库，并在出现负面苗头时快速启动核实、口径统一、回应分发和复盘机制。"
        )
