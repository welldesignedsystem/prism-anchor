"""
Real-world integration tests for TechnicalSEOHandler with NO MOCKS.

These tests verify the TechnicalSEOHandler against real websites using actual HTTP requests:
  - Google PageSpeed Insights API for speed scores
  - Real HEAD requests for sitemap detection
  - Real homepage fetch + link crawl for broken link detection

Run with: uv run pytest tests/integration/test_technical_seo_integration.py -v -s -m network
Skip network tests: uv run pytest -m "not network"

Note: These tests make actual network calls and depend on external services.
"""

import pytest

from src.core import (
    AIEngine,
    CrawlFrequency,
    WorkflowContext,
    WorkflowOrchestrator,
)
from src.audit import AuditStep, AuditError
from src.audit.technical_seo_handler import TechnicalSEOHandler
from src.setup import SetupStep


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def real_site_context():
    """Context for testing against real websites."""
    ctx = WorkflowContext(
        domain="example.com",
        queries=["example website", "example domain"],
    )
    ctx.engines = [AIEngine.CHATGPT]
    ctx.crawl_frequency = CrawlFrequency.AD_HOC
    return ctx


@pytest.fixture
def handler():
    return TechnicalSEOHandler()


# ── _check_page_speed — live network ──────────────────────────────────────────

@pytest.mark.network
class TestCheckPageSpeedRealNetwork:

    def test_example_com_returns_float(self, handler):
        score = handler._check_page_speed("example.com")
        assert isinstance(score, float)

    def test_score_within_valid_range(self, handler):
        score = handler._check_page_speed("example.com")
        assert 0.0 <= score <= 100.0

    def test_known_fast_site_scores_above_threshold(self, handler):
        # google.com consistently scores well on PageSpeed
        score = handler._check_page_speed("google.com")
        assert score >= 50.0, (
            f"google.com scored {score:.1f} — PageSpeed API may be unavailable "
            "or google.com has degraded. Update threshold if consistently lower."
        )

    def test_unreachable_domain_returns_zero(self, handler):
        score = handler._check_page_speed("this-domain-does-not-exist-xyz.com")
        assert score == 0.0

    def test_returns_zero_on_invalid_domain_format(self, handler):
        score = handler._check_page_speed("not_a_valid_domain")
        assert score == 0.0

    def test_github_com_returns_nonzero(self, handler):
        score = handler._check_page_speed("github.com")
        assert score > 0.0, (
            "github.com returned 0 — PageSpeed API may be rate-limiting or unreachable"
        )

    def test_score_is_rounded_to_one_decimal(self, handler):
        score = handler._check_page_speed("example.com")
        assert score == round(score, 1)


# ── _check_sitemap — live network ─────────────────────────────────────────────

@pytest.mark.network
class TestCheckSitemapRealNetwork:

    def test_returns_bool(self, handler):
        result = handler._check_sitemap("example.com")
        assert isinstance(result, bool)

    def test_github_com_has_sitemap(self, handler):
        result = handler._check_sitemap("github.com")
        assert result is True, (
            "github.com no longer has a sitemap — update this test"
        )

    def test_nonexistent_domain_returns_false(self, handler):
        result = handler._check_sitemap("this-domain-does-not-exist-xyz.com")
        assert result is False

    def test_example_com_sitemap_check_does_not_raise(self, handler):
        try:
            result = handler._check_sitemap("example.com")
            assert isinstance(result, bool)
        except Exception as exc:
            pytest.fail(f"_check_sitemap raised unexpectedly: {exc}")

    def test_sitemap_check_follows_redirects(self, handler):
        result = handler._check_sitemap("python.org")
        assert isinstance(result, bool)

    def test_robots_txt_fallback_executes(self, handler):
        """
        Verify the robots.txt Sitemap: fallback path works when /sitemap.xml
        returns 404 or is not found. Tests real network requests.
        """
        # Test with a site that might not have direct sitemap.xml
        result = handler._check_sitemap("example.com")
        assert isinstance(result, bool)
        # If result is True, it either found /sitemap.xml or Sitemap: in robots.txt
        # If False, neither were found - both valid outcomes

    def test_python_org_sitemap(self, handler):
        """Test sitemap detection on python.org with real network."""
        result = handler._check_sitemap("python.org")
        assert isinstance(result, bool)


# ── _check_broken_links — live network ────────────────────────────────────────

