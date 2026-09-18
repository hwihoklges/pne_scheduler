# 정확성 리뷰와 제품 확장 계획 — 2026-09-18

대상: `review/correctness-and-roadmap`. 이 문서는 기존 [로드맵](../planning/ROADMAP.md)의 장비 증거·Gate 이력을 대체하지 않는다. **구현됨**은 코드와 회귀 테스트가 존재한다는 뜻이며, **계획**은 미완료 산출물, **실험적**은 사용 가능하더라도 과학·장비 승인이 없는 기능이다.

## 1. 이번 리뷰의 상태와 안전 경계

| 영역 | 상태 | 의미와 남은 한계 |
|---|---|---|
| resume checkpoint / splice | 구현됨 | 중단 지점 판정 및 splice 참조 검증 강화. LOOP의 실제 반복 이력, 남은 횟수, 재개 시 셀 상태는 사람이 검토해야 한다. |
| template writer / 출력 게시 | 구현됨 | 원본 보존, 출력 충돌 및 실패 시 복구 경로 강화. 협력적 lock을 따르는 프로세스 사이의 보호이며 강제 종료 후 stale lock이 남을 수 있다. |
| SCH + manifest 게시 | 구현됨 | 예외 처리 가능한 실패에 대한 복구이지 두 파일을 하나로 묶는 전원 장애 원자성이 아니다. 재시작 시 두 파일의 hash와 짝을 확인한다. |
| API 접근 정책 / Next proxy | 구현됨 | 로컬 경계와 인증된 cloud 모드를 구분한다. 안전한 다중 tenant 운영 준비 완료를 의미하지 않는다. |
| frontend 문서 변경 queue | 구현됨 | 순차 변경·오류 처리의 controller 회귀 검증. 브라우저 DOM·실제 사용자 동작 전체를 검증한 것은 아니다. |
| from-scratch writer / 장비 실행 | 실험적 | `equipment_executable=false` 유지. 소프트웨어 테스트만으로 장비 실행을 승인하지 않는다. |

최종 로컬 Python 3.14.3 검증에서 **703 passed / 1 skipped / 37 subtests passed**, Ruff 통과를 확인했다(미보유 lab Schedule.mdb로 1 skip). 이는 해당 환경의 스냅샷이지 아래 새 CI matrix의 통과 증거가 아니다. skip 사유와 실제 장비 검증 기록을 함께 검토한다.

stale lock은 소유 프로세스가 종료되었는지, 출력에 진행 중인 작업이 없는지 확인한 뒤 운영자가 처리한다. 무조건 시간만 보고 lock을 제거하지 않는다. crash 이후에는 원본·출력·manifest를 보존하고 hash 불일치 파일을 장비로 보내지 않는다.

## 2. 배포 정책: 소스에 맞춘 환경 변수

정의는 [API 정책](../api/security.py)과 [Next 설정](../web/next.config.ts)에 있다. API 정책은 애플리케이션 생성 시 읽으므로 변경 후 재시작이 필요하다.

| 변수 | 기본 / 허용값 | 운영 계약 |
|---|---|---|
| `PNE_SERVER_MODE` | `local` / `cloud` | local은 Host와 실제 peer 모두 loopback이어야 한다. forwarded header는 신뢰하지 않는다. |
| `PNE_LOCAL_RESOURCES` | local `1`, cloud `0`; `0` 또는 `1` | cloud에서 `1`은 시작 오류다. cloud에는 로컬 파일·장비 자원 경로를 노출하지 않는다. |
| `PNE_API_TOKEN` | cloud 필수, 32자 이상 | 정확한 `Authorization: Bearer …` 비교. local에서는 사용하지 않는다. 길이 제한은 충분한 무작위성·회전 정책을 대신하지 않는다. |
| `PNE_ALLOWED_HOSTS` | cloud 필수, 쉼표 구분 | 포트를 포함하는 정확한 authority 목록. wildcard가 아니며 cloud 요청 Host를 소문자로 비교한다. |
| `PNE_ALLOWED_ORIGINS` | local: localhost/127.0.0.1/[::1], 3000/8000의 http origin; cloud: 빈 목록 | 정확한 http(s) origin, 경로·후행 slash 없음. local은 loopback origin만 허용. Origin이 제공되면 목록과 일치해야 하고 cross-site Fetch 요청은 거부한다. |
| `PNE_API` | `http://127.0.0.1:8000` | Next proxy 대상. local에서는 loopback HTTP(S)만, URL 자격 증명 금지. |

**Cloud는 TLS 종료와 인증을 담당하는 신뢰된 gateway가 필수**다. Next proxy에 공용 서버 토큰을 주입하거나 브라우저 저장소·응답·로그로 노출하지 않는다. origin 검사와 단일 bearer token은 사용자별 권한·tenant 격리·quota·감사 체계를 제공하지 않는다. gateway에서 인증하더라도 API의 Host/token 정책을 만족해야 한다. cloud 기능은 현재 제한된 stateless 경계이며, 파일 시스템을 그대로 공개하는 배포는 지원하지 않는다.

## 3. 실행 가능한 단계별 로드맵

