# Intent-Slide 로컬 작업실

AI 도구의 기존 로그인과 설정으로 공통 엔진 v2에 연결하는 한국어 작업실입니다. Codex·Claude Code는 확인된 정액제 로그인을 사용하고, Gemini CLI·OpenCode는 표시된 로그인·모델·과금 안내를 확인한 뒤 명시적으로 선택합니다. 정적 브리프 생성기인 기존 `web/`과 분리되어 있으며 콘솔 자체는 외부 패키지나 CDN에 의존하지 않습니다.

## 실행과 연결

v2 서버가 이 디렉터리를 같은 출처에서 제공합니다. 처음에는 서버 실행 터미널의 일회용 접속 링크를 사용합니다. 브라우저는 `#session` 값을 즉시 주소에서 제거한 뒤 세션 쿠키와 CSRF 토큰으로 교환합니다. 토큰은 화면·로그·로컬 저장소에 기록하지 않습니다.

이 디렉터리만 정적 HTTP 서버로 열면 실제 API가 없어 연결 필요 화면을 표시합니다. 모의 진행률이나 프로젝트 데이터는 제품 화면에 삽입하지 않습니다.

명령은 복제한 Intent-Slide 저장소의 최상위 폴더에서 실행합니다. 운영체제별 실행 방법과 지원·검증 범위는 [제품 시작 안내](../../../README.md)를 따릅니다. 아래 셸 명령은 Mac 예시입니다.

```sh
./intent-slide start
```

기본 주소는 `127.0.0.1:4317`, 기본 저장소는 `projects/presentation-agent-suite/.runtime/live`입니다. 터미널에 출력된 일회용 접속 링크를 브라우저에서 엽니다. 이 링크를 보고서·공유 문서에 복사하지 않습니다. 이미 열려 있는 작업실에서 접속 링크로 이동한 경우도 `hashchange`를 통해 토큰을 제거하고 교환합니다.

별도 검증 저장소를 사용할 때는 포트와 데이터 디렉터리를 함께 지정합니다. `--no-runner`는 AI를 실행하지 않고 화면·기록·검토를 확인할 때 사용합니다. 이 옵션에서 대기열을 생성해도 모델 작업은 시작되지 않습니다.

```sh
.venv/bin/python projects/presentation-agent-suite/scripts/presentation_console.py \
  --port 4318 \
  --data-dir projects/presentation-agent-suite/.runtime/local-check \
  --no-runner
```

같은 데이터 디렉터리에 두 서비스를 동시에 실행할 수 없습니다. 서비스가 재시작되면 중단된 실행 기록을 복구하고, 사용자가 재개를 선택할 수 있게 합니다. 지원되는 제작 방식과 AI·렌더 도구의 준비 상태는 화면의 **AI 연결과 준비 상태**에서 확인합니다. 준비되지 않은 제작 방식은 진행 불가 사유를 표시합니다.

## 처음 시작하기

1. 페이지가 열리면 준비된 AI와 기존 로그인을 자동으로 찾습니다. 자동 연결 조건을 충족한 도구를 기본 선택하고, 여러 도구가 준비되면 함께 표시합니다.
2. 로그인이 필요하면 **AI 연결**의 공식 절차를 따르고 **다시 탐색**을 누릅니다. 작업실에 비밀번호나 API 키를 붙여넣지 않습니다. AI 도구 설치 링크는 선택적 추가 안내입니다. 실제 확인되지 않은 상태는 준비 완료로 표시하지 않으며 Claude Code의 제작 검증 대기 표시도 유지합니다.
3. **새 프로젝트**에서 첫 이야기를 적고 AI·모델·추론 설정, 자료 활용 범위와 참고 파일을 선택합니다. 이름은 선택 사항입니다.
4. 생성한 프로젝트에 파일을 모두 첨부한 뒤 첫 의도 작업을 실행합니다. 첨부가 실패하면 실행을 멈추고 이미 만든 프로젝트와 첨부를 보존합니다. 최신 입력을 확인한 뒤 직접 이어서 진행합니다.

프로젝트의 **AI 설정**은 다음 작업부터 적용합니다. 설정을 열 때 기존 모델·추론 설정을 보존하며, AI를 바꾸면 먼저 기본값으로 돌아가고 지원되는 모델과 추론 옵션을 새로 선택할 수 있습니다. 작업이 실행 중이거나 답변을 기다리는 동안은 AI를 변경할 수 없습니다. 브라우저는 선택한 AI 이름만 로컬 설정에 저장하고 대화·자격 정보를 저장하지 않습니다.

