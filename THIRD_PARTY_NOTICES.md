# 제3자 코드·글꼴·런타임 의존성 고지

Intent-Slide에 포함된 SlideMaster 코드의 고지와 설치되는 Python 의존성의 조건을 구분합니다. 저장소 전체와 모든 의존성을 하나의 MIT 라이선스로 단정하지 않습니다.

## 포함된 upstream 자료

- SlideMaster: commit `450bd21305a175fa009213c4c2fb4811f0d322dc`, [MIT 원문](licenses/slidemaster-MIT.txt).
- Pretendard: [SIL OFL 1.1 원문](.claude/skills/ppt-master/assets/fonts/Pretendard/LICENSE.txt). 전역 글꼴 설치는 자동 수행하지 않습니다.
- DrawingML preset 자료: [출처와 변경 고지](.claude/skills/ppt-master/scripts/pptx_shapes/data/NOTICE.md), [Apache 2.0](.claude/skills/ppt-master/scripts/pptx_shapes/data/LICENSE-APACHE-2.0.txt), [Open XML SDK MIT](.claude/skills/ppt-master/scripts/pptx_shapes/data/LICENSE-OPEN-XML-SDK-MIT.txt).
- 다른 canonical skill의 포함된 LICENSE·출처 파일은 원본과 함께 보존합니다. [파일별 upstream manifest](vendor/slidemaster-manifest.json)는 복사 출처이며 이후 제품 변경을 금지하는 검사가 아닙니다.
- NAVER 로고와 이를 사용하는 brand/deck 패키지, NAVER Simple Icon은 공개 배포에서 제외합니다. [공식 NAVER 브랜드 가이드](https://www.navercorp.com/company/brandGuide)의 템플릿·다운로드 자료 재배포 제한을 2026-09-06 확인했습니다.
- [Jangpm design system](.claude/skills/ppt-master/templates/decks/jangpm/templates/design_spec.md)의 선택 캐릭터 PNG 2개는 별도 재배포 허가가 확인되지 않아 제외합니다. 이미지 참조가 없는 레이아웃은 유지합니다. 각 브랜드·상표 권리는 원권리자에게 있으며 upstream MIT 고지를 별도 브랜드 권리의 허가로 해석하지 않습니다.
- 위 19개 제외 파일의 역사적 경로·SHA는 upstream manifest에 출처로 보존하고, 실제 ZIP의 release manifest에 제외 사유를 기록합니다. 출처 기록에 등장한다는 사실은 해당 파일이 배포되거나 사용이 허가됐다는 뜻이 아닙니다.

## 설치되는 Python 패키지

특히 **PyMuPDF는 설치한 배포 메타데이터상 GNU AGPL 3.0 또는 Artifex 상용 라이선스**입니다. upstream 코드의 MIT 고지가 이 의존성의 별도 조건을 대체하지 않습니다. 아래는 수집한 원문·메타데이터 고지이며 제품 구성의 법적 호환성을 판정하거나 별도 허가를 부여하지 않습니다.

패키지는 전역 설치하거나 wheel을 제품 ZIP에 복사하지 않습니다. setup이 `requirements-local.lock`의 고정 버전·해시를 확인해 사용자의 저장소 `.venv`에 설치합니다. [버전·공식 메타데이터 URL·wheel 해시](vendor/runtime-dependencies.json)를 보존합니다.

| 패키지 | 버전 | wheel에 포함된 고지 |
|---|---|---|
| beautifulsoup4 | 4.15.0 | [원문1](licenses/runtime/beautifulsoup4/licenses/AUTHORS), [원문2](licenses/runtime/beautifulsoup4/licenses/LICENSE) |
| blinker | 1.9.0 | [원문1](licenses/runtime/blinker/LICENSE.txt) |
| certifi | 2026.7.22 | [원문1](licenses/runtime/certifi/licenses/LICENSE) |
| charset-normalizer | 3.5.1 | [원문1](licenses/runtime/charset-normalizer/licenses/LICENSE) |
| click | 8.5.0 | [원문1](licenses/runtime/click/licenses/LICENSE.txt) |
| et_xmlfile | 2.0.0 | [원문1](licenses/runtime/et-xmlfile/AUTHORS.txt), [원문2](licenses/runtime/et-xmlfile/LICENCE.python), [원문3](licenses/runtime/et-xmlfile/LICENCE.rst) |
| Flask | 3.1.3 | [원문1](licenses/runtime/flask/licenses/LICENSE.txt) |
| idna | 3.19 | [원문1](licenses/runtime/idna/licenses/LICENSE.md) |
| itsdangerous | 2.2.0 | [원문1](licenses/runtime/itsdangerous/LICENSE.txt) |
| Jinja2 | 3.1.6 | [원문1](licenses/runtime/jinja2/licenses/LICENSE.txt) |
| lxml | 6.1.3 | [원문1](licenses/runtime/lxml/licenses/LICENSE.txt), [원문2](licenses/runtime/lxml/licenses/LICENSES.txt) |
| MarkupSafe | 3.0.3 | [원문1](licenses/runtime/markupsafe/licenses/LICENSE.txt) |
| numpy | 2.5.2 | [원문1](licenses/runtime/numpy/licenses/LICENSE.txt), [원문2](licenses/runtime/numpy/licenses/numpy/_core/include/numpy/libdivide/LICENSE.txt), [원문3](licenses/runtime/numpy/licenses/numpy/_core/src/common/pythoncapi-compat/COPYING), [원문4](licenses/runtime/numpy/licenses/numpy/_core/src/highway/LICENSE), [원문5](licenses/runtime/numpy/licenses/numpy/_core/src/multiarray/dragon4_LICENSE.txt), [원문6](licenses/runtime/numpy/licenses/numpy/_core/src/npysort/x86-simd-sort/LICENSE.md), [원문7](licenses/runtime/numpy/licenses/numpy/_core/src/umath/svml/LICENSE), [원문8](licenses/runtime/numpy/licenses/numpy/fft/pocketfft/LICENSE.md), [원문9](licenses/runtime/numpy/licenses/numpy/linalg/lapack_lite/LICENSE.txt), [원문10](licenses/runtime/numpy/licenses/numpy/ma/LICENSE), [원문11](licenses/runtime/numpy/licenses/numpy/random/LICENSE.md), [원문12](licenses/runtime/numpy/licenses/numpy/random/src/distributions/LICENSE.md), [원문13](licenses/runtime/numpy/licenses/numpy/random/src/mt19937/LICENSE.md), [원문14](licenses/runtime/numpy/licenses/numpy/random/src/pcg64/LICENSE.md), [원문15](licenses/runtime/numpy/licenses/numpy/random/src/philox/LICENSE.md), [원문16](licenses/runtime/numpy/licenses/numpy/random/src/sfc64/LICENSE.md), [원문17](licenses/runtime/numpy/licenses/numpy/random/src/splitmix64/LICENSE.md) |
| openpyxl | 3.1.5 | [원문1](licenses/runtime/openpyxl/LICENCE.rst) |
| pillow | 12.3.0 | [원문1](licenses/runtime/pillow/licenses/LICENSE) |
| pymupdf | 1.28.2 | [원문1](licenses/runtime/pymupdf/COPYING) |
| python-pptx | 1.0.2 | [원문1](licenses/runtime/python-pptx/LICENSE) |
| requests | 2.34.2 | [원문1](licenses/runtime/requests/licenses/LICENSE), [원문2](licenses/runtime/requests/licenses/NOTICE) |
| soupsieve | 2.9.2 | [원문1](licenses/runtime/soupsieve/licenses/LICENSE.md) |
| typing_extensions | 4.16.0 | [원문1](licenses/runtime/typing-extensions/licenses/LICENSE) |
| urllib3 | 2.7.0 | [원문1](licenses/runtime/urllib3/licenses/LICENSE.txt) |
| Werkzeug | 3.1.8 | [원문1](licenses/runtime/werkzeug/licenses/LICENSE.txt) |
| xlsxwriter | 3.2.9 | [원문1](licenses/runtime/xlsxwriter/LICENSE.txt) |

Codex와 Claude Code 실행파일·인증 정보는 이 배포에 포함하지 않습니다. 각 사용자는 본인이 설치한 공식 CLI와 계정의 이용 조건에 따라 로그인합니다.
