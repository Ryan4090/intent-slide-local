"""Adversarial source-provenance, excerpt, format, and calculation contracts."""

from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from presentation_agents.v2.contracts import ContractError, validate_research
from presentation_agents.v2.evidence import EvidenceReader

SUITE = Path(__file__).resolve().parents[1]


class ResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = SUITE / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="research-test-", dir=runtime)
        self.root = Path(self.temp.name)
        self.artifacts = []
        artifact = self.add_file("original.txt", b"Actual revenue is 100.\nSecond source line.\nThird line.\n")
        self.intent = {"source_mode":"provided_only", "requirements":[], "slides":[{"uid":"slide-one"}]}
        self.data = {
            "sources":[{"id":"source-one", "origin":"provided", "artifact_id":artifact["id"],
                        "sha256":artifact["sha256"], "title":"User-provided report", "accessed_at":"2026-09-06"}],
            "claims":[{"id":"claim-one", "text":"Reported revenue is 100", "kind":"fact", "evidence_status":"SUPPORTED",
                       "supports":[{"source_id":"source-one", "locator":"lines:1", "excerpt":"Actual revenue is 100."}]}],
            "packets":[], "messages":[{"slide_uid":"slide-one", "message":"Reported revenue", "claim_ids":["claim-one"]}],
            "analysis":"The source reports revenue.", "limitations":["Semantic entailment requires review."],
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_file(self, name: str, content: bytes, *, kind: str = "attachment", provenance: dict | None = None) -> dict:
        path = self.root / name
        path.write_bytes(content)
        artifact = {"id":f"artifact-{len(self.artifacts) + 1}", "path":name,
                    "sha256":hashlib.sha256(content).hexdigest(), "valid":True, "kind":kind,
                    "provenance":provenance if provenance is not None else {"origin":"user-upload"}}
        self.artifacts.append(artifact)
        return artifact

    def validate(self, data: dict | None = None, **kwargs: object) -> dict:
        return validate_research(data if data is not None else self.data, self.intent, self.artifacts,
                                 artifact_root=self.root, **kwargs)

    def use_artifact(self, artifact: dict, locator: str, excerpt: str) -> None:
        self.data["sources"][0].update(artifact_id=artifact["id"], sha256=artifact["sha256"])
        self.data["claims"][0]["supports"][0].update(locator=locator, excerpt=excerpt)

    def test_real_excerpt_receipt_overwrites_worker_stamp_and_keeps_semantics_separate(self) -> None:
        support = self.data["claims"][0]["supports"][0]
        support["verification"] = {"status":"FAKE", "semantic_entailment":"VERIFIED"}
        support["excerpt"] = "Actual\t revenue\n is 100."
        result = self.validate()
        receipt = result["claims"][0]["supports"][0]["verification"]
        self.assertEqual(receipt["status"], "VERIFIED")
        self.assertEqual(receipt["semantic_entailment"], "UNVERIFIED")
        self.assertEqual(support["verification"]["status"], "FAKE", "input remains unchanged")

    def test_initial_worker_factual_spelling_normalizes_without_weakening_sources(self):
        self.data['claims'][0]['kind'] = 'factual'
        self.assertEqual(self.validate()['claims'][0]['kind'], 'fact')
        self.data['claims'][0]['supports'][0]['excerpt'] = 'unsupported replacement'
        with self.assertRaises(ContractError):
            self.validate()

    def test_original_audit_fabricated_excerpt_and_locator_are_rejected(self) -> None:
        for locator, excerpt in [("lines:999999", "Invented source sentence"),
                                 ("lines:1", "Revenue is 999 trillion"),
                                 ("lines:2", "Actual revenue is 100."),
                                 ("line 1", "Actual revenue is 100."),
                                 ("lines:3-1", "Third line."),
                                 ("lines:0", "Actual revenue is 100.")]:
            with self.subTest(locator=locator, excerpt=excerpt):
                changed = copy.deepcopy(self.data)
                changed["claims"][0]["supports"][0].update(locator=locator, excerpt=excerpt)
                with self.assertRaises(ContractError):
                    self.validate(changed)

    def test_source_hash_changes_missing_root_and_symlinks_fail_closed(self) -> None:
        with self.assertRaises(ContractError):
            validate_research(self.data, self.intent, self.artifacts)
        (self.root / "original.txt").write_text("tampered source", encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "bytes changed"):
            self.validate()
        (self.root / "link.txt").symlink_to(self.root / "original.txt")
        self.artifacts[0]["path"] = "link.txt"
        with self.assertRaisesRegex(ContractError, "symlink"):
            self.validate()

    def test_worker_cannot_declare_its_external_source_user_provided(self) -> None:
        external = self.add_file("download.txt", b"Actual revenue is 100.", kind="source", provenance={"origin":"provided"})
        self.use_artifact(external, "lines:1", "Actual revenue is 100.")
        with self.assertRaisesRegex(ContractError, "service-imported"):
            self.validate()
        external["provenance"] = {"origin":"user-upload"}
        with self.assertRaises(ContractError):
            self.validate()

    def test_service_owned_derived_provenance_and_imported_attachment_are_accepted(self) -> None:
        self.artifacts[0]["provenance"] = {"origin":"import"}
        derived = self.add_file("extraction.md", b"Actual revenue is 100.", kind="source",
                                provenance={"origin":"trusted-extraction", "derived_from":self.artifacts[0]["id"]})
        self.use_artifact(derived, "lines:1", "Actual revenue is 100.")
        self.validate()
        self.artifacts[0]["valid"] = False
        with self.assertRaisesRegex(ContractError, "stale"):
            self.validate()

    def test_provenance_cycle_is_blocked(self) -> None:
        self.artifacts[0].update(kind="source", provenance={"derived_from":self.artifacts[0]["id"]})
        with self.assertRaisesRegex(ContractError, "cycle"):
            self.validate()

    def test_duplicate_records_and_non_object_boundaries_raise_contract_errors(self) -> None:
        for field in ("sources", "claims", "packets", "messages"):
            for bad in (None, "string", {}, [None], ["string"], [{"id":[]}], [{"id":{}}]):
                with self.subTest(field=field, bad=bad):
                    changed = copy.deepcopy(self.data)
                    changed[field] = bad
                    with self.assertRaises(ContractError):
                        self.validate(changed)
        for field in ("sources", "claims", "messages"):
            changed = copy.deepcopy(self.data)
            changed[field].append(copy.deepcopy(changed[field][0]))
            with self.assertRaisesRegex(ContractError, "duplicate"):
                self.validate(changed)

    def test_malformed_nested_enums_and_maps_raise_contract_errors(self) -> None:
        for container, field in [("sources", "origin"), ("sources", "status"), ("sources", "artifact_id"),
                                 ("sources", "url"), ("claims", "evidence_status"), ("claims", "kind"),
                                 ("claims", "supports"), ("claims", "critical"), ("messages", "claim_ids")]:
            for bad in ([], {}, True, None):
                if container == "sources" and field == "url" and bad is None:
                    continue
                if container == "claims" and field == "critical" and bad is True:
                    continue
                if container == "messages" and field == "claim_ids" and bad == []:
                    continue
                with self.subTest(container=container, field=field, bad=bad):
                    changed = copy.deepcopy(self.data)
                    changed[container][0][field] = bad
                    with self.assertRaises(ContractError):
                        self.validate(changed)

    def test_partial_research_still_rejects_unknown_page_claim_and_requirement_refs(self) -> None:
        for field, records in [
            ("messages", [{"slide_uid":"unknown", "message":"message", "claim_ids":[]}]),
            ("messages", [{"slide_uid":"slide-one", "message":"message", "claim_ids":["unknown"]}]),
            ("packets", [{"requirement_uid":"unknown", "work_status":"DONE", "claim_ids":["claim-one"]}]),
        ]:
            changed = copy.deepcopy(self.data)
            changed[field] = records
            with self.assertRaises(ContractError):
                self.validate(changed, partial=True)

    def test_duplicate_url_and_snapshot_counts_do_not_claim_source_independence(self) -> None:
        self.intent["source_mode"] = "external"
        self.data["sources"][0].update(origin="external", url="https://EXAMPLE.com:443/report#page1")
        second = copy.deepcopy(self.data["sources"][0])
        second.update(id="source-two", url="https://example.com/report#page2")
        self.data["sources"].append(second)
        result = self.validate()
        self.assertEqual(result["verification"]["source_files"], 1)
        self.assertEqual(result["verification"]["unique_urls"], 1)
        self.assertIsNone(result["verification"]["independent_sources"])

    def test_pdf_page_location_and_empty_or_missing_page_failures(self) -> None:
        import pymupdf
        with pymupdf.open() as document:
            document.new_page().insert_text((40, 40), "PDF source amount is 42.")
            document.new_page()
            content = document.tobytes()
        artifact = self.add_file("source.pdf", content)
        self.use_artifact(artifact, "page:1", "source amount is 42.")
        self.validate()
        for locator in ("page:2", "page:3", "lines:1"):
            self.data["claims"][0]["supports"][0]["locator"] = locator
            with self.assertRaises(ContractError):
                self.validate()

    def test_docx_paragraph_and_xlsx_sheet_cell_extraction(self) -> None:
        path = self.root / "source.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>First paragraph.</w:t></w:r></w:p><w:p><w:r><w:t>Revenue 42.</w:t></w:r></w:p></w:body></w:document>')
        artifact = self.add_file("source.docx", path.read_bytes())
        self.use_artifact(artifact, "lines:2", "Revenue 42.")
        self.validate()
        path = self.root / "source.xlsx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Metrics" r:id="rId1"/></sheets></workbook>')
            archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1" t="inlineStr"><is><t>Revenue</t></is></c><c r="B1"><v>42</v></c></row><row><c r="A2"><f>1+1</f><v>999</v></c></row></sheetData></worksheet>')
        artifact = self.add_file("source.xlsx", path.read_bytes())
        self.use_artifact(artifact, "lines:2", "A1=Revenue B1=42")
        self.validate()
        self.use_artifact(artifact, "lines:3", "999")
        with self.assertRaises(ContractError):
            self.validate()

    def test_unsupported_format_is_not_treated_as_verified_text(self) -> None:
        artifact = self.add_file("source.png", b"fake binary format")
        self.use_artifact(artifact, "lines:1", "fake")
        with self.assertRaisesRegex(ContractError, "unsupported source format"):
            self.validate()

    def arithmetic_data(self, living: float, pension: float, result: float) -> dict:
        data = copy.deepcopy(self.data)
        def number(identifier: str, value: float) -> dict:
            return {"id":identifier, "kind":"numeric", "text":identifier, "value":value,
                    "evidence_status":"ASSUMPTION", "limitations":["Illustrative fixture"],
                    "unit":"KRW/month", "population":"example household", "as_of":"2026-09"}
        data["claims"] = [number("living", living), number("pension", pension), {
            **number("gap", result), "kind":"derived", "formula":"Living costs minus pension",
            "input_claim_ids":["living", "pension"],
            "calculation":{"expression":"L - P", "bindings":{"L":"living", "P":"pension"}},
        }]
        data["messages"][0]["claim_ids"] = ["gap"]
        return data

    def test_derived_positive_zero_and_negative_gap_are_recomputed(self) -> None:
        for living, pension, gap in [(300, 100, 200), (100, 100, 0), (100, 300, -200)]:
            with self.subTest(gap=gap):
                result = self.validate(self.arithmetic_data(living, pension, gap))
                self.assertEqual(result["claims"][2]["verification"]["calculation"]["computed_value"], gap)
        with self.assertRaisesRegex(ContractError, "differs"):
            self.validate(self.arithmetic_data(300, 100, 999))

    def test_sourced_numeric_value_cannot_differ_from_the_verified_quote(self) -> None:
        claim = self.data["claims"][0]
        claim.update(kind="numeric", value=100, value_text="100", unit="KRW", population="reported company", as_of="2026")
        result = self.validate()
        self.assertEqual(result["claims"][0]["verification"]["source_value"], "VERIFIED")
        for value, literal in [(999, "100"), (999, "999"), (10, "10")]:
            with self.subTest(value=value, literal=literal):
                claim.update(value=value, value_text=literal)
                with self.assertRaises(ContractError):
                    self.validate()

    def test_nonfinite_boolean_and_invalid_numeric_metadata_fail(self) -> None:
        for value in (True, False, "300", float("nan"), float("inf"), 10 ** 200):
            with self.subTest(value=str(value)):
                with self.assertRaises(ContractError):
                    self.validate(self.arithmetic_data(value, 100, 200))
        for date in ("yesterday", "2026-99-01", "2026-02-30"):
            data = self.arithmetic_data(300, 100, 200)
            data["claims"][0]["as_of"] = date
            with self.assertRaises(ContractError):
                self.validate(data)

    def test_derived_code_execution_division_zero_unbound_and_cycles_are_rejected(self) -> None:
        for expression in ("__import__('os').system('false')", "L.__class__", "L / 0", "Q + 1", "L ** 1000", "[L][0]"):
            data = self.arithmetic_data(300, 100, 200)
            data["claims"][2]["calculation"]["expression"] = expression
            with self.subTest(expression=expression), self.assertRaises(ContractError):
                self.validate(data)
        data = self.arithmetic_data(300, 100, 200)
        data["claims"][2]["input_claim_ids"] = ["gap"]
        with self.assertRaisesRegex(ContractError, "cycle"):
            self.validate(data)

    def test_supported_derived_claim_cannot_upgrade_assumed_inputs(self) -> None:
        data = self.arithmetic_data(300, 100, 200)
        data["claims"][2]["evidence_status"] = "SUPPORTED"
        with self.assertRaisesRegex(ContractError, "upgrade"):
            self.validate(data)


if __name__ == "__main__":
    unittest.main()
