from __future__ import annotations

import logging
import os
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from src.core import WorkflowContext

from src.audit import AuditHandler
from src.audit import AuditResult

logger = logging.getLogger(__name__)

# Google PageSpeed Insights API — free, no auth needed for basic use.
# Set PAGESPEED_API_KEY env var for higher quota.
_PAGESPEED_API = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
_PAGESPEED_KEY = os.getenv("PAGESPEED_API_KEY", "")

# Timeouts (seconds)
_CONNECT_TIMEOUT  = 5.0
_REQUEST_TIMEOUT  = 15.0


class TechnicalSEOHandler(AuditHandler):
    """
    B2 — Technical SEO Audit.

    Three real checks:
      1. Page speed  — Google PageSpeed Insights API (mobile strategy).
      2. Sitemap     — HEAD request to /sitemap.xml; falls back to
                       parsing <loc> entries in robots.txt.
      3. Broken links — crawls all <a href> links on the homepage and
                        HEAD-checks each one for a non-2xx/3xx status.

    Short-circuits the audit chain if overall score < PASS_THRESHOLD.
    """

    PASS_THRESHOLD:      float = 40.0
    MAX_LINKS_TO_CHECK:  int   = 50     # cap to avoid long audit times

    # ── Template entry point ───────────────────────────────────────────────────

    def _handle(self, ctx: WorkflowContext) -> AuditResult:
        logger.info("[TechnicalSEOHandler] Running technical SEO audit for %r", ctx.domain)

        findings: list[str] = []
        checks:   dict[str, bool] = {}

        speed_score  = self._check_page_speed(ctx.domain)
        has_sitemap  = self._check_sitemap(ctx.domain)
        broken_links = self._check_broken_links(ctx.domain)

        # ── Speed ──────────────────────────────────────────────────────────────
        if speed_score < 50:
            findings.append(f"Page speed score is low: {speed_score:.0f}/100")
            checks["speed"] = False
        else:
            checks["speed"] = True

        # ── Sitemap ────────────────────────────────────────────────────────────
        if not has_sitemap:
            findings.append("No sitemap.xml found")
            checks["sitemap"] = False
        else:
            checks["sitemap"] = True

        # ── Broken links ───────────────────────────────────────────────────────
        if broken_links:
            findings.append(f"{len(broken_links)} broken link(s) detected")
            checks["broken_links"] = False
        else:
            checks["broken_links"] = True

        passed_checks = sum(checks.values())
        score  = (passed_checks / len(checks)) * 100.0
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

    # ── Check 1 — Page speed ───────────────────────────────────────────────────

    def _check_page_speed(self, domain: str) -> float:
        """
        Calls Google PageSpeed Insights API.
        Returns the Lighthouse performance score (0–100).
        Falls back to 0.0 on any error.
        """
        url    = f"https://{domain}"
        params = {"url": url, "strategy": "mobile"}
        if _PAGESPEED_KEY:
            params["key"] = _PAGESPEED_KEY

        try:
            with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
                resp = client.get(_PAGESPEED_API, params=params)
                resp.raise_for_status()
                data = resp.json()

            # Score lives at lighthouseResult.categories.performance.score (0–1)
            score = (
                data
                .get("lighthouseResult", {})
                .get("categories", {})
                .get("performance", {})
                .get("score", 0)
            )
            result = round(float(score) * 100, 1)
            logger.info("[TechnicalSEOHandler] PageSpeed score: %s", result)
            return result

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "[TechnicalSEOHandler] PageSpeed API returned %s: %s",
                exc.response.status_code, exc.response.text[:200],
            )
            return 0.0
        except Exception as exc:
            logger.warning("[TechnicalSEOHandler] PageSpeed check failed: %s", exc)
            return 0.0

    # ── Check 2 — Sitemap ──────────────────────────────────────────────────────

    def _check_sitemap(self, domain: str) -> bool:
        """
        1. HEAD https://<domain>/sitemap.xml
        2. If not found, parse robots.txt for a Sitemap: directive.
        Returns True if either locates a sitemap.
        """
        base_url     = f"https://{domain}"
        sitemap_url  = urljoin(base_url, "/sitemap.xml")

        try:
            with httpx.Client(
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as client:
                resp = client.head(sitemap_url)
                if resp.status_code < 400:
                    logger.info(
                        "[TechnicalSEOHandler] sitemap.xml found at %s (HTTP %s)",
                        sitemap_url, resp.status_code,
                    )
                    return True

                # Fall back — look for Sitemap: line in robots.txt
                robots_url  = urljoin(base_url, "/robots.txt")
                robots_resp = client.get(robots_url)
                if robots_resp.status_code == 200:
                    for line in robots_resp.text.splitlines():
                        if line.strip().lower().startswith("sitemap:"):
                            sitemap_ref = line.split(":", 1)[1].strip()
                            logger.info(
                                "[TechnicalSEOHandler] Sitemap found in robots.txt: %s",
                                sitemap_ref,
                            )
                            return True

        except Exception as exc:
            logger.warning("[TechnicalSEOHandler] Sitemap check failed: %s", exc)

        logger.info("[TechnicalSEOHandler] No sitemap found for %r", domain)
        return False

    # ── Check 3 — Broken links ─────────────────────────────────────────────────

    def _check_broken_links(self, domain: str) -> list[str]:
        """
        1. Fetches the homepage HTML.
        2. Extracts all <a href> links that belong to the same domain.
        3. HEAD-checks up to MAX_LINKS_TO_CHECK of them.
        Returns a list of URLs that returned a 4xx or 5xx status.
        """
        base_url    = f"https://{domain}"
        broken:     list[str] = []
        parsed_base = urlparse(base_url)

        try:
            with httpx.Client(
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "TechnicalSEOAuditBot/1.0"},
            ) as client:

                # Fetch homepage
                home_resp = client.get(base_url)
                home_resp.raise_for_status()

                soup  = BeautifulSoup(home_resp.text, "html.parser")
                hrefs = {
                    a["href"]
                    for a in soup.find_all("a", href=True)
                    if a["href"].startswith(("http://", "https://", "/"))
                }

                # Resolve relative URLs and filter to same domain
                same_domain_links: list[str] = []
                for href in hrefs:
                    full = href if href.startswith("http") else urljoin(base_url, href)
                    if urlparse(full).netloc == parsed_base.netloc:
                        same_domain_links.append(full)

                # Cap the number of links to check
                to_check = same_domain_links[: self.MAX_LINKS_TO_CHECK]
                logger.info(
                    "[TechnicalSEOHandler] Checking %d internal links for breakage",
                    len(to_check),
                )

                for link in to_check:
                    try:
                        r = client.head(link, follow_redirects=True)
                        if r.status_code >= 400:
                            broken.append(link)
                            logger.debug(
                                "[TechnicalSEOHandler] Broken link %s (%s)",
                                link, r.status_code,
                            )
                    except Exception as link_exc:
                        # Treat connection errors as broken
                        broken.append(link)
                        logger.debug(
                            "[TechnicalSEOHandler] Link unreachable %s: %s",
                            link, link_exc,
                        )

        except Exception as exc:
            logger.warning("[TechnicalSEOHandler] Broken link check failed: %s", exc)

        logger.info(
            "[TechnicalSEOHandler] Broken links found: %d", len(broken)
        )
        return broken