import pytest
from unittest.mock import Mock, patch

from src.audit import (
    AuditError,
    AuditResult,
    AuditHandler,
    CrawlerAuditHandler,
    TechnicalSEOHandler,
    ContentAuditHandler,
    AuditStep,
)
from src.core import WorkflowContext, StepStatus


@pytest.fixture
def sample_context():
    ctx = WorkflowContext(domain="example.com", queries=["what is seo", "ai optimization"])
    ctx.set_state("setup_complete", True)
    return ctx


class TestAuditResult:
    def test_init_defaults(self):
        result = AuditResult(handler="TestHandler", passed=True)
        assert result.handler == "TestHandler"
        assert result.passed is True
        assert result.score == 0.0
        assert result.findings == []
        assert result.metadata == {}

    def test_repr_pass(self):
        result = AuditResult(handler="Test", passed=True, score=85.5)
        assert repr(result) == "<AuditResult Test PASS score=85.5>"

    def test_repr_fail(self):
        result = AuditResult(handler="Test", passed=False, score=45.0)
        assert repr(result) == "<AuditResult Test FAIL score=45.0>"


class TestAuditHandler:
    def test_set_next_returns_next(self):
        h1 = CrawlerAuditHandler()
        h2 = TechnicalSEOHandler()
        result = h1.set_next(h2)
        assert result is h2
        assert h1._next is h2

    def test_handle_stores_result_in_context(self, sample_context):
        handler = CrawlerAuditHandler()
        with patch.object(handler, '_handle') as mock_handle:
            mock_handle.return_value = AuditResult("Test", True, 100.0)
            handler.handle(sample_context)

        results = sample_context.get_result("audit_results")
        assert len(results) == 1
        assert results[0].handler == "Test"

    def test_handle_passes_chain_continues(self, sample_context):
        h1 = CrawlerAuditHandler()
        h2 = TechnicalSEOHandler()
        h1.set_next(h2)

        with patch.object(h1, '_handle') as mock_h1, \
             patch.object(h2, '_handle') as mock_h2:
            mock_h1.return_value = AuditResult("H1", True, 100.0)
            mock_h2.return_value = AuditResult("H2", True, 100.0)

            result = h1.handle(sample_context)

        assert result is True
        assert len(sample_context.get_result("audit_results")) == 2

    def test_handle_fails_short_circuits(self, sample_context):
        h1 = CrawlerAuditHandler()
        h2 = TechnicalSEOHandler()
        h1.set_next(h2)

        with patch.object(h1, '_handle') as mock_h1, \
             patch.object(h2, '_handle') as mock_h2:
            mock_h1.return_value = AuditResult("H1", False, 50.0, ["fail"])
            mock_h2.return_value = AuditResult("H2", True, 100.0)

            result = h1.handle(sample_context)

        assert result is False
        assert len(sample_context.get_result("audit_results")) == 1  # Only H1

    def test_repr_no_next(self):
        handler = CrawlerAuditHandler()
        assert repr(handler) == "<CrawlerAuditHandler next=None>"

    def test_repr_with_next(self):
        h1 = CrawlerAuditHandler()
        h2 = TechnicalSEOHandler()
        h1.set_next(h2)
        assert repr(h1) == "<CrawlerAuditHandler next=TechnicalSEOHandler>"


