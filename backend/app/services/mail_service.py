"""全局邮箱配置与 SMTP 发信服务。

这层负责两件事：
1. 决定“当前有效的邮箱配置”来自哪里（SQLite 优先，环境变量兜底）；
2. 用这套配置真正发出邮件。

这样做以后，巡检 Agent 不需要自己管理 SMTP，只需要关心：
- 谁是收件人；
- 什么时候应该发通知。
"""

from __future__ import annotations

import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

from ..schemas import MailRuntimeSettings, MailSettings, MailTestRequest, MailTestResponse, UpdateMailSettingsRequest
from ..settings import AppSettings
from .mail_settings_store import MailSettingsStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MailService:
    """统一处理全局邮箱配置和 SMTP 发信。"""

    def __init__(self, mail_store: MailSettingsStore, app_settings: AppSettings) -> None:
        self.mail_store = mail_store
        self.app_settings = app_settings

    def _mask_password(self, password: str | None) -> str | None:
        if password is None or password == "":
            return None
        if len(password) <= 8:
            return "*" * len(password)
        return password[:4] + ("*" * (len(password) - 8)) + password[-4:]

    def _runtime_to_public(self, runtime: MailRuntimeSettings) -> MailSettings:
        password = runtime.smtp_password or ""
        return MailSettings(
            enabled=runtime.enabled,
            smtp_host=runtime.smtp_host,
            smtp_port=runtime.smtp_port,
            smtp_username=runtime.smtp_username,
            has_password=password != "",
            password_masked=self._mask_password(password),
            use_tls=runtime.use_tls,
            use_ssl=runtime.use_ssl,
            sender_email=runtime.sender_email,
        )

    def _blank_runtime(self) -> MailRuntimeSettings:
        now = _utc_now()
        return MailRuntimeSettings(
            enabled=False,
            smtp_host="",
            smtp_port=587,
            smtp_username="",
            smtp_password=None,
            use_tls=True,
            use_ssl=False,
            sender_email="",
            created_at=now,
            updated_at=now,
        )

    def _env_runtime(self) -> MailRuntimeSettings:
        now = _utc_now()
        smtp_username = (self.app_settings.watcher_smtp_username or "").strip()
        return MailRuntimeSettings(
            enabled=(self.app_settings.watcher_smtp_host or "").strip() != "",
            smtp_host=(self.app_settings.watcher_smtp_host or "").strip(),
            smtp_port=self.app_settings.watcher_smtp_port,
            smtp_username=smtp_username,
            smtp_password=(self.app_settings.watcher_smtp_password or "").strip() or None,
            use_tls=self.app_settings.watcher_smtp_use_tls,
            use_ssl=self.app_settings.watcher_smtp_use_ssl,
            sender_email=smtp_username,
            created_at=now,
            updated_at=now,
        )

    def get_runtime_settings(self) -> MailRuntimeSettings:
        stored = self.mail_store.get_runtime_settings()
        if stored is not None:
            return stored

        env_runtime = self._env_runtime()
        if (
            env_runtime.smtp_host != ""
            or env_runtime.smtp_username != ""
            or (env_runtime.smtp_password or "") != ""
        ):
            return env_runtime
        return self._blank_runtime()

    def get_mail_settings(self) -> MailSettings:
        return self._runtime_to_public(self.get_runtime_settings())

    def update_mail_settings(self, request: UpdateMailSettingsRequest) -> MailSettings:
        current = self.get_runtime_settings()
        now = _utc_now()
        smtp_username = (request.smtp_username if request.smtp_username is not None else current.smtp_username).strip()
        next_runtime = MailRuntimeSettings(
            enabled=current.enabled if request.enabled is None else request.enabled,
            smtp_host=(request.smtp_host if request.smtp_host is not None else current.smtp_host).strip(),
            smtp_port=current.smtp_port if request.smtp_port is None else request.smtp_port,
            smtp_username=smtp_username,
            smtp_password=current.smtp_password if request.smtp_password is None else request.smtp_password.strip(),
            use_tls=current.use_tls if request.use_tls is None else request.use_tls,
            use_ssl=current.use_ssl if request.use_ssl is None else request.use_ssl,
            sender_email=smtp_username,
            created_at=current.created_at,
            updated_at=now,
        )
        saved = self.mail_store.save_runtime_settings(next_runtime)
        return self._runtime_to_public(saved)

    def _require_runnable_runtime(self) -> MailRuntimeSettings:
        runtime = self.get_runtime_settings()
        if not runtime.enabled:
            raise RuntimeError("全局邮箱设置当前未启用，请先到设置 -> 邮箱设置开启。")
        if runtime.smtp_host.strip() == "":
            raise RuntimeError("全局邮箱设置缺少 SMTP Host，请先到设置 -> 邮箱设置补充。")
        if runtime.smtp_username.strip() == "":
            raise RuntimeError("全局邮箱设置缺少 SMTP 用户名，请先到设置 -> 邮箱设置补充。")
        if runtime.sender_email.strip() == "":
            raise RuntimeError("全局邮箱设置缺少发件邮箱，请先到设置 -> 邮箱设置补充。")
        return runtime

    def send_email(
        self,
        *,
        recipient_emails: list[str],
        subject: str,
        body: str,
        html_body: str | None = None,
    ) -> MailRuntimeSettings:
        runtime = self._require_runnable_runtime()
        if len(recipient_emails) == 0:
            raise RuntimeError("当前巡检 Agent 还没有配置收件邮箱。")

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = runtime.sender_email
        message["To"] = ", ".join(recipient_emails)
        message.set_content(body, subtype="plain", charset="utf-8")
        if html_body is not None and html_body.strip() != "":
            message.add_alternative(html_body, subtype="html", charset="utf-8")

        if runtime.use_ssl:
            with smtplib.SMTP_SSL(runtime.smtp_host, runtime.smtp_port, timeout=20) as server:
                if runtime.smtp_username != "" or (runtime.smtp_password or "") != "":
                    server.login(runtime.smtp_username, runtime.smtp_password or "")
                server.send_message(message)
            return runtime

        with smtplib.SMTP(runtime.smtp_host, runtime.smtp_port, timeout=20) as server:
            if runtime.use_tls:
                server.starttls()
            if runtime.smtp_username != "" or (runtime.smtp_password or "") != "":
                server.login(runtime.smtp_username, runtime.smtp_password or "")
            server.send_message(message)
        return runtime

    def test_mail_settings(self, request: MailTestRequest) -> MailTestResponse:
        recipient_email = request.recipient_email.strip()
        try:
            runtime = self.send_email(
                recipient_emails=[recipient_email],
                subject=(request.subject or "LangChain Learning Demo 邮件测试").strip()
                or "LangChain Learning Demo 邮件测试",
                body=(request.body or "这是一封来自全局邮箱设置中心的测试邮件。").strip()
                or "这是一封来自全局邮箱设置中心的测试邮件。",
            )
            return MailTestResponse(
                ok=True,
                message="测试邮件发送成功。",
                sender_email=runtime.sender_email,
                recipient_email=recipient_email,
            )
        except Exception as exc:
            runtime = self.get_runtime_settings()
            return MailTestResponse(
                ok=False,
                message=str(exc),
                sender_email=runtime.sender_email,
                recipient_email=recipient_email,
            )
