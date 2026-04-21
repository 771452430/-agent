"""飞书设置与多维表格访问服务。"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import error as urlerror
from urllib import parse, request

from ..schemas import (
    FeishuBitableValidationResponse,
    FeishuRuntimeSettings,
    FeishuSettings,
    UpdateFeishuSettingsRequest,
)
from .feishu_settings_store import FeishuSettingsStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FeishuService:
    """统一处理飞书配置和多维表格读写。"""

    DEFAULT_BITABLE_HOST = "feishu.cn"
    TOKEN_REFRESH_BUFFER = timedelta(minutes=5)
    TENANT_ACCESS_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"

    def __init__(self, feishu_store: FeishuSettingsStore) -> None:
        self.feishu_store = feishu_store
        self._token_lock = threading.Lock()
        self._cached_tenant_access_token: str | None = None
        self._cached_tenant_access_token_expires_at: datetime | None = None

    def _mask_value(self, raw_value: str | None) -> str | None:
        normalized = (raw_value or "").strip()
        if normalized == "":
            return None
        if len(normalized) <= 10:
            return "*" * len(normalized)
        return normalized[:6] + ("*" * max(len(normalized) - 10, 1)) + normalized[-4:]

    def _blank_runtime(self) -> FeishuRuntimeSettings:
        now = _utc_now()
        return FeishuRuntimeSettings(app_id=None, app_secret=None, created_at=now, updated_at=now)

    def _runtime_to_public(self, runtime: FeishuRuntimeSettings) -> FeishuSettings:
        app_id = (runtime.app_id or "").strip()
        app_secret = (runtime.app_secret or "").strip()
        return FeishuSettings(
            configured=app_id != "" and app_secret != "",
            app_id=app_id,
            has_app_secret=app_secret != "",
            app_secret_masked=self._mask_value(app_secret),
            auth_mode="tenant_access_token_internal",
        )

    def get_runtime_settings(self) -> FeishuRuntimeSettings:
        stored = self.feishu_store.get_runtime_settings()
        if stored is not None:
            return stored
        return self._blank_runtime()

    def get_feishu_settings(self) -> FeishuSettings:
        return self._runtime_to_public(self.get_runtime_settings())

    def update_feishu_settings(self, request_data: UpdateFeishuSettingsRequest) -> FeishuSettings:
        current = self.get_runtime_settings()
        app_id = current.app_id
        app_secret = current.app_secret
        if request_data.app_id is not None:
            app_id = request_data.app_id.strip() or None
        if request_data.app_secret is not None:
            app_secret = request_data.app_secret.strip() or None
        saved = self.feishu_store.save_runtime_settings(
            FeishuRuntimeSettings(
                app_id=app_id,
                app_secret=app_secret,
                created_at=current.created_at,
                updated_at=_utc_now(),
            )
        )
        self._clear_cached_token()
        return self._runtime_to_public(saved)

    def _clear_cached_token(self) -> None:
        with self._token_lock:
            self._cached_tenant_access_token = None
            self._cached_tenant_access_token_expires_at = None

    def _require_runtime_settings(self) -> FeishuRuntimeSettings:
        runtime = self.get_runtime_settings()
        if (runtime.app_id or "").strip() == "" or (runtime.app_secret or "").strip() == "":
            raise RuntimeError("飞书应用配置未完成，请先到设置 -> 飞书设置保存 App ID 和 App Secret。")
        return runtime

    def _cached_token_is_valid(self) -> bool:
        if self._cached_tenant_access_token is None or self._cached_tenant_access_token_expires_at is None:
            return False
        return _utc_now() + self.TOKEN_REFRESH_BUFFER < self._cached_tenant_access_token_expires_at

    def _friendly_auth_failure_message(self, raw_message: str) -> str:
        lowered = raw_message.lower()
        if "timeout" in lowered or "timed out" in lowered or "temporary failure" in lowered:
            return "飞书接口网络请求失败，请稍后重试。"
        return "飞书应用凭据无效，请检查 App ID / App Secret，并确认应用已发布可用。"

    def _refresh_tenant_access_token_if_needed(self) -> str:
        if self._cached_token_is_valid():
            assert self._cached_tenant_access_token is not None
            return self._cached_tenant_access_token

        runtime = self._require_runtime_settings()
        try:
            payload = self._request_json(
                method="POST",
                url=self.TENANT_ACCESS_TOKEN_URL,
                payload={
                    "app_id": (runtime.app_id or "").strip(),
                    "app_secret": (runtime.app_secret or "").strip(),
                },
                include_auth=False,
            )
        except RuntimeError as exc:
            raise RuntimeError(self._friendly_auth_failure_message(str(exc))) from exc

        token = str(payload.get("tenant_access_token") or "").strip()
        expire_seconds = int(payload.get("expire") or 0)
        if token == "" or expire_seconds <= 0:
            raise RuntimeError("飞书应用凭据无效，请检查 App ID / App Secret，并确认应用已发布可用。")
        self._cached_tenant_access_token = token
        self._cached_tenant_access_token_expires_at = _utc_now() + timedelta(seconds=expire_seconds)
        return token

    def _get_tenant_access_token(self) -> str:
        if self._cached_token_is_valid():
            assert self._cached_tenant_access_token is not None
            return self._cached_tenant_access_token
        with self._token_lock:
            return self._refresh_tenant_access_token_if_needed()

    def build_bitable_url(
        self,
        *,
        app_token: str,
        table_id: str,
        view_id: str | None = None,
        host: str | None = None,
    ) -> str:
        normalized_app_token = app_token.strip()
        normalized_table_id = table_id.strip()
        normalized_view_id = (view_id or "").strip() or None
        normalized_host = (host or self.DEFAULT_BITABLE_HOST).strip() or self.DEFAULT_BITABLE_HOST
        query = {"table": normalized_table_id}
        if normalized_view_id is not None:
            query["view"] = normalized_view_id
        return f"https://{normalized_host}/base/{normalized_app_token}?{parse.urlencode(query)}"

    def parse_bitable_url(self, raw_url: str) -> dict[str, str | None]:
        normalized = raw_url.strip()
        if normalized == "":
            raise ValueError("飞书多维表格地址不能为空。")

        parsed = parse.urlparse(normalized)
        scheme = parsed.scheme.lower()
        host = parsed.netloc.lower().strip()
        if scheme not in {"http", "https"} or host == "":
            raise ValueError("飞书多维表格地址格式错误，请粘贴完整链接。")
        if not (host == "feishu.cn" or host.endswith(".feishu.cn")):
            raise ValueError("当前只支持飞书多维表格地址，请确认链接域名为 feishu.cn。")

        path_parts = [part for part in parsed.path.split("/") if part.strip() != ""]
        if len(path_parts) < 2 or path_parts[0] != "base":
            raise ValueError("飞书多维表格地址格式错误，未识别到 /base/{app_token}。")
        app_token = path_parts[1].strip()
        if app_token == "":
            raise ValueError("飞书多维表格地址里缺少 Base Token。")

        query = parse.parse_qs(parsed.query, keep_blank_values=False)
        table_id = ((query.get("table") or [""])[0]).strip()
        if table_id == "":
            raise ValueError("飞书多维表格地址里缺少 table 参数。")
        view_id = ((query.get("view") or [""])[0]).strip() or None
        normalized_url = self.build_bitable_url(
            app_token=app_token,
            table_id=table_id,
            view_id=view_id,
            host=host,
        )
        return {
            "normalized_url": normalized_url,
            "app_token": app_token,
            "table_id": table_id,
            "view_id": view_id,
            "host": host,
        }

    def validate_bitable_url(self, raw_url: str) -> FeishuBitableValidationResponse:
        try:
            parsed = self.parse_bitable_url(raw_url)
            runtime = self.get_runtime_settings()
            configured_app_id = (runtime.app_id or "").strip()
            if configured_app_id != "" and configured_app_id == str(parsed["app_token"]):
                return FeishuBitableValidationResponse(
                    ok=False,
                    message="当前飞书设置里填入的是多维表格 Base Token，不是飞书自建应用的 App ID。请到 设置 -> 飞书设置，改填企业自建应用的 App ID 和 App Secret。",
                    normalized_url=str(parsed["normalized_url"]),
                    parsed_app_token=str(parsed["app_token"]),
                    parsed_table_id=str(parsed["table_id"]),
                    parsed_view_id=parsed["view_id"],
                )
            self.list_bitable_records(
                app_token=str(parsed["app_token"]),
                table_id=str(parsed["table_id"]),
                page_size=1,
            )
            return FeishuBitableValidationResponse(
                ok=True,
                message="地址可用，当前飞书应用可以读取该多维表格。",
                normalized_url=str(parsed["normalized_url"]),
                parsed_app_token=str(parsed["app_token"]),
                parsed_table_id=str(parsed["table_id"]),
                parsed_view_id=parsed["view_id"],
            )
        except ValueError as exc:
            return FeishuBitableValidationResponse(ok=False, message=str(exc))
        except RuntimeError as exc:
            message = self._friendly_validation_message(str(exc))
            parsed_payload: dict[str, str | None] = {}
            try:
                parsed_payload = self.parse_bitable_url(raw_url)
            except ValueError:
                parsed_payload = {}
            return FeishuBitableValidationResponse(
                ok=False,
                message=message,
                normalized_url=str(parsed_payload.get("normalized_url") or ""),
                parsed_app_token=str(parsed_payload.get("app_token") or ""),
                parsed_table_id=str(parsed_payload.get("table_id") or ""),
                parsed_view_id=parsed_payload.get("view_id"),
            )

    def _friendly_validation_message(self, raw_message: str) -> str:
        normalized = raw_message.strip()
        if normalized == "":
            return "飞书地址验证失败。"
        if "飞书应用配置未完成" in normalized:
            return "当前还没有配置飞书 App ID / App Secret，请先到设置 -> 飞书设置保存。"
        if "飞书应用凭据无效" in normalized:
            return normalized
        lowered = normalized.lower()
        if " 401 " in f" {lowered} " or " 403 " in f" {lowered} " or "permission" in lowered or "无权限" in normalized:
            return "当前飞书应用对该多维表格没有读取权限，或该表不存在。"
        if " 404 " in f" {lowered} ":
            return "未找到对应的多维表格，请确认地址是否正确。"
        if "timeout" in lowered or "timed out" in lowered or "temporary failure" in lowered:
            return "飞书接口网络请求失败，请稍后重试。"
        if normalized.startswith("飞书接口失败："):
            return f"飞书地址验证失败：{normalized}"
        return f"飞书接口验证失败：{normalized}"

    def _records_base_url(self, *, app_token: str, table_id: str) -> str:
        return f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"

    def _fields_base_url(self, *, app_token: str, table_id: str) -> str:
        return f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"

    def _tables_base_url(self, *, app_token: str) -> str:
        return f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables"

    def _request_json(
        self,
        *,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
        include_auth: bool = True,
    ) -> dict[str, Any]:
        normalized_url = url
        if query_params:
            filtered_query = {
                key: value
                for key, value in query_params.items()
                if value is not None and str(value).strip() != ""
            }
            if filtered_query:
                normalized_url += "?" + parse.urlencode(filtered_query)

        headers = {"Accept": "application/json", "Content-Type": "application/json; charset=utf-8"}
        if include_auth:
            headers["Authorization"] = f"Bearer {self._get_tenant_access_token()}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = request.Request(normalized_url, data=body, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=45) as response:
                raw = response.read().decode("utf-8")
        except urlerror.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                detail = str(exc)
            raise RuntimeError(f"飞书接口失败：{exc.code} {detail}") from exc
        except Exception as exc:
            raise RuntimeError(f"飞书接口失败：{exc}") from exc

        try:
            parsed = json.loads(raw or "{}")
        except Exception as exc:
            raise RuntimeError(f"飞书返回不是有效 JSON：{raw[:400]}") from exc

        if not isinstance(parsed, dict):
            raise RuntimeError(f"飞书返回格式异常：{raw[:400]}")
        code = parsed.get("code")
        if code not in {None, 0}:
            message = parsed.get("msg") or parsed.get("message") or raw[:400]
            raise RuntimeError(f"飞书接口返回错误：code={code}, message={message}")
        return parsed

    def list_bitable_records(self, *, app_token: str, table_id: str, page_size: int = 100) -> list[dict[str, Any]]:
        self._require_runtime_settings()
        base_url = self._records_base_url(app_token=app_token, table_id=table_id)
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            payload = self._request_json(
                method="GET",
                url=base_url,
                query_params={"page_size": page_size, "page_token": page_token},
            )
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            batch = data.get("items") if isinstance(data.get("items"), list) else []
            items.extend(item for item in batch if isinstance(item, dict))
            has_more = bool(data.get("has_more"))
            page_token = str(data.get("page_token") or "").strip() or None
            if not has_more or page_token is None:
                break
        return items

    def list_bitable_fields(self, *, app_token: str, table_id: str, page_size: int = 100) -> list[dict[str, Any]]:
        self._require_runtime_settings()
        base_url = self._fields_base_url(app_token=app_token, table_id=table_id)
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            payload = self._request_json(
                method="GET",
                url=base_url,
                query_params={"page_size": page_size, "page_token": page_token},
            )
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            batch = data.get("items") if isinstance(data.get("items"), list) else []
            items.extend(item for item in batch if isinstance(item, dict))
            has_more = bool(data.get("has_more"))
            page_token = str(data.get("page_token") or "").strip() or None
            if not has_more or page_token is None:
                break
        return items

    def list_bitable_tables(self, *, app_token: str, page_size: int = 100) -> list[dict[str, Any]]:
        """列出一个多维表格 app 下的全部分页（table）。"""

        self._require_runtime_settings()
        base_url = self._tables_base_url(app_token=app_token)
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            payload = self._request_json(
                method="GET",
                url=base_url,
                query_params={"page_size": page_size, "page_token": page_token},
            )
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            batch = data.get("items") if isinstance(data.get("items"), list) else []
            items.extend(item for item in batch if isinstance(item, dict))
            has_more = bool(data.get("has_more"))
            page_token = str(data.get("page_token") or "").strip() or None
            if not has_more or page_token is None:
                break
        return items

    def list_bitable_records_page(
        self,
        *,
        app_token: str,
        table_id: str,
        page_size: int = 5,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        self._require_runtime_settings()
        base_url = self._records_base_url(app_token=app_token, table_id=table_id)
        payload = self._request_json(
            method="GET",
            url=base_url,
            query_params={"page_size": page_size, "page_token": page_token},
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        items = data.get("items") if isinstance(data.get("items"), list) else []
        next_page_token = str(data.get("page_token") or "").strip() or None
        return {
            "items": [item for item in items if isinstance(item, dict)],
            "has_more": bool(data.get("has_more")),
            "page_token": next_page_token,
        }

    def get_bitable_record(self, *, app_token: str, table_id: str, record_id: str) -> dict[str, Any]:
        self._require_runtime_settings()
        url = self._records_base_url(app_token=app_token, table_id=table_id) + f"/{record_id}"
        payload = self._request_json(
            method="GET",
            url=url,
        )
        data = payload.get("data")
        if isinstance(data, dict):
            record = data.get("record")
            if isinstance(record, dict):
                return record
        return {}

    def create_bitable_record(
        self,
        *,
        app_token: str,
        table_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_runtime_settings()
        url = self._records_base_url(app_token=app_token, table_id=table_id)
        payload = self._request_json(
            method="POST",
            url=url,
            payload={"fields": fields},
        )
        data = payload.get("data")
        if isinstance(data, dict):
            record = data.get("record")
            if isinstance(record, dict):
                return record
        return {}

    def update_bitable_record(
        self,
        *,
        app_token: str,
        table_id: str,
        record_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_runtime_settings()
        url = self._records_base_url(app_token=app_token, table_id=table_id) + f"/{record_id}"
        payload = self._request_json(
            method="PUT",
            url=url,
            payload={"fields": fields},
        )
        data = payload.get("data")
        if isinstance(data, dict):
            record = data.get("record")
            if isinstance(record, dict):
                return record
        return {}

    def delete_bitable_record(self, *, app_token: str, table_id: str, record_id: str) -> bool:
        self._require_runtime_settings()
        url = self._records_base_url(app_token=app_token, table_id=table_id) + f"/{record_id}"
        self._request_json(
            method="DELETE",
            url=url,
        )
        return True
