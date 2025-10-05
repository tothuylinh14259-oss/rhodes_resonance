from __future__ import annotations

from typing import Callable, List

from .events import Event, SequenceGenerator, utc_now

EventHandler = Callable[[Event], None]


class EventBus:
    """Simple synchronous event bus with monotonic sequence numbers."""

    def __init__(self) -> None:
        self._handlers: List[EventHandler] = []
        self._seq = SequenceGenerator()

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        """Register an event handler.

        Returns
        -------
        Callable[[], None]
            Function to unsubscribe the handler.
        """

        self._handlers.append(handler)

        def _unsubscribe() -> None:
            try:
                self._handlers.remove(handler)
            except ValueError:
                pass

        return _unsubscribe

    def publish(self, event: Event) -> Event:
        """Dispatch an event to all subscribers after validation."""

        event.assign_runtime_fields(self._seq.next(), utc_now())
        event.validate()
        errors: List[Exception] = []
        for handler in list(self._handlers):
            try:
                handler(event)
            except Exception as exc:  # pragma: no cover - logging handlers should not fail
                errors.append(exc)
        if errors:
            raise RuntimeError("One or more logging handlers failed") from errors[0]
        return event

    def clear(self) -> None:
        """Remove all handlers (useful for tests)."""

        self._handlers.clear()
