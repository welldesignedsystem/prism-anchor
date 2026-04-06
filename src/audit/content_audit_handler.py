from __future__ import annotations

import logging
import re
import urllib.request
from html.parser import HTMLParser

from src.core import WorkflowContext

from src.audit import AuditHandler
from src.audit import AuditResult

logger = logging.getLogger(__name__)


# ── HTML parser ────────────────────────────────────────────────────────────────

class _ContentParser(HTMLParser):
    """
    Extracts signals from a page's HTML needed for AEO/GEO scoring:
    - title, meta description
    - heading texts (h1–h3)
    - paragraph texts
    - presence of schema.org JSON-LD
    - presence of FAQ / HowTo markup
    """

    def __init__(self) -> None:
        super().__init__()
        self.title:        str        = ""
        self.description:  str        = ""
        self.headings:     list[str]  = []
        self.paragraphs:   list[str]  = []
        self.has_jsonld:   bool       = False
        self.has_faq:      bool       = False
        self.has_howto:    bool       = False
        self.word_count:   int        = 0

        self._in_title:    bool       = False
        self._in_heading:  bool       = False
        self._in_para:     bool       = False
        self._in_script:   bool       = False
        self._script_type: str        = ""
        self._buf:         list[str]  = []

    # ── tag open ──────────────────────────────────────────────────────────────

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)

        if tag == "title":
            self._in_title = True
            self._buf.clear()

        elif tag in {"h1", "h2", "h3"}:
            self._in_heading = True
            self._buf.clear()

        elif tag == "p":
            self._in_para = True
            self._buf.clear()

        elif tag == "meta":
            name    = (attr.get("name")    or "").lower()
            content =  attr.get("content") or ""
            if name == "description":
                self.description = content

        elif tag == "script":
            stype = (attr.get("type") or "").lower()
            if "json" in stype and "ld" in stype:
                self._in_script   = True
                self._script_type = "jsonld"
                self._buf.clear()
            else:
                self._in_script   = True
                self._script_type = "other"

    # ── tag close ─────────────────────────────────────────────────────────────

    def handle_endtag(self, tag: str) -> None:
        text = "".join(self._buf).strip()

        if tag == "title" and self._in_title:
            self.title    = text
            self._in_title = False
            self._buf.clear()

        elif tag in {"h1", "h2", "h3"} and self._in_heading:
            if text:
                self.headings.append(text)
            self._in_heading = False
            self._buf.clear()

        elif tag == "p" and self._in_para:
            if text:
                self.paragraphs.append(text)
                self.word_count += len(text.split())
            self._in_para = False
            self._buf.clear()

        elif tag == "script" and self._in_script:
            if self._script_type == "jsonld" and text:
                self.has_jsonld = True
                lower = text.lower()
                if "faqpage" in lower:
                    self.has_faq = True
                if "howto" in lower:
                    self.has_howto = True
            self._in_script   = False
            self._script_type = ""
            self._buf.clear()

    # ── data ──────────────────────────────────────────────────────────────────

    def handle_data(self, data: str) -> None:
        if self._in_title or self._in_heading or self._in_para or (
            self._in_script and self._script_type == "jsonld"
        ):
            self._buf.append(data)


# ── Concrete handler ───────────────────────────────────────────────────────────

