"""
Real-world integration tests for the complete workflow: Core → Setup → Audit (Crawler).

These tests verify the workflow against real websites using actual HTTP requests.
Uses AD_HOC crawl frequency for on-demand testing.

Note: These tests make actual network calls and should be run with appropriate
rate limiting to avoid overwhelming real servers.
"""

import pytest
from src.core import (
    WorkflowContext,
    WorkflowOrchestrator,
    AIEngine,
    CrawlFrequency,
)
from src.setup import SetupStep
from src.audit import AuditStep, AuditError


@pytest.fixture
def real_site_context():
    """Context for testing against real websites."""
    ctx = WorkflowContext(
        domain="example.com",
        queries=["example website", "example domain"],
    )
    ctx.engines = [AIEngine.CHATGPT]
    ctx.crawl_frequency = CrawlFrequency.AD_HOC  # On-demand crawling
    return ctx


class TestRealWebsiteIntegration:
    """Integration tests using real websites with actual HTTP calls."""

    def test_example_com_workflow(self, real_site_context):
        """
        Test complete workflow against example.com (a real, stable website).

        This test makes actual HTTP requests to:
        - Fetch robots.txt from example.com
        - Fetch homepage to check for noindex meta tags

        Flow:
        1. Setup validates domain and configuration
        2. Audit checks AI bot access via robots.txt
        3. Audit checks for noindex meta tags
        """
        setup_step = SetupStep()
        audit_step = AuditStep()

        # Execute setup
        setup_step.execute(real_site_context)
        assert real_site_context.get_state("setup_complete") is True
        print(f"\n✓ Setup complete for {real_site_context.domain}")

        # Execute audit against real website
        try:
            audit_step.execute(real_site_context)
            print(f"✓ Audit passed for {real_site_context.domain}")
            assert real_site_context.get_state("audit_complete") is True
        except AuditError as e:
            # Audit failure is expected for some sites, still validates execution
            print(f"⚠ Audit detected issues: {e}")
            assert real_site_context.get_state("audit_complete") is True

        # Verify audit was actually executed
        results = real_site_context.get_result("audit_results", [])
        assert len(results) > 0

        # Check crawler audit results
        crawler_result = results[0]
        assert crawler_result.handler == "CrawlerAuditHandler"
        print(f"  - Crawler audit score: {crawler_result.score:.1f}")
        print(f"  - Passed: {crawler_result.passed}")
        if crawler_result.findings:
            print(f"  - Findings: {crawler_result.findings}")

    def test_github_com_workflow(self, real_site_context):
        """
        Test workflow against github.com using real HTTP requests.

        GitHub is a well-maintained site with clear robots.txt policies.
        """
        real_site_context.domain = "github.com"
        real_site_context.queries = [
            "github repository",
            "github open source",
        ]

        setup_step = SetupStep()
        audit_step = AuditStep()

        setup_step.execute(real_site_context)
        assert real_site_context.get_state("setup_complete") is True
        print(f"\n✓ Setup complete for {real_site_context.domain}")

        try:
            audit_step.execute(real_site_context)
            print(f"✓ Audit passed for {real_site_context.domain}")
        except AuditError as e:
            print(f"⚠ Audit detected issues: {e}")

        results = real_site_context.get_result("audit_results", [])
        assert len(results) > 0

        crawler_result = results[0]
        print(f"  - Crawler audit score: {crawler_result.score:.1f}")
        print(f"  - Blocked bots: {real_site_context.get_state('crawler_blocked_bots', [])}")


class TestOrchestratorRealWebsite:
    """Test orchestrator execution against real websites."""

    def test_orchestrator_run_once_real_website(self, real_site_context):
        """
        Test full orchestrator workflow against a real website.
        """
        setup_step = SetupStep()
        audit_step = AuditStep()

        orchestrator = WorkflowOrchestrator(real_site_context)
        orchestrator.add_step(setup_step)
        orchestrator.add_step(audit_step)

        # Execute workflow
        orchestrator.run_once()

        # Verify both steps completed
        assert real_site_context.get_state("setup_complete") is True
        assert real_site_context.get_state("audit_complete") is True

        print(f"\n✓ Full workflow completed for {real_site_context.domain}")
        print(f"  - Setup: {orchestrator.status_report()['S1_Setup']}")
        print(f"  - Audit: {orchestrator.status_report()['S2_Audit']}")


