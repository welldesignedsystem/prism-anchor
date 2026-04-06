import pytest
from unittest.mock import Mock, patch, MagicMock
import urllib.error
import urllib.robotparser

from src.audit import CrawlerAuditHandler
from src.audit.crawler_audit_handler import _MetaTagParser
from src.core import WorkflowContext


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_context():
    ctx = WorkflowContext(domain="example.com", queries=["test query"])
    return ctx


# ── _MetaTagParser ─────────────────────────────────────────────────────────────
# Pure HTML parsing — no network, no mocks needed.

class TestMetaTagParser:

    def test_noindex_with_robots_meta_tag(self):
        parser = _MetaTagParser()
        parser.feed('<html><head><meta name="robots" content="noindex, nofollow"></head></html>')
        assert parser.noindex is True

    def test_noindex_with_googlebot_meta_tag(self):
        parser = _MetaTagParser()
        parser.feed('<html><head><meta name="googlebot" content="noindex"></head></html>')
        assert parser.noindex is True

    def test_noindex_with_bingbot_meta_tag(self):
        parser = _MetaTagParser()
        parser.feed('<html><head><meta name="bingbot" content="noindex"></head></html>')
        assert parser.noindex is True

    def test_noindex_case_insensitive(self):
        parser = _MetaTagParser()
        parser.feed('<meta name="ROBOTS" content="NOINDEX">')
        assert parser.noindex is True

    def test_no_noindex_with_index(self):
        parser = _MetaTagParser()
        parser.feed('<html><head><meta name="robots" content="index, follow"></head></html>')
        assert parser.noindex is False

    def test_no_noindex_with_other_tags(self):
        parser = _MetaTagParser()
        parser.feed('<html><head><meta name="description" content="Test"><meta name="keywords" content="test"></head></html>')
        assert parser.noindex is False

    def test_empty_html(self):
        parser = _MetaTagParser()
        parser.feed("")
        assert parser.noindex is False

    def test_noindex_ignored_if_not_meta_tag(self):
        parser = _MetaTagParser()
        parser.feed('<html><body>noindex</body></html>')
        assert parser.noindex is False

    def test_multiple_meta_tags_first_noindex(self):
        parser = _MetaTagParser()
        parser.feed('<meta name="robots" content="noindex"><meta name="description" content="Test">')
        assert parser.noindex is True

    def test_multiple_meta_tags_later_noindex(self):
        parser = _MetaTagParser()
        parser.feed('<meta name="description" content="Test"><meta name="robots" content="noindex">')
        assert parser.noindex is True

    def test_noindex_with_spaces_in_content(self):
        parser = _MetaTagParser()
        parser.feed('<meta name="robots" content="noindex , follow">')
        assert parser.noindex is True


# ── _fetch_robots_txt — live network ──────────────────────────────────────────

