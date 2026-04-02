"""用友联系人查询服务。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .work_notify_settings_service import WorkNotifySettingsService


CONTACTS_URL_TEMPLATE = (
    "https://c2.yonyoucloud.com/yonbip-ec-contacts/contacts/pcUser/pc/search"
    "?vercode=8.3.15&language=zh_CN&locale=zh_CN&et=1775097041.027"
    "&uspace_product_line=pc&keywords=liugangy%40yonyou.com&pageNum=1&pageSize=10"
    "&crossTenant=1&esnAttr=1&ek2=86191eb5fa978f3fa2faa3ac16a3e40bf7b68d18c1f3ed984c61b0afa702c5b8"
)


class YonyouContactsSearchError(RuntimeError):
    """联系人查询错误。"""


class YonyouContactsSearchService:
    """根据邮箱或短账号查询用友联系人，并提取 yhtUserId。"""

    def __init__(self, work_notify_settings_service: WorkNotifySettingsService | None = None) -> None:
        self.work_notify_settings_service = work_notify_settings_service

    def normalize_account(self, raw_value: str) -> str:
        normalized = raw_value.strip()
        if normalized == "":
            raise YonyouContactsSearchError("缺少登记人邮箱或短账号。")
        if "@" in normalized:
            return normalized
        return normalized + "@yonyou.com"

    def build_url(self, account_or_email: str) -> str:
        parsed = urllib.parse.urlsplit(CONTACTS_URL_TEMPLATE)
        query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        next_query_items: list[tuple[str, str]] = []
        replaced = False
        for key, value in query_items:
            if key == "keywords":
                next_query_items.append((key, account_or_email))
                replaced = True
            else:
                next_query_items.append((key, value))
        if not replaced:
            raise YonyouContactsSearchError("联系人查询 URL 模板缺少 keywords 参数。")
        next_query = urllib.parse.urlencode(next_query_items)
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, next_query, parsed.fragment))

    def _resolve_cookie(self, cookie: str | None = None) -> str:
        normalized_cookie = (cookie or "").strip()
        if normalized_cookie != "":
            return normalized_cookie
        stored_runtime = (
            self.work_notify_settings_service.get_runtime_settings()
            if self.work_notify_settings_service is not None
            else None
        )
        normalized_cookie = ((stored_runtime.contacts_cookie if stored_runtime is not None else None) or "").strip()
        if normalized_cookie == "":
            raise YonyouContactsSearchError("缺少联系人查询 Cookie：请到设置 -> 工作通知设置补充。")
        return normalized_cookie

    def search_raw(self, account_or_email: str, *, cookie: str | None = None, timeout: int = 30) -> dict[str, Any]:
        normalized_account = self.normalize_account(account_or_email)
        request = urllib.request.Request(
            url=self.build_url(normalized_account),
            method="GET",
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": "Mozilla/5.0",
                "Cookie": self._resolve_cookie(cookie),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise YonyouContactsSearchError(f"HTTP {exc.code}: {body[:300]}") from exc
        except urllib.error.URLError as exc:
            raise YonyouContactsSearchError(f"联系人查询请求失败: {exc.reason}") from exc

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise YonyouContactsSearchError(f"联系人查询响应不是合法 JSON: {body[:300]}") from exc
        return payload if isinstance(payload, dict) else {"data": payload}

    def resolve_yht_user_ids(self, account_or_email: str, *, cookie: str | None = None, timeout: int = 30) -> list[str]:
        payload = self.search_raw(account_or_email, cookie=cookie, timeout=timeout)
        if int(payload.get("code") or 0) != 200:
            raise YonyouContactsSearchError(
                f"联系人查询失败: code={payload.get('code')} message={payload.get('message')}"
            )
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        user_ids: list[str] = []
        seen: set[str] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            candidate = str(item.get("yhtUserId") or "").strip()
            if candidate == "" or candidate in seen:
                continue
            seen.add(candidate)
            user_ids.append(candidate)
        return user_ids
