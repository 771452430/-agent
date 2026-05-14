"""Support issue prompt regression tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas import Citation, FinalResponse, ModelConfig, SupportIssueAgentConfig
from app.services.llm_service import LLMService, SUPPORT_ISSUE_RESPONSE_STYLE_PROMPT
from app.services.provider_store import ProviderStore
from app.services.support_issue_service import SupportIssueService


class SupportIssuePromptTests(unittest.TestCase):
    """Keep support issue AI solution prompts human-readable and consistent."""

    def test_support_issue_system_prompt_includes_style_guidance(self) -> None:
        prompt = SupportIssueService._compose_system_prompt(object(), "FAQ")
        self.assertIn(SUPPORT_ISSUE_RESPONSE_STYLE_PROMPT, prompt)

    def test_retrieval_summary_prompt_includes_style_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = LLMService(ProviderStore(Path(temp_dir) / "providers.sqlite"))
            captured: dict[str, Any] = {}

            def fake_generate_response(**kwargs: Any) -> FinalResponse:
                captured.update(kwargs)
                return FinalResponse(answer="整理后的方案")

            service.generate_response = fake_generate_response  # type: ignore[method-assign]

            citation = Citation(
                document_id="doc-1",
                document_name="示例文档",
                chunk_id="chunk-1",
                snippet="这里是证据片段。",
            )
            answer = service.summarize_retrieval(
                query="示例问题",
                citations=[citation],
                retrieval_context="检索上下文",
                model_config=ModelConfig(mode="learning", provider="mock", model="mock-model"),
            )

        self.assertEqual(answer, "整理后的方案")
        self.assertIn(SUPPORT_ISSUE_RESPONSE_STYLE_PROMPT, captured["system_prompt"])

    def test_draft_prompt_includes_style_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = LLMService(ProviderStore(Path(temp_dir) / "providers.sqlite"))
            captured: dict[str, Any] = {}

            def fake_invoke_structured_output_with_fallback(**kwargs: Any) -> Any:
                prompt = kwargs["prompt"]
                prompt_variables = kwargs["prompt_variables"]
                captured["system_prompt"] = prompt.invoke(prompt_variables).to_messages()[0].content
                return kwargs["fallback_result"]

            service._invoke_structured_output_with_fallback = fake_invoke_structured_output_with_fallback  # type: ignore[method-assign]

            result = service.draft_support_solution(
                question="示例问题",
                category="FAQ",
                retrieval_summary="这里是检索总结。",
                retrieval_hit_count=1,
                similar_case_context="",
                similar_case_count=0,
                model_config=ModelConfig(mode="learning", provider="mock", model="mock-model"),
            )

        self.assertEqual(result.solution, "这里是检索总结。")
        self.assertIn(SUPPORT_ISSUE_RESPONSE_STYLE_PROMPT, captured["system_prompt"])

    def test_runtime_field_mapping_skips_missing_optional_write_fields(self) -> None:
        class FakeFeishuService:
            def list_bitable_fields(self, **_kwargs: Any) -> list[dict[str, Any]]:
                return [
                    {"field_name": "问题", "type": 1, "ui_type": "Text"},
                    {"field_name": "AI解决方案", "type": 1, "ui_type": "Text"},
                    {"field_name": "回复进度", "type": 3, "ui_type": "SingleSelect"},
                    {"field_name": "域名（xxx@yonyou.com）", "type": 1, "ui_type": "Text"},
                ]

        now = datetime.now(timezone.utc)
        agent = SupportIssueAgentConfig(
            id="agent-1",
            name="Support Agent",
            description="",
            enabled=True,
            poll_interval_minutes=30,
            feishu_bitable_url="https://example.com/base/app-token?table=table-id",
            feishu_app_token="app-token",
            feishu_table_id="table-id",
            model_config=ModelConfig(mode="learning", provider="mock", model="mock-model"),
            knowledge_scope_type="global",
            knowledge_scope_id=None,
            question_field_name="问题",
            answer_field_name="AI解决方案",
            link_field_name="相关文档链接",
            progress_field_name="回复进度",
            status_field_name="处理状态",
            module_field_name="负责模块",
            support_staff_field_name="支持人员",
            registrant_field_name="域名（xxx@yonyou.com）",
            feedback_result_field_name="人工处理结果",
            feedback_final_answer_field_name="人工最终方案",
            feedback_comment_field_name="反馈备注",
            confidence_field_name="AI置信度",
            hit_count_field_name="命中知识数",
            support_owner_rules=[],
            fallback_support_yht_user_id="",
            digest_enabled=False,
            digest_recipient_emails=[],
            case_review_enabled=True,
            created_at=now,
            updated_at=now,
        )
        service = object.__new__(SupportIssueService)
        service.feishu_service = FakeFeishuService()

        resolved_fields = service._resolve_runtime_field_mapping(agent, records=[])

        self.assertEqual(resolved_fields["answer"].field_name, "AI解决方案")
        self.assertEqual(resolved_fields["progress"].field_name, "回复进度")
        self.assertEqual(resolved_fields["link"].field_name, "")
        self.assertEqual(resolved_fields["module"].field_name, "")
        self.assertEqual(resolved_fields["support_staff"].field_name, "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