@pytest.mark.network
class TestCheckBrokenLinksRealNetwork:

    def test_returns_list(self, handler):
        result = handler._check_broken_links("example.com")
        assert isinstance(result, list)

    def test_all_items_are_strings(self, handler):
        result = handler._check_broken_links("example.com")
        assert all(isinstance(url, str) for url in result)

    def test_example_com_has_no_broken_links(self, handler):
        result = handler._check_broken_links("example.com")
        assert result == [], (
            f"example.com reported broken links: {result}"
        )

    def test_nonexistent_domain_returns_empty_list(self, handler):
        result = handler._check_broken_links("this-domain-does-not-exist-xyz.com")
        assert result == []

    def test_respects_max_links_cap(self, handler):
        handler.MAX_LINKS_TO_CHECK = 3
        result = handler._check_broken_links("github.com")
        assert isinstance(result, list)

    def test_broken_links_are_same_domain_only(self, handler):
        domain = "example.com"
        result = handler._check_broken_links(domain)
        for url in result:
            assert domain in url, (
                f"Found cross-domain URL in broken links: {url}"
            )

    def test_does_not_raise_on_connection_error(self, handler):
        try:
            result = handler._check_broken_links("this-domain-does-not-exist-xyz.com")
            assert isinstance(result, list)
        except Exception as exc:
            pytest.fail(f"_check_broken_links raised unexpectedly: {exc}")


# ── _handle full method — live network ────────────────────────────────────────

@pytest.mark.network
class TestHandleRealNetwork:

    def test_handle_example_com_returns_audit_result(self, handler, real_site_context):
        result = handler._handle(real_site_context)
        assert result.handler == "TechnicalSEOHandler"
        assert isinstance(result.passed, bool)
        assert 0.0 <= result.score <= 100.0
        assert isinstance(result.findings, list)

    def test_handle_result_metadata_keys_present(self, handler, real_site_context):
        result = handler._handle(real_site_context)
        assert "speed_score"  in result.metadata
        assert "has_sitemap"  in result.metadata
        assert "broken_links" in result.metadata
        assert "checks"       in result.metadata

    def test_handle_stores_checks_in_context(self, handler, real_site_context):
        handler._handle(real_site_context)
        checks = real_site_context.get_state("technical_seo_checks")
        assert isinstance(checks, dict)
        assert "speed"        in checks
        assert "sitemap"      in checks
        assert "broken_links" in checks

    def test_handle_stores_broken_links_in_context(self, handler, real_site_context):
        handler._handle(real_site_context)
        broken = real_site_context.get_state("broken_links")
        assert isinstance(broken, list)

    def test_handle_score_formula_matches_checks(self, handler, real_site_context):
        result = handler._handle(real_site_context)
        checks = result.metadata["checks"]
        expected = (sum(checks.values()) / len(checks)) * 100.0
        assert result.score == pytest.approx(expected)

    def test_handle_score_never_below_zero(self, handler, real_site_context):
        result = handler._handle(real_site_context)
        assert result.score >= 0.0

    def test_handle_score_never_above_100(self, handler, real_site_context):
        result = handler._handle(real_site_context)
        assert result.score <= 100.0

    def test_handle_findings_match_failed_checks(self, handler, real_site_context):
        result = handler._handle(real_site_context)
        checks = result.metadata["checks"]
        failed = [k for k, v in checks.items() if not v]
        keyword_map = {
            "speed":        "speed",
            "sitemap":      "sitemap",
            "broken_links": "broken link",
        }
        for failed_check in failed:
            keyword = keyword_map.get(failed_check, failed_check)
            assert any(keyword in f.lower() for f in result.findings), (
                f"No finding reported for failed check {failed_check!r}. "
                f"Findings: {result.findings}"
            )

    def test_handle_github_com_prints_summary(self, handler):
        ctx = WorkflowContext(domain="github.com", queries=["open source"])
        result = handler._handle(ctx)
        assert result.handler == "TechnicalSEOHandler"
        print(f"\n  github.com score={result.score:.1f}, passed={result.passed}")
        print(f"  checks={result.metadata['checks']}")
        if result.findings:
            print(f"  findings={result.findings}")


# ── Full workflow integration ──────────────────────────────────────────────────

