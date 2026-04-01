#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from client import DEFAULT_AUTH_BASE_URL, extract_access_token, fetch_access_token, prompt_required


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="获取用友自建应用 access_token；若缺少 appKey/appSecret 会交互式提示输入。",
    )
    parser.add_argument("--app-key", help="应用 appKey")
    parser.add_argument("--app-secret", help="应用 appSecret")
    parser.add_argument(
        "--auth-base-url",
        default=DEFAULT_AUTH_BASE_URL,
        help=f"鉴权基础域名，默认 {DEFAULT_AUTH_BASE_URL}；若业务接口返回非法 token，请改为租户对应的数据中心域名",
    )
    parser.add_argument("--timeout", type=int, default=30, help="请求超时秒数，默认 30")
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出完整 JSON；默认仅输出 access_token",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app_key = prompt_required("appKey", args.app_key, env_names=("YONYOU_APP_KEY",))
    app_secret = prompt_required(
        "appSecret",
        args.app_secret,
        secret=True,
        env_names=("YONYOU_APP_SECRET",),
    )

    response = fetch_access_token(
        app_key=app_key,
        app_secret=app_secret,
        auth_base_url=args.auth_base_url,
        timeout=args.timeout,
    )

    if args.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return

    print(extract_access_token(response))


if __name__ == "__main__":
    main()
