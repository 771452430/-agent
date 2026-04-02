"""工作通知设置服务。"""

from __future__ import annotations

from datetime import datetime, timezone

from ..schemas import (
    UpdateWorkNotifySettingsRequest,
    WorkNotifyRuntimeSettings,
    WorkNotifySettings,
)
from .work_notify_settings_store import WorkNotifySettingsStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkNotifySettingsService:
    """统一处理工作通知配置读取与保存。"""

    def __init__(self, work_notify_store: WorkNotifySettingsStore) -> None:
        self.work_notify_store = work_notify_store

    def _mask_value(self, raw_value: str | None) -> str | None:
        normalized = (raw_value or "").strip()
        if normalized == "":
            return None
        if len(normalized) <= 10:
            return "*" * len(normalized)
        return normalized[:6] + ("*" * max(len(normalized) - 10, 1)) + normalized[-4:]

    def _blank_runtime(self) -> WorkNotifyRuntimeSettings:
        now = _utc_now()
        return WorkNotifyRuntimeSettings(
            app_key=None,
            app_secret=None,
            contacts_cookie=None,
            created_at=now,
            updated_at=now,
        )

    def _runtime_to_public(self, runtime: WorkNotifyRuntimeSettings) -> WorkNotifySettings:
        app_key = (runtime.app_key or "").strip()
        app_secret = (runtime.app_secret or "").strip()
        return WorkNotifySettings(
            configured=app_key != "" and app_secret != "",
            app_key=app_key,
            has_app_secret=app_secret != "",
            app_secret_masked=self._mask_value(app_secret),
            has_contacts_cookie=(runtime.contacts_cookie or "").strip() != "",
            contacts_cookie_masked=self._mask_value(runtime.contacts_cookie),
        )

    def get_runtime_settings(self) -> WorkNotifyRuntimeSettings:
        stored = self.work_notify_store.get_runtime_settings()
        if stored is not None:
            return stored
        return self._blank_runtime()

    def get_work_notify_settings(self) -> WorkNotifySettings:
        return self._runtime_to_public(self.get_runtime_settings())

    def update_work_notify_settings(self, request_data: UpdateWorkNotifySettingsRequest) -> WorkNotifySettings:
        current = self.get_runtime_settings()
        app_key = current.app_key
        app_secret = current.app_secret
        contacts_cookie = current.contacts_cookie
        if request_data.app_key is not None:
            app_key = request_data.app_key.strip() or None
        if request_data.app_secret is not None:
            app_secret = request_data.app_secret.strip() or None
        if request_data.contacts_cookie is not None:
            contacts_cookie = request_data.contacts_cookie.strip() or None
        saved = self.work_notify_store.save_runtime_settings(
            WorkNotifyRuntimeSettings(
                app_key=app_key,
                app_secret=app_secret,
                contacts_cookie=contacts_cookie,
                created_at=current.created_at,
                updated_at=_utc_now(),
            )
        )
        return self._runtime_to_public(saved)
