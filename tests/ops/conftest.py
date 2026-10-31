import pytest


class FakeRunner:
    """az/azd 호출을 기록하는 가짜 runner.

    `azd env get-values`·`azd env set`은 self.env 사전으로 흉내 냅니다. 그 밖의 명령은
    on(접두어..., results=[...])로 등록한 결과를 순서대로 돌려주고(마지막 결과는 반복),
    결과가 예외 인스턴스면 raise 합니다. 등록되지 않은 명령은 빈 문자열을 돌려줍니다.
    """

    def __init__(self, env=None):
        self.env = dict(env or {})
        self.calls: list[list[str]] = []
        self._responses: list[tuple[tuple[str, ...], list]] = []

    def on(self, *prefix, results):
        self._responses.append((prefix, list(results)))
        return self

    def __call__(self, args, *, stream=False):
        args = list(args)
        self.calls.append(args)
        if args[:3] == ["azd", "env", "get-values"]:
            return "\n".join(f'{k}="{v}"' for k, v in self.env.items()) + "\n"
        if args[:3] == ["azd", "env", "set"]:
            self.env[args[3]] = args[4]
            return ""
        for prefix, queue in self._responses:
            if tuple(args[:len(prefix)]) == prefix:
                result = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(result, BaseException):
                    raise result
                return result
        return ""

    def commands(self, *prefix):
        return [c for c in self.calls if tuple(c[:len(prefix)]) == prefix]

    def index(self, *prefix, start=0):
        """접두어와 일치하는 start 이후 첫 호출의 위치."""
        for i, call in enumerate(self.calls[start:], start):
            if tuple(call[:len(prefix)]) == prefix:
                return i
        raise AssertionError(f"호출 없음: {prefix}")


@pytest.fixture
def fake_runner():
    return FakeRunner
