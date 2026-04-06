from __future__ import annotations

import logging

from src.core import StepBase, WorkflowContext

from .audit_error import AuditError
from .audit_handler import AuditHandler
from .audit_result import AuditResult
from .crawler_audit_handler import CrawlerAuditHandler
from .technical_seo_handler import TechnicalSEOHandler
from .content_audit_handler import ContentAuditHandler

logger = logging.getLogger(__name__)


# ── AuditStep ──────────────────────────────────────────────────────────────────

class AuditStep(StepBase):
    """
    Step 2 — Audits using Chain of Responsibility with short-circuit.

    Chain: CrawlerAuditHandler → TechnicalSEOHandler → ContentAuditHandler

    If any handler returns False (audit failed), the chain stops and
    _validate() raises AuditError so the orchestrator knows setup is
    incomplete before monitoring begins.

    A custom chain can be injected for testing.
    """

    def __init__(self, chain: AuditHandler | None = None) -> None:
        super().__init__(name="S2_Audit")
        self._chain = chain or self._build_default_chain()

    def _setup(self, context: WorkflowContext) -> None:
        if not context.get_state("setup_complete"):
            raise AuditError("S1_Setup must complete before S2_Audit.")
        logger.info("[AuditStep] Starting audits for %r", context.domain)

    def _run(self, context: WorkflowContext) -> None:
        passed = self._chain.handle(context)
        context.set_state("audit_passed", passed)

    def _validate(self, context: WorkflowContext) -> None:
        results: list[AuditResult] = context.get_result("audit_results", [])

        if not results:
            raise AuditError("No audit results were produced.")

        failed = [r for r in results if not r.passed]
        if failed:
            summary = "; ".join(
                f"{r.handler} (score={r.score:.1f})" for r in failed
            )
            raise AuditError(f"Audit(s) failed — {summary}")

        logger.info(
            "[AuditStep] All %d audit(s) passed", len(results)
        )

    def _teardown(self, context: WorkflowContext) -> None:
        context.set_state("audit_complete", True)

    @staticmethod
    def _build_default_chain() -> AuditHandler:
        crawler   = CrawlerAuditHandler()
        technical = TechnicalSEOHandler()
        content   = ContentAuditHandler()
        crawler.set_next(technical).set_next(content)
        return crawler
