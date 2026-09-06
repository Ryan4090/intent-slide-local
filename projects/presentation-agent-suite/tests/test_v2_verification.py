"""Trusted G4 contract tests; external verifier/render processes are mocked.

ZIP/XML, candidate paths, reference sets, input hashes and PNG validation run
for real. These tests do not claim an OfficeCLI/PowerPoint rendering pass.
"""
import copy
import shutil
import json
import os
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
import zlib
from pathlib import Path
from PIL import Image
from unittest.mock import patch
from xml.sax.saxutils import escape

from presentation_agents import pipeline as legacy
from presentation_agents.v2.contracts import ContractError, file_hash
from presentation_agents.v2.verification import VerificationBlocked, receipt_environment_matches, verify_candidate
import presentation_agents.v2.verification as verification_module

REPO = Path(__file__).resolve().parents[3]
NS = "http://schemas.openxmlformats.org/"


def workspace_temporary_directory():
    runtime = REPO / "projects/presentation-agent-suite/.runtime"
    runtime.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=runtime, prefix="verification-test-")


def png(pixel=b"\x20\x40\x60"):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0" + pixel)) + chunk(b"IEND", b""))


def package(path, *, text="Fixture", notes=None, count=1, extra=None):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="' + NS + 'package/2006/content-types"/>')
        archive.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="' + NS + 'presentationml/2006/main" xmlns:r="' + NS + 'officeDocument/2006/relationships"><p:sldIdLst>' + ''.join(f'<p:sldId id="{i+255}" r:id="rId{i}"/>' for i in range(1, count+1)) + '</p:sldIdLst></p:presentation>')
        rel = '<Relationships xmlns="' + NS + 'package/2006/relationships">'
        archive.writestr("ppt/_rels/presentation.xml.rels", rel + ''.join(f'<Relationship Id="rId{i}" Type="{NS}officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>' for i in range(1,count+1)) + '</Relationships>')
        for i in range(1, count+1):
            archive.writestr(f"ppt/slides/slide{i}.xml", '<p:sld xmlns:p="' + NS + 'presentationml/2006/main" xmlns:a="' + NS + 'drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>' + escape(text) + '</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
            if notes is not None:
                archive.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", rel + f'<Relationship Id="n1" Type="{NS}officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide{i}.xml"/></Relationships>')
                archive.writestr(f"ppt/notesSlides/notesSlide{i}.xml", '<p:notes xmlns:p="' + NS + 'presentationml/2006/main" xmlns:a="' + NS + 'drawingml/2006/main"><p:cSld><a:p><a:r><a:t>' + escape(notes) + '</a:t></a:r></a:p></p:cSld></p:notes>')
        for name, content in (extra or {}).items():
            archive.writestr(name, content)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = workspace_temporary_directory()
        self.workspace = Path(self.temporary.name)
        self.project = self.workspace / "deck"
        for name in ("svg_output", "exports", "notes"):
            (self.project / name).mkdir(parents=True)
        (self.project / "svg_output/P01.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Fixture</text></svg>')
        self.direction = {"route": "main-svg-generation", "design_spec": "Approved design\n", "spec_lock": "Approved lock\n"}
        (self.project / "design_spec.md").write_text(self.direction["design_spec"])
        (self.project / "spec_lock.md").write_text(self.direction["spec_lock"])
        self.pptx = self.project / "exports/candidate.pptx"
        package(self.pptx)
        self.intent = {"slides": [{"uid": "slide-1"}], "requirements": [{"legacy_id": "EVID-P01-01"}], "fields": {"speaker_notes": {"value": False}}}
        self.research = {"claims": [{"id": "claim-1"}]}
        self.data = {"project_path": "deck", "pptx_path": "deck/exports/candidate.pptx", "pages": [{"slide_uid": "slide-1", "path": "deck/svg_output/P01.svg", "claim_ids": ["claim-1"]}], "verdict": "PASS", "contact_sheet_path": "worker-forged.png"}

    def tearDown(self):
        self.temporary.cleanup()

    def run_verifier(self, mutation=None, *, exit_code=0, officecli="mock-officecli", image=None, cancelled=None):
        def external(command, **kwargs):
            self.assertEqual(command[-1], "--require-render-success")
            self.assertEqual(kwargs["timeout"], 600)
            self.assertIs(kwargs["cancelled"], cancelled)
            contact = self.project / "_pptx_render/candidate-grid.png"
            if officecli:
                contact.write_bytes(png() if image is None else image)
            if mutation:
                mutation()
            return subprocess.CompletedProcess(command, exit_code, stdout="mocked external verifier", stderr="")
        with patch("presentation_agents.v2.verification.legacy._run_bounded_subprocess", side_effect=external), patch(
            "presentation_agents.v2.verification.legacy._officecli_bin", return_value=officecli
        ), patch("presentation_agents.v2.verification.legacy._render_macos_quicklook_contact_sheet", return_value=False):
            return verify_candidate(REPO, self.workspace, self.data, self.intent, self.research, self.direction, cancelled=cancelled)

    def test_cancelled_candidate_never_starts_verifier_or_returns_receipt(self):
        with patch.object(legacy, "_run_bounded_subprocess") as external:
            with self.assertRaisesRegex(ContractError, "cancelled"):
                verify_candidate(REPO, self.workspace, self.data, self.intent, self.research, self.direction, cancelled=lambda: True)
            external.assert_not_called()
        state = {"cancelled": False}
        with self.assertRaisesRegex(ContractError, "cancelled"):
            self.run_verifier(lambda: state.update(cancelled=True), cancelled=lambda: state["cancelled"])
        self.assertFalse((self.project / "_pptx_render/candidate-grid.png").exists())

    def test_verifier_process_cancellation_is_a_contract_failure(self):
        callback = lambda: False
        with patch.object(legacy, "_run_bounded_subprocess", side_effect=legacy.SubprocessCancelled("fixture cancelled")) as external:
            with self.assertRaisesRegex(ContractError, "cancelled"):
                verify_candidate(REPO, self.workspace, self.data, self.intent, self.research, self.direction, cancelled=callback)
            self.assertIs(external.call_args.kwargs["cancelled"], callback)

    def test_fallback_render_receives_and_propagates_cancellation(self):
        callback = lambda: False
        with patch.object(legacy, "_run_bounded_subprocess", return_value=subprocess.CompletedProcess([], 0, "", "")), patch.object(
            legacy, "_officecli_bin", return_value=None
        ), patch.object(legacy, "_render_macos_quicklook_contact_sheet", side_effect=legacy.SubprocessCancelled("fixture render cancelled")) as render:
            with self.assertRaisesRegex(ContractError, "cancelled"):
                verify_candidate(REPO, self.workspace, self.data, self.intent, self.research, self.direction, cancelled=callback)
            self.assertIs(render.call_args.kwargs["cancelled"], callback)

    def _isolated_fallback(self, mutate=None, *, emit_provenance=True, cancelled=None):
        def rendered(candidate, contact, **kwargs):
            contact.write_bytes(png())
            directory = contact.parent / f'{contact.stem}-pages'
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir()
            page_image = directory / 'P01.png'
            Image.new('RGB', (1920, 1080), 'white').save(page_image)
            if emit_provenance:
                proof = {"schema_version": "quicklook-render.v1", "method": "isolated-slide-selection", "source_sha256": file_hash(candidate),
                         "contact_sheet_sha256": file_hash(contact), "slide_count": 1, "pages": [{"page": 1, "image_path": f'{contact.stem}-pages/P01.png',
                            "image_sha256": file_hash(page_image), "image_width": 1920, "image_height": 1080}]}
                if mutate:
                    mutate(proof)
                contact.with_suffix('.render.json').write_text(json.dumps(proof))
            return True
        with patch.object(legacy, '_run_bounded_subprocess', return_value=subprocess.CompletedProcess([], 0, '', '')), patch.object(
            legacy, '_officecli_bin', return_value=None
        ), patch.object(legacy, '_render_macos_quicklook_contact_sheet', side_effect=rendered):
            return verify_candidate(REPO, self.workspace, self.data, self.intent, self.research, self.direction, cancelled=cancelled)

    def test_isolated_fallback_pass_binds_provenance_as_an_artifact(self):
        receipt = self._isolated_fallback()
        self.assertEqual(receipt['verdict'], 'PASS')
        self.assertEqual(receipt['renderer'], 'macos-quicklook-webkit')
        proof_artifact = next(a for a in receipt['artifacts'] if a['kind'] == 'render_provenance')
        proof_path = self.workspace / proof_artifact['path']
        self.assertEqual(proof_artifact['sha256'], file_hash(proof_path))
        self.assertEqual(proof_artifact['bytes'], proof_path.stat().st_size)
        proof = json.loads(proof_path.read_text())
        self.assertEqual(proof['source_sha256'], receipt['pptx_sha256'])
        self.assertEqual(proof['contact_sheet_sha256'], receipt['contact_sheet_sha256'])
        self.assertEqual(next(a['sha256'] for a in receipt['artifacts'] if a['kind'] == 'contact_sheet'),
                         receipt['contact_sheet_sha256'])

    def test_isolated_fallback_rejects_wrong_source_image_count_and_order(self):
        for key, value in [('source_sha256', '0' * 64), ('contact_sheet_sha256', '0' * 64),
                           ('slide_count', 2), ('slide_count', True), ('pages', [{'page': 2}]),
                           ('pages', [{'page': True}])]:
            with self.subTest(key=key), self.assertRaisesRegex(ContractError, 'provenance'):
                self._isolated_fallback(lambda proof: proof.update({key: value}))

    def test_isolated_fallback_requires_new_provenance(self):
        with self.assertRaisesRegex(ContractError, 'provenance'):
            self._isolated_fallback(emit_provenance=False)

    def test_isolated_fallback_requires_the_exact_provenance_schema(self):
        for schema in (None, "quicklook-render.v0", {"schema": "quicklook-render.v1"}):
            with self.subTest(schema=schema), self.assertRaisesRegex(ContractError, 'provenance'):
                self._isolated_fallback(lambda proof: proof.update(schema_version=schema))

    def test_render_outputs_changed_after_provenance_validation_are_rejected(self):
        original_capture = verification_module._gate_capture
        for target in ('contact_sheet', 'render_provenance', 'render_page'):
            calls = 0
            def capture(*args, **kwargs):
                nonlocal calls
                result = original_capture(*args, **kwargs)
                calls += 1
                if calls == 2:
                    contact = self.project / '_pptx_render/candidate-grid.png'
                    if target == 'contact_sheet':
                        contact.write_bytes(png(b'\xff\x00\x00'))
                    elif target == 'render_page':
                        (contact.parent / f'{contact.stem}-pages/P01.png').write_bytes(png())
                    else:
                        proof_path = contact.with_suffix('.render.json')
                        proof = json.loads(proof_path.read_text())
                        proof['slide_count'] = 99
                        proof_path.write_text(json.dumps(proof))
                return result
            with self.subTest(target=target), patch.object(verification_module, '_gate_capture', side_effect=capture):
                with self.assertRaisesRegex(ContractError, 'render.*changed'):
                    self._isolated_fallback()

    def test_trusted_receipt_binds_package_inputs_rules_and_real_png(self):
        receipt = self.run_verifier()
        self.assertEqual(receipt["verdict"], "PASS")
        self.assertEqual(receipt["package"]["slide_count"], 1)
        self.assertNotEqual(receipt["contact_sheet_path"], self.data["contact_sheet_path"])
        for key in ("pptx_sha256", "contact_sheet_sha256", "owner_sha256", "validator_bundle_sha256"):
            self.assertEqual(len(receipt[key]), 64)
        self.assertIn("deck/spec_lock.md", receipt["input_sha256"])
        self.assertTrue(any(a["kind"] == "verification_log" for a in receipt["artifacts"]))

    def test_worker_pass_never_overrides_external_failure(self):
        with self.assertRaisesRegex(ContractError, "owner verifier failed"):
            self.run_verifier(exit_code=1)

    def test_environment_change_invalidates_receipt_without_attempt_workspace(self):
        receipt = self.run_verifier()
        self.assertTrue(receipt_environment_matches(REPO, receipt))
        from presentation_agents.v2 import verification
        real_hash = verification.file_hash
        def changed_hash(path):
            return "0" * 64 if Path(path).name == "verification.py" else real_hash(path)
        with patch.object(verification, "file_hash", side_effect=changed_hash):
            self.assertFalse(receipt_environment_matches(REPO, receipt))
        old_receipt = copy.deepcopy(receipt)
        old_receipt.pop("verifier_environment")
        self.assertFalse(receipt_environment_matches(REPO, old_receipt))

    def test_every_existing_input_is_returned_for_canonical_preservation(self):
        images = self.project / "images"
        images.mkdir()
        (images / "photo.png").write_bytes(png())
        (self.project / "svg_output/P01.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><image href="../images/photo.png"/></svg>')
        receipt = self.run_verifier()
        artifacts = {artifact["path"]: artifact for artifact in receipt["artifacts"]}
        for relative, expected in receipt["input_sha256"].items():
            if expected is not None:
                self.assertEqual(artifacts[relative]["sha256"], expected)
        self.assertEqual(artifacts["deck/design_spec.md"]["kind"], "spec")
        self.assertEqual(artifacts["deck/images/photo.png"]["kind"], "input")
        self.assertFalse(any(".." in Path(relative).parts for relative in artifacts))

    def test_direct_routes_block_without_demanding_svg(self):
        for route in ("template-fill", "native-enhance"):
            with self.subTest(route=route), self.assertRaisesRegex(VerificationBlocked, "owner verification adapter"):
                verify_candidate(REPO, self.workspace, {}, {}, {}, {"route": route})

    def test_paths_and_symlinks_are_rejected(self):
        for relative in ("../candidate.pptx", "/tmp/candidate.pptx"):
            self.data["pptx_path"] = relative
            with self.assertRaises(ContractError):
                self.run_verifier()
        link = self.project / "exports/link.pptx"
        link.symlink_to(self.pptx)
        self.data["pptx_path"] = "deck/exports/link.pptx"
        with self.assertRaisesRegex(ContractError, "symlink"):
            self.run_verifier()

    def test_g3_direction_drift_is_blocked(self):
        (self.project / "spec_lock.md").write_text("Unapproved design change")
        with self.assertRaisesRegex(ContractError, "G3-approved"):
            self.run_verifier()

    def test_page_claims_and_evidence_references_are_validated(self):
        self.data["pages"][0]["claim_ids"] = ["claim-undefined"]
        with self.assertRaisesRegex(ContractError, "undefined claim"):
            self.run_verifier()
        self.data["pages"][0]["claim_ids"] = ["claim-1"]
        package(self.pptx, text="EVID-P01-99")
        with self.assertRaisesRegex(ContractError, "undefined evidence/claim"):
            self.run_verifier()

    def test_pptx_notes_are_inspected_even_when_notes_were_not_required(self):
        package(self.pptx, notes="[CLAIM: claim-undefined]")
        with self.assertRaisesRegex(ContractError, "undefined evidence/claim"):
            self.run_verifier()

    def test_split_xml_text_runs_do_not_hide_unknown_evidence(self):
        slide = '<p:sld xmlns:p="' + NS + 'presentationml/2006/main" xmlns:a="' + NS + 'drawingml/2006/main"><p:cSld><p:spTree><p:sp><a:p><a:r><a:t>EVID-P01</a:t></a:r><a:r><a:t>-99</a:t></a:r></a:p></p:sp></p:spTree></p:cSld></p:sld>'
        package(self.pptx, extra={"ppt/slides/slide77.xml": slide})
        with self.assertRaisesRegex(ContractError, "undefined evidence/claim"):
            self.run_verifier()

    def test_required_notes_have_source_and_package_mapping(self):
        self.intent["fields"]["speaker_notes"]["value"] = True
        with self.assertRaisesRegex(ContractError, "speaker notes are missing"):
            self.run_verifier()
        note = self.project / "notes/P01.md"
        note.write_text("[CLAIM: claim-1] EVID-P01-01")
        self.data["pages"][0]["notes_path"] = "deck/notes/P01.md"
        with self.assertRaisesRegex(ContractError, "notes are missing from a PPTX"):
            self.run_verifier()
        package(self.pptx, notes=note.read_text())
        self.assertEqual(self.run_verifier()["package"]["notes_count"], 1)

    def test_mismatched_slide_count_and_selected_candidate_are_rejected(self):
        package(self.pptx, count=2)
        with self.assertRaisesRegex(ContractError, "slide count"):
            self.run_verifier()
        package(self.pptx)
        other = self.project / "exports/newer.pptx"
        package(other)
        os.utime(other, ns=(self.pptx.stat().st_atime_ns, self.pptx.stat().st_mtime_ns + 1_000_000_000))
        with self.assertRaisesRegex(ContractError, "selected export"):
            self.run_verifier()

    def test_exported_notes_must_keep_the_source_reference_mapping(self):
        note = self.project / "notes/P01.md"
        note.write_text("[CLAIM: claim-1] EVID-P01-01")
        self.data["pages"][0]["notes_path"] = "deck/notes/P01.md"
        package(self.pptx, notes="Text with omitted source references")
        with self.assertRaisesRegex(ContractError, "exported notes references"):
            self.run_verifier()
        package(self.pptx, notes=note.read_text())
        note.rename(self.project / "notes/unmapped.md")
        self.data["pages"][0]["notes_path"] = "deck/notes/unmapped.md"
        with self.assertRaisesRegex(ContractError, "notes filename"):
            self.run_verifier()

    def test_zip_traversal_and_compression_bombs_are_rejected(self):
        package(self.pptx, extra={"../outside": "not extracted"})
        with self.assertRaisesRegex(ContractError, "traverses"):
            self.run_verifier()
        package(self.pptx, extra={"ppt/media/bomb.bin": b"0" * 1_000_000})
        with self.assertRaisesRegex(ContractError, "compression ratio"):
            self.run_verifier()

    def test_input_mutation_during_external_validation_is_rejected(self):
        def mutate():
            target = self.project / "svg_output/P01.svg"
            old = target.stat()
            target.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Changed</text></svg>')
            os.utime(target, ns=(old.st_atime_ns, old.st_mtime_ns))
        with self.assertRaisesRegex(ContractError, "changed during verification"):
            self.run_verifier(mutate)

    def test_missing_renderer_blocks_and_invalid_png_fails(self):
        with self.assertRaises(VerificationBlocked):
            self.run_verifier(officecli=None)
        with self.assertRaisesRegex(ContractError, "PNG"):
            self.run_verifier(image=b"worker says rendered")

    def test_external_media_relationship_is_rejected_before_render(self):
        package(self.pptx, extra={"ppt/slides/_rels/slide1.xml.rels": '<Relationships xmlns="' + NS + 'package/2006/relationships"><Relationship Id="image1" Type="' + NS + 'officeDocument/2006/relationships/image" TargetMode="External" Target="https://example.invalid/image.png"/></Relationships>'})
        with self.assertRaisesRegex(ContractError, "external PPTX media"):
            self.run_verifier()


class CancellationProcessTests(unittest.TestCase):
    """Actual local child sessions; Office/QuickLook integration remains mocked."""

    def test_pre_cancelled_process_is_never_started(self):
        with patch.object(legacy.subprocess, "Popen") as start:
            with self.assertRaises(legacy.SubprocessCancelled):
                legacy._run_bounded_subprocess([sys.executable, "-c", "pass"], timeout=10, cancelled=lambda: True)
            start.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "nested session termination is a POSIX contract")
    def test_cancellation_terminates_actual_nested_child_sessions(self):
        with workspace_temporary_directory() as folder:
            marker = Path(folder) / "children.json"
            leaf = "import time; time.sleep(60)"
            middle = (
                "import json,os,subprocess,sys; from pathlib import Path; "
                f"child=subprocess.Popen([sys.executable,'-c',{leaf!r}],start_new_session=True); "
                f"Path({str(marker)!r}).write_text(json.dumps([os.getpid(),child.pid])); child.wait()"
            )
            parent = (
                "import subprocess,sys; "
                f"child=subprocess.Popen([sys.executable,'-c',{middle!r}],start_new_session=True); child.wait()"
            )
            started = time.monotonic()
            pids = []
            try:
                with self.assertRaises(legacy.SubprocessCancelled):
                    legacy._run_bounded_subprocess([sys.executable, "-c", parent], timeout=10, cancelled=marker.is_file)
                self.assertLess(time.monotonic() - started, 10)
                pids = json.loads(marker.read_text())
                self.assertEqual(len(pids), 2)
                deadline = time.monotonic() + 2
                running = pids
                while running and time.monotonic() < deadline:
                    running = []
                    for pid in pids:
                        result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=2)
                        if result.stdout.strip() and not result.stdout.strip().startswith("Z"):
                            running.append(pid)
                    if running:
                        time.sleep(0.05)
                self.assertEqual(running, [], "cancelled descendants still execute")
            finally:
                if not pids and marker.is_file():
                    pids = json.loads(marker.read_text())
                for pid in pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_quicklook_and_swift_receive_cancellation_callback(self):
        with workspace_temporary_directory() as folder:
            output = Path(folder) / "contact.png"
            package(Path(folder) / "deck.pptx")
            callback = lambda: False
            def external(command, **kwargs):
                self.assertIs(kwargs["cancelled"], callback)
                if "qlmanage" in command[0]:
                    (Path(command[command.index("-o") + 1]) / "Preview.html").write_text("fixture")
                    return subprocess.CompletedProcess(command, 0, "", "")
                output.write_bytes(png())
                raise legacy.SubprocessCancelled("fixture Swift cancelled")
            with patch.object(legacy.sys, "platform", "darwin"), patch.object(legacy.shutil, "which", side_effect=lambda name: "/fixture/" + name), patch.object(
                legacy, "_run_bounded_subprocess", side_effect=external
            ) as run:
                with self.assertRaises(legacy.SubprocessCancelled):
                    legacy._render_macos_quicklook_contact_sheet(Path(folder) / "deck.pptx", output, cancelled=callback)
            self.assertEqual(run.call_count, 2)
            self.assertFalse(output.exists())

    @unittest.skipUnless(os.name == "posix", "inherited process groups are a POSIX contract")
    def test_cancellation_remains_active_while_descendant_holds_output_pipe(self):
        with workspace_temporary_directory() as folder:
            marker = Path(folder) / "pipe-child.json"
            code = (
                "import json,subprocess,sys; from pathlib import Path; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                f"Path({str(marker)!r}).write_text(json.dumps(child.pid))"
            )
            started = time.monotonic()
            try:
                with self.assertRaises(legacy.SubprocessCancelled):
                    legacy._run_bounded_subprocess(
                        [sys.executable, "-c", code], timeout=10,
                        cancelled=lambda: marker.is_file() and time.monotonic() - started >= 0.5,
                    )
                self.assertLess(time.monotonic() - started, 3)
                child = json.loads(marker.read_text())
                result = subprocess.run(["ps", "-o", "stat=", "-p", str(child)], capture_output=True, text=True, timeout=2)
                self.assertTrue(not result.stdout.strip() or result.stdout.strip().startswith("Z"))
            finally:
                if marker.is_file():
                    try:
                        os.kill(json.loads(marker.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass


if __name__ == "__main__":
    unittest.main()
