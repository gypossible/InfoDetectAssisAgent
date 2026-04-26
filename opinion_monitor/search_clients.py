from __future__ import annotations

import logging
import hashlib
import re
import time
import warnings
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

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


class QccRegionBlockedError(SearchClientError):
    """企查查因出口 IP 区域限制拒绝请求。"""


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _pick_first(data: dict | None, *keys: str) -> object:
    if not isinstance(data, dict):
        return None
    for key in keys:
        if key in data and data.get(key) not in {None, ""}:
            return data.get(key)
    return None


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
        return False
    return start_time <= parsed_time <= end_time


def _normalize_match_text(value: object) -> str:
    text = _safe_text(value)
    if not text:
        return ""
    normalized = (
        text.replace("\u3000", "")
        .replace("（", "")
        .replace("）", "")
        .replace("(", "")
        .replace(")", "")
        .replace("【", "")
        .replace("】", "")
        .replace("[", "")
        .replace("]", "")
        .replace("《", "")
        .replace("》", "")
        .replace('"', "")
        .replace("'", "")
    )
    normalized = re.sub(r"\s+", "", normalized)
    return normalized.casefold()


def _contains_entity(entity_name: str, *values: object) -> bool:
    target = _normalize_match_text(entity_name)
    if not target:
        return False
    combined = "".join(_normalize_match_text(value) for value in values if _safe_text(value))
    return target in combined


def _is_retryable_error(exc: Exception) -> bool:
    message = str(exc).lower()
    retryable_markers = (
        "excessive requests",
        "rate limit",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "connection",
        "server error",
        "502",
        "503",
        "504",
    )
    return any(marker in message for marker in retryable_markers)


def _is_qcc_region_block(status: object, message: object) -> bool:
    status_text = _safe_text(status)
    message_text = _safe_text(message)
    region_markers = (
        "数据不能出境",
        "暂不支持境外ip请求",
        "暂不支持境外 IP 请求",
        "境外ip",
        "境外 IP",
    )
    if status_text in {"121", "100002"}:
        return True
    return any(marker.lower() in message_text.lower() for marker in region_markers)


def _bing_freshness(lookback_days: int) -> str | None:
    if lookback_days <= 1:
        return "Day"
    if lookback_days <= 7:
        return "Week"
    if lookback_days <= 31:
        return "Month"
    return None


def _serpapi_tbs(lookback_days: int) -> str | None:
    if lookback_days <= 1:
        return "qdr:d"
    if lookback_days <= 7:
        return "qdr:w"
    if lookback_days <= 31:
        return "qdr:m"
    return None


def _ddg_timelimit(lookback_days: int) -> str | None:
    if lookback_days <= 1:
        return "d"
    if lookback_days <= 7:
        return "w"
    if lookback_days <= 31:
        return "m"
    return None


def _tavily_time_range(lookback_days: int, configured_value: str) -> str | None:
    if lookback_days <= 1:
        return "day"
    if lookback_days <= 7:
        return "week"
    if lookback_days <= 31:
        return "month"
    if lookback_days <= 366:
        return "year"
    return configured_value or None


def _normalize_domain(value: object) -> str:
    text = _safe_text(value).lower().strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlparse(text)
    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname


def _matches_domain(hostname: str, candidate: str) -> bool:
    normalized_candidate = _normalize_domain(candidate)
    if not hostname or not normalized_candidate:
        return False
    return hostname == normalized_candidate or hostname.endswith(f".{normalized_candidate}")


def _is_mainland_domain(hostname: str, settings: Settings) -> bool:
    if not hostname:
        return False
    if any(_matches_domain(hostname, domain) for domain in settings.mainland_source_domains):
        return True
    mainland_suffixes = (".gov.cn", ".com.cn", ".net.cn", ".org.cn", ".cn")
    return hostname.endswith(mainland_suffixes)


def _is_mainland_news_item(item: NewsItem, settings: Settings) -> bool:
    return _is_mainland_domain(_normalize_domain(item.url or item.source), settings)


def _sort_mainland_first(items: list[NewsItem], settings: Settings) -> list[NewsItem]:
    return sorted(
        items,
        key=lambda item: (
            not _is_mainland_news_item(item, settings),
            item.published_at,
            item.title,
        ),
        reverse=False,
    )


