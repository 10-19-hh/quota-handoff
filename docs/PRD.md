# Quota Handoff — Product Requirements Document (공개판)

> 버전: 0.1.2 · 상태: 초안 / M0.1 + 릴리스 검토 수정 반영 · 라이선스 MIT · 저장소 이름 미확정
> 첫 타깃: 여러 코딩 에이전트를 함께 쓰는 개인 개발자 / 로컬 CLI
> 원칙: **읽기 전용, 개인정보 비수집, OS 무변경, 실행은 사용자가 결정**

## 0. 한 문장 정의

Claude Code, Codex 등 여러 코딩 에이전트의 *공개되었거나 사용이 허용된 사용량 정보*를 정규화한다. 그 정보로 다음 작업에 적합한 에이전트를 설명과 함께 제안하고, 작업 맥락을 잃지 않도록 인계 문서를 만든다. 이 도구는 **로컬 우선 CLI**다.

이 제품은 샌드박스 실행기, 모델 API 프록시, 인증 중개 서비스가 아니다.

## 1. 문제

- 에이전트를 둘 이상 쓰면 각 에이전트의 남은 사용량을 따로 확인해 다음 작업에 쓸 에이전트를 골라야 한다.
- 한도에 다다라 에이전트를 바꾸면 남은 작업, 주의사항, 테스트 상태를 손으로 다시 정리해야 한다.
- 오케스트레이션 도구가 권한 우회, 시스템 서비스, 인증 파일에 손대면 도입 비용과 보안 위험이 커진다.

Orca 같은 도구는 이미 사용량 표시, 작업 추적, 워크트리 관리를 제공한다. 이 프로젝트는 새 에이전트 플랫폼이 아니다. **정책 엔진, 인계 포맷, 가벼운 UX를 더하는 보조 도구**다.

## 2. 목표와 성공 기준

| 목표 | v0.1 완료 기준 |
|---|---|
| 쉬운 첫 경험 | Python이 준비된 환경에서 로그인 없이 `--demo`로 3분 안에 `status`→`plan` 완료 |
| 명료한 추천 | 같은 입력이면 같은 추천과 사유. 모든 추천에 출처, 시각, 불확실성 표시 |
| 안전한 인계 | Markdown/JSON 인계 문서 생성. 비밀 값, 파일 본문, Git diff 원문은 기본적으로 수집하지 않음 |
| 낮은 운영 위험 | 실행 경로에 OS ACL, 서비스, 샌드박스, Git 쓰기, 에이전트 실행 없음 |
| 검증 가능성 | 정책 경계값, 누락, 오류, 회귀 케이스 테스트 전부 통과 |
| 공개 가능성 | 예제에 실제 계정이나 절대경로 없음. 설치, 제거, 제한 사항 안내 |

출시 가설: 자동 실행이 없어도 `추천 이유 + 복사 가능한 인계 패키지`만으로 인계 시간이 줄어든다. 이는 검증 목표이며 확인된 사실이 아니다.

## 3. 핵심 흐름

1. `status`로 현재 확인 가능한 사용량을 본다.
2. `plan`에 작업 유형과 선호 에이전트를 넘긴다.
3. 정책 엔진이 한도 창(5시간, 주간 등)을 비교해 후보, 이유, 경고를 출력한다.
4. `handoff create`가 사용자가 쓴 작업 요약을 인계 문서로 만든다.
5. 사용자는 인계 문서를 **직접 연 공식 에이전트 세션**에 붙여 넣는다. 도구는 OAuth나 세션을 대신 호출하지 않는다.

```console
quota-handoff status --demo
quota-handoff plan --demo --task code --prefer codex
quota-handoff handoff create --input examples/task-sample.json --output ./handoff.md
```

## 4. 기능 요구사항

### F1. 사용량 정규화 (P0)

`UsageSnapshot`:

```json
{
  "provider": "codex",
  "account_label": "local-primary",
  "source": "fixture",
  "observed_at": "2026-11-18T09:00:00+09:00",
  "status": "available",
  "windows": [
    {"key": "5h", "used_pct": 42, "resets_at": null},
    {"key": "weekly", "used_pct": null, "resets_at": null, "state": "failed"}
  ]
}
```