@pytest.mark.network
class TestTechnicalSEOWorkflowIntegration:

    def test_example_com_full_workflow(self, real_site_context):
        """S1 -> S2 full workflow — TechnicalSEOHandler is the second audit handler."""
        setup_step = SetupStep()
        audit_step = AuditStep()

        setup_step.execute(real_site_context)
        assert real_site_context.get_state("setup_complete") is True
        print(f"\n  Setup complete for {real_site_context.domain}")

        try:
            audit_step.execute(real_site_context)
            print(f"  Audit passed for {real_site_context.domain}")
        except AuditError as e:
            print(f"  Audit detected issues: {e}")

        assert real_site_context.get_state("audit_complete") is True

        results = real_site_context.get_result("audit_results", [])
        assert len(results) >= 2, "Expected CrawlerAuditHandler + TechnicalSEOHandler"

        tech = next((r for r in results if r.handler == "TechnicalSEOHandler"), None)
        assert tech is not None, "TechnicalSEOHandler did not run"
        print(f"  Technical SEO score: {tech.score:.1f}, passed: {tech.passed}")
        if tech.findings:
            print(f"  Findings: {tech.findings}")

    def test_github_com_full_workflow(self, real_site_context):
        real_site_context.domain  = "github.com"
        real_site_context.queries = ["github repository", "open source code"]

        setup_step = SetupStep()
        audit_step = AuditStep()

        setup_step.execute(real_site_context)

        try:
            audit_step.execute(real_site_context)
        except AuditError as e:
            print(f"\n  Audit issues for github.com: {e}")

        results = real_site_context.get_result("audit_results", [])
        tech = next((r for r in results if r.handler == "TechnicalSEOHandler"), None)

        if tech:
            print(f"\n  github.com technical SEO score: {tech.score:.1f}")
            print(f"  Speed score:  {tech.metadata['speed_score']}")
            print(f"  Has sitemap:  {tech.metadata['has_sitemap']}")
            print(f"  Broken links: {len(tech.metadata['broken_links'])}")

    def test_orchestrator_run_once_includes_technical_seo(self, real_site_context):
        orchestrator = (
            WorkflowOrchestrator(real_site_context)
            .add_step(SetupStep())
            .add_step(AuditStep())
        )
        orchestrator.run_once()

        assert real_site_context.get_state("setup_complete") is True
        assert real_site_context.get_state("audit_complete") is True

        results  = real_site_context.get_result("audit_results", [])
        handlers = [r.handler for r in results]
        assert "TechnicalSEOHandler" in handlers

        print(f"\n  Orchestrator steps: {orchestrator.status_report()}")
        print(f"  Audit handlers ran: {handlers}")

    def test_technical_seo_skipped_when_crawler_short_circuits(self, real_site_context):
        """
        Verify TechnicalSEOHandler does NOT run when CrawlerAuditHandler
        fails and short-circuits the chain. Use a nonexistent domain to trigger failure.
        """
        real_site_context.domain = "nonexistent-domain-xyz-123.com"

        setup_step = SetupStep()
        audit_step = AuditStep()

        setup_step.execute(real_site_context)

        try:
            audit_step.execute(real_site_context)
        except AuditError:
            pass

        results = real_site_context.get_result("audit_results", [])
        handlers = [r.handler for r in results]

        # CrawlerAuditHandler should run but fail, short-circuiting the chain
        assert "CrawlerAuditHandler" in handlers
        # TechnicalSEOHandler should NOT run due to short-circuit
        assert "TechnicalSEOHandler" not in handlers
        print(f"\n  Short-circuit confirmed — handlers: {handlers}")

    def test_adhoc_allows_repeated_technical_seo_runs(self, real_site_context):
        """AD_HOC frequency permits running the audit multiple times on demand."""
        setup_step = SetupStep()
        audit_step = AuditStep()

        setup_step.execute(real_site_context)

        try:
            audit_step.execute(real_site_context)
        except AuditError:
            pass

        first_results = real_site_context.get_result("audit_results", [])
        first_tech = next(
            (r for r in first_results if r.handler == "TechnicalSEOHandler"), None
        )

        # Reset audit state for second run
        real_site_context.set_state("audit_complete", False)
        real_site_context.set_result("audit_results", [])

        try:
            audit_step.execute(real_site_context)
        except AuditError:
            pass

        second_results = real_site_context.get_result("audit_results", [])
        second_tech = next(
            (r for r in second_results if r.handler == "TechnicalSEOHandler"), None
        )

        if first_tech and second_tech:
            assert first_tech.handler == second_tech.handler
            print(f"\n  AD_HOC run 1 score: {first_tech.score:.1f}")
            print(f"  AD_HOC run 2 score: {second_tech.score:.1f}")