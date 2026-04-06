from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

logger = logging.getLogger(__name__)


# ── Enums ──────────────────────────────────────────────────────────────────────

class StepStatus(Enum):
    PENDING   = auto()
    RUNNING   = auto()
    COMPLETED = auto()
    FAILED    = auto()
    SKIPPED   = auto()


class CrawlFrequency(Enum):
    WEEKLY      = "weekly"
    DAILY       = "daily"
    SIX_HOURLY  = "6h"
    HOURLY      = "1h"
    AD_HOC      = "ad-hoc"


class AIEngine(Enum):
    PERPLEXITY = "perplexity"
    CHATGPT    = "chatgpt"
    GEMINI     = "gemini"
    COPILOT    = "copilot"


# ── Custom exceptions ──────────────────────────────────────────────────────────

class StepSkippedError(Exception):
    """Raise inside _run() or _validate() to skip a step gracefully (not a failure)."""


class WorkflowAbortError(Exception):
    """Raise inside _run() or _validate() to stop the entire workflow immediately."""


# ── WorkflowContext ────────────────────────────────────────────────────────────

@dataclass
class WorkflowContext:
    """
    Shared data bag passed through every step.
    Steps read from and write to this object.
    """
    domain: str
    queries: list[str]
    engines: list[AIEngine]          = field(default_factory=list)
    crawl_frequency: CrawlFrequency  = CrawlFrequency.DAILY
    results: dict[str, Any]          = field(default_factory=dict)
    shared_state: dict[str, Any]     = field(default_factory=dict)

    def set_result(self, key: str, value: Any) -> None:
        self.results[key] = value

    def get_result(self, key: str, default: Any = None) -> Any:
        return self.results.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        self.shared_state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        return self.shared_state.get(key, default)

    def __repr__(self) -> str:
        return f"<WorkflowContext domain={self.domain!r} queries={len(self.queries)}>"


# ── StepBase — Template Method ─────────────────────────────────────────────────

class StepBase(ABC):
    """
    Template Method pattern.

    Fixed lifecycle skeleton defined in execute():
        _setup()    — optional pre-run hook
        _run()      — core logic (abstract, must implement)
        _validate() — post-run checks (abstract, must implement)
        _teardown() — optional cleanup, runs even on failure

    Subclasses implement _run() and _validate() only.
    """

    def __init__(self, name: str) -> None:
        self.name   = name
        self.status = StepStatus.PENDING

    # ── Template method — do NOT override ─────────────────────────────────────

    def execute(self, context: WorkflowContext) -> None:
        logger.info("[%s] Starting", self.name)
        self.status = StepStatus.RUNNING

        try:
            self._setup(context)
            self._run(context)
            self._validate(context)
            self.status = StepStatus.COMPLETED
            logger.info("[%s] Completed", self.name)

        except StepSkippedError as exc:
            self.status = StepStatus.SKIPPED
            logger.info("[%s] Skipped: %s", self.name, exc)

        except WorkflowAbortError:
            self.status = StepStatus.FAILED
            logger.error("[%s] Aborted workflow", self.name)
            raise

        except Exception as exc:
            self.status = StepStatus.FAILED
            logger.error("[%s] Failed: %s", self.name, exc)
            raise

        finally:
            self._teardown(context)

    # ── Hooks ──────────────────────────────────────────────────────────────────

    def _setup(self, context: WorkflowContext) -> None:
        """Optional pre-run setup. Override when needed."""

    @abstractmethod
    def _run(self, context: WorkflowContext) -> None:
        """Core step logic. Must be implemented by every subclass."""

    @abstractmethod
    def _validate(self, context: WorkflowContext) -> None:
        """Post-run validation. Must be implemented by every subclass."""

    def _teardown(self, context: WorkflowContext) -> None:
        """Optional cleanup. Runs even if the step fails. Override when needed."""

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} status={self.status.name}>"


# ── WorkflowOrchestrator ───────────────────────────────────────────────────────

class WorkflowOrchestrator:
    """
    Owns the ordered list of steps and drives execution.

    Two execution modes:
      run_once()  — runs all steps S1 through S7 exactly once.
      run_loop()  — runs setup steps (S1, S2) once, then loops
                    the monitoring steps (S3 onwards) continuously.

    Override _before_step() / _after_step() in a subclass for
    cross-cutting concerns like metrics, tracing, or rate limiting.
    """

    LOOP_START_INDEX: int = 2   # S3 is index 2; S1 and S2 run once

    def __init__(self, context: WorkflowContext) -> None:
        self.context = context
        self._steps: list[StepBase] = []

    # ── Step registration ──────────────────────────────────────────────────────

    def add_step(self, step: StepBase) -> WorkflowOrchestrator:
        """Register a step. Returns self for fluent chaining."""
        self._steps.append(step)
        return self

    @property
    def steps(self) -> list[StepBase]:
        return list(self._steps)

    # ── Execution ──────────────────────────────────────────────────────────────

    def run_once(self) -> None:
        """Run all registered steps once in order (S1 through S7)."""
        logger.info("Orchestrator: full run started (%d steps)", len(self._steps))
        for step in self._steps:
            self._execute_step(step)
        logger.info("Orchestrator: full run completed")

    def run_loop(self, max_iterations: int = 0) -> None:
        """
        Phase 1 — run setup steps (S1, S2) once.
        Phase 2 — loop monitoring steps (S3 onwards) until stopped.

        Args:
            max_iterations: Cap on loop cycles. 0 = run forever.
        """
        if not self._steps:
            raise RuntimeError("No steps registered.")

        setup_steps = self._steps[: self.LOOP_START_INDEX]
        loop_steps  = self._steps[self.LOOP_START_INDEX :]

        # Phase 1 — one-time setup
        logger.info("Orchestrator: setup phase (%d steps)", len(setup_steps))
        for step in setup_steps:
            self._execute_step(step)

        if not loop_steps:
            logger.warning("No loop steps registered; exiting after setup.")
            return

        # Phase 2 — continuous monitoring loop
        iteration = 0
        logger.info("Orchestrator: monitoring loop started")

        while True:
            iteration += 1
            logger.info("Orchestrator: loop iteration %d", iteration)

            for step in loop_steps:
                self._execute_step(step)

            if max_iterations and iteration >= max_iterations:
                logger.info(
                    "Orchestrator: reached max_iterations=%d, stopping",
                    max_iterations,
                )
                break

        logger.info("Orchestrator: monitoring loop ended after %d iteration(s)", iteration)

    def trigger_step(self, step: StepBase) -> None:
        """Execute a single step on demand (e.g. triggered by an external alert)."""
        if step not in self._steps:
            raise ValueError(f"Step {step!r} is not registered with this orchestrator.")
        self._execute_step(step)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _execute_step(self, step: StepBase) -> None:
        self._before_step(step)
        step.execute(self.context)
        self._after_step(step)

    # ── Override hooks ─────────────────────────────────────────────────────────

    def _before_step(self, step: StepBase) -> None:
        """Called before each step. Override for logging, metrics, tracing, etc."""

    def _after_step(self, step: StepBase) -> None:
        """Called after each step. Override for logging, metrics, tracing, etc."""

    # ── Introspection ──────────────────────────────────────────────────────────

    def status_report(self) -> dict[str, str]:
        return {step.name: step.status.name for step in self._steps}

    def __repr__(self) -> str:
        return (
            f"<WorkflowOrchestrator"
            f" domain={self.context.domain!r}"
            f" steps={len(self._steps)}>"
        )


