"""Final-package font transport; no system font installation or network."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from xml.etree import ElementTree as ET

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / '.claude/skills/ppt-master/scripts'))
from svg_to_pptx.pptx_package import font_embedding as fonts

P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'


class FontEmbeddingTests(unittest.TestCase):
    def setUp(self):
        runtime = REPO / '.runtime'; runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='font-package-', dir=runtime)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def package(self, runs):
        entries = {
            'ppt/presentation.xml': f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst/><p:sldSz cx="100" cy="100"/><p:notesSz cx="100" cy="100"/><p:defaultTextStyle/></p:presentation>',
            'ppt/_rels/presentation.xml.rels': f'<Relationships xmlns="{PKG}"><Relationship Id="existing" Type="{R}/slide" Target="slides/slide1.xml"/></Relationships>',
            '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>',
            'ppt/slides/slide1.xml': f'<p:sld xmlns:p="{P}" xmlns:a="{A}">' + ''.join(f'<a:r><a:rPr b="{int(bold)}"><a:latin typeface="{face}"/><a:ea typeface="{face}"/></a:rPr><a:t>한글</a:t></a:r>' for face, bold in runs) + '</p:sld>',
        }
        for name, data in entries.items():
            p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(data)
        return entries

    def test_regular_bold_and_named_weight_embed_only_used_original_bytes(self):
        original = self.package([('Pretendard', False), ('Pretendard', True), ('Pretendard Medium', False)])
        records = fonts.embed_bundled_fonts(self.root)
        self.assertEqual({r['file'] for r in records}, {'Pretendard-Regular.otf', 'Pretendard-Bold.otf', 'Pretendard-Medium.otf'})
        for record in records:
            eot = (self.root / record['part']).read_bytes()
            total, size, version, flags = struct.unpack_from('<4I', eot)
            self.assertEqual((total, version, flags), (len(eot), 0x10000, 0))
            self.assertEqual(struct.unpack_from('<HH', eot, 32), (0, 0x504c))
            self.assertEqual(eot[-size:], (fonts.BUNDLED_FONT_DIR / record['file']).read_bytes())
        self.assertEqual((self.root / 'ppt/fonts/Pretendard-LICENSE.txt').read_bytes(), (fonts.BUNDLED_FONT_DIR / 'LICENSE.txt').read_bytes())
        self.assertEqual((self.root / 'ppt/slides/slide1.xml').read_text(), original['ppt/slides/slide1.xml'])
        presentation = ET.parse(self.root / 'ppt/presentation.xml').getroot()
        self.assertEqual(presentation.get('embedTrueTypeFonts'), '1')
        self.assertEqual([c.tag.rsplit('}', 1)[-1] for c in presentation][-2:], ['embeddedFontLst', 'defaultTextStyle'])
        regular_family = next(entry for entry in presentation.find(f'{{{P}}}embeddedFontLst') if entry.find(f'{{{P}}}font').get('typeface') == 'Pretendard')
        self.assertEqual([c.tag.rsplit('}', 1)[-1] for c in regular_family], ['font', 'regular', 'bold'])
        for name, root_name in (('[Content_Types].xml', 'Types'), ('ppt/_rels/presentation.xml.rels', 'Relationships')):
            text = (self.root / name).read_text()
            self.assertIn('<' + root_name + ' ', text)
            self.assertNotIn('ns0:', text)
        self.assertEqual(len(ET.parse(self.root / 'ppt/_rels/presentation.xml.rels').getroot()), 4)

    def test_unknown_family_is_not_read_as_a_path_and_empty_result_is_byte_identical(self):
        original = self.package([('../../private/font', False), ('Arial', False)])
        self.assertEqual(fonts.embed_bundled_fonts(self.root), [])
        for name, text in original.items(): self.assertEqual((self.root / name).read_text(), text)
        self.assertFalse((self.root / 'ppt/fonts').exists())

    def test_second_call_keeps_current_embedding_byte_identical(self):
        self.package([('Pretendard', False)])
        fonts.embed_bundled_fonts(self.root)
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(fonts.embed_bundled_fonts(self.root), [])
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_case_and_whitespace_aliases_do_not_duplicate_a_family_slot(self):
        self.package([('Pretendard', False), ('pretendard', False), (' Pretendard ', False)])
        self.assertEqual(len(fonts.embed_bundled_fonts(self.root)), 1)
        entries = ET.parse(self.root / 'ppt/presentation.xml').getroot().findall(f'{{{P}}}embeddedFontLst/{{{P}}}embeddedFont')
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0].findall(f'{{{P}}}regular')), 1)

    def test_original_embedding_is_retained_when_another_family_is_added(self):
        self.package([('Pretendard', False)])
        p = self.root / 'ppt/presentation.xml'
        entry = '<p:embeddedFontLst><p:embeddedFont><p:font typeface="BrandFont"/><p:regular r:id="brandFont"/></p:embeddedFont></p:embeddedFontLst>'
        p.write_text(p.read_text().replace('<p:defaultTextStyle/>', entry + '<p:defaultTextStyle/>'))
        fonts.embed_bundled_fonts(self.root)
        self.assertIn('<p:font typeface="BrandFont"/>', p.read_text())
        self.assertEqual(len(ET.parse(p).getroot().findall(f'{{{P}}}embeddedFontLst')), 1)

    def test_existing_regular_can_gain_new_bold_without_replacing_regular(self):
        self.package([('Pretendard', False)])
        fonts.embed_bundled_fonts(self.root)
        regular = (self.root / 'ppt/fonts/Pretendard-Regular.fntdata').read_bytes()
        p = self.root / 'ppt/slides/slide1.xml'
        p.write_text(p.read_text().replace('b="0"', 'b="1"'))
        self.assertEqual([r['file'] for r in fonts.embed_bundled_fonts(self.root)], ['Pretendard-Bold.otf'])
        self.assertEqual((self.root / 'ppt/fonts/Pretendard-Regular.fntdata').read_bytes(), regular)
        entries = ET.parse(self.root / 'ppt/presentation.xml').getroot().findall(f'{{{P}}}embeddedFontLst/{{{P}}}embeddedFont')
        self.assertEqual(len(entries), 1)
        self.assertIsNotNone(entries[0].find(f'{{{P}}}regular'))
        self.assertIsNotNone(entries[0].find(f'{{{P}}}bold'))

    def test_missing_slot_cannot_mix_with_unknown_existing_font_bytes(self):
        self.package([('Pretendard', False)])
        fonts.embed_bundled_fonts(self.root)
        (self.root / 'ppt/fonts/Pretendard-Regular.fntdata').write_bytes(b'unknown original template font')
        p = self.root / 'ppt/slides/slide1.xml'
        p.write_text(p.read_text().replace('b="0"', 'b="1"'))
        before = (self.root / 'ppt/presentation.xml').read_bytes()
        with self.assertRaisesRegex(ValueError, 'unverified existing'):
            fonts.embed_bundled_fonts(self.root)
        self.assertEqual((self.root / 'ppt/presentation.xml').read_bytes(), before)

    def test_existing_bold_gains_regular_in_schema_order(self):
        self.package([('Pretendard', True)])
        fonts.embed_bundled_fonts(self.root)
        p = self.root / 'ppt/slides/slide1.xml'
        p.write_text(p.read_text().replace('b="1"', 'b="0"'))
        fonts.embed_bundled_fonts(self.root)
        entry = ET.parse(self.root / 'ppt/presentation.xml').getroot().find(f'{{{P}}}embeddedFontLst/{{{P}}}embeddedFont')
        self.assertEqual([c.tag.rsplit('}', 1)[-1] for c in entry], ['font', 'regular', 'bold'])

    def test_theme_alias_uses_locked_font_instead_of_embedding_unused_theme_catalog(self):
        self.package([('+mn-ea', False)])
        p = self.root / 'ppt/theme/theme1.xml'; p.parent.mkdir()
        p.write_text(f'<a:theme xmlns:a="{A}"><a:themeElements><a:fontScheme><a:minorFont><a:latin typeface="Pretendard"/><a:ea typeface="Pretendard Medium"/></a:minorFont><a:majorFont><a:latin typeface="Pretendard ExtraBold"/></a:majorFont></a:fontScheme></a:themeElements></a:theme>')
        self.assertEqual({r['file'] for r in fonts.embed_bundled_fonts(self.root)}, {'Pretendard-Medium.otf'})

    def test_bold_run_inherits_paragraph_font_family(self):
        self.package([])
        p = self.root / 'ppt/slides/slide1.xml'
        p.write_text(f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:txBody><a:p><a:pPr><a:defRPr><a:latin typeface="Pretendard"/></a:defRPr></a:pPr><a:r><a:rPr b="1"/><a:t>한글</a:t></a:r></a:p></p:txBody></p:sld>')
        self.assertEqual({r['file'] for r in fonts.embed_bundled_fonts(self.root)}, {'Pretendard-Regular.otf', 'Pretendard-Bold.otf'})

    def test_font_integrity_failure_leaves_staging_unchanged(self):
        original = self.package([('Pretendard', False)])
        with patch.dict(fonts.BUNDLED_SHA256, {'Pretendard-Regular.otf': '0' * 64}):
            with self.assertRaisesRegex(ValueError, 'integrity'):
                fonts.embed_bundled_fonts(self.root)
        for name, text in original.items(): self.assertEqual((self.root / name).read_text(), text)
        self.assertFalse((self.root / 'ppt/fonts').exists())

    def test_unknown_embedding_rights_and_malformed_font_fail_before_wrapping(self):
        raw = bytearray((fonts.BUNDLED_FONT_DIR / 'Pretendard-Regular.otf').read_bytes())
        count = struct.unpack_from('>H', raw, 4)[0]
        for index in range(count):
            if raw[12 + index * 16:16 + index * 16] == b'OS/2':
                offset = struct.unpack_from('>I', raw, 20 + index * 16)[0]
                struct.pack_into('>H', raw, offset + 8, 2)
        with self.assertRaisesRegex(ValueError, 'rights'): fonts.make_eot(bytes(raw))
        with self.assertRaises(ValueError): fonts.make_eot(b'OTTO')

    def test_main_svg_export_hands_off_a_candidate_containing_original_font(self):
        from svg_to_pptx.pptx_package.builder import create_pptx_with_native_svg
        svg = self.root / 'P01.svg'
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><text x="80" y="180" font-family="Pretendard" font-size="48">한글 생계 설계</text></svg>')
        candidate = self.root / 'candidate.pptx'
        self.assertTrue(create_pptx_with_native_svg([svg], candidate, pptx_structure='flat', verbose=False, transition=None, enable_notes=False))
        with zipfile.ZipFile(candidate) as archive:
            eot = archive.read('ppt/fonts/Pretendard-Regular.fntdata')
            size = struct.unpack_from('<I', eot, 4)[0]
            self.assertEqual(eot[-size:], (fonts.BUNDLED_FONT_DIR / 'Pretendard-Regular.otf').read_bytes())
            self.assertEqual(archive.read('ppt/fonts/Pretendard-LICENSE.txt'), (fonts.BUNDLED_FONT_DIR / 'LICENSE.txt').read_bytes())
            self.assertIn('한글 생계 설계', archive.read('ppt/slides/slide1.xml').decode())


if __name__ == '__main__': unittest.main()
