from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from src.core import StepBase, WorkflowContext

logger = logging.getLogger(__name__)


# ── Exceptions ─────────────────────────────────────────────────────────────────

class AuditError(Exception):
    """Raised by an AuditHandler on a hard failure (not a short-circuit)."""


# ── Audit result ───────────────────────────────────────────────────────────────

@dataclass
class AuditResult:
    handler:  str
    passed:   bool
    score:    float = 0.0           # 0.0 – 100.0
    findings: list[str] = field(default_factory=list)
    metadata: dict      = field(default_factory=dict)

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"<AuditResult {self.handler} {status} score={self.score:.1f}>"


# ── AuditHandler — Chain of Responsibility with short-circuit ──────────────────

class AuditHandler(ABC):
    """
    Chain of Responsibility base for Step 2.

    handle() returns True  → audit passed, chain continues.
    handle() returns False → audit failed, chain is SHORT-CIRCUITED.

    Each handler appends its AuditResult to ctx results under
    the key  "audit_results"  (a list).
    """

    def __init__(self) -> None:
        self._next: Optional[AuditHandler] = None

    def set_next(self, handler: AuditHandler) -> AuditHandler:
        self._next = handler
        return handler

    def handle(self, ctx: WorkflowContext) -> bool:
        result = self._handle(ctx)

        # Store result in context
        audit_results: list[AuditResult] = ctx.get_result("audit_results", [])
        audit_results.append(result)
        ctx.set_result("audit_results", audit_results)

        if not result.passed:
            logger.warning(
                "[%s] Audit FAILED (score=%.1f) — short-circuiting chain. Findings: %s",
                result.handler, result.score, result.findings,
            )
            return False

        logger.info(
            "[%s] Audit PASSED (score=%.1f)",
            result.handler, result.score,
        )

        if self._next:
            return self._next.handle(ctx)

        return True

    @abstractmethod
    def _handle(self, ctx: WorkflowContext) -> AuditResult: ...

    def __repr__(self) -> str:
        nxt = self._next.__class__.__name__ if self._next else None
        return f"<{self.__class__.__name__} next={nxt}>"


# ── Concrete handlers ──────────────────────────────────────────────────────────

class CrawlerAuditHandler(AuditHandler):
    """
    B1 — AI Crawler Audit.
    Checks whether AI bots (GPTBot, PerplexityBot, etc.)
    are permitted to crawl the domain.

    In production: fetch robots.txt and meta tags, then parse.
    Here we provide a realistic stub with the full interface.
    """

    KNOWN_AI_BOTS = [
        "GPTBot", "PerplexityBot", "GoogleExtended",
        "CCBot", "anthropic-ai",
    ]

    def _handle(self, ctx: WorkflowContext) -> AuditResult:
        logger.info("[CrawlerAuditHandler] Checking AI bot access for %r", ctx.domain)

        findings: list[str] = []
        blocked:  list[str] = []

        # ── Stub: replace with real robots.txt fetch + parse ──────────────────
        robots_rules: dict[str, bool] = self._fetch_robots_txt(ctx.domain)

        for bot in self.KNOWN_AI_BOTS:
            if not robots_rules.get(bot, True):
                blocked.append(bot)
                findings.append(f"{bot} is blocked in robots.txt")

        # ── Stub: replace with real meta-tag check ────────────────────────────
        noindex = self._check_meta_noindex(ctx.domain)
        if noindex:
            findings.append("Meta noindex tag detected — AI crawlers may ignore content")

        passed = len(blocked) == 0 and not noindex
        score  = 100.0 - (len(blocked) / len(self.KNOWN_AI_BOTS)) * 80.0
        if noindex:
            score -= 20.0
        score = max(0.0, score)

        ctx.set_state("crawler_blocked_bots", blocked)

        return AuditResult(
            handler  = "CrawlerAuditHandler",
            passed   = passed,
            score    = score,
            findings = findings,
            metadata = {"blocked_bots": blocked, "noindex": noindex},
        )

    # ── Stubs — replace with real implementations ─────────────────────────────

    def _fetch_robots_txt(self, domain: str) -> dict[str, bool]:
        """
        Returns {bot_name: is_allowed}.
        Stub always allows all bots — replace with real HTTP fetch + parse.
        """
        return {bot: True for bot in self.KNOWN_AI_BOTS}

    def _check_meta_noindex(self, domain: str) -> bool:
        """
        Returns True if a noindex meta tag is present.
        Stub always returns False — replace with real HTML fetch + parse.
        """
        return False


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