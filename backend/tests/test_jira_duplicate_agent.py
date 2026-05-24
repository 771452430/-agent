"""Jira 重复工单审核 Agent 回归测试。"""

from __future__ import annotations

import json
import base64
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from app.schemas import (
    CreateJiraDuplicateAgentRequest,
    FinalResponse,
    JiraDuplicateCandidate,
    JiraDuplicateIssueResult,
    JiraSolutionDraftReplyRequest,
    JiraSolutionSearchRequest,
    ModelConfig,
    UpdateJiraDataSourceSettingsRequest,
    ParsedBug,
)
from app.services.jira_data_source_service import JiraDataSourceService
from app.services.jira_data_source_store import JiraDataSourceStore
from app.services.jira_duplicate_service import MATCH_TEXT_NORMALIZER_VERSION, JiraDuplicateService
from app.services.jira_duplicate_store import JiraDuplicateStore
from app.services.llm_service import LLMService
from app.services.provider_store import ProviderStore
from app.settings import AppSettings


LEGACY_CONFIG_KEY = b"jira_skill_shared_key_v2024_secure"


def _write_legacy_jira_config(path: Path, *, app_key: str, app_secret: str) -> None:
    payload = json.dumps({"JIRA_APP_KEY": app_key, "JIRA_APP_SECRET": app_secret}, ensure_ascii=False).encode("utf-8")
    encrypted = bytearray()
    for index, byte in enumerate(payload):
        encrypted.append(byte ^ LEGACY_CONFIG_KEY[index % len(LEGACY_CONFIG_KEY)])
    path.write_bytes(base64.b64encode(bytes(encrypted)))


def _create_source_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE jira_support_issues (
                id TEXT PRIMARY KEY,
                issue_key TEXT UNIQUE,
                summary TEXT,
                feature_type TEXT,
                status TEXT,
                priority TEXT,
                domain TEXT,
                module TEXT,
                dev_type TEXT,
                return_type TEXT,
                sop_vdm TEXT,
                created TEXT,
                updated TEXT,
                confirm_time TEXT,
                solution TEXT,
                description TEXT,
                project_key TEXT,
                project_name TEXT,
                assignee_key TEXT,
                assignee_name TEXT,
                assignee_display_name TEXT,
                assignee_email TEXT,
                reporter_key TEXT,
                reporter_name TEXT,
                reporter_display_name TEXT,
                reporter_email TEXT,
                versions TEXT,
                fix_versions TEXT,
                query_project_id INTEGER,
                query_domain_module TEXT,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO jira_support_issues (
                id, issue_key, summary, status, domain, module, solution, description,
                versions, fix_versions, updated
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "1",
                    "YYZJ-138327",
                    "【DSP支持问题】刚打了331合集补丁，出纳的用户设置的是登录需要插CA输密码，现在是CA的弹框一直加载不出来，着急，请集团协助解决！！！",
                    "支持确认完成",
                    "工作台",
                    "登录入口与配置",
                    "内外网问题，已有补丁。补丁版本 aPaaS_iuap-apcom-workbench_iuap.7.0.2507_20250824-34_QP20260414-9439。",
                    "CA 弹框一直加载不出来，登录前需要插 CA 输密码。\n【帐户分享链接】友户通\nhttps://euc.yonyoucloud.com/cas/shareLogin/ABC123",
                    "331",
                    "",
                    "2026-04-01",
                ),
                (
                    "2",
                    "QYJX-1",
                    "合并报表导出数据量过大导致服务重启",
                    "支持确认完成",
                    "合并报表",
                    "50工作台_合并工作台",
                    "拆分表单分批导出，避开批量取数高峰。",
                    "报表导出问题。\n【账户分享链接】友户通\nhttps://euc.yonyoucloud.com/cas/shareLogin/XYZ789",
                    "",
                    "",
                    "2026-04-02",
                ),
                (
                    "3",
                    "YYZJ-117685",
                    "【DSP支持问题】打了合集补丁后，其实授权没了",
                    "支持确认完成",
                    "工作台",
                    "计量控制",
                    "重新刷新许可授权。",
                    "打了合集补丁后授权没了。",
                    "",
                    "",
                    "2026-04-02",
                ),
                (
                    "5",
                    "EXPS-195190",
                    "【DSP支持问题】企业微信工作台登录报错，企业微信号149，员工之前更换过手机号，最新手机号19382264687",
                    "研发已完成",
                    "财务",
                    "工作台",
                    "需要删除之前手机号关联的数据，等下下个紧急窗口提sql工单进行修复",
                    "企业微信工作台登录报错，员工之前更换过手机号。",
                    "",
                    "",
                    "2026-04-03",
                ),
                (
                    "4",
                    "YYZJ-PENDING",
                    "待处理工单不应该进入历史案例",
                    "待分析",
                    "工作台",
                    "工作台",
                    "这条不能被召回。",
                    "未完成。",
                    "",
                    "",
                    "2026-04-03",
                ),
            ],
        )


