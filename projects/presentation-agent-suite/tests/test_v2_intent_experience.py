"""Intent-first interviews and approved delivery strategy, without live AI calls."""
import copy
import tempfile
import unittest
from pathlib import Path

from presentation_agents.v2.contracts import ContractError, Conflict, normalize_intent
from presentation_agents.v2.engine import Engine
from test_v2_engine import intent
import test_v2_runner as fixtures


def interview():
    return {
        "kind": "question", "question": "발표가 끝나면 청중이 무엇을 결정하면 좋을까요?",
        "impact": "결론과 근거의 순서를 결정합니다.",
        "intent_summary": "경영진에게 운영 개선의 우선순위를 제안하려는 요청으로 이해했습니다.",
        "questions": [{"id": "decision", "question": "어떤 결정을 이끌어낼까요?",
                       "why": "마지막 슬라이드의 행동 요청이 달라집니다.",
                       "options": [{"label": "예산 승인", "description": "효과와 비용을 비교합니다."},
                                   {"label": "실행 순서 합의", "description": "단계별 로드맵을 제시합니다."}]}],
    }


def strategy():
    return {"audience_shift": "문제 공감에서 우선순위 합의로",
            "core_message": "작게 시작해 효과가 확인된 개선을 확장합니다.",
            "narrative_arc": "결론 → 문제의 근거 → 대안 비교 → 실행 합의",
            "presentation_mode": "경영진 회의에서 발표 후 토론",
            "visual_principles": ["검증한 수치는 비교 차트로", "실행 단계는 로드맵으로"],
            "constraints": ["제공 자료 밖의 성과를 단정하지 않음"]}


class IntentContractTests(unittest.TestCase):
    def test_delivery_strategy_rejects_empty_or_wrong_shape(self):
        for key, value in (("core_message", ""), ("audience_shift", []),
                           ("visual_principles", "차트"), ("constraints", [None])):
            with self.subTest(key=key):
                data = intent(1)
                data["delivery_strategy"] = {**strategy(), key: value}
                with self.assertRaises(ContractError):
                    normalize_intent(data, "provided_only")

    def test_delivery_strategy_is_optional_and_preserved_without_invented_decisions(self):
        legacy = normalize_intent(intent(1), "provided_only")
        self.assertNotIn("delivery_strategy", legacy)
        data = intent(1)
        data.update(intent_summary="운영 개선의 우선순위를 합의합니다.", delivery_strategy=strategy())
        actual = normalize_intent(data, "provided_only")
        self.assertEqual(actual["delivery_strategy"], strategy())
        self.assertEqual(actual["intent_summary"], data["intent_summary"])


class InterviewEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name))
        self.state = self.engine.create("인터뷰", "우선순위를 결정할 발표", "provided_only", "create")

    def tearDown(self):
        self.temp.cleanup()

    def command(self, name, payload=None):
        state = self.engine.snapshot(self.state["id"])
        return self.engine.command(state["id"], name, payload or {}, f"op-{state['revision']}", state["revision"])

    def test_structured_question_is_preserved_and_answered_as_one_user_message(self):
        packet = interview()
        self.engine.add_question(self.state["id"], packet["question"], packet["impact"],
                                 questions=packet["questions"], intent_summary=packet["intent_summary"])
        state = self.engine.snapshot(self.state["id"])
        question = state["questions"][-1]
        self.assertEqual(question["questions"], packet["questions"])
        self.assertEqual(question["intent_summary"], packet["intent_summary"])
        self.assertEqual(state["progress"]["percent"], 0)
        with self.assertRaises(ContractError):
            self.command("run")
        answered = self.command("answer", {"question_id": question["id"], "answer": "어떤 결정을 이끌어낼까요?\n실행 순서 합의"})
        self.assertEqual(answered["questions"][-1]["status"], "ANSWERED")
        self.assertEqual(answered["messages"][-1]["role"], "user")
        self.assertIsNone(answered["intent"])
        self.assertFalse(answered["approvals"])

    def test_malformed_interviews_are_rejected_without_saved_questions(self):
        invalid = [[], [interview()["questions"][0]] * 4,
                   [{**interview()["questions"][0], "options": [{"label": "a", "description": "b"}]}],
                   [{**interview()["questions"][0], "why": " "}],
                   [interview()["questions"][0], copy.deepcopy(interview()["questions"][0])]]
        for questions in invalid:
            with self.subTest(questions=questions), self.assertRaises(ContractError):
                self.engine.add_question(self.state["id"], "질문", "전달방식 확인", questions=questions)
        self.assertFalse(self.engine.snapshot(self.state["id"])["questions"])

    def test_delivery_strategy_is_bound_to_current_g1_review(self):
        data = intent(1)
        data["delivery_strategy"] = strategy()
        first = self.engine.publish(self.state["id"], "intent", data)
        review = first["reviews"][-1]
        self.command("approve", {"review_id": review["id"], "bundle_sha256": review["bundle_sha256"]})
        self.command("request_changes", {"stage": "intent", "reason": "발표 전달방식 변경"})
        revised = copy.deepcopy(first["intent"])
        revised["delivery_strategy"]["presentation_mode"] = "사전 배포 문서로 읽고 비동기 의견 수렴"
        second = self.engine.publish(self.state["id"], "intent", revised)
        self.assertFalse(any(a["valid"] for a in second["approvals"]))
        self.assertNotEqual(second["reviews"][-1]["bundle_sha256"], review["bundle_sha256"])
        self.assertEqual(second["status"], "INTENT_REVIEW")
        with self.assertRaises(ContractError):
            self.command("approve", {"review_id": review["id"], "bundle_sha256": review["bundle_sha256"]})

    def test_create_validates_design_preference_and_binds_it_to_worker_input(self):
        selected = self.engine.create("선택", "선택한 디자인", "provided_only", "selected", design_preference={"preset_id": "signal"})
        self.assertEqual(selected["design_preference"], {"preset_id": "signal"})
        no_preference = copy.deepcopy(selected)
        no_preference["design_preference"] = None
        self.assertNotEqual(self.engine.input_hash(selected), self.engine.input_hash(no_preference))
        with self.assertRaises(ContractError):
            self.engine.create("선택", "잘못된 디자인", "provided_only", "bad", design_preference={"preset_id": "unlisted"})

    def test_select_design_preserves_content_approval_but_rejects_a_live_job(self):
        first = self.engine.publish(self.state["id"], "intent", intent(1))
        review = first["reviews"][-1]
        approved = self.command("approve", {"review_id": review["id"], "bundle_sha256": review["bundle_sha256"]})
        selected = self.command("select_design", {"preset_id": "signal"})
        self.assertEqual(selected["active_stage"], "research")
        self.assertTrue(selected["approvals"][-1]["valid"])
        self.assertEqual(selected["reviews"][-1]["bundle_sha256"], review["bundle_sha256"])
        self.assertGreater(selected["content_revision"], approved["content_revision"])
        unchanged = self.command("select_design", {"preset_id": "signal"})
        self.assertEqual(unchanged["content_revision"], selected["content_revision"])
        self.command("run")
        with self.assertRaises(Conflict):
            self.command("select_design", {"preset_id": "pitch"})

    def test_select_design_waits_for_interview_answer(self):
        self.engine.add_question(self.state["id"], "청중은 누구인가요?", "의도 확인")
        with self.assertRaises(Conflict):
            self.command("select_design", {"preset_id": "signal"})


