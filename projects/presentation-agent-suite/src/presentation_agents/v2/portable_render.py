"""Render the submitted PPTX through LibreOffice, then rasterize its actual PDF."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

def find_soffice() -> str | None:
    bundled = os.environ.get('INTENT_SLIDE_SOFFICE')
    if bundled and Path(bundled).is_file():
        return bundled
    candidates = [shutil.which('soffice.com'), shutil.which('soffice')]
    if os.name == 'nt':
        for key in ('ProgramFiles', 'ProgramFiles(x86)'):
            if os.environ.get(key):
                candidates.append(str(Path(os.environ[key])/'LibreOffice/program/soffice.com'))
    else:
        candidates.append('/Applications/LibreOffice.app/Contents/MacOS/soffice')
    return next((name for name in candidates if name and Path(name).is_file()), None)


def render_pptx(candidate: Path, contact: Path, *, cancelled=None, timeout=180) -> dict:
    """Return bound page provenance. Failures never yield a reusable render."""
    from PIL import Image, ImageOps
    import pymupdf
    from presentation_agents import pipeline as legacy

    binary = find_soffice()
    if not binary:
        raise RuntimeError('내장 PPTX 렌더러를 찾지 못했습니다. 완전한 배포본을 확인하세요.')
    candidate = candidate.resolve(strict=True)
    contact.parent.mkdir(parents=True, exist_ok=True)
    pages_dir = contact.parent / f'{contact.stem}-pages'
    if contact.is_symlink() or pages_dir.exists() or contact.exists():
        raise ValueError('Fresh render destinations are required')
    source_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
    pages_dir.mkdir()
    success = False
    try:
        with tempfile.TemporaryDirectory(prefix='pptx-render-', dir=contact.parent) as temporary:
            scratch = Path(temporary)
            # Read-only source copy; profile and PDF stay inside this attempt.
            deck = scratch/'candidate.pptx'
            shutil.copyfile(candidate, deck)
            if hashlib.sha256(deck.read_bytes()).hexdigest() != source_hash:
                raise ValueError('PPTX render snapshot changed during copy')
            profile = scratch/'profile'
            profile.mkdir()
            (profile/'user').mkdir()
            (profile/'user/registrymodifications.xcu').write_text(
                '<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
                '<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
                '<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop></item>'
                '<item oor:path="/org.openoffice.Office.Update"><prop oor:name="Enabled" oor:op="fuse"><value>false</value></prop></item>'
                '<item oor:path="/org.openoffice.Office.Jobs/Jobs/UpdateCheck/Arguments"><prop oor:name="AutoCheckEnabled" oor:op="fuse"><value>false</value></prop><prop oor:name="AutoDownloadEnabled" oor:op="fuse"><value>false</value></prop></item></oor:items>', encoding='utf-8')
            command = [binary, '-env:UserInstallation='+profile.as_uri(), '--headless', '--nologo', '--nodefault', '--norestore',
                       '--convert-to', 'pdf:impress_pdf_Export', '--outdir', str(scratch), str(deck)]
            result = legacy._run_bounded_subprocess(command, cwd=scratch, env=os.environ.copy(), timeout=timeout, cancelled=cancelled)
            if result.returncode:
                raise RuntimeError('LibreOffice did not render the submitted PPTX')
            pdf = scratch/'candidate.pdf'
            if not pdf.is_file() or pdf.is_symlink() or not 1 <= pdf.stat().st_size <= 256*1024*1024:
                raise RuntimeError('Renderer did not produce a bounded PDF')
            entries = []
            thumbs = []
            with pymupdf.open(pdf) as document:
                if not 1 <= len(document) <= 100:
                    raise ValueError('Rendered page count is outside bounds')
                for number, page in enumerate(document, 1):
                    if cancelled and cancelled():
                        raise legacy.SubprocessCancelled('PPTX rasterization cancelled')
                    if page.rect.width <= 0 or page.rect.height <= 0:
                        raise ValueError('Invalid rendered page dimensions')
                    scale = 1920/page.rect.width
                    if math.ceil(page.rect.height*scale)*1920 > 16_000_000:
                        raise ValueError('Rendered page exceeds pixel bounds')
                    bitmap = page.get_pixmap(matrix=pymupdf.Matrix(scale,scale), alpha=False)
                    name = f'P{number:02d}.png'
                    path = pages_dir/name
                    bitmap.save(path)
                    data = path.read_bytes()
                    legacy._validate_png_bytes(data)
                    entries.append({'page':number, 'image_path':f'{pages_dir.name}/{name}',
                                    'image_width':bitmap.width, 'image_height':bitmap.height,
                                    'image_sha256':hashlib.sha256(data).hexdigest()})
                    with Image.open(path) as image:
                        thumbnail = ImageOps.contain(image.convert('RGB'), (480,360))
                        cell = Image.new('RGB', (480,360), 'white')
                        cell.paste(thumbnail, ((480-thumbnail.width)//2, (360-thumbnail.height)//2))
                        thumbs.append(cell)
            columns = min(3,len(thumbs))
            sheet = Image.new('RGB', (columns*480, math.ceil(len(thumbs)/columns)*360), '#E5E7EB')
            for index, thumb in enumerate(thumbs):
                sheet.paste(thumb, ((index%columns)*480,(index//columns)*360))
            sheet.save(contact)
            if hashlib.sha256(candidate.read_bytes()).hexdigest() != source_hash:
                raise ValueError('PPTX changed during rendering')
            proof = {'schema_version':'portable-render.v1', 'method':'libreoffice-pdf-pages', 'source_sha256':source_hash,
                     'contact_sheet_sha256':hashlib.sha256(contact.read_bytes()).hexdigest(), 'slide_count':len(entries), 'pages':entries}
            contact.with_suffix('.render.json').write_text(json.dumps(proof,sort_keys=True),encoding='utf-8')
            success=True
            return proof
    finally:
        if not success:
            contact.unlink(missing_ok=True)
            contact.with_suffix('.render.json').unlink(missing_ok=True)
            shutil.rmtree(pages_dir)