- `account_label`은 사용자가 정한 임의 레이블이다. 인증 식별자나 이메일은 받지 않는다.
- `used_pct`는 `[0,100]` 범위이고, 미보고 시 `null`이다. 0%나 100%로 대체하지 않는다.
- `status`: `available|unknown|stale|unavailable|error`.
- 창 상태 `state`:
  - `reported`: 값 있음
  - `unsupported`: 공급자가 이 창을 제공하지 않음. 누락으로 보지 않는다.
  - `failed`: 조회 실패. 누락으로 본다.
  - `not_reported`: `null`의 기본값. 누락으로 본다.
- 원본 페이로드나 입력값 원문을 오류 메시지와 로그에 그대로 출력하지 않는다.

### F2. 설명 가능한 결정적 추천 (P0) — 정책 v0.1.1

1. 위험 점수는 보고된 창 중 가장 높은 `used_pct`다. 기본 등급은 `<80` ok, `80–95 미만` warning, `≥95` near_limit(제외)다. 이 값은 정책 예시이며 실제 한도의 예측이 아니다.
2. 신선도 기본값은 5분이다. 60초를 넘는 미래 시각은 `error`로 처리한다. `stale/unknown/error`는 자동 선택하지 않고 수동 검토 대상으로 표시한다.
3. **완전 후보**는 누락 창이 없는 후보, **부분 후보**는 `failed`/`not_reported` 창이 있는 후보다. 부분 후보는 `confidence=partial`과 경고로 표시하고 자동 선택하지 않는다.
4. 적격 후보 중에서 **안전 등급을 먼저 비교**한다. 같은 등급 안에서만 사용자 선호를 적용한다. 선호 대상이 더 위험한 등급이면 선호를 무시하고 사유 코드를 남긴다.
5. 선호를 적용하지 않으면 사용률이 낮은 후보를 고른다. 완전히 같으면 `(provider, account_label)` 정렬 순서로 정한다. 입력 행 순서는 결과에 영향을 주지 않는다(AT-05).
6. 결과는 `recommended|manual_decision_required|no_eligible_provider` 중 하나다. 부분 후보나 불확실한 후보만 있으면 `manual_decision_required`다. 항상 `reason_codes`와 사람이 읽을 문장을 함께 낸다.
7. 공급자의 모델 능력이나 비용을 추정하지 않는다.

### F3. 명시적 작업 인계 패키지 (P0)

- 사용자가 작성한 `objective`, `done`, `remaining`, `blockers`, `read_first`, `tests`, `next_steps`, `do_not`, `expected_outcome`, `notes`를 검증해 `handoff.md`를 만들고, 선택적으로 `handoff.json`도 만든다.
- 모델 추론이 아닌 결정적 템플릿 변환이다. 같은 입력이면 같은 바이트가 나온다.
- Git 검색, 파일 본문 읽기, diff 추출, commit/push/merge, 에이전트 재실행을 하지 않는다. 쓰기는 지정한 출력 파일에만 하며, 덮어쓰려면 `--force`가 필요하다.
- 비밀 값 패턴과 홈 경로 사용자명을 보수적으로 마스킹하고 경고한다. **패턴 마스킹은 보안 보장이 아니다.**

### F4. 로컬 상태 (P1, 미구현)

사용자가 명시적으로 저장할 때만 기록한다. 정책 버전, 입력 시각, 추천 사유, 사용자 선택을 남기고, 토큰과 원본 페이로드는 저장하지 않는다. `history`와 `clear`를 제공한다.

### F5. 실시간 어댑터 (P1, 별도 승인 게이트)

현재 설치된 버전에서 확인한 읽기 전용 명령만 쓴다. `shell=False`, 허용 목록, 시간 제한, 출력 크기 제한을 둔다. 인증 값은 파싱 직후 폐기한다. 미지원 스키마는 `unknown`으로 처리한다. 격리된 환경에서 먼저 검증한다. 자세한 조건은 `docs/REVIEW-M0.1.md`의 M1 진입 조건을 따른다.

## 5. 비목표

1. 모델 API 호출. OAuth 토큰 수집, 보관, 프록시. 한도 우회나 다계정 회전.
2. 에이전트나 오케스트레이터 Worker의 자동 실행 또는 무인 실행.
3. 샌드박스, ACL, 서비스, 레지스트리, 방화벽 변경. 프로세스 제어.
4. 사용자 저장소나 워크트리 변경. Git 자동 commit, push, merge.
5. 동의 없는 세션 기록 수집. 비공식 인증 엔드포인트 사용.
6. 백그라운드 데몬, 중앙 서버, 원격 계정 관리.

## 6. 약관과 보안

