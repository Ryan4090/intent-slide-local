"""Real Korean PDF, research-ledger, and G2 report integrity regression checks."""

from __future__ import annotations

import copy
import os
import re
import tempfile
import unittest
from pathlib import Path

import pymupdf

from presentation_agents.v2.contracts import ContractError
from presentation_agents.v2.engine import Engine
from presentation_agents.v2.reporting import markdown_pdf, research_markdown

SUITE = Path(__file__).resolve().parents[1]


def report_fixture() -> tuple[dict, dict]:
    intent = {"fields":{"topic":{"value":"은퇴 이후 생활비와 연금의 차이를 점검합니다"}},
              "slides":[{"uid":"slide-one", "display_id":"P01", "title":"월 현금흐름 확인"},
                        {"uid":"slide-two", "display_id":"P02", "title":"개인별 확인과 실행"}]}
    research = {
        "analysis":"가구의 생활비와 연금을 같은 월 단위로 비교합니다. 아래 수치는 검증 구조를 설명하는 예시이며 실제 가구 통계가 아닙니다.",
        "messages":[{"slide_uid":"slide-one", "message":"생활비 300만원에서 연금 180만원을 빼면 월 차액은 120만원입니다.", "claim_ids":["CLAIM-GAP"], "caveat":"세금·기타 소득·개인별 지출 차이는 별도로 확인합니다."},
                    {"slide_uid":"slide-two", "message":"개인 연금과 지출을 조회한 뒤 실제 수치를 입력합니다.", "claim_ids":[]}],
        "claims":[{"id":"CLAIM-LIVING", "text":"예시 가구의 월 생활비는 300만원입니다.", "kind":"numeric", "evidence_status":"PROVIDED", "value":300, "value_text":"300", "unit":"만원/월", "population":"검증용 예시 가구", "as_of":"2026-09-06", "supports":[{"source_id":"SOURCE-ONE", "locator":"lines:1", "excerpt":"월 생활비는 300만원입니다."}]},
                  {"id":"CLAIM-PENSION", "text":"예시 가구의 월 연금은 180만원입니다.", "kind":"numeric", "evidence_status":"PROVIDED", "value":180, "value_text":"180", "unit":"만원/월", "population":"검증용 예시 가구", "as_of":"2026-09-06", "supports":[{"source_id":"SOURCE-ONE", "locator":"lines:2", "excerpt":"월 연금은 180만원입니다."}]},
                  {"id":"CLAIM-GAP", "text":"예시 월 차액은 120만원입니다.", "kind":"derived", "evidence_status":"SUPPORTED", "value":120, "unit":"만원/월", "population":"검증용 예시 가구", "as_of":"2026-09-06", "formula":"월 차액 = 월 생활비 − 월 연금", "input_claim_ids":["CLAIM-LIVING", "CLAIM-PENSION"], "calculation":{"expression":"L - P", "bindings":{"L":"CLAIM-LIVING", "P":"CLAIM-PENSION"}}, "limitations":["개인의 적정 생활비를 의미하지 않습니다."]}],
        "sources":[{"id":"SOURCE-ONE", "title":"검증용 사용자 제공 예시 자료", "status":"SNAPSHOT", "origin":"provided", "accessed_at":"2026-09-06", "artifact_id":"artifact-fixture", "sha256":"a" * 64}],
        "packets":[], "limitations":["검증용 숫자이며 실제 은퇴 설계 권고가 아닙니다.", "의미상 뒷받침과 최신성은 별도 검토가 필요합니다."],
    }
    return intent, research


class ReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = SUITE / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="reporting-test-", dir=runtime)
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_real_korean_pdf_preserves_analysis_ledger_source_and_limitations(self) -> None:
        intent, research = report_fixture()
        research["claims"][0]["as_of_note"] = "검증 자료의 기준일이며 개인의 조사일이 아닙니다."
        research["claims"][0]["supports"][0]["excerpt"] = "- 월 생활비는 300만원입니다.\n* 원문 기호 **그대로** · \ue044 ․"
        markdown = research_markdown(research, intent)
        content = markdown_pdf(markdown)
        self.assertTrue(content.startswith(b"%PDF-"))
        with pymupdf.open(stream=content, filetype="pdf") as document:
            self.assertGreater(document.page_count, 0)
            text = "\n".join(page.get_text() for page in document)
            compact = re.sub(r"[\s\u200b]+", "", text)
            for phrase in ("리서치·분석 검토", "주장·원문 위치·계산 장부", "CLAIM-GAP", "SOURCE-ONE", "lines:1", "120", "자료 목록", "해석과 검증의 한계"):
                self.assertIn(re.sub(r"\s+", "", phrase), compact)
            self.assertIn("a" * 64, compact)
            self.assertIn("as_of_note:검증자료의기준일이며개인의조사일이아닙니다.", compact)
            self.assertIn("-월생활비는300만원입니다.*원문기호**그대로**·[U+E044][U+2024]", compact)
            self.assertIn("검증용숫자이며실제은퇴설계권고가아닙니다.", compact)
            for page in document:
                self.assertIn("SlideMaster", page.get_text())
                self.assertFalse(page.get_links(), "plain source content must not introduce active links")

    def test_contract_default_source_status_is_renderable(self) -> None:
        intent, research = report_fixture()
        del research["sources"][0]["status"]
        markdown = research_markdown(research, intent)
        self.assertIn("상태: SNAPSHOT", markdown)

    def test_valid_list_analysis_and_string_limitations_remain_whole(self) -> None:
        intent, research = report_fixture()
        research["analysis"] = ["첫 번째 분석 문단입니다.", "두 번째 분석 문단입니다."]
        research["limitations"] = "개인별 확인이 필요합니다."
        research["claims"][2]["limitations"] = "세금은 제외했습니다."
        markdown = research_markdown(research, intent)
        self.assertIn("첫 번째 분석 문단입니다.", markdown)
        self.assertIn("두 번째 분석 문단입니다.", markdown)
        self.assertIn("- 개인별 확인이 필요합니다.", markdown)
        self.assertIn("- 한계: 세금은 제외했습니다.", markdown)
        self.assertNotIn("- 한계: 세\n", markdown)

    def test_html_like_user_text_is_rendered_as_text_without_active_content(self) -> None:
        text = '# 안전한 원문 표시\n\n<script>alert("fixture")</script>\n\n<img src="https://example.invalid/tracker">'
        with pymupdf.open(stream=markdown_pdf(text), filetype="pdf") as document:
            extracted = "\n".join(page.get_text() for page in document)
            self.assertIn("<script>", extracted)
            self.assertIn("example.invalid/tracker", extracted)
            self.assertEqual(sum(len(page.get_images()) for page in document), 0)
            self.assertEqual(sum(len(page.get_links()) for page in document), 0)

    def test_long_report_paginates_and_retains_the_last_ledger_entry(self) -> None:
        markdown = "# 긴 검토 보고서\n\n" + "\n\n".join(
            f"### CLAIM-{i:03d}\n\n주장 {i}의 근거와 범위, 모집단, 기준일을 함께 검토합니다. " + "장부 검증 문장입니다. " * 7
            for i in range(1, 45)
        )
        with pymupdf.open(stream=markdown_pdf(markdown), filetype="pdf") as document:
            self.assertGreaterEqual(document.page_count, 3)
            text = "\n".join(page.get_text() for page in document)
            self.assertIn("CLAIM-001", text)
            self.assertIn("CLAIM-044", text)
            self.assertTrue(all(page.get_text().strip() for page in document))
            for page in document:
                for word in page.get_text("words"):
                    self.assertGreaterEqual(word[0], 0)
                    self.assertLessEqual(word[2], page.rect.width + 1)
                    self.assertLessEqual(word[3], page.rect.height + 1)

    def prepared_engine(self) -> tuple[Engine, dict]:
        engine = Engine(self.root)
        run = engine.create("한글 보고서 승인 검증", "예시 자료로만 검증", "provided_only", "create")
        run = engine.add_attachment(run["id"], "example.txt", "월 생활비는 300만원입니다.\n월 연금은 180만원입니다.\n".encode(), "attach", run["revision"])
        attachment = run["artifacts"][0]
        intent, research = report_fixture()
        data = {"fields":{key:{"value":value,"state":"proposed","source":"test brief"} for key, value in {
            "topic":"한글 보고서 승인 검증", "audience":"검토자", "objective":"검증", "success_criteria":"보고서 무결성", "slide_count":2,
        }.items()}, "slides":[{"uid":p["uid"], "title":p["title"], "purpose":"검토", "content":["예시 검토"], "evidence_needed":[]} for p in intent["slides"]]}
        run = engine.publish(run["id"], "intent", data)
        review = run["reviews"][-1]
        run = engine.command(run["id"], "approve", {"review_id":review["id"],"bundle_sha256":review["bundle_sha256"]}, "g1", run["revision"])
        research["sources"][0].update(artifact_id=attachment["id"], sha256=attachment["sha256"])
        run = engine.publish(run["id"], "research", research)
        return engine, run

    def test_g2_manifest_binds_real_markdown_pdf_and_research_contract(self) -> None:
        engine, run = self.prepared_engine()
        review = next(r for r in run["reviews"] if r["gate"] == "G2" and r["status"] == "PENDING")
        by_kind = {a["kind"]:a for a in run["artifacts"] if a["valid"]}
        for kind in ("research", "analysis_report", "analysis_pdf"):
            self.assertIn(by_kind[kind]["id"], review["artifact_ids"])
            self.assertEqual(review["manifest"][by_kind[kind]["id"]], by_kind[kind]["sha256"])
        pdf = engine.artifact_path(run["id"], by_kind["analysis_pdf"]["id"])
        with pymupdf.open(pdf) as document:
            self.assertIn("CLAIM-GAP", "\n".join(page.get_text() for page in document))

    def test_modified_pdf_blocks_g2_even_when_mtime_is_preserved(self) -> None:
        engine, run = self.prepared_engine()
        review = next(r for r in run["reviews"] if r["gate"] == "G2" and r["status"] == "PENDING")
        artifact = next(a for a in run["artifacts"] if a["kind"] == "analysis_pdf")
        path = engine.artifact_path(run["id"], artifact["id"])
        stamp = path.stat()
        path.write_bytes(path.read_bytes() + b"\n% altered report\n")
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with self.assertRaises(ContractError):
            engine.command(run["id"], "approve", {"review_id":review["id"],"bundle_sha256":review["bundle_sha256"]}, "g2", run["revision"])
        current = engine.snapshot(run["id"])
        self.assertEqual(current["status"], "STALE")
        self.assertTrue(any(a["gate"] == "G1" and a["valid"] for a in current["approvals"]))

    def test_modified_approved_pdf_invalidates_g2_and_research_start(self) -> None:
        engine, run = self.prepared_engine()
        review = next(r for r in run["reviews"] if r["gate"] == "G2" and r["status"] == "PENDING")
        run = engine.command(run["id"], "approve", {"review_id":review["id"],"bundle_sha256":review["bundle_sha256"]}, "g2", run["revision"])
        self.assertEqual(run["progress"]["percent"], 55)
        artifact = next(a for a in run["artifacts"] if a["kind"] == "analysis_pdf")
        path = engine.artifact_path(run["id"], artifact["id"])
        path.write_bytes(path.read_bytes() + b"\n% altered after approval\n")
        current = engine.snapshot(run["id"])
        self.assertFalse(any(a["gate"] == "G2" and a["valid"] for a in current["approvals"]))
        self.assertLess(current["progress"]["percent"], 55)
        with self.assertRaises(ContractError):
            engine.command(run["id"], "run", {}, "design-run", current["revision"])


if __name__ == "__main__":
    unittest.main()
