"""GitLab 导入设置服务。"""

from __future__ import annotations

from datetime import datetime, timezone

from ..schemas import (
    GitLabImportRuntimeSettings,
    GitLabImportSettings,
    GitLabImportStoredSettings,
    UpdateGitLabImportSettingsRequest,
)
from ..settings import AppSettings
from .gitlab_settings_store import GitLabSettingsStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GitLabSettingsService:
    """统一处理 GitLab 导入设置的读写和环境变量回退。"""

    def __init__(self, store: GitLabSettingsStore, app_settings: AppSettings) -> None:
        self.store = store
        self.app_settings = app_settings

    def _mask_value(self, raw_value: str | None) -> str | None:
        normalized = (raw_value or "").strip()
        if normalized == "":
            return None
        if len(normalized) <= 10:
            return "*" * len(normalized)
        return normalized[:6] + ("*" * max(len(normalized) - 10, 1)) + normalized[-4:]

    def _normalize_hosts(self, hosts: list[str] | tuple[str, ...] | None) -> list[str]:
        normalized = [str(item).strip().lower() for item in (hosts or []) if str(item).strip() != ""]
        deduped: list[str] = []
        seen: set[str] = set()
        for item in normalized:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def get_stored_settings(self) -> GitLabImportStoredSettings:
        return self.store.get_stored_settings() or self.store.build_blank_settings()

    def get_runtime_settings(self) -> GitLabImportRuntimeSettings:
        stored = self.get_stored_settings()
        stored_token = (stored.token or "").strip()
        env_token = (self.app_settings.gitlab_import_token or "").strip()
        token_source: str
        if stored_token != "":
            token = stored_token
            token_source = "database"
        elif env_token != "":
            token = env_token
            token_source = "environment"
        else:
            token = None
            token_source = "none"

        stored_hosts = self._normalize_hosts(stored.allowed_hosts)
        env_hosts = self._normalize_hosts(self.app_settings.gitlab_import_allowed_hosts)
        allowed_hosts = stored_hosts or env_hosts or ["git.yyrd.com"]
        return GitLabImportRuntimeSettings(
            token=token,
            allowed_hosts=allowed_hosts,
            token_source=token_source,
            created_at=stored.created_at,
            updated_at=stored.updated_at,
        )

    def get_public_settings(self) -> GitLabImportSettings:
        runtime = self.get_runtime_settings()
        token = (runtime.token or "").strip()
        return GitLabImportSettings(
            configured=token != "",
            has_token=token != "",
            token_masked=self._mask_value(token),
            token_source=runtime.token_source,
            allowed_hosts=list(runtime.allowed_hosts),
        )

    def update_settings(self, request_data: UpdateGitLabImportSettingsRequest) -> GitLabImportSettings:
        current = self.get_stored_settings()
        token = current.token
        if request_data.clear_token:
            token = None
        elif request_data.token is not None and request_data.token.strip() != "":
            token = request_data.token.strip()

        allowed_hosts = current.allowed_hosts
        if request_data.allowed_hosts is not None:
            normalized_hosts = self._normalize_hosts(request_data.allowed_hosts)
            allowed_hosts = normalized_hosts or None

        self.store.save_stored_settings(
            GitLabImportStoredSettings(
                token=token,
                allowed_hosts=allowed_hosts,
                created_at=current.created_at,
                updated_at=_utc_now(),
            )
        )
        return self.get_public_settings()
