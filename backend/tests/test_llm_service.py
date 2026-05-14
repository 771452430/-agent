"""LLM provider compatibility regression tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas import ModelConfig, ProviderRuntimeConfig
from app.services.llm_service import LLMService
from app.services.provider_store import ProviderStore


class LLMServiceCompatibilityTests(unittest.TestCase):
    """Cover provider response shapes seen in OpenAI-compatible gateways."""

    def test_direct_completion_continues_after_empty_chat_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = LLMService(ProviderStore(Path(temp_dir) / "providers.sqlite"))
            now = datetime.now(timezone.utc)
            provider = ProviderRuntimeConfig(
                id="custom_openai",
                name="Custom OpenAI Compatible",
                enabled=True,
                protocol="openai_compatible",
                allowed_protocols=["openai_compatible"],
                api_base_url="https://api.example.test",
                api_key="test-key",
                models=[],
                locked=False,
                created_at=now,
                updated_at=now,
            )
            model_config = ModelConfig(
                mode="provider",
                provider="custom_openai",
                model="gpt-5.2",
                temperature=0.2,
                max_tokens=256,
            )

            def fake_attempts(**_: Any) -> list[tuple[str, str, dict[str, Any]]]:
                return [
                    ("chat-string", "https://api.example.test/v1/chat/completions", {}),
                    ("responses-input_text", "https://api.example.test/responses", {}),
                ]

            def fake_post(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
                del headers, payload
                if url.endswith("/chat/completions"):
                    return {"choices": [{"message": {"role": "assistant"}, "finish_reason": "stop"}]}
                return {
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "打开指定的服务使用 jDiwork.openService。",
                                }
                            ],
                        }
                    ]
                }

            service._candidate_completion_attempts = fake_attempts  # type: ignore[method-assign]
            service._request_json_post = fake_post  # type: ignore[method-assign]

            result = service._direct_completion_response(
                provider=provider,
                model_config=model_config,
                prompt_messages=[],
                citations=[],
                tool_outputs={},
            )

        self.assertEqual(result.answer, "打开指定的服务使用 jDiwork.openService。")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