일정은 달력 약속 대신 선행 산출물과 인수 기준으로 관리한다. 각 단계는 PR에서 구현/계획/실험적 상태와 증거 URL을 갱신한다.

| 단계 / 상태 | 의존성 | 산출물 | 인수 기준 |
|---|---|---|---|
| S0 정확성 기반 고정 — 구현됨 + 검증 잔여 | 이번 리뷰 | 3조합 Python CI, Node controller/build/typecheck, 회귀 케이스 목록 | 전체 테스트와 corpus smoke 통과, skip 공개; resume 실패·게시 실패·정책 거부 테스트 유지. 원격 결과 확인 전 완료 승인 금지. |
| S1 typed protocol DSL·스키마 — 계획 (기존 v2·ParameterSpec 재사용) | S0 | 명시적 단위·타입·범위의 protocol 계약, schema version migration, import adapter 계약 | 이전 프로젝트 golden migration과 roundtrip, 모호한 단위·알 수 없는 schema 거부, editor와 compiler의 동일한 validation 결과. |
| S2 설명 가능한 편집·검토 — 계획 | S1 | protocol graph preview, timeline, cycle/time 추정 구간·불확실성; semantic SCH diff, provenance와 hash-bound 승인 이력 | graph와 컴파일 step/LOOP 일치, CV·조건 종료 시간을 확정값처럼 표시하지 않음; 의미 변화와 opaque byte 변화를 구분하고 수정 시 기존 승인 무효화. |
| S3 안전한 resume 검토 — 계획 (현재 splice 기반) | S1, S2 | 쓰기 없는 dry-run, checkpoint 근거·제외 step·LOOP 재매핑·잔여 반복 표, replay harness | 중첩 LOOP·중간 중단·불완전 로그·잘못된 번호 fixture에서 잘못된 재개를 차단. 사람이 원본/재개/셀 상태를 확인·서명하기 전 실행 승인 금지. |
| S4 instrument target capability — 계획 | S1, 기존 장비 registry 및 controlled pairs | 장비/CTS build/layout별 target profile, capability negotiation 및 fail-closed export | 미지원 step·단위·layout 조합은 거부; 장비별 CTS reopen/Save-As 비교와 감독된 장비 검증을 별도 기록. 소프트웨어 green만으로 승인 불가. |
| S5 실험 설계·채널 계획 — 계획 | S2, S4 | project template, parameter sweep/DOE, channel scheduling·자원 충돌 simulation | 동일 seed/config의 동일 계획, 채널 중복·공유 자원·안전 한계 충돌 검출; 예상 시간 범위와 실제 오차 보고. 실시간 제어 아님. |
| S6 실험 데이터 추적·CycleDiag 연계 — 계획 | S1, S2 및 CycleDiag 데이터 계약 | 표준 manifest/result, lab import adapters, cell/batch/channel/장비/원본 hash 연결, 결과 피드백 검토 화면 | schema 호환·단위·cycle identity 검증, 원본부터 결과까지 추적 가능. 피드백은 새 초안/사람 승인만 생성하고 **실행 중 장비 파라미터 자동 변경은 금지**. |
| S7 cloud workspace·batch job — 계획 | S0 정책, S1, S6 | 프로젝트·job 모델, 제한된 저장소, tenant 격리, quota, audit history, 취소/재시도 | cross-tenant 접근·path traversal·quota 우회 부정 테스트, job 중단 복구, retention/삭제 감사; 보안 검토 전 multi-tenant 운영 승인 금지. |

S3와 S4는 별도 장비 검증 책임자의 승인이 필요하다. S5 DOE 결과나 S6 진단 점수는 프로토콜의 물리적 안전성을 증명하지 않는다. 프로젝트 단위 승인자는 입력 hash, target profile, 해석 불확실성, CTS·장비 증거를 확인한다.

## 4. CI와 재현성 인계

[ci.yml](../.github/workflows/ci.yml): Ubuntu/Python 3.10, Ubuntu/3.14, Windows/3.14 각각 전체 Ruff·pytest·corpus smoke. frontend는 Ubuntu/Node 22에서 `npm ci`, `npm test`, `npm run build`, `tsc --noEmit`을 실행한다. controller 테스트에 더해 Playwright Chromium으로 실제 화면 로딩·Flask proxy 응답·외부 Origin 거부를 검증한다. 전체 사용자 흐름 또는 장비 검증을 대체하지 않는다. 로컬 Node 배포 사이트 접근이 제한되어 웹 실행 증거는 원격 CI에서 확인한다.

JUnit, skip 사유, pip freeze, Node 의존성 목록을 보존한다. workflow별 concurrency 취소, `contents: read`, timeout 및 14일 artifact 보존을 설정했다. freeze는 lock이 아니며 직접·전이 의존성 재현성을 완성하지 않는다.

검증된 환경의 [Python 3.14 constraints](../constraints/py314.txt)를 3.14 install에 연결했다. 3.10에는 이를 적용하지 않는다. 버전 고정은 배포물 hash 검증 lock이나 과학적 재현성 보증이 아니므로 CI의 실제 설치 결과를 함께 보존한다. 원격 matrix·브라우저 결과 확인 및 장비 LOOP 수동 검토가 남아 있다.