def _dedupe_news_items(items: list[NewsItem]) -> list[NewsItem]:
    unique_items: list[NewsItem] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        dedupe_key = (
            item.entity_name.strip().casefold(),
            item.title.strip().casefold(),
            item.url.strip().casefold(),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        unique_items.append(item)
    return unique_items


def _flatten_qcc_records(value: object) -> list[dict]:
    if isinstance(value, list):
        records: list[dict] = []
        for item in value:
            records.extend(_flatten_qcc_records(item))
        return records
    if isinstance(value, dict):
        nested_keys = (
            "Result",
            "Items",
            "Data",
            "List",
            "NewsList",
            "News",
            "Rows",
        )
        for key in nested_keys:
            nested_value = value.get(key)
            if isinstance(nested_value, (list, dict)):
                nested_records = _flatten_qcc_records(nested_value)
                if nested_records:
                    return nested_records
        if _pick_first(
            value,
            "Title",
            "NewsTitle",
            "Name",
            "Url",
            "NewsUrl",
            "Link",
        ):
            return [value]
    return []


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

    def is_available(self) -> tuple[bool, str]:
        return True, ""


class BingNewsSearchClient(BaseSearchClient):
    provider_name = "bing"

    def search(self, entity_name: str, start_time: datetime, end_time: datetime) -> list[NewsItem]:
        if not self.settings.bing_subscription_key:
            raise SearchClientError("Bing News Search 未配置 BING_SUBSCRIPTION_KEY。")

        params = {
            "q": entity_name,
            "count": self.settings.max_results_per_entity,
            "sortBy": "Date",
            "mkt": self.settings.bing_market,
            "safeSearch": "Off",
            "textFormat": "Raw",
        }
        freshness = _bing_freshness(self.settings.search_lookback_days)
        if freshness:
            params["freshness"] = freshness

        try:
            response = self.session.get(
                self.settings.bing_endpoint,
                headers={"Ocp-Apim-Subscription-Key": self.settings.bing_subscription_key},
                params=params,
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
            if not _contains_entity(
                entity_name,
                article.get("name"),
                article.get("description"),
                article.get("url"),
            ):
                continue

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

        params = {
            "engine": "google",
            "q": entity_name,
            "tbm": "nws",
            "api_key": self.settings.serpapi_api_key,
            "num": self.settings.max_results_per_entity,
            "gl": self.settings.serpapi_gl,
            "hl": self.settings.serpapi_hl,
        }
        tbs = _serpapi_tbs(self.settings.search_lookback_days)
        if tbs:
            params["tbs"] = tbs

        try:
            response = self.session.get(
                self.settings.serpapi_endpoint,
                params=params,
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
            if not _contains_entity(
                entity_name,
                article.get("title"),
                article.get("snippet"),
                article.get("link"),
            ):
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
            original_warn = warnings.warn
            with warnings.catch_warnings():
                warnings.warn = lambda *args, **kwargs: None
                ddgs = DDGS()
                results = ddgs.news(
                    keywords=entity_name,
                    region=self.settings.ddg_region,
                    safesearch="off",
                    timelimit=_ddg_timelimit(self.settings.search_lookback_days),
                    max_results=self.settings.max_results_per_entity,
                ) or []
            warnings.warn = original_warn
        except Exception as exc:
            warnings.warn = original_warn
            raise SearchClientError(f"DuckDuckGo 搜索失败：{exc}") from exc

        items: list[NewsItem] = []
        for article in results:
            published_at = _safe_text(article.get("date"))
            if not is_within_time_window(published_at, start_time, end_time):
                continue
            if not _contains_entity(
                entity_name,
                article.get("title"),
                article.get("body"),
                article.get("url"),
            ):
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
        items = _sort_mainland_first(items, self.settings)
        if self.settings.mainland_source_mode == "only":
            return [item for item in items if _is_mainland_news_item(item, self.settings)]
        return items


class QccNewsSearchClient(BaseSearchClient):
    provider_name = "qcc"

    def is_available(self) -> tuple[bool, str]:
        if not self.settings.qcc_app_key:
            return (
                False,
                "企查查新闻接口未配置 QCC_APP_KEY。企查查官方开放平台鉴权需要 APPKEY 与 SecretKey。",
            )
        if not self.settings.qcc_secret_key:
            return (
                False,
                "企查查新闻接口未配置 QCC_SECRET_KEY。企查查官方开放平台鉴权需要 APPKEY 与 SecretKey。",
            )
        return True, ""

    def _build_signature_headers(self) -> dict[str, str]:
        timespan = str(int(time.time()))
        raw = (
            f"{self.settings.qcc_app_key}{timespan}{self.settings.qcc_secret_key}"
        ).encode("utf-8")
        token = hashlib.md5(raw).hexdigest().upper()
        return {"Token": token, "Timespan": timespan}

    def _extract_items_from_payload(
        self,
        payload: dict,
        entity_name: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[NewsItem]:
        raw_records = _flatten_qcc_records(payload.get("Result") or payload)
        items: list[NewsItem] = []
        for article in raw_records:
            published_at = _safe_text(
                _pick_first(
                    article,
                    "PublishDate",
                    "PubDate",
                    "Date",
                    "PublishTime",
                    "PubTime",
                    "NewsDate",
                )
            )
            if published_at and not is_within_time_window(published_at, start_time, end_time):
                continue

            title = _pick_first(article, "Title", "NewsTitle", "Name")
            url = _pick_first(article, "Url", "NewsUrl", "Link")
            source = _pick_first(article, "Source", "SourceName", "Media", "SiteName")
            snippet = _pick_first(
                article,
                "Abstract",
                "Summary",
                "Content",
                "Excerpt",
                "Brief",
                "Desc",
            )
            if not _contains_entity(entity_name, title, snippet, url, source):
                continue

            items.append(
                self._build_item(
                    entity_name=entity_name,
                    title=title,
                    url=url,
                    published_at=published_at,
                    source=source,
                    snippet=snippet,
                )
            )
        items = _dedupe_news_items(items)
        items = _sort_mainland_first(items, self.settings)
        if self.settings.mainland_source_mode == "only":
            return [item for item in items if _is_mainland_news_item(item, self.settings)]
        return items

    def search(self, entity_name: str, start_time: datetime, end_time: datetime) -> list[NewsItem]:
        available, reason = self.is_available()
        if not available:
            raise SearchClientError(reason)

        params = {
            "key": self.settings.qcc_app_key,
            "searchKey": entity_name,
            "pageIndex": 1,
            "pageSize": max(1, min(self.settings.qcc_page_size, self.settings.max_results_per_entity, 20)),
            "startDate": start_time.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            "endDate": end_time.astimezone(timezone.utc).strftime("%Y-%m-%d"),
        }

        last_error: Exception | None = None
        for attempt in range(self.settings.request_retry_attempts + 1):
            try:
                response = self.session.get(
                    self.settings.qcc_news_endpoint,
                    headers=self._build_signature_headers(),
                    params=params,
                    timeout=self.settings.request_timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                status = _safe_text(payload.get("Status"))
                if status and status != "200":
                    message = _safe_text(payload.get("Message")) or "企查查接口返回异常。"
                    if _is_qcc_region_block(status, message):
                        raise QccRegionBlockedError(f"企查查新闻接口区域限制 {status}：{message}")
                    raise SearchClientError(f"企查查新闻接口返回 {status}：{message}")
                items = self._extract_items_from_payload(payload, entity_name, start_time, end_time)
                return items[: self.settings.max_results_per_entity]
            except SearchClientError as exc:
                last_error = exc
                if attempt >= self.settings.request_retry_attempts or not _is_retryable_error(exc):
                    raise
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.settings.request_retry_attempts or not _is_retryable_error(exc):
                    raise SearchClientError(f"企查查新闻接口请求失败：{exc}") from exc
            except ValueError as exc:
                raise SearchClientError(f"企查查新闻接口返回了无法解析的 JSON：{exc}") from exc

            sleep_seconds = min(
                self.settings.request_retry_backoff_seconds * (2**attempt),
                self.settings.request_retry_backoff_max_seconds,
            )
            logger.warning(
                "企查查新闻接口触发临时错误，主体 %s 将在 %.1f 秒后重试 [%s/%s]：%s",
                entity_name,
                sleep_seconds,
                attempt + 1,
                self.settings.request_retry_attempts,
                last_error,
            )
            time.sleep(sleep_seconds)

        raise SearchClientError(f"企查查新闻接口请求失败：{last_error}")


class TavilyNewsSearchClient(BaseSearchClient):
    provider_name = "tavily"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        if TavilyClient is None:
            raise SearchClientError("未安装 tavily-python，无法使用 Tavily 搜索。")
        if not settings.tavily_api_key:
            raise SearchClientError("Tavily 未配置 TAVILY_API_KEY。")
        self.client = TavilyClient(api_key=settings.tavily_api_key)
        self.fallback_client = DuckDuckGoNewsSearchClient(settings) if DDGS is not None else None

    def _request_tavily(self, entity_name: str, request_kwargs: dict[str, object]) -> dict:
        last_error: Exception | None = None
        for attempt in range(self.settings.request_retry_attempts + 1):
            try:
                return self.client.search(**request_kwargs)
            except Exception as exc:
                last_error = exc
                if attempt >= self.settings.request_retry_attempts or not _is_retryable_error(exc):
                    raise SearchClientError(f"Tavily 搜索失败：{exc}") from exc

                sleep_seconds = min(
                    self.settings.request_retry_backoff_seconds * (2**attempt),
                    self.settings.request_retry_backoff_max_seconds,
                )
                logger.warning(
                    "Tavily 搜索触发限流或临时错误，主体 %s 将在 %.1f 秒后重试 [%s/%s]：%s",
                    entity_name,
                    sleep_seconds,
                    attempt + 1,
                    self.settings.request_retry_attempts,
                    exc,
                )
                time.sleep(sleep_seconds)
        raise SearchClientError(f"Tavily 搜索失败：{last_error}")

    def _build_items_from_response(
        self,
        entity_name: str,
        response: dict,
        start_time: datetime,
        end_time: datetime,
    ) -> list[NewsItem]:
        items: list[NewsItem] = []
        for article in response.get("results", []):
            published_at = _safe_text(article.get("published_date"))
            if not is_within_time_window(published_at, start_time, end_time):
                continue

            snippet = _safe_text(article.get("content") or article.get("raw_content"))
            if not _contains_entity(
                entity_name,
                article.get("title"),
                snippet,
                article.get("url"),
            ):
                continue

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

    def search(self, entity_name: str, start_time: datetime, end_time: datetime) -> list[NewsItem]:
        request_kwargs = {
            "query": entity_name,
            "topic": self.settings.tavily_topic,
            "max_results": min(self.settings.max_results_per_entity, 20),
            "search_depth": self.settings.tavily_search_depth,
            "include_raw_content": self.settings.tavily_include_raw_content,
            "chunks_per_source": self.settings.tavily_chunks_per_source,
            "include_answer": False,
            "include_images": False,
        }
        tavily_time_range = _tavily_time_range(
            self.settings.search_lookback_days,
            self.settings.tavily_time_range,
        )
        if tavily_time_range:
            request_kwargs["time_range"] = tavily_time_range
        else:
            request_kwargs["start_date"] = start_time.astimezone(timezone.utc).strftime("%Y-%m-%d")
            request_kwargs["end_date"] = end_time.astimezone(timezone.utc).strftime("%Y-%m-%d")

        for attempt in range(self.settings.request_retry_attempts + 1):
            try:
                response = self.client.search(**request_kwargs)
                break
            except Exception as exc:
                if attempt >= self.settings.request_retry_attempts or not _is_retryable_error(exc):
                    if self.fallback_client is not None:
                        logger.warning(
                            "主体 %s 的 Tavily 搜索失败，改用 DuckDuckGo 兜底：%s",
                            entity_name,
                            exc,
                        )
                        fallback_items = self.fallback_client.search(entity_name, start_time, end_time)
                        if self.settings.mainland_source_mode == "only":
                            return [
                                item for item in fallback_items if _is_mainland_news_item(item, self.settings)
                            ]
                        return fallback_items
                    raise SearchClientError(f"Tavily 搜索失败：{exc}") from exc

                sleep_seconds = min(
                    self.settings.request_retry_backoff_seconds * (2**attempt),
                    self.settings.request_retry_backoff_max_seconds,
                )
                logger.warning(
                    "Tavily 搜索触发限流或临时错误，主体 %s 将在 %.1f 秒后重试 [%s/%s]：%s",
                    entity_name,
                    sleep_seconds,
                    attempt + 1,
                    self.settings.request_retry_attempts,
                    exc,
                )
                time.sleep(sleep_seconds)

        items = self._build_items_from_response(entity_name, response, start_time, end_time)

        if self.settings.mainland_source_mode in {"prefer", "only"}:
            mainland_items = [item for item in items if _is_mainland_news_item(item, self.settings)]
            needs_cn_top_up = self.settings.mainland_source_mode == "only" or not mainland_items
            if needs_cn_top_up and self.settings.mainland_source_domains:
                cn_kwargs = dict(request_kwargs)
                cn_kwargs["include_domains"] = self.settings.mainland_source_domains[:20]
                try:
                    cn_response = self._request_tavily(entity_name, cn_kwargs)
                    cn_items = self._build_items_from_response(entity_name, cn_response, start_time, end_time)
                    items = _dedupe_news_items(cn_items + items)
                except SearchClientError as exc:
                    logger.warning("主体 %s 的中国大陆站点补充搜索失败：%s", entity_name, exc)

            items = _sort_mainland_first(items, self.settings)
            if self.settings.mainland_source_mode == "only":
                items = [item for item in items if _is_mainland_news_item(item, self.settings)]

        if items or self.fallback_client is None:
            return items[: self.settings.max_results_per_entity]

        logger.info("主体 %s 在 Tavily 未命中有效结果，改用 DuckDuckGo 兜底。", entity_name)
        fallback_items = self.fallback_client.search(entity_name, start_time, end_time)
        if self.settings.mainland_source_mode == "only":
            fallback_items = [item for item in fallback_items if _is_mainland_news_item(item, self.settings)]
        elif self.settings.mainland_source_mode == "prefer":
            fallback_items = _sort_mainland_first(fallback_items, self.settings)
        return fallback_items[: self.settings.max_results_per_entity]

    @staticmethod
    def _extract_domain(url: object) -> str:
        text = _safe_text(url)
        if not text:
            return ""
        match = re.match(r"https?://([^/]+)", text)
        return match.group(1) if match else ""


class CompositeSearchClient(BaseSearchClient):
    provider_name = "composite"

    def __init__(self, settings: Settings, clients: list[BaseSearchClient], provider_names: list[str]) -> None:
        super().__init__(settings)
        self.clients = clients
        self.provider_names = provider_names
        self.disabled_provider_reasons: dict[str, str] = {}

    def search(self, entity_name: str, start_time: datetime, end_time: datetime) -> list[NewsItem]:
        combined_items: list[NewsItem] = []
        errors: list[str] = []
        successful_provider_count = 0

        for client in self.clients:
            provider_label = getattr(client, "provider_name", client.__class__.__name__)
            if provider_label in self.disabled_provider_reasons:
                continue
            try:
                provider_items = client.search(entity_name, start_time, end_time)
                combined_items.extend(provider_items)
                successful_provider_count += 1
            except QccRegionBlockedError as exc:
                if self.settings.qcc_auto_disable_on_region_block:
                    self.disabled_provider_reasons[provider_label] = str(exc)
                    logger.warning(
                        "来源 %s 因区域限制在本轮任务中自动停用：%s",
                        provider_label,
                        exc,
                    )
                    continue
                logger.warning("来源 %s 抓取主体 %s 失败：%s", provider_label, entity_name, exc)
                errors.append(f"{provider_label}: {exc}")
            except SearchClientError as exc:
                logger.warning("来源 %s 抓取主体 %s 失败：%s", provider_label, entity_name, exc)
                errors.append(f"{provider_label}: {exc}")

        combined_items = _dedupe_news_items(combined_items)
        if self.settings.mainland_source_mode == "prefer":
            combined_items = _sort_mainland_first(combined_items, self.settings)
        elif self.settings.mainland_source_mode == "only":
            combined_items = [
                item for item in combined_items if _is_mainland_news_item(item, self.settings)
            ]

        if combined_items:
            return combined_items[: self.settings.max_results_per_entity]
        if successful_provider_count > 0:
            return []
        raise SearchClientError("；".join(errors) or "所有搜索来源均不可用。")


def _build_single_search_client(provider: str, settings: Settings) -> BaseSearchClient:
    if provider == "tavily":
        return TavilyNewsSearchClient(settings)
    if provider == "bing":
        return BingNewsSearchClient(settings)
    if provider == "serpapi":
        return SerpApiSearchClient(settings)
    if provider in {"duckduckgo", "ddg"}:
        return DuckDuckGoNewsSearchClient(settings)
    if provider in {"qcc", "qichacha"}:
        return QccNewsSearchClient(settings)
    raise SearchClientError(f"不支持的搜索提供商：{provider}")


def build_search_client(settings: Settings) -> BaseSearchClient:
    providers = settings.search_providers
    if len(providers) == 1:
        return _build_single_search_client(providers[0], settings)

    clients: list[BaseSearchClient] = []
    skipped_reasons: list[str] = []
    for provider in providers:
        try:
            client = _build_single_search_client(provider, settings)
        except SearchClientError as exc:
            skipped_reasons.append(f"{provider}: {exc}")
            continue
        available, reason = client.is_available()
        if available:
            clients.append(client)
        else:
            skipped_reasons.append(f"{provider}: {reason}")

    if not clients:
        raise SearchClientError(
            "未找到可用的搜索来源。"
            + (f" 跳过原因：{'；'.join(skipped_reasons)}" if skipped_reasons else "")
        )

    if skipped_reasons:
        logger.warning("部分搜索来源未启用：%s", "；".join(skipped_reasons))
    return CompositeSearchClient(settings, clients=clients, provider_names=providers)