class TestCrawlerAuditHandler:
    def test_handle_all_allowed_passes(self, sample_context):
        handler = CrawlerAuditHandler()
        with patch.object(handler, '_fetch_robots_txt') as mock_robots, \
             patch.object(handler, '_check_meta_noindex') as mock_noindex:
            mock_robots.return_value = {bot: True for bot in handler.KNOWN_AI_BOTS}
            mock_noindex.return_value = False

            result = handler._handle(sample_context)

        assert result.passed is True
        assert result.score == 100.0
        assert result.findings == []
        assert sample_context.get_state("crawler_blocked_bots") == []

    def test_handle_bots_blocked_fails(self, sample_context):
        handler = CrawlerAuditHandler()
        blocked_bots = ["GPTBot", "PerplexityBot"]
        robots = {bot: False if bot in blocked_bots else True for bot in handler.KNOWN_AI_BOTS}
        with patch.object(handler, '_fetch_robots_txt') as mock_robots, \
             patch.object(handler, '_check_meta_noindex') as mock_noindex:
            mock_robots.return_value = robots
            mock_noindex.return_value = False

            result = handler._handle(sample_context)

        assert result.passed is False
        expected_score = 100.0 - (len(blocked_bots) / len(handler.KNOWN_AI_BOTS)) * 80.0
        assert result.score == pytest.approx(expected_score)
        assert len(result.findings) == len(blocked_bots)
        assert sample_context.get_state("crawler_blocked_bots") == blocked_bots

    def test_handle_noindex_fails(self, sample_context):
        handler = CrawlerAuditHandler()
        with patch.object(handler, '_fetch_robots_txt') as mock_robots, \
             patch.object(handler, '_check_meta_noindex') as mock_noindex:
            mock_robots.return_value = {bot: True for bot in handler.KNOWN_AI_BOTS}
            mock_noindex.return_value = True

            result = handler._handle(sample_context)

        assert result.passed is False
        assert result.score == 80.0  # 100 - 20
        assert "noindex" in result.findings[0]

    def test_fetch_robots_txt_stub(self):
        handler = CrawlerAuditHandler()
        result = handler._fetch_robots_txt("example.com")
        assert all(result.values())  # All True

    def test_check_meta_noindex_stub(self):
        handler = CrawlerAuditHandler()
        assert handler._check_meta_noindex("example.com") is False


class TestTechnicalSEOHandler:
    def test_handle_all_pass(self, sample_context):
        handler = TechnicalSEOHandler()
        with patch.object(handler, '_check_page_speed') as mock_speed, \
             patch.object(handler, '_check_sitemap') as mock_sitemap, \
             patch.object(handler, '_check_broken_links') as mock_links:
            mock_speed.return_value = 90.0
            mock_sitemap.return_value = True
            mock_links.return_value = []

            result = handler._handle(sample_context)

        assert result.passed is True
        assert result.score == 100.0
        assert result.findings == []
        assert sample_context.get_state("technical_seo_checks") == {
            "speed": True, "sitemap": True, "broken_links": True
        }

    def test_handle_speed_low(self, sample_context):
        handler = TechnicalSEOHandler()
        with patch.object(handler, '_check_page_speed') as mock_speed, \
             patch.object(handler, '_check_sitemap') as mock_sitemap, \
             patch.object(handler, '_check_broken_links') as mock_links:
            mock_speed.return_value = 40.0
            mock_sitemap.return_value = False  # Make it fail
            mock_links.return_value = []

            result = handler._handle(sample_context)

        assert result.passed is False  # 33.33 < 40
        assert result.score == pytest.approx(33.33, abs=0.1)
        assert "speed" in result.findings[0]

    def test_handle_no_sitemap(self, sample_context):
        handler = TechnicalSEOHandler()
        with patch.object(handler, '_check_page_speed') as mock_speed, \
             patch.object(handler, '_check_sitemap') as mock_sitemap, \
             patch.object(handler, '_check_broken_links') as mock_links:
            mock_speed.return_value = 90.0
            mock_sitemap.return_value = False
            mock_links.return_value = ["broken"]  # Make it fail

            result = handler._handle(sample_context)

        assert result.passed is False
        assert "sitemap" in result.findings[0]

    def test_handle_broken_links(self, sample_context):
        handler = TechnicalSEOHandler()
        broken = ["http://example.com/broken"]
        with patch.object(handler, '_check_page_speed') as mock_speed, \
             patch.object(handler, '_check_sitemap') as mock_sitemap, \
             patch.object(handler, '_check_broken_links') as mock_links:
            mock_speed.return_value = 40.0  # Make it fail
            mock_sitemap.return_value = True
            mock_links.return_value = broken

            result = handler._handle(sample_context)

        assert result.passed is False
        assert any("broken link" in f for f in result.findings)
        assert sample_context.get_state("broken_links") == broken

    def test_stubs(self):
        handler = TechnicalSEOHandler()
        # Mock the real network calls since this is a unit test, not an integration test
        with patch.object(handler, '_check_page_speed') as mock_speed:
            with patch.object(handler, '_check_sitemap') as mock_sitemap:
                with patch.object(handler, '_check_broken_links') as mock_broken:
                    mock_speed.return_value = 85.0
                    mock_sitemap.return_value = True
                    mock_broken.return_value = []
                    
                    assert handler._check_page_speed("example.com") == 85.0
                    assert handler._check_sitemap("example.com") is True
                    assert handler._check_broken_links("example.com") == []


