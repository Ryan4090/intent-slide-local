# 설치·배포 검증 기록

2026-09-06 · **PARTIAL**. 이전 후보 ZIP의 재현성·파일 집합과 새 환경 설치를 확인했습니다. 새 제품의 실제 3장 제작은 G4/G5와 전체 100%까지 통과했고 제품 코드의 MIT 라이선스 선택도 완료됐습니다. 최종 ZIP과 원격 발행 확인은 별도 배포 단계이며 [전체 검증 현황](VERIFICATION.md)과 구분해 관리합니다.

| 주장 | 상태와 실제 증거 |
|---|---|
| upstream 원본만 복사 | **VERIFIED** — commit `450bd21305a175fa009213c4c2fb4811f0d322dc`의 12,376개 파일, 30,003,568 bytes를 Git 객체에서 복사하고 전 파일 SHA 비교 통과. 이후 제품 변경은 별도 provenance |
| 후보 ZIP에서 새 설치 | **VERIFIED** — 별도 제품 폴더에 추출한 뒤 새 `.venv` 생성. Python 3.12/macOS Apple Silicon에서 고정 23개 패키지 설치·`pip check`·READY 및 설치 명령 exit 0 확인 |
| 설치 재실행 | **VERIFIED** — `./intent-slide setup` 두 번째 실행에서 재설치 없이 준비 상태 확인 |
| 추출본 Codex 준비 진단 | **VERIFIED** — `doctor --provider codex --connect`에서 누락·버전 불일치 없음, `ready=true`, `auth_mode=subscription` 확인. 실제 파일 제작 완료와는 별개 |
| 추출본 서비스 시작·종료 | **VERIFIED** — 실제 native preflight 준비, 페이지 HTTP 200, 미인증 API 401, 인증 후 API 200·빈 작업 목록, 실행 capability 준비, 검사한 서버 종료 확인 |
| 설치·배포 회귀 | **VERIFIED** — 최신 21개 테스트 통과. 실제 Git tree→tar→ZIP 경로와 브랜드 제외·manifest 경계 포함. 설치·provider fixture는 실제 모델 증거로 해석하지 않음 |
| 제품 미포함 commit의 발행 차단 | **VERIFIED** — 기존 prototype HEAD에서 builder 실행이 필수 파일 누락으로 exit 2. 작업 트리로 대체하거나 ZIP을 발행하지 않음 |
| 동일 commit 반복 생성 | **VERIFIED** — 후보 commit `b3375d79d6d5f8427078a1b0318646424dfc0a4a`의 ZIP 생성·재생성 `UNCHANGED`, SHA 동일성 확인 |
| 후보 공개 범위 독립 감사 | **VERIFIED** — 배포 코드의 허용 함수를 재사용하지 않고 실제 ZIP의 파일 집합·경로·형식·개인자료 패턴·해시 검사. 아래 범위에서 문제 없음 |
| Intel Mac/Windows/Linux 설치 | **UNVERIFIED** — Apple Silicon 이외 실사용 검사는 하지 않음 |
| Claude 실제 모델 실행 | **UNVERIFIED** — 베타 범위. 인증·초기화 검사와 실제 모델 제작 완료를 구분 |

## 실제 후보와 독립 확인

후보 파일은 `Intent-Slide-b3375d79d6d5.zip`, SHA-256은 `99a4310bee19088dae1804b8eb41d79a94a7ffbd8a451285c6c5c2c928aa3f1e`입니다. 이 수치는 해당 commit의 후보에만 적용됩니다. **공개용 브랜드 파일 제외 전의 기술 검증 후보**이며 이후 문서·코드·라이선스 변경이 반영된 최종 공개 배포를 대신하지 않습니다.

- 총 12,449개 entry: 제품 파일 12,448개와 manifest 1개. 비압축 파일 합계 37,379,456 bytes.
- 독립 경로 분해·정규화 검사에서 절대 경로, traversal, 중복, macOS 대소문자 충돌, Git/runtime/인증 파일, 기존 root prototype·개인 프로젝트 경로 없음.
- 모두 일반 파일: mode `0644` 12,447개, `0755` 2개. 실행 권한 파일은 `intent-slide` wrapper와 upstream `text_fit.py`이며 네이티브 실행파일은 없음.
- 텍스트 12,388개에서 주요 인증값 형태와 개인 절대 경로를 발견하지 못함. 이 정적 패턴 검사는 모든 종류의 비밀 부재를 증명하는 검사는 아님.
- 바이너리 61개는 Pretendard OTF 6개, 참조 JPEG 45개, 도구·브랜드 PNG 10개. 모두 upstream 해시와 일치. PPTX/PDF/DB/개인 파일럿 산출물 없음.
- upstream MIT, Pretendard OFL, geometry 고지와 Python 의존성 고지 원문 44개 포함·해시 확인. 브랜드별 별도 재배포 허가는 미확인이며 법적 호환성 판단은 하지 않음.