## 화면과 동작

- 프로젝트 생성·전환, 자료 범위 선택, 실제 파일 첨부.
- 대화와 질문 답변, 단계별 입력·출력, 승인·수정 요청, 실행·취소·재개.
- 서버가 계산한 전체 진행률·단계별 진행률·완료 단위·재작업 이유.
- 주장·근거 및 연구 원문, 실행 이력, 파일 버전·해시·다운로드.
- SSE 이벤트 연결과 마지막 순번 이후 재연결. 세션이 만료되면 자동 재연결을 멈추고 대화 초안을 보존한 채 새 접속 링크를 안내. 실패한 승인 요청을 자동 재실행하지 않음.
- 제공자가 요청한 실행 권한과 업무 결과 승인을 별도 표시. 실행 승인은 이번 요청의 허용·거절로 제한.
- HTML·SVG는 실행하지 않고 원문으로 표시. PNG·JPEG 등은 MIME 확인 후 이미지, PDF는 제한된 프레임으로 표시.

## 세 단계의 입력·출력과 체크포인트

| 단계 | 입력 | 출력 | 체크포인트 |
|---|---|---|---|
| ① 의도·구조 확정 | 요청·대화·첨부·청중·목적·제약 | 의도 계약, 페이지 구성, 조사 요구, 성공 기준 | G1: 사용자가 현재 의도·구성 버전 승인 |
| ② 리서치·분석 | 승인된 의도, 자료 범위, 조사 요구 | 주장·출처·계산·한계, 분석 결과, 페이지별 메시지 | G2: 사용자가 현재 연구 결과와 한계 승인 |
| ③ 제작·검증·출고 | 승인된 메시지·근거, 디자인 요구 | 디자인 방향, 페이지·노트, 렌더, 검사 기록, 최종 PPTX | G3: 디자인 방향 승인 → G4: 자동 검사 → G5: 독립 시각 검토·출고 |

파일 목록에서 원문, 버전, 내용 해시를 확인할 수 있습니다. 검토 화면은 G1의 목적·청중·구성, G2의 분석·주장·가정·한계, G3의 디자인 방향과 실제 미리보기를 보여줍니다. 현재 검토 묶음의 유효 파일에 연결된 내용을 확인할 수 있을 때 해당 버전의 승인 버튼이 활성화됩니다. 열람 중 대상 버전이 바뀌면 이전 열람 상태로 승인하지 않습니다. 승인·질문 답변·대화 전송 이후 실행 가능한 다음 작업은 서비스가 대기열로 연결합니다. 미해결 질문이나 필수 승인이 있으면 먼저 검토함에서 처리합니다.

G1~G3는 업무 결과 승인입니다. AI 도구의 실행 권한 요청은 대화 기록 위의 **다음으로 가기 전에 확인할 내용**에 별도로 표시되며, 이번 요청에 대한 허용·거절만 전달합니다. 업무 승인을 대신 만들거나 지속적인 실행 권한으로 확장하지 않습니다. 파일 변경 요청에는 같은 실행 항목의 경로·변경 종류·이동 대상·제공된 diff를 표시합니다. 시도 폴더 안/밖/미확인, 이벤트 누락과 표시 한도에 따른 생략·잘림을 구분합니다. 경로가 확인됐다는 표시 자체로 자동 허용하지 않습니다.

연결된 MCP 도구의 추가 정보 요청은 도구 이름과 원문 메시지를 표시합니다. 객체형 양식의 필수 항목, 문자열, 단일 선택, 예·아니요 입력을 지원하며 실제 응답은 `action`과 구조화된 `content`로 전달합니다. 지원하지 않는 입력 조건이나 외부 주소 방식에서는 요청 거절·취소를 명시적으로 전달할 수 있습니다. 거절·취소에는 작성 중인 입력값을 포함하지 않습니다.

## 진행률과 재작업

전체 진행률은 서버가 **현재 유효한 완료 단위**의 배점을 합산해 계산합니다. 실행 시간이나 AI의 자기보고로 숫자를 올리지 않습니다.

| 완료 항목 | 배점 | 기본 누적 완료율 |
|---|---:|---:|
| 의도·구성 작성 / G1 승인 | 15 / 5 | 20% |
| 조사 요구 처리 / 분석·스토리 / G2 승인 | 20 / 10 / 5 | 55% |
| 디자인 방향 / G3 승인 | 8 / 2 | 65% |
| 페이지·발표 노트 | 20 | 85% |
| G4 자동 검사 | 10 | 95% |
| G5 독립 검토·출고 | 5 | 100% |

