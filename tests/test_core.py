import pytest
from unittest.mock import MagicMock, patch
import logging

# Assuming src is in PYTHONPATH or installed; adjust import as needed
from src.core.core import (
    StepStatus, CrawlFrequency, AIEngine,
    StepSkippedError, WorkflowAbortError,
    WorkflowContext, StepBase, WorkflowOrchestrator,
)


class TestEnums:
    def test_step_status_values(self):
        assert StepStatus.PENDING.value == 1
        assert StepStatus.RUNNING.value == 2
        assert StepStatus.COMPLETED.value == 3
        assert StepStatus.FAILED.value == 4
        assert StepStatus.SKIPPED.value == 5

    def test_crawl_frequency_values(self):
        assert CrawlFrequency.WEEKLY.value == "weekly"
        assert CrawlFrequency.DAILY.value == "daily"
        assert CrawlFrequency.SIX_HOURLY.value == "6h"
        assert CrawlFrequency.HOURLY.value == "1h"

    def test_ai_engine_values(self):
        assert AIEngine.PERPLEXITY.value == "perplexity"
        assert AIEngine.CHATGPT.value == "chatgpt"
        assert AIEngine.GEMINI.value == "gemini"
        assert AIEngine.COPILOT.value == "copilot"


class TestExceptions:
    def test_step_skipped_error(self):
        exc = StepSkippedError("Test skip")
        assert str(exc) == "Test skip"

    def test_workflow_abort_error(self):
        exc = WorkflowAbortError("Test abort")
        assert str(exc) == "Test abort"


class TestWorkflowContext:
    def test_init_defaults(self):
        ctx = WorkflowContext(domain="test.com", queries=["q1"])
        assert ctx.domain == "test.com"
        assert ctx.queries == ["q1"]
        assert ctx.engines == []
        assert ctx.crawl_frequency == CrawlFrequency.DAILY
        assert ctx.results == {}
        assert ctx.shared_state == {}

    def test_set_get_result(self):
        ctx = WorkflowContext(domain="test.com", queries=[])
        ctx.set_result("key1", "value1")
        assert ctx.get_result("key1") == "value1"
        assert ctx.get_result("missing", "default") == "default"

    def test_set_get_state(self):
        ctx = WorkflowContext(domain="test.com", queries=[])
        ctx.set_state("key1", "value1")
        assert ctx.get_state("key1") == "value1"
        assert ctx.get_state("missing", "default") == "default"

    def test_repr(self):
        ctx = WorkflowContext(domain="test.com", queries=["q1", "q2"])
        assert repr(ctx) == "<WorkflowContext domain='test.com' queries=2>"


class TestStepBase:
    # Concrete subclass for testing
    class ConcreteStep(StepBase):
        def __init__(self, name: str):
            super().__init__(name)
            self.run_called = False
            self.validate_called = False
            self.setup_called = False
            self.teardown_called = False

        def _setup(self, context):
            self.setup_called = True

        def _run(self, context):
            self.run_called = True

        def _validate(self, context):
            self.validate_called = True

        def _teardown(self, context):
            self.teardown_called = True

    def test_init(self):
        step = self.ConcreteStep("test_step")
        assert step.name == "test_step"
        assert step.status == StepStatus.PENDING

    def test_execute_success(self):
        step = self.ConcreteStep("test_step")
        ctx = WorkflowContext(domain="test.com", queries=[])
        step.execute(ctx)
        assert step.status == StepStatus.COMPLETED
        assert step.setup_called
        assert step.run_called
        assert step.validate_called
        assert step.teardown_called

    def test_execute_skip(self):
        class SkipStep(StepBase):
            def _run(self, context):
                raise StepSkippedError("Skipping")

            def _validate(self, context):
                pass

        step = SkipStep("skip_step")
        ctx = WorkflowContext(domain="test.com", queries=[])
        step.execute(ctx)
        assert step.status == StepStatus.SKIPPED

    def test_execute_abort(self):
        class AbortStep(StepBase):
            def _run(self, context):
                raise WorkflowAbortError("Aborting")

            def _validate(self, context):
                pass

        step = AbortStep("abort_step")
        ctx = WorkflowContext(domain="test.com", queries=[])
        with pytest.raises(WorkflowAbortError):
            step.execute(ctx)
        assert step.status == StepStatus.FAILED

    def test_execute_failure(self):
        class FailStep(StepBase):
            def _run(self, context):
                raise ValueError("Test error")

            def _validate(self, context):
                pass

        step = FailStep("fail_step")
        ctx = WorkflowContext(domain="test.com", queries=[])
        with pytest.raises(ValueError):
            step.execute(ctx)
        assert step.status == StepStatus.FAILED

    def test_repr(self):
        step = self.ConcreteStep("test_step")
        assert repr(step) == "<ConcreteStep name='test_step' status=PENDING>"


