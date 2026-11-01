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


# 각본의 "event"/"withdraw"는 신청 여정 이벤트 이름의 별칭이다. 그 밖의 첫 원소는 이벤트 이름 그대로다.
_ALIASES = {"event": "officer_decision", "withdraw": "consent_withdrawn"}


def drive(ctx, orchestrator=None):
    """생성기를 끝까지 돌린다. outcomes: ("timer",) 또는 (이벤트 이름|별칭, payload)."""
    if orchestrator is None:
        from journey.orchestration import journey as orchestrator

    gen = orchestrator(ctx, RETRY)
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
            assert all(not t.cancelled for t in yielded.tasks), "취소된 태스크를 다시 기다림"
            outcome = ctx.outcomes.pop(0)
            if outcome[0] == "timer":
                (timer,) = [t for t in yielded.tasks if t.kind == "timer"]
                ctx.current_utc_datetime = timer.input
                value = timer
            else:
                name = _ALIASES.get(outcome[0], outcome[0])
                (event,) = [t for t in yielded.tasks if t.kind == "event" and t.name == name]
                assert event.result is None, "이미 완료된 이벤트 태스크를 다시 기다림"
                event.result = outcome[1]
                value = event
        else:
            raise AssertionError(f"unexpected yield {yielded!r}")


@pytest.fixture
def run_journey():
    def run(payload, activities, outcomes=()):
        ctx = FakeContext(payload, activities, outcomes)
        return drive(ctx), ctx

    return run


@pytest.fixture
def run_orchestrator():
    """신청 여정 밖의 오케스트레이터(동의 수명주기 등)를 같은 방식으로 구동한다."""

    def run(orchestrator, payload, activities, outcomes=()):
        ctx = FakeContext(payload, activities, outcomes)
        return drive(ctx, orchestrator), ctx

    return run
