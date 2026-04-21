"""巡检 Agent 的 LangGraph 编排。

这个图和聊天图最大的区别在于：
- 聊天图围绕“当前一次请求”组织；
- 巡检图围绕“定时自动化 + 增量状态”组织。

节点职责严格贴合产品链路：
1. 抓取面板 JSON；
2. 结构化抽取 bug 列表；
3. 对比已见 bug_id，只保留新增；
4. 规则优先匹配经办人，LLM 只做兜底；
5. 调分配接口；
6. 生成并发送邮件；
7. 持久化运行记录与已见 bug。

这里额外保留了一个“手动立即运行”的教学型分支：
- 定时轮巡：只在发现新增 bug_id 时才发邮件；
- 手动立即运行：即使没有新增，也会把当前抓到的 bug 列表作为快照邮件发出去，
  这样你点击按钮后能立刻看到整条链路是否跑通。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from ..schemas import (
    OwnerRule,
    ParsedBug,
    WatcherAgentConfig,
    WatcherAssignmentResult,
    WatcherOwnerSuggestion,
    WatcherRun,
    WatcherRunStatus,
)
from ..services.llm_service import LLMService


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WatcherGraphState(TypedDict, total=False):
    watcher: WatcherAgentConfig
    run_started_at: datetime
    force_email_snapshot: bool
    force_assign_snapshot: bool
    raw_dashboard: Any
    fetched_count: int
    parsed_bugs: list[ParsedBug]
    new_bugs: list[ParsedBug]
    assignment_bugs: list[ParsedBug]
    unmatched_bugs: list[ParsedBug]
    assignment_results: list[WatcherAssignmentResult]
    email_subject: str
    email_body: str
    email_html: str
    emailed: bool
    run_status: WatcherRunStatus
    summary: str
    error_message: str | None
    persisted_run: WatcherRun


class WatcherAgentGraph:
    """用 LangGraph 把巡检链路拆成可观察的节点。"""

    def __init__(
        self,
        *,
        llm_service: LLMService,
        fetch_dashboard_json: Callable[[WatcherAgentConfig], tuple[Any, int]],
        hydrate_bug_details: Callable[[WatcherAgentConfig, list[ParsedBug]], list[ParsedBug]],
        get_seen_bug_ids: Callable[[str], set[str]],
        call_assignment_api: Callable[[WatcherAgentConfig, list[WatcherAssignmentResult]], list[WatcherAssignmentResult]],
        send_email: Callable[[WatcherAgentConfig, str, str, str | None], None],
        persist_run: Callable[[WatcherGraphState], WatcherRun],
    ) -> None:
        self.llm_service = llm_service
        self.fetch_dashboard_json_callback = fetch_dashboard_json
        self.hydrate_bug_details_callback = hydrate_bug_details
        self.get_seen_bug_ids_callback = get_seen_bug_ids
        self.call_assignment_api_callback = call_assignment_api
        self.send_email_callback = send_email
        self.persist_run_callback = persist_run
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(WatcherGraphState)
        builder.add_node("fetch_dashboard_json", self.fetch_dashboard_json)
        builder.add_node("extract_bug_list", self.extract_bug_list)
        builder.add_node("hydrate_bug_details", self.hydrate_bug_details)
        builder.add_node("detect_new_bugs", self.detect_new_bugs)
        builder.add_node("match_owner_rules", self.match_owner_rules)
        builder.add_node("llm_assign_fallback", self.llm_assign_fallback)
        builder.add_node("call_assignment_api", self.call_assignment_api)
        builder.add_node("compose_email", self.compose_email)
        builder.add_node("send_email", self.send_email)
        builder.add_node("persist_run", self.persist_run)

        builder.add_edge(START, "fetch_dashboard_json")
        builder.add_conditional_edges(
            "fetch_dashboard_json",
            self._route_after_fetch,
            {"extract": "extract_bug_list", "persist": "persist_run"},
        )
        builder.add_conditional_edges(
            "extract_bug_list",
            self._route_after_extract,
            {"hydrate": "hydrate_bug_details", "persist": "persist_run"},
        )
        builder.add_edge("hydrate_bug_details", "detect_new_bugs")
        builder.add_conditional_edges(
            "detect_new_bugs",
            self._route_after_detect,
            {"match": "match_owner_rules", "compose": "compose_email", "persist": "persist_run"},
        )
        builder.add_conditional_edges(
            "match_owner_rules",
            self._route_after_match,
            {"llm": "llm_assign_fallback", "assign": "call_assignment_api"},
        )
        builder.add_edge("llm_assign_fallback", "call_assignment_api")
        builder.add_edge("call_assignment_api", "compose_email")
        builder.add_edge("compose_email", "send_email")
        builder.add_edge("send_email", "persist_run")
        builder.add_edge("persist_run", END)
        return builder.compile()

    def _route_after_fetch(self, state: WatcherGraphState) -> Literal["extract", "persist"]:
        return "persist" if state.get("error_message") else "extract"

    def _route_after_extract(self, state: WatcherGraphState) -> Literal["hydrate", "persist"]:
        return "persist" if state.get("error_message") else "hydrate"

    def _route_after_detect(self, state: WatcherGraphState) -> Literal["match", "compose", "persist"]:
        run_status = state.get("run_status")
        if run_status == "failed":
            return "persist"
        if len(state.get("assignment_bugs", [])) > 0:
            return "match"
        if run_status in {"baseline_seeded", "no_change"}:
            should_send_snapshot = bool(state.get("force_email_snapshot")) and len(state.get("parsed_bugs", [])) > 0
            return "compose" if should_send_snapshot else "persist"
        return "match"

    def _route_after_match(self, state: WatcherGraphState) -> Literal["llm", "assign"]:
        watcher = state["watcher"]
        if watcher.match_mode == "fixed_match":
            return "assign"
        return "llm" if len(state.get("unmatched_bugs", [])) > 0 else "assign"

    def fetch_dashboard_json(self, state: WatcherGraphState) -> dict[str, Any]:
        watcher = state["watcher"]
        try:
            payload, fetched_count = self.fetch_dashboard_json_callback(watcher)
            return {"raw_dashboard": payload, "fetched_count": fetched_count, "run_status": "success"}
        except Exception as exc:
            return {
                "raw_dashboard": None,
                "fetched_count": 0,
                "run_status": "failed",
                "error_message": f"抓取面板失败：{exc}",
                "summary": "面板抓取失败，未进入后续解析链路。",
            }

    def extract_bug_list(self, state: WatcherGraphState) -> dict[str, Any]:
        watcher = state["watcher"]
        if watcher.match_mode == "fixed_match":
            bugs = self.llm_service.preview_bug_list_from_payload(state.get("raw_dashboard"))
        else:
            bugs = self.llm_service.extract_bug_list(
                dashboard_payload=state.get("raw_dashboard"),
                model_config=watcher.model_settings,
            )
        if len(bugs) == 0:
            return {
                "parsed_bugs": [],
                "run_status": "failed",
                "error_message": "未能从原始 JSON 中提取出稳定的 bug_id 列表。",
                "summary": "结构化提取失败：没有识别到稳定 bug_id。",
            }
        return {"parsed_bugs": bugs}

    def hydrate_bug_details(self, state: WatcherGraphState) -> dict[str, Any]:
        watcher = state["watcher"]
        parsed_bugs = state.get("parsed_bugs", [])
        try:
            return {"parsed_bugs": self.hydrate_bug_details_callback(watcher, parsed_bugs)}
        except Exception:
            return {"parsed_bugs": parsed_bugs}

    def detect_new_bugs(self, state: WatcherGraphState) -> dict[str, Any]:
        watcher = state["watcher"]
        parsed_bugs = state.get("parsed_bugs", [])
        if watcher.match_mode == "fixed_match":
            return {
                "new_bugs": [],
                "assignment_bugs": parsed_bugs,
                "force_assign_snapshot": True,
                "run_status": "success",
                "summary": f"固定匹配模式：本轮全量抓取 {len(parsed_bugs)} 个 Bug，准备按规则遍历转派。",
            }
        seen_bug_ids = self.get_seen_bug_ids_callback(watcher.id)
        force_email_snapshot = bool(state.get("force_email_snapshot"))
        force_assign_snapshot = bool(state.get("force_assign_snapshot"))

        def build_assign_summary(new_bug_count: int) -> str:
            if new_bug_count > 0:
                return (
                    f"发现 {new_bug_count} 个新增 Bug；"
                    f"本次按当前列表 {len(parsed_bugs)} 条执行分配与通知。"
                )
            return (
                f"本轮抓取 {len(parsed_bugs)} 个 Bug，没有发现新的 bug_id；"
                f"本次按当前列表 {len(parsed_bugs)} 条执行分配与通知。"
            )

        if len(seen_bug_ids) == 0:
            if force_assign_snapshot and len(parsed_bugs) > 0:
                return {
                    "new_bugs": [],
                    "assignment_bugs": parsed_bugs,
                    "run_status": "success",
                    "summary": (
                        f"首次运行已建立基线，共记录 {len(parsed_bugs)} 个已存在 Bug。"
                        f" 本次按当前列表 {len(parsed_bugs)} 条执行分配与通知。"
                    ),
                }
            return {
                "new_bugs": [],
                "assignment_bugs": [],
                "run_status": "baseline_seeded",
                "summary": (
                    f"首次运行已建立基线，共记录 {len(parsed_bugs)} 个已存在 Bug。"
                    + (" 本次是手动立即运行，将额外发送当前列表快照邮件。" if force_email_snapshot else "")
                ),
            }

        new_bugs = [bug for bug in parsed_bugs if bug.bug_id not in seen_bug_ids]
        if force_assign_snapshot and len(parsed_bugs) > 0:
            return {
                "new_bugs": new_bugs,
                "assignment_bugs": parsed_bugs,
                "run_status": "success",
                "summary": build_assign_summary(len(new_bugs)),
            }
        if len(new_bugs) == 0:
            return {
                "new_bugs": [],
                "assignment_bugs": [],
                "run_status": "no_change",
                "summary": (
                    f"本轮抓取 {len(parsed_bugs)} 个 Bug，没有发现新的 bug_id。"
                    + (" 本次是手动立即运行，将额外发送当前列表快照邮件。" if force_email_snapshot else "")
                ),
            }
        return {
            "new_bugs": new_bugs,
            "assignment_bugs": new_bugs,
            "run_status": "success",
            "summary": f"发现 {len(new_bugs)} 个新增 Bug，准备进入分配与通知链路。",
        }

    def _match_terms(self, haystack: str, terms: list[str]) -> list[str]:
        lowered = haystack.lower()
        matched: list[str] = []
        for term in terms:
            normalized = term.strip().lower()
            if normalized != "" and normalized in lowered:
                matched.append(term)
        return matched

    def _match_rule(self, bug: ParsedBug, rule: OwnerRule, *, keyword_haystack: str) -> tuple[int, str]:
        if rule.assignee_code.strip() == "":
            return 0, ""
        service_hits = self._match_terms(bug.service, rule.services)
        module_hits = self._match_terms(bug.module, rule.modules)
        keyword_hits = self._match_terms(keyword_haystack, rule.keywords)
        customer_issue_type_hits = self._match_terms(bug.customer_issue_type, rule.customer_issue_types)

        score = len(service_hits) * 3 + len(module_hits) * 3 + len(keyword_hits) + len(customer_issue_type_hits) * 3
        reasons: list[str] = []
        if service_hits:
            reasons.append("service 命中：" + "、".join(service_hits))
        if module_hits:
            reasons.append("module 命中：" + "、".join(module_hits))
        if keyword_hits:
            reasons.append("keyword 命中：" + "、".join(keyword_hits))
        if customer_issue_type_hits:
            reasons.append("客户问题类型命中：" + "、".join(customer_issue_type_hits))
        return score, "；".join(reasons)

    def match_owner_rules(self, state: WatcherGraphState) -> dict[str, Any]:
        watcher = state["watcher"]
        results: list[WatcherAssignmentResult] = []
        unmatched_bugs: list[ParsedBug] = []

        for bug in state.get("assignment_bugs", []):
            best_rule: OwnerRule | None = None
            best_score = 0
            best_reason = ""
            keyword_haystack = bug.title if watcher.match_mode == "fixed_match" else " ".join([bug.title, bug.raw_excerpt, bug.status])
            for rule in watcher.owner_rules:
                score, reason = self._match_rule(bug, rule, keyword_haystack=keyword_haystack)
                if score > best_score:
                    best_rule = rule
                    best_score = score
                    best_reason = reason

            if best_rule is None or best_score == 0:
                unmatched_bugs.append(bug)
                continue

            results.append(
                WatcherAssignmentResult(
                    bug_id=bug.bug_id,
                    bug_aid=bug.bug_aid,
                    jira_issue_id=bug.jira_issue_id,
                    jira_form_token=bug.jira_form_token,
                    jira_atl_token=bug.jira_atl_token,
                    title=bug.title,
                    service=bug.service,
                    module=bug.module,
                    status=bug.status,
                    raw_excerpt=bug.raw_excerpt,
                    assignee_code=best_rule.assignee_code,
                    match_source="rule",
                    match_reason=best_reason or "规则命中。",
                    assignment_status="pending",
                )
            )

        return {"assignment_results": results, "unmatched_bugs": unmatched_bugs}

    def llm_assign_fallback(self, state: WatcherGraphState) -> dict[str, Any]:
        watcher = state["watcher"]
        results = list(state.get("assignment_results", []))

        for bug in state.get("unmatched_bugs", []):
            suggestion = self.llm_service.suggest_bug_owner(
                bug=bug,
                owner_rules=watcher.owner_rules,
                model_config=watcher.model_settings,
            )
            if suggestion.matched:
                results.append(
                    WatcherAssignmentResult(
                        bug_id=bug.bug_id,
                        bug_aid=bug.bug_aid,
                        jira_issue_id=bug.jira_issue_id,
                        jira_form_token=bug.jira_form_token,
                        jira_atl_token=bug.jira_atl_token,
                        title=bug.title,
                        service=bug.service,
                        module=bug.module,
                        status=bug.status,
                        raw_excerpt=bug.raw_excerpt,
                        assignee_code=suggestion.assignee_code,
                        match_source=suggestion.match_source,
                        match_reason=suggestion.reason,
                        assignment_status="pending",
                    )
                )
            else:
                results.append(
                    WatcherAssignmentResult(
                        bug_id=bug.bug_id,
                        bug_aid=bug.bug_aid,
                        jira_issue_id=bug.jira_issue_id,
                        jira_form_token=bug.jira_form_token,
                        jira_atl_token=bug.jira_atl_token,
                        title=bug.title,
                        service=bug.service,
                        module=bug.module,
                        status=bug.status,
                        raw_excerpt=bug.raw_excerpt,
                        assignee_code=None,
                        match_source="unmatched",
                        match_reason=suggestion.reason or "规则和 LLM 兜底都没有找到转派目标。",
                        assignment_status="unmatched",
                        assignment_message="未匹配转派目标，未调用分配接口。",
                    )
                )
        return {"assignment_results": results, "unmatched_bugs": []}

    def call_assignment_api(self, state: WatcherGraphState) -> dict[str, Any]:
        watcher = state["watcher"]
        try:
            updated = self.call_assignment_api_callback(watcher, state.get("assignment_results", []))
            return {"assignment_results": updated}
        except Exception as exc:
            patched: list[WatcherAssignmentResult] = []
            for item in state.get("assignment_results", []):
                if item.assignment_status in {"unmatched", "skipped"}:
                    patched.append(item)
                    continue
                patched.append(
                    item.model_copy(
                        update={"assignment_status": "failed", "assignment_message": f"分配接口调用失败：{exc}"}
                    )
                )
            return {
                "assignment_results": patched,
                "run_status": "partial_success",
                "error_message": f"分配接口调用失败：{exc}",
            }

    def compose_email(self, state: WatcherGraphState) -> dict[str, Any]:
        watcher = state["watcher"]
        assignment_results = state.get("assignment_results", [])
        snapshot_bugs = state.get("parsed_bugs", []) if state.get("force_email_snapshot") else []
        assign_current_list = bool(state.get("force_assign_snapshot"))
        new_bug_count = len(state.get("new_bugs", []))
        if assign_current_list and len(assignment_results) > 0:
            subject = (
                f"[巡检 Agent] {watcher.name} 立即执行结果："
                f"当前列表分配 {len(assignment_results)} 条"
                + (f" / 新增 {new_bug_count} 条" if new_bug_count > 0 else "")
            )
        elif assign_current_list:
            subject = f"[巡检 Agent] {watcher.name} 立即执行结果：当前列表未命中转派规则"
        elif len(assignment_results) > 0 and len(snapshot_bugs) > 0:
            subject = (
                f"[巡检 Agent] {watcher.name} 立即执行结果："
                f"当前 {len(snapshot_bugs)} 条 / 新增 {new_bug_count or len(assignment_results)} 条"
            )
        elif len(assignment_results) > 0:
            subject = f"[巡检 Agent] {watcher.name} 发现 {new_bug_count or len(assignment_results)} 个新增 Bug"
        else:
            subject = f"[巡检 Agent] {watcher.name} 当前 Bug 列表快照（{len(snapshot_bugs)} 条）"
        body = self.llm_service.compose_watcher_email_summary(
            watcher_name=watcher.name,
            dashboard_url=watcher.dashboard_url,
            started_at=state.get("run_started_at", _utc_now()),
            assignment_results=assignment_results,
            snapshot_bugs=snapshot_bugs,
            new_bug_count=new_bug_count,
            assign_current_list=assign_current_list,
        )
        html_body = self.llm_service.compose_watcher_email_html(
            watcher_name=watcher.name,
            dashboard_url=watcher.dashboard_url,
            started_at=state.get("run_started_at", _utc_now()),
            assignment_results=assignment_results,
            snapshot_bugs=snapshot_bugs,
            new_bug_count=new_bug_count,
            assign_current_list=assign_current_list,
        )
        return {"email_subject": subject, "email_body": body, "email_html": html_body}

    def send_email(self, state: WatcherGraphState) -> dict[str, Any]:
        watcher = state["watcher"]
        try:
            self.send_email_callback(
                watcher,
                state.get("email_subject", ""),
                state.get("email_body", ""),
                state.get("email_html"),
            )
            all_results = state.get("assignment_results", [])
            snapshot_bugs = state.get("parsed_bugs", []) if state.get("force_email_snapshot") else []
            has_failed_assignment = any(item.assignment_status == "failed" for item in all_results)
            if len(all_results) == 0 and len(snapshot_bugs) > 0:
                return {
                    "emailed": True,
                    "run_status": state.get("run_status", "success"),
                    "summary": f"已发送当前 Bug 列表快照邮件，共 {len(snapshot_bugs)} 条。",
                }
            return {
                "emailed": True,
                "run_status": "partial_success" if has_failed_assignment else "success",
                "summary": (
                    (
                        f"本次按当前列表分配 {len(all_results)} 个 Bug，"
                        if state.get("force_assign_snapshot")
                        else f"本轮新增 {len(state.get('new_bugs', [])) or len(all_results)} 个 Bug，"
                    )
                    + "已发送邮件。"
                    + ("部分分配失败。" if has_failed_assignment else "分配与通知完成。")
                ),
            }
        except Exception as exc:
            bug_count = len(state.get("assignment_results", []))
            if bug_count == 0 and state.get("force_email_snapshot"):
                bug_count = len(state.get("parsed_bugs", []))
            return {
                "emailed": False,
                "run_status": "partial_success",
                "error_message": f"邮件发送失败：{exc}",
                "summary": f"本轮需要通知 {bug_count} 个 Bug，但邮件发送失败。",
            }

    def persist_run(self, state: WatcherGraphState) -> dict[str, Any]:
        run = self.persist_run_callback(state)
        return {"persisted_run": run}
