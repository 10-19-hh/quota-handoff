# T-008 릴리스 검토: M0.1 → M0.2

- 대상: `src/quota_handoff/` 전체, `tests/` 전체, `examples/`, `README.md`, `pyproject.toml`, `docs/PRD.md`, `docs/SECURITY.md`, `docs/REVIEW-M0.1.md`, `.github/workflows/test.yml`
- 버전: `0.1.1.dev0`
- 방식: 코드 정독, 그리고 프로젝트를 임시 디렉터리에 복사해 실행한 재현 프로브. 프로젝트 소스는 바꾸지 않았다. 이 파일만 새로 만들었다.

## 0. 검토자 독립성 한계 (먼저 읽을 것)

이 검토는 M0–M0.1 구현을 작성한 에이전트가 수행했다. 기억에 의존하지 않고 디스크의 파일과 실행 결과만 근거로 삼았지만, **실질적인 제3자 검토는 아니다.** `docs/REVIEW-M0.1.md`의 B2(제3자 검토)는 이 문서로 해소되지 않는다. 사람이나 별도 검토자의 재확인이 여전히 필요하다.

## 1. 최종 판정: **REVISE**

릴리스를 막는 결함이 4건 있다(B-1 ~ B-4). 모두 수정 범위가 작다. 프로세스 실행, 네트워크, 인증 파일 접근 경로는 발견하지 못했으므로 HOLD 사유는 없다. B-1~B-3을 고치고 회귀 테스트를 추가하면 M0.2에 들어갈 수 있다. B-4(라이선스)는 공개 배포 전까지만 해결하면 된다.

## 2. 실행 근거

| 항목 | 결과 |
|---|---|
| 테스트 (Python 3.11 / 3.13 / 3.14, Windows) | 140/140 통과 (각 버전 모두) |
| Python 3.12 | **미검증.** 로컬 런처에 등록된 3.12 실행 파일이 존재하지 않았다 |
| Linux / macOS | **미검증.** CI는 정의만 있고 한 번도 실행되지 않았다 |
| wheel 빌드 (오프라인, 임시 디렉터리) | 성공. 15개 모듈과 `quota-handoff = quota_handoff.cli:main` entry point 확인. License 메타데이터 없음, `examples/` 미포함 |
| `src/` import 전수 확인 | `argparse, dataclasses, datetime, json, math, os, pathlib, re, sys, tempfile, typing`과 내부 모듈뿐이다 |
| 공개 트리 비공개 용어 검색 | 0건 |

## 3. BLOCKER

### B-1. 오류 메시지가 `schema_version` 값을 그대로 출력함 — SECURITY.md 서술과 불일치

- **위치:** `src/quota_handoff/normalizer.py` `check_schema_version()` (L66–69, `{version!r}`). fixture, policy, task 입력이 모두 이 함수를 거친다.
- **발생 조건:** 입력 파일의 `schema_version`에 임의 문자열(예: 실수로 붙여 넣은 토큰)이 들어간 경우. 프로브에서 `status`와 `handoff create` 모두 exit 2로 끝나면서 값 전체를 stderr에 출력했다. 길이 제한이 없어 최대 1 MiB까지 출력된다.
- **영향:** `docs/SECURITY.md`는 "Error messages … do not echo input values"라고 쓰고 있지만 사실이 아니다. `used_pct` 범위 오류도 숫자 값을 그대로 출력한다(민감하지는 않음). 실제 피해는 사용자 본인 파일이 로컬 stderr에 나오는 정도다. 다만 공개 보안 문서의 주장이 틀렸고, 오류 메시지가 그대로 수집되는 M1 어댑터 환경에서는 같은 패턴이 실제 위험이 된다.
- **최소 수정안:** 미지원 버전일 때 값을 출력하지 않는다. 예: `unsupported schema_version (type str, 47 chars)`. 또는 `safe_name()`과 `redact()`를 거쳐 잘라서 출력한다. SECURITY.md 문장은 "문자열 값은 출력하지 않는다. 숫자 범위 오류는 숫자만 표시한다"로 정확히 고친다.
- **테스트 누락:** 있음. `ErrorHygieneTests`는 timestamp, source, 필드 이름만 검사하고 `schema_version`은 검사하지 않는다.