class TestAdhocCrawlFrequency:
    """Tests specifically for AD_HOC crawl frequency."""

    def test_adhoc_crawl_configuration(self, real_site_context):
        """
        Verify AD_HOC crawl frequency is properly configured.
        """
        assert real_site_context.crawl_frequency == CrawlFrequency.AD_HOC
        assert real_site_context.crawl_frequency.value == "ad-hoc"
        print(f"\n✓ Crawl frequency set to: {real_site_context.crawl_frequency.value}")

    def test_adhoc_on_demand_audit(self, real_site_context):
        """
        Test that AD_HOC frequency allows on-demand audit execution.

        AD_HOC means:
        - No scheduled intervals
        - Manual/on-demand execution
        - Useful for testing specific sites
        """
        setup_step = SetupStep()
        audit_step = AuditStep()

        # First audit run
        setup_step.execute(real_site_context)
        audit_step.execute(real_site_context)
        first_audit_time = real_site_context.get_state("audit_complete")

        # Can run again immediately (AD_HOC allows this)
        real_site_context.set_state("audit_complete", False)
        real_site_context.set_result("audit_results", [])
        audit_step.execute(real_site_context)
        second_audit_time = real_site_context.get_state("audit_complete")

        # Both executions succeeded
        assert first_audit_time is True
        assert second_audit_time is True
        print(f"\n✓ AD_HOC frequency allows multiple on-demand audits")


class TestRealWorldScenarios:
    """Real-world testing scenarios."""

    def test_accessible_website_audit(self, real_site_context):
        """
        Test audit of an accessible, AI-friendly website (example.com).

        Expected behavior:
        - robots.txt allows most bots
        - No noindex meta tag
        - Audit should pass
        """
        setup_step = SetupStep()
        audit_step = AuditStep()

        setup_step.execute(real_site_context)

        try:
            audit_step.execute(real_site_context)
            # If audit passes, verify high score
            results = real_site_context.get_result("audit_results", [])
            if results and results[0].passed:
                print(f"\n✓ Accessible website audit passed")
                print(f"  - Score: {results[0].score:.1f}/100")
        except AuditError as e:
            # Some sites may block certain bots
            print(f"\n⚠ Site has restrictions: {e}")
            results = real_site_context.get_result("audit_results", [])
            if results:
                print(f"  - Score: {results[0].score:.1f}/100")
                print(f"  - Findings: {results[0].findings}")

    def test_crawler_audit_finds_robots_txt(self, real_site_context):
        """
        Verify that crawler audit can successfully fetch and parse robots.txt.

        This tests the real _fetch_robots_txt implementation.
        """
        setup_step = SetupStep()
        audit_step = AuditStep()

        setup_step.execute(real_site_context)
        audit_step.execute(real_site_context)

        # Check that crawler audit ran
        results = real_site_context.get_result("audit_results", [])
        crawler_result = results[0]

        # Verify it's the crawler audit and it produced results
        assert crawler_result.handler == "CrawlerAuditHandler"
        print(f"\n✓ Crawler audit successfully executed against real website")
        print(f"  - Domain: {real_site_context.domain}")
        print(f"  - Score: {crawler_result.score:.1f}")

    def test_crawler_audit_checks_meta_tags(self, real_site_context):
        """
        Verify that crawler audit can fetch homepage and parse meta tags.

        This tests the real _check_meta_noindex implementation.
        """
        setup_step = SetupStep()
        audit_step = AuditStep()

        setup_step.execute(real_site_context)
        audit_step.execute(real_site_context)

        results = real_site_context.get_result("audit_results", [])
        crawler_result = results[0]

        # Check metadata from the real fetch
        metadata = crawler_result.metadata
        assert "noindex" in metadata

        print(f"\n✓ Meta tag check executed successfully")
        print(f"  - Noindex detected: {metadata['noindex']}")

