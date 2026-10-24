# Portable design typography

All font files are redistributable under SIL Open Font License 1.1. See `OFL.txt`
and the upstream Noto Sans / Noto Serif family licenses. The `sans/` and `serif/`
WOFF2 subsets are the same Noto Sans SC and Noto Serif SC variable fonts already
used by MateQuill's frontend, copied here so a backend-only image does not depend
on the frontend deployment directory.

`coverage.json` indexes Unicode coverage by source file. Layout and SVG rendering
resolve the same glyph and actual `wght` axis value (200..900) using FontTools.
Titles can use real Noto Serif SC while body uses Noto Sans SC. No synthetic bold
stroke or browser font fallback is used. Each artboard's typography metadata lists
actual source files, actual weights, and any fallback characters.

`NotoSansCJKsc-Regular.otf` is Noto Sans CJK SC Regular from upstream
[notofonts/noto-cjk](https://github.com/notofonts/noto-cjk),
`Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf`. It covers additional rare
CJK characters not present in frontend subsets. Such fallback is explicitly
reported as sans / 400 rather than pretending to be the requested serif weight.

Native text remains in each PosterDocument layer and is re-rendered after edits.
Exported SVG typography is outlined for portable appearance and includes original
text as accessible labels. It should not be described as native editable SVG type.

Pillow's compatibility helper loads bytes into memory (needed on some Windows
FreeType builds); core design measurements use the exact FontTools glyph advances.
No fonts are fetched at request time.

`NotoEmoji-wght.ttf` is the Google Fonts Noto Emoji variable font from
`google/fonts/ofl/notoemoji/NotoEmoji[wght].ttf`, licensed under the included
`EMOJI-OFL.txt`. It supplies real monochrome outlines for marketing emoji missing
from the CJK font. Its actual weight range is 300..700. Variation selectors and
ZWJ are zero-advance controls; composed emoji sequences are not a claim of a full
color-emoji shaping engine.
