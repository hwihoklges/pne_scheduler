"""Load cycler StepEnd / raw CSV and detect experiment checkpoint."""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..schema.enums import CTS_STEP_OFFSET


@dataclass(frozen=True, slots=True)
class StepEndRow:
    cts_step_no: int
    sch_step_no: int
    step_type: str
    completion_code: str
    total_cycle: int | None
    cycle_num: int | None
    step_time_sec: float | None
    # None preserves code-based interpretation for older callers/StepEnd files.
    step_completed: bool | None = None


@dataclass
class ExperimentCheckpoint:
    """Latest observed progress and a conservative resume decision.

    The historical ``last_completed_*`` field names remain API-compatible: on
    interrupted data they identify the observed step, not proof of completion.
    Always interpret them together with ``step_completed``.
    """

    source_sch: Path | None
    data_path: Path
    last_completed_cts_step: int
    last_completed_sch_step: int
    resume_sch_step: int
    total_cycle: int | None
    cycle_num: int | None
    completed_loop_iterations: int | None
    step_completed: bool
    is_finished: bool
    confidence: str
    detail: str
    warnings: list[str] = field(default_factory=list)


_COMPLETION_FINISHED = re.compile(r"last\s*step|experiment\s*end|end\s*step", re.I)
_COMPLETION_OK = re.compile(r"\b(?:complete|completed|stepend)\b", re.I)
_COMPLETION_NOT_OK = re.compile(r"\b(?:incomplete|not\s+complete(?:d)?)\b", re.I)


