from __future__ import annotations

import logging

from src.core import WorkflowContext

from .audit_handler import AuditHandler
from .audit_result import AuditResult

logger = logging.getLogger(__name__)


class ContentAuditHandler(AuditHandler):
    """
    B3 — Content Audit.
    Scores each tracked URL for AEO (Answer Engine Optimisation)
    and GEO (Generative Engine Optimisation) readiness.

    Operates on ctx.queries as representative content signals.
    In production: fetch each URL, parse content, run scoring models.
    """

    PASS_THRESHOLD: float = 50.0

    def _handle(self, ctx: WorkflowContext) -> AuditResult:
        logger.info("[ContentAuditHandler] Running content audit for %r", ctx.domain)

        findings:    list[str] = []
        page_scores: list[dict] = []

        for query in ctx.queries:
            aeo_score, geo_score = self._score_content(ctx.domain, query)
            page_scores.append({
                "query":     query,
                "aeo_score": aeo_score,
                "geo_score": geo_score,
            })
            if aeo_score < 50:
                findings.append(f"Low AEO score ({aeo_score:.0f}) for query: {query!r}")
            if geo_score < 50:
                findings.append(f"Low GEO score ({geo_score:.0f}) for query: {query!r}")

        avg_aeo = sum(p["aeo_score"] for p in page_scores) / len(page_scores)
        avg_geo = sum(p["geo_score"] for p in page_scores) / len(page_scores)
        score   = (avg_aeo + avg_geo) / 2
        passed  = score >= self.PASS_THRESHOLD

        ctx.set_result("content_audit_scores", page_scores)
        ctx.set_state("avg_aeo_score", avg_aeo)
        ctx.set_state("avg_geo_score", avg_geo)

        return AuditResult(
            handler  = "ContentAuditHandler",
            passed   = passed,
            score    = score,
            findings = findings,
            metadata = {
                "avg_aeo_score": avg_aeo,
                "avg_geo_score": avg_geo,
                "page_scores":   page_scores,
            },
        )

    # ── Stub ───────────────────────────────────────────────────────────────────

    def _score_content(self, domain: str, query: str) -> tuple[float, float]:
        """
        Returns (aeo_score, geo_score) for a domain/query pair.
        Stub returns (72.0, 68.0). Replace with real content scoring.
        """
        return 72.0, 68.0
