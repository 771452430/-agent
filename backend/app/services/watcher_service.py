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
        self.watcher_store = watcher_store
        self.llm_service = llm_service
        self.settings = settings
        self.mail_service = mail_service
        self.graph = WatcherAgentGraph(
            llm_service=llm_service,
            fetch_dashboard_json=self._fetch_dashboard_json,
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

    def list_watchers(self) -> list[WatcherAgentConfig]:
        return self.watcher_store.list_watchers()

    def get_watcher(self, watcher_id: str) -> WatcherAgentConfig:
        watcher = self.watcher_store.get_watcher(watcher_id)
        if watcher is None:
            raise HTTPException(status_code=404, detail="Watcher not found")
        return watcher

    def create_watcher(self, request: CreateWatcherRequest) -> WatcherAgentConfig:
        model_config = self._require_runnable_model_config(request.model_settings)
        return self.watcher_store.create_watcher(request, model_config)

    def update_watcher(self, watcher_id: str, request: UpdateWatcherRequest) -> WatcherAgentConfig:
        current = self.watcher_store.get_watcher(watcher_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Watcher not found")

        updated = self.watcher_store.update_watcher(
            watcher_id,
            request,
            model_config=self._require_runnable_model_config(request.model_settings) if request.model_settings else None,
        )
        assert updated is not None
        return updated

    def list_runs(self, watcher_id: str) -> list[WatcherRun]:
        self.get_watcher(watcher_id)
        return self.watcher_store.list_runs(watcher_id)

    def list_due_watchers(self) -> list[WatcherAgentConfig]:
        return self.watcher_store.list_due_watchers()

    def test_fetch(self, request: WatcherFetchTestRequest) -> WatcherFetchTestResponse:
        result = self._execute_dashboard_request(
            dashboard_url=request.dashboard_url,
            request_method=request.request_method,
            request_headers=request.request_headers,
            request_body_json=request.request_body_json,
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

    def _build_request_headers(self, extra_headers: dict[str, str]) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        headers.update({key: value for key, value in extra_headers.items() if key.strip() != ""})
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

    def _execute_dashboard_request(
        self,
        *,
        dashboard_url: str,
        request_method: str,
        request_headers: dict[str, str],
        request_body_json: dict[str, Any] | None,
    ) -> dict[str, Any]:
        headers = self._build_request_headers(request_headers)
        request_data: bytes | None = None
        if request_method == "POST":
            if request_body_json is not None:
                request_data = json.dumps(request_body_json, ensure_ascii=False).encode("utf-8")
            header_names = {key.lower() for key in headers}
            if "content-type" not in header_names:
                headers["Content-Type"] = "application/json;charset=UTF-8"

        req = request.Request(dashboard_url, data=request_data, headers=headers, method=request_method)
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
                should_retry = attempt < max_attempts and (
                    "timed out" in message.lower()
                    or "handshake" in message.lower()
                    or isinstance(exc, TimeoutError)
                )
                if not should_retry:
                    break
                time.sleep(1.0)

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
        )
        if not result["ok"] and result["response_body_preview"] == "":
            raise RuntimeError(result["message"])
        parsed_payload = result["parsed_payload"]
        if parsed_payload is None:
            raise RuntimeError("接口没有返回可解析的 JSON。")
        return parsed_payload, result["parsed_item_count"]

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

        api_url = self._build_assignment_url(watcher)
        updated: list[WatcherAssignmentResult] = []

        for item in results:
            if item.assignee_code is None or item.assignee_code.strip() == "":
                updated.append(
                    item.model_copy(
                        update={"assignment_status": "unmatched", "assignment_message": "未匹配经办人编码，未调用分配接口。"}
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
