from __future__ import annotations

import logging

from src.core import WorkflowContext

from .audit_handler import AuditHandler
from .audit_result import AuditResult

logger = logging.getLogger(__name__)


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
