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

    def handle_endtag(self, tag: str) -> None:
        text = "".join(self._buf).strip()

        if tag == "title" and self._in_title:
            self.title     = text
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

    def handle_data(self, data: str) -> None:
        if self._in_title or self._in_heading or self._in_para or (
            self._in_script and self._script_type == "jsonld"
        ):
            self._buf.append(data)


# ── Score breakdown dataclass ──────────────────────────────────────────────────

class _ScoreBreakdown:
    """
    Holds per-signal scores and recommendations for a single query audit.
    """

    def __init__(self) -> None:
        self.aeo_signals: list[dict] = []   # {signal, earned, max, passed, fix}
        self.geo_signals: list[dict] = []   # {signal, earned, max, passed, fix}

    def add_aeo(
        self,
        signal:  str,
        earned:  float,
        max_pts: float,
        passed:  bool,
        fix:     str,
    ) -> None:
        self.aeo_signals.append({
            "signal": signal,
            "earned": earned,
            "max":    max_pts,
            "passed": passed,
            "fix":    fix if not passed else "",
        })

    def add_geo(
        self,
        signal:  str,
        earned:  float,
        max_pts: float,
        passed:  bool,
        fix:     str,
    ) -> None:
        self.geo_signals.append({
            "signal": signal,
            "earned": earned,
            "max":    max_pts,
            "passed": passed,
            "fix":    fix if not passed else "",
        })

    @property
    def aeo_score(self) -> float:
        return min(sum(s["earned"] for s in self.aeo_signals), 100.0)

    @property
    def geo_score(self) -> float:
        return min(sum(s["earned"] for s in self.geo_signals), 100.0)

    @property
    def aeo_recommendations(self) -> list[str]:
        return [s["fix"] for s in self.aeo_signals if s["fix"]]

    @property
    def geo_recommendations(self) -> list[str]:
        return [s["fix"] for s in self.geo_signals if s["fix"]]

    @property
    def aeo_missing_pts(self) -> float:
        return sum(s["max"] - s["earned"] for s in self.aeo_signals)

    @property
    def geo_missing_pts(self) -> float:
        return sum(s["max"] - s["earned"] for s in self.geo_signals)


# ── Concrete handler ───────────────────────────────────────────────────────────

