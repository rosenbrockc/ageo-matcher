"""Bounty lifecycle workflow for durable orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

logger = logging.getLogger(__name__)

try:
    from temporalio import workflow as _workflow
    TEMPORAL_AVAILABLE = True
except ImportError:
    TEMPORAL_AVAILABLE = False

    class _WorkflowShim:
        class unsafe:
            @staticmethod
            def imports_passed_through():
                return contextlib.nullcontext()

        def defn(self, value):
            return value

        def run(self, value):
            return value

        def signal(self, value):
            return value

        def query(self, value):
            return value

        async def wait_condition(self, predicate, timeout=None):
            while not predicate():
                await asyncio.sleep(0.05)

        async def sleep(self, duration):
            seconds = duration.total_seconds() if hasattr(duration, "total_seconds") else float(duration)
            await asyncio.sleep(seconds)

        async def execute_activity(self, fn, *args, **kwargs):
            result = fn(*args)
            if inspect.isawaitable(result):
                return await result
            return result

    _workflow = _WorkflowShim()

workflow = _workflow

with workflow.unsafe.imports_passed_through():
    from sciona.workflows.bounty_activities import (
        ComputeSettlementInput,
        ExecutePayoutsInput,
        LaunchVerificationInput,
        RecordFundingInput,
        RecordSettlementInput,
        compute_settlement,
        execute_payouts,
        launch_verification,
        record_funding,
        record_settlement,
    )


@dataclass(frozen=True)
class BountyWorkflowInput:
    bounty_id: str
    escrow_amount: float
    principal_id: str = ""
    deadline_seconds: int = 0


@dataclass(frozen=True)
class FundSignal:
    stripe_payment_id: str


@dataclass(frozen=True)
class SubmitSignal:
    submission_id: str
    architect_id: str


@dataclass(frozen=True)
class VerifyCompleteSignal:
    submission_id: str
    passed: bool
    metric_values: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class SettleSignal:
    transfer_ids: list[str] = field(default_factory=list)


async def _call_activity(fn, *args):
    if _temporal_runtime_active():
        return await workflow.execute_activity(
            fn,
            *args,
            start_to_close_timeout=timedelta(minutes=5),
        )
    result = fn(*args)
    if inspect.isawaitable(result):
        return await result
    return result


def _temporal_runtime_active() -> bool:
    if not TEMPORAL_AVAILABLE:
        return False
    try:
        workflow.info()
    except Exception:
        return False
    return True


@workflow.defn
class BountyWorkflow:
    """Durable state container for a single bounty."""

    def __init__(self) -> None:
        self._input: BountyWorkflowInput | None = None
        self._status = "draft"
        self._funded = False
        self._cancelled = False
        self._submissions: list[dict[str, Any]] = []
        self._verified_submissions: list[dict[str, Any]] = []
        self._settlement_transfer_ids: list[str] = []
        self._terminal_status: str | None = None
        self._notify = asyncio.Event()

    @workflow.run
    async def run(self, input: BountyWorkflowInput) -> str:
        self._input = input
        temporal_runtime = _temporal_runtime_active()
        deadline_at: float | None = None
        if input.deadline_seconds > 0:
            deadline_at = asyncio.get_running_loop().time() + float(input.deadline_seconds)

        while self._terminal_status is None:
            if (
                deadline_at is not None
                and self._status in {"open", "submitted"}
                and asyncio.get_running_loop().time() >= deadline_at
            ):
                self._status = "expired"
                self._terminal_status = "expired"
                break

            if temporal_runtime:
                await workflow.wait_condition(
                    lambda: self._terminal_status is not None
                    or (
                        deadline_at is not None
                        and self._status in {"open", "submitted"}
                        and asyncio.get_running_loop().time() >= deadline_at
                    )
                )
                continue

            timeout = None
            if deadline_at is not None and self._status in {"open", "submitted"}:
                timeout = max(deadline_at - asyncio.get_running_loop().time(), 0.0)
            try:
                if timeout is None:
                    await self._notify.wait()
                else:
                    await asyncio.wait_for(self._notify.wait(), timeout=timeout)
                self._notify.clear()
            except asyncio.TimeoutError:
                self._status = "expired"
                self._terminal_status = "expired"

        return self._terminal_status or self._status

    @workflow.signal
    async def fund(self, stripe_payment_id: str) -> None:
        self._funded = True
        self._status = "open"
        if self._input is not None:
            await _call_activity(
                record_funding,
                RecordFundingInput(
                    bounty_id=self._input.bounty_id,
                    stripe_payment_id=stripe_payment_id,
                ),
            )
        self._notify.set()

    @workflow.signal
    async def submit(self, submission_id: str, architect_id: str) -> None:
        self._submissions.append(
            {
                "submission_id": submission_id,
                "architect_id": architect_id,
            }
        )
        self._status = "submitted"
        if self._input is not None:
            await _call_activity(
                launch_verification,
                LaunchVerificationInput(
                    bounty_id=self._input.bounty_id,
                    submission_id=submission_id,
                ),
            )
        self._notify.set()

    @workflow.signal
    async def cancel(self) -> None:
        self._cancelled = True
        self._status = "cancelled"
        self._terminal_status = "cancelled"
        self._notify.set()

    @workflow.signal
    async def verify_complete(
        self,
        submission_id: str,
        passed: bool,
        metric_values: dict[str, float] | None = None,
    ) -> None:
        metrics = metric_values or {}
        if passed:
            self._verified_submissions.append(
                {
                    "submission_id": submission_id,
                    "metric_values": metrics,
                }
            )
            self._status = "verified"
        else:
            self._status = "submitted"
        self._notify.set()

    @workflow.signal
    async def settle(self, transfer_ids: list[str] | None = None) -> None:
        provided_transfer_ids = list(transfer_ids or [])
        self._settlement_transfer_ids = list(provided_transfer_ids)
        self._status = "settled"
        self._terminal_status = "settled"
        if self._input is not None:
            plan_json = await _call_activity(
                compute_settlement,
                ComputeSettlementInput(
                    bounty_id=self._input.bounty_id,
                    escrow_amount=self._input.escrow_amount,
                    verified_submission_ids=[
                        item["submission_id"] for item in self._verified_submissions
                    ],
                ),
            )
            if isinstance(plan_json, str):
                returned_transfer_ids = await _call_activity(
                    execute_payouts,
                    ExecutePayoutsInput(
                        bounty_id=self._input.bounty_id,
                        payout_plan_json=plan_json,
                    ),
                )
                if isinstance(returned_transfer_ids, list) and returned_transfer_ids:
                    self._settlement_transfer_ids = [
                        str(item) for item in returned_transfer_ids
                    ]
            await _call_activity(
                record_settlement,
                RecordSettlementInput(
                    bounty_id=self._input.bounty_id,
                    transfer_ids=self._settlement_transfer_ids,
                ),
            )
        self._notify.set()

    @workflow.query
    def get_status(self) -> str:
        return self._status

    @workflow.query
    def get_submissions(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._submissions]

    @workflow.query
    def get_verified_submissions(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._verified_submissions]

    @workflow.query
    def get_settlement_transfers(self) -> list[str]:
        return list(self._settlement_transfer_ids)
