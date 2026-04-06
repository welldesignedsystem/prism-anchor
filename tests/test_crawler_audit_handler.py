import pytest
from unittest.mock import Mock, patch, MagicMock
from io import BytesIO
import urllib.error
import urllib.robotparser

from src.audit import CrawlerAuditHandler
from src.audit.crawler_audit_handler import _MetaTagParser
from src.core import WorkflowContext


@pytest.fixture
def sample_context():
    ctx = WorkflowContext(domain="example.com", queries=["test query"])
    return ctx


class TestMetaTagParser:
    """Tests for the _MetaTagParser helper class."""

    def test_noindex_with_robots_meta_tag(self):
        parser = _MetaTagParser()
        html = '<html><head><meta name="robots" content="noindex, nofollow"></head></html>'
        parser.feed(html)
        assert parser.noindex is True

    def test_noindex_with_googlebot_meta_tag(self):
        parser = _MetaTagParser()
        html = '<html><head><meta name="googlebot" content="noindex"></head></html>'
        parser.feed(html)
        assert parser.noindex is True

    def test_noindex_with_bingbot_meta_tag(self):
        parser = _MetaTagParser()
        html = '<html><head><meta name="bingbot" content="noindex"></head></html>'
        parser.feed(html)
        assert parser.noindex is True

    def test_noindex_case_insensitive(self):
        parser = _MetaTagParser()
        html = '<meta name="ROBOTS" content="NOINDEX">'
        parser.feed(html)
        assert parser.noindex is True

    def test_no_noindex_with_index(self):
        parser = _MetaTagParser()
        html = '<html><head><meta name="robots" content="index, follow"></head></html>'
        parser.feed(html)
        assert parser.noindex is False

    def test_no_noindex_with_other_tags(self):
        parser = _MetaTagParser()
        html = '<html><head><meta name="description" content="Test"><meta name="keywords" content="test"></head></html>'
        parser.feed(html)
        assert parser.noindex is False

    def test_empty_html(self):
        parser = _MetaTagParser()
        parser.feed("")
        assert parser.noindex is False

    def test_noindex_ignored_if_not_meta_tag(self):
        parser = _MetaTagParser()
        html = '<html><body>noindex</body></html>'
        parser.feed(html)
        assert parser.noindex is False

    def test_multiple_meta_tags_first_noindex(self):
        parser = _MetaTagParser()
        html = '<meta name="robots" content="noindex"><meta name="description" content="Test">'
        parser.feed(html)
        assert parser.noindex is True

    def test_multiple_meta_tags_later_noindex(self):
        parser = _MetaTagParser()
        html = '<meta name="description" content="Test"><meta name="robots" content="noindex">'
        parser.feed(html)
        assert parser.noindex is True

    def test_noindex_with_spaces_in_content(self):
        parser = _MetaTagParser()
        html = '<meta name="robots" content="noindex , follow">'
        parser.feed(html)
        assert parser.noindex is True