상단의 전체 비율과 각 단계 내부 완료율을 구분합니다. 다섯 체크포인트는 서버 완료 단위와 현재 검토 요청을 읽어 표시하며, 대기·완료·재검증·실패를 구분합니다. **지금 할 일**은 질문 답변, 결과 검토, 진행·재개 또는 최종 다운로드로 연결됩니다. **계산 기준·변경 이유 보기**에서 완료 단위와 무효화 사유를 확인합니다. 조사 처리 완료는 주장 입증과 별개입니다. 현재 후보의 필수 검사와 출고 기록이 모두 유효할 때만 100%가 됩니다. 과거 프로젝트에 계산 기준이 없으면 당시 완료 기록과 진행률 미계측을 함께 표시합니다.

제작 중에는 별도로 **슬라이드 작성 X/N**을 표시합니다. 최신 `design_build` 시도의 `job.execution.pages`에서 서버 체크포인트 검사를 통과한 페이지 수를 읽으며, 최종 PPTX 파일 검사나 출고 승인을 뜻하지 않습니다. 전체 비율은 이 수치로 올리지 않습니다. 새 시도는 첫 체크포인트부터 확인하고 이전 시도의 숫자를 이어받지 않습니다. 실패·중단·입력 변경 후의 수치는 마지막 시도 기록으로 표시하며, G4가 채택한 작성 기록은 이 표시에서 제외합니다.

**수정 요청**에는 두 범위가 있습니다.

- **단계 전체:** 선택한 단계와 영향을 받는 후속 결과를 다시 확인합니다. 제작 단계 전체 수정은 디자인 방향 승인 G3도 다시 확인합니다.
- **페이지·발표 노트 선택:** 제작 단계의 **페이지별 수정**, 또는 원문 상세창의 **이 페이지 수정 / 이 발표 노트 수정**으로 시작합니다. 현재 페이지 완료 단위에 연결된 유효한 파일을 선택합니다. 선택한 결과와 연결된 최종 검사·출고를 재작업하고, G3와 영향을 받지 않은 다른 페이지는 유지합니다. 공통 노트가 여러 페이지에 연결되어 있으면 선택창에 영향 페이지를 함께 표시합니다.

페이지 선택이 비어 있거나 선택한 파일이 무효화되어도 전체 단계 수정으로 자동 전환하지 않습니다. 수정창을 연 뒤 상태가 바뀌면 서버가 충돌을 반환하고 최신 결과를 다시 표시합니다. 대상을 확인한 뒤 직접 다시 제출합니다. 실패 응답에서 결과가 불확실한 동일 요청을 재시도할 때는 operation ID를 유지합니다.

## 최종 결과와 파일 찾기

완료된 실행의 출고 기록, 현재 후보, PPTX ID와 SHA, 전체 미리보기 SHA, 검토 기록이 현재 유효한 파일에 연결될 때 **PPTX 다운로드**를 표시합니다. 임의의 최신 파일명으로 출고본을 추정하지 않습니다. 현재 후보에 속한 페이지 렌더만 갤러리에 표시하고 발표 노트와 검사 기록을 함께 엽니다.

**단계와 결과**는 핵심 결과를 먼저 표시합니다. 많은 입력 원문과 세부 결과는 펼쳐 볼 수 있고, 무효화된 결과는 이전 버전으로 구분합니다. **근거와 분석**에서는 입증된 주장·가정과 제안·확인 필요를 나눠 볼 수 있습니다. 원문 위치 확인과 주장 해석의 입증은 서로 다른 상태입니다.

키보드로 Tab을 이동하며 사용할 수 있습니다. 질문 답변은 대화 기록 안에 숨기지 않고 별도 영역에 표시합니다. 대화는 ⌘/Ctrl+Enter로 전송하고, 정보 탭은 방향키·Home·End로 이동하며, 상세창은 Escape로 닫습니다.

## CLI와 웹의 같은 기록 사용

