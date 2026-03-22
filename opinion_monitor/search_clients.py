from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

try:
    from tavily import TavilyClient
except ImportError:
    TavilyClient = None

from .config import Settings
from .models import NewsItem

try:
    from duckduckgo_search import DDGS
except ImportError:
    try:
        from ddgs import DDGS
    except ImportError:
        DDGS = None

logger = logging.getLogger(__name__)


class SearchClientError(Exception):
    """搜索客户端异常。"""


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_published_at(raw_value: str, end_time: datetime) -> datetime | None:
    text = _safe_text(raw_value)
    if not text:
        return None

    normalized = text.lower()
    relative_rules = [
        (r"(\d+)\s*minutes?\s*ago", "minutes"),
        (r"(\d+)\s*hours?\s*ago", "hours"),
        (r"(\d+)\s*days?\s*ago", "days"),
        (r"(\d+)\s*mins?\s*ago", "minutes"),
        (r"(\d+)\s*小时前", "hours"),
        (r"(\d+)\s*分钟[前内]", "minutes"),
        (r"(\d+)\s*天前", "days"),
    ]
    for pattern, unit in relative_rules:
        match = re.search(pattern, normalized)
        if match:
            value = int(match.group(1))
            return end_time - timedelta(**{unit: value})

    if normalized in {"yesterday", "昨天"}:
        return end_time - timedelta(days=1)
    if normalized in {"today", "今天"}:
        return end_time

    timestamp = pd.to_datetime(text, errors="coerce", utc=True)
    if pd.isna(timestamp):
        return None
    return timestamp.to_pydatetime()


def is_within_time_window(raw_value: str, start_time: datetime, end_time: datetime) -> bool:
    parsed_time = parse_published_at(raw_value, end_time)
    if parsed_time is None:
        return True
    return start_time <= parsed_time <= end_time


class BaseSearchClient(ABC):
    provider_name = "base"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()

    @abstractmethod
    def search(self, entity_name: str, start_time: datetime, end_time: datetime) -> list[NewsItem]:
        raise NotImplementedError

    def _build_item(
        self,
        entity_name: str,
        title: object,
        url: object,
        published_at: object,
        source: object,
        snippet: object,
    ) -> NewsItem:
        return NewsItem(
            entity_name=entity_name,
            title=_safe_text(title),
            url=_safe_text(url),
            published_at=_safe_text(published_at),
            source=_safe_text(source),
            snippet=_safe_text(snippet),
            provider=self.provider_name,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )


