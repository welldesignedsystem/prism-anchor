from __future__ import annotations

import logging
import urllib.robotparser
import urllib.request
from html.parser import HTMLParser

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

    # ── Real implementations ───────────────────────────────────────────────────

    def _fetch_robots_txt(self, domain: str) -> dict[str, bool]:
        """
        Fetches and parses robots.txt, returning {bot_name: is_allowed}
        for every bot in KNOWN_AI_BOTS.

        Falls back to allowing all bots on any network/parse error so that
        a missing or unreachable robots.txt never fails the audit unfairly.
        """
        robots_url = f"https://{domain}/robots.txt"
        rp = urllib.robotparser.RobotFileParser(url=robots_url)

        try:
            rp.read()                           # one HTTP GET, stdlib only
        except Exception:
            logger.warning(
                "[CrawlerAuditHandler] Could not fetch %s — assuming all allowed",
                robots_url,
            )
            return {bot: True for bot in self.KNOWN_AI_BOTS}

        # urllib.robotparser.can_fetch() requires a path, not just a domain.
        # "/" covers the root; AI bots that care about a specific path would
        # need a more targeted check — extend here as required.
        return {
            bot: rp.can_fetch(bot, f"https://{domain}/")
            for bot in self.KNOWN_AI_BOTS
        }

    def _check_meta_noindex(self, domain: str) -> bool:
        """
        Fetches the homepage HTML and returns True if any <meta> tag
        contains a noindex directive targeting robots/AI crawlers.

        Only the first 32 KB of the response is read: meta tags always
        live in <head>, so there is no need to download the full page.
        """
        url = f"https://{domain}/"

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; AuditBot/1.0)"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                # Read only the head of the document to keep latency low.
                html_chunk = resp.read(32_768).decode("utf-8", errors="replace")
        except Exception:
            logger.warning(
                "[CrawlerAuditHandler] Could not fetch %s for meta-tag check", url
            )
            return False

        parser = _MetaTagParser()
        parser.feed(html_chunk)
        return parser.noindex

class _MetaTagParser(HTMLParser):
    """Lightweight parser that scans <meta> tags for noindex directives."""

    def __init__(self):
        super().__init__()
        self.noindex = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return

        attr = dict(attrs)
        name    = (attr.get("name")    or "").lower()
        content = (attr.get("content") or "").lower()

        # <meta name="robots" content="noindex, ...">
        # <meta name="googlebot" content="noindex, ...">
        if name in {"robots", "googlebot", "bingbot"} and "noindex" in content:
            self.noindex = True