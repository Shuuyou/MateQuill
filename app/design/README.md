# Design kernel

The kernel owns planning, typed design objects, editorial platform connectors,
measured Chinese line breaking, native PosterDocument composition, and portable
SVG output. It does not read tenant resources, open uploaded files, fetch URLs,
charge providers, enqueue jobs, or decide whether a source run is authorized.
Those operations belong to the authenticated service boundary.

## Integration

```python
from app.design import plan_design, compose_design, render_design_svg

# `record` must be an authorized, audited source run. Existing image IDs should
# already correspond to the authenticated server's selected assets.
plan = await plan_design(task, record, instruction, platform, llm=llm)
boards = compose_design(task, record, plan, assets)
svg = render_design_svg(boards[0].document, asset_data)
```

`assets` is an asset-ID mapping containing provenance plus already-validated
`data_url` values or separately supplied image bytes at rendering. No path or URL
will be fetched by the kernel. Product image pixels are contained in locked
layers; real photographs can be sliced to the desired canvas. Native text and
source references remain editable in the document.

The model proposes bounded concept geometry and selects existing `text_ids` and
`asset_ids`. Its freely written concept thesis is internal art direction, never
published copy. Unknown references, repeated compositions, colliding text or
exact-product regions, and invalid colors reject the proposal. Explicit photo
regions support intentional overlay typography, reviewed against actual images.
Local composition is an explicitly
recorded fallback; it is never reported as successful model art direction.

All platform dimensions and page counts are editorial starting points, not
purported official upload limits. Surfaces are independent (e.g. image notes and
video covers). Prefer changing a connector version over sprinkling platform
checks in HTTP routes.

`document.status` is draft or blocked, never an assertion of finished marketing
quality. The service must render and inspect the actual output, handle overflow,
and apply independent factual and product checks before delivery. Empty imagery
uses native typography and an internal audit entry, never a fake placeholder.

## Typography

The OFL fonts ship under `fonts/`; production images must include that directory.
FontTools measures the same actual variable glyph advances used by SVG export;
line breaks preserve source characters and model tokens. Font outlines make
output independent of host font fallback. Real Noto Serif / Sans families and
variable weights are used; rare-character fallbacks are disclosed in metadata.
Text edits must be rendered again with this renderer. The ordinary legacy poster
renderer remains available but does not provide the same line metrics.

Test using `python -m pytest tests/test_design_kernel.py -q`.