`presentation_v2.py`는 실행 ID와 데이터 디렉터리로 같은 SQLite 기록을 조회하고 변경합니다. 웹 서비스에 지정한 `--data-dir`와 CLI의 `--data-dir`가 같아야 동일한 작업을 가리킵니다. 기존 `presentation_agents.py`도 작업 폴더에 `run.json`이 있으면 v2 참조를 읽어 같은 엔진으로 연결합니다. `run.json`이 없는 기존 프로젝트는 기존 파이프라인 경로를 유지합니다.

```sh
.venv/bin/python projects/presentation-agent-suite/scripts/presentation_v2.py list
.venv/bin/python projects/presentation-agent-suite/scripts/presentation_v2.py status RUN_ID
.venv/bin/python projects/presentation-agent-suite/scripts/presentation_v2.py progress RUN_ID
.venv/bin/python projects/presentation-agent-suite/scripts/presentation_v2.py events RUN_ID --after 0
.venv/bin/python projects/presentation-agent-suite/scripts/presentation_v2.py validate RUN_ID
```

`RUN_ID`는 `list`에서 확인한 실제 ID로 바꿉니다. 상태 변경에는 현재 `revision`과 이 요청에만 사용하는 `operation-id`가 필요합니다. 아래 `7`은 예시이며 실제 현재 개정 번호로 바꿉니다.

```sh
.venv/bin/python projects/presentation-agent-suite/scripts/presentation_v2.py resume RUN_ID \
  --expected-revision 7 --operation-id example-resume-001
```

AI 실행 서비스가 같은 데이터 디렉터리로 켜져 있으면 큐 감시기가 약 1초 간격으로 CLI의 `run` / `resume` 요청을 감지합니다. 앞선 모델 작업이 실행 중이면 순서를 기다립니다. 서비스를 실행하지 않았거나 `--no-runner`로 시작했다면 AI는 실행되지 않습니다. CLI의 `cancel`은 같은 실행의 취소 상태를 기록하며, 실행 중인 제공자 연결의 중단 동작은 서비스와 연결해 확인해야 합니다.

`presentation_v2.py`에서 `request-changes RUN_ID --stage design --reason "변경 이유" --expected-revision N --operation-id UNIQUE_ID`로 단계 전체 수정을 기록할 수 있습니다. CLI에는 웹 사용자 승인 명령이 없습니다. `import-legacy`는 원본을 변경하지 않고 이전 프로젝트를 호환 보기로 등록합니다.

기존 작업 폴더 경로를 사용하는 명령도 지원합니다. 아래 `WORKSPACE`는 서비스가 참조하는 `run.json`을 가진 실제 작업 폴더 경로로 바꿉니다. 이 파일의 `run_id`와 `state_store`가 같은 실행과 저장소를 가리켜야 하며, 과거 승인이나 기록을 새 승인으로 복사하지 않습니다.

```sh
.venv/bin/python projects/presentation-agent-suite/scripts/presentation_agents.py status WORKSPACE
.venv/bin/python projects/presentation-agent-suite/scripts/presentation_agents.py progress WORKSPACE
.venv/bin/python projects/presentation-agent-suite/scripts/presentation_agents.py events WORKSPACE --after 0
.venv/bin/python projects/presentation-agent-suite/scripts/presentation_agents.py resume WORKSPACE \
  --expected-revision 7 --operation-id example-workspace-resume-001
```

이 경로에서도 `run`, `cancel`, `resume`, `request-changes`가 같은 엔진과 서비스 대기열을 사용합니다. `request-changes`에는 반복 가능한 `--artifact-id`를 제공하므로 페이지·발표 노트 일부만 지정할 수 있습니다. 현재 유효한 파일 ID를 사용하며 옵션을 생략하면 단계 전체 수정입니다.

```sh
.venv/bin/python projects/presentation-agent-suite/scripts/presentation_agents.py request-changes WORKSPACE \
  --stage design --reason "첫 페이지 제목과 발표 노트 수정" \
  --artifact-id CURRENT_PAGE_ARTIFACT_ID --artifact-id CURRENT_NOTES_ARTIFACT_ID \
  --expected-revision 7 --operation-id example-page-change-001
```

## API 연결 계약

모든 웹 변경은 같은 출처의 세션 쿠키와 `X-CSRF-Token`을 사용합니다. 변경 명령은 `operation_id`, `expected_revision`, `command`, `payload`를 전달합니다. `409` 응답에서는 최신 상태를 조회하며 승인을 자동 재제출하지 않습니다.

