#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

URL_TEMPLATE = (
    "https://c2.yonyoucloud.com/yonbip-ec-contacts/contacts/pcUser/pc/search"
    "?vercode=8.3.15&language=zh_CN&locale=zh_CN&et=1775097041.027"
    "&uspace_product_line=pc&keywords=liugangy%40yonyou.com&pageNum=1&pageSize=10"
    "&crossTenant=1&esnAttr=1&ek2=86191eb5fa978f3fa2faa3ac16a3e40bf7b68d18c1f3ed984c61b0afa702c5b8"
)


def prompt_cookie(cookie: str | None) -> str:
    if cookie and cookie.strip():
        return cookie.strip()
    entered = getpass.getpass("请输入本次请求使用的 Cookie: ").strip()
    if entered == "":
        raise SystemExit("缺少必填参数：Cookie")
    return entered


def normalize_account(value: str) -> str:
    normalized = value.strip()
    if normalized == "":
        raise SystemExit("缺少必填参数：account")
    if "@" in normalized:
        return normalized
    return normalized + "@yonyou.com"


def build_url(account: str) -> str:
    parsed = urllib.parse.urlsplit(URL_TEMPLATE)
    query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    replaced = False
    next_query_items: list[tuple[str, str]] = []
    for key, value in query_items:
        if key == "keywords":
            next_query_items.append((key, account))
            replaced = True
        else:
            next_query_items.append((key, value))
    if not replaced:
        raise SystemExit("固定 URL 模板中缺少 keywords 参数。")
    next_query = urllib.parse.urlencode(next_query_items)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, next_query, parsed.fragment))


def main() -> int:
    parser = argparse.ArgumentParser(description="Query the Yonyou contacts search API.")
    parser.add_argument("--account", required=True, help="短账号或完整邮箱")
    parser.add_argument("--cookie", help="本次请求使用的 Cookie；不传则运行时提示输入")
    parser.add_argument("--timeout", type=int, default=30, help="请求超时秒数，默认 30")
    args = parser.parse_args()

    account = normalize_account(args.account)
    cookie = prompt_cookie(args.cookie)
    url = build_url(account)

    request = urllib.request.Request(
        url=url,
        method="GET",
        headers={
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0",
            "Cookie": cookie,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "status": response.getcode(),
                            "error": "响应不是合法 JSON",
                            "body": body[:2000],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    file=sys.stderr,
                )
                return 1
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": exc.code,
                    "error": "HTTPError",
                    "body": body[:2000],
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1
    except urllib.error.URLError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "URLError",
                    "reason": str(exc.reason),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
