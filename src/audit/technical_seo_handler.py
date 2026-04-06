from __future__ import annotations

import logging

from src.core import WorkflowContext

from .audit_handler import AuditHandler
from .audit_result import AuditResult

logger = logging.getLogger(__name__)


class TechnicalSEOHandler(AuditHandler):
    """
    B2 — Technical SEO Audit.
    Checks: page speed, sitemap presence, broken links.

    Short-circuits if score drops below PASS_THRESHOLD.
    In production: integrate PageSpeed Insights API, sitemap fetch, link crawler.
    """

    PASS_THRESHOLD: float = 40.0

    def _handle(self, ctx: WorkflowContext) -> AuditResult:
        logger.info("[TechnicalSEOHandler] Running technical SEO audit for %r", ctx.domain)

        findings: list[str] = []
        checks:   dict[str, bool] = {}

        # ── Stub checks — replace with real implementations ───────────────────
        speed_score   = self._check_page_speed(ctx.domain)
        has_sitemap   = self._check_sitemap(ctx.domain)
        broken_links  = self._check_broken_links(ctx.domain)

        # Speed
        if speed_score < 50:
            findings.append(f"Page speed score is low: {speed_score}/100")
            checks["speed"] = False
        else:
            checks["speed"] = True

        # Sitemap
        if not has_sitemap:
            findings.append("No sitemap.xml found")
            checks["sitemap"] = False
        else:
            checks["sitemap"] = True

        # Broken links
        if broken_links:
            findings.append(f"{len(broken_links)} broken link(s) detected")
            checks["broken_links"] = False
        else:
            checks["broken_links"] = True

        passed_checks = sum(checks.values())
        score = (passed_checks / len(checks)) * 100.0
        passed = score >= self.PASS_THRESHOLD

        ctx.set_state("technical_seo_checks", checks)
        ctx.set_state("broken_links", broken_links)

        return AuditResult(
            handler  = "TechnicalSEOHandler",
            passed   = passed,
            score    = score,
            findings = findings,
            metadata = {
                "speed_score":  speed_score,
                "has_sitemap":  has_sitemap,
                "broken_links": broken_links,
                "checks":       checks,
            },
        )

    # ── Stubs ──────────────────────────────────────────────────────────────────

    def _check_page_speed(self, domain: str) -> float:
        """Returns 0–100. Stub returns 85. Replace with PageSpeed Insights API."""
        return 85.0

    def _check_sitemap(self, domain: str) -> bool:
        """Returns True if sitemap.xml exists. Stub returns True."""
        return True

    def _check_broken_links(self, domain: str) -> list[str]:
        """Returns list of broken URLs. Stub returns empty list."""
        return []
