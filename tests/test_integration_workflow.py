"""
Integration tests for the complete workflow: Core → Setup → Audit (Crawler).

These tests verify that the workflow orchestrator can successfully execute
the setup and audit steps end-to-end, with mocked network calls to avoid
external dependencies.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import urllib.error

from src.core import (
    WorkflowContext,
    WorkflowOrchestrator,
    AIEngine,
    CrawlFrequency,
)
from src.setup import SetupStep, ConfigValidationError
from src.audit import AuditStep, AuditError


@pytest.fixture
def sample_context():
    """Sample context with proper engine setup."""
    ctx = WorkflowContext(
        domain="example.com",
        queries=["what is SEO", "AI optimization"],
    )
    # Must set engines for setup to pass
    ctx.engines = [AIEngine.CHATGPT]
    ctx.crawl_frequency = CrawlFrequency.WEEKLY
    return ctx


class TestEndToEndWorkflowIntegration:
    """End-to-end integration tests for Core → Setup → Audit workflow."""

    def test_workflow_setup_and_audit_success(self, sample_context):
        """
        Test complete workflow where setup succeeds and all audits pass.

        Flow:
        1. Create setup step and audit step
        2. Run SetupStep to validate and normalize configuration
        3. Run AuditStep with all checks passing
        4. Verify final state marks both complete
        """
        setup_step = SetupStep()
        audit_step = AuditStep()

        # Execute setup
        setup_step.execute(sample_context)
        assert sample_context.get_state("setup_complete") is True

        # Execute audit with mocked network calls
        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class, \
             patch('urllib.request.urlopen') as mock_urlopen:

            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp
            mock_rp.can_fetch.return_value = True

            mock_response = MagicMock()
            mock_response.read.return_value = b'<html></html>'
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            audit_step.execute(sample_context)

        assert sample_context.get_state("audit_complete") is True
        assert sample_context.get_state("audit_passed") is True

    def test_workflow_audit_requires_setup(self, sample_context):
        """
        Test that audit step requires setup to complete first.

        Verifies:
        - Audit raises AuditError if setup not marked complete
        - Proper dependency checking between steps
        """
        audit_step = AuditStep()

        # Audit should fail without setup complete
        with pytest.raises(AuditError, match="S1_Setup must complete"):
            audit_step.execute(sample_context)

    def test_workflow_with_real_crawler_all_allowed(self, sample_context):
        """
        Test full workflow with realistic crawler behavior mocked.

        Simulates:
        - robots.txt check allowing all bots
        - Homepage has no noindex meta tag
        - Audit passes with high score
        """
        setup_step = SetupStep()
        audit_step = AuditStep()

        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class, \
             patch('urllib.request.urlopen') as mock_urlopen:

            # Setup robots.txt to allow all bots
            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp
            mock_rp.can_fetch.return_value = True

            # Setup homepage with no noindex
            mock_response = MagicMock()
            mock_response.read.return_value = b'<html><head><title>Example</title></head></html>'
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            # Execute setup
            setup_step.execute(sample_context)
            assert sample_context.get_state("setup_complete") is True

            # Execute audit
            audit_step.execute(sample_context)
            assert sample_context.get_state("audit_passed") is True

            # Check audit results
            results = sample_context.get_result("audit_results", [])
            assert len(results) > 0
            crawler_result = results[0]
            assert crawler_result.handler == "CrawlerAuditHandler"
            assert crawler_result.passed is True
            assert crawler_result.score == 100.0

    def test_workflow_crawler_detects_blocked_bots(self, sample_context):
        """
        Test workflow where crawler audit detects blocked AI bots.

        Simulates:
        - robots.txt blocks GPTBot and PerplexityBot
        - Audit fails with specific blocked bot findings
        """
        setup_step = SetupStep()
        audit_step = AuditStep()

        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class, \
             patch('urllib.request.urlopen') as mock_urlopen:

            # Setup robots.txt to block GPTBot and PerplexityBot
            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp

            def can_fetch_side_effect(bot, url):
                return bot not in ["GPTBot", "PerplexityBot"]

            mock_rp.can_fetch.side_effect = can_fetch_side_effect

            # Setup homepage with no noindex
            mock_response = MagicMock()
            mock_response.read.return_value = b'<html></html>'
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            # Execute setup
            setup_step.execute(sample_context)

            # Execute audit - should fail
            with pytest.raises(AuditError) as exc_info:
                audit_step.execute(sample_context)

            # Verify blocked bots are detected
            assert "CrawlerAuditHandler" in str(exc_info.value)
            blocked = sample_context.get_state("crawler_blocked_bots")
            assert "GPTBot" in blocked
            assert "PerplexityBot" in blocked

    def test_workflow_crawler_detects_noindex(self, sample_context):
        """
        Test workflow where crawler audit detects noindex meta tag.

        Simulates:
        - robots.txt allows all bots
        - Homepage has noindex meta tag
        - Audit fails with noindex finding
        """
        setup_step = SetupStep()
        audit_step = AuditStep()

        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class, \
             patch('urllib.request.urlopen') as mock_urlopen:

            # Setup robots.txt to allow all bots
            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp
            mock_rp.can_fetch.return_value = True

            # Setup homepage WITH noindex
            html_with_noindex = b'<html><head><meta name="robots" content="noindex"></head></html>'
            mock_response = MagicMock()
            mock_response.read.return_value = html_with_noindex
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            # Execute setup
            setup_step.execute(sample_context)

            # Execute audit - should fail due to noindex
            with pytest.raises(AuditError) as exc_info:
                audit_step.execute(sample_context)

            # Verify noindex is detected
            assert "CrawlerAuditHandler" in str(exc_info.value)

    def test_workflow_network_error_graceful_fallback(self, sample_context):
        """
        Test workflow where network errors occur but audit gracefully continues.

        Simulates:
        - robots.txt fetch fails (network error) → fallback allows all
        - Homepage fetch fails (network error) → fallback no noindex
        - Audit passes because both fallbacks allow content
        """
        setup_step = SetupStep()
        audit_step = AuditStep()

        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class, \
             patch('urllib.request.urlopen') as mock_urlopen:

            # robots.txt fetch fails
            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp
            mock_rp.read.side_effect = urllib.error.URLError("Connection refused")

            # Homepage fetch fails
            mock_urlopen.side_effect = urllib.error.URLError("timeout")

            # Execute setup
            setup_step.execute(sample_context)

            # Execute audit - should still pass due to fallbacks
            audit_step.execute(sample_context)
            assert sample_context.get_state("audit_passed") is True

            # Verify fallback behavior resulted in allowed state
            results = sample_context.get_result("audit_results", [])
            crawler_result = results[0]
            assert crawler_result.passed is True
            assert crawler_result.score == 100.0


class TestOrchestratorExecution:
    """Tests for WorkflowOrchestrator execution with multiple steps."""

    def test_orchestrator_run_once_executes_all_steps(self, sample_context):
        """
        Test that orchestrator.run_once() executes both setup and audit steps.
        """
        setup_step = SetupStep()
        audit_step = AuditStep()

        orchestrator = WorkflowOrchestrator(sample_context)
        orchestrator.add_step(setup_step)
        orchestrator.add_step(audit_step)

        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class, \
             patch('urllib.request.urlopen') as mock_urlopen:

            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp
            mock_rp.can_fetch.return_value = True

            mock_response = MagicMock()
            mock_response.read.return_value = b'<html></html>'
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            # Execute workflow
            orchestrator.run_once()

            # Verify both steps completed
            assert sample_context.get_state("setup_complete") is True
            assert sample_context.get_state("audit_complete") is True

    def test_orchestrator_status_report(self, sample_context):
        """
        Test that orchestrator can report status of all steps.
        """
        setup_step = SetupStep()
        audit_step = AuditStep()

        orchestrator = WorkflowOrchestrator(sample_context)
        orchestrator.add_step(setup_step)
        orchestrator.add_step(audit_step)

        status = orchestrator.status_report()
        assert "S1_Setup" in status
        assert "S2_Audit" in status


class TestRealWorldScenarios:
    """Tests simulating real-world website scenarios."""

    def test_wordpress_site_audit(self, sample_context):
        """
        Simulate auditing a WordPress site with open robots.txt and no noindex.
        """
        sample_context.domain = "myblog.wordpress.com"

        setup_step = SetupStep()
        audit_step = AuditStep()

        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class, \
             patch('urllib.request.urlopen') as mock_urlopen:

            # WordPress with open robots
            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp
            mock_rp.can_fetch.return_value = True

            # Typical WordPress homepage
            wp_html = b'''<html>
                <head>
                    <title>My Blog</title>
                    <meta name="description" content="My WordPress Blog">
                    <meta name="viewport" content="width=device-width, initial-scale=1">
                </head>
                <body>
                    <h1>Welcome to My Blog</h1>
                </body>
            </html>'''

            mock_response = MagicMock()
            mock_response.read.return_value = wp_html
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            setup_step.execute(sample_context)
            audit_step.execute(sample_context)

            assert sample_context.get_state("audit_passed") is True
            results = sample_context.get_result("audit_results", [])
            assert results[0].passed is True

    def test_enterprise_site_with_ai_bot_restrictions(self, sample_context):
        """
        Simulate auditing an enterprise site that blocks AI bots.
        """
        sample_context.domain = "corp.example.com"

        setup_step = SetupStep()
        audit_step = AuditStep()

        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class, \
             patch('urllib.request.urlopen') as mock_urlopen:

            # Enterprise robots.txt blocking AI bots
            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp

            def enterprise_block(bot, url):
                # Block all AI bots but allow search engines
                ai_bots = ["GPTBot", "PerplexityBot", "anthropic-ai"]
                return bot not in ai_bots

            mock_rp.can_fetch.side_effect = enterprise_block

            # Enterprise site with robots meta tag
            corp_html = b'''<html>
                <head>
                    <meta name="robots" content="noindex, nofollow">
                </head>
            </html>'''

            mock_response = MagicMock()
            mock_response.read.return_value = corp_html
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            setup_step.execute(sample_context)

            # Should fail audit
            with pytest.raises(AuditError):
                audit_step.execute(sample_context)

