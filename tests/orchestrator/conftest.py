"""Durable 컨텍스트 흉내. journey 생성기를 활동 스텁과 이벤트·타이머 각본으로 구동한다."""

from datetime import UTC, datetime

import pytest

START = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)  # KST 2026-11-02 00:00
RETRY = "RETRY"


class FakeTask:
    def __init__(self, kind, name=None, payload=None, retry=None):
        self.kind, self.name, self.input, self.retry = kind, name, payload, retry
        self.result = None
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeAny:
    def __init__(self, tasks):
        self.tasks = tasks


class FakeContext:
    def __init__(self, payload, activities, outcomes=(), start=START):
        self.payload = payload
        self.activities = activities
        self.outcomes = list(outcomes)
        self.current_utc_datetime = start
        self.calls: list[FakeTask] = []
        self.saved: list[dict] = []
        self.events: list[FakeTask] = []
        self.timers: list[FakeTask] = []
        self.custom_status = None

    def get_input(self):
        return self.payload

    def call_activity(self, name, payload):
        return FakeTask("activity", name, payload)

    def call_activity_with_retry(self, name, retry, payload):
        return FakeTask("activity", name, payload, retry)

    def wait_for_external_event(self, name):
        task = FakeTask("event", name)
        self.events.append(task)
        return task

    def create_timer(self, fire_at):
        task = FakeTask("timer", payload=fire_at)
        self.timers.append(task)
        return task

    def task_any(self, tasks):
        return FakeAny(tasks)

    def set_custom_status(self, status):
        self.custom_status = status

    def called(self, name):
        return [c for c in self.calls if c.name == name]


def drive(ctx):
    """생성기를 끝까지 돌린다. outcomes: ("event", payload) 또는 ("timer",)."""
    from journey.orchestration import journey

    gen = journey(ctx, RETRY)
    value, error = None, None
    while True:
        try:
            yielded = gen.throw(error) if error else gen.send(value)
        except StopIteration as stop:
            return stop.value
        value, error = None, None
        if isinstance(yielded, FakeTask):
            ctx.calls.append(yielded)
            if yielded.name == "save_document":
                ctx.saved.append(yielded.input["document"])
                continue
            try:
                value = ctx.activities[yielded.name](yielded.input)
            except Exception as e:  # noqa: BLE001
                error = e
        elif isinstance(yielded, FakeAny):
            event, timer = yielded.tasks
            assert event.kind == "event" and timer.kind == "timer"
            outcome = ctx.outcomes.pop(0)
            if outcome[0] == "event":
                event.result = outcome[1]
                value = event
            else:
                ctx.current_utc_datetime = timer.input
                value = timer
        else:
            raise AssertionError(f"unexpected yield {yielded!r}")


@pytest.fixture
def run_journey():
    def run(payload, activities, outcomes=()):
        ctx = FakeContext(payload, activities, outcomes)
        return drive(ctx), ctx

    return run
