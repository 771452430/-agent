#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from typing import Any

from client import (
    DEFAULT_AUTH_BASE_URL,
    extract_access_token,
    fetch_access_token,
    parse_json_text,
    post_work_notify,
    prompt_required,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="发送用友幂等工作通知；使用前请先准备 3 个必填值：appKey、appSecret、yhtUserId。",
    )
    parser.add_argument("--app-key", help="应用 appKey")
    parser.add_argument("--app-secret", help="应用 appSecret")
    parser.add_argument(
        "--auth-base-url",
        help=f"鉴权基础域名；不传时优先复用 --openapi-base-url，否则回落到 {DEFAULT_AUTH_BASE_URL}",
    )
    parser.add_argument("--openapi-base-url", help="租户 OpenAPI 基础域名，例如 https://xxx.diwork.com")
    parser.add_argument("--src-msg-id", help="消息唯一标识，用于幂等")
    parser.add_argument(
        "--yht-user-id",
        action="append",
        default=[],
        help="友互通 userId，可重复传入",
    )
    parser.add_argument(
        "--yht-user-ids",
        help="逗号分隔的友互通 userId 列表",
    )
    parser.add_argument("--label-code", help="领域编码")
    parser.add_argument("--title", help="通知标题")
    parser.add_argument("--content", help="通知内容")
    parser.add_argument("--url", help="移动端打开地址")
    parser.add_argument("--web-url", help="Web 端打开地址")
    parser.add_argument("--mini-program-url", help="友空间小程序地址")
    parser.add_argument("--app-id", help="应用 appId")
    parser.add_argument("--tab-id", help="移动端自定义分类")
    parser.add_argument("--catcode1st", help="分类 id")
    parser.add_argument("--service-code", help="服务编码")
    parser.add_argument("--attributes", help="自定义扩展属性 JSON，默认不传")
    esn_group = parser.add_mutually_exclusive_group()
    esn_group.add_argument("--esn-data", help="业务属性 JSON")
    esn_group.add_argument("--from-id", help="快捷写入 esnData[0].fromId 的业务 id")
    parser.add_argument("--timeout", type=int, default=30, help="请求超时秒数，默认 30")
    return parser.parse_args()


def prompt_if_missing(label: str, value: str | None) -> str:
    return prompt_required(label, value)


def collect_user_ids(args: argparse.Namespace) -> list[str]:
    user_ids: list[str] = []
    user_ids.extend(user_id.strip() for user_id in args.yht_user_id if user_id and user_id.strip())
    if args.yht_user_ids:
        user_ids.extend(
            user_id.strip()
            for user_id in args.yht_user_ids.split(",")
            if user_id.strip()
        )

    if user_ids:
        return list(dict.fromkeys(user_ids))

    prompted = prompt_required("yhtUserIds（多个请用英文逗号分隔）")
    return list(dict.fromkeys(user_id.strip() for user_id in prompted.split(",") if user_id.strip()))


def build_esn_data(args: argparse.Namespace) -> Any:
    if args.esn_data:
        return parse_json_text(args.esn_data, default=None, field_name="esnData")
    if args.from_id:
        return [{"fromId": args.from_id}]
    return None


def add_optional(body: dict[str, Any], key: str, value: Any) -> None:
    if value is not None and value != "":
        body[key] = value


def main() -> None:
    args = parse_args()

    app_key = prompt_required("appKey", args.app_key, env_names=("YONYOU_APP_KEY",))
    app_secret = prompt_required(
        "appSecret",
        args.app_secret,
        secret=True,
        env_names=("YONYOU_APP_SECRET",),
    )
    openapi_base_url = prompt_if_missing("租户 OpenAPI 域名", args.openapi_base_url)
    auth_base_url = args.auth_base_url or openapi_base_url or DEFAULT_AUTH_BASE_URL
    yht_user_ids = collect_user_ids(args)
    src_msg_id = prompt_if_missing("srcMsgId", args.src_msg_id)
    title = prompt_if_missing("title", args.title)
    content = prompt_if_missing("content", args.content)

    attributes = parse_json_text(args.attributes, default=None, field_name="attributes")
    esn_data = build_esn_data(args)

    body: dict[str, Any] = {
        "srcMsgId": src_msg_id,
        "yhtUserIds": yht_user_ids,
        "title": title,
        "content": content,
    }
    add_optional(body, "labelCode", args.label_code)
    add_optional(body, "url", args.url)
    add_optional(body, "webUrl", args.web_url)
    add_optional(body, "miniProgramUrl", args.mini_program_url)
    add_optional(body, "appId", args.app_id)
    add_optional(body, "tabId", args.tab_id)
    add_optional(body, "catcode1st", args.catcode1st)
    add_optional(body, "serviceCode", args.service_code)
    add_optional(body, "attributes", attributes)
    add_optional(body, "esnData", esn_data)

    token_response = fetch_access_token(
        app_key=app_key,
        app_secret=app_secret,
        auth_base_url=auth_base_url,
        timeout=args.timeout,
    )
    access_token = extract_access_token(token_response)

    response = post_work_notify(
        openapi_base_url=openapi_base_url,
        access_token=access_token,
        body=body,
        timeout=args.timeout,
    )
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