class TestCrawlerAuditHandlerFetchRobotsTxt:
    """Tests for _fetch_robots_txt method."""

    def test_fetch_robots_txt_all_bots_allowed(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class:
            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp
            # Simulate can_fetch returning True for all bots
            mock_rp.can_fetch.return_value = True

            result = handler._fetch_robots_txt("example.com")

        assert all(result.values())
        assert len(result) == len(handler.KNOWN_AI_BOTS)

    def test_fetch_robots_txt_some_bots_blocked(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class:
            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp
            # Simulate blocking GPTBot and PerplexityBot
            def can_fetch_side_effect(bot, url):
                return bot not in ["GPTBot", "PerplexityBot"]
            mock_rp.can_fetch.side_effect = can_fetch_side_effect

            result = handler._fetch_robots_txt("example.com")

        assert result["GPTBot"] is False
        assert result["PerplexityBot"] is False
        assert result["GoogleExtended"] is True

    def test_fetch_robots_txt_network_error_allows_all(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class:
            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp
            mock_rp.read.side_effect = urllib.error.URLError("Connection refused")

            result = handler._fetch_robots_txt("example.com")

        # On error, should allow all bots
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

    def test_fetch_robots_txt_uses_correct_url(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class:
            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp
            mock_rp.can_fetch.return_value = True

            handler._fetch_robots_txt("example.com")

        mock_rp_class.assert_called_once_with(url="https://example.com/robots.txt")

    def test_fetch_robots_txt_checks_root_path(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.robotparser.RobotFileParser') as mock_rp_class:
            mock_rp = Mock()
            mock_rp_class.return_value = mock_rp
            mock_rp.can_fetch.return_value = True

            handler._fetch_robots_txt("example.com")

        # Verify that can_fetch was called with "/" path for each bot
        calls = mock_rp.can_fetch.call_args_list
        for call in calls:
            bot_name, url = call[0]
            assert url == "https://example.com/"


class TestCrawlerAuditHandlerCheckMetaNoindex:
    """Tests for _check_meta_noindex method."""

    def test_check_meta_noindex_found(self):
        handler = CrawlerAuditHandler()
        html_with_noindex = b'<html><head><meta name="robots" content="noindex"></head></html>'

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = html_with_noindex
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            result = handler._check_meta_noindex("example.com")

        assert result is True

    def test_check_meta_noindex_not_found(self):
        handler = CrawlerAuditHandler()
        html_without_noindex = b'<html><head><meta name="description" content="Test"></head></html>'

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = html_without_noindex
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            result = handler._check_meta_noindex("example.com")

        assert result is False

    def test_check_meta_noindex_network_error(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

            result = handler._check_meta_noindex("example.com")

        assert result is False

    def test_check_meta_noindex_timeout(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("timeout")

            result = handler._check_meta_noindex("example.com")

        assert result is False

    def test_check_meta_noindex_uses_correct_url(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.request.urlopen') as mock_urlopen, \
             patch('urllib.request.Request') as mock_request_class:
            mock_response = MagicMock()
            mock_response.read.return_value = b'<html></html>'
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            handler._check_meta_noindex("example.com")

        mock_request_class.assert_called_once()
        call_args = mock_request_class.call_args
        assert call_args[0][0] == "https://example.com/"

    def test_check_meta_noindex_sets_user_agent(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.request.urlopen') as mock_urlopen, \
             patch('urllib.request.Request') as mock_request_class:
            mock_response = MagicMock()
            mock_response.read.return_value = b'<html></html>'
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            handler._check_meta_noindex("example.com")

        mock_request_class.assert_called_once()
        call_kwargs = mock_request_class.call_args[1]
        assert "User-Agent" in call_kwargs["headers"]
        assert "AuditBot" in call_kwargs["headers"]["User-Agent"]

    def test_check_meta_noindex_reads_limited_chunk(self):
        handler = CrawlerAuditHandler()
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b'<html></html>'
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            handler._check_meta_noindex("example.com")

        mock_response.read.assert_called_once_with(32_768)

    def test_check_meta_noindex_handles_non_utf8(self):
        handler = CrawlerAuditHandler()
        # Use latin-1 encoded content with non-UTF8 bytes
        html_latin1 = '<html><head><meta name="description" content="café"></head></html>'.encode('latin-1')

        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = html_latin1
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            result = handler._check_meta_noindex("example.com")

        # Should handle encoding gracefully
        assert result is False


class TestCrawlerAuditHandlerIntegration:
    """Integration tests for CrawlerAuditHandler."""

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

    def test_handle_noindex_fails(self, sample_context):
        handler = CrawlerAuditHandler()
        with patch.object(handler, '_fetch_robots_txt') as mock_robots, \
             patch.object(handler, '_check_meta_noindex') as mock_noindex:
            mock_robots.return_value = {bot: True for bot in handler.KNOWN_AI_BOTS}
            mock_noindex.return_value = True

            result = handler._handle(sample_context)

        assert result.passed is False
        assert result.score == 80.0
        assert "noindex" in result.findings[0]

    def test_handle_stores_blocked_bots_in_context(self, sample_context):
        handler = CrawlerAuditHandler()
        blocked_bots = ["GPTBot"]
        robots = {bot: False if bot in blocked_bots else True for bot in handler.KNOWN_AI_BOTS}

        with patch.object(handler, '_fetch_robots_txt') as mock_robots, \
             patch.object(handler, '_check_meta_noindex') as mock_noindex:
            mock_robots.return_value = robots
            mock_noindex.return_value = False

            result = handler._handle(sample_context)

        assert sample_context.get_state("crawler_blocked_bots") == blocked_bots

