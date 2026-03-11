from dataclasses import dataclass, field
from threading import Lock


@dataclass
class HealthState:
    initializing: bool = True
    ready: bool = False
    startup_error: str | None = None
    startup_ms: int | None = None
    _lock: Lock = field(default_factory=Lock)

    def set_ready(self, startup_ms: int) -> None:
        with self._lock:
            self.initializing = False
            self.ready = True
            self.startup_ms = startup_ms
            self.startup_error = None

    def set_failed(self, error_message: str) -> None:
        with self._lock:
            self.initializing = False
            self.ready = False
            self.startup_error = error_message


health_state = HealthState()