### B-2. `handoff create`의 다중 출력이 원자적이지 않음, macOS에서 대소문자 충돌 미방지

- **위치:** `src/quota_handoff/cli.py` `cmd_handoff_create()` (L84–104), `_same_path()` (L107–115).
- **발생 조건과 영향:**
  1. **부분 기록 (재현됨):** `--output new.md --json-output existing.json`(`--force` 없음). `new.md`를 먼저 만든 뒤 JSON 쓰기가 거부되어 exit 4로 끝난다. "안전 게이트 거부"라고 보고하지만 파일 하나는 이미 생겼다. 같은 명령을 다시 실행하면 이번에는 `new.md`가 이미 있어서 실패한다.
  2. **대소문자 충돌 (코드 분석, macOS 실기 미재현):** `_same_path`는 `os.path.normcase`에 의존한다. POSIX에서 이 함수는 대소문자를 바꾸지 않는다(`posixpath.normcase('h.md') != posixpath.normcase('H.md')` 확인). 두 파일이 아직 없으면 `samefile` 분기도 타지 않는다. 대소문자를 구분하지 않는 macOS 기본 파일 시스템(APFS)에서 `--output h.md --json-output H.md --force`를 주면, Markdown을 쓴 직후 JSON이 같은 파일을 교체하고 **exit 0으로 끝난다.** Markdown 결과가 조용히 사라진다. 검토 요청 항목인 "출력 경로 대소문자 충돌 방지"는 Windows에서만 성립한다.
- **최소 수정안:** 쓰기 전에 두 대상을 한꺼번에 검사한다.
  - `--force`가 없으면 두 파일이 모두 없는지 먼저 확인하고, 하나라도 있으면 아무것도 쓰지 않고 exit 4로 끝낸다.
  - 첫 파일을 쓴 뒤 두 번째 경로가 이미 존재하고 `os.path.samefile(output, json_output)`이 참이면 exit 2로 중단한다. 이렇게 하면 파일 시스템의 대소문자 규칙과 무관하게 동작한다.
  - 경쟁 상태까지 막을 필요는 없다.
- **테스트 누락:** 있음. 부분 기록 테스트가 없다. 대소문자 테스트(`test_case_variant_outputs_rejected_on_windows`)는 Windows가 아니면 skip된다.

### B-3. README대로 개발 환경을 만들면 테스트가 실패함

- **위치:** `tests/test_hardening.py` `PublicContentTests.files()`. 프로젝트 루트 아래 모든 파일을 UTF-8 텍스트로 읽는다.
- **발생 조건 (재현됨):** README Install 절의 `python -m venv .venv`를 체크아웃 안에서 실행하면, README Development 절의 `python -m unittest discover -s tests -t .`가 실패한다. `.venv`의 `.exe` 파일들에서 `UnicodeDecodeError`가 난다. `build/`, `dist/`, 나중에 추가할 이미지 같은 바이너리 파일도 똑같이 깨진다.
- **영향:** 문서대로 따라 한 기여자의 테스트가 실패하고, 공개 직후 이슈로 이어진다. CI는 깨끗한 체크아웃이라 이 문제가 드러나지 않는다.
- **최소 수정안:** 검사 대상을 공개 대상 경로의 허용 목록으로 한정한다(`src/`, `tests/`, `docs/`, `examples/`, `.github/`, `README.md`, `pyproject.toml`, `.gitignore`). 그 안에서 디코딩할 수 없는 파일은 실패로 보고하되, 원인을 명확히 표시한다.
- **테스트 누락:** 해당 없음(테스트 자체의 결함). 수정한 뒤 `.venv`가 있는 상태로 한 번 실행해 확인할 것.

### B-4. 라이선스 미적용 (공개 배포 차단, M0.2 개발은 차단하지 않음)

