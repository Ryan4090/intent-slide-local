# 로컬 AI 호환 범위

2026-09-06 · **PARTIAL**. 실행기를 바꿀 수 있는 공통 계약을 제공하지만 모든 AI·모델·운영체제의 전체 제작을 검증한 것은 아닙니다. 연결 준비, 실제 모델 실행, G5 이미지 검토를 구분합니다. 기존 Codex 제작 증거는 [검증 기록](VERIFICATION.md)에 있습니다.

| 실행기 | 연결 방식 | 확인한 범위 | 남은 경계 |
|---|---|---|---|
| Codex | 내장 0.153.4 native CLI의 `app-server` | **VERIFIED**: Mac ARM64 내장 런타임·구독 준비 응답. 이전 v0.1.0은 macOS 3장 G1–G5·100%와 공식 npm CLI 파일 생성·읽기를 검증 | 새 번들의 플랫폼별 모델 제작 증거는 VERIFICATION.md에 별도 기록. Windows 전체 제작·모든 모델·요금제는 **UNVERIFIED** |
| Claude Code | 설치된 CLI의 stream-json | **PARTIALLY_VERIFIED**: 초기화·fixture 계약 | Beta. 검증 환경에 해당 구독이 없어 실제 모델 제작은 **UNVERIFIED** |
| Gemini CLI | 설치된 `gemini --acp` | **PARTIALLY_VERIFIED**: 0.54.4 옵션·ACP 초기화 확인. 실제 `session/new`는 `AUTH_REQUIRED`로 **BLOCKED**. 로그인 변경·모델 호출 없음 | 실제 제작 **UNVERIFIED**. 이 버전의 도구 결과는 표시용 text/diff여서 현재 G5 이미지 바이트 증거를 충족하지 못함 |
| OpenCode | 설치된 `opencode acp` | **PARTIALLY_VERIFIED**: 공식 프로토콜과 독립 JSON-RPC fixture 계약 | 이 환경에 CLI가 없어 실제 연결·모델·G5는 **UNVERIFIED** |
| OpenAI 호환 API·Ollama | 별도 모델 API | 확장 후보이며 현재 제품 실행기로 미구현 | 모델 URL만으로 파일·도구·승인·세션·G5 계약이 생기지 않음 |

