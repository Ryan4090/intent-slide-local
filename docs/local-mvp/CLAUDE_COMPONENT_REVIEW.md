# Claude Code 검증용 실행 파일 검토

2026-09-06 · third-party-component-review · **PARTIALLY_VERIFIED**

목적: Intent-Slide의 두 번째 공식 CLI 연결을 실제 프로세스로 검사한다. 사용자의 GitHub·본인 정액제 도구 기반 출시 요청과 이 작업의 사전 승인 범위를 적용한다. 제품 사용자에게 CLI 자동 설치를 강제하지 않는다.

## 확인한 대상

- **VERIFIED:** Anthropic 공식 배포 `2.1.263`, darwin-arm64, manifest commit `37ae3f38d765199d54a6913cd61c6c9ad8576cc6`.
- **VERIFIED:** 199,257,984 bytes, SHA-256 `ef5d2909c8af49f31ab6d5487e90316777bc2fac170adfe8160716caa8aaf4f9`; HTTPS로 조회한 공식 manifest와 파일 해시 일치.
- **VERIFIED:** `codesign --verify --strict` 성공. 서명 표시 `Developer ID Application: Anthropic PBC (Q6L2SF6YDW)`, Apple Root CA 체인, hardened runtime.
- **UNVERIFIED:** GPG manifest 서명은 gpg 미설치로 확인하지 않았다. 폐쇄형 바이너리 전체 소스·네트워크 동작의 독립 감사도 수행하지 않았다. 해시·서명 검증을 기능 안전성의 증명으로 해석하지 않는다.

## 설치·권한 판단

공식 설치 스크립트 원문을 읽었으며 실행하지 않았다. 스크립트는 사용자 홈에 다운로드한 뒤 바이너리의 install 명령으로 launcher·shell 연동을 구성하고 다운로드 파일을 지운다. 이 검증에서는 내려받은 **비변형 파일**을 저장소 `.runtime/component-review/`에서 직접 실행한다. 전역 launcher, shell 설정, MCP, plugin은 추가하지 않는다.

본인 로그인은 공식 CLI가 관리한다. 인증 상태 명령의 결과 중 로그인 방식·여부만 검사하며 이메일·토큰·계정 식별자를 기록하지 않는다. 자격 증명 복사·추출·프록시 인증을 구현하지 않는다. 실제 모델 호출은 다음 별도 검사다.

검증 프로세스에만 문서화된 `DISABLE_AUTOUPDATER=1`을 적용해 검사 도중 배포 버전이 바뀌지 않게 한다. CLI 자체는 공식 약관에 따른 별도 제품이며 Intent-Slide가 재배포하거나 자체 코드의 MIT 고지를 적용하지 않는다.

대안은 사용자가 이미 설치한 CLI를 사용하는 것이다. 현재 PATH 및 확인한 표준 설치 위치에 없으므로 동일 공식 바이너리를 작업 폴더에 준비했다. native login·작업 프로세스는 CLI의 사용자 저장소를 사용할 수 있으며, 해당 내부 구현 전체를 검증했다는 주장은 하지 않는다.

**UNVERIFIED:** 롤백은 이 검증용 실행 파일과 캐시만 제거하는 경로가 있으나 실행하지 않았다. 사용자 기존 인증·설정은 롤백 대상으로 삭제하지 않는다.

근거: [공식 설치와 무결성 문서](https://code.claude.com/docs/en/setup), [CLI 명령](https://code.claude.com/docs/en/cli-reference), [제3자 제품에서의 비변형 CLI 사용](https://code.claude.com/docs/en/legal-and-compliance). 공식 manifest와 installer는 `.runtime/component-review/`에 로컬 검토 자료로 보존하며 배포에서 제외한다.
