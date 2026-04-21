#!/usr/bin/env python3
"""支持问题 Agent RAG 离线评估脚本。

评估目标：
- 从已审核通过案例和反馈事实里抽样，形成最小可运行评估集；
- 跑当前 support_issue retrieval profile；
- 输出 Recall@3 / Recall@5 / MRR / no-hit 准确率；
- 给出“直接采纳 vs 修改后采纳”的简单关联分析。

首版默认强制走 learning mode，
这样即使本机没有真实 provider，也能用确定性 rewrite + rerank 回退跑完整链路。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.schemas import ModelConfig, SupportIssueAgentConfig, SupportIssueCaseCandidate, SupportIssueFeedbackFact
from app.services.knowledge_store import KnowledgeStore
from app.services.llm_service import LLMService
from app.services.provider_store import ProviderStore
from app.services.support_issue_store import SupportIssueStore
from app.settings import load_settings


FEEDBACK_ACCEPTED = "直接采纳"
FEEDBACK_REVISED_ACCEPTED = "修改后采纳"
FEEDBACK_REJECTED = "驳回"
POSITIVE_FEEDBACK_RESULTS = {FEEDBACK_ACCEPTED, FEEDBACK_REVISED_ACCEPTED}


@dataclass(frozen=True)
class EvaluationSample:
    """一条离线评估样本。"""

    agent_id: str
    record_id: str
    query: str
    category: str
    source: str
    feedback_result: str
    positive_document_id: str | None = None
    expected_no_hit: bool = False


def _default_sqlite_path() -> Path:
    settings = load_settings()
    candidates = [
        settings.sqlite_path,
        settings.backend_dir / "agent_demo.db",
        settings.backend_dir / "agent_demo.sqlite",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return settings.sqlite_path


def _default_chroma_dir() -> Path:
    settings = load_settings()
    candidates = [
        settings.chroma_dir,
        settings.backend_dir / "data" / "chroma",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return settings.chroma_dir


def _learning_model_config() -> ModelConfig:
    return ModelConfig(mode="learning", provider="mock", model="learning-mode")


def _choose_model_config(agent: SupportIssueAgentConfig, *, use_agent_model: bool) -> ModelConfig:
    return agent.model_settings if use_agent_model else _learning_model_config()


def _candidate_samples(agent: SupportIssueAgentConfig, candidates: list[SupportIssueCaseCandidate]) -> list[EvaluationSample]:
    samples: list[EvaluationSample] = []
    for candidate in candidates:
        if candidate.question.strip() == "" or not candidate.knowledge_document_id:
            continue
        samples.append(
            EvaluationSample(
                agent_id=agent.id,
                record_id=candidate.record_id,
                query=candidate.question.strip(),
                category=candidate.question_category.strip(),
                source="approved_case_candidate",
                feedback_result=candidate.feedback_result.strip(),
                positive_document_id=candidate.knowledge_document_id,
            )
        )
    return samples


def _feedback_samples(
    agent: SupportIssueAgentConfig,
    facts: list[SupportIssueFeedbackFact],
    candidate_by_record: dict[str, SupportIssueCaseCandidate],
    seen_record_ids: set[str],
) -> list[EvaluationSample]:
    samples: list[EvaluationSample] = []
    for fact in facts:
        if fact.question.strip() == "" or fact.record_id in seen_record_ids:
            continue

        matched_candidate = candidate_by_record.get(fact.record_id)
        if fact.feedback_result in POSITIVE_FEEDBACK_RESULTS and matched_candidate and matched_candidate.knowledge_document_id:
            samples.append(
                EvaluationSample(
                    agent_id=agent.id,
                    record_id=fact.record_id,
                    query=fact.question.strip(),
                    category=fact.question_category.strip(),
                    source="feedback_fact_positive",
                    feedback_result=fact.feedback_result.strip(),
                    positive_document_id=matched_candidate.knowledge_document_id,
                )
            )
            seen_record_ids.add(fact.record_id)
            continue

        if fact.feedback_result == FEEDBACK_REJECTED or (
            fact.retrieval_hit_count <= 0
            and fact.feedback_result not in POSITIVE_FEEDBACK_RESULTS
            and matched_candidate is None
        ):
            samples.append(
                EvaluationSample(
                    agent_id=agent.id,
                    record_id=fact.record_id,
                    query=fact.question.strip(),
                    category=fact.question_category.strip(),
                    source="feedback_fact_no_hit",
                    feedback_result=fact.feedback_result.strip(),
                    positive_document_id=None,
                    expected_no_hit=True,
                )
            )
            seen_record_ids.add(fact.record_id)
    return samples


def build_evaluation_samples(
    support_issue_store: SupportIssueStore,
    *,
    agent_id: str | None = None,
) -> tuple[dict[str, SupportIssueAgentConfig], list[EvaluationSample]]:
    """从支持问题库里抽出评估样本。"""

    agents = support_issue_store.list_agents()
    if agent_id:
        agents = [agent for agent in agents if agent.id == agent_id]

    agent_map = {agent.id: agent for agent in agents}
    samples: list[EvaluationSample] = []
    for agent in agents:
        approved_candidates = support_issue_store.list_approved_case_candidates(agent.id)
        candidate_by_record = {item.record_id: item for item in approved_candidates}
        facts = support_issue_store.list_feedback_facts(agent.id)

        candidate_samples = _candidate_samples(agent, approved_candidates)
        seen_record_ids = {item.record_id for item in candidate_samples}
        feedback_samples = _feedback_samples(agent, facts, candidate_by_record, seen_record_ids)
        samples.extend(candidate_samples)
        samples.extend(feedback_samples)

    return agent_map, samples


def _first_rank(document_ids: list[str], target_document_id: str) -> int | None:
    for index, document_id in enumerate(document_ids, start=1):
        if document_id == target_document_id:
            return index
    return None


def _ordered_unique(values: list[str]) -> list[str]:
    """保留顺序去重，避免同一文档的多个 chunk 污染评估排名。"""

    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if normalized == "" or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def evaluate_samples(
    *,
    samples: list[EvaluationSample],
    agent_map: dict[str, SupportIssueAgentConfig],
    knowledge_store: KnowledgeStore,
    llm_service: LLMService,
    use_agent_model: bool,
    preview_limit: int,
) -> dict[str, Any]:
    """执行离线评估，并返回结构化结果。"""

    positive_total = 0
    recall_at_3 = 0
    recall_at_5 = 0
    mrr_total = 0.0
    no_hit_total = 0
    no_hit_correct = 0
    direct_positive_ranks: list[int] = []
    rewritten_positive_ranks: list[int] = []
    sample_results: list[dict[str, Any]] = []

    from app.rag.pipeline import RAGPipeline

    pipeline = RAGPipeline(knowledge_store, llm_service)
    for sample in samples:
        agent = agent_map.get(sample.agent_id)
        if agent is None:
            continue

        result = pipeline.run(
            query=sample.query,
            scope_type=agent.knowledge_scope_type,
            scope_id=agent.knowledge_scope_id,
            model_config=_choose_model_config(agent, use_agent_model=use_agent_model),
            retrieval_profile="support_issue",
            query_bundle_context={
                "question": sample.query,
                "category": sample.category,
            },
        )

        debug = result.debug
        ranked_document_ids = _ordered_unique(
            [item.document_id for item in (debug.rerank_preview if debug is not None else [])]
        )
        selected_document_ids = _ordered_unique([item.document_id for item in result.citations])
        predicted_no_hit = debug.selected_count == 0 if debug is not None else len(result.citations) == 0

        sample_result: dict[str, Any] = {
            "agent_id": sample.agent_id,
            "record_id": sample.record_id,
            "source": sample.source,
            "feedback_result": sample.feedback_result,
            "query": sample.query,
            "rewritten_queries": (
                [item.query for item in debug.query_bundle.query_variants]
                if debug is not None
                else []
            ),
            "selected_document_ids": selected_document_ids,
            "rerank_top5_document_ids": ranked_document_ids[:5],
            "predicted_no_hit": predicted_no_hit,
        }

        if sample.positive_document_id:
            positive_total += 1
            rank = _first_rank(ranked_document_ids, sample.positive_document_id)
            sample_result["positive_document_id"] = sample.positive_document_id
            sample_result["positive_rank"] = rank
            if rank is not None:
                if rank <= 3:
                    recall_at_3 += 1
                if rank <= 5:
                    recall_at_5 += 1
                mrr_total += 1.0 / rank
                if sample.feedback_result == FEEDBACK_REVISED_ACCEPTED:
                    rewritten_positive_ranks.append(rank)
                else:
                    direct_positive_ranks.append(rank)
        elif sample.expected_no_hit:
            no_hit_total += 1
            if predicted_no_hit:
                no_hit_correct += 1

        sample_results.append(sample_result)

    def safe_rate(numerator: float, denominator: float) -> float:
        return 0.0 if denominator <= 0 else round(numerator / denominator, 4)

    def avg_mrr(ranks: list[int]) -> float:
        if not ranks:
            return 0.0
        return round(sum(1.0 / rank for rank in ranks) / len(ranks), 4)

    return {
        "sample_count": len(sample_results),
        "positive_sample_count": positive_total,
        "expected_no_hit_sample_count": no_hit_total,
        "metrics": {
            "recall_at_3": safe_rate(recall_at_3, positive_total),
            "recall_at_5": safe_rate(recall_at_5, positive_total),
            "mrr": safe_rate(mrr_total, positive_total),
            "no_hit_accuracy": safe_rate(no_hit_correct, no_hit_total),
        },
        "rewrite_correlation": {
            "direct_accept_sample_count": len(direct_positive_ranks),
            "manual_rewrite_sample_count": len(rewritten_positive_ranks),
            "direct_accept_avg_mrr": avg_mrr(direct_positive_ranks),
            "manual_rewrite_avg_mrr": avg_mrr(rewritten_positive_ranks),
            "manual_rewrite_rate": safe_rate(len(rewritten_positive_ranks), positive_total),
        },
        "preview": sample_results[:preview_limit],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="离线评估支持问题 Agent 的 RAG 提准效果。")
    parser.add_argument("--sqlite-path", type=Path, default=_default_sqlite_path())
    parser.add_argument("--chroma-dir", type=Path, default=_default_chroma_dir())
    parser.add_argument("--agent-id", type=str, default=None)
    parser.add_argument(
        "--use-agent-model",
        action="store_true",
        help="默认强制走 learning mode；加上这个参数后改为使用 Agent 自身模型配置。",
    )
    parser.add_argument("--preview-limit", type=int, default=8)
    args = parser.parse_args()

    settings = load_settings()
    provider_store = ProviderStore(args.sqlite_path)
    knowledge_store = KnowledgeStore(args.sqlite_path, args.chroma_dir, provider_store=provider_store, settings=settings)
    support_issue_store = SupportIssueStore(args.sqlite_path)
    llm_service = LLMService(provider_store=provider_store)

    agent_map, samples = build_evaluation_samples(support_issue_store, agent_id=args.agent_id)
    if not samples:
        print(
            json.dumps(
                {
                    "ok": False,
                    "message": "没有找到可评估的支持问题样本。请先确认是否已有已审核案例或反馈事实。",
                    "agent_id": args.agent_id,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    summary = evaluate_samples(
        samples=samples,
        agent_map=agent_map,
        knowledge_store=knowledge_store,
        llm_service=llm_service,
        use_agent_model=args.use_agent_model,
        preview_limit=max(1, args.preview_limit),
    )
    print(json.dumps({"ok": True, **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