- **위치:** 저장소 루트에 `LICENSE` 파일이 없다. `pyproject.toml`에 `license` 필드가 없다. wheel METADATA에도 License 정보가 없다.
- **영향:** 라이선스가 없으면 법적으로 "모든 권리 보유" 상태다. 오픈소스로 공개할 수 없다.
- **최소 수정안:** 7절 검토에 따라 MIT를 채택하는 경우 다음을 한다.
  - `LICENSE` 파일을 추가한다. 저작권자 표기는 실명 대신 핸들이나 "Quota Handoff contributors"를 쓸 수 있다.
  - `pyproject.toml`에 `license = "MIT"`(SPDX 표기, build 요구사항을 `setuptools>=77`로 올려야 함) 또는 `license = {text = "MIT"}`(현재 setuptools 하한 유지)를 추가한다.
  - `classifiers`를 추가한다.
- **테스트 누락:** 해당 없음.

## 4. 기능 정확성 확인

| 항목 | 확인 결과 | 근거 |
|---|---|---|
| 사용량 값 검증 | 정상. 유한수 0–100, bool·NaN·문자열 거부, 범위 밖 값은 보정하지 않고 거부 | `normalizer.py` L86–95, `test_normalizer` |
| 타임스탬프 검증 | 정상. 오프셋 필수, `Z` 허용, 순수 날짜 거부. 극단값 문제는 I-1 참고 | L45–57 |
| 창 상태 4종 구분 | 정상. `reported`는 숫자 필수, 나머지 세 상태는 `null` 필수. `unsupported`만 누락으로 세지 않음 | `models.py`, `normalizer.py` L96–105, `WindowStateTests` |
| 등급과 `--prefer` | 정상. 등급을 먼저 비교하고 같은 등급 안에서만 선호 적용. 무시할 때는 사유 코드 3종 중 하나를 남김 | `policy.plan`, `_preference_outcome`, `R1PreferenceTests` |
| 부분 사용량 | 정상. 완전 후보가 우선이고, 부분 후보만 있으면 manual. 보고된 창이 95% 이상인 부분 후보는 제외 | `R2PartialTests` |
| 동점 결정성 | 정상. 정렬 키 `(risk, provider, account_label)`. 4개 행의 모든 순열에서 출력 동일 | `test_order_invariance_with_states` |
| demo 구분 | 정상. `source`/`account_label`이 `demo`, 배너, JSON `demo: true`. fixture가 `source: demo`를 쓰면 거부되어 위장할 수 없음 | `adapters/demo.py`, `DemoTests` |
| Markdown 인계 | 생성은 정상. 다만 마크다운/HTML 주입 가능 (I-3) | 프로브 P8 |
| 결정적 출력 | 정상. 같은 입력이면 md·json 바이트 동일, 줄바꿈 LF | `test_handoff_deterministic_bytes` |
| 덮어쓰기 방지 | 단일 파일은 정상(`xb`로 생성, dangling symlink도 거부). 다중 출력은 **B-2** | `jsonio.py` L72–80 |
| 대소문자 충돌 방지 | Windows만 정상. macOS는 **B-2** | `cli.py` L107–115 |

## 5. 보안 검토