| 경로 | 역할 |
|---|---|
| `/api/v2/session` | 일회용 접속 정보 교환, 기존 브라우저 세션 확인 |
| `/api/v2/capabilities` | 실제 AI 목록·선택 기본값·설치와 로그인 상태 |
| `/api/v2/capabilities/discover` | AI 자동 탐색 시작·현재 상태 (POST·CSRF), `refresh: true`로 재탐색 |
| `/api/v2/capabilities/refresh` | 선택한 AI의 설치·로그인 다시 확인 (POST·CSRF) |
| `/api/v2/runs` | 프로젝트 목록·생성, `execution` 선택 |
| `/api/v2/runs/{id}` | 현재 입력·출력·상태·진행률 스냅숏 |
| `/api/v2/runs/{id}/commands` | 대화, 답변, 승인, 수정 요청, 실행·취소·재개 |
| `/api/v2/runs/{id}/events?after=N` | 순번 기반 SSE, 재연결 후 누락 기록 전달 |
| `/api/v2/runs/{id}/attachments` | 실제 파일 multipart 업로드 |
| `/api/v2/runs/{id}/artifacts/{artifact_id}` | 프로젝트에 속한 원문·미리보기·다운로드 |

페이지가 인증되면 자동 탐색을 한 번 시작합니다. `discovery.status`가 `RUNNING`인 동안만 1초 간격으로 확인하며, 45초가 지나면 자동 확인을 멈추고 재탐색을 안내합니다. 일부 AI가 먼저 준비되면 바로 프로젝트를 시작할 수 있습니다. 세션 만료나 화면 전환은 해당 확인 요청을 종료하며, 늦은 응답으로 사용자의 선택을 덮어쓰지 않습니다.

자동 선택은 서버가 `ready: true`와 `auto_connect: true`를 모두 확인한 도구에서 저장된 선호, 서버 추천, 첫 준비 도구 순으로 정합니다. `ready: true`이고 `auto_connect: false`인 도구는 로그인·모델·과금 안내를 확인한 뒤 명시적으로 선택할 수 있습니다. 준비되지 않은 도구에 대한 설치 안내는 선택적 추가 도구 안내이며, 계정 연결은 공식 로그인 절차를 사용합니다.

프로젝트 생성의 `execution`은 `{provider, model, effort}`입니다. 새 프로젝트와 기존 프로젝트 모두 실제 AI 모델 목록과 모델별 추론 옵션을 선택할 수 있고, `null`은 도구 기본 설정입니다. 목록에 없는 기존 설정은 그대로 보존할 수 있지만 새로운 미지원 조합은 전송하지 않습니다. 실행의 `configure_provider` 명령도 같은 형태이며 작업 중에는 변경할 수 없습니다. 자동 탐색은 현재 프로젝트의 실행 설정을 변경하지 않습니다.

도구 답변 `provider_answer`는 `request_id`, `response`와 함께 `question_id`, `job_id`, `provider_id`를 모두 전달하여 현재 질문과 실행에 결속합니다.

페이지별 수정 payload는 다음 형식입니다. `artifact_ids`에는 현재 선택한 `page` 또는 `notes` 결과물 ID만 넣습니다.

```json
{
  "stage": "design",
  "reason": "첫 페이지 제목을 간결하게 수정",
  "artifact_ids": ["현재-페이지-산출물-ID"]
}
```

## 검증

프로젝트의 기존 JavaScript 검증 패턴과 이번 구현 요청의 회귀 검사 요구에 따라 Node 기본 테스트 러너를 사용합니다. 새 테스트 의존성은 없습니다.

```sh
node --test projects/presentation-agent-suite/console/tests/*.test.mjs
node --check projects/presentation-agent-suite/console/app.mjs
```

순수 계약 검사는 XSS 이스케이프, 미계측 진행률, 프로젝트별 파일 URL, 승인 충돌, 세션 인증, 첨부 전송, 현재 페이지 선택과 재작업 범위를 검증합니다. 실제 연결·렌더·승인·재작업·390/1024/1440px 검증은 v2 서버와 브라우저를 연결한 통합 검사에서 수행합니다. 단위 검사 통과를 실제 UI 검증으로 간주하지 않습니다.

이번 제품 UI 변경의 단위·계약 검사 67개와 모듈 구문 검사가 통과했습니다. 이 기록은 화면 검증을 대신하지 않습니다. 실제 로컬 서버 연결·390/1024/1440px 렌더·질문·검토·첨부·다운로드 검사는 [출시 검증 기록](../../../docs/local-mvp/VERIFICATION.md)에서 별도로 확인합니다.
