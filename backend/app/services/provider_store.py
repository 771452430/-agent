"""全局 provider 配置中心。

这个模块是“多厂商 API 设置”的数据真源，职责只有两个：
1. 把 provider 的启用状态、协议、Base URL、API Key、模型列表落到 SQLite；
2. 对外返回“脱敏后的配置”，避免前端拿到明文密钥。

为什么把 provider 配置独立成 store：
- Thread / Agent 里只需要保存 `provider + model` 这类引用；
- 真正的密钥和 Base URL 应该是全局共享配置，而不是散落在每次请求里；
- 这样更符合学习目标：你能明确看到“运行时参数”和“配置中心”是两层概念。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..schemas import ProviderConfig, ProviderModel, ProviderProtocol, ProviderRuntimeConfig


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ProviderSeed:
    """预置 provider 定义。"""

    id: str
    name: str
    protocol: ProviderProtocol
    allowed_protocols: tuple[ProviderProtocol, ...]
    api_base_url: str
    models: tuple[ProviderModel, ...]
    locked: bool = True
    enabled: bool = True


DEFAULT_PROVIDER_ID = "mock"
OFFICIAL_OPENAI_PROVIDER_ID = "openai"
CUSTOM_OPENAI_PROVIDER_ID = "custom_openai"
OFFICIAL_OPENAI_BASE_URLS = ("https://api.openai.com", "https://api.openai.com/v1")

SEED_PROVIDERS: tuple[ProviderSeed, ...] = (
    ProviderSeed(
        id="mock",
        name="Learning Mode",
        protocol="mock_local",
        allowed_protocols=("mock_local",),
        api_base_url="",
        models=(ProviderModel(id="learning-mode", label="Learning Mode", source="manual"),),
    ),
    ProviderSeed(
        id="openai",
        name="OpenAI",
        protocol="openai_compatible",
        allowed_protocols=("openai_compatible",),
        api_base_url="https://api.openai.com/v1",
        models=(
            ProviderModel(id="gpt-4.1-mini", label="GPT-4.1 Mini", source="manual"),
            ProviderModel(id="gpt-4.1", label="GPT-4.1", source="manual"),
        ),
    ),
    ProviderSeed(
        id="anthropic",
        name="Anthropic",
        protocol="anthropic_compatible",
        allowed_protocols=("anthropic_compatible",),
        api_base_url="https://api.anthropic.com",
        models=(
            ProviderModel(id="claude-3-5-haiku-latest", label="Claude 3.5 Haiku", source="manual"),
            ProviderModel(id="claude-3-7-sonnet-latest", label="Claude 3.7 Sonnet", source="manual"),
        ),
    ),
    ProviderSeed(
        id="ollama",
        name="Ollama",
        protocol="ollama_native",
        allowed_protocols=("ollama_native",),
        api_base_url="http://127.0.0.1:11434",
        models=(ProviderModel(id="llama3.1", label="llama3.1", source="manual"),),
    ),
    ProviderSeed(
        id="deepseek",
        name="DeepSeek",
        protocol="openai_compatible",
        allowed_protocols=("openai_compatible",),
        api_base_url="https://api.deepseek.com/v1",
        models=(ProviderModel(id="deepseek-chat", label="DeepSeek Chat", source="manual"),),
    ),
    ProviderSeed(
        id="qwen",
        name="Qwen",
        protocol="openai_compatible",
        allowed_protocols=("openai_compatible",),
        api_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=(ProviderModel(id="qwen-plus", label="Qwen Plus", source="manual"),),
    ),
    ProviderSeed(
        id="zhipu",
        name="Zhipu",
        protocol="openai_compatible",
        allowed_protocols=("openai_compatible",),
        api_base_url="https://open.bigmodel.cn/api/paas/v4",
        models=(ProviderModel(id="glm-4-plus", label="GLM-4 Plus", source="manual"),),
    ),
    ProviderSeed(
        id="moonshot",
        name="Moonshot",
        protocol="openai_compatible",
        allowed_protocols=("openai_compatible",),
        api_base_url="https://api.moonshot.cn/v1",
        models=(ProviderModel(id="moonshot-v1-8k", label="Moonshot v1 8K", source="manual"),),
    ),
    ProviderSeed(
        id="minimax",
        name="MiniMax",
        protocol="openai_compatible",
        allowed_protocols=("openai_compatible", "anthropic_compatible"),
        api_base_url="https://api.minimaxi.com/anthropic",
        models=(
            ProviderModel(id="MiniMax-M2.5", label="MiniMax M2.5", source="manual"),
            ProviderModel(id="MiniMax-M2.1", label="MiniMax M2.1", source="manual"),
        ),
    ),
    ProviderSeed(
        id="volcengine",
        name="Volcengine",
        protocol="openai_compatible",
        allowed_protocols=("openai_compatible",),
        api_base_url="https://ark.cn-beijing.volces.com/api/v3",
        models=(ProviderModel(id="doubao-seed-1-6", label="Doubao Seed 1.6", source="manual"),),
    ),
    ProviderSeed(
        id="custom_openai",
        name="Custom OpenAI Compatible",
        protocol="openai_compatible",
        allowed_protocols=("openai_compatible",),
        api_base_url="",
        models=(),
    ),
    ProviderSeed(
        id="custom_anthropic",
        name="Custom Anthropic Compatible",
        protocol="anthropic_compatible",
        allowed_protocols=("anthropic_compatible",),
        api_base_url="",
        models=(),
    ),
)


class ProviderStore:
    """管理 provider 配置的持久化与脱敏读取。"""

    _KEEP_EXISTING = object()

    def __init__(self, sqlite_path: Path) -> None:
        self.sqlite_path = sqlite_path
        self._init_db()
        self._seed_defaults()
        self._migrate_legacy_openai_provider()
        self._normalize_enabled_providers()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_configs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    protocol TEXT NOT NULL,
                    allowed_protocols_json TEXT NOT NULL,
                    api_base_url TEXT NOT NULL,
                    api_key TEXT,
                    models_json TEXT NOT NULL,
                    locked INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _seed_defaults(self) -> None:
        now = _utc_now().isoformat()
        with self._connect() as conn:
            for seed in SEED_PROVIDERS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO provider_configs (
                        id, name, enabled, protocol, allowed_protocols_json,
                        api_base_url, api_key, models_json, locked, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        seed.id,
                        seed.name,
                        1 if seed.enabled else 0,
                        seed.protocol,
                        json.dumps(list(seed.allowed_protocols), ensure_ascii=False),
                        seed.api_base_url,
                        None,
                        json.dumps([model.model_dump(mode="json") for model in seed.models], ensure_ascii=False),
                        1 if seed.locked else 0,
                        now,
                        now,
                    ),
                )

    def _normalize_url(self, value: str | None) -> str:
        return (value or "").strip().rstrip("/")

    def _seed_by_id(self, provider_id: str) -> ProviderSeed | None:
        for seed in SEED_PROVIDERS:
            if seed.id == provider_id:
                return seed
        return None

    def _is_official_openai_base_url(self, value: str | None) -> bool:
        normalized = self._normalize_url(value)
        return normalized in OFFICIAL_OPENAI_BASE_URLS

    def _migrate_legacy_openai_provider(self) -> None:
        """把历史第三方 OpenAI-compatible 配置从 `openai` 迁到 `custom_openai`。"""

        openai = self.get_runtime_provider(OFFICIAL_OPENAI_PROVIDER_ID)
        custom = self.get_runtime_provider(CUSTOM_OPENAI_PROVIDER_ID)
        openai_seed = self._seed_by_id(OFFICIAL_OPENAI_PROVIDER_ID)
        if openai is None or custom is None or openai_seed is None:
            return

        legacy_base_url = self._normalize_url(openai.api_base_url)
        if legacy_base_url == "" or self._is_official_openai_base_url(legacy_base_url):
            return

        now = _utc_now().isoformat()
        next_custom_models = self._normalize_models(openai.models + custom.models)
        next_custom_api_key = openai.api_key if (openai.api_key or "").strip() != "" else custom.api_key

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE provider_configs
                SET enabled = ?, protocol = ?, api_base_url = ?, api_key = ?, models_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    1 if (openai.enabled or custom.enabled) else 0,
                    custom.protocol,
                    legacy_base_url,
                    next_custom_api_key,
                    json.dumps([model.model_dump(mode="json") for model in next_custom_models], ensure_ascii=False),
                    now,
                    CUSTOM_OPENAI_PROVIDER_ID,
                ),
            )
            conn.execute(
                """
                UPDATE provider_configs
                SET enabled = 0, protocol = ?, api_base_url = ?, api_key = ?, models_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    openai_seed.protocol,
                    openai_seed.api_base_url,
                    None,
                    json.dumps([model.model_dump(mode="json") for model in openai_seed.models], ensure_ascii=False),
                    now,
                    OFFICIAL_OPENAI_PROVIDER_ID,
                ),
            )

    def _normalize_enabled_providers(self) -> None:
        """把 provider 的启用状态收敛成“最多只有一个 enabled”。

        这是为了符合当前设置面板的交互约束：
        - provider 配置是全局真源；
        - 当前版本只允许一个 provider 处于启用状态；
        - 当历史数据里有多个 enabled（例如旧版本全量 seed）时，
          启动时自动做一次收敛，避免前后端行为不一致。
        """

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, api_key, updated_at
                FROM provider_configs
                WHERE enabled = 1
                ORDER BY updated_at DESC, created_at DESC
                """
            ).fetchall()
            if len(rows) <= 1:
                return

            keeper_id: str | None = None
            for row in rows:
                if (row["api_key"] or "").strip() != "":
                    keeper_id = row["id"]
                    break
            if keeper_id is None and any(row["id"] == DEFAULT_PROVIDER_ID for row in rows):
                keeper_id = DEFAULT_PROVIDER_ID
            if keeper_id is None:
                keeper_id = rows[0]["id"]

            conn.execute("UPDATE provider_configs SET enabled = 0 WHERE id != ?", (keeper_id,))
            conn.execute("UPDATE provider_configs SET enabled = 1 WHERE id = ?", (keeper_id,))

    def _parse_models(self, raw: str | None) -> list[ProviderModel]:
        items = json.loads(raw or "[]")
        normalized: list[ProviderModel] = []
        seen: set[str] = set()
        for item in items:
            try:
                model = ProviderModel.model_validate(item)
            except Exception:
                continue
            if model.id in seen:
                continue
            normalized.append(model)
            seen.add(model.id)
        return normalized

    def _parse_allowed_protocols(self, raw: str | None) -> list[ProviderProtocol]:
        items = json.loads(raw or "[]")
        return [item for item in items if item in {"openai_compatible", "anthropic_compatible", "ollama_native", "mock_local"}]

    def _mask_api_key(self, api_key: str | None) -> str | None:
        if api_key is None or api_key == "":
            return None
        if len(api_key) <= 8:
            return "*" * len(api_key)
        return api_key[:4] + ("*" * (len(api_key) - 8)) + api_key[-4:]

    def _row_to_provider(self, row: sqlite3.Row) -> ProviderConfig:
        api_key = row["api_key"] or ""
        return ProviderConfig(
            id=row["id"],
            name=row["name"],
            enabled=bool(row["enabled"]),
            protocol=row["protocol"],
            allowed_protocols=self._parse_allowed_protocols(row["allowed_protocols_json"]),
            api_base_url=row["api_base_url"] or "",
            has_api_key=api_key != "",
            api_key_masked=self._mask_api_key(api_key),
            models=self._parse_models(row["models_json"]),
            locked=bool(row["locked"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_runtime(self, row: sqlite3.Row) -> ProviderRuntimeConfig:
        return ProviderRuntimeConfig(
            id=row["id"],
            name=row["name"],
            enabled=bool(row["enabled"]),
            protocol=row["protocol"],
            allowed_protocols=self._parse_allowed_protocols(row["allowed_protocols_json"]),
            api_base_url=row["api_base_url"] or "",
            api_key=row["api_key"] or None,
            models=self._parse_models(row["models_json"]),
            locked=bool(row["locked"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _normalize_models(self, models: list[ProviderModel] | list[dict[str, Any]] | None) -> list[ProviderModel]:
        normalized: list[ProviderModel] = []
        seen: set[str] = set()
        for item in models or []:
            model = item if isinstance(item, ProviderModel) else ProviderModel.model_validate(item)
            if model.id in seen:
                continue
            normalized.append(model)
            seen.add(model.id)
        return normalized

    def default_model_config(self) -> tuple[str, str]:
        runtime = self.get_runtime_provider(DEFAULT_PROVIDER_ID)
        if runtime is None or len(runtime.models) == 0:
            return DEFAULT_PROVIDER_ID, "learning-mode"
        return runtime.id, runtime.models[0].id

    def list_providers(self) -> list[ProviderConfig]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM provider_configs ORDER BY name ASC").fetchall()
        return [self._row_to_provider(row) for row in rows]

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM provider_configs WHERE id = ?", (provider_id,)).fetchone()
        return self._row_to_provider(row) if row is not None else None

    def get_runtime_provider(self, provider_id: str) -> ProviderRuntimeConfig | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM provider_configs WHERE id = ?", (provider_id,)).fetchone()
        return self._row_to_runtime(row) if row is not None else None

    def update_provider(
        self,
        provider_id: str,
        *,
        enabled: bool | None = None,
        protocol: ProviderProtocol | None = None,
        api_base_url: str | None = None,
        api_key: str | object = _KEEP_EXISTING,
        models: list[ProviderModel] | list[dict[str, Any]] | None = None,
    ) -> ProviderConfig | None:
        current = self.get_runtime_provider(provider_id)
        if current is None:
            return None

        next_protocol = protocol or current.protocol
        if next_protocol not in current.allowed_protocols:
            allowed = ", ".join(current.allowed_protocols)
            raise ValueError(f"{provider_id} 只允许这些协议: {allowed}")

        next_models = self._normalize_models(models) if models is not None else current.models
        next_api_key = current.api_key if api_key is self._KEEP_EXISTING else str(api_key or "").strip()
        next_updated_at = _utc_now().isoformat()
        next_enabled = enabled if enabled is not None else current.enabled

        with self._connect() as conn:
            if next_enabled:
                conn.execute("UPDATE provider_configs SET enabled = 0 WHERE id != ?", (provider_id,))
            conn.execute(
                """
                UPDATE provider_configs
                SET enabled = ?, protocol = ?, api_base_url = ?, api_key = ?, models_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    1 if next_enabled else 0,
                    next_protocol,
                    (api_base_url if api_base_url is not None else current.api_base_url).strip(),
                    next_api_key,
                    json.dumps([model.model_dump(mode="json") for model in next_models], ensure_ascii=False),
                    next_updated_at,
                    provider_id,
                ),
            )

        return self.get_provider(provider_id)
