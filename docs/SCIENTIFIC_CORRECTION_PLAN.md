# 물리량·시간 예측 검증 계획 (2026-09-19)

기준 코드 `e98678b`. 상태: **독립 검토 조건 수용·구현 승인 (2026-09-19)**.

## 유지할 물리식

- I[mA] = C-rate[h^-1] × Q_nom[mAh].
- 동일 nominal 기준의 용량 비율 f에 대해 이상 CC 시간 t[s] = 3600f/C-rate.
  용량은 약분되므로 50 mAh든 5000 mAh든 0.5C 전량의 이상 시간은 7200 s다.
- 전압 cutoff, 사용 가능한 실제 용량, CV taper, 열/휴지 이력과 장비 overhead는
  별도다. nominal 시간은 실험 소요 시간 또는 안전성을 보장하지 않는다.

## 계획

| ID | 위험 | 구현과 합격 기준 |
|---|---|---|
| S1 | 변환/limit 비교에서 NaN·Infinity·0/음수 nominal 값이 정상처럼 전파될 수 있음 | c_rate helper와 duration 입력의 finite·정의역 검증; 한도는 명시 값만 사용; invalid 입력이 출력/시간에 전파되지 않는 회귀 테스트 |
| S2 | CV/경쟁 종료조건과 LOOP 시간의 완전성 오해 | 수치 estimate와 정확성/unknown 상태를 구별; CV 잔여는 추정 완료로 표시하지 않음; 중첩/교차 loop를 지원하지 않으면 정확하다고 합산하지 않고 명시적으로 unknown 처리 |
| S3 | 과학적으로 틀린 용량 scaling 제안 | I=CQ, 역변환, cell size 변경 시 시간 불변 테스트를 고정; 임의 capacity multiplier나 안전 C-rate 경계 도입 금지 |

검토→구현→synthetic/기존 테스트→Windows/Linux CI→PR 병합 순서로 실행한다.
장비 writer 허용 범위나 equipment_executable 승인은 확대하지 않는다.
범용 safe voltage/current/C-rate 수치는 도입하지 않는다. profile 한계는
실험·장비 책임자가 정하고, 프로그램 검증 통과는 장비 운전 승인이 아니다.

## 독립 검토/실행 기록

CycleDiag와 함께 별도 검토자가 소스/계획을 점검했다. S1–S2 조건부 승인,
S3 승인 의견을 받았으며 다음 조건을 수용한다.

- boolean을 숫자로 받지 않고 NaN/Inf/overflow 및 field별 허용 zero를 구분한다.
  explicit limit/current override/cutoff 값과 IR boundary를 검사하며 invalid limit을
  없는 limit처럼 취급하지 않는다. output writer 허용 범위는 확대하지 않는다.
- configured timer 값, nominal CC 계산, elapsed-time 모델, timer ceiling, unknown을
  구분한다. 확인되지 않은 timer scope를 wall-clock 상한으로 주장하지 않는다.
  pure CV/default CCCV, absolute-current 우선순위가 반영되지 않으면 abstain한다.
- nested/crossing LOOP를 정확하게 구현하지 않는다면 unknown; missing/fractional/
  nonpositive count나 미해결 target을 절삭/0초로 통과시키지 않는다.
  combine/presentation에도 unknown을 보존한다. exact_seconds는 측정 시간이 아니다.
- 일정한 C-rate와 일정한 absolute current는 다른 조건이다. C-rate 시간 불변성과
  current 역변환 테스트를 추가한다. capacity 종료 binary 의미 미검증 경고를 유지한다.

구현 전 검토 조건을 기록한 것이며 실제 장비 validation을 완료했다는 뜻은 아니다.

## Implementation execution — 2026-09-23

Initial implementation state: code implemented; tests were pending actual execution.
Finite-domain validation covers public conversions, mutable cell/step use boundaries,
explicit profile limits and compiler inputs. No equipment approval or binary layout changes.
Duration keeps legacy numerical subtotals and adds typed kind/model completeness,
aggregate status, unknown counts and warnings. `exact_seconds` is configured arithmetic,
not measured elapsed time. Unknown taper/competing timers remain incomplete; current
override takes priority and requires explicit nominal capacity for a CC model.
Disjoint simple loops use total body executions; nested/crossing loops abstain.
Invalid explicit limits fail closed rather than becoming absent limits on lenient load.
Python 3.14 stdlib analytical tests and syntax checks are recorded below;
the full local pytest suite was run on 2026-09-23. Windows/Linux CI remains
pending the main integration workflow.

### Actual local verification (2026-09-23)

**Code implemented; local checks and full scheduler pytest passed; frontend/CI pending.**
The earlier dependency-free checks installed no dependencies or LFS fixtures.
No commit/push/merge performed in this correction pass.
All source edits are confined to this repository on `science/validation-contracts`.