| 항목 | 결과 |
|---|---|
| 프로세스·네트워크 실행 경로 | 발견하지 못했다. import 전수 확인과 정적·런타임 테스트로 뒷받침된다. 단, 정적 테스트는 직접 호출만 잡고 런타임 패치는 Python 수준 진입점만 막는다. 모든 경로를 증명하지는 못한다 |
| 동적 import / 우회 호출 | `eval`/`exec`/`__import__`/`importlib` 없음. `getattr(stream, "reconfigure")` 하나뿐이고, 고정 문자열이라 위험하지 않다 |
| 출력 경로 검증 | 부모 디렉터리가 있어야 하고, 디렉터리 경로를 거부하며, 디렉터리를 만들지 않는다. **B-2** 외에 `--output`이 `--input`과 같은 경로여도 `--force`면 입력 파일을 Markdown으로 덮어쓴다(재현, I-2) |
| symlink | `--force` 없이는 생성 자체를 거부한다. `--force`는 `os.replace`로 링크를 교체할 뿐 대상 파일을 따라가지 않는다. 임시 파일은 같은 디렉터리에 `mkstemp`(O_EXCL)로 만들고 실패하면 지운다. POSIX에서 `--force` 결과 파일 권한이 `mkstemp` 기본값 0600이 될 것으로 보인다(분석만, 실기 미확인, I-8) |
| 비밀값 / 경로 마스킹 | 테스트한 형식은 잘 동작한다. 놓치는 경우: WSL 경로(`/mnt/c/Users/…`), UNC 경로, 공백이 있는 사용자명(첫 단어만 가려짐). 오탐: `sk-learn-…` 같은 일반 단어. 4000자 필드에서 정규식 비용이 이차적으로 늘어난다(I-4). 문서의 "보장 아님" 표기는 정확하다 |
| 오류 메시지의 원문 출력 | **B-1.** 그 밖에 알 수 없는 필드 이름은 40자까지 출력된다(SECURITY.md에 명시되어 있음, I-6) |
| 인증 파일 / OS 설정 접근 | 없다. 환경변수, 홈 디렉터리, 레지스트리, 서비스 접근 코드가 없다 |

## 6. IMPROVEMENT

| ID | 내용 | 위치 / 근거 |
|---|---|---|
| I-1 | 처리하지 않은 예외가 traceback과 exit 1로 끝나 종료 코드 계약(0/2/3/4)을 벗어난다. `--now 0001-01-01T00:00:10+00:00`(demo) → `OverflowError`. 깊게 중첩된 JSON → `RecursionError`. 파일 권한 오류 → `OSError`. `loads_strict`에서 `RecursionError`를, `DemoProvider`와 나이 계산에서 `OverflowError`를, CLI 최상단에서 `OSError`를 잡아 exit 2로 바꿀 것 | 프로브 P4b, P5 |
| I-2 | 출력 경로가 `--input`과 같으면 거부할 것(`--force`여도) | 프로브 P2 |
| I-3 | `objective`와 `expected_outcome`이 여러 줄 그대로 렌더링되어 `## Do not` 같은 제목을 주입할 수 있다. 원시 HTML(`<img src=…>`)도 그대로 통과한다. 이전 에이전트가 쓴 체크포인트를 다음 에이전트에 붙여 넣는 흐름이라면 프롬프트 주입 경로가 된다. 줄 앞의 `#`, `>`, `<`를 이스케이프하거나 인용 블록으로 감쌀 것 | `handoff.render_markdown`, 프로브 P8 |
| I-4 | `secret_assignment`의 `[\w.-]*` 접두부 때문에 백트래킹이 이차적으로 늘어난다. `a-a-a-…` 4000자 필드 하나에 약 0.56초가 걸리고, 1 MiB 입력이면 수 분이 될 수 있다(로컬 자기 입력에 한정). 접두부 길이를 제한(`[\w.-]{0,40}`)할 것 | 프로브 P11 |
| I-5 | 마스킹 범위를 넓힐 것: WSL `/mnt/<drive>/Users/`, UNC, 공백이 있는 사용자명. 오탐: `sk-` 뒤에 하이픈 단어가 이어지는 경우는 숫자 포함을 요구할 것 | 프로브 P10 |
| I-6 | 알 수 없는 필드 이름도 `redact()`를 거친 뒤 출력할 것 | `normalizer.safe_name` |
| I-7 | CI: Python 3.14를 매트릭스에 추가할 것. `pip install .` 후 `quota-handoff` entry point를 실행하는 단계가 없다. CI를 한 번도 실행하지 않았으니 M0.2 첫 작업으로 실행할 것 | `.github/workflows/test.yml` |
| I-8 | `--force` 결과 파일 권한이 기본 생성 경로와 다를 수 있다(POSIX). `os.replace` 전에 `chmod`로 umask 기본값을 맞출지 결정할 것. Linux/macOS에서 확인 필요 | `jsonio.write_text_file` |
| I-9 | `examples/`가 wheel에 들어가지 않는다. PyPI로 설치하면 README의 세 번째 명령(`--input examples/task-sample.json`)이 동작하지 않는다. 내장 샘플 출력 옵션을 두거나 README를 "체크아웃 기준"으로 명시할 것 | wheel 검사 |
| I-10 | 버전이 `__init__.py`와 `pyproject.toml` 두 곳에 있다. setuptools dynamic version으로 하나로 합칠 것 | |
| I-11 | `SECURITY.md`의 "Reporting"에 연락 경로가 없다. 공개할 때 GitHub private vulnerability reporting을 켤 것 | |
| I-12 | 공개 검사 테스트의 이메일 정규식은 몇 개 최상위 도메인만 본다. 일반 규칙으로 넓힐 것 | `PublicContentTests.EMAIL` |

