"""User-facing schedule step intent (C-rate based)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from .numeric import finite_number, positive_integer

StepKind = Literal[
    "charge",
    "discharge",
    "rest",
    "ocv",
    "impedance",
    "pattern",
    "balance",
    "cycle",
    "loop",
    "end",
]
StepModeName = Literal["CCCV", "CC", "CV"]


@dataclass
class StepIntent:
    step_type: StepKind
    mode: StepModeName | None = None
    label: str = ""
    # Stable, fragment-local identity used by the schedule composer.  Binary
    # step numbers are resolved only after every module has been ordered.
    ref_id: str | None = None
    c_rate: float | None = None
    cv_cutoff_c_rate: float | None = None
    # Absolute overrides (preferred over C-rate when both are set).
    current_mA: float | None = None
    cv_cutoff_mA: float | None = None
    voltage_v: float | None = None
    end_voltage_v: float | None = None
    end_time_s: float | None = None
    end_capacity_fraction: float | None = None
    # SOC / capacity-reference (Ensol dod_percent @+384).
    dod_percent: float | None = None
    # Sampling (Ensol record_dV_mV @+332, record_time_s @+340).
    record_time_s: float | None = None
    record_dV_mV: float | None = None
    # LOOP (Gate B: loop_target@48 + loop_count@52; Ensol also writes @564).
    loop_goto_step: int | None = None
    loop_target_ref: str | None = None
    loop_count: int | None = None
    loop_reset_capacity: bool = False
    # Legacy ASSB SOC reference step (offset unresolved vs Ensol map).
    goto_step_id: int | None = None
    # DC-IR window — kept on IR; binary packing deferred (Excel≠Ensol offsets).
    dcr_start_s: float | None = None
    dcr_end_s: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate numeric domains at use boundaries, retaining editable bad IR.

        Timers/sampling/DCR allow zero; current magnitudes and capacity cutoffs
        are positive. This does not certify equipment execution or loop topology.
        """
        for name in ("c_rate", "cv_cutoff_c_rate", "current_mA", "cv_cutoff_mA"):
            value = getattr(self, name)
            if value is not None:
                finite_number(value, name, positive=True)
        for name in ("end_time_s", "record_time_s", "record_dV_mV", "dcr_start_s", "dcr_end_s"):
            value = getattr(self, name)
            if value is not None:
                finite_number(value, name, nonnegative=True)
        for name in ("voltage_v", "end_voltage_v"):
            value = getattr(self, name)
            if value is not None:
                finite_number(value, name)
        for name, maximum in (("end_capacity_fraction", 1), ("dod_percent", 100)):
            value = getattr(self, name)
            if value is not None and finite_number(value, name, positive=True) > maximum:
                raise ValueError(f"{name} must be in (0, {maximum}]")
        for name in ("loop_count", "loop_goto_step", "goto_step_id"):
            value = getattr(self, name)
            if value is not None:
                positive_integer(value, name)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StepIntent:
        payload = dict(data)
        extra = payload.pop("extra", {})
        known = {key: payload[key] for key in cls.__dataclass_fields__ if key in payload}
        intent = cls(**known)
        intent.extra = extra
        return intent