- Python 3.14, with repository parent on `PYTHONPATH`, bytecode disabled:
  `py -3.14 -B -m unittest discover -s tests -p test_science_contracts.py -v`:
  **15 tests passed**, including parameterized subtests for finite domains, booleans,
  overflows, zero-permitted fields, mutable profiles, overrides, timers/taper,
  simple/nested/crossing loops and real API/workspace propagation.
- Existing `test_scheduler_safety.py` through stdlib unittest: **29 tests passed**.
- Direct invocation of existing zero-argument, dependency-free test functions:
  **21 passed** (preflight, summary, equipment profiles, two synthetic composer tests).
  This is not a pytest run and does not claim fixture/parameterized pytest coverage.
- **14 module smoke checks passed** through composition, compilation and duration:
  catalog defaults, with one explicit zero-second rest for the custom-steps module.
  The initial smoke harness used a nonexistent catalog accessor; corrected to the
  inspected `MODULE_CATALOG` API before the successful run (no source workaround).
- All **256 Python source files** parsed with `ast.parse`; `git diff --check` passed.
  Editor diagnostics reported no errors. Frontend TypeScript build and GUI visual
  validation were not run in this dependency-free environment.
- Python 3.14 project virtual environment, full scheduler `pytest tests -q --tb=short`:
  **722 passed, 2 skipped, 171 subtests passed** (exit 0). This includes the
  Gate D module pipeline and verification tests. The previous full run had
  9 LOOP-related harness failures and 710 passed, 2 skipped; comparing each
  written type code to the corresponding record from the complete composed
  compile removed the standalone-LOOP failure without relaxing validation.
  Three new tests cover the composed loop number, count and target at both
  Gate B/Ensol offsets, absence of a standalone expected-LOOP compile, and
  rejection of LOOPs targeting themselves with and without a preceding step.
- `ruff check .`: **all checks passed** (exit 0); `git diff --check`: **passed**
  (exit 0). These are local software checks, not equipment verification.

### Compatibility and remaining limits

- Existing duration fields remain numeric subtotals. `StepDurationEstimate.kind`
  distinguishes configured timer, nominal CC, control and unknown;
  `model_complete`, aggregate `status`, unknown counts and warnings must be read
  alongside the subtotal. `elapsed_time_status` remains unknown even for configured
  arithmetic. Public duration functions accept optional explicit `cell` metadata.
- Single-step validation/conversion helpers raise `ValueError` for invalid domains;
  aggregate duration converts those failures to unknown with warnings. Compiler
  numeric/representation failures remain blocking, not equipment authorization.
- Pure CV has no CC estimate. CCCV/default charge may retain a nominal CC part,
  never a complete taper estimate. Competing cutoffs/timers make the model incomplete.
- Simple disjoint loops count total body executions. Nested/crossing loops deliberately
  abstain; their reported subtotal is not a full repeated schedule duration or bound.
- A finish timestamp is suppressed for incomplete models. Complete arithmetic may
  show only an explicitly hypothetical calendar projection, never measured elapsed time.
- Invalid explicit current limits/formation capacities are no longer silently cleared
  by lenient loading; callers receive `ProjectLoadError` and must correct the source.
- Only unsafe existing expectations changed: formation completeness and predicted
  finish timestamp. The compiler loop byte test now supplies a valid preceding body;
  every offset/value assertion is retained. Binary layouts, capacity-field evidence
  warnings and equipment execution approval are unchanged.
- Full local scheduler pytest passed; frontend build and Windows/Linux CI remain
  for main integration.
  No equipment validation, CV taper model, overhead model or universal safety limits
  are claimed by these analytical/software checks.

### Changed files

- Engine: [c_rate](../engine/c_rate.py), [duration](../engine/duration.py),
  [compiler](../engine/compiler.py), [public exports](../engine/__init__.py).
- IR: [numeric domains](../ir/numeric.py), [cell profile](../ir/cell_profile.py),
  [step intent](../ir/step_intent.py), [equipment profile](../ir/equipment_profile.py),
  [loader](../ir/loader.py), [composer](../ir/composer.py), [procedure](../ir/procedure.py).
- Boundaries/presentation: [preflight](../validate/preflight.py),
  [derived values](../spec/derived.py), [summary](../report/summary.py),
  [flow model](../ui/flow_model.py), [flow editor](../ui/flow_editor.py),
  [workspace model](../ui/workspace_model.py), [API serializer](../api/serializers.py),
  [TypeScript response type](../web/src/lib/api.ts).
- Tests: [science contracts](../tests/test_science_contracts.py),
  [duration](../tests/test_duration.py), [summary](../tests/test_recipes_and_summary.py),
  [compiler byte assertions](../tests/test_compiler_c2.py),
  [Gate D harness regression](../tests/test_gate_d_harness.py).
- Gate D: [module pipeline harness](../validate/gate_d_harness.py).
- Documentation: this execution record, [README](../README.md),
  [existing roadmap](../planning/ROADMAP.md).