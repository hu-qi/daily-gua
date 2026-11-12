"""生成编排：LLM 生产 → 解析 → 白名单清洗 → 校验；任一步失败即降级为链接汇总。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..common.config import Settings
from ..common.models import Post, utc_now
from ..common.redaction import get_logger
from ..rank.scorer import ScoreBreakdown
from .llm import LLMClient, LLMError
from .melons import MelonScore, melon_index
from .parse import PayloadError, parse_llm_payload
from .prompts import build_messages
from .report import GuaReport, ReportMeta, build_degraded_report, build_report

LOGGER = get_logger(__name__)


@dataclass
class GenerateResult:
    report: GuaReport
    notes: list[str] = field(default_factory=list)


def generate_report(
    primary: Post,
    evidence: list[Post],
    candidates: list[ScoreBreakdown],
    *,
    date: str,
    settings: Settings,
    llm: LLMClient | None = None,
    sources_used: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
    degrade_reason: str | None = None,
    now=None,
) -> GenerateResult:
    """产出一定成功：LLM 不可用时降级，降级原因写进报告与 Issue。"""
    moment = now or utc_now()
    melon: MelonScore = melon_index(primary, now=moment)
    notes: list[str] = []
    client = llm if llm is not None else LLMClient(settings)

    meta = ReportMeta(
        llm_model=settings.llm_model,
        collected_at=moment.isoformat(),
        sources_used=sources_used,
        warnings=warnings,
    )

    forced = degrade_reason
    if forced is None and not client.available:
        forced = "LLM 未配置（缺少 LLM_BASE_URL / LLM_API_KEY）"
    if forced is not None:
        notes.append(f"{forced}，已降级为链接汇总")
        report = build_degraded_report(
            primary,
            candidates=candidates,
            date=date,
            melon=melon,
            reason=forced,
            meta=meta,
        )
        return GenerateResult(report=report, notes=notes)

    messages = build_messages(primary, evidence, melon, token_budget=settings.token_budget)
    try:
        raw = client.complete(messages)
        payload = parse_llm_payload(raw)
    except (LLMError, PayloadError) as exc:
        LOGGER.warning("LLM 生成失败，降级为链接汇总: %s", exc)
        notes.append(f"LLM 生成失败，已降级: {exc}")
        report = build_degraded_report(
            primary,
            candidates=candidates,
            date=date,
            melon=melon,
            reason=f"LLM 生成失败（{exc}），本页仅保留链接汇总",
            meta=meta,
        )
        return GenerateResult(report=report, notes=notes)

    report = build_report(
        payload,
        primary=primary,
        evidence=evidence,
        candidates=candidates,
        date=date,
        melon=melon,
        meta=meta,
    )
    if report.meta.degraded:
        notes.append(f"生成内容未通过溯源校验，已降级: {report.meta.downgrade_reason}")
    else:
        LOGGER.info("LLM 瓜报生成成功：%s", report.headline)
    return GenerateResult(report=report, notes=notes)
