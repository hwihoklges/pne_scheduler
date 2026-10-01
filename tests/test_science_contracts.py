"""Analytical science contracts; runnable with stdlib unittest, no fixtures/deps."""

import math
import struct
import unittest
from unittest.mock import patch

from pne_scheduler.api.serializers import views_json
from pne_scheduler.engine.c_rate import (
    c_rate_from_current_mA, capacity_mAh_from_fraction, current_mA_from_c_rate,
    format_c_rate_label, is_fast_charge_c_rate, snap_c_rate, validate_current_within_limits,
)
from pne_scheduler.engine.compiler import compile_steps, compile_step_warnings
from pne_scheduler.engine.duration import (
    combine_duration_estimates, estimate_step_duration, estimate_steps_duration, format_duration,
)
from pne_scheduler.ir.cell_profile import CellProfile
from pne_scheduler.ir.composer import compose_module_steps
from pne_scheduler.ir.equipment_profile import EquipmentProfile, effective_current_limit_mA
from pne_scheduler.ir.loader import ProjectLoadError, repair_project_dict
from pne_scheduler.ir.procedure import build_procedure
from pne_scheduler.ir.project import ModuleNode, ScheduleProject
from pne_scheduler.ir.step_intent import StepIntent
from pne_scheduler.report.summary import summarize_project
from pne_scheduler.spec.derived import module_derived_values
from pne_scheduler.ui.flow_model import FlowProjectModel
from pne_scheduler.ui.document import ProjectDocument
from pne_scheduler.ui.workspace_model import WorkspaceModel
from pne_scheduler.validate.preflight import validate_project


def cell(q=50.0):
    return CellProfile(q, 4.2, 2.5)


def rest(seconds=10):
    return StepIntent("rest", end_time_s=seconds)


def loop(target=1, count=3):
    return StepIntent("loop", loop_goto_step=target, loop_count=count)


