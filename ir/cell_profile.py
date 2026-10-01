"""Cell-level parameters shared across experiment modules."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from .numeric import finite_number


@dataclass
class CellProfile:
    """Nominal cell parameters for C-rate and limit checks."""

    nominal_capacity_mAh: float
    v_max: float
    v_min: float
    max_current_mA: float | None = None
    formation_capacity_mAh: float | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Recheck mutable profiles before calculations; no implicit limits."""
        finite_number(self.nominal_capacity_mAh, "nominal_capacity_mAh", positive=True)
        finite_number(self.v_min, "v_min")
        finite_number(self.v_max, "v_max")
        for name in ("max_current_mA", "formation_capacity_mAh"):
            value = getattr(self, name)
            if value is not None:
                finite_number(value, name, positive=True)
        if self.v_max <= self.v_min:
            raise ValueError("v_max must be greater than v_min")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CellProfile:
        return cls(**data)
