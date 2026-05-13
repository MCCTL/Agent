from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class Operation:
    operation_id: str
    label: str
    status: str
    created_at: datetime
    updated_at: datetime
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "label": self.label,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "result": self.result,
            "error": self.error,
        }


class OperationRegistry:
    def __init__(self) -> None:
        self._operations: dict[str, Operation] = {}

    def start(self, label: str, runner: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        operation_id = uuid.uuid4().hex
        operation = Operation(
            operation_id=operation_id,
            label=label,
            status="queued",
            created_at=now,
            updated_at=now,
        )
        self._operations[operation_id] = operation
        asyncio.create_task(self._run(operation, runner))
        return operation.to_dict()

    def get(self, operation_id: str) -> dict[str, Any]:
        operation = self._operations.get(operation_id)
        if operation is None:
            raise RuntimeError("Operation not found.")
        return operation.to_dict()

    async def _run(self, operation: Operation, runner: Callable[[], Awaitable[dict[str, Any]]]) -> None:
        operation.status = "running"
        operation.updated_at = datetime.now(timezone.utc)
        try:
            operation.result = await runner()
            operation.status = "success"
        except Exception as exc:
            operation.error = str(exc)
            operation.status = "failed"
        finally:
            operation.updated_at = datetime.now(timezone.utc)