class ContentAuditHandler(AuditHandler):
    """
    B3 — Content Audit.
    Scores each tracked query's landing page for AEO and GEO readiness.

    AEO (Answer Engine Optimisation) — signals that help a page win
    featured snippets, PAA boxes, and voice answers:
      • Direct question/answer structure in headings
      • FAQ / HowTo schema markup
      • Concise meta description
      • Sufficient word count
      • Query keyword presence in title + headings

    GEO (Generative Engine Optimisation) — signals that help a page
    get cited by AI-generated answers:
      • JSON-LD structured data
      • Paragraph depth (more citable passages)
      • Clear, descriptive title
      • Heading hierarchy (h1–h3 coverage)
      • Query keyword density in body text
    """

    PASS_THRESHOLD: float = 50.0

    def _handle(self, ctx: WorkflowContext) -> AuditResult:
        logger.info("[ContentAuditHandler] Running content audit for %r", ctx.domain)

        findings:    list[str]  = []
        page_scores: list[dict] = []

        for query in ctx.queries:
            aeo_score, geo_score = self._score_content(ctx.domain, query)
            page_scores.append({
                "query":     query,
                "aeo_score": aeo_score,
                "geo_score": geo_score,
            })
            if aeo_score < 50:
                findings.append(
                    f"Low AEO score ({aeo_score:.0f}) for query: {query!r}"
                )
            if geo_score < 50:
                findings.append(
                    f"Low GEO score ({geo_score:.0f}) for query: {query!r}"
                )

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

    # ── Scoring ────────────────────────────────────────────────────────────────

    def _score_content(self, domain: str, query: str) -> tuple[float, float]:
        """
        Fetches the domain homepage and scores it against the query.
        Returns (aeo_score, geo_score) each in range [0, 100].
        """
        parsed = self._fetch_and_parse(domain)
        if parsed is None:
            logger.warning(
                "[ContentAuditHandler] Could not fetch %s — returning zero scores",
                domain,
            )
            return 0.0, 0.0

        aeo = self._compute_aeo(parsed, query)
        geo = self._compute_geo(parsed, query)
        return aeo, geo

    def _compute_aeo(self, p: _ContentParser, query: str) -> float:
        """
        AEO score — answer-engine readiness.

        Signal                          Max pts
        ─────────────────────────────── ───────
        Meta description present             15
        Query keyword in title               20
        Query keyword in any heading         20
        FAQ schema present                   20
        HowTo schema present                 10
        Word count ≥ 300                     15
        ─────────────────────────────── ───────
        Total                               100
        """
        score    = 0.0
        keywords = self._keywords(query)

        if p.description:
            score += 15.0

        if self._text_contains_any(p.title, keywords):
            score += 20.0

        if any(self._text_contains_any(h, keywords) for h in p.headings):
            score += 20.0

        if p.has_faq:
            score += 20.0

        if p.has_howto:
            score += 10.0

        if p.word_count >= 300:
            score += 15.0
        elif p.word_count >= 150:
            score += 7.0

        return min(score, 100.0)

    def _compute_geo(self, p: _ContentParser, query: str) -> float:
        """
        GEO score — generative-engine citability.

        Signal                          Max pts
        ─────────────────────────────── ───────
        JSON-LD structured data present      25
        Title is descriptive (≥ 5 words)     15
        ≥ 3 headings present                 15
        ≥ 5 paragraphs present               15
        Query keyword in body paragraphs     20
        Word count ≥ 500                     10
        ─────────────────────────────── ───────
        Total                               100
        """
        score    = 0.0
        keywords = self._keywords(query)

        if p.has_jsonld:
            score += 25.0

        title_words = len(p.title.split())
        if title_words >= 5:
            score += 15.0
        elif title_words >= 3:
            score += 7.0

        heading_count = len(p.headings)
        if heading_count >= 3:
            score += 15.0
        elif heading_count >= 1:
            score += 7.0

        para_count = len(p.paragraphs)
        if para_count >= 5:
            score += 15.0
        elif para_count >= 2:
            score += 7.0

        body = " ".join(p.paragraphs).lower()
        if self._text_contains_any(body, keywords):
            score += 20.0

        if p.word_count >= 500:
            score += 10.0
        elif p.word_count >= 300:
            score += 5.0

        return min(score, 100.0)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _fetch_and_parse(self, domain: str) -> _ContentParser | None:
        url = f"https://{domain}/"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; AuditBot/1.0)"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read(131_072).decode("utf-8", errors="replace")
        except Exception:
            logger.warning(
                "[ContentAuditHandler] Failed to fetch %s", url
            )
            return None

        parser = _ContentParser()
        parser.feed(html)
        return parser

    @staticmethod
    def _keywords(query: str) -> list[str]:
        """Lowercased individual tokens from the query, stop-words removed."""
        stop = {
            "a", "an", "the", "and", "or", "in", "on", "at",
            "to", "for", "of", "is", "are", "was", "with",
        }
        return [
            w for w in re.findall(r"[a-z0-9]+", query.lower())
            if w not in stop
        ]

    @staticmethod
    def _text_contains_any(text: str, keywords: list[str]) -> bool:
        lower = text.lower()
        return any(kw in lower for kw in keywords)


# ── Example usage ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
    )

    test_cases = [
        {
            "domain":  "apacrelocation.com",
            "queries": [
                "corporate relocation services asia pacific",
                "expat relocation singapore",
                "international moving company",
            ],
        },
    ]

    handler = ContentAuditHandler()

    print("\n" + "=" * 80)
    print("ContentAuditHandler — Real Website Audit Example")
    print("=" * 80)

    for case in test_cases:
        domain  = case["domain"]
        queries = case["queries"]

        print(f"\n{'─' * 80}")
        print(f"Auditing: {domain}")
        print(f"Queries:  {queries}")
        print(f"{'─' * 80}")

        ctx    = WorkflowContext(domain=domain, queries=queries)
        result = handler._handle(ctx)

        print(f"\n✓ Handler:    {result.handler}")
        print(f"✓ Passed:     {result.passed}")
        print(f"✓ Score:      {result.score:.1f}/100")
        print(f"\nMetadata:")
        print(f"  • Avg AEO:  {result.metadata['avg_aeo_score']:.1f}")
        print(f"  • Avg GEO:  {result.metadata['avg_geo_score']:.1f}")

        print(f"\nPer-query breakdown:")
        for ps in result.metadata["page_scores"]:
            print(
                f"  {ps['query']!r:50s}  "
                f"AEO={ps['aeo_score']:.0f}  "
                f"GEO={ps['geo_score']:.0f}"
            )

        if result.findings:
            print(f"\nFindings ({len(result.findings)}):")
            for f in result.findings:
                print(f"  ⚠ {f}")
        else:
            print(f"\nFindings: None")

        print(f"\nResult JSON:")
        print(json.dumps({
            "handler":  result.handler,
            "passed":   result.passed,
            "score":    result.score,
            "findings": result.findings,
            "metadata": {
                "avg_aeo_score": result.metadata["avg_aeo_score"],
                "avg_geo_score": result.metadata["avg_geo_score"],
                "page_scores":   result.metadata["page_scores"],
            },
        }, indent=2))

    print(f"\n{'=' * 80}")
    print("Audit Complete")
    print(f"{'=' * 80}\n")