@pytest.mark.network
class TestCrawlerAuditHandlerFetchRobotsTxt:

    def test_fetch_robots_txt_returns_all_known_bots(self):
        # example.com has a permissive robots.txt — all bots should be allowed
        handler = CrawlerAuditHandler()
        result = handler._fetch_robots_txt("example.com")
        assert set(result.keys()) == set(handler.KNOWN_AI_BOTS)

    def test_fetch_robots_txt_returns_booleans(self):
        handler = CrawlerAuditHandler()
        result = handler._fetch_robots_txt("example.com")
        assert all(isinstance(v, bool) for v in result.values())

    def test_fetch_robots_txt_example_com_allows_all(self):
        # example.com does not block any crawlers
        handler = CrawlerAuditHandler()
        result = handler._fetch_robots_txt("example.com")
        assert all(result.values()), f"Unexpected blocks: {[k for k,v in result.items() if not v]}"

    def test_fetch_robots_txt_openai_blocks_gptbot(self):
        # openai.com blocks GPTBot (confirmed 2024)
        handler = CrawlerAuditHandler()
        result = handler._fetch_robots_txt("openai.com")
        assert set(result.keys()) == set(handler.KNOWN_AI_BOTS)
        assert result.get("GPTBot") is False, (
            "openai.com no longer blocks GPTBot — update this test"
        )

    def test_fetch_robots_txt_network_error_allows_all(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class:
            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp
            mock_rp.read.side_effect = urllib.error.URLError("Connection refused")
            result = handler._fetch_robots_txt("example.com")
        assert all(result.values())
        assert len(result) == len(handler.KNOWN_AI_BOTS)

    def test_fetch_robots_txt_parse_error_allows_all(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class:
            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp
            mock_rp.read.side_effect = Exception("Invalid robots.txt format")
            result = handler._fetch_robots_txt("example.com")
        assert all(result.values())

    def test_fetch_robots_txt_timeout_allows_all(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class:
            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp
            mock_rp.read.side_effect = urllib.error.URLError("timeout")
            result = handler._fetch_robots_txt("example.com")
        assert all(result.values())


# ── _check_meta_noindex — live network ────────────────────────────────────────

@pytest.mark.network
class TestCrawlerAuditHandlerCheckMetaNoindex:

    def test_check_meta_noindex_example_com_not_noindex(self):
        # example.com does not set noindex
        handler = CrawlerAuditHandler()
        result = handler._check_meta_noindex("example.com")
        assert result is False

    def test_check_meta_noindex_returns_bool(self):
        handler = CrawlerAuditHandler()
        result = handler._check_meta_noindex("example.com")
        assert isinstance(result, bool)

    def test_check_meta_noindex_network_error_returns_false(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
            result = handler._check_meta_noindex("example.com")
        assert result is False

    def test_check_meta_noindex_timeout_returns_false(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("timeout")
            result = handler._check_meta_noindex("example.com")
        assert result is False

    def test_check_meta_noindex_reads_limited_chunk(self):
        # Verify the 32 KB read cap is respected (mock only the I/O boundary)
        handler = CrawlerAuditHandler()
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b'<html></html>'
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response
            handler._check_meta_noindex("example.com")
        mock_response.read.assert_called_once_with(32_768)

    def test_check_meta_noindex_sets_user_agent(self):
        # Verify AuditBot user-agent is sent (mock only the I/O boundary)
        handler = CrawlerAuditHandler()
        with patch('urllib.request.urlopen') as mock_urlopen, \
             patch('urllib.request.Request') as mock_request_class:
            mock_response = MagicMock()
            mock_response.read.return_value = b'<html></html>'
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response
            handler._check_meta_noindex("example.com")
        call_kwargs = mock_request_class.call_args[1]
        assert "User-Agent" in call_kwargs["headers"]
        assert "AuditBot" in call_kwargs["headers"]["User-Agent"]

    def test_check_meta_noindex_handles_non_utf8(self):
        handler = CrawlerAuditHandler()
        html_latin1 = '<html><head><meta name="description" content="café"></head></html>'.encode('latin-1')
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = html_latin1
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response
            result = handler._check_meta_noindex("example.com")
        assert result is False


# ── Integration — full live run ────────────────────────────────────────────────

@pytest.mark.network
class TestCrawlerAuditHandlerIntegration:

    def test_handle_example_com_passes(self):
        # example.com has no AI bot restrictions and no noindex
        handler = CrawlerAuditHandler()
        ctx = WorkflowContext(domain="example.com", queries=["test query"])
        result = handler._handle(ctx)
        assert result.passed is True
        assert result.score == 100.0
        assert result.findings == []

    def test_handle_openai_com_fails(self):
        # openai.com blocks GPTBot — audit should fail with a reduced score
        handler = CrawlerAuditHandler()
        ctx = WorkflowContext(domain="openai.com", queries=["test query"])
        result = handler._handle(ctx)
        assert result.passed is False
        assert result.score < 100.0
        assert any("GPTBot" in f for f in result.findings)

    def test_handle_result_structure(self):
        handler = CrawlerAuditHandler()
        ctx = WorkflowContext(domain="example.com", queries=["test query"])
        result = handler._handle(ctx)
        assert result.handler == "CrawlerAuditHandler"
        assert isinstance(result.passed, bool)
        assert 0.0 <= result.score <= 100.0
        assert isinstance(result.findings, list)
        assert "blocked_bots" in result.metadata
        assert "noindex" in result.metadata

    def test_handle_stores_blocked_bots_in_context(self):
        handler = CrawlerAuditHandler()
        ctx = WorkflowContext(domain="openai.com", queries=["test query"])
        handler._handle(ctx)
        blocked = ctx.get_state("crawler_blocked_bots")
        assert isinstance(blocked, list)
        assert "GPTBot" in blocked

    def test_handle_score_formula(self):
        # Verify the scoring formula holds against a real domain
        handler = CrawlerAuditHandler()
        ctx = WorkflowContext(domain="openai.com", queries=["test query"])
        result = handler._handle(ctx)
        blocked_count = len(result.metadata["blocked_bots"])
        expected = 100.0 - (blocked_count / len(handler.KNOWN_AI_BOTS)) * 80.0
        if result.metadata["noindex"]:
            expected -= 20.0
        assert result.score == pytest.approx(max(0.0, expected))

    def test_handle_score_never_below_zero(self):
        handler = CrawlerAuditHandler()
        ctx = WorkflowContext(domain="example.com", queries=["test query"])
        result = handler._handle(ctx)
        assert result.score >= 0.0