class BingNewsSearchClient(BaseSearchClient):
    provider_name = "bing"

    def search(self, entity_name: str, start_time: datetime, end_time: datetime) -> list[NewsItem]:
        if not self.settings.bing_subscription_key:
            raise SearchClientError("Bing News Search 未配置 BING_SUBSCRIPTION_KEY。")

        try:
            response = self.session.get(
                self.settings.bing_endpoint,
                headers={"Ocp-Apim-Subscription-Key": self.settings.bing_subscription_key},
                params={
                    "q": entity_name,
                    "count": self.settings.max_results_per_entity,
                    "freshness": "Day",
                    "sortBy": "Date",
                    "mkt": self.settings.bing_market,
                    "safeSearch": "Off",
                    "textFormat": "Raw",
                },
                timeout=self.settings.request_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise SearchClientError(f"Bing News Search 请求失败：{exc}") from exc

        items: list[NewsItem] = []
        for article in payload.get("value", []):
            published_at = _safe_text(article.get("datePublished"))
            if not is_within_time_window(published_at, start_time, end_time):
                continue

            providers = article.get("provider") or []
            source = ""
            if providers and isinstance(providers[0], dict):
                source = providers[0].get("name", "")

            items.append(
                self._build_item(
                    entity_name=entity_name,
                    title=article.get("name"),
                    url=article.get("url"),
                    published_at=published_at,
                    source=source,
                    snippet=article.get("description"),
                )
            )

        return items


class SerpApiSearchClient(BaseSearchClient):
    provider_name = "serpapi"

    def search(self, entity_name: str, start_time: datetime, end_time: datetime) -> list[NewsItem]:
        if not self.settings.serpapi_api_key:
            raise SearchClientError("SerpAPI 未配置 SERPAPI_API_KEY。")

        try:
            response = self.session.get(
                self.settings.serpapi_endpoint,
                params={
                    "engine": "google",
                    "q": entity_name,
                    "tbm": "nws",
                    "api_key": self.settings.serpapi_api_key,
                    "num": self.settings.max_results_per_entity,
                    "tbs": "qdr:d",
                    "gl": self.settings.serpapi_gl,
                    "hl": self.settings.serpapi_hl,
                },
                timeout=self.settings.request_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise SearchClientError(f"SerpAPI 请求失败：{exc}") from exc

        items: list[NewsItem] = []
        for article in payload.get("news_results", []):
            published_at = _safe_text(article.get("date"))
            if not is_within_time_window(published_at, start_time, end_time):
                continue

            items.append(
                self._build_item(
                    entity_name=entity_name,
                    title=article.get("title"),
                    url=article.get("link"),
                    published_at=published_at,
                    source=article.get("source"),
                    snippet=article.get("snippet"),
                )
            )

        return items


class DuckDuckGoNewsSearchClient(BaseSearchClient):
    provider_name = "duckduckgo"

    def search(self, entity_name: str, start_time: datetime, end_time: datetime) -> list[NewsItem]:
        if DDGS is None:
            raise SearchClientError(
                "未安装 duckduckgo-search 或 ddgs，无法使用 DuckDuckGo 新闻搜索。"
            )

        try:
            ddgs = DDGS()
            results = ddgs.news(
                keywords=entity_name,
                region=self.settings.ddg_region,
                safesearch="off",
                timelimit="d",
                max_results=self.settings.max_results_per_entity,
            ) or []
        except Exception as exc:
            raise SearchClientError(f"DuckDuckGo 搜索失败：{exc}") from exc

        items: list[NewsItem] = []
        for article in results:
            published_at = _safe_text(article.get("date"))
            if not is_within_time_window(published_at, start_time, end_time):
                continue

            items.append(
                self._build_item(
                    entity_name=entity_name,
                    title=article.get("title"),
                    url=article.get("url"),
                    published_at=published_at,
                    source=article.get("source"),
                    snippet=article.get("body"),
                )
            )

        return items


class TavilyNewsSearchClient(BaseSearchClient):
    provider_name = "tavily"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        if TavilyClient is None:
            raise SearchClientError("未安装 tavily-python，无法使用 Tavily 搜索。")
        if not settings.tavily_api_key:
            raise SearchClientError("Tavily 未配置 TAVILY_API_KEY。")
        self.client = TavilyClient(api_key=settings.tavily_api_key)

    def search(self, entity_name: str, start_time: datetime, end_time: datetime) -> list[NewsItem]:
        try:
            response = self.client.search(
                query=f'"{entity_name}" 相关新闻 舆情 风险 动态',
                topic=self.settings.tavily_topic,
                time_range=self.settings.tavily_time_range,
                start_date=start_time.astimezone(timezone.utc).strftime("%Y-%m-%d"),
                end_date=end_time.astimezone(timezone.utc).strftime("%Y-%m-%d"),
                max_results=min(self.settings.max_results_per_entity, 20),
                search_depth=self.settings.tavily_search_depth,
                include_raw_content=self.settings.tavily_include_raw_content,
                chunks_per_source=self.settings.tavily_chunks_per_source,
                include_answer=False,
                include_images=False,
            )
        except Exception as exc:
            raise SearchClientError(f"Tavily 搜索失败：{exc}") from exc

        items: list[NewsItem] = []
        for article in response.get("results", []):
            published_at = _safe_text(article.get("published_date"))
            if published_at and not is_within_time_window(published_at, start_time, end_time):
                continue

            snippet = _safe_text(article.get("content") or article.get("raw_content"))
            source = self._extract_domain(article.get("url"))
            items.append(
                self._build_item(
                    entity_name=entity_name,
                    title=article.get("title"),
                    url=article.get("url"),
                    published_at=published_at,
                    source=source,
                    snippet=snippet,
                )
            )
        return items

    @staticmethod
    def _extract_domain(url: object) -> str:
        text = _safe_text(url)
        if not text:
            return ""
        match = re.match(r"https?://([^/]+)", text)
        return match.group(1) if match else ""


def build_search_client(settings: Settings) -> BaseSearchClient:
    provider = settings.search_provider.lower()
    if provider == "tavily":
        return TavilyNewsSearchClient(settings)
    if provider == "bing":
        return BingNewsSearchClient(settings)
    if provider == "serpapi":
        return SerpApiSearchClient(settings)
    if provider in {"duckduckgo", "ddg"}:
        return DuckDuckGoNewsSearchClient(settings)
    raise SearchClientError(f"不支持的搜索提供商：{settings.search_provider}")