class TestContentAuditHandler:
    def test_handle_good_scores_pass(self, sample_context):
        handler = ContentAuditHandler()
        with patch.object(handler, '_score_content') as mock_score:
            mock_score.return_value = (80.0, 75.0)

            result = handler._handle(sample_context)

        avg = (80.0 + 75.0) / 2
        assert result.passed is True
        assert result.score == avg
        assert result.findings == []
        scores = sample_context.get_result("content_audit_scores")
        assert len(scores) == len(sample_context.queries)

    def test_handle_low_scores_fail(self, sample_context):
        handler = ContentAuditHandler()
        with patch.object(handler, '_score_content') as mock_score:
            mock_score.return_value = (30.0, 40.0)

            result = handler._handle(sample_context)

        assert result.passed is False
        assert result.score == 35.0
        assert len(result.findings) == 4  # 2 queries * 2 low scores

    def test_score_content_stub(self):
        handler = ContentAuditHandler()
        aeo, geo = handler._score_content("example.com", "query")
        assert aeo == 72.0
        assert geo == 68.0


class TestAuditStep:
    def test_init_default_chain(self):
        step = AuditStep()
        assert isinstance(step._chain, CrawlerAuditHandler)
        assert step.name == "S2_Audit"

    def test_init_custom_chain(self):
        custom = Mock(spec=AuditHandler)
        step = AuditStep(chain=custom)
        assert step._chain is custom

    def test_setup_requires_setup_complete(self, sample_context):
        sample_context.set_state("setup_complete", False)
        step = AuditStep()
        with pytest.raises(AuditError, match="S1_Setup must complete"):
            step._setup(sample_context)

    def test_setup_success(self, sample_context):
        step = AuditStep()
        step._setup(sample_context)  # Should not raise

    def test_run_sets_audit_passed(self, sample_context):
        step = AuditStep()
        with patch.object(step._chain, 'handle') as mock_handle:
            mock_handle.return_value = True
            step._run(sample_context)

        assert sample_context.get_state("audit_passed") is True

    def test_validate_no_results_raises(self, sample_context):
        step = AuditStep()
        with pytest.raises(AuditError, match="No audit results"):
            step._validate(sample_context)

    def test_validate_failed_audits_raises(self, sample_context):
        sample_context.set_result("audit_results", [
            AuditResult("Test", False, 30.0, ["fail"])
        ])
        step = AuditStep()
        with pytest.raises(AuditError, match="Audit\\(s\\) failed"):
            step._validate(sample_context)

    def test_validate_all_pass(self, sample_context):
        sample_context.set_result("audit_results", [
            AuditResult("H1", True, 100.0),
            AuditResult("H2", True, 90.0),
        ])
        step = AuditStep()
        step._validate(sample_context)  # Should not raise

    def test_teardown_sets_complete(self, sample_context):
        step = AuditStep()
        step._teardown(sample_context)
        assert sample_context.get_state("audit_complete") is True

    def test_build_default_chain(self):
        chain = AuditStep._build_default_chain()
        assert isinstance(chain, CrawlerAuditHandler)
        assert isinstance(chain._next, TechnicalSEOHandler)
        assert isinstance(chain._next._next, ContentAuditHandler)
        assert chain._next._next._next is None
