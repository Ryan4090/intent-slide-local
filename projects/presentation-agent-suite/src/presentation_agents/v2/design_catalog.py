"""Product-owned design directions grounded in bundled Slide Master layouts.

The previews are schematic UI assets, not generated slides or evidence. Layout
roots supply structure references; a preset does not copy an institution's brand
or approve a template. The user's current intent and G3 still govern authoring.
"""
from __future__ import annotations

from copy import deepcopy

from .contracts import ContractError


CATALOG_VERSION = "intent-slide-designs.v1"
_LAYOUTS = ".claude/skills/ppt-master/templates/layouts/"
_TYPOGRAPHY = "Pretendard: 결론 제목은 굵게, 본문·출처는 명확한 위계로"


def _preset(preset_id: str, label: str, description: str, best_for: list[str],
            palette: list[str], layout: str, visualization_patterns: list[str],
            delivery_guidance: str) -> dict:
    return {
        "id": preset_id,
        "label": label,
        "description": description,
        "best_for": best_for,
        "palette": palette,
        "typography": _TYPOGRAPHY,
        "preview_url": f"/design-previews/{preset_id}.svg",
        "preview_kind": "schematic",
        "template_root": _LAYOUTS + layout,
        "reference_mode": "structure_reference",
        "visualization_patterns": visualization_patterns,
        "delivery_guidance": delivery_guidance,
        "canvas": "16:9",
        "catalog_version": CATALOG_VERSION,
    }