- **사용량 정보 조회**와 **모델 요청 중개**는 다르다. 구독 자격증명을 이용한 제3자 요청 라우팅은 하지 않는다.
- 일부 오케스트레이터는 에이전트를 시작할 때 권한 우회 플래그를 기본으로 붙일 수 있다. 따라서 이 도구는 Worker 실행을 자동으로 호출하지 않는다.
- 실패할 때는 닫힌 쪽으로 처리한다. 정보가 부족하면 `unknown`, 권한이 모호하면 실행 금지, 인증 파일 접근이 필요하면 기능을 차단한다.
- 권한은 운영체제의 통상적인 사용자 권한을 따른다. 격리나 강화를 주장하지 않는다.

## 7. 아키텍처

Python 3.11+, 표준 라이브러리만 사용, `argparse`와 `dataclasses`, 테스트는 `unittest`.

```text
CLI
 ├── UsageProvider: FixtureProvider, DemoProvider (M0) / 실시간 어댑터 (M1, 게이트)
 ├── Normalizer + Validator
 ├── PolicyEngine (결정적, LLM 없음)
 ├── HandoffRenderer (JSON → Markdown)
 └── LocalStore (M1, 선택)
```

## 8. 계약과 종료 코드

- `status`: `snapshots[]`, `source`, `observed_at`, `freshness`, `confidence`, `warnings[]`, `demo`.
- `plan`: `recommendation`, `reason_codes[]`, `candidate_evaluations[]`, `manual_approval_required=true`, `demo`.
- `handoff`: `schema_version`, `objective`, `completed`, `pending`, `blockers`, `tests`, `next_action`, `restrictions`.
- 스키마 버전: 입력은 `0.1`(창 `state` 없음)과 `0.2`를 받고, 출력은 항상 `0.2`.
- reason code 목록과 안정성 규칙: `docs/REASON_CODES.md`.
- 종료 코드: `0` 정상, `2` 입력/스키마/출력 오류, `3` 추천 불가(수동 결정 또는 적격 없음), `4` 안전 게이트 거부(기존 파일 덮어쓰기, 입력 파일 덮어쓰기).

## 9. 마일스톤

| 마일스톤 | 범위 | 상태 |
|---|---|---|
| M0 | fixture `status`/`plan`, handoff, 테스트, README | 완료 |
| M0.1 | 정책 R1/R2 확정, `--demo`, 공개 문서 정리, 마스킹·코드 경로 회귀 테스트 | 완료 |
| M0.1 수정 | 릴리스 검토 BLOCKER 수정, 스키마 0.2, reason code 목록, MIT | 완료(재검토 대기) |
| M1 | 실시간 읽기 전용 어댑터, 스키마 감지 | 진입 조건 충족 후 별도 승인 |
| M2 | 패키징, 공개 문서, 사용자 평가 | M1 이후 |
| M3+ | 인계 UX 개선, 선택적 실행(별도 정책 검토) | 범위 밖 |

## 10. 수용 테스트

- AT-01: 에이전트나 오케스트레이터 없이 샘플 명령이 동작한다.
- AT-02: 5시간 60%, 주간 97%인 공급자를 추천하지 않는다.
- AT-03: 모든 사용량이 `unknown`이면 `manual_decision_required`를 내고 수치를 지어내지 않는다.
- AT-04: 5분을 넘긴 관측은 `stale`로 처리하고 자동 추천하지 않는다.
- AT-05: 입력 행 순서를 섞어도 결과와 reason_codes가 같다.
- AT-06: 비밀 패턴은 마스킹하거나 경고한다. 공개 예제에는 실토큰과 절대경로가 없다.
- AT-07: 출력 파일 덮어쓰기를 기본으로 거부한다.
- AT-08: 테스트 중 모델, 오케스트레이터, 서비스, ACL, Git 쓰기 호출이 없다.
- AT-09: 잘못된 입력은 유용한 오류와 종료 코드 2로 끝난다.
- AT-10: Windows, macOS, Linux 경로를 고려하고 OS를 수정하지 않는다.

## 11. 미해결 사항

제품명과 저장소명, 실시간 어댑터에 허용할 인증 방식, 실제 작업 승인 UI. (라이선스는 MIT로 결정.)

## 12. 참고 자료

- Orca usage tracking: https://orca.dev/docs/agents/usage-tracking
- Orca CLI reference: https://orca.dev/docs/cli/reference
- Claude Code legal and compliance: https://code.claude.com/docs/en/legal-and-compliance

공식 문서는 바뀔 수 있다. 실제 명령과 응답 스키마는 설치된 버전에서 따로 검증한다.
