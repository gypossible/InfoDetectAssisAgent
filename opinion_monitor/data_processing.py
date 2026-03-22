from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .models import NewsItem

NEGATIVE_KEYWORDS = [
    "处罚",
    "违规",
    "投诉",
    "维权",
    "爆雷",
    "亏损",
    "裁员",
    "事故",
    "风险",
    "下架",
    "调查",
    "纠纷",
    "质疑",
    "舆情危机",
    "故障",
    "被执行",
    "冻结",
    "违约",
    "举报",
    "负面",
]

POSITIVE_KEYWORDS = [
    "增长",
    "合作",
    "中标",
    "获奖",
    "回暖",
    "创新",
    "突破",
    "发布",
    "升级",
    "盈利",
]

DATAFRAME_COLUMNS = [
    "entity_name",
    "title",
    "url",
    "published_at",
    "source",
    "snippet",
    "provider",
    "fetched_at",
    "sentiment_hint",
    "risk_keywords",
    "published_at_dt",
    "content_text",
]


class NewsDataProcessor:
    @staticmethod
    def _clean_text(value: object) -> str:
        if value is None:
            return ""
        return str(value).replace("\u3000", " ").strip()

    @staticmethod
    def _extract_negative_keywords(text: str) -> str:
        matched = [keyword for keyword in NEGATIVE_KEYWORDS if keyword in text]
        return "、".join(matched)

    @staticmethod
    def _infer_sentiment(text: str) -> str:
        negative_score = sum(keyword in text for keyword in NEGATIVE_KEYWORDS)
        positive_score = sum(keyword in text for keyword in POSITIVE_KEYWORDS)
        if negative_score > positive_score:
            return "negative"
        if positive_score > negative_score:
            return "positive"
        return "neutral"

    def to_dataframe(self, news_items: list[NewsItem]) -> pd.DataFrame:
        if not news_items:
            return pd.DataFrame(columns=DATAFRAME_COLUMNS)

        dataframe = pd.DataFrame([item.to_record() for item in news_items])
        for column in ["entity_name", "title", "url", "published_at", "source", "snippet", "provider", "fetched_at"]:
            dataframe[column] = dataframe[column].map(self._clean_text)

        dataframe["content_text"] = (
            dataframe["title"].fillna("") + " " + dataframe["snippet"].fillna("")
        ).str.replace(r"\s+", " ", regex=True).str.strip()
        dataframe["risk_keywords"] = dataframe["content_text"].map(self._extract_negative_keywords)
        dataframe["sentiment_hint"] = dataframe["content_text"].map(self._infer_sentiment)
        dataframe["published_at_dt"] = pd.to_datetime(
            dataframe["published_at"], errors="coerce", utc=True
        )

        dataframe = dataframe.drop_duplicates(subset=["entity_name", "title", "url"])
        dataframe = dataframe.sort_values(
            by=["published_at_dt", "entity_name"],
            ascending=[False, True],
            na_position="last",
        )
        return dataframe.reset_index(drop=True)

    def export_to_excel(self, dataframe: pd.DataFrame, output_dir: Path, run_time: datetime) -> Path:
        output_path = output_dir / f"舆情原始数据_{run_time.strftime('%Y%m%d')}.xlsx"

        export_columns = [
            "entity_name",
            "title",
            "url",
            "published_at",
            "source",
            "snippet",
            "provider",
            "fetched_at",
            "sentiment_hint",
            "risk_keywords",
        ]
        export_dataframe = dataframe.reindex(columns=export_columns).rename(
            columns={
                "entity_name": "主体名称",
                "title": "标题",
                "url": "链接",
                "published_at": "发布时间",
                "source": "来源",
                "snippet": "摘要",
                "provider": "搜索渠道",
                "fetched_at": "抓取时间",
                "sentiment_hint": "情绪标签",
                "risk_keywords": "风险关键词",
            }
        )
        export_dataframe.to_excel(output_path, index=False, engine="openpyxl")
        return output_path
