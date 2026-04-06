from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

from src.core import WorkflowContext

from .audit_result import AuditResult

logger = logging.getLogger(__name__)


# ── AuditHandler — Chain of Responsibility with short-circuit ──────────────────

class AuditHandler(ABC):
    """
    Chain of Responsibility base for Step 2.

    handle() returns True  → audit passed, chain continues.
    handle() returns False → audit failed, chain is SHORT-CIRCUITED.

    Each handler appends its AuditResult to ctx results under
    the key  "audit_results"  (a list).
    """

    def __init__(self) -> None:
        self._next: Optional[AuditHandler] = None

    def set_next(self, handler: AuditHandler) -> AuditHandler:
        self._next = handler
        return handler

    def handle(self, ctx: WorkflowContext) -> bool:
        result = self._handle(ctx)

        # Store result in context
        audit_results: list[AuditResult] = ctx.get_result("audit_results", [])
        audit_results.append(result)
        ctx.set_result("audit_results", audit_results)

        if not result.passed:
            logger.warning(
                "[%s] Audit FAILED (score=%.1f) — short-circuiting chain. Findings: %s",
                result.handler, result.score, result.findings,
            )
            return False

        logger.info(
            "[%s] Audit PASSED (score=%.1f)",
            result.handler, result.score,
        )

        if self._next:
            return self._next.handle(ctx)

        return True

    @abstractmethod
    def _handle(self, ctx: WorkflowContext) -> AuditResult: ...

    def __repr__(self) -> str:
        nxt = self._next.__class__.__name__ if self._next else None
        return f"<{self.__class__.__name__} next={nxt}>"
