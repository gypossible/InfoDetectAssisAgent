from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


DEFAULT_MAINLAND_SOURCE_DOMAINS = ",".join(
    [
        "people.com.cn",
        "xinhuanet.com",
        "cctv.com",
        "chinanews.com.cn",
        "china.com.cn",
        "gmw.cn",
        "cnr.cn",
        "thepaper.cn",
        "caixin.com",
        "yicai.com",
        "21jingji.com",
        "nbd.com.cn",
        "eastmoney.com",
        "10jqka.com.cn",
        "cnstock.com",
        "stcn.com",
        "cs.com.cn",
        "cls.cn",
        "jrj.com.cn",
        "finance.sina.com.cn",
        "finance.ifeng.com",
        "qq.com",
        "163.com",
        "sohu.com",
        "gov.cn",
        "csrc.gov.cn",
        "sse.com.cn",
        "szse.cn",
    ]
)


@dataclass(slots=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    output_dir: Path = PROJECT_ROOT / "outputs"
    log_dir: Path = PROJECT_ROOT / "logs"

    excel_input_path: Path = Path(
        os.getenv(
            "EXCEL_INPUT_PATH",
            str(Path.home() / "Documents" / "舆情监测主体"),
        )
    )  # TODO: 请在这里填入本地 Excel 文件或文件夹绝对路径
    excel_upload_dir: Path = Path(
        os.getenv("EXCEL_UPLOAD_DIR", str(PROJECT_ROOT / "data" / "uploads"))
    )
    excel_target_column_letter: str = os.getenv(
        "EXCEL_TARGET_COLUMN_LETTER", "B"
    ).strip().upper()  # TODO: 如需更换监测列，请在这里填入 Excel 列字母

    search_provider: str = os.getenv("SEARCH_PROVIDER", "tavily").strip().lower()
    search_lookback_days: int = int(os.getenv("SEARCH_LOOKBACK_DAYS", "365"))
    mainland_source_mode: str = os.getenv("MAINLAND_SOURCE_MODE", "prefer").strip().lower()
    mainland_source_domains_raw: str = os.getenv(
        "MAINLAND_SOURCE_DOMAINS",
        DEFAULT_MAINLAND_SOURCE_DOMAINS,
    )
    request_delay_seconds: float = float(os.getenv("REQUEST_DELAY_SECONDS", "1.5"))
    request_timeout_seconds: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
    request_retry_attempts: int = int(os.getenv("REQUEST_RETRY_ATTEMPTS", "3"))
    request_retry_backoff_seconds: float = float(os.getenv("REQUEST_RETRY_BACKOFF_SECONDS", "3"))
    request_retry_backoff_max_seconds: float = float(
        os.getenv("REQUEST_RETRY_BACKOFF_MAX_SECONDS", "20")
    )
    max_results_per_entity: int = int(os.getenv("MAX_RESULTS_PER_ENTITY", "10"))
    max_excel_files: int = int(os.getenv("MAX_EXCEL_FILES", "50"))

    bing_subscription_key: str = os.getenv("BING_SUBSCRIPTION_KEY", "")  # TODO: 请在这里填入 Bing News Search API Key
    bing_endpoint: str = os.getenv(
        "BING_ENDPOINT", "https://api.bing.microsoft.com/v7.0/news/search"
    )  # TODO: 如使用私有化网关，请在这里填入 Bing News Search 接口地址
    bing_market: str = os.getenv("BING_MARKET", "zh-CN")

    serpapi_api_key: str = os.getenv("SERPAPI_API_KEY", "")  # TODO: 请在这里填入 SerpAPI Key
    serpapi_endpoint: str = os.getenv("SERPAPI_ENDPOINT", "https://serpapi.com/search.json")
    serpapi_gl: str = os.getenv("SERPAPI_GL", "cn")
    serpapi_hl: str = os.getenv("SERPAPI_HL", "zh-cn")

    ddg_region: str = os.getenv("DDG_REGION", "cn-zh")

    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")  # TODO: 请在这里填入 Tavily API Key
    tavily_topic: str = os.getenv("TAVILY_TOPIC", "news").strip().lower()
    tavily_search_depth: str = os.getenv("TAVILY_SEARCH_DEPTH", "advanced").strip().lower()
    tavily_time_range: str = os.getenv("TAVILY_TIME_RANGE", "year").strip().lower()
    tavily_include_raw_content: str = os.getenv(
        "TAVILY_INCLUDE_RAW_CONTENT", "markdown"
    ).strip().lower()
    tavily_chunks_per_source: int = int(os.getenv("TAVILY_CHUNKS_PER_SOURCE", "3"))

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")  # TODO: 请在这里填入 OpenAI 或兼容模型 API Key
    openai_base_url: str = os.getenv(
        "OPENAI_BASE_URL", ""
    )  # TODO: 如使用兼容 OpenAI 协议的模型服务，请在这里填入 Base URL
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o")  # TODO: 请在这里填入用于报告生成的模型名称
    llm_max_articles_for_prompt: int = int(os.getenv("LLM_MAX_ARTICLES_FOR_PROMPT", "50"))
    email_summary_char_limit: int = int(os.getenv("EMAIL_SUMMARY_CHAR_LIMIT", "220"))

    smtp_host: str = os.getenv("SMTP_HOST", "smtp.example.com")  # TODO: 请在这里填入 SMTP 服务器地址
    smtp_port: int = int(os.getenv("SMTP_PORT", "465"))  # TODO: 请在这里填入 SMTP 端口
    smtp_username: str = os.getenv("SMTP_USERNAME", "sender@example.com")  # TODO: 请在这里填入发件邮箱账号
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")  # TODO: 请在这里填入邮箱授权码或密码
    smtp_sender: str = os.getenv("SMTP_SENDER", os.getenv("SMTP_USERNAME", "sender@example.com"))  # TODO: 请在这里填入发件邮箱地址
    smtp_use_ssl: bool = _as_bool(os.getenv("SMTP_USE_SSL"), default=True)

    email_recipients_raw: str = os.getenv(
        "EMAIL_RECIPIENTS", "liuguangyuan@natrust.cn"
    )  # TODO: 如需多人收件，请使用英文逗号分隔
    schedule_time: str = os.getenv("SCHEDULE_TIME", "07:30")

    @property
    def email_recipients(self) -> list[str]:
        return _split_csv(self.email_recipients_raw)

    @property
    def mainland_source_domains(self) -> list[str]:
        return [item.lower() for item in _split_csv(self.mainland_source_domains_raw)]

    @property
    def formatted_run_date(self) -> str:
        return datetime.now().strftime("%Y%m%d")

    def ensure_directories(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.excel_upload_dir.mkdir(parents=True, exist_ok=True)

    def build_run_output_dir(self, run_time: datetime) -> Path:
        run_output_dir = self.output_dir / run_time.strftime("%Y%m%d")
        run_output_dir.mkdir(parents=True, exist_ok=True)
        return run_output_dir