Gemini와 OpenCode는 사용자가 직접 선택합니다. `ready=true`는 무프롬프트 ACP 초기화·세션 생성·모델 목록 확인을 뜻하며 구독 포함 여부의 증명이 아닙니다. 두 실행기의 `auto_connect`는 `false`, 인증 모드는 `native_unverified`입니다. 기존 도구의 로그인·모델·과금 설정을 사용하며, 구독 포함 여부는 해당 제공자에서 확인해야 합니다. 제품이 로그인 정보를 읽거나 다른 API 과금 방식으로 전환하지 않습니다. 공식 실행 방식은 [Gemini ACP](https://geminicli.com/docs/cli/acp-mode/)와 [OpenCode ACP](https://opencode.ai/docs/acp/)를 따릅니다.

## ACP 실행 계약

- **VERIFIED — fixture:** ACP v1 JSON-RPC stdio, 독립 stage 세션, 스트리밍, 실제 workspace 출력파일, 승인 ID `0`, 일회성 허용·거절, 중복 응답 거부, 취소 및 응답하지 않는 자식 프로세스 종료. 영구 허용은 전송하지 않습니다. 요청의 짧은 제한시간은 최대 60초이며 모델 턴은 별도 실행시간 제한과 사람의 승인 대기를 구분합니다. [ACP 초기화](https://agentclientprotocol.com/protocol/v1/initialization), [턴과 취소](https://agentclientprotocol.com/protocol/v1/prompt-turn), [도구와 승인](https://agentclientprotocol.com/protocol/v1/tool-calls).
- **VERIFIED — fixture:** `configOptions`가 제공되면 모델·추론 옵션은 실제 목록에서만 고르고 변경 응답의 `currentValue`까지 확인합니다. 구형 `models` 목록은 `session/set_model`의 성공 응답을 요구합니다. 설치된 Gemini 0.54.4의 공개 코드에서도 이 구형 명령과 빈 성공 응답을 확인했습니다. 성공 응답을 실제 과금 모델 관측으로 확대하지 않습니다. 목록에 없는 값·미지원 명령은 모델 프롬프트 전 차단합니다. 없는 추론 강도를 만들어 표시하지 않습니다. [세션 설정 옵션](https://agentclientprotocol.com/protocol/v1/session-config-options).
- **VERIFIED — fixture:** 클라이언트 파일 기능은 해당 세션 workspace 안의 제한된 UTF-8 읽기만 허용합니다. 경로 이탈·심볼릭 링크·대용량·바이너리 읽기와 클라이언트 쓰기·터미널 실행은 거부합니다. POSIX의 중간 디렉터리 교체도 열린 디렉터리와 `no-follow`로 차단합니다. 이는 **네이티브 CLI 자체 도구의 OS sandbox를 보장하지 않습니다**. 기존 CLI의 권한 모드·MCP·확장은 그 도구의 설정을 따릅니다. 읽기 전용 세션을 쓰기 가능 모드로 바꾸거나 승인 회피 옵션을 추가하지 않습니다.
- **VERIFIED — fixture:** 이미지 관측은 같은 턴의 성공한 read 도구, workspace 경로, 읽기 시작과 완료 사이의 SHA, 실제 반환된 PNG/JPEG 바이트가 모두 일치할 때만 발행합니다. 텍스트로 “읽었다”고 한 결과·실패·재생·중복·완료 시 경로 변경·파일 변경/삭제는 관측이 아닙니다. `promptCapabilities.image`는 입력 이미지 능력이고 `image_view_verified`와 별개입니다. Gemini 0.54.4의 `toToolCallContent`는 이미지 `llmContent`를 전송하지 않으므로 이 제한을 추론으로 메우지 않습니다.

**UNVERIFIED:** Gemini/OpenCode 실제 모델 실행, native Windows의 ACP 전체 실행·파일 경계·G5. 공통 전송 계층은 Windows Job Object와 POSIX 프로세스 그룹을 사용하지만 플랫폼별 실제 실행 증거를 fixture로 대신하지 않습니다. 임의 Windows `.cmd`/`.bat` 실행은 허용하지 않으며, 검토되지 않은 launcher는 차단될 수 있습니다. 실행기·모델이 이미지 바이트 증거를 제공하지 않으면 자동 G5 완료를 보장하지 않습니다.

## 확장 경계와 확인 방법

웹 설정은 `provider`, `model`, `effort`만 받습니다. Gemini/OpenCode 명령은 고정된 등록 항목이며 사용자가 임의 shell 명령이나 argv를 등록하는 기능은 없습니다. 새 ACP 실행기는 고정 프로필·공식 문서·프로토콜/승인/취소/파일·이미지 회귀를 추가하고 실제 실행을 별도로 확인해야 합니다. ACP의 공통 규격은 구현 기반이지 모든 에이전트의 동일한 동작 보증이 아닙니다. [ACP 세션 생성](https://agentclientprotocol.com/protocol/v1/session-setup).

OpenAI 호환 API 또는 Ollama를 추가하려면 제품이 도구 실행, 일회성 승인, 파일 격리, 세션 저장, 취소, 실제 이미지 입력·검토 증거를 맡는 별도 실행 계층이 필요합니다. Ollama의 도구 호출·이미지 지원도 모델별 능력을 확인해야 하며, 인증된 CLI 구독을 임의 API로 대체하지 않습니다. [Ollama 호환 API](https://docs.ollama.com/api/openai-compatibility), [도구 호출](https://docs.ollama.com/capabilities/tool-calling), [이미지](https://docs.ollama.com/capabilities/vision).

ACP 대상 회귀는 실제 AI 대신 독립 Python JSON-RPC 프로세스를 사용합니다. **VERIFIED: 18 tests, 3.648s, OK**. 모델 과금이나 인증 자료 접근은 없었습니다. 실패 재현 후 확인한 모델 선택·허가 요청 순서·이미지 변경 경계가 포함됩니다. 기존 전체 회귀와 중복 합산하지 않습니다.

```sh
PYTHONPATH=projects/presentation-agent-suite/src .venv/bin/python -B -m unittest discover -s projects/presentation-agent-suite/tests -p 'test_v2_acp_provider.py' -v
```

설치와 실행 방법은 [설치 문서](INSTALLATION.md)를 따릅니다. 이 문서는 실행기 호환 계약이며 새 전역 도구·계정·구독 구매·API 키를 요구하지 않습니다.