def _is_completed(row: StepEndRow) -> bool:
    if row.step_completed is not None:
        return row.step_completed
    return bool(
        _COMPLETION_OK.search(row.completion_code)
        and not _COMPLETION_NOT_OK.search(row.completion_code)
    )


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
        return int(number) if math.isfinite(number) and number.is_integer() else None
    except (ValueError, OverflowError):
        return None


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_column(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _column_map(header: list[str]) -> dict[str, str]:
    aliases = {
        "stepno": "StepNo",
        "steptype": "StepType",
        "code": "Code",
        "totalcycle": "TotalCycle",
        "cyclenum": "CycleNum",
        "curcycle": "CurCycle",
        "steptimesec": "StepTime_sec",
    }
    mapping: dict[str, str] = {}
    for col in header:
        key = _normalize_column(col)
        if key in aliases:
            mapping[aliases[key]] = col
        elif key in {"stepno", "steptype", "code", "totalcycle", "cyclenum", "curcycle"}:
            mapping[key] = col
    return mapping


def load_stepend_csv(path: str | Path, *, encoding: str = "cp949") -> list[StepEndRow]:
    resolved = Path(path)
    rows: list[StepEndRow] = []
    with resolved.open("r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return rows
        colmap = _column_map(list(reader.fieldnames))
        step_col = colmap.get("StepNo")
        if step_col is None:
            raise ValueError(f"StepNo column not found in {resolved.name}")

        for raw in reader:
            cts_step = _parse_int(raw.get(step_col))
            if cts_step is None or cts_step <= CTS_STEP_OFFSET:
                continue
            sch_step = cts_step - CTS_STEP_OFFSET
            rows.append(
                StepEndRow(
                    cts_step_no=cts_step,
                    sch_step_no=sch_step,
                    step_type=str(raw.get(colmap.get("StepType", ""), "")).strip(),
                    completion_code=str(raw.get(colmap.get("Code", ""), "")).strip(),
                    total_cycle=_parse_int(raw.get(colmap.get("TotalCycle", ""))),
                    cycle_num=_parse_int(raw.get(colmap.get("CycleNum", ""))),
                    step_time_sec=_parse_float(raw.get(colmap.get("StepTime_sec", ""))),
                )
            )
    return rows


def load_raw_csv_checkpoint(path: str | Path, *, encoding: str = "cp949") -> StepEndRow | None:
    """Use the latest valid SCH progress row, including an unfinished leg.

    CTS step 1 is the preamble, not SCH step 1. A raw StepEnd flag is explicit
    evidence of completion; step type alone (notably Rest) is not.
    """
    resolved = Path(path)
    last: StepEndRow | None = None
    with resolved.open("r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return None
        colmap = _column_map(list(reader.fieldnames))
        step_col = colmap.get("StepNo")
        if step_col is None:
            return None
        stepend_col = None
        for col in reader.fieldnames:
            if _normalize_column(col) == "stepend":
                stepend_col = col
                break

        for raw in reader:
            cts_step = _parse_int(raw.get(step_col))
            if cts_step is None or cts_step <= CTS_STEP_OFFSET:
                continue
            is_stepend = False
            if stepend_col is not None:
                val = str(raw.get(stepend_col, "")).strip().lower()
                is_stepend = val in {"1", "true", "yes"}
            row = StepEndRow(
                cts_step_no=cts_step,
                sch_step_no=cts_step - CTS_STEP_OFFSET,
                step_type=str(raw.get(colmap.get("StepType", ""), "")).strip(),
                completion_code=(
                    str(raw.get(colmap.get("Code", ""), "")).strip()
                    or ("StepEnd" if is_stepend else "InProgress")
                ),
                total_cycle=_parse_int(raw.get(colmap.get("TotalCycle", ""))),
                cycle_num=_parse_int(raw.get(colmap.get("CycleNum", ""))),
                step_time_sec=_parse_float(raw.get(colmap.get("StepTime_sec", ""))),
                step_completed=is_stepend,
            )
            last = row
    return last


def detect_checkpoint(
    data_path: str | Path,
    *,
    source_sch: str | Path | None = None,
) -> ExperimentCheckpoint:
    path = Path(data_path)
    warnings: list[str] = []

    if path.suffix.lower() == ".csv":
        name_lower = path.name.lower()
        if "stepend" in name_lower or "step_end" in name_lower:
            rows = load_stepend_csv(path)
        else:
            row = load_raw_csv_checkpoint(path)
            rows = [row] if row is not None else []
    else:
        raise ValueError(f"Unsupported data file: {path}")

    if not rows:
        raise ValueError(f"No step progress rows found in {path}")

    last = rows[-1]
    code = last.completion_code
    step_type = last.step_type.lower()

    is_finished = bool(
        _COMPLETION_FINISHED.search(code)
        or step_type == "end"
        or "last step" in code.lower()
    )
    step_completed = is_finished or _is_completed(last)

    if is_finished:
        resume_sch = last.sch_step_no
        detail = "experiment already finished at END step"
        confidence = "high"
    elif step_completed:
        resume_sch = last.sch_step_no + 1
        detail = f"last step {last.sch_step_no} completed ({code}) → resume at step {resume_sch}"
        confidence = "high"
    else:
        resume_sch = last.sch_step_no
        detail = f"step {last.sch_step_no} interrupted ({code}) → resume at same step"
        confidence = "medium"
        warnings.append("step may have been interrupted mid-leg; verify resume step manually")

    completed_loops = _infer_completed_loops(rows)

    return ExperimentCheckpoint(
        source_sch=Path(source_sch) if source_sch is not None else None,
        data_path=path,
        last_completed_cts_step=last.cts_step_no,
        last_completed_sch_step=last.sch_step_no,
        resume_sch_step=resume_sch,
        total_cycle=last.total_cycle,
        cycle_num=last.cycle_num,
        completed_loop_iterations=completed_loops,
        step_completed=step_completed,
        is_finished=is_finished,
        confidence=confidence,
        detail=detail,
        warnings=warnings,
    )


def _infer_completed_loops(rows: list[StepEndRow]) -> int | None:
    discharge_ends = [
        r
        for r in rows
        if r.step_type.lower() == "discharge" and _is_completed(r)
    ]
    if discharge_ends:
        return len(discharge_ends)
    cycles = [r.total_cycle for r in rows if r.total_cycle is not None]
    if cycles:
        return max(cycles)
    return None