class JiraDataSourceServiceTests(unittest.TestCase):
    """Jira 历史库同步服务测试。"""

    def test_update_masks_secret_and_uses_server_db_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            settings = AppSettings(
                data_dir=data_dir,
                uploads_dir=data_dir / "uploads",
                sqlite_path=data_dir / "app.sqlite",
                chroma_dir=data_dir / "chroma",
                jira_support_db_path=data_dir / "jira" / "jira_support.db",
                jira_legacy_skill_dir=None,
            )
            settings.ensure_directories()
            service = JiraDataSourceService(JiraDataSourceStore(settings.sqlite_path), settings)

            saved = service.update_settings(
                UpdateJiraDataSourceSettingsRequest(
                    app_key="app-key",
                    app_secret="super-secret-value",
                    sync_keyword="工作台",
                    sync_date_range="本年",
                )
            )

            self.assertEqual(saved.db_path, str(settings.jira_support_db_path))
            self.assertTrue(saved.has_app_secret)
            self.assertNotEqual(saved.app_secret_masked, "super-secret-value")
            self.assertEqual(saved.credential_source, "settings")

    def test_legacy_skill_config_is_used_when_no_secret_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            legacy_dir = Path(temp_dir) / "legacy-skill"
            legacy_dir.mkdir()
            _write_legacy_jira_config(legacy_dir / "jira_config.enc", app_key="legacy-key", app_secret="legacy-secret")
            settings = AppSettings(
                data_dir=data_dir,
                uploads_dir=data_dir / "uploads",
                sqlite_path=data_dir / "app.sqlite",
                chroma_dir=data_dir / "chroma",
                jira_support_db_path=data_dir / "jira" / "jira_support.db",
                jira_app_key=None,
                jira_app_secret=None,
                jira_legacy_skill_dir=legacy_dir,
            )
            settings.ensure_directories()
            service = JiraDataSourceService(JiraDataSourceStore(settings.sqlite_path), settings)

            public = service.get_public_settings()
            runtime = service.get_runtime_settings()
            effective_runtime, source = service._resolve_runtime_credentials(runtime)

            self.assertTrue(public.has_app_secret)
            self.assertEqual(public.credential_source, "legacy_skill")
            self.assertEqual(source, "legacy_skill")
            self.assertEqual(effective_runtime.app_key, "legacy-key")
            self.assertEqual(effective_runtime.app_secret, "legacy-secret")

    def test_saved_secret_takes_priority_over_legacy_skill_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            legacy_dir = Path(temp_dir) / "legacy-skill"
            legacy_dir.mkdir()
            _write_legacy_jira_config(legacy_dir / "jira_config.enc", app_key="legacy-key", app_secret="legacy-secret")
            settings = AppSettings(
                data_dir=data_dir,
                uploads_dir=data_dir / "uploads",
                sqlite_path=data_dir / "app.sqlite",
                chroma_dir=data_dir / "chroma",
                jira_support_db_path=data_dir / "jira" / "jira_support.db",
                jira_app_key=None,
                jira_app_secret=None,
                jira_legacy_skill_dir=legacy_dir,
            )
            settings.ensure_directories()
            service = JiraDataSourceService(JiraDataSourceStore(settings.sqlite_path), settings)
            service.update_settings(
                UpdateJiraDataSourceSettingsRequest(app_key="saved-key", app_secret="saved-secret")
            )

            public = service.get_public_settings()
            runtime = service.get_runtime_settings()
            effective_runtime, source = service._resolve_runtime_credentials(runtime)

            self.assertEqual(public.credential_source, "settings")
            self.assertEqual(source, "settings")
            self.assertEqual(effective_runtime.app_key, "saved-key")
            self.assertEqual(effective_runtime.app_secret, "saved-secret")

    def test_missing_secret_still_reports_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            settings = AppSettings(
                data_dir=data_dir,
                uploads_dir=data_dir / "uploads",
                sqlite_path=data_dir / "app.sqlite",
                chroma_dir=data_dir / "chroma",
                jira_support_db_path=data_dir / "jira" / "jira_support.db",
                jira_app_key=None,
                jira_app_secret=None,
                jira_legacy_skill_dir=None,
            )
            settings.ensure_directories()
            service = JiraDataSourceService(JiraDataSourceStore(settings.sqlite_path), settings)

            result = service.test_settings()

            self.assertFalse(result.ok)
            self.assertIn("缺少 JIRA_APP_SECRET", result.message)

    def test_sync_saves_records_and_triggers_reindex_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            settings = AppSettings(
                data_dir=data_dir,
                uploads_dir=data_dir / "uploads",
                sqlite_path=data_dir / "app.sqlite",
                chroma_dir=data_dir / "chroma",
                jira_support_db_path=data_dir / "jira" / "jira_support.db",
                jira_legacy_skill_dir=None,
            )
            settings.ensure_directories()
            service = JiraDataSourceService(JiraDataSourceStore(settings.sqlite_path), settings)
            service._init_data = {  # type: ignore[attr-defined]
                "jira_projects": [],
                "jira_domain_modules": [
                    {
                        "project_id": "101",
                        "pkey": "YYZJ",
                        "project_name": "云平台-工作台",
                        "domain_name": "工作台",
                        "domain_module": "登录入口与配置",
                    }
                ],
            }
            service.update_settings(
                UpdateJiraDataSourceSettingsRequest(
                    app_key="app-key",
                    app_secret="super-secret-value",
                    sync_keyword="工作台",
                    sync_date_range="本年",
                )
            )

            def fake_query(**_: object) -> list[dict[str, object]]:
                return [
                    {
                        "id": "1",
                        "issueKey": "YYZJ-1",
                        "summary": "CA 登录异常",
                        "status": "支持确认完成",
                        "domain": "工作台",
                        "module": "登录入口与配置",
                        "solution": "安装对应修复补丁。",
                        "description": "打补丁后 CA 弹框加载不出来。",
                        "project": {"projectKey": "YYZJ", "projectName": "云平台-工作台"},
                        "assignee": {},
                        "reporter": {},
                    }
                ]

            service.query_jira_data_all = fake_query  # type: ignore[method-assign]
            reindexed_paths: list[str] = []

            def fake_reindex(path: str) -> dict[str, int]:
                reindexed_paths.append(path)
                return {"indexed_count": 1}

            run = service.sync_now(reindex_callback=fake_reindex)

            self.assertEqual(run.status, "success")
            self.assertEqual(run.fetched_count, 1)
            self.assertEqual(run.inserted_count, 1)
            self.assertEqual(run.reindexed_count, 1)
            self.assertEqual(reindexed_paths, [str(settings.jira_support_db_path)])
            with sqlite3.connect(settings.jira_support_db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM jira_support_issues").fetchone()[0]
            self.assertEqual(count, 1)


class JiraDuplicateAgentTests(unittest.TestCase):
    def _build_service(self, temp_dir: Path) -> tuple[JiraDuplicateService, JiraDuplicateStore]:
        app_sqlite = temp_dir / "app.sqlite"
        chroma_dir = temp_dir / "chroma"
        provider_store = ProviderStore(temp_dir / "providers.sqlite")
        settings = AppSettings(
            root_dir=temp_dir,
            backend_dir=temp_dir,
            data_dir=temp_dir,
            uploads_dir=temp_dir / "uploads",
            sqlite_path=app_sqlite,
            chroma_dir=chroma_dir,
        )
        store = JiraDuplicateStore(app_sqlite)
        service = JiraDuplicateService(
            store=store,
            llm_service=LLMService(provider_store),
            settings=settings,
            provider_store=provider_store,
        )
        return service, store

    def test_reindex_only_reads_completed_solved_cases(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp_dir = Path(raw_temp)
            source_db = temp_dir / "jira_support.db"
            _create_source_db(source_db)
            before_tables = sqlite3.connect(source_db).execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()

            service, store = self._build_service(temp_dir)
            agent = store.create_agent(
                CreateJiraDuplicateAgentRequest(
                    name="测试 Agent",
                    source_db_path=str(source_db),
                    dashboard_url="http://example.test/jira",
                    enabled=False,
                ),
                ModelConfig(),
            )

            result = service.reindex(agent.id)
            indexed_normalizer_version = store.indexed_normalizer_version()
            after_tables = sqlite3.connect(source_db).execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()

        self.assertEqual(before_tables, after_tables)
        self.assertEqual(result["indexed_count"], 4)
        self.assertEqual(indexed_normalizer_version, MATCH_TEXT_NORMALIZER_VERSION)
        self.assertFalse(agent.model_review_enabled)

    def test_reindex_strips_public_share_link_noise_from_search_text(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp_dir = Path(raw_temp)
            source_db = temp_dir / "jira_support.db"
            _create_source_db(source_db)
            service, store = self._build_service(temp_dir)
            agent = store.create_agent(
                CreateJiraDuplicateAgentRequest(
                    name="测试 Agent",
                    source_db_path=str(source_db),
                    dashboard_url="http://example.test/jira",
                    enabled=False,
                ),
                ModelConfig(),
            )
            service.reindex(agent.id)

            search_text = "\n".join(case["search_text"] for case in store.list_index_cases())

        self.assertNotIn("帐户分享链接", search_text)
        self.assertNotIn("账户分享链接", search_text)
        self.assertNotIn("shareLogin", search_text)
        self.assertNotIn("DSP支持问题", search_text)

    def test_public_share_link_noise_is_removed_from_current_issue_query(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            service, _store = self._build_service(Path(raw_temp))
            issue = ParsedBug(
                bug_id="YYZJ-NOISE",
                title="【DSP支持问题】采购订单保存失败",
                raw_excerpt=(
                    "问题描述：【DSP支持问题】采购订单保存失败。\n"
                    "【帐户分享链接】友户通\n"
                    "https://euc.yonyoucloud.com/cas/shareLogin/SAMEPUBLICLINK\n"
                    "--------------------------------------------------------------"
                ),
            )

            query = service._compose_issue_search_text(issue)

        self.assertIn("采购订单保存失败", query)
        self.assertNotIn("DSP支持问题", query)
        self.assertNotIn("帐户分享链接", query)
        self.assertNotIn("shareLogin", query)
        self.assertNotIn("SAMEPUBLICLINK", query)

    def test_public_share_link_overlap_does_not_make_unrelated_issue_high(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp_dir = Path(raw_temp)
            source_db = temp_dir / "jira_support.db"
            _create_source_db(source_db)
            service, store = self._build_service(temp_dir)
            agent = store.create_agent(
                CreateJiraDuplicateAgentRequest(
                    name="测试 Agent",
                    source_db_path=str(source_db),
                    dashboard_url="http://example.test/jira",
                    enabled=False,
                ),
                ModelConfig(),
            )
            service.reindex(agent.id)

            issue = ParsedBug(
                bug_id="YYZJ-UNRELATED",
                title="采购订单保存失败",
                raw_excerpt=(
                    "问题描述：采购订单保存失败。\n"
                    "【帐户分享链接】友户通\n"
                    "https://euc.yonyoucloud.com/cas/shareLogin/ABC123"
                ),
                service="供应链",
                module="采购订单",
                status="待分析",
            )
            result = service._match_current_issue(agent, issue)

        self.assertNotEqual(result.match_level, "high")

    def test_ca_patch_issue_matches_completed_ca_case(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp_dir = Path(raw_temp)
            source_db = temp_dir / "jira_support.db"
            _create_source_db(source_db)
            service, store = self._build_service(temp_dir)
            agent = store.create_agent(
                CreateJiraDuplicateAgentRequest(
                    name="测试 Agent",
                    source_db_path=str(source_db),
                    dashboard_url="http://example.test/jira",
                    enabled=False,
                ),
                ModelConfig(),
            )
            service.reindex(agent.id)

            issue = ParsedBug(
                bug_id="YYZJ-139171",
                title="【DSP支持问题】打了330补丁合集之后，ca用户登不上了，驱动加载正常，一点登陆页面就会刷新。",
                service="工作台",
                module="工作台",
                status="待分析",
            )
            result = service._match_current_issue(agent, issue)

        self.assertEqual(result.match_level, "high")
        self.assertGreaterEqual(result.match_score, 0.82)
        self.assertEqual(result.candidates[0].issue_key, "YYZJ-138327")
        self.assertTrue(result.candidates[0].solution.strip() != "")

    def test_manual_solution_search_uses_duplicate_matching(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp_dir = Path(raw_temp)
            source_db = temp_dir / "jira_support.db"
            _create_source_db(source_db)
            service, store = self._build_service(temp_dir)

            response = service.search_solution(
                JiraSolutionSearchRequest(
                    description="【DSP支持问题】打了330补丁合集之后，ca用户登不上了，驱动加载正常，一点登陆页面就会刷新。",
                    source_db_path=str(source_db),
                    domain="工作台",
                    module="工作台",
                )
            )
            indexed_count = store.case_count()

        self.assertGreater(indexed_count, 0)
        self.assertEqual(response.result.match_level, "high")
        self.assertEqual(response.result.candidates[0].issue_key, "YYZJ-138327")
        self.assertTrue(response.result.candidates[0].solution.strip() != "")
        self.assertIn("问题指纹匹配", response.result.candidates[0].reason)

    def test_manual_solution_search_is_invariant_to_public_dsp_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp_dir = Path(raw_temp)
            source_db = temp_dir / "jira_support.db"
            _create_source_db(source_db)
            service, _store = self._build_service(temp_dir)
            common_payload = {
                "source_db_path": str(source_db),
                "domain": "工作台",
                "module": "工作台",
            }

            with_prefix = service.search_solution(
                JiraSolutionSearchRequest(
                    description="【DSP支持问题】打了330补丁合集之后，ca用户登不上了，驱动加载正常，一点登陆页面就会刷新。",
                    **common_payload,
                )
            )
            without_prefix = service.search_solution(
                JiraSolutionSearchRequest(
                    description="打了330补丁合集之后，ca用户登不上了，驱动加载正常，一点登陆页面就会刷新。",
                    **common_payload,
                )
            )

        self.assertEqual(with_prefix.result.match_level, "high")
        self.assertEqual(without_prefix.result.match_level, "high")
        self.assertEqual(with_prefix.result.candidates[0].issue_key, "YYZJ-138327")
        self.assertEqual(without_prefix.result.candidates[0].issue_key, "YYZJ-138327")

    def test_draft_solution_reply_learning_mode_returns_template(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp_dir = Path(raw_temp)
            source_db = temp_dir / "jira_support.db"
            _create_source_db(source_db)
            service, _store = self._build_service(temp_dir)
            search_response = service.search_solution(
                JiraSolutionSearchRequest(
                    description="打了330补丁合集之后，ca用户登不上了，驱动加载正常，一点登陆页面就会刷新。",
                    source_db_path=str(source_db),
                    domain="工作台",
                    module="工作台",
                )
            )

            draft = service.draft_solution_reply(
                JiraSolutionDraftReplyRequest(
                    description="打了330补丁合集之后，ca用户登不上了，驱动加载正常，一点登陆页面就会刷新。",
                    result=search_response.result,
                    candidates=search_response.result.candidates,
                    model_settings=ModelConfig(),
                )
            )

        self.assertFalse(draft.generated_by_model)
        self.assertTrue(draft.message.startswith("当前为 Learning Mode"))
        self.assertTrue(draft.draft_text.startswith("您好"))
        self.assertIn("建议您先按以下方式处理", draft.draft_text)
        self.assertNotIn("Learning Mode", draft.draft_text)
        self.assertNotIn("模板草稿", draft.draft_text)
        self.assertNotIn("建议复用", draft.draft_text)
        self.assertNotIn("YYZJ-138327", draft.draft_text)
        self.assertNotIn("相似度", draft.draft_text)
        self.assertNotIn("请确认", draft.draft_text)
        self.assertNotIn("请提供", draft.draft_text)
        self.assertNotIn("补充截图", draft.draft_text)
        self.assertNotIn("发给我们后", draft.draft_text)

    def test_draft_solution_reply_provider_uses_llm_and_only_high_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp_dir = Path(raw_temp)
            source_db = temp_dir / "jira_support.db"
            _create_source_db(source_db)
            service, _store = self._build_service(temp_dir)
            search_response = service.search_solution(
                JiraSolutionSearchRequest(
                    description="打了330补丁合集之后，ca用户登不上了，驱动加载正常，一点登陆页面就会刷新。",
                    source_db_path=str(source_db),
                    domain="工作台",
                    module="工作台",
                )
            )
            low_candidate = JiraDuplicateCandidate(
                issue_key="LOW-1",
                summary="低相似候选不应进入草稿",
                solution="这段不应该传给模型",
                score=0.61,
            )

            with mock.patch.object(
                service.llm_service,
                "generate_response",
                return_value=FinalResponse(answer="模型生成的工单回复"),
            ) as generate_mock:
                draft = service.draft_solution_reply(
                    JiraSolutionDraftReplyRequest(
                        description="打了330补丁合集之后，ca用户登不上了，驱动加载正常，一点登陆页面就会刷新。",
                        result=search_response.result,
                        candidates=search_response.result.candidates + [low_candidate],
                        model_settings=ModelConfig(mode="provider", provider="custom_openai", model="gpt-5.4"),
                    )
                )

        self.assertTrue(draft.generated_by_model)
        self.assertEqual(draft.draft_text, "模型生成的工单回复")
        self.assertIn("客户支持客服", generate_mock.call_args.kwargs["system_prompt"])
        self.assertIn("一次性发给客户", generate_mock.call_args.kwargs["system_prompt"])
        self.assertIn("不要要求客户补充环境", generate_mock.call_args.kwargs["system_prompt"])
        tool_candidates = generate_mock.call_args.kwargs["tool_outputs"]["参考处理经验"]
        self.assertTrue(any(item["问题描述"] for item in tool_candidates))
        self.assertTrue(any(item["处理方式"] for item in tool_candidates))
        self.assertFalse(any("LOW-1" in json.dumps(item, ensure_ascii=False) for item in tool_candidates))
        self.assertFalse(any("相似度" in item for item in tool_candidates))
        self.assertFalse(any("工单号" in item for item in tool_candidates))

    def test_sanitize_customer_reply_removes_follow_up_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            service, _store = self._build_service(Path(raw_temp))
            sanitized = service._sanitize_customer_reply(
                "您好，请确认当前环境。请提供浏览器版本。[citations] 发给我们后我们继续处理。"
            )

        self.assertNotIn("请确认", sanitized)
        self.assertNotIn("请提供", sanitized)
        self.assertNotIn("[citations]", sanitized)
        self.assertNotIn("发给我们后", sanitized)

    def test_draft_solution_reply_rejects_when_no_high_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            service, _store = self._build_service(Path(raw_temp))
            medium_result = JiraDuplicateIssueResult(
                issue_key="MANUAL-QUERY",
                title="普通问题",
                match_level="medium",
                match_score=0.62,
                candidates=[
                    JiraDuplicateCandidate(
                        issue_key="LOW-1",
                        summary="低相似候选",
                        solution="低相似方案",
                        score=0.62,
                    )
                ],
            )

            with self.assertRaises(HTTPException):
                service.draft_solution_reply(
                    JiraSolutionDraftReplyRequest(
                        description="普通问题",
                        result=medium_result,
                        candidates=medium_result.candidates,
                        model_settings=ModelConfig(),
                    )
                )

    def test_patch_only_overlap_does_not_promote_unrelated_case(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp_dir = Path(raw_temp)
            source_db = temp_dir / "jira_support.db"
            _create_source_db(source_db)
            service, store = self._build_service(temp_dir)
            agent = store.create_agent(
                CreateJiraDuplicateAgentRequest(
                    name="测试 Agent",
                    source_db_path=str(source_db),
                    dashboard_url="http://example.test/jira",
                    enabled=False,
                ),
                ModelConfig(),
            )
            service.reindex(agent.id)

            issue = ParsedBug(
                bug_id="YYZJ-139171",
                title="【DSP支持问题】打了330补丁合集之后，ca用户登不上了，驱动加载正常，一点登陆页面就会刷新。",
                service="工作台",
                module="工作台",
                status="待分析",
            )
            result = service._match_current_issue(agent, issue)

        authorization_candidate = next(
            (candidate for candidate in result.candidates if candidate.issue_key == "YYZJ-117685"),
            None,
        )
        if authorization_candidate is not None:
            self.assertLess(authorization_candidate.score, 0.78)

    def test_phone_number_login_issue_does_not_match_mobile_environment_by_phone_text(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp_dir = Path(raw_temp)
            source_db = temp_dir / "jira_support.db"
            _create_source_db(source_db)
            service, store = self._build_service(temp_dir)
            agent = store.create_agent(
                CreateJiraDuplicateAgentRequest(
                    name="测试 Agent",
                    source_db_path=str(source_db),
                    dashboard_url="http://example.test/jira",
                    enabled=False,
                ),
                ModelConfig(),
            )
            service.reindex(agent.id)

            issue = ParsedBug(
                bug_id="YYZJ-139146",
                title="租户管理员登录友空间提示账号异常，没有注册，手机号13891820939。",
                status="待分析",
            )
            query = service._compose_issue_search_text(issue)
            signature = service._issue_signature(query)
            result = service._match_current_issue(agent, issue)

        self.assertNotIn("移动端", signature.environments)
        self.assertNotEqual(result.match_level, "high")

    def test_generic_report_export_signature_can_promote_to_high(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp_dir = Path(raw_temp)
            source_db = temp_dir / "jira_support.db"
            _create_source_db(source_db)
            service, store = self._build_service(temp_dir)
            agent = store.create_agent(
                CreateJiraDuplicateAgentRequest(
                    name="测试 Agent",
                    source_db_path=str(source_db),
                    dashboard_url="http://example.test/jira",
                    enabled=False,
                ),
                ModelConfig(),
            )
            service.reindex(agent.id)

            issue = ParsedBug(
                bug_id="QYJX-PENDING",
                title="合并报表导出的时候数据量太大，系统服务会重启，麻烦协助处理。",
                service="合并报表",
                module="50工作台_合并工作台",
                status="待分析",
            )
            result = service._match_current_issue(agent, issue)

        self.assertEqual(result.match_level, "high")
        self.assertEqual(result.candidates[0].issue_key, "QYJX-1")
        self.assertIn("问题指纹匹配", result.candidates[0].reason)

    def test_model_review_is_skipped_when_switch_is_off(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp_dir = Path(raw_temp)
            source_db = temp_dir / "jira_support.db"
            _create_source_db(source_db)
            service, store = self._build_service(temp_dir)
            agent = store.create_agent(
                CreateJiraDuplicateAgentRequest(
                    name="测试 Agent",
                    source_db_path=str(source_db),
                    dashboard_url="http://example.test/jira",
                    enabled=False,
                    model_settings=ModelConfig(mode="provider", provider="custom_openai", model="gpt-5.4"),
                ),
                ModelConfig(mode="provider", provider="custom_openai", model="gpt-5.4"),
            )
            service.reindex(agent.id)
            issue = ParsedBug(
                bug_id="YYZJ-MEDIUM",
                title="老师您好，补丁后登录异常，需要老师帮忙分析。",
                service="工作台",
                module="工作台",
                status="待分析",
            )

            with mock.patch.object(service, "_case_similarity", return_value=0.70), \
                mock.patch.object(service, "_important_overlap_bonus", return_value=0.0), \
                mock.patch.object(service, "_signature_match", return_value=(0.0, "", False)), \
                mock.patch.object(service, "_query_vector_candidates", return_value=[]), \
                mock.patch.object(service, "_model_boundary_judgement", side_effect=AssertionError("should not call")):
                result = service._match_current_issue(agent, issue)

        self.assertEqual(result.match_level, "medium")

    def test_model_review_is_used_when_switch_is_on(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp_dir = Path(raw_temp)
            source_db = temp_dir / "jira_support.db"
            _create_source_db(source_db)
            service, store = self._build_service(temp_dir)
            agent = store.create_agent(
                CreateJiraDuplicateAgentRequest(
                    name="测试 Agent",
                    source_db_path=str(source_db),
                    dashboard_url="http://example.test/jira",
                    enabled=False,
                    model_review_enabled=True,
                    model_settings=ModelConfig(mode="provider", provider="custom_openai", model="gpt-5.4"),
                ),
                ModelConfig(mode="provider", provider="custom_openai", model="gpt-5.4"),
            )
            service.reindex(agent.id)
            issue = ParsedBug(
                bug_id="YYZJ-MEDIUM",
                title="老师您好，补丁后登录异常，需要老师帮忙分析。",
                service="工作台",
                module="工作台",
                status="待分析",
            )

            with mock.patch.object(service, "_case_similarity", return_value=0.70), \
                mock.patch.object(service, "_important_overlap_bonus", return_value=0.0), \
                mock.patch.object(service, "_signature_match", return_value=(0.0, "", False)), \
                mock.patch.object(service, "_query_vector_candidates", return_value=[]), \
                mock.patch.object(service, "_model_boundary_judgement", return_value=(0.82, "模型复核：同类问题")) as review_mock:
                result = service._match_current_issue(agent, issue)

        self.assertTrue(review_mock.called)
        self.assertEqual(result.match_level, "high")

    def test_new_default_thresholds_are_more_recall_friendly(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp_dir = Path(raw_temp)
            source_db = temp_dir / "jira_support.db"
            _create_source_db(source_db)
            service, store = self._build_service(temp_dir)
            agent = store.create_agent(
                CreateJiraDuplicateAgentRequest(
                    name="测试 Agent",
                    source_db_path=str(source_db),
                    dashboard_url="http://example.test/jira",
                    enabled=False,
                ),
                ModelConfig(),
            )

        self.assertEqual(agent.high_similarity_threshold, 0.78)
        self.assertEqual(agent.medium_similarity_threshold, 0.55)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
