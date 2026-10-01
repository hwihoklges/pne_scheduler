"""Read-only values computed from a module's inputs.

Users ask "so what current is that actually, and how long will it run?" — the
answers are derived, never typed, so they belong next to the form as
non-editable rows rather than as fields somebody could contradict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..engine.duration import estimate_steps_duration
from ..engine.c_rate import c_rate_from_current_mA, current_mA_from_c_rate
from ..ir.cell_profile import CellProfile
from ..ir.numeric import finite_number
from ..ir.composer import compose_module_steps
from ..ir.project import ModuleNode
from . import units

Severity = Literal["info", "warning", "error"]


@dataclass(frozen=True, slots=True)
class DerivedValue:
    label: str
    text: str
    severity: Severity = "info"
    help: str = ""


def module_derived_values(
    module_type: str,
    params: dict[str, Any],
    *,
    cell: CellProfile,
    current_limit_mA: float | None = None,
) -> tuple[DerivedValue, ...]:
    """Step count, duration, and peak current for the current inputs."""
    try:
        steps = compose_module_steps(
            [ModuleNode("preview", module_type, dict(params))], cell, append_end=False
        )
    except (TypeError, ValueError) as exc:
        return (
            DerivedValue(
                "미리보기", f"현재 값으로는 스텝을 만들 수 없습니다 — {exc}", "error"
            ),
        )

    values: list[DerivedValue] = [
        DerivedValue("스텝 수", f"{len(steps)} 스텝", help="이 모듈이 만드는 장비 스텝 개수입니다."),
    ]

    estimate = estimate_steps_duration(steps, cell=cell)
    if steps:
        text = units.format_duration_ko(estimate.estimated_seconds)
        if not estimate.is_exact:
            text += " (근사)"
        if not estimate.is_complete:
            text += f" · incomplete / unknown {estimate.unknown_step_count}"
        values.append(
            DerivedValue(
                "예상 소요 시간", text,
                severity="warning" if not estimate.is_complete else "info",
                help="Configured/nominal subtotal, not elapsed time or a wall-clock ceiling. " + " ".join(estimate.warnings),
            )
        )

    try:
        if current_limit_mA is not None:
            current_limit_mA = finite_number(current_limit_mA, "current_limit_mA", positive=True)
        peak = _peak_current(steps, cell)
    except ValueError as exc:
        values.append(DerivedValue("최고 전류", str(exc), "error"))
        return tuple(values)
    if peak is not None:
        rate, current = peak
        text = f"{units.format_c_rate(rate)} = {units.format_current_mA(current)}"
        severity: Severity = "info"
        if current_limit_mA:
            ratio = current / current_limit_mA
            text += f" · 장비 한계 {units.format_current_mA(current_limit_mA)}의 {ratio * 100:.0f}%"
            if ratio > 1.0 + 1e-9:
                severity = "error"
            elif ratio > 0.9:
                severity = "warning"
        values.append(
            DerivedValue(
                "최고 전류", text, severity,
                help="이 모듈에서 가장 큰 충·방전 전류입니다. 장비 정격을 넘으면 내보낼 수 없습니다.",
            )
        )

    loops = [step.loop_count for step in steps if step.step_type == "loop" and step.loop_count]
    if loops:
        values.append(
            DerivedValue("반복 스텝", f"{len(loops)}개 · 최대 {max(loops):,}회")
        )
    return tuple(values)


def _peak_current(steps, cell: CellProfile) -> tuple[float, float] | None:
    cell.validate()
    best: tuple[float, float] | None = None
    for step in steps:
        if step.step_type not in {"charge", "discharge"}:
            continue
        step.validate()
        if step.current_mA is not None:
            current = step.current_mA
            rate = c_rate_from_current_mA(current, cell)
        elif step.c_rate is not None:
            rate = step.c_rate
            current = current_mA_from_c_rate(rate, cell)
        else:
            continue
        if best is None or current > best[1]:
            best = (rate, current)
    return best


__all__ = ["DerivedValue", "module_derived_values"]
