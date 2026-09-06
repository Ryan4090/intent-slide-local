"""Embed reviewed bundled faces in the final PPTX before package validation.

EOT v1 stores the original OpenType bytes without subsetting or compression.
LibreOffice 26.8 reads this through p:embeddedFontLst and registers the font for
the document/process, so rendering needs no global font installation.
Sources: https://www.w3.org/submissions/EOT/ (section 3.1),
https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oe376/1663dabc-5d98-463f-889e-bcd9b77c3d34
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import posixpath
import struct

from lxml import etree as ET  # Existing python-pptx runtime dependency.

P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
BUNDLED_FONT_DIR = Path(__file__).resolve().parents[3] / 'assets/fonts/Pretendard'
BUNDLED_SHA256 = {
    'Pretendard-Regular.otf': '3ffbacde6ab8411f1d2db54bb9b1f0b3ee2a738932033722cf0388c06aed1c93',
    'Pretendard-Bold.otf': '2e91915fab54df71cc9598ebf608b2bdb54c6fe3c066ac61dff0bc44fca71cc7',
    'Pretendard-Light.otf': '51418ee96ef96e9059db88d7da82f24fada1f4aca70d5157c878ea6b460dd343',
    'Pretendard-Medium.otf': 'd39e50e4bb52b4993b6a4eeb821a171254745bd824446af01e1f616b89fface0',
    'Pretendard-SemiBold.otf': 'c89bc43027dc7cde5726e96223376f8eec09302b2fc1f8147fd5b57cfc376118',
    'Pretendard-ExtraBold.otf': 'c35fe941b7568d52a96010561540e47f9d3948dfde66ba25bc1908233e0a40cd',
    'LICENSE.txt': 'b04538c9abec39a3db75108cf0af0fd9c77032fe8aa2cf38345b4d250e98e38e',
}


def _font_bytes(name: str) -> bytes:
    # Names come only from the fixed catalog below, never from an input path.
    path = BUNDLED_FONT_DIR / name
    if path.is_symlink() or path.resolve().parent != BUNDLED_FONT_DIR.resolve():
        raise ValueError('Bundled font integrity: unexpected font path')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != BUNDLED_SHA256[name]:
        raise ValueError(f'Bundled font integrity mismatch: {name}')
    return data


def make_eot(font: bytes) -> bytes:
    """Wrap an editable original OpenType font; never rewrite its payload."""
    if len(font) < 12 or font[:4] not in (b'OTTO', b'\x00\x01\x00\x00'):
        raise ValueError('Invalid OpenType header')
    count = struct.unpack_from('>H', font, 4)[0]
    if not 1 <= count <= 256 or 12 + count * 16 > len(font):
        raise ValueError('Invalid OpenType directory')
    tables = {}
    for index in range(count):
        tag, _, offset, size = struct.unpack_from('>4sIII', font, 12 + index * 16)
        if offset + size > len(font) or tag in tables:
            raise ValueError('Invalid OpenType table bounds')
        tables[tag] = font[offset:offset + size]
    os2, head, name = (tables.get(tag, b'') for tag in (b'OS/2', b'head', b'name'))
    if len(os2) < 86 or len(head) < 12 or len(name) < 6:
        raise ValueError('Required OpenType metadata is missing')
    u16 = lambda data, at: struct.unpack_from('>H', data, at)[0]
    u32 = lambda data, at: struct.unpack_from('>I', data, at)[0]
    # This path supports the reviewed installable/embedding rights only. It
    # never clears a restriction or promises support for arbitrary font EULAs.
    if u16(os2, 8) != 0:
        raise ValueError('Bundled font embedding rights must be unrestricted')
    _, records, storage = struct.unpack_from('>HHH', name)
    if 6 + records * 12 > len(name):
        raise ValueError('Invalid OpenType names')
    names = {}
    for index in range(records):
        platform, _, language, key, size, offset = struct.unpack_from('>6H', name, 6 + index * 12)
        if storage + offset + size > len(name):
            raise ValueError('Invalid OpenType name bounds')
        if platform == 3 and language == 0x409 and key in (1, 2, 4, 5):
            names[key] = name[storage + offset:storage + offset + size].decode('utf-16be').encode('utf-16le')
    if set(names) != {1, 2, 4, 5}:
        raise ValueError('English OpenType family/style/version names are required')
    header = bytearray(struct.pack('<4I10sBBIHH', 0, len(font), 0x10000, 0,
                                  os2[32:42], 1, u16(os2, 62) & 1, u16(os2, 4), 0, 0x504c))
    header += struct.pack('<7I', *(u32(os2, at) for at in (42, 46, 50, 54, 78, 82)), u32(head, 8))
    header += bytes(16)
    for key in (1, 2, 5, 4):
        header += struct.pack('<HH', 0, len(names[key])) + names[key]
    struct.pack_into('<I', header, 0, len(header) + len(font))
    return bytes(header) + font


def _parse(data: bytes):
    if b'<!DOCTYPE' in data.upper():
        raise ValueError('Font packaging does not accept document type declarations')
    return ET.fromstring(data, parser=ET.XMLParser(resolve_entities=False, no_network=True))


def _used_faces(root: Path) -> set[tuple[str, bool]]:
    aliases: dict[str, set[str]] = {}
    for path in (root / 'ppt/theme').glob('*.xml'):
        theme = _parse(path.read_bytes())
        for prefix, kind in (('mj', 'majorFont'), ('mn', 'minorFont')):
            for scheme in theme.iter(f'{{{A}}}{kind}'):
                for suffix, element in (('lt', 'latin'), ('ea', 'ea'), ('cs', 'cs')):
                    font = scheme.find(f'{{{A}}}{element}')
                    if font is not None and font.get('typeface'):
                        aliases.setdefault(f'+{prefix}-{suffix}', set()).add(font.get('typeface'))
    used = set()
    # Themes are catalogs; only references in document content select a face.
    for folder in ('slides', 'slideLayouts', 'slideMasters', 'notesSlides', 'notesMasters', 'charts'):
        for path in (root / 'ppt' / folder).glob('*.xml'):
            document = _parse(path.read_bytes())
            for node in document.iter():
                if node.tag not in {f'{{{A}}}latin', f'{{{A}}}ea', f'{{{A}}}cs'}:
                    continue
                face = node.get('typeface', '')
                parent = node.getparent()
                bold = parent is not None and parent.get('b') in ('1', 'true')
                used.update((value, bold) for value in aliases.get(face, {face}))
            # Native SVG runs are explicit. Also cover ordinary paragraph/list
            # inheritance retained in a source template without inventing faces.
            for run in document.iter(f'{{{A}}}r', f'{{{A}}}fld'):
                properties = run.find(f'{{{A}}}rPr')
                if properties is None or properties.get('b') not in ('1', 'true'):
                    continue
                paragraph = run.getparent()
                defaults = [paragraph.find(f'{{{A}}}pPr/{{{A}}}defRPr')]
                ppr = paragraph.find(f'{{{A}}}pPr')
                level = ppr.get('lvl', '0') if ppr is not None else '0'
                if level.isdigit() and int(level) < 9:
                    body = paragraph.getparent()
                    if body is not None:
                        defaults.append(body.find(f'{{{A}}}lstStyle/{{{A}}}lvl{int(level) + 1}pPr/{{{A}}}defRPr'))
                for tag in ('latin', 'ea', 'cs'):
                    font = properties.find(f'{{{A}}}{tag}')
                    if font is None:
                        font = next((candidate.find(f'{{{A}}}{tag}') for candidate in defaults
                                     if candidate is not None and candidate.find(f'{{{A}}}{tag}') is not None), None)
                    if font is not None:
                        face = font.get('typeface', '')
                        used.update((value, True) for value in aliases.get(face, {face}))
    return used


def _catalog(face: str, bold: bool) -> tuple[str, str] | None:
    normalized = face.casefold().strip()
    if normalized in ('pretendard', 'pretendard regular', 'pretendard-regular'):
        return ('Pretendard-Bold.otf', 'bold') if bold else ('Pretendard-Regular.otf', 'regular')
    for suffix in ('Bold', 'Light', 'Medium', 'SemiBold', 'ExtraBold'):
        if normalized in (f'pretendard {suffix}'.casefold(), f'pretendard-{suffix}'.casefold()):
            return f'Pretendard-{suffix}.otf', 'regular'
    return None


def embed_bundled_fonts(root: Path) -> list[dict[str, str]]:
    """Add selected font parts to the exporter's private unpacked package.

    Existing embedded families are retained. No source PPTX, global font folder,
    rendering copy or signed application is modified by this function.
    """
    requested = {}
    for face, bold in sorted(_used_faces(root)):
        match = _catalog(face, bold)
        if match:
            face = face.strip()
            requested.setdefault((face.casefold(), match[1]), (face, *match))
    selections = set(requested.values())
    if not selections:
        return []
    presentation_path = root / 'ppt/presentation.xml'
    presentation = _parse(presentation_path.read_bytes())
    font_list = presentation.find(f'{{{P}}}embeddedFontLst')
    existing = {}
    for entry in presentation.findall(f'{{{P}}}embeddedFontLst/{{{P}}}embeddedFont'):
        face = entry.find(f'{{{P}}}font')
        if face is not None:
            existing[face.get('typeface', '').strip().casefold()] = entry
    selections = {entry for entry in selections if entry[0].casefold() not in existing
                  or existing[entry[0].casefold()].find(f'{{{P}}}{entry[2]}') is None}
    if not selections:
        return []
    rel_path = root / 'ppt/_rels/presentation.xml.rels'
    relationships = _parse(rel_path.read_bytes())
    content_path = root / '[Content_Types].xml'
    content_types = _parse(content_path.read_bytes())
    # LibreOffice's OPC helper compares literal unprefixed names. lxml retains
    # default namespaces; ElementTree would silently produce ns0:Types, etc.
    if relationships.tag != f'{{{PKG}}}Relationships' or content_types.tag != f'{{{CT}}}Types' or relationships.prefix or content_types.prefix:
        raise ValueError('PPTX package requires unprefixed default namespace roots')
    ids = {element.get('Id') for element in relationships}
    # A partially embedded template may require a newly used bold slot. Add it
    # only when existing slots contain the same reviewed original font family.
    # Never silently mix an unknown font that happens to reuse its family name.
    for face in {entry[0] for entry in selections if entry[0].casefold() in existing}:
        for child in existing[face.casefold()]:
            slot = ET.QName(child).localname
            if slot == 'font':
                continue
            match = _catalog(face, slot == 'bold') if slot in ('regular', 'bold') else None
            relationship = next((node for node in relationships if node.get('Id') == child.get(f'{{{R}}}id')), None)
            if not match or relationship is None or relationship.get('Type') != R + '/font' or relationship.get('TargetMode') == 'External':
                raise ValueError('Cannot complete an unverified existing embedded font family')
            target = relationship.get('Target', '')
            part = posixpath.normpath(posixpath.join('ppt', target)) if not target.startswith('/') else target.lstrip('/')
            path = root / part
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or path.read_bytes() != make_eot(_font_bytes(match[0])):
                raise ValueError('Cannot complete an unverified existing embedded font family')
    additions = {}
    records = []
    for face, filename, slot in sorted(selections):
        data = _font_bytes(filename)
        part = 'ppt/fonts/' + filename[:-4] + '.fntdata'
        additions[part] = make_eot(data)
        record = {'family': face, 'file': filename, 'slot': slot, 'part': part,
                  'sha256': hashlib.sha256(data).hexdigest()}
        records.append(record)
    additions['ppt/fonts/Pretendard-LICENSE.txt'] = _font_bytes('LICENSE.txt')
    # Reject a collision before writing any part; never replace a user's font.
    for part, data in additions.items():
        path = root / part
        if path.is_symlink() or (path.exists() and path.read_bytes() != data):
            raise ValueError(f'Bundled font part collision: {part}')
    if font_list is None:
        font_list = ET.Element(f'{{{P}}}embeddedFontLst')
        after = {'custShowLst', 'photoAlbum', 'custDataLst', 'kinsoku', 'defaultTextStyle', 'modifyVerifier', 'extLst'}
        position = next((i for i, child in enumerate(presentation) if ET.QName(child).localname in after), len(presentation))
        presentation.insert(position, font_list)
    families = dict(existing)
    for record in records:
        face = record['family']
        key = face.casefold()
        if key not in families:
            families[key] = ET.SubElement(font_list, f'{{{P}}}embeddedFont')
            ET.SubElement(families[key], f'{{{P}}}font', typeface=face)
        rid = 'intentSlideFont' + str(len(ids) + 1)
        while rid in ids: rid += '_'
        ids.add(rid)
        order = ('font', 'regular', 'bold', 'italic', 'boldItalic')
        slot = ET.Element(f'{{{P}}}{record["slot"]}', {f'{{{R}}}id': rid}, nsmap={'r': R})
        position = next((i for i, child in enumerate(families[key])
                         if ET.QName(child).localname in order and order.index(ET.QName(child).localname) > order.index(record['slot'])), len(families[key]))
        families[key].insert(position, slot)
        ET.SubElement(relationships, f'{{{PKG}}}Relationship', Id=rid, Type=R + '/font', Target=record['part'][4:])
    for extension, mime in (('fntdata', 'application/x-fontdata'), ('txt', 'text/plain')):
        default = next((node for node in content_types if node.tag == f'{{{CT}}}Default' and node.get('Extension') == extension), None)
        if default is not None and default.get('ContentType') != mime:
            raise ValueError(f'Bundled font content type collision: {extension}')
        if default is None:
            content_types.insert(0, ET.Element(f'{{{CT}}}Default', Extension=extension, ContentType=mime))
    presentation.set('embedTrueTypeFonts', '1')
    # Entire original faces are embedded, retaining all editable characters.
    presentation.set('saveSubsetFonts', '0')
    for part, data in additions.items():
        path = root / part; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    for path, tree in ((presentation_path, presentation), (rel_path, relationships), (content_path, content_types)):
        path.write_bytes(ET.tostring(tree, xml_declaration=True, encoding='UTF-8'))
    return records