_PRESETS = (
    _preset(
        "signal", "시그널", "결론과 근거가 선명한 전략 보고", ["의사결정", "경영 보고"],
        ["#102B46", "#007D8A", "#EDF5F5"], "academic_defense",
        ["핵심 지표와 근거 차트", "대안 비교표", "권고와 실행 순서"],
        "청중이 내려야 할 결정을 먼저 제목에 쓰고, 판단을 바꾸는 근거와 대안을 비교한 뒤 실행을 제안한다. "
        "넓은 여백과 강한 제목 위계로 핵심을 읽게 하고 차트 옆에 해석을 붙인다.",
    ),
    _preset(
        "pitch", "도약", "문제에서 기회로 이어지는 설득", ["제안", "투자·도입 설득"],
        ["#321B2B", "#BB472F", "#FFF1E9"], "government_red",
        ["문제와 해결 전후 비교", "기회와 검증 근거", "실행 단계와 요청"],
        "청중의 문제, 제안의 변화, 확인된 가능성, 구체적인 요청 순서로 긴장을 만든다. "
        "큰 대비와 짧은 메시지를 쓰되 시장 규모나 성과를 근거 없이 확대하지 않는다.",
    ),
    _preset(
        "editorial", "프레임", "관점과 논리를 읽히는 에디토리얼", ["인사이트", "강연·리포트"],
        ["#282039", "#6949BC", "#F5F0E8"], "psychology_attachment",
        ["관점과 근거의 좌우 구성", "개념 관계도", "핵심 문장과 주석"],
        "청중이 바꿔 보아야 할 관점을 하나 제시하고 사례와 구조를 통해 이해를 쌓는다. "
        "편집 지면처럼 큰 제목, 비대칭 열, 짧은 주석을 사용하며 긴 인용문을 장식으로 넣지 않는다.",
    ),
    _preset(
        "evidence", "프루프", "데이터와 한계를 함께 보여주는 분석", ["리서치", "성과·실험 보고"],
        ["#143F65", "#2866AD", "#EFF4FA"], "academic_defense",
        ["측정값 비교 차트", "가설·방법·결과 구조", "불확실성과 한계 표"],
        "질문과 분석 방법, 확인된 결과, 해석의 한계를 차례로 보여준다. "
        "축·단위·기간·표본·출처를 보존하고 비교 가능한 데이터에만 차트를 사용한다.",
    ),
    _preset(
        "roadmap", "마일스톤", "목표를 실행 가능한 순서로 연결", ["실행 계획", "프로젝트 공유"],
        ["#3A3325", "#996600", "#FBF5E8"], "government_blue",
        ["단계별 로드맵", "일정과 의존 관계", "책임·완료 기준 표"],
        "현재 상태에서 목표까지 필요한 순서와 의존 관계를 드러낸다. "
        "수평 진행선과 단계별 완료 기준을 사용하고 확정 일정과 제안 일정을 구별한다.",
    ),
    _preset(
        "system", "아키텍트", "복잡한 구조와 작동 방식을 명료하게", ["기술 설명", "서비스·운영 구조"],
        ["#102D32", "#69D4BC", "#ECF8F4"], "ai_ops",
        ["계층형 아키텍처", "입출력 흐름도", "역할과 경계 매트릭스"],
        "큰 구성 요소에서 시작해 핵심 경계와 정보의 이동을 설명한다. "
        "짙은 배경과 민트 선을 사용하고 연결선은 실제 관계가 있을 때만 넣으며 범례를 제공한다.",
    ),
    _preset(
        "public", "브리핑", "핵심 쟁점과 근거를 빠르게 검토", ["정책·사업 보고", "공식 브리핑"],
        ["#243F70", "#596DB5", "#F1F3F9"], "government_blue",
        ["쟁점별 검토 표", "근거와 영향의 대응", "주체별 실행 구조"],
        "검토해야 할 쟁점을 먼저 정리하고 근거, 영향, 결정 사항을 일관된 구조로 제시한다. "
        "정돈된 제목 띠와 표를 사용하되 사용자 기관의 실제 자료 없이 로고나 공식성을 주장하지 않는다.",
    ),
    _preset(
        "care", "공감", "사람과 경험을 중심으로 이해를 연결", ["교육·워크숍", "경험·사례 공유"],
        ["#23483F", "#467763", "#F4F4EA"], "medical_university",
        ["경험 여정 지도", "상황·행동·변화 흐름", "개념 연결도"],
        "청중이 공감할 상황에서 시작해 경험과 원리를 연결하고 적용할 행동으로 마무리한다. "
        "따뜻한 바탕과 완만한 형태를 쓰되 실제 사례와 가상 시나리오를 명확히 구분한다.",
    ),
    _preset(
        "minimal", "포커스", "하나의 메시지를 크게 남기는 발표", ["제품 소개", "짧은 핵심 발표"],
        ["#212528", "#60676B", "#F8F8F5"], "academic_defense",
        ["한 문장과 핵심 시각물", "기능 전후 대비", "주장과 단일 근거"],
        "한 장에서 바꾸고 싶은 생각을 하나 정해 큰 문장과 필요한 시각물만 배치한다. "
        "모노크롬과 넓은 여백을 유지하고 단순화 때문에 근거·단서·접근성을 잃지 않는다.",
    ),
    _preset(
        "workshop", "스파크", "질문하고 비교하며 함께 답을 발견", ["참여형 강의", "아이디어·전략 워크숍"],
        ["#24352C", "#647E25", "#F2F6E8"], "pixel_retro",
        ["선택지 비교 보드", "질문에서 답으로 가는 단계", "우선순위 매트릭스"],
        "청중이 답할 수 있는 질문과 선택지를 먼저 보여주고 비교와 토론 후 다음 행동을 정한다. "
        "각진 카드와 명료한 번호로 참여 순서를 만들고 회고나 투표 결과를 실제 수집 없이 만들지 않는다.",
    ),
)


def list_design_presets() -> list[dict]:
    """Return independent serializable values for the authenticated console."""
    return deepcopy(list(_PRESETS))


def get_design_preset(preset_id: str) -> dict:
    """Resolve only reviewed IDs; never turn user input into filesystem paths."""
    if isinstance(preset_id, str):
        for preset in _PRESETS:
            if preset["id"] == preset_id:
                return deepcopy(preset)
    raise ContractError("디자인 목록에서 사용할 스타일을 선택해 주세요")


def normalize_preference(value: dict | None) -> dict | None:
    """Store just the user's choice, with no implicit approval or legacy default."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"preset_id"}:
        raise ContractError("디자인 선택에는 preset_id만 지정해 주세요")
    preset = get_design_preset(value["preset_id"])
    return {"preset_id": preset["id"]}