class InterviewRunnerTests(unittest.TestCase):
    # Reuse the explicit provider fixture lifecycle, not its test cases.
    setUp = fixtures.RunnerTests.setUp
    tearDown = fixtures.RunnerTests.tearDown
    command = fixtures.RunnerTests.command
    launch = fixtures.RunnerTests.launch
    wait_for = fixtures.RunnerTests.wait_for
    join = fixtures.RunnerTests.join
    approve = fixtures.RunnerTests.approve
    prepare_design = fixtures.RunnerTests.prepare_design
    research = staticmethod(fixtures.RunnerTests.research)

    def test_worker_interview_reaches_inbox_before_any_research(self):
        self.launch(lambda provider: provider.finish(interview()))
        state = self.join()
        self.assertEqual(state["questions"][-1]["questions"], interview()["questions"])
        self.assertEqual(state["questions"][-1]["intent_summary"], interview()["intent_summary"])
        self.assertEqual(state["status"], "WAITING_USER")
        self.assertEqual([j["phase"] for j in state["jobs"]], ["intent"])
        self.assertIsNone(state["research"])
        self.assertFalse(state["approvals"])

    def test_invalid_worker_interview_fails_instead_of_creating_an_unanswerable_form(self):
        malformed = interview()
        malformed["questions"][0]["options"] = ["raw"]
        self.launch(lambda provider: provider.finish(malformed))
        state = self.join()
        self.assertEqual(state["jobs"][-1]["status"], "FAILED")
        self.assertFalse(state["questions"])

    def test_worker_receives_selected_preset_and_its_actual_reference_root(self):
        self.command("select_design", {"preset_id": "signal"})
        provider = self.launch(lambda provider: provider.finish(interview()))
        self.join()
        self.assertEqual(provider.packet["design_preference"], {"preset_id": "signal"})
        self.assertEqual(provider.packet["design_preset"]["id"], "signal")
        self.assertTrue((fixtures.REPO / provider.packet["design_preset"]["template_root"]).is_dir())

    def test_changing_style_reopens_only_design_and_preserves_research_approval(self):
        self.prepare_design()
        before = self.engine.snapshot(self.run_id)
        self.assertTrue(all(a["valid"] for a in before["approvals"]))
        changed = self.command("select_design", {"preset_id": "pitch"})
        self.assertEqual(changed["active_phase"], "design_direction")
        self.assertEqual(changed["status"], "DESIGN_READY")
        self.assertEqual([a["gate"] for a in changed["approvals"] if a["valid"]], ["G1", "G2"])
        self.assertTrue(any(a["kind"] == "direction" and not a["valid"] for a in changed["artifacts"]))
        self.assertEqual(changed["intent"], before["intent"])
        self.assertEqual(changed["research"], before["research"])


if __name__ == "__main__":
    unittest.main()
