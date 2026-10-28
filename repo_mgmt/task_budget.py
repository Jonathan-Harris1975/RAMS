"""One technical-recovery allowance across planning, reviews and council seats."""

from contextlib import contextmanager
from contextvars import ContextVar

_budget: ContextVar[dict[str, int] | None] = ContextVar("task_recovery_budget", default=None)


@contextmanager
def task_recovery_scope(limit: int = 2):
    if _budget.get() is not None:
        yield
        return
    token = _budget.set({"limit": limit, "used": 0})
    try:
        yield
    finally:
        _budget.reset(token)


def claim_recovery() -> None:
    budget = _budget.get()
    if budget is None:
        return
    if budget["used"] >= budget["limit"]:
        raise RuntimeError("Task recovery budget exhausted; manual review required")
    budget["used"] += 1


def recovery_budget_snapshot() -> dict[str, int] | None:
    budget = _budget.get()
    return dict(budget) if budget is not None else None
