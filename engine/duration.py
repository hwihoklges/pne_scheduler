"""Approximate schedule duration from version-independent StepIntent values."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from ..ir.cell_profile import CellProfile
from ..ir.numeric import finite_number, positive_integer
from ..ir.step_intent import StepIntent
from .c_rate import c_rate_from_current_mA

DurationKind = Literal["configured_timer", "nominal_cc", "control", "unknown"]
DurationStatus = Literal["configured", "nominal", "incomplete"]


@dataclass(frozen=True, slots=True)
class StepDurationEstimate:
    """Legacy numeric subtotal plus typed basis and model completeness.

    A finite seconds value may be only a configured timer or nominal CC part.
    It is never measured elapsed time or an assured wall-clock ceiling.
    """
    step_index: int
    seconds: float | None
    basis: str
    approximate: bool
    kind: DurationKind = "unknown"
    model_complete: bool = True


@dataclass(frozen=True, slots=True)
class DurationEstimate:
    """Backward-compatible subtotals, not an elapsed-time prediction.

    exact_seconds means configured arithmetic only. Incomplete models retain
    available subtotals; unknown_step_count includes unmodelled executions.
    """
    estimated_seconds: float
    exact_seconds: float
    approximate_seconds: float
    unknown_step_count: int
    steps: tuple[StepDurationEstimate, ...]
    warnings: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return self.unknown_step_count == 0 and all(
            item.model_complete and item.seconds is not None for item in self.steps
        )

    @property
    def is_exact(self) -> bool:
        return self.is_complete and not any(item.approximate for item in self.steps)

    @property
    def status(self) -> DurationStatus:
        return "incomplete" if not self.is_complete else "configured" if self.is_exact else "nominal"

    @property
    def elapsed_time_status(self) -> Literal["unknown"]:
        return "unknown"


def estimate_step_duration(
    step: StepIntent,
    *,
    step_index: int,
    cell: CellProfile | None = None,
) -> StepDurationEstimate:
    """Estimate configured/nominal arithmetic; invalid domains raise ValueError.

    Absolute current takes precedence. Without explicit nominal capacity its
    capacity duration is unknown, unless an explicit timer is available.
    """
    step.validate()
    if cell is not None:
        cell.validate()
    if step.step_type in {"cycle", "loop", "end"}:
        if step.step_type == "loop":
            if (step.loop_target_ref is not None or step.loop_count is None
                    or step.loop_goto_step is None or not 1 <= step.loop_goto_step < step_index):
                return StepDurationEstimate(step_index, None, "unresolved LOOP", True, "unknown", False)
        return StepDurationEstimate(step_index, 0.0, "control step", False, "control")

    taper = step.mode in {"CV", "CCCV"} or (step.step_type == "charge" and step.mode is None)
    if step.end_time_s is not None:
        has_competing_condition = any(
            value is not None
            for value in (
                step.end_voltage_v,
                step.end_capacity_fraction,
                step.cv_cutoff_c_rate,
                step.cv_cutoff_mA,
                step.dod_percent,
            )
        )
        return StepDurationEstimate(
            step_index,
            float(step.end_time_s),
            "configured end time (scope not verified as wall-clock ceiling)",
            has_competing_condition or taper,
            "configured_timer",
            not (has_competing_condition or taper),
        )

    if step.mode == "CV":
        return StepDurationEstimate(step_index, None, "unknown CV taper", True, "unknown", False)
    rate = step.c_rate
    if step.current_mA is not None:
        rate = c_rate_from_current_mA(step.current_mA, cell) if cell is not None else None
    if (
        step.step_type in {"charge", "discharge"}
        and rate is not None
    ):
        fraction = (
            float(step.end_capacity_fraction)
            if step.end_capacity_fraction is not None
            else 1.0
        )
        return StepDurationEstimate(
            step_index,
            finite_number(3600.0 * fraction / rate, "nominal CC seconds", nonnegative=True),
            "capacity fraction / C-rate"
            if step.end_capacity_fraction is not None
            else "nominal 100% capacity / C-rate",
            True,
            "nominal_cc",
            not taper and all(value is None for value in (
                step.end_voltage_v, step.cv_cutoff_c_rate, step.cv_cutoff_mA, step.dod_percent,
            )),
        )

    return StepDurationEstimate(
        step_index,
        None,
        "no time or C-rate duration model",
        True,
        "unknown",
        False,
    )


def estimate_steps_duration(steps: list[StepIntent], *, cell: CellProfile | None = None) -> DurationEstimate:
    """Aggregate independent simple loops; invalid/overlapping loops fail closed.

    Invalid inputs become unknown with warnings here (the single-step helper
    raises). Nested/crossing loops are deliberately not modelled.
    """
    warnings: list[str] = []
    items: list[StepDurationEstimate] = []
    for index, step in enumerate(steps, start=1):
        try:
            item = estimate_step_duration(step, step_index=index, cell=cell)
        except ValueError as exc:
            item = StepDurationEstimate(index, None, str(exc), True, "unknown", False)
        items.append(item)
        if not item.model_complete:
            warnings.append(f"Step {index}: incomplete duration model: {item.basis}.")
    estimates = tuple(items)
    exact = sum(
        estimate.seconds or 0.0
        for estimate in estimates
        if not estimate.approximate
    )
    approximate = sum(
        estimate.seconds or 0.0
        for estimate in estimates
        if estimate.approximate
    )
    unknown = sum(estimate.seconds is None or not estimate.model_complete for estimate in estimates)
    intervals: list[tuple[int, int]] = []

    for index, step in enumerate(steps):
        if step.step_type != "loop" or not estimates[index].model_complete:
            continue
        target = step.loop_goto_step
        intervals.append((int(target) - 1, index))

    for start, index in intervals:
        step = steps[index]
        if any((start, index) != other and max(start, other[0]) <= min(index, other[1])
               for other in intervals):
            warnings.append(f"Step {index + 1}: nested/crossing LOOP duration is unknown.")
            items[index] = replace(estimates[index], seconds=None, kind="unknown",
                                   approximate=True, model_complete=False,
                                   basis="nested/crossing LOOP")
            unknown += 1
            continue
        repeats = positive_integer(step.loop_count, "loop_count") - 1
        body = estimates[start:index]
        try:
            exact += repeats * sum(
                estimate.seconds or 0.0
                for estimate in body
                if not estimate.approximate
            )
            approximate += repeats * sum(
                estimate.seconds or 0.0
                for estimate in body
                if estimate.approximate
            )
        except OverflowError:
            exact = approximate = float("inf")
        unknown += repeats * sum(estimate.seconds is None or not estimate.model_complete for estimate in body)
        if repeats:
            warnings.append(
                f"Step {index + 1}: loop count {step.loop_count} is interpreted "
                "as total body executions."
            )

    if any(
        (step.mode in {"CV", "CCCV"} or (step.step_type == "charge" and step.mode is None))
        for step in steps
    ):
        warnings.append(
            "Unknown CV taper: pure CV has no CC model; CCCV nominal CC subtotals are incomplete."
        )
    if any(
        estimate.kind == "nominal_cc" and estimate.seconds is not None
        for estimate in estimates
    ):
        warnings.append(
            "C-rate estimates assume nominal usable capacity and exclude equipment overhead."
        )

    try:
        finite_number(exact + approximate, "duration subtotal", nonnegative=True)
    except ValueError:
        exact = approximate = 0.0
        unknown += 1
        warnings.append("Duration arithmetic overflow; subtotal unavailable.")
    warnings.append("Configured/nominal arithmetic is not measured elapsed time or a wall-clock guarantee.")
    return DurationEstimate(
        estimated_seconds=exact + approximate,
        exact_seconds=exact,
        approximate_seconds=approximate,
        unknown_step_count=unknown,
        steps=tuple(items),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def combine_duration_estimates(
    estimates: list[DurationEstimate],
) -> DurationEstimate:
    steps: list[StepDurationEstimate] = []
    warnings: list[str] = []
    offset = 0
    for estimate in estimates:
        steps.extend(
            StepDurationEstimate(
                step_index=item.step_index + offset,
                seconds=item.seconds,
                basis=item.basis,
                approximate=item.approximate,
                kind=item.kind,
                model_complete=item.model_complete,
            )
            for item in estimate.steps
        )
        offset += len(estimate.steps)
        warnings.extend(estimate.warnings)
    exact = sum(item.exact_seconds for item in estimates)
    approximate = sum(item.approximate_seconds for item in estimates)
    unknown = sum(item.unknown_step_count for item in estimates)
    try:
        finite_number(exact + approximate, "combined duration", nonnegative=True)
    except ValueError:
        exact = approximate = 0.0
        unknown += 1
        warnings.append("Combined duration arithmetic overflow; subtotal unavailable.")
    return DurationEstimate(
        estimated_seconds=exact + approximate,
        exact_seconds=exact,
        approximate_seconds=approximate,
        unknown_step_count=unknown,
        steps=tuple(steps),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def format_duration(seconds: float) -> str:
    """Format finite, nonnegative arithmetic seconds (zero remains valid)."""
    seconds = finite_number(seconds, "seconds", nonnegative=True)
    total = max(0, round(seconds))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)
