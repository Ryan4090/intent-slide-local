# Bundled Fonts

## Pretendard (bundled default deck font)

[Pretendard](https://github.com/orioncactus/pretendard) v1.3.9 — SIL Open Font License 1.1
(`Pretendard/LICENSE.txt`). Korean + Latin coverage in one family; the fixed typography
choice for every deck generated in this repo (see `references/strategist.md §g`
bundled font lock).

Bundled static cuts (OTF): Light(300) / Regular(400) / Medium(500) / SemiBold(600) /
Bold(700) / ExtraBold(800). The full 9-weight set is available from the upstream release.

### Family names as installed on Windows

| SVG `font-family` | Weight | Notes |
|---|---|---|
| `Pretendard` | 400 / 700 via `font-weight` | Regular and Bold fold into one family |
| `Pretendard Light` | use normal weight | separate installed family name |
| `Pretendard Medium` | use normal weight | separate installed family name |
| `Pretendard SemiBold` | use normal weight | separate installed family name |
| `Pretendard ExtraBold` | use normal weight | separate installed family name |

Recommended deck stack: `Pretendard, "Malgun Gothic", sans-serif` (tail is browser-preview
fallback only; the converter exports Pretendard into both the Latin and EA typeface slots —
registered in `scripts/svg_to_pptx/drawingml/utils.py DUAL_SCRIPT_FONTS`).

### Portable PPTX export

The native SVG exporter embeds the selected bundled Pretendard faces in the final
PPTX as uncompressed EOT, retaining the original OTF bytes and SIL OFL notice.
Regular/Bold and the four named intermediate families above are supported. It does
not install fonts globally or change the signed renderer. These faces need no
separate installation for Intent-Slide's bundled LibreOffice rendering.

Keep the exported PPTX unchanged through G4/G5. Inspect its actual rendered pages;
source SVG text extraction alone cannot prove that Korean glyphs are visible.
Other fonts or export paths need their own valid font source and verification.
Microsoft PowerPoint display/editing and recipient settings are a separate check;
do not claim that every viewer honors embedded fonts.
