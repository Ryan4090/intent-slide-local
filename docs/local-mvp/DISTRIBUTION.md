# 제품 배포 묶음과 출처

기존 비공개 저장소의 Git 이력과 과거 프로토타입을 공개하지 않습니다. 제품에 필요한 허용 경로의 **커밋된 파일 바이트만** 배포 ZIP에 넣으며, 새 공개 저장소는 이 스냅샷에서 별도 Git root로 시작합니다. 아래 스크립트는 원격 저장소 생성·push·commit을 수행하지 않습니다.

## 두 종류의 manifest

- `vendor/slidemaster-manifest.json`: SlideMaster commit `450bd21305a175fa009213c4c2fb4811f0d322dc`에서 가져온 12,376개 파일의 출처와 SHA-256입니다. 복사는 해당 Git 객체에서만 수행했으며 개인 작업 트리 변경은 가져오지 않았습니다. 이는 **과거 기준의 출처 기록**입니다. 이후 제품 코드 변경을 오류로 취급하지 않습니다.
- ZIP의 `release-manifest.json`: 실제 배포에 선택한 제품 commit, 모든 포함 파일의 경로·크기·mode·SHA-256과 upstream 이후 변경된 파일, 별도 권리 때문에 제외한 파일의 경로·원본 SHA·사유를 기록합니다. 배포의 파일 무결성과 upstream 출처를 구분합니다.

원래 레이아웃은 엔진과 제작 owner의 상대 경로를 유지하기 위해 보존합니다. 개인 프레젠테이션 자료를 가져오기 위해 `projects/` 전체를 복사하는 방식은 사용하지 않습니다.

## 제품 commit에서 생성

```sh
# 먼저 출시 담당자가 제품 파일을 검토하고 commit한 뒤 실행
.venv/bin/python scripts/build_local_release.py --ref HEAD

# 위 명령이 반환한 경로 사용
.venv/bin/python scripts/build_local_release.py --verify .runtime/dist/Intent-Slide-COMMIT12.zip
```

기본 출력은 `.runtime/dist/Intent-Slide-<commit 앞 12자리>.zip`입니다. `--output`으로 저장소 안의 다른 경로를 지정할 수 있습니다. 아직 제품 파일을 포함하지 않은 commit은 필수 파일 누락으로 실패합니다. 누락된 파일을 현재 작업 트리에서 조용히 보충하지 않습니다.

같은 commit과 파일은 정렬·고정 시각·무압축 ZIP을 사용해 동일한 바이트를 생성합니다. 발행은 저장소 내부 임시 파일에서 검사 후 원자적으로 새 파일을 만들며, 내용이 다른 기존 ZIP은 덮어쓰지 않습니다. 이미 동일한 ZIP이 있으면 `UNCHANGED`를 반환합니다. ZIP 파일 해시는 전송 무결성 검사에 쓸 수 있지만 제작자의 디지털 서명은 아닙니다.

## 포함과 제외

| 포함하는 제품 경로 | 제외하는 자료 |
|---|---|
| 실행 wrapper, 설치·배포 스크립트, 고정 의존성, `.gitignore`, 제품 안내 | 기존 root `src/`, `web/` 합성 프로토타입 |
| `.claude/skills/`, `.codex/skills/`, `docs/rules/` | root `data/`, `output/`, `artifacts/`, `reports/`, `docs/program/` |
| suite의 `src`, `scripts`, `console`, `agents`, `tests` | 개인 프로젝트와 실행 DB·대화·후보·최종 고객 산출물 |
| `vendor/`, `licenses/`, `docs/local-mvp/`, 제품 설치 테스트 | 모든 `.git`, `.venv`, `.runtime`, `node_modules`, `__pycache__`, `.env*`, 인증 파일 |

허용 경로 안에서도 symlink, Git submodule, 비정상 경로와 인증 파일명을 거절합니다. 구체적인 허용·제외 목록은 [배포 스크립트](../../scripts/build_local_release.py)의 `ALLOW`, `EXCLUDE`, `allowed_path`가 소유합니다. NAVER 브랜드·IR 템플릿과 NAVER 아이콘, Jangpm 선택 캐릭터 등 19개 파일은 별도 권리 경계 때문에 제외합니다. NAVER 선택 항목은 catalog에서도 제거하며 Jangpm 레이아웃은 이미지 없이 유지합니다. 새 공개 저장소에는 검증된 ZIP의 제품 내용만 가져옵니다. 기존 `.git` 디렉터리나 이력을 복사하거나 mirror push하지 않습니다.

## 의존성과 라이선스

`requirements-local.txt`는 9개 직접 런타임 의존성입니다. `requirements-local.lock`은 전이 의존성을 포함한 23개 패키지 버전과 공식 PyPI wheel 해시 612개를 고정합니다. 설치는 `--require-hashes --only-binary=:all:`로 실행하며 소스 빌드나 최신 버전 추정을 하지 않습니다. 여러 wheel 플랫폼의 해시가 있다는 사실은 여러 OS의 제품 지원을 뜻하지 않습니다. 실제 설치 검증은 macOS Apple Silicon/Python 3.12입니다.

잠금 갱신은 명시적인 의존성 변경입니다. 버전 해석, 공식 release 해시, 실제 설치, 전이 의존성과 라이선스 고지, 관련 회귀를 함께 검토한 새 commit으로 갱신합니다. 기존 lock을 실행 중 자동 수정하지 않습니다.

[제3자 고지](../../THIRD_PARTY_NOTICES.md)는 upstream MIT, Pretendard SIL OFL, geometry 자료의 Apache/MIT 및 Python 패키지별 조건을 구분합니다. PyMuPDF의 별도 AGPL/상용 라이선스 조건에 관해 이 배포 도구가 법적 호환성을 판정하지 않습니다. 공식 Codex·Claude Code 실행파일과 사용자 인증은 배포에 포함하지 않습니다.

현재 upstream manifest는 당시 checkout의 제작 owner를 가리킵니다. 개인 작업 트리에만 있던 Role/Lead 파서 변경은 포함되지 않았습니다. 과거 파일럿의 검증된 산출물과 새 제품 checkout에서의 재검증 가능성은 별개이며, owner나 검사 코드가 바뀐 후보는 현재 환경으로 G4를 다시 통과해야 합니다.
