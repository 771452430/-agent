"""用友工作通知发送服务。"""

from __future__ import annotations

import base64
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from .work_notify_settings_service import WorkNotifySettingsService


AUTH_PATH = "/iuap-api-auth/open-auth/selfAppAuth/base/v1/getAccessToken"
DEFAULT_AUTH_BASE_URL = "https://c2.yonyoucloud.com"
DEFAULT_OPENAPI_BASE_URL = "https://c1.yonyoucloud.com"
NOTIFY_PATHS = (
    "/iuap-api-gateway/yonbip/uspace/rest/openapi/idempotent/work/notify/push",
    "/yonbip/uspace/rest/openapi/idempotent/work/notify/push",
)


class YonyouWorkNotifyError(RuntimeError):
    """用友工作通知调用错误。"""

    def __init__(self, message: str, *, code: int | None = None, body: str = "", reason: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.body = body
        self.reason = reason


@dataclass(frozen=True)
class ResolvedYonyouCredentials:
    """发送用友工作通知所需的认证信息。"""

    app_key: str
    app_secret: str
    auth_base_url: str


class YonyouWorkNotifyService:
    """处理 access_token 获取与工作通知发送。"""

    def __init__(self, work_notify_settings_service: WorkNotifySettingsService | None = None) -> None:
        self.work_notify_settings_service = work_notify_settings_service

    def resolve_credentials(
        self,
        *,
        app_key: str | None,
        app_secret: str | None,
        auth_base_url: str | None,
        openapi_base_url: str,
    ) -> ResolvedYonyouCredentials:
        stored_runtime = (
            self.work_notify_settings_service.get_runtime_settings()
            if self.work_notify_settings_service is not None
            else None
        )
        normalized_app_key = (
            app_key
            or (stored_runtime.app_key if stored_runtime is not None else None)
            or os.getenv("YONYOU_APP_KEY", "")
        ).strip()
        normalized_app_secret = (
            app_secret
            or (stored_runtime.app_secret if stored_runtime is not None else None)
            or os.getenv("YONYOU_APP_SECRET", "")
        ).strip()
        normalized_auth_base_url = (
            auth_base_url
            or os.getenv("YONYOU_AUTH_BASE_URL")
            or openapi_base_url
            or DEFAULT_AUTH_BASE_URL
        ).strip()

        if normalized_app_key == "" or normalized_app_secret == "":
            raise YonyouWorkNotifyError(
                "缺少用友应用凭据：请在工作台设置 -> 工作通知设置中保存 AppKey / AppSecret，"
                "或在工具入参中传 `app_key` / `app_secret`，或配置环境变量 "
                "`YONYOU_APP_KEY` / `YONYOU_APP_SECRET`。"
            )

        if normalized_auth_base_url == "":
            raise YonyouWorkNotifyError("缺少鉴权域名：请传 `auth_base_url`，或配置 `YONYOU_AUTH_BASE_URL`。")

        return ResolvedYonyouCredentials(
            app_key=normalized_app_key,
            app_secret=normalized_app_secret,
            auth_base_url=normalized_auth_base_url,
        )

    def build_signature(self, parameters: dict[str, str], app_secret: str) -> str:
        source = "".join(f"{key}{parameters[key]}" for key in sorted(parameters))
        digest = hmac.new(
            app_secret.encode("utf-8"),
            source.encode("utf-8"),
            sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def build_url(self, base_url: str, path: str, query: dict[str, str] | None = None) -> str:
        full_url = f"{base_url.rstrip('/')}{path}"
        if not query:
            return full_url
        return f"{full_url}?{urllib.parse.urlencode(query)}"

    def http_json(
        self,
        url: str,
        *,
        method: str,
        payload: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        request = urllib.request.Request(
            url=url,
            data=data,
            headers=headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise YonyouWorkNotifyError(
                f"HTTP {exc.code}: {body}",
                code=exc.code,
                body=body,
            ) from exc
        except urllib.error.URLError as exc:
            raise YonyouWorkNotifyError(
                f"请求失败: {exc.reason}",
                reason=str(exc.reason),
            ) from exc

    def fetch_access_token(
        self,
        *,
        app_key: str,
        app_secret: str,
        auth_base_url: str,
        timeout: int = 30,
    ) -> dict[str, Any]:
        parameters = {
            "appKey": app_key,
            "timestamp": str(int(time.time() * 1000)),
        }
        parameters["signature"] = self.build_signature(parameters, app_secret)
        url = self.build_url(auth_base_url, AUTH_PATH, parameters)
        return self.http_json(url, method="GET", timeout=timeout)

    def extract_access_token(self, response: dict[str, Any]) -> str:
        if str(response.get("code")) != "00000":
            raise YonyouWorkNotifyError(
                "获取 access_token 失败: " + json.dumps(response, ensure_ascii=False, indent=2)
            )

        data = response.get("data")
        if not isinstance(data, dict) or not data.get("access_token"):
            raise YonyouWorkNotifyError(
                "认证响应缺少 access_token: " + json.dumps(response, ensure_ascii=False, indent=2)
            )

        return str(data["access_token"])

    def post_work_notify(
        self,
        *,
        openapi_base_url: str,
        access_token: str,
        body: dict[str, Any],
        timeout: int = 30,
    ) -> tuple[dict[str, Any], str]:
        errors: list[str] = []
        for notify_path in NOTIFY_PATHS:
            url = self.build_url(openapi_base_url, notify_path, {"access_token": access_token})
            try:
                return self.http_json(url, method="POST", payload=body, timeout=timeout), notify_path
            except YonyouWorkNotifyError as exc:
                errors.append(f"{notify_path} -> {exc}")
                if exc.code not in {401, 404}:
                    raise

        raise YonyouWorkNotifyError("通知接口调用失败，已尝试路径：\n" + "\n".join(errors))

    def send_work_notify(
        self,
        *,
        openapi_base_url: str | None = None,
        src_msg_id: str,
        yht_user_ids: list[str],
        title: str,
        content: str,
        label_code: str | None = None,
        service_code: str | None = None,
        url: str | None = None,
        web_url: str | None = None,
        mini_program_url: str | None = None,
        app_id: str | None = None,
        tab_id: str | None = None,
        catcode1st: str | None = None,
        attributes: dict[str, Any] | None = None,
        esn_data: dict[str, Any] | list[dict[str, Any]] | None = None,
        app_key: str | None = None,
        app_secret: str | None = None,
        auth_base_url: str | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        normalized_openapi_base_url = (
            openapi_base_url
            or os.getenv("YONYOU_OPENAPI_BASE_URL")
            or DEFAULT_OPENAPI_BASE_URL
        ).strip()
        normalized_src_msg_id = src_msg_id.strip()
        normalized_title = title.strip()
        normalized_content = content.strip()
        normalized_user_ids = list(dict.fromkeys(user_id.strip() for user_id in yht_user_ids if user_id.strip()))

        if normalized_openapi_base_url == "":
            raise YonyouWorkNotifyError("缺少 `openapi_base_url`。")
        if normalized_src_msg_id == "":
            raise YonyouWorkNotifyError("缺少 `src_msg_id`。")
        if normalized_title == "":
            raise YonyouWorkNotifyError("缺少 `title`。")
        if normalized_content == "":
            raise YonyouWorkNotifyError("缺少 `content`。")
        if len(normalized_user_ids) == 0:
            raise YonyouWorkNotifyError("缺少 `yht_user_ids`。")

        credentials = self.resolve_credentials(
            app_key=app_key,
            app_secret=app_secret,
            auth_base_url=auth_base_url,
            openapi_base_url=normalized_openapi_base_url,
        )

        body: dict[str, Any] = {
            "srcMsgId": normalized_src_msg_id,
            "yhtUserIds": normalized_user_ids,
            "title": normalized_title,
            "content": normalized_content,
        }

        optional_fields = {
            "labelCode": label_code,
            "serviceCode": service_code,
            "url": url,
            "webUrl": web_url,
            "miniProgramUrl": mini_program_url,
            "appId": app_id,
            "tabId": tab_id,
            "catcode1st": catcode1st,
            "attributes": attributes,
            "esnData": esn_data,
        }
        for key, value in optional_fields.items():
            if value is not None and value != "":
                body[key] = value

        token_response = self.fetch_access_token(
            app_key=credentials.app_key,
            app_secret=credentials.app_secret,
            auth_base_url=credentials.auth_base_url,
            timeout=timeout,
        )
        access_token = self.extract_access_token(token_response)
        notify_response, used_notify_path = self.post_work_notify(
            openapi_base_url=normalized_openapi_base_url,
            access_token=access_token,
            body=body,
            timeout=timeout,
        )

        response_data = notify_response.get("data")
        if not isinstance(response_data, dict):
            response_data = {}

        return {
            "ok": str(notify_response.get("code")) == "200" and str(response_data.get("flag")) == "0",
            "code": str(notify_response.get("code", "")),
            "message": str(notify_response.get("message", "")),
            "data": response_data,
            "used_notify_path": used_notify_path,
        }
