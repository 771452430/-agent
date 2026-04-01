#!/usr/bin/env python3
from __future__ import annotations

import base64
import getpass
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from hashlib import sha256
from typing import Any

AUTH_PATH = "/iuap-api-auth/open-auth/selfAppAuth/base/v1/getAccessToken"
DEFAULT_AUTH_BASE_URL = "https://c2.yonyoucloud.com"
NOTIFY_PATHS = (
    "/iuap-api-gateway/yonbip/uspace/rest/openapi/idempotent/work/notify/push",
    "/yonbip/uspace/rest/openapi/idempotent/work/notify/push",
)


class HttpJsonError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, body: str = "", reason: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.body = body
        self.reason = reason


def prompt_required(
    label: str,
    value: str | None = None,
    *,
    secret: bool = False,
    env_names: tuple[str, ...] = (),
) -> str:
    if value:
        return value

    for env_name in env_names:
        env_value = os.getenv(env_name)
        if env_value:
            return env_value

    prompt = f"请输入{label}: "
    entered = getpass.getpass(prompt) if secret else input(prompt)
    entered = entered.strip()
    if not entered:
        raise SystemExit(f"缺少必填参数：{label}")
    return entered


def parse_json_text(raw: str | None, *, default: Any, field_name: str) -> Any:
    if raw is None or raw.strip() == "":
        return default

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{field_name} 不是合法 JSON: {exc}") from exc


def build_signature(parameters: dict[str, str], app_secret: str) -> str:
    source = "".join(f"{key}{parameters[key]}" for key in sorted(parameters))
    digest = hmac.new(
        app_secret.encode("utf-8"),
        source.encode("utf-8"),
        sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_url(base_url: str, path: str, query: dict[str, str] | None = None) -> str:
    full_url = f"{base_url.rstrip('/')}{path}"
    if not query:
        return full_url
    return f"{full_url}?{urllib.parse.urlencode(query)}"


def http_json(
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
        raise HttpJsonError(
            f"HTTP {exc.code}: {body}",
            code=exc.code,
            body=body,
        ) from exc
    except urllib.error.URLError as exc:
        raise HttpJsonError(
            f"请求失败: {exc.reason}",
            reason=str(exc.reason),
        ) from exc


def fetch_access_token(
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
    parameters["signature"] = build_signature(parameters, app_secret)
    url = build_url(auth_base_url, AUTH_PATH, parameters)
    try:
        return http_json(url, method="GET", timeout=timeout)
    except HttpJsonError as exc:
        raise SystemExit(str(exc)) from exc


def extract_access_token(response: dict[str, Any]) -> str:
    if response.get("code") != "00000":
        raise SystemExit(json.dumps(response, ensure_ascii=False, indent=2))

    data = response.get("data")
    if not isinstance(data, dict) or not data.get("access_token"):
        raise SystemExit(f"认证响应缺少 access_token: {json.dumps(response, ensure_ascii=False, indent=2)}")

    return str(data["access_token"])


def post_work_notify(
    *,
    openapi_base_url: str,
    access_token: str,
    body: dict[str, Any],
    timeout: int = 30,
) -> dict[str, Any]:
    errors: list[str] = []
    for notify_path in NOTIFY_PATHS:
        url = build_url(openapi_base_url, notify_path, {"access_token": access_token})
        try:
            return http_json(url, method="POST", payload=body, timeout=timeout)
        except HttpJsonError as exc:
            errors.append(f"{notify_path} -> {exc}")
            if exc.code not in {401, 404}:
                raise SystemExit(str(exc)) from exc

    raise SystemExit("通知接口调用失败，已尝试路径：\n" + "\n".join(errors))