최상위 `LICENSE` 파일은 이 과거 후보에 없습니다. 이후 사용자가 제품 코드의 MIT 적용을 승인했습니다. 최종 배포에서는 [MIT License](../../LICENSE)와 제3자 고지를 함께 포함하며 실제 포함 여부를 검사합니다.

이후 commit `244375d`에서 NAVER/Jangpm 관련 19개 파일을 공개 배포에서 제외하고 관련 카탈로그·참조와 manifest 경계를 보강했습니다. 최신 21개 회귀는 이 제외 규칙과 변조 거절을 포함합니다. **위 파일 수·SHA·fresh 설치 결과는 과거 후보에 한정하며 최종 배포본의 결과로 사용하지 않습니다.** 최종 ZIP은 해당 GitHub Release의 manifest·체크섬으로 식별합니다. 제외 사유와 조건은 [제3자 고지](../../THIRD_PARTY_NOTICES.md)를 따릅니다.

## 회귀와 수정 근거

```sh
.venv/bin/python -B -m unittest discover -s tests -p 'test_local_*.py' -v
```

최신 결과: **21 tests, 1.029s, OK**. 저장소 내부 venv, symlink·불완전한 환경 차단, 실패 시 READY 미기록, shell 없는 인자 전달, 정확한 파일 집합·권한·SHA, 브랜드 제외·manifest 변조 및 기존 파일 덮어쓰기 거부를 검사합니다. 기존 archive 권한 수정 당시의 17개 통과 로그는 과거 증거로 보존합니다.

provider 인스턴스를 다시 호출하던 doctor 오류, 공개 제품의 `.gitignore` 누락, Git `100644`가 tar에서 `0664`로 표현되는 실제 archive 오류를 각각 실패 재현 후 고쳤습니다. 파일 mode는 커밋된 Git tree만 소유하며 tar의 권한을 ZIP으로 전달하지 않습니다. 기존 ZIP 권한 허용 범위 `0644`·`0755`는 유지됩니다.

처음 가져온 upstream 파일과 라이선스 원문에는 기존 공백 형식이 있어 전체 신규 파일을 whitespace clean으로 주장하지 않습니다. 제품 변경 파일에 한정한 diff 검사가 통과했습니다.

의존성 고지는 공식 release 메타데이터와 다운로드 wheel의 원문을 수집했습니다. 23개 패키지의 고지 원문 44개를 포함하며 [목록과 해시](../../vendor/runtime-dependencies.json)를 보존합니다. 실행 로그·설치 wheel은 `.runtime/distribution-lock/`에 있고 제품 ZIP에서 제외합니다.

직접 확인한 로컬 근거는 `fresh-install.log`, `fresh-doctor.json`, `fresh-start.json`, `archive-mode-red.log`, `archive-mode-green.log`, `manifest-boundary-green.log`입니다. 원시 설치·실행 로그는 runtime에 보존하고 공개 제품 ZIP에는 포함하지 않습니다.

## 최종 발행 검증 절차

1. 최종 코드·문서·라이선스 변경을 새 commit에 반영한 뒤 ZIP 생성·반복 생성·독립 파일 집합 검사를 수행합니다.
2. 새 제품의 실제 G4·G5 완료 증거를 보존하고, UI 파일 선택기·브라우저 저장 위치 등 미검증 범위를 유지합니다.
3. 새 공개 저장소에는 검증된 제품 스냅샷만 가져오며 기존 비공개 Git 이력은 가져오지 않습니다.

문서 동결 시점에는 원격 발행·배포 다운로드 확인을 **UNVERIFIED**로 둡니다. 실제 실행한 결과는 해당 GitHub Release와 출시 작업이력에 기록하며, 최종 ZIP 자신의 SHA를 이 문서에 넣어 산출물이 순환 변경되게 하지 않습니다.

개인 API 키·인증 저장소·환경 설정 내용은 이 검사에 사용하거나 기록하지 않았습니다. 로컬 설치의 삭제/롤백 경로는 존재하지만 실제 삭제는 실행하지 않았으므로 롤백 검증은 **UNVERIFIED**입니다.
