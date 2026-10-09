"""In-memory receipt-token store for the approval flow.

FR (per the user's spec for this console): a POST approval returns 202 +
receipt token immediately; the UI polls the receipt and shows "Confirming..."
until terminal. Never render Confirmed on 202 — commit can take real time
(actual vendor-sim calls), so the HTTP response for the approval POST must
not itself carry the outcome.

Process-local and in-memory: this console is a single-process dev tool, not
a durable service — a restart loses in-flight receipts, which is acceptable
here (the underlying graph state itself IS durable, via the Postgres
checkpointer; only the "is this particular approval still being processed"
bookkeeping is not).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

ReceiptStatus = Literal["confirming", "succeeded", "failed"]


@dataclass
class Receipt:
    token: str
    status: ReceiptStatus = "confirming"
    result: dict[str, Any] | None = None
    error: str | None = None


class ReceiptStore:
    def __init__(self) -> None:
        self._receipts: dict[str, Receipt] = {}

    def create(self) -> Receipt:
        receipt = Receipt(token=uuid.uuid4().hex)
        self._receipts[receipt.token] = receipt
        return receipt

    def get(self, token: str) -> Receipt | None:
        return self._receipts.get(token)

    def mark_succeeded(self, token: str, result: dict[str, Any]) -> None:
        receipt = self._receipts.get(token)
        if receipt is not None:
            receipt.status = "succeeded"
            receipt.result = result

    def mark_failed(self, token: str, error: str) -> None:
        receipt = self._receipts.get(token)
        if receipt is not None:
            receipt.status = "failed"
            receipt.error = error


receipt_store = ReceiptStore()