class ContentAuditHandler(AuditHandler):
    """
    B3 — Content Audit.
    Scores each tracked query's landing page for AEO and GEO readiness,
    and produces per-signal recommendations to reach 100/100.

    AEO (Answer Engine Optimisation) — signals that help a page win
    featured snippets, PAA boxes, and voice answers.

    GEO (Generative Engine Optimisation) — signals that help a page
    get cited by AI-generated answers.
    """

    PASS_THRESHOLD: float = 50.0

    def _handle(self, ctx: WorkflowContext) -> AuditResult:
        logger.info("[ContentAuditHandler] Running content audit for %r", ctx.domain)

        findings:    list[str]  = []
        page_scores: list[dict] = []

        for query in ctx.queries:
            breakdown = self._score_content(ctx.domain, query)

            page_scores.append({
                "query":              query,
                "aeo_score":          breakdown.aeo_score,
                "geo_score":          breakdown.geo_score,
                "aeo_signals":        breakdown.aeo_signals,
                "geo_signals":        breakdown.geo_signals,
                "aeo_recommendations": breakdown.aeo_recommendations,
                "geo_recommendations": breakdown.geo_recommendations,
            })

            if breakdown.aeo_score < 50:
                findings.append(
                    f"Low AEO score ({breakdown.aeo_score:.0f}) for query: {query!r}"
                )
            if breakdown.geo_score < 50:
                findings.append(
                    f"Low GEO score ({breakdown.geo_score:.0f}) for query: {query!r}"
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

    def _score_content(self, domain: str, query: str) -> _ScoreBreakdown:
        """
        Fetches the domain homepage and scores it against the query.
        Returns a _ScoreBreakdown with per-signal detail and recommendations.
        """
        parsed = self._fetch_and_parse(domain)
        bd     = _ScoreBreakdown()

        if parsed is None:
            logger.warning(
                "[ContentAuditHandler] Could not fetch %s — returning zero scores",
                domain,
            )
            # Populate all signals as failed so recommendations are complete
            self._compute_aeo(bd, _ContentParser(), query)
            self._compute_geo(bd, _ContentParser(), query)
            return bd

        self._compute_aeo(bd, parsed, query)
        self._compute_geo(bd, parsed, query)
        return bd

    def _compute_aeo(
        self,
        bd:    _ScoreBreakdown,
        p:     _ContentParser,
        query: str,
    ) -> None:
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
        keywords = self._keywords(query)

        # Meta description
        has_desc = bool(p.description)
        bd.add_aeo(
            signal  = "Meta description present",
            earned  = 15.0 if has_desc else 0.0,
            max_pts = 15.0,
            passed  = has_desc,
            fix     = (
                "Add a <meta name=\"description\"> tag (150–160 chars) that "
                "directly answers what the page is about. Include primary keywords."
            ),
        )

        # Query keyword in title
        kw_in_title = self._text_contains_any(p.title, keywords)
        bd.add_aeo(
            signal  = "Query keyword in title",
            earned  = 20.0 if kw_in_title else 0.0,
            max_pts = 20.0,
            passed  = kw_in_title,
            fix     = (
                f"Include at least one query keyword ({', '.join(keywords[:3])}) "
                f"in the <title> tag. Current title: {p.title!r}"
            ),
        )

        # Query keyword in heading
        kw_in_heading = any(self._text_contains_any(h, keywords) for h in p.headings)
        bd.add_aeo(
            signal  = "Query keyword in any heading",
            earned  = 20.0 if kw_in_heading else 0.0,
            max_pts = 20.0,
            passed  = kw_in_heading,
            fix     = (
                f"Add an H1 or H2 that contains a query keyword "
                f"({', '.join(keywords[:3])}). "
                f"Current headings: {p.headings[:3]}"
            ),
        )

        # FAQ schema
        bd.add_aeo(
            signal  = "FAQ schema (FAQPage JSON-LD)",
            earned  = 20.0 if p.has_faq else 0.0,
            max_pts = 20.0,
            passed  = p.has_faq,
            fix     = (
                "Add FAQPage schema markup (JSON-LD) with at least 3 Q&A pairs "
                "relevant to the query. This directly targets PAA boxes and "
                "voice search answers."
            ),
        )

        # HowTo schema
        bd.add_aeo(
            signal  = "HowTo schema (HowTo JSON-LD)",
            earned  = 10.0 if p.has_howto else 0.0,
            max_pts = 10.0,
            passed  = p.has_howto,
            fix     = (
                "Add HowTo schema markup (JSON-LD) if the page describes a "
                "process or steps. Captures how-to rich results in AI engines."
            ),
        )

        # Word count
        if p.word_count >= 300:
            wc_earned, wc_passed = 15.0, True
        elif p.word_count >= 150:
            wc_earned, wc_passed = 7.0, False
        else:
            wc_earned, wc_passed = 0.0, False

        bd.add_aeo(
            signal  = f"Word count ≥ 300 (current: {p.word_count})",
            earned  = wc_earned,
            max_pts = 15.0,
            passed  = wc_passed,
            fix     = (
                f"Increase body content to at least 300 words (currently {p.word_count}). "
                "Aim for 500+ words with direct answers near the top of the page."
            ),
        )

    def _compute_geo(
        self,
        bd:    _ScoreBreakdown,
        p:     _ContentParser,
        query: str,
    ) -> None:
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
        keywords = self._keywords(query)

        # JSON-LD
        bd.add_geo(
            signal  = "JSON-LD structured data present",
            earned  = 25.0 if p.has_jsonld else 0.0,
            max_pts = 25.0,
            passed  = p.has_jsonld,
            fix     = (
                "Add JSON-LD structured data. Start with Organization or "
                "LocalBusiness schema, then add FAQPage or Article as appropriate. "
                "AI engines use structured data to verify and cite facts."
            ),
        )

        # Descriptive title
        title_words  = len(p.title.split())
        title_passed = title_words >= 5
        title_earned = 15.0 if title_words >= 5 else (7.0 if title_words >= 3 else 0.0)
        bd.add_geo(
            signal  = f"Descriptive title ≥ 5 words (current: {title_words} words)",
            earned  = title_earned,
            max_pts = 15.0,
            passed  = title_passed,
            fix     = (
                f"Expand the page title to at least 5 descriptive words. "
                f"Current title: {p.title!r}. "
                "A descriptive title helps AI engines understand and cite the page."
            ),
        )

        # Heading count
        heading_count  = len(p.headings)
        heading_passed = heading_count >= 3
        heading_earned = 15.0 if heading_count >= 3 else (7.0 if heading_count >= 1 else 0.0)
        bd.add_geo(
            signal  = f"≥ 3 headings (current: {heading_count})",
            earned  = heading_earned,
            max_pts = 15.0,
            passed  = heading_passed,
            fix     = (
                f"Add more H2/H3 headings to structure the content "
                f"(currently {heading_count}). "
                "Each heading should introduce a distinct subtopic or answer. "
                "AI engines use headings to extract citable sections."
            ),
        )

        # Paragraph count
        para_count  = len(p.paragraphs)
        para_passed = para_count >= 5
        para_earned = 15.0 if para_count >= 5 else (7.0 if para_count >= 2 else 0.0)
        bd.add_geo(
            signal  = f"≥ 5 paragraphs (current: {para_count})",
            earned  = para_earned,
            max_pts = 15.0,
            passed  = para_passed,
            fix     = (
                f"Add more paragraph-level content (currently {para_count} <p> tags). "
                "Each paragraph should make one clear, citable claim. "
                "AI engines pull citations from individual paragraphs."
            ),
        )

        # Keyword in body
        body      = " ".join(p.paragraphs).lower()
        kw_in_body = self._text_contains_any(body, keywords)
        bd.add_geo(
            signal  = "Query keyword in body paragraphs",
            earned  = 20.0 if kw_in_body else 0.0,
            max_pts = 20.0,
            passed  = kw_in_body,
            fix     = (
                f"Ensure body paragraphs contain query keywords "
                f"({', '.join(keywords[:3])}). "
                "Write 1–2 paragraphs that directly and explicitly answer the query."
            ),
        )

        # Word count
        if p.word_count >= 500:
            wc_earned, wc_passed = 10.0, True
        elif p.word_count >= 300:
            wc_earned, wc_passed = 5.0, False
        else:
            wc_earned, wc_passed = 0.0, False

        bd.add_geo(
            signal  = f"Word count ≥ 500 (current: {p.word_count})",
            earned  = wc_earned,
            max_pts = 10.0,
            passed  = wc_passed,
            fix     = (
                f"Increase body content to at least 500 words (currently {p.word_count}). "
                "Longer, substantive content gives AI engines more passages to cite."
            ),
        )

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
            logger.warning("[ContentAuditHandler] Failed to fetch %s", url)
            return None

        parser = _ContentParser()
        parser.feed(html)
        return parser

    @staticmethod
    def _keywords(query: str) -> list[str]:
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

        # ── Per-query breakdown ────────────────────────────────────────────────
        for ps in result.metadata["page_scores"]:
            print(f"\n{'━' * 80}")
            print(f"  Query: {ps['query']}")
            print(f"  AEO: {ps['aeo_score']:.0f}/100   GEO: {ps['geo_score']:.0f}/100")

            print(f"\n  AEO Signal Breakdown:")
            print(f"  {'Signal':<45}  {'Earned':>6}  {'Max':>5}  {'Status'}")
            print(f"  {'─' * 45}  {'─' * 6}  {'─' * 5}  {'─' * 6}")
            for sig in ps["aeo_signals"]:
                status = "✓" if sig["passed"] else "✗"
                print(
                    f"  {sig['signal']:<45}  "
                    f"{sig['earned']:>6.0f}  "
                    f"{sig['max']:>5.0f}  "
                    f"{status}"
                )

            print(f"\n  GEO Signal Breakdown:")
            print(f"  {'Signal':<45}  {'Earned':>6}  {'Max':>5}  {'Status'}")
            print(f"  {'─' * 45}  {'─' * 6}  {'─' * 5}  {'─' * 6}")
            for sig in ps["geo_signals"]:
                status = "✓" if sig["passed"] else "✗"
                print(
                    f"  {sig['signal']:<45}  "
                    f"{sig['earned']:>6.0f}  "
                    f"{sig['max']:>5.0f}  "
                    f"{status}"
                )

            if ps["aeo_recommendations"]:
                print(f"\n  AEO Recommendations to reach 100:")
                for i, rec in enumerate(ps["aeo_recommendations"], 1):
                    print(f"    {i}. {rec}")

            if ps["geo_recommendations"]:
                print(f"\n  GEO Recommendations to reach 100:")
                for i, rec in enumerate(ps["geo_recommendations"], 1):
                    print(f"    {i}. {rec}")

        # ── Findings ──────────────────────────────────────────────────────────
        if result.findings:
            print(f"\n{'─' * 80}")
            print(f"Findings ({len(result.findings)}):")
            for f in result.findings:
                print(f"  ⚠ {f}")

        # ── JSON ──────────────────────────────────────────────────────────────
        print(f"\n{'─' * 80}")
        print("Result JSON:")
        print(json.dumps({
            "handler":  result.handler,
            "passed":   result.passed,
            "score":    result.score,
            "findings": result.findings,
            "metadata": result.metadata,
        }, indent=2))

    print(f"\n{'=' * 80}")
    print("Audit Complete")
    print(f"{'=' * 80}\n")