"""飞书多维表格 CRUD 联调脚本。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.services.feishu_service import FeishuService
from backend.app.services.feishu_settings_store import FeishuSettingsStore
from backend.app.settings import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="验证飞书多维表格的增删改查能力。")
    parser.add_argument("--url", required=True, help="完整飞书多维表格 URL")
    parser.add_argument(
        "--question-field",
        default="问题",
        help="写入测试行时使用的问题字段名，默认 `问题`。",
    )
    parser.add_argument(
        "--status-field",
        default="处理状态",
        help="写入测试行时使用的状态字段名，默认 `处理状态`。",
    )
    parser.add_argument(
        "--answer-field",
        default="AI解决方案",
        help="写入测试行时使用的答案字段名，默认 `AI解决方案`。",
    )
    parser.add_argument(
        "--keep-record",
        action="store_true",
        help="保留新建的测试记录，默认执行完会自动删除。",
    )
    args = parser.parse_args()

    try:
        settings = load_settings()
        feishu_service = FeishuService(FeishuSettingsStore(settings.sqlite_path))
        parsed = feishu_service.parse_bitable_url(args.url)
        app_token = str(parsed["app_token"])
        table_id = str(parsed["table_id"])

        print("== URL 解析 ==")
        print(
            json.dumps(
                {
                    "normalized_url": parsed["normalized_url"],
                    "app_token": app_token,
                    "table_id": table_id,
                    "view_id": parsed["view_id"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print("\n== 读列表 ==")
        records = feishu_service.list_bitable_records(app_token=app_token, table_id=table_id, page_size=5)
        print(f"读取成功，当前前 5 条共拿到 {len(records)} 条。")

        print("\n== 新增 ==")
        created = feishu_service.create_bitable_record(
            app_token=app_token,
            table_id=table_id,
            fields={
                args.question_field: "【Codex 联调测试】这是一条自动创建的飞书多维表格测试记录",
                args.status_field: "待生成",
            },
        )
        record_id = str(created.get("record_id") or created.get("recordId") or "").strip()
        if record_id == "":
            raise RuntimeError(f"新增成功但未返回 record_id：{json.dumps(created, ensure_ascii=False)}")
        print(f"新增成功，record_id={record_id}")

        print("\n== 读取单行 ==")
        fetched = feishu_service.get_bitable_record(app_token=app_token, table_id=table_id, record_id=record_id)
        print(json.dumps(fetched, ensure_ascii=False, indent=2)[:1200])

        print("\n== 更新 ==")
        updated = feishu_service.update_bitable_record(
            app_token=app_token,
            table_id=table_id,
            record_id=record_id,
            fields={
                args.answer_field: "这是一条由 CRUD 联调脚本写入的更新内容。",
                args.status_field: "处理中",
            },
        )
        print(json.dumps(updated, ensure_ascii=False, indent=2)[:1200])

        if args.keep_record:
            print("\n== 删除 ==")
            print("已跳过，使用了 --keep-record。")
            return 0

        print("\n== 删除 ==")
        feishu_service.delete_bitable_record(app_token=app_token, table_id=table_id, record_id=record_id)
        print("删除成功。")
        return 0
    except Exception as exc:
        print(f"联调失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