class TestWorkflowOrchestrator:
    def test_init(self):
        ctx = WorkflowContext(domain="test.com", queries=[])
        orch = WorkflowOrchestrator(ctx)
        assert orch.context == ctx
        assert orch.steps == []

    def test_add_step(self):
        ctx = WorkflowContext(domain="test.com", queries=[])
        orch = WorkflowOrchestrator(ctx)
        step = MagicMock(spec=StepBase)
        step.name = "test"
        result = orch.add_step(step)
        assert result is orch  # Fluent interface
        assert orch.steps == [step]

    def test_run_once_no_steps(self):
        ctx = WorkflowContext(domain="test.com", queries=[])
        orch = WorkflowOrchestrator(ctx)
        orch.run_once()  # Should not raise

    def test_run_once_with_steps(self):
        ctx = WorkflowContext(domain="test.com", queries=[])
        orch = WorkflowOrchestrator(ctx)
        step = MagicMock(spec=StepBase)
        orch.add_step(step)
        orch.run_once()
        step.execute.assert_called_once_with(ctx)

    def test_run_loop_no_steps(self):
        ctx = WorkflowContext(domain="test.com", queries=[])
        orch = WorkflowOrchestrator(ctx)
        with pytest.raises(RuntimeError, match="No steps registered"):
            orch.run_loop()

    def test_run_loop_only_setup_steps(self):
        ctx = WorkflowContext(domain="test.com", queries=[])
        orch = WorkflowOrchestrator(ctx)
        step1 = MagicMock(spec=StepBase)
        step2 = MagicMock(spec=StepBase)
        orch.add_step(step1).add_step(step2)
        orch.run_loop(max_iterations=1)
        step1.execute.assert_called_once()
        step2.execute.assert_called_once()

    def test_run_loop_with_loop_steps(self):
        ctx = WorkflowContext(domain="test.com", queries=[])
        orch = WorkflowOrchestrator(ctx)
        setup1 = MagicMock(spec=StepBase)
        setup2 = MagicMock(spec=StepBase)
        loop1 = MagicMock(spec=StepBase)
        orch.add_step(setup1).add_step(setup2).add_step(loop1)
        orch.run_loop(max_iterations=2)
        setup1.execute.assert_called_once()
        setup2.execute.assert_called_once()
        assert loop1.execute.call_count == 2

    def test_trigger_step_registered(self):
        ctx = WorkflowContext(domain="test.com", queries=[])
        orch = WorkflowOrchestrator(ctx)
        step = MagicMock(spec=StepBase)
        orch.add_step(step)
        orch.trigger_step(step)
        step.execute.assert_called_once_with(ctx)

    def test_trigger_step_not_registered(self):
        ctx = WorkflowContext(domain="test.com", queries=[])
        orch = WorkflowOrchestrator(ctx)
        step = MagicMock(spec=StepBase)
        with pytest.raises(ValueError, match="not registered"):
            orch.trigger_step(step)

    def test_status_report(self):
        ctx = WorkflowContext(domain="test.com", queries=[])
        orch = WorkflowOrchestrator(ctx)
        step1 = MagicMock(spec=StepBase)
        step1.name = "step1"
        step1.status = StepStatus.COMPLETED
        step2 = MagicMock(spec=StepBase)
        step2.name = "step2"
        step2.status = StepStatus.FAILED
        orch.add_step(step1).add_step(step2)
        report = orch.status_report()
        assert report == {"step1": "COMPLETED", "step2": "FAILED"}

    def test_repr(self):
        ctx = WorkflowContext(domain="test.com", queries=[])
        orch = WorkflowOrchestrator(ctx)
        assert repr(orch) == "<WorkflowOrchestrator domain='test.com' steps=0>"

    @patch('src.core.core.logger')
    def test_before_after_hooks(self, mock_logger):
        ctx = WorkflowContext(domain="test.com", queries=[])
        orch = WorkflowOrchestrator(ctx)
        step = MagicMock(spec=StepBase)
        orch.add_step(step)
        orch.run_once()
        # _before_step and _after_step are no-ops by default, but can be overridden
        # Here we just ensure no errors


# Integration test
def test_full_workflow_example(caplog):
    with caplog.at_level(logging.INFO):
        # This is similar to the if __name__ == "__main__" block
        ctx = WorkflowContext(
            domain="example.com",
            queries=["best CRM software"],
            engines=[AIEngine.CHATGPT],
            crawl_frequency=CrawlFrequency.DAILY,
        )

        class TestStep(StepBase):
            def _run(self, context):
                context.set_result(self.name, f"{self.name} OK")

            def _validate(self, context):
                assert context.get_result(self.name) is not None

        orch = WorkflowOrchestrator(ctx)
        for i in range(1, 8):
            orch.add_step(TestStep(f"S{i}_Test"))

        orch.run_loop(max_iterations=1)

        report = orch.status_report()
        assert all(status == "COMPLETED" for status in report.values())

        # Check logs contain expected messages
        assert "Orchestrator: setup phase (2 steps)" in caplog.text
        assert "Orchestrator: monitoring loop started" in caplog.text
        assert "Orchestrator: loop iteration 1" in caplog.text
        assert "Orchestrator: monitoring loop ended after 1 iteration(s)" in caplog.text