## 7. 정책 결정 검토

| 결정 | 검토 의견 |
|---|---|
| 동점: `(provider, account_label)` 정렬 | **유지 권장.** 입력 순서를 쓰면 AT-05와 충돌한다. 순열 테스트로 고정되어 있다 |
| `schema_version` 0.2 | **첫 공개 시 0.2로 올리기를 권장(차단 사유는 아님).** 근거: (1) 파서가 알 수 없는 필드를 엄격히 거부하므로, `state`를 넣은 문서는 0.1만 아는 도구에서 "unsupported schema_version"이 아니라 "unknown field" 오류가 난다. 선택 필드 추가도 사실상 호환성을 깬다. (2) 출력 계약도 `0.1` 그대로인 채 바뀌었다(`confidence` 값 `reported`→`complete`, reason code 이름 변경, `demo`·`missing_windows` 추가). 제안: 입력은 0.1(`state` 금지)과 0.2를 모두 받고, 출력은 0.2로 낸다. 공개 전이므로 "0.1 동결" 선언도 대안이지만, 내부에서 만든 0.1 산출물과 의미가 다르다 |
| 95% 이상 관측된 부분 후보 제외 | **유지 권장.** 보고된 값 하나로 이미 한도 근접이 확정되므로 누락 창이 결론을 바꿀 수 없다. `test_partial_with_reported_near_limit_is_excluded`로 고정되어 있다 |
| MIT 라이선스 | **채택 가능.** 런타임 의존성이 없고(표준 라이브러리만, PSF 라이선스), 빌드 의존성 setuptools(MIT)는 배포물에 들어가지 않는다. 문서 안의 외부 링크는 라이선스 의무를 만들지 않는다. 남은 결정은 저작권자 표기(개인정보 노출 여부)와 상표 표기다. README에 "Not affiliated" 문구가 이미 있다 |
| reason_codes 안정성 | 현재는 코드가 각 모듈에 흩어진 문자열이다. `declared_status_{status}`처럼 동적으로 조합되는 코드도 있다. 첫 공개 전에 다음을 권장한다: (1) `docs/REASON_CODES.md`에 전체 목록과 의미를 등록한다. (2) 생성될 수 있는 모든 코드가 등록 목록 안에 있는지 검사하는 테스트를 둔다. (3) 규칙: 코드 추가는 마이너 변경, 이름 변경이나 삭제는 `schema_version` 메이저 변경과 한 릴리스 이상의 사용 중단 예고를 거친다. (4) 소비자는 모르는 코드를 무시하도록 문서화한다 |

## 8. M0.2 진입 조건

1. B-1, B-2, B-3 수정과 각각의 회귀 테스트(B-2는 POSIX 대소문자 무시 파일 시스템 시나리오 포함).
2. CI 매트릭스(Linux/macOS/Windows × 3.11–3.14)를 처음으로 실행해 통과.
3. `schema_version` 정책과 reason code 등록 방식 결정.
4. B-4(라이선스)는 공개 배포 전까지 해결.
5. 사람 또는 별도 에이전트의 재검토(이 문서의 B-1~B-3 재현 포함).

이 검토 범위에서 실제 Orca·Codex·Claude 실행, 사용량 실조회, 인증 파일 열람, OS 설정 변경, git 작업은 하지 않았다.
