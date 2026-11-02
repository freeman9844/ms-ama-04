"""프로세스 단위 서킷브레이커. 연속 실패 threshold번이면 open, cooldown 뒤 시험 1건(half_open)."""

import threading
import time
from collections.abc import Callable


class CircuitBreaker:
    def __init__(
        self,
        threshold: int = 3,
        cooldown_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        self.threshold, self.cooldown_s, self._clock, self._on_change = threshold, cooldown_s, clock, on_change
        self._lock = threading.Lock()
        self.state = "closed"
        self._failures = 0
        self._opened_at = 0.0
        self._trial = False

    def _set(self, state: str) -> None:
        if state == self.state:
            return
        self.state = state
        if self._on_change is not None:
            try:
                self._on_change(state)
            except Exception:  # noqa: BLE001 - 알림 실패가 호출을 막지 않는다
                pass

    def allow(self) -> bool:
        with self._lock:
            if self.state == "closed":
                return True
            if self.state == "open":
                if self._clock() - self._opened_at < self.cooldown_s:
                    return False
                self._set("half_open")
                self._trial = True
                return True
            if self._trial:
                return False
            self._trial = True
            return True

    def success(self) -> None:
        with self._lock:
            self._failures, self._trial = 0, False
            self._set("closed")

    def failure(self) -> None:
        with self._lock:
            self._trial = False
            self._failures += 1
            if self.state == "half_open" or self._failures >= self.threshold:
                self._opened_at = self._clock()
                self._set("open")
