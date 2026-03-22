from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class NewsItem:
    entity_name: str
    title: str
    url: str
    published_at: str
    source: str
    snippet: str
    provider: str
    fetched_at: str

    def to_record(self) -> dict[str, str]:
        return asdict(self)


@dataclass(slots=True)
class PipelineResult:
    entity_count: int
    article_count: int
    data_file_path: Path
    report_file_path: Path
    email_sent: bool