class ScienceContracts(unittest.TestCase):
    def test_capacity_cancels_at_constant_rate(self):
        for q, current in ((50, 25), (5000, 2500)):
            with self.subTest(capacity=q):
                self.assertEqual(current_mA_from_c_rate(.5, cell(q)), current)
                self.assertEqual(c_rate_from_current_mA(current, cell(q)), .5)
                step = StepIntent("charge", mode="CC", c_rate=.5, end_capacity_fraction=1)
                estimate = estimate_steps_duration([step], cell=cell(q))
                self.assertEqual(estimate.estimated_seconds, 7200)
                self.assertEqual(estimate.status, "nominal")
                self.assertFalse(estimate.is_exact)
                self.assertEqual(capacity_mAh_from_fraction(.5, cell(q)), q / 2)

    def test_fixed_current_is_different_and_matches_compiler_precedence(self):
        step = StepIntent("charge", mode="CC", c_rate=.5, current_mA=25)
        for q, seconds in ((50, 7200), (5000, 720000)):
            self.assertEqual(estimate_steps_duration([step], cell=cell(q)).estimated_seconds, seconds)
            self.assertEqual(struct.unpack_from("<f", compile_steps([step], cell(q))[0], 16)[0], 25)
        missing = estimate_steps_duration([step])
        self.assertEqual(missing.unknown_step_count, 1)
        self.assertEqual(missing.estimated_seconds, 0)
        step.end_time_s = 12
        self.assertEqual(estimate_steps_duration([step]).estimated_seconds, 12)
        self.assertTrue(estimate_steps_duration([step]).is_exact)

    def test_public_helpers_reject_invalid_domains(self):
        bad_values = (True, False, math.nan, math.inf, -math.inf, 0, -1, 10**400)
        helpers = (current_mA_from_c_rate, c_rate_from_current_mA,
                   capacity_mAh_from_fraction, validate_current_within_limits)
        for value in bad_values:
            for helper in helpers:
                with self.subTest(helper=helper.__name__, value=repr(value)):
                    with self.assertRaises(ValueError):
                        helper(value, cell())
            for helper in (snap_c_rate, format_c_rate_label, is_fast_charge_c_rate):
                with self.assertRaises(ValueError):
                    helper(value)
        for value in (True, math.nan, math.inf, -1, 10**400):
            with self.assertRaises(ValueError):
                snap_c_rate(.5, rtol=value)
        self.assertEqual(snap_c_rate(.5, rtol=0).label, "C/2")
        with self.assertRaises(ValueError):
            capacity_mAh_from_fraction(1.1, cell())
        with self.assertRaises(ValueError):
            current_mA_from_c_rate(1e308, cell(1e308))
        with self.assertRaises(ValueError):
            c_rate_from_current_mA(1e308, cell(1e-308))

    def test_profiles_and_mutation_boundaries(self):
        for name in ("nominal_capacity_mAh", "max_current_mA", "formation_capacity_mAh"):
            for value in (True, math.nan, math.inf, 0, -1, 10**400):
                data = dict(nominal_capacity_mAh=50, v_max=4.2, v_min=2.5)
                data[name] = value
                with self.subTest(field=name, value=repr(value)):
                    with self.assertRaises(ValueError):
                        CellProfile.from_dict(data)
                    mutated = cell()
                    setattr(mutated, name, value)
                    with self.assertRaises(ValueError):
                        current_mA_from_c_rate(.5, mutated)
                    with self.assertRaises(ValueError):
                        compile_steps([rest()], mutated)
                    self.assertFalse(estimate_steps_duration([rest()], cell=mutated).is_complete)
        for name in ("v_min", "v_max"):
            for value in (True, math.nan, math.inf, 10**400):
                data = dict(nominal_capacity_mAh=50, v_max=4.2, v_min=2.5)
                data[name] = value
                with self.assertRaises(ValueError):
                    CellProfile.from_dict(data)
        self.assertEqual(CellProfile(50, 1, 0).v_min, 0)

    def test_explicit_limits_never_become_absent(self):
        for value in (True, math.nan, math.inf, 0, -1, 10**400):
            with self.assertRaises(ValueError):
                effective_current_limit_mA(value, None)
            with self.assertRaises(ValueError):
                EquipmentProfile("PNE02", max_current_mA=value)
            with self.assertRaises(ProjectLoadError):
                repair_project_dict({"cell_profile": {"max_current_mA": value}})
            with self.assertRaises(ProjectLoadError):
                repair_project_dict({"equipment": {"max_current_mA": value}})
        limited = CellProfile(50, 4.2, 2.5, max_current_mA=20)
        self.assertTrue(validate_current_within_limits(25, limited))
        self.assertEqual(validate_current_within_limits(20, limited), [])
        self.assertIsNone(effective_current_limit_mA(None, None))

    def test_step_domains_and_zero_fields(self):
        for name in ("c_rate", "current_mA", "cv_cutoff_c_rate", "cv_cutoff_mA", "end_capacity_fraction"):
            for value in (0, -1):
                step = StepIntent("charge", mode="CC")
                setattr(step, name, value)
                with self.assertRaises(ValueError):
                    step.validate()
        for name in ("end_time_s", "record_time_s", "record_dV_mV", "dcr_start_s", "dcr_end_s"):
            step = rest()
            setattr(step, name, -1)
            with self.assertRaises(ValueError):
                step.validate()
        fields = ("c_rate", "current_mA", "cv_cutoff_c_rate", "cv_cutoff_mA",
                  "voltage_v", "end_voltage_v", "end_time_s", "end_capacity_fraction",
                  "record_time_s", "record_dV_mV", "dod_percent", "dcr_start_s", "dcr_end_s")
        for field in fields:
            for value in (True, math.nan, math.inf, -math.inf, 10**400):
                step = StepIntent("charge", mode="CC", c_rate=.5)
                setattr(step, field, value)
                with self.subTest(field=field, value=repr(value)):
                    with self.assertRaises(ValueError):
                        step.validate()
                    with self.assertRaises(ValueError):
                        compile_steps([step], cell())
                    result = estimate_steps_duration([step])
                    self.assertFalse(result.is_complete)
                    self.assertTrue(math.isfinite(result.estimated_seconds))
        step = rest(0)
        step.record_time_s = step.record_dV_mV = step.dcr_start_s = step.dcr_end_s = 0
        step.validate()
        compile_steps([step], cell())
        self.assertTrue(estimate_steps_duration([step]).is_exact)
        self.assertEqual(format_duration(0), "0s")
        for value in (True, math.nan, math.inf, -1):
            with self.assertRaises(ValueError):
                format_duration(value)
        with self.assertRaises(ValueError):
            compile_steps([StepIntent("charge", voltage_v=1e308)], cell())

    def test_timers_taper_and_unknown_elapsed(self):
        configured = estimate_steps_duration([rest(125)])
        self.assertEqual(configured.exact_seconds, 125)
        self.assertEqual(configured.status, "configured")
        self.assertEqual(configured.elapsed_time_status, "unknown")
        for mode in (None, "CCCV", "CV"):
            for timer in (None, 0, 50):
                step = StepIntent("charge", mode=mode, c_rate=.5, end_time_s=timer)
                estimate = estimate_steps_duration([step])
                self.assertFalse(estimate.is_complete)
                self.assertFalse(estimate.is_exact)
                self.assertEqual(estimate.unknown_step_count, 1)
                self.assertTrue(any("CV taper" in w for w in estimate.warnings))
                if mode == "CV" and timer is None:
                    self.assertIsNone(estimate.steps[0].seconds)
        for field, value in (("end_voltage_v", 4.2), ("end_capacity_fraction", .5),
                             ("cv_cutoff_mA", 1), ("cv_cutoff_c_rate", .1), ("dod_percent", 50)):
            step = StepIntent("charge", mode="CC", c_rate=.5, end_time_s=100)
            setattr(step, field, value)
            estimate = estimate_steps_duration([step])
            self.assertEqual(estimate.steps[0].kind, "configured_timer")
            self.assertEqual(estimate.estimated_seconds, 100)
            self.assertFalse(estimate.is_complete)

    def test_nominal_cc_competing_cutoff_remains_incomplete(self):
        for field, value in (("end_voltage_v", 4.2), ("cv_cutoff_mA", 1),
                             ("cv_cutoff_c_rate", .1), ("dod_percent", 50)):
            step = StepIntent("charge", mode="CC", c_rate=.5)
            setattr(step, field, value)
            estimate = estimate_steps_duration([step])
            self.assertEqual(estimate.estimated_seconds, 7200)
            self.assertEqual(estimate.steps[0].kind, "nominal_cc")
            self.assertFalse(estimate.is_complete)

    def test_compiler_loop_bytes_remain_unchanged(self):
        record = compile_steps([rest() for _ in range(7)] + [loop(target=7, count=3)], cell())[-1]
        for offset, expected in ((8, 8), (48, 7), (52, 3), (564, 7)):
            self.assertEqual(struct.unpack_from("<I", record, offset)[0], expected)

    def test_simple_loops_and_unknown_repetition(self):
        estimate = estimate_steps_duration([rest(10), rest(20), loop(count=4), rest(5)])
        self.assertEqual(estimate.estimated_seconds, 125)
        self.assertTrue(estimate.is_exact)
        self.assertEqual(estimate_steps_duration([rest(), loop(count=1)]).estimated_seconds, 10)
        estimate = estimate_steps_duration([StepIntent("charge", c_rate=.5), loop(count=4)])
        self.assertEqual(estimate.estimated_seconds, 28800)
        self.assertEqual(estimate.unknown_step_count, 4)
        self.assertFalse(estimate.is_complete)

    def test_bad_loops_fail_closed_in_estimator_composer_compiler(self):
        bad = [loop(count=value) for value in (None, True, 0, -1, 1.5, math.nan, math.inf, 10**400)]
        bad += [loop(target=value) for value in (None, True, 0, -1, 1.5, 2, math.nan, math.inf)]
        bad += [StepIntent("loop", loop_count=3, loop_target_ref="missing")]
        for step in bad:
            with self.subTest(step=step):
                estimate = estimate_steps_duration([rest(), step])
                self.assertFalse(estimate.is_complete)
                self.assertTrue(estimate.warnings)
                with self.assertRaises(ValueError):
                    compile_steps([rest(), step], cell())
                with patch("pne_scheduler.modules.expand_module", return_value=[rest(), step]):
                    with self.assertRaises(ValueError):
                        compose_module_steps([ModuleNode("m", "rest", {})], cell())

    def test_nested_crossing_and_combination_preserve_unknown(self):
        for steps in ([rest(), loop(count=2), loop(count=3)],
                      [rest(), rest(), loop(count=2), loop(target=2, count=3)]):
            estimate = estimate_steps_duration(steps)
            self.assertFalse(estimate.is_complete)
            self.assertFalse(estimate.is_exact)
            self.assertTrue(any("nested/crossing" in w for w in estimate.warnings))
            combined = combine_duration_estimates([estimate_steps_duration([rest()]), estimate])
            self.assertEqual(combined.status, "incomplete")
            self.assertGreater(combined.unknown_step_count, 0)
            self.assertEqual(combined.steps[-1].step_index, len(steps) + 1)
        disjoint = estimate_steps_duration([rest(), loop(count=2), rest(20), loop(target=3, count=3)])
        self.assertEqual(disjoint.estimated_seconds, 80)
        self.assertTrue(disjoint.is_exact)

    def test_duration_overflow_is_not_finite_assurance(self):
        with self.assertRaises(ValueError):
            estimate_step_duration(StepIntent("charge", mode="CC", c_rate=1e-308), step_index=1)
        cases = ([rest(1e308), rest(1e308)], [rest(1e308), loop(count=10)],
                 [StepIntent("charge", mode="CC", c_rate=1e-308)])
        for steps in cases:
            estimate = estimate_steps_duration(steps)
            self.assertFalse(estimate.is_complete)
            self.assertTrue(math.isfinite(estimate.estimated_seconds))
        large = estimate_steps_duration([rest(1e308)])
        combined = combine_duration_estimates([large, large])
        self.assertFalse(combined.is_complete)
        self.assertTrue(math.isfinite(combined.estimated_seconds))

    def test_presentation_and_preflight_propagation(self):
        project = ScheduleProject("science", cell(), modules=[ModuleNode("f", "formation", {"cycle_count": 1})])
        procedure = build_procedure(project)
        self.assertEqual(procedure.duration_status, "incomplete")
        self.assertGreater(procedure.duration_unknown_step_count, 0)
        self.assertFalse(procedure.duration_exact)
        self.assertEqual(procedure.phases[0].duration_status, "incomplete")
        summary = summarize_project(project, procedure=procedure)
        self.assertIn("incomplete", summary.duration_text)
        self.assertEqual(summary.finish_text, "")
        self.assertTrue(summary.warnings)
        self.assertFalse(FlowProjectModel(project).estimate_duration().total.is_complete)
        derived = module_derived_values("formation", {"cycle_count": 1}, cell=cell())
        self.assertTrue(any("incomplete" in value.text for value in derived))
        for step in (rest(0), StepIntent("charge", mode="CC", c_rate=.5, cv_cutoff_mA=math.nan)):
            with patch.object(ScheduleProject, "expand_steps", return_value=[step, StepIntent("end")]):
                result = validate_project(project)
                self.assertEqual(result.passed, step.step_type == "rest")
        project.cell_profile.nominal_capacity_mAh = math.nan
        self.assertFalse(validate_project(project).passed)
        self.assertTrue(any("semantic_unverified" in w for w in compile_step_warnings(
            [StepIntent("charge", c_rate=.5, end_capacity_fraction=.5)])))

    def test_api_and_workspace_preserve_incomplete_status(self):
        for steps in ([StepIntent("charge", c_rate=.5)],
                      [rest(), loop(count=2), loop(count=3)],
                      [rest(), rest(), loop(count=2), loop(target=2, count=3)]):
            project = ScheduleProject("science", cell(), modules=[ModuleNode(
                "custom", "custom_steps", {"steps": [step.to_dict() for step in steps]})])
            model = WorkspaceModel(ProjectDocument(project, record_history=False))
            data = views_json(model)
            self.assertEqual(data["procedure"]["durationStatus"], "incomplete")
            self.assertFalse(data["procedure"]["durationExact"])
            self.assertGreater(data["procedure"]["durationUnknownStepCount"], 0)
            self.assertTrue(data["procedure"]["durationWarnings"])
            self.assertIn("incomplete", data["modules"][0]["duration"])
            self.assertIn("incomplete", data["summary"]["durationText"])


if __name__ == "__main__":
    unittest.main()