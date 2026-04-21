"""巡检 Agent 的业务服务。

这一层负责把“配置存储、LangGraph 编排、外部 HTTP/邮件集成、调度”粘合起来：
- store 负责持久化；
- graph 负责运行顺序；
- service 负责真实副作用（抓面板、调分配接口、发邮件）。

这样拆开后，你在学习时可以清楚区分：
1. 状态存在哪里；
2. 决策链路怎么走；
3. 哪些步骤是外部系统副作用。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
import html
import re
from typing import Any
from urllib import error as urlerror
from urllib import parse
from urllib import request
from uuid import uuid4

from fastapi import HTTPException

from ..graphs.watcher_graph import WatcherAgentGraph, WatcherGraphState
from ..schemas import (
    CreateWatcherRequest,
    ModelConfig,
    ParsedBug,
    UpdateWatcherRequest,
    WatcherAgentConfig,
    WatcherAssignmentResult,
    WatcherFetchTestRequest,
    WatcherFetchTestResponse,
    WatcherRun,
)
from ..settings import AppSettings
from .llm_service import LLMService
from .mail_service import MailService
from .watcher_store import WatcherStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


AUTO_DISABLE_FAILURE_THRESHOLD = 2


class WatcherService:
    """巡检 Agent 的统一服务入口。"""

    def __init__(
        self,
        watcher_store: WatcherStore,
        llm_service: LLMService,
        settings: AppSettings,
        mail_service: MailService,
    ) -> None:
        # WatcherService 是“巡检业务总装配层”：
        # - Store 负责保存配置和运行历史；
        # - LLMService 负责解析 bug、补全负责人建议；
        # - Graph 负责把整条巡检链路按步骤串起来。
        self.watcher_store = watcher_store
        self.llm_service = llm_service
        self.settings = settings
        self.mail_service = mail_service
        self.graph = WatcherAgentGraph(
            llm_service=llm_service,
            fetch_dashboard_json=self._fetch_dashboard_json,
            hydrate_bug_details=self._hydrate_bug_details,
            get_seen_bug_ids=self.watcher_store.get_seen_bug_ids,
            call_assignment_api=self._call_assignment_api,
            send_email=self._send_email,
            persist_run=self._persist_run,
        )

    def _require_runnable_model_config(self, model_config: ModelConfig | None) -> ModelConfig:
        try:
            resolved, _provider = self.llm_service.ensure_model_config_runnable(model_config)
            return resolved
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _resolve_watcher_model_config(self, model_config: ModelConfig | None, *, match_mode: str) -> ModelConfig:
        if match_mode == "fixed_match":
            return self.llm_service.resolve_model_config(model_config)
        return self._require_runnable_model_config(model_config)

    def list_watchers(self) -> list[WatcherAgentConfig]:
        return self.watcher_store.list_watchers()

    def get_watcher(self, watcher_id: str) -> WatcherAgentConfig:
        watcher = self.watcher_store.get_watcher(watcher_id)
        if watcher is None:
            raise HTTPException(status_code=404, detail="Watcher not found")
        return watcher

    def create_watcher(self, request: CreateWatcherRequest) -> WatcherAgentConfig:
        model_config = self._resolve_watcher_model_config(request.model_settings, match_mode=request.match_mode)
        return self.watcher_store.create_watcher(request, model_config)

    def update_watcher(self, watcher_id: str, request: UpdateWatcherRequest) -> WatcherAgentConfig:
        current = self.watcher_store.get_watcher(watcher_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Watcher not found")

        updated = self.watcher_store.update_watcher(
            watcher_id,
            request,
            model_config=(
                self._resolve_watcher_model_config(
                    request.model_settings,
                    match_mode=request.match_mode if request.match_mode is not None else current.match_mode,
                )
                if request.model_settings
                else None
            ),
        )
        assert updated is not None
        return updated

    def list_runs(self, watcher_id: str) -> list[WatcherRun]:
        self.get_watcher(watcher_id)
        return self.watcher_store.list_runs(watcher_id)

    def list_due_watchers(self) -> list[WatcherAgentConfig]:
        return self.watcher_store.list_due_watchers()

    def test_fetch(self, request: WatcherFetchTestRequest) -> WatcherFetchTestResponse:
        # 这个接口只验证“能不能抓到面板内容”，不会真的写运行记录、
        # 也不会调用分配接口或发邮件，适合用户先排查抓取配置。
        result = self._execute_dashboard_request(
            dashboard_url=request.dashboard_url,
            request_method=request.request_method,
            request_headers=request.request_headers,
            request_body_json=request.request_body_json,
            request_body_text=request.request_body_text,
        )
        parsed_bug_preview: list[ParsedBug] = []
        parsed_payload = result.get("parsed_payload")
        if parsed_payload is not None:
            parsed_bug_preview = self.llm_service.preview_bug_list_from_payload(parsed_payload)[:5]
        return WatcherFetchTestResponse(
            ok=result["ok"],
            status_code=result["status_code"],
            message=result["message"],
            dashboard_url=request.dashboard_url,
            request_method=request.request_method,
            request_headers=request.request_headers,
            request_body_json=request.request_body_json,
            request_body_text=request.request_body_text,
            detail_url_template=request.detail_url_template,
            detail_request_method=request.detail_request_method,
            detail_request_headers=request.detail_request_headers,
            detail_request_body_text=request.detail_request_body_text,
            response_content_type=result["content_type"],
            response_body_preview=result["response_body_preview"],
            parsed_item_count=result["parsed_item_count"],
            parsed_bug_count=len(parsed_bug_preview),
            parsed_bug_preview=parsed_bug_preview,
        )

    def run_watcher(
        self,
        watcher_id: str,
        *,
        force_email_snapshot: bool = False,
        force_assign_snapshot: bool = False,
        scheduled_run: bool = False,
    ) -> WatcherRun:
        watcher = self.get_watcher(watcher_id)
        # 先构造一份最小可运行的图状态；
        # 后续每个节点都会在这份状态上逐步补充 parsed_bugs、assignment_results 等字段。
        initial_state: WatcherGraphState = {
            "watcher": watcher,
            "run_started_at": _utc_now(),
            "force_email_snapshot": force_email_snapshot,
            "force_assign_snapshot": force_assign_snapshot,
            "emailed": False,
            "assignment_results": [],
            "assignment_bugs": [],
            "new_bugs": [],
            "parsed_bugs": [],
            "run_status": "success",
            "summary": "巡检开始执行。",
            "error_message": None,
        }

        try:
            # LangGraph 负责真正的巡检流程推进；
            # service 这一层则负责准备输入、接住输出，以及兜底异常处理。
            final_state = self.graph.graph.invoke(initial_state)
            persisted_run = final_state.get("persisted_run")
            if isinstance(persisted_run, WatcherRun):
                if scheduled_run:
                    return self._handle_scheduled_run_result(watcher, persisted_run)
                return persisted_run
            raise RuntimeError("巡检图未返回持久化运行记录。")
        except HTTPException:
            raise
        except Exception as exc:
            # 即使中途异常，也要落一条 failed run，方便前端和调度器看到真实失败原因。
            failed_run = WatcherRun(
                id=str(uuid4()),
                agent_id=watcher.id,
                status="failed",
                started_at=initial_state["run_started_at"],
                ended_at=_utc_now(),
                fetched_count=0,
                parsed_count=0,
                new_bug_count=0,
                assigned_count=0,
                emailed=False,
                summary="巡检执行异常中断。",
                error_message=str(exc),
                assignment_results=[],
            )
            self.watcher_store.record_run(watcher.id, failed_run)
            if scheduled_run:
                return self._handle_scheduled_run_result(watcher, failed_run)
            return failed_run

    def _compose_auto_disable_alert_email(
        self,
        watcher: WatcherAgentConfig,
        *,
        disabled_at: datetime,
        reason: str,
        consecutive_failure_count: int,
    ) -> tuple[str, str, str]:
        formatted_time = disabled_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        safe_reason = reason.strip() or "未知错误"
        subject = f"[巡检 Agent] {watcher.name} 已自动停用"
        body = "\n".join(
            [
                f"巡检 Agent：{watcher.name}",
                f"停用时间：{formatted_time}",
                f"连续失败次数：{consecutive_failure_count}",
                f"面板地址：{watcher.dashboard_url}",
                "",
                "最近失败原因：",
                safe_reason,
                "",
                "系统已自动关闭该 Agent 的轮巡；如需继续轮巡，请修复问题后手动重新启用。",
            ]
        ).strip()
        html_body = f"""
        <div style="padding:24px;background:#0f172a;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
          <div style="max-width:720px;margin:0 auto;background:#111827;border:1px solid #334155;border-radius:16px;padding:20px;">
            <div style="font-size:14px;color:#fbbf24;">巡检 Agent 自动停用告警</div>
            <h2 style="margin:8px 0 0;font-size:24px;color:#f8fafc;">{html.escape(watcher.name)}</h2>
            <div style="margin-top:16px;font-size:14px;line-height:1.8;color:#cbd5e1;">
              <div><strong>停用时间：</strong>{html.escape(formatted_time)}</div>
              <div><strong>连续失败次数：</strong>{consecutive_failure_count}</div>
              <div><strong>面板地址：</strong><a href="{html.escape(watcher.dashboard_url)}" style="color:#7dd3fc;">{html.escape(watcher.dashboard_url)}</a></div>
            </div>
            <div style="margin-top:16px;background:#0f172a;border:1px solid #334155;border-radius:12px;padding:16px;">
              <div style="font-size:13px;color:#94a3b8;">最近失败原因</div>
              <div style="margin-top:8px;white-space:pre-wrap;line-height:1.7;color:#e2e8f0;">{html.escape(safe_reason)}</div>
            </div>
            <div style="margin-top:16px;color:#f8fafc;">系统已自动关闭该 Agent 的轮巡；如需继续轮巡，请修复问题后手动重新启用。</div>
          </div>
        </div>
        """.strip()
        return subject, body, html_body

    def _handle_scheduled_run_result(self, watcher: WatcherAgentConfig, run: WatcherRun) -> WatcherRun:
        # 定时运行和手动运行的差别主要在这里：
        # 定时运行需要额外套一层“连续失败自动停用”的保护策略，防止错误无限重复。
        policy = self.watcher_store.apply_scheduled_run_policy(
            watcher.id,
            run,
            failure_threshold=AUTO_DISABLE_FAILURE_THRESHOLD,
        )
        updated_run = run.model_copy(update={"summary": str(policy["summary"])})
        if not bool(policy["auto_disabled"]):
            return updated_run

        disabled_at = policy["auto_disabled_at"]
        if not isinstance(disabled_at, datetime):
            disabled_at = run.ended_at or _utc_now()
        auto_disabled_reason = str(policy["auto_disabled_reason"] or run.error_message or run.summary or "轮巡失败")
        subject, body, html_body = self._compose_auto_disable_alert_email(
            watcher,
            disabled_at=disabled_at,
            reason=auto_disabled_reason,
            consecutive_failure_count=int(policy["consecutive_failure_count"]),
        )
        try:
            self.mail_service.send_email(
                recipient_emails=watcher.recipient_emails,
                subject=subject,
                body=body,
                html_body=html_body,
            )
            final_summary = f"{updated_run.summary} 已发送自动停用告警邮件。".strip()
        except Exception as exc:
            final_summary = f"{updated_run.summary} 自动停用告警邮件发送失败：{exc}".strip()

        self.watcher_store.update_run_summary(updated_run.id, final_summary)
        return updated_run.model_copy(update={"summary": final_summary})

    def _build_request_headers(self, extra_headers: dict[str, str], *, default_accept: str = "*/*") -> dict[str, str]:
        headers = {key: value for key, value in extra_headers.items() if key.strip() != ""}
        if not any(key.lower() == "accept" for key in headers):
            headers["Accept"] = default_accept
        return headers

    def _count_dashboard_items(self, payload: Any) -> int:
        if isinstance(payload, list):
            return len(payload)
        if isinstance(payload, dict):
            for key in ("records", "items", "bugs", "list", "results"):
                value = payload.get(key)
                if isinstance(value, list):
                    return len(value)
            for key in ("data", "page", "result"):
                value = payload.get(key)
                if isinstance(value, (dict, list)):
                    nested_count = self._count_dashboard_items(value)
                    if nested_count > 0:
                        return nested_count
            return len(payload)
        return 1

    def _execute_http_request(
        self,
        *,
        target_url: str,
        request_method: str,
        request_headers: dict[str, str],
        request_body_json: dict[str, Any] | None,
        request_body_text: str | None,
        default_accept: str = "*/*",
    ) -> dict[str, Any]:
        headers = self._build_request_headers(request_headers, default_accept=default_accept)
        request_data: bytes | None = None
        if request_method == "POST":
            if request_body_text is not None and request_body_text.strip() != "":
                request_data = request_body_text.encode("utf-8")
            elif request_body_json is not None:
                request_data = json.dumps(request_body_json, ensure_ascii=False).encode("utf-8")
            header_names = {key.lower() for key in headers}
            if "content-type" not in header_names and request_body_json is not None:
                headers["Content-Type"] = "application/json;charset=UTF-8"

        req = request.Request(target_url, data=request_data, headers=headers, method=request_method)
        status_code = 200
        content_type = ""
        raw = ""
        ok = True
        message = "请求成功。"
        timeout_seconds = 45
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                with request.urlopen(req, timeout=timeout_seconds) as response:
                    status_code = getattr(response, "status", 200)
                    content_type = response.headers.get("Content-Type", "")
                    raw = response.read().decode("utf-8", errors="replace")
                    ok = True
                    message = "请求成功。"
                    break
            except urlerror.HTTPError as exc:
                ok = False
                status_code = exc.code
                content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
                raw = exc.read().decode("utf-8", errors="replace")
                message = f"接口返回 HTTP {exc.code}"
                break
            except Exception as exc:
                ok = False
                message = str(exc)
                # 对超时/握手类网络问题做一次轻量重试，
                # 既能提升偶发网络抖动下的成功率，又不会把请求无限拖长。
                should_retry = attempt < max_attempts and (
                    "timed out" in message.lower()
                    or "handshake" in message.lower()
                    or isinstance(exc, TimeoutError)
                )
                if not should_retry:
                    break
                time.sleep(1.0)

        return {
            "ok": ok,
            "status_code": status_code,
            "message": message,
            "content_type": content_type,
            "raw": raw,
        }

    def _execute_dashboard_request(
        self,
        *,
        dashboard_url: str,
        request_method: str,
        request_headers: dict[str, str],
        request_body_json: dict[str, Any] | None,
        request_body_text: str | None,
    ) -> dict[str, Any]:
        # 这里是巡检抓取阶段最底层的 HTTP 执行器。
        # 无论请求来自“测试抓取”还是“正式运行”，最终都会走到这里。
        request_result = self._execute_http_request(
            target_url=dashboard_url,
            request_method=request_method,
            request_headers=request_headers,
            request_body_json=request_body_json,
            request_body_text=request_body_text,
            default_accept="application/json",
        )
        ok = bool(request_result["ok"])
        status_code = int(request_result["status_code"])
        message = str(request_result["message"])
        content_type = str(request_result["content_type"])
        raw = str(request_result["raw"])

        parsed_payload: Any = None
        parsed_item_count = 0
        if raw.strip() != "":
            try:
                parsed_payload = json.loads(raw)
                parsed_item_count = self._count_dashboard_items(parsed_payload)
            except Exception:
                parsed_payload = None

        response_body_preview = raw[:12000]
        if len(raw) > 12000:
            response_body_preview += "\n...<truncated>"

        return {
            "ok": ok,
            "status_code": status_code,
            "message": message,
            "content_type": content_type,
            "response_body_preview": response_body_preview,
            "parsed_payload": parsed_payload,
            "parsed_item_count": parsed_item_count,
        }

    def _fetch_dashboard_json(self, watcher: WatcherAgentConfig) -> tuple[Any, int]:
        result = self._execute_dashboard_request(
            dashboard_url=watcher.dashboard_url,
            request_method=watcher.request_method,
            request_headers=watcher.request_headers,
            request_body_json=watcher.request_body_json,
            request_body_text=watcher.request_body_text,
        )
        if not result["ok"] and result["response_body_preview"] == "":
            raise RuntimeError(result["message"])
        parsed_payload = result["parsed_payload"]
        if parsed_payload is None:
            raise RuntimeError("接口没有返回可解析的 JSON。")
        return parsed_payload, result["parsed_item_count"]

    def _detail_request_enabled(self, watcher: WatcherAgentConfig) -> bool:
        return watcher.detail_url_template is not None and watcher.detail_url_template.strip() != ""

    def _merge_detail_headers(self, watcher: WatcherAgentConfig) -> dict[str, str]:
        merged = dict(watcher.request_headers)
        merged.update(watcher.detail_request_headers)
        return merged

    def _get_header_case_insensitive(self, headers: dict[str, str], name: str) -> str:
        normalized_name = name.strip().lower()
        for key, value in headers.items():
            if key.strip().lower() == normalized_name:
                return value
        return ""

    def _set_header_case_insensitive(self, headers: dict[str, str], name: str, value: str) -> dict[str, str]:
        updated = {key: item for key, item in headers.items() if key.strip().lower() != name.strip().lower()}
        updated[name] = value
        return updated

    def _execute_http_request_with_request_cookie_fallback(
        self,
        *,
        watcher: WatcherAgentConfig,
        target_url: str,
        request_method: str,
        request_headers: dict[str, str],
        request_body_text: str | None,
        default_accept: str,
    ) -> dict[str, Any]:
        result = self._execute_http_request(
            target_url=target_url,
            request_method=request_method,
            request_headers=request_headers,
            request_body_json=None,
            request_body_text=request_body_text,
            default_accept=default_accept,
        )
        if int(result["status_code"]) not in {401, 403}:
            return result

        current_cookie = self._get_header_case_insensitive(request_headers, "Cookie").strip()
        watcher_request_cookie = self._get_header_case_insensitive(watcher.request_headers, "Cookie").strip()
        if watcher_request_cookie == "" or watcher_request_cookie == current_cookie:
            return result

        retry_headers = self._set_header_case_insensitive(request_headers, "Cookie", watcher_request_cookie)
        return self._execute_http_request(
            target_url=target_url,
            request_method=request_method,
            request_headers=retry_headers,
            request_body_json=None,
            request_body_text=request_body_text,
            default_accept=default_accept,
        )

    def _render_detail_template(self, template: str, bug: ParsedBug) -> str:
        rendered = template
        timestamp_ms = str(int(time.time() * 1000))
        replacements = {
            "{{bug_id}}": bug.bug_id,
            "{{issue_key}}": bug.bug_id,
            "{{issueKey}}": bug.bug_id,
            "{{bug_aid}}": bug.bug_aid,
            "{{aid}}": bug.bug_aid,
            "{{timestamp_ms}}": timestamp_ms,
            "{{now_ms}}": timestamp_ms,
        }
        for needle, value in replacements.items():
            rendered = rendered.replace(needle, value)
        return rendered

    def _detail_preview_text(self, raw: str) -> str:
        normalized = self._strip_html_to_text(raw)
        return normalized[:800]

    def _strip_html_to_text(self, raw: str) -> str:
        normalized = html.unescape(raw or "")
        normalized = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", normalized)
        normalized = re.sub(r"(?i)<br\s*/?>", "\n", normalized)
        normalized = re.sub(r"(?i)</?(div|p|li|tr|td|th|dd|dt|section|article|h[1-6])[^>]*>", "\n", normalized)
        normalized = re.sub(r"(?s)<[^>]+>", " ", normalized)
        lines = [
            re.sub(r"\s+", " ", line).strip(" \t\r\n:：-")
            for line in normalized.splitlines()
        ]
        compact = "\n".join(line for line in lines if line != "").strip()
        return compact

    def _extract_header_cookie_value(self, headers: dict[str, str], name: str) -> str:
        cookie = headers.get("Cookie") or headers.get("cookie") or ""
        target_name = name.strip().lower()
        for segment in cookie.split(";"):
            cookie_name, _, cookie_value = segment.strip().partition("=")
            if cookie_name.strip().lower() == target_name and cookie_value.strip() != "":
                return cookie_value.strip()
        return ""

    def _load_json_if_possible(self, raw: str) -> Any | None:
        try:
            return json.loads(raw)
        except Exception:
            return None

    def _normalize_panel_label(self, value: str) -> str:
        return re.sub(r"\s+", "", value.strip().strip(":："))

    def _collect_jira_panel_htmls(self, payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        panels = payload.get("panels")
        if not isinstance(panels, dict):
            return []

        htmls: list[str] = []
        for key in ("leftPanels", "rightPanels", "infoPanels"):
            items = panels.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                panel_html = item.get("html")
                if isinstance(panel_html, str) and panel_html.strip() != "":
                    htmls.append(panel_html)
        return htmls

    def _extract_jira_panel_fields(self, payload: Any) -> dict[str, str]:
        fields: dict[str, str] = {}
        for panel_html in self._collect_jira_panel_htmls(payload):
            for matched in re.finditer(r"(?is)<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>", panel_html):
                label = self._normalize_panel_label(self._strip_html_to_text(matched.group(1)))
                value = self._strip_html_to_text(matched.group(2)).strip()
                if label != "" and value != "" and label not in fields:
                    fields[label] = value
            for matched in re.finditer(r'(?is)<li\b[^>]*class=["\'][^"\']*\bitem\b[^"\']*["\'][^>]*>(.*?)</li>', panel_html):
                item_html = matched.group(1)
                label_match = re.search(
                    r'(?is)<strong\b[^>]*class=["\'][^"\']*\bname\b[^"\']*["\'][^>]*>(.*?)</strong>',
                    item_html,
                )
                value_match = re.search(
                    r'(?is)<(?:div|span)\b[^>]*class=["\'][^"\']*\bvalue\b[^"\']*["\'][^>]*>(.*)',
                    item_html,
                )
                if label_match is None or value_match is None:
                    continue
                label = self._normalize_panel_label(self._strip_html_to_text(label_match.group(1)))
                value = self._strip_html_to_text(value_match.group(1)).strip()
                if label != "" and value != "" and label not in fields:
                    fields[label] = value
        return fields

    def _get_jira_panel_field(self, fields: dict[str, str], *labels: str) -> str:
        for label in labels:
            normalized = self._normalize_panel_label(label)
            if normalized in fields and fields[normalized].strip() != "":
                return fields[normalized].strip()
        return ""

    def _split_jira_service_module(self, value: str) -> tuple[str, str]:
        line_parts = [
            re.sub(r"\s+", " ", part).strip(" -")
            for part in re.split(r"[\r\n]+", value)
            if re.sub(r"\s+", " ", part).strip(" -") != ""
        ]
        if len(line_parts) >= 2:
            return line_parts[0], line_parts[1]
        normalized = re.sub(r"\s+", " ", value).strip().strip("-")
        if normalized == "":
            return "", ""
        for separator in (" - ", " / ", " | ", "-", "/", "|", ">", " > "):
            if separator not in normalized:
                continue
            left, right = normalized.split(separator, 1)
            if left.strip() != "" and right.strip() != "":
                return left.strip(), right.strip()
        return normalized, ""

    def _extract_jira_service_module_fields(self, fields: dict[str, str]) -> tuple[str, str, str]:
        category = self._get_jira_panel_field(fields, "领域模块")
        service, module = self._split_jira_service_module(category)
        initial_service = self._get_jira_panel_field(fields, "初始领域")
        initial_module = self._get_jira_panel_field(fields, "初始模块")

        if service == "":
            service = initial_service
        if module == "":
            module = initial_module
        if (category == "" or "\n" in category or "\r" in category) and (service != "" or module != ""):
            category = " - ".join(part for part in (service, module) if part != "")
        return service, module, category

    def _extract_jira_issue_id_from_payload(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        issue = payload.get("issue")
        if isinstance(issue, dict):
            issue_id = issue.get("id")
            if isinstance(issue_id, (str, int)) and str(issue_id).strip() != "":
                return str(issue_id).strip()
        return ""

    def _extract_jira_assign_issue_id_from_payload(self, node: Any) -> str:
        if isinstance(node, dict):
            node_id = node.get("id")
            href = node.get("href")
            if node_id == "assign-issue" and isinstance(href, str):
                matched = re.search(r"[?&]id=(\d+)", href)
                if matched is not None:
                    return matched.group(1).strip()
            for value in node.values():
                found = self._extract_jira_assign_issue_id_from_payload(value)
                if found != "":
                    return found
        elif isinstance(node, list):
            for item in node:
                found = self._extract_jira_assign_issue_id_from_payload(item)
                if found != "":
                    return found
        return ""

    def _extract_jira_assignment_meta(
        self,
        raw: str,
        headers: dict[str, str],
        bug: ParsedBug,
        *,
        payload: Any | None = None,
        panel_fields: dict[str, str] | None = None,
    ) -> dict[str, str]:
        def find_first(patterns: list[str]) -> str:
            for pattern in patterns:
                matched = re.search(pattern, raw, flags=re.IGNORECASE)
                if matched is not None:
                    return matched.group(1).strip()
            return ""

        parsed_payload = payload if payload is not None else self._load_json_if_possible(raw)
        extracted_fields = panel_fields or (
            self._extract_jira_panel_fields(parsed_payload) if parsed_payload is not None else {}
        )
        issue_id = (
            self._extract_jira_issue_id_from_payload(parsed_payload)
            or self._get_jira_panel_field(extracted_fields, "问题ID")
            or self._extract_jira_assign_issue_id_from_payload(parsed_payload)
            or find_first(
                [
                    r'name=["\']id["\'][^>]*value=["\'](\d+)["\']',
                    r'issueId["\']?\s*[:=]\s*["\']?(\d+)["\']?',
                    r'\bentityId["\']?\s*[:=]\s*["\']?(\d+)["\']?',
                    r'\bissue_id["\']?\s*[:=]\s*["\']?(\d+)["\']?',
                ]
            )
            or bug.jira_issue_id
            or (bug.bug_aid if bug.bug_aid.isdigit() else "")
        )
        form_token = find_first(
            [
                r'name=["\']formToken["\'][^>]*value=["\']([^"\']+)["\']',
                r'formToken["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            ]
        ) or bug.jira_form_token
        payload_atl_token = ""
        if isinstance(parsed_payload, dict):
            candidate_atl_token = parsed_payload.get("atl_token")
            if isinstance(candidate_atl_token, (str, int)) and str(candidate_atl_token).strip() != "":
                payload_atl_token = str(candidate_atl_token).strip()
        atl_token = (
            payload_atl_token
            or find_first(
                [
                    r'name=["\']atl_token["\'][^>]*value=["\']([^"\']+)["\']',
                    r'ajs-atl-token["\'][^>]*content=["\']([^"\']+)["\']',
                    r'atl_token["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                ]
            )
            or self._extract_header_cookie_value(headers, "atlassian.xsrf.token")
            or bug.jira_atl_token
        )
        return {
            "jira_issue_id": issue_id,
            "jira_form_token": form_token,
            "jira_atl_token": atl_token,
        }

    def _extract_customer_issue_type_from_json(self, node: Any) -> str:
        if isinstance(node, dict):
            for key, value in node.items():
                normalized_key = str(key).strip().lower()
                if normalized_key in {
                    "customerissuetype",
                    "customer_issue_type",
                    "customerproblemtype",
                    "customer_problem_type",
                    "客户问题类型",
                }:
                    if isinstance(value, (str, int, float)) and str(value).strip() != "":
                        return str(value).strip()
                if isinstance(value, dict):
                    title = value.get("title")
                    if title == "客户问题类型":
                        for nested_key in ("value", "name", "label", "text"):
                            nested_value = value.get(nested_key)
                            if isinstance(nested_value, (str, int, float)) and str(nested_value).strip() != "":
                                return str(nested_value).strip()
                found = self._extract_customer_issue_type_from_json(value)
                if found != "":
                    return found
        elif isinstance(node, list):
            for item in node:
                found = self._extract_customer_issue_type_from_json(item)
                if found != "":
                    return found
        return ""

    def _extract_customer_issue_type(
        self,
        raw: str,
        *,
        payload: Any | None = None,
        panel_fields: dict[str, str] | None = None,
    ) -> str:
        extracted_fields = panel_fields or {}
        labeled_value = self._get_jira_panel_field(extracted_fields, "客户问题类型")
        if labeled_value != "":
            return labeled_value

        parsed = payload if payload is not None else self._load_json_if_possible(raw)
        if parsed is not None:
            found = self._extract_customer_issue_type_from_json(parsed)
            if found != "":
                return found

        lines = [line for line in self._detail_preview_text(raw).splitlines() if line.strip() != ""]
        for index, line in enumerate(lines):
            if "客户问题类型" not in line:
                continue
            remainder = line.split("客户问题类型", 1)[1].strip(" ：:-")
            if remainder != "":
                return remainder
            if index + 1 < len(lines):
                return lines[index + 1].strip()
        return ""

    def _append_detail_excerpt(self, raw_excerpt: str, detail_excerpt: str) -> str:
        if detail_excerpt.strip() == "":
            return raw_excerpt
        parts = [raw_excerpt.strip()] if raw_excerpt.strip() != "" else []
        parts.append("[详情接口]\n" + detail_excerpt.strip())
        return "\n\n".join(parts)[:2400]

    def _hydrate_bug_details(self, watcher: WatcherAgentConfig, bugs: list[ParsedBug]) -> list[ParsedBug]:
        if not self._detail_request_enabled(watcher) or len(bugs) == 0:
            return bugs

        hydrated: list[ParsedBug] = []
        for bug in bugs:
            rendered_url = self._render_detail_template(watcher.detail_url_template or "", bug).strip()
            if rendered_url == "":
                hydrated.append(bug)
                continue
            detail_headers = self._merge_detail_headers(watcher)
            rendered_headers = {
                key: self._render_detail_template(str(value), bug)
                for key, value in detail_headers.items()
                if key.strip() != ""
            }
            rendered_body = (
                self._render_detail_template(watcher.detail_request_body_text, bug)
                if watcher.detail_request_body_text is not None
                else None
            )
            try:
                detail_result = self._execute_http_request_with_request_cookie_fallback(
                    watcher=watcher,
                    target_url=rendered_url,
                    request_method=watcher.detail_request_method,
                    request_headers=rendered_headers,
                    request_body_text=rendered_body,
                    default_accept="*/*",
                )
            except Exception:
                hydrated.append(bug)
                continue

            detail_raw = str(detail_result["raw"])
            detail_payload = self._load_json_if_possible(detail_raw)
            panel_fields = self._extract_jira_panel_fields(detail_payload) if detail_payload is not None else {}
            jira_assignment_meta = self._extract_jira_assignment_meta(
                detail_raw,
                rendered_headers,
                bug,
                payload=detail_payload,
                panel_fields=panel_fields,
            )
            service, module, category = self._extract_jira_service_module_fields(panel_fields)
            hydrated.append(
                bug.model_copy(
                    update={
                        "customer_issue_type": (
                            self._extract_customer_issue_type(
                                detail_raw,
                                payload=detail_payload,
                                panel_fields=panel_fields,
                            )
                            or bug.customer_issue_type
                        ),
                        "jira_issue_id": jira_assignment_meta["jira_issue_id"] or bug.jira_issue_id,
                        "jira_form_token": jira_assignment_meta["jira_form_token"] or bug.jira_form_token,
                        "jira_atl_token": jira_assignment_meta["jira_atl_token"] or bug.jira_atl_token,
                        "service": service or bug.service,
                        "module": module or bug.module,
                        "category": category or bug.category,
                        "raw_excerpt": self._append_detail_excerpt(bug.raw_excerpt, self._detail_preview_text(detail_raw)),
                    }
                )
            )
        return hydrated

    def _is_jira_watcher(self, watcher: WatcherAgentConfig) -> bool:
        parsed_url = parse.urlsplit(watcher.dashboard_url)
        host = parsed_url.netloc.lower()
        path = parsed_url.path.lower()
        detail_url = (watcher.detail_url_template or "").lower()
        return "jira" in host or "/rest/issuenav/" in path or "ajaxissueaction" in detail_url

    def _extract_line_id(self, watcher: WatcherAgentConfig) -> str:
        body = watcher.request_body_json or {}
        direct = body.get("lineId") if isinstance(body, dict) else None
        if isinstance(direct, (str, int)) and str(direct).strip() != "":
            return str(direct).strip()

        def walk(node: Any) -> str:
            if isinstance(node, dict):
                if node.get("fieldCode") == "lineId":
                    candidates = [node.get("value"), node.get("feValue")]
                    values = node.get("values")
                    if isinstance(values, list):
                        candidates.extend(values)
                    for candidate in candidates:
                        if isinstance(candidate, (str, int)) and str(candidate).strip() != "":
                            return str(candidate).strip()
                for value in node.values():
                    found = walk(value)
                    if found != "":
                        return found
            elif isinstance(node, list):
                for item in node:
                    found = walk(item)
                    if found != "":
                        return found
            return ""

        return walk(body)

    def _extract_tenant_info(self, watcher: WatcherAgentConfig) -> str:
        parsed_url = parse.urlsplit(watcher.dashboard_url)
        query = parse.parse_qs(parsed_url.query, keep_blank_values=True)
        tenant_info = (query.get("tenant_info") or [""])[0].strip()
        if tenant_info != "":
            return tenant_info

        cookie = watcher.request_headers.get("Cookie") or watcher.request_headers.get("cookie") or ""
        for segment in cookie.split(";"):
            name, _, value = segment.strip().partition("=")
            if name == "tenant_info" and value.strip() != "":
                return value.strip()
        return ""

    def _build_assignment_url(self, watcher: WatcherAgentConfig) -> str:
        line_id = self._extract_line_id(watcher)
        if line_id == "":
            raise RuntimeError("当前请求配置里缺少 lineId，无法调用 PM 分配接口。")

        parsed_url = parse.urlsplit(watcher.dashboard_url)
        path = parsed_url.path
        if path.endswith("/page"):
            path = path[: -len("/page")] + "/update"
        else:
            path = path.rstrip("/") + "/update"

        query_params: dict[str, str] = {"lineId": line_id}
        tenant_info = self._extract_tenant_info(watcher)
        if tenant_info != "":
            query_params["tenant_info"] = tenant_info

        return parse.urlunsplit((parsed_url.scheme, parsed_url.netloc, path, parse.urlencode(query_params), ""))

    def _call_assignment_api(
        self,
        watcher: WatcherAgentConfig,
        results: list[WatcherAssignmentResult],
    ) -> list[WatcherAssignmentResult]:
        if len(results) == 0:
            return []

        if self._is_jira_watcher(watcher):
            return self._call_jira_assignment_api(watcher, results)

        api_url = self._build_assignment_url(watcher)
        updated: list[WatcherAssignmentResult] = []

        for item in results:
            if item.assignee_code is None or item.assignee_code.strip() == "":
                updated.append(
                    item.model_copy(
                        update={"assignment_status": "unmatched", "assignment_message": "未匹配转派目标，未调用分配接口。"}
                    )
                )
                continue

            if item.bug_aid.strip() == "":
                updated.append(
                    item.model_copy(
                        update={"assignment_status": "failed", "assignment_message": "缺少 PM aid，无法调用分配接口。"}
                    )
                )
                continue

            headers = dict(watcher.request_headers)
            headers["Accept"] = headers.get("Accept", "application/json, text/plain, */*")
            headers["Content-Type"] = "application/json;charset=utf-8"
            payload = {"aids": [item.bug_aid], "assignee": item.assignee_code.strip()}

            try:
                req = request.Request(
                    api_url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers=headers,
                    method="PUT",
                )
                with request.urlopen(req, timeout=20) as response:
                    raw = response.read().decode("utf-8")
                response_text = raw[:200] if raw else "ok"
                try:
                    parsed_body = json.loads(raw) if raw else {}
                except Exception:
                    parsed_body = {}
                if isinstance(parsed_body, dict) and parsed_body.get("code") not in (None, 200, "200"):
                    updated.append(
                        item.model_copy(
                            update={
                                "assignment_status": "failed",
                                "assignment_message": (
                                    f"PM 返回失败：code={parsed_body.get('code')} "
                                    f"msg={parsed_body.get('msg') or response_text}"
                                ).strip(),
                            }
                        )
                    )
                    continue
                if isinstance(parsed_body, dict) and isinstance(parsed_body.get("msg"), str) and parsed_body["msg"].strip() != "":
                    response_text = parsed_body["msg"].strip()
                updated.append(
                    item.model_copy(
                        update={"assignment_status": "success", "assignment_message": f"PM 分配成功：{response_text}"}
                    )
                )
            except urlerror.HTTPError as exc:
                try:
                    detail = exc.read().decode("utf-8")
                except Exception:
                    detail = str(exc)
                updated.append(
                    item.model_copy(
                        update={"assignment_status": "failed", "assignment_message": f"{exc.code} {detail}".strip()}
                    )
                )
            except Exception as exc:
                updated.append(
                    item.model_copy(update={"assignment_status": "failed", "assignment_message": str(exc)})
                )

        return updated

    def _call_jira_assignment_api(
        self,
        watcher: WatcherAgentConfig,
        results: list[WatcherAssignmentResult],
    ) -> list[WatcherAssignmentResult]:
        parsed_url = parse.urlsplit(watcher.dashboard_url)
        api_url = parse.urlunsplit((parsed_url.scheme, parsed_url.netloc, "/secure/AssignIssue.jspa", "", ""))
        base_headers = self._merge_detail_headers(watcher)
        updated: list[WatcherAssignmentResult] = []

        for item in results:
            target = (item.assignee_code or "").strip()
            if target == "":
                updated.append(
                    item.model_copy(
                        update={"assignment_status": "unmatched", "assignment_message": "未匹配转派目标，未调用 Jira 转派接口。"}
                    )
                )
                continue

            issue_id = item.jira_issue_id.strip() or (item.bug_id.strip() if item.bug_id.strip().isdigit() else "")
            form_token = item.jira_form_token.strip()
            atl_token = item.jira_atl_token.strip()
            rendered_headers = {
                key: self._render_detail_template(str(value), ParsedBug(bug_id=item.bug_id, bug_aid=item.bug_aid))
                for key, value in base_headers.items()
                if key.strip() != ""
            }
            if issue_id != "" and (form_token == "" or atl_token == ""):
                assign_form_meta = self._fetch_jira_assign_form_meta(
                    watcher,
                    issue_id=issue_id,
                    request_headers=rendered_headers,
                    issue_key=item.bug_id,
                )
                issue_id = issue_id or assign_form_meta["jira_issue_id"]
                form_token = form_token or assign_form_meta["jira_form_token"]
                atl_token = atl_token or assign_form_meta["jira_atl_token"]
            if issue_id == "" or form_token == "":
                updated.append(
                    item.model_copy(
                        update={
                            "assignment_status": "failed",
                            "assignment_message": "缺少 Jira issue id 或 formToken，无法提交转派。",
                        }
                    )
                )
                continue
            if atl_token == "":
                atl_token = self._extract_header_cookie_value(rendered_headers, "atlassian.xsrf.token")
            if atl_token == "":
                updated.append(
                    item.model_copy(
                        update={
                            "assignment_status": "failed",
                            "assignment_message": "缺少 Jira atl_token，请确认 Cookie 中包含 atlassian.xsrf.token。",
                        }
                    )
                )
                continue

            rendered_headers["Accept"] = rendered_headers.get("Accept", "text/html, */*; q=0.01")
            rendered_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            rendered_headers["Origin"] = f"{parsed_url.scheme}://{parsed_url.netloc}"
            rendered_headers["Referer"] = rendered_headers.get("Referer", f"{parsed_url.scheme}://{parsed_url.netloc}/browse/{item.bug_id}")
            rendered_headers["X-Requested-With"] = rendered_headers.get("X-Requested-With", "XMLHttpRequest")
            payload = parse.urlencode(
                {
                    "inline": "true",
                    "decorator": "dialog",
                    "id": issue_id,
                    "formToken": form_token,
                    "assignee": target,
                    "dnd-dropzone": "",
                    "comment": "",
                    "commentLevel": "",
                    "atl_token": atl_token,
                }
            )

            try:
                result = self._execute_http_request_with_request_cookie_fallback(
                    watcher=watcher,
                    target_url=api_url,
                    request_method="POST",
                    request_headers=rendered_headers,
                    request_body_text=payload,
                    default_accept="text/html, */*; q=0.01",
                )
                if not bool(result["ok"]):
                    updated.append(
                        item.model_copy(
                            update={
                                "assignment_status": "failed",
                                "assignment_message": f"Jira 转派失败：{result['message']}",
                            }
                        )
                    )
                    continue
                response_text = str(result["raw"]).strip()
                response_preview = response_text[:200] if response_text != "" else "ok"
                updated.append(
                    item.model_copy(
                        update={
                            "assignment_status": "success",
                            "assignment_message": f"Jira 转派成功：{response_preview}",
                        }
                    )
                )
            except Exception as exc:
                updated.append(
                    item.model_copy(update={"assignment_status": "failed", "assignment_message": f"Jira 转派失败：{exc}"})
                )

        return updated

    def _fetch_jira_assign_form_meta(
        self,
        watcher: WatcherAgentConfig,
        *,
        issue_id: str,
        request_headers: dict[str, str],
        issue_key: str,
    ) -> dict[str, str]:
        parsed_url = parse.urlsplit(watcher.dashboard_url)
        form_url = parse.urlunsplit(
            (
                parsed_url.scheme,
                parsed_url.netloc,
                "/secure/AssignIssue!default.jspa",
                parse.urlencode({"id": issue_id}),
                "",
            )
        )
        headers = dict(request_headers)
        headers["Accept"] = headers.get("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        headers["Referer"] = headers.get("Referer", f"{parsed_url.scheme}://{parsed_url.netloc}/browse/{issue_key}")
        result = self._execute_http_request_with_request_cookie_fallback(
            watcher=watcher,
            target_url=form_url,
            request_method="GET",
            request_headers=headers,
            request_body_text=None,
            default_accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        )
        if not bool(result["ok"]):
            return {"jira_issue_id": issue_id, "jira_form_token": "", "jira_atl_token": ""}

        bug = ParsedBug(bug_id=issue_key or issue_id, jira_issue_id=issue_id)
        return self._extract_jira_assignment_meta(str(result["raw"]), headers, bug)

    def _send_email(self, watcher: WatcherAgentConfig, subject: str, body: str, html_body: str | None = None) -> None:
        self.mail_service.send_email(
            recipient_emails=watcher.recipient_emails,
            subject=subject,
            body=body,
            html_body=html_body,
        )

    def _persist_run(self, state: WatcherGraphState) -> WatcherRun:
        watcher = state["watcher"]
        ended_at = _utc_now()
        parsed_bugs = state.get("parsed_bugs", [])
        assignment_results = state.get("assignment_results", [])
        new_bug_count = len(state.get("new_bugs", []))

        if state.get("run_status") in {"baseline_seeded", "no_change", "success", "partial_success"}:
            self.watcher_store.upsert_seen_bugs(watcher.id, parsed_bugs, ended_at)

        run = WatcherRun(
            id=str(uuid4()),
            agent_id=watcher.id,
            status=state.get("run_status", "failed"),
            started_at=state.get("run_started_at", ended_at),
            ended_at=ended_at,
            fetched_count=int(state.get("fetched_count", 0)),
            parsed_count=len(parsed_bugs),
            new_bug_count=0 if state.get("run_status") == "baseline_seeded" else new_bug_count,
            assigned_count=sum(1 for item in assignment_results if item.assignment_status == "success"),
            emailed=bool(state.get("emailed", False)),
            summary=state.get("summary", "") or "巡检完成。",
            error_message=state.get("error_message"),
            assignment_results=assignment_results,
        )
        self.watcher_store.record_run(watcher.id, run)
        return run
