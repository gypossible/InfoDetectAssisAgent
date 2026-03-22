from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email import encoders
from email.header import Header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

from .config import Settings

logger = logging.getLogger(__name__)


def build_email_subject(run_time: datetime) -> str:
    return f"【每日舆情简报】{run_time.strftime('%Y年%m月%d日')} 舆情分析报告"


def build_email_body(run_time: datetime, core_summary: str) -> str:
    return (
        "您好，\n\n"
        f"以下为 {run_time.strftime('%Y年%m月%d日')} 的舆情监测核心摘要：\n"
        f"{core_summary}\n\n"
        "完整舆情分析报告及原始数据表已作为附件发送，请查收。\n\n"
        "此邮件由自动化舆情监测 Agent 自动发送。"
    )


class EmailDispatcher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send_email(
        self,
        subject: str,
        body: str,
        attachments: list[Path],
    ) -> None:
        if not self.settings.smtp_host or not self.settings.smtp_username or not self.settings.smtp_password:
            raise ValueError("SMTP 配置不完整，请补充 SMTP_HOST、SMTP_USERNAME、SMTP_PASSWORD。")

        message = MIMEMultipart()
        message["From"] = formataddr(
            (str(Header("自动化舆情监测 Agent", "utf-8")), self.settings.smtp_sender)
        )
        message["To"] = ", ".join(self.settings.email_recipients)
        message["Subject"] = str(Header(subject, "utf-8"))
        message.attach(MIMEText(body, "plain", "utf-8"))

        for attachment_path in attachments:
            self._attach_file(message, attachment_path)

        if self.settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                self.settings.smtp_host,
                self.settings.smtp_port,
                timeout=30,
            ) as server:
                server.login(self.settings.smtp_username, self.settings.smtp_password)
                server.sendmail(
                    self.settings.smtp_sender,
                    self.settings.email_recipients,
                    message.as_string(),
                )
        else:
            with smtplib.SMTP(
                self.settings.smtp_host,
                self.settings.smtp_port,
                timeout=30,
            ) as server:
                server.starttls()
                server.login(self.settings.smtp_username, self.settings.smtp_password)
                server.sendmail(
                    self.settings.smtp_sender,
                    self.settings.email_recipients,
                    message.as_string(),
                )

        logger.info("邮件发送成功，收件人：%s", ", ".join(self.settings.email_recipients))

    @staticmethod
    def _attach_file(message: MIMEMultipart, file_path: Path) -> None:
        with file_path.open("rb") as file_obj:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(file_obj.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=("utf-8", "", file_path.name),
        )
        message.attach(part)
