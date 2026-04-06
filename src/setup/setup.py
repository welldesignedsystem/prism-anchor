from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Optional

from src.core.core import WorkflowContext, StepBase, AIEngine, CrawlFrequency

logger = logging.getLogger(__name__)


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ConfigValidationError(Exception):
    """Raised by a ConfigHandler when its input is invalid."""


# ── ConfigHandler — Chain of Responsibility ────────────────────────────────────

class ConfigHandler(ABC):

    def __init__(self) -> None:
        self._next: Optional[ConfigHandler] = None

    def set_next(self, handler: ConfigHandler) -> ConfigHandler:
        self._next = handler
        return handler

    def handle(self, ctx: WorkflowContext) -> None:
        self._handle(ctx)
        if self._next:
            self._next.handle(ctx)

    @abstractmethod
    def _handle(self, ctx: WorkflowContext) -> None: ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} next={self._next.__class__.__name__ if self._next else None}>"


# ── Concrete handlers ──────────────────────────────────────────────────────────

class DomainHandler(ConfigHandler):
    _STRIP_SCHEME = re.compile(r"^https?://", re.IGNORECASE)

    def _handle(self, ctx: WorkflowContext) -> None:
        raw = ctx.domain.strip()
        if not raw:
            raise ConfigValidationError("Domain must not be empty.")

        normalised = self._STRIP_SCHEME.sub("", raw).rstrip("/").lower()
        if "." not in normalised:
            raise ConfigValidationError(
                f"Domain {normalised!r} does not look valid (no TLD found)."
            )

        ctx.domain = normalised
        logger.info("[DomainHandler] Domain set to %r", ctx.domain)


class QueryHandler(ConfigHandler):
    MAX_QUERIES: int = 100
    MAX_LENGTH: int = 200

    def _handle(self, ctx: WorkflowContext) -> None:
        if not ctx.queries:
            raise ConfigValidationError("At least one tracked query is required.")

        cleaned: list[str] = []
        seen: set[str] = set()

        for raw in ctx.queries:
            q = raw.strip()
            if not q:
                continue
            if len(q) > self.MAX_LENGTH:
                raise ConfigValidationError(
                    f"Query exceeds {self.MAX_LENGTH} characters: {q[:40]!r}…"
                )
            lower = q.lower()
            if lower not in seen:
                seen.add(lower)
                cleaned.append(q)

        if not cleaned:
            raise ConfigValidationError("No valid queries after cleaning.")
        if len(cleaned) > self.MAX_QUERIES:
            raise ConfigValidationError(
                f"Too many queries: {len(cleaned)} (max {self.MAX_QUERIES})."
            )

        ctx.queries = cleaned
        logger.info("[QueryHandler] %d queries registered", len(ctx.queries))


class EngineHandler(ConfigHandler):

    def _handle(self, ctx: WorkflowContext) -> None:
        if not ctx.engines:
            raise ConfigValidationError(
                f"At least one AI engine must be selected. "
                f"Valid options: {[e.value for e in AIEngine]}"
            )

        resolved: list[AIEngine] = []
        seen: set[AIEngine] = set()

        for item in ctx.engines:
            engine = self._resolve(item)
            if engine not in seen:
                seen.add(engine)
                resolved.append(engine)

        ctx.engines = resolved
        logger.info("[EngineHandler] Engines: %s", [e.value for e in ctx.engines])

    @staticmethod
    def _resolve(item: AIEngine | str) -> AIEngine:
        if isinstance(item, AIEngine):
            return item
        try:
            return AIEngine(item.lower())
        except ValueError:
            raise ConfigValidationError(
                f"Unknown engine {item!r}. Valid options: {[e.value for e in AIEngine]}"
            )


class FrequencyHandler(ConfigHandler):

    def _handle(self, ctx: WorkflowContext) -> None:
        freq = ctx.crawl_frequency
        if isinstance(freq, CrawlFrequency):
            logger.info("[FrequencyHandler] Crawl frequency: %s", freq.value)
            return
        if isinstance(freq, str):
            try:
                ctx.crawl_frequency = CrawlFrequency(freq.lower())
                logger.info("[FrequencyHandler] Crawl frequency resolved: %s", ctx.crawl_frequency.value)
                return
            except ValueError:
                pass
        raise ConfigValidationError(
            f"Invalid crawl frequency {freq!r}. "
            f"Valid options: {[f.value for f in CrawlFrequency]}"
        )


# ── SetupStep ──────────────────────────────────────────────────────────────────

class SetupStep(StepBase):

    def __init__(self, chain: ConfigHandler | None = None) -> None:
        super().__init__(name="S1_Setup")
        self._chain = chain or self._build_default_chain()

    def _setup(self, context: WorkflowContext) -> None:
        logger.info("[SetupStep] Validating project configuration for %r", context.domain)

    def _run(self, context: WorkflowContext) -> None:
        self._chain.handle(context)

    def _validate(self, context: WorkflowContext) -> None:
        if not context.domain:
            raise ConfigValidationError("domain is empty after setup.")
        if not context.queries:
            raise ConfigValidationError("queries is empty after setup.")
        if not context.engines:
            raise ConfigValidationError("engines is empty after setup.")
        logger.info(
            "[SetupStep] Validation passed — domain=%r, queries=%d, engines=%d",
            context.domain, len(context.queries), len(context.engines),
        )

    def _teardown(self, context: WorkflowContext) -> None:
        context.set_state("setup_complete", True)

    @staticmethod
    def _build_default_chain() -> ConfigHandler:
        domain = DomainHandler()
        query = QueryHandler()
        engine = EngineHandler()
        frequency = FrequencyHandler()
        domain.set_next(query).set_next(engine).set_next(frequency)
        return domain
