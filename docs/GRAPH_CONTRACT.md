# Verified graph editing contract

## Scope and decisions

The public unit for widths and fonts is a physical point. Prefer the native
`-wp` width flag, probe readable support first, and verify the resulting value.
For a legacy installation exposing only raw `-w`, the documented conversion is
500 units per point. That fallback is identified in diagnostics and has not
been physically revalidated on Origin 2020. No version number alone proves
that a property is available.

Persistent layer names are the stable IDs. Layer indices remain available for
legacy projects. A context-local selector carries the resolved target through
existing activation/fresh-COM-handle helpers, preserving their loaded-project
and fresh-plot protections. No process-global active-layer cache is introduced.
Ambiguous/conflicting selectors fail before a property mutation.

New high-level operations restore existing ranges, increments, rescale policies
and geometry. Right-Y additions never rebuild an existing legend unless
explicitly requested. Attaching errors to an existing curve restores the saved
axis state and legend visibility after the temporary error-column plot. Child geometry temporarily disables ResizeParent; initial
geometry writes use at most three readback attempts. Repeated named configuration
reuses the layer. Reusing a layer with different source bindings fails instead
of adding another plot. Use a new layer ID for a new series.

The old `apply_publication_style` remains an explicit opt-in operation with its
legacy defaults. New template defaults contain no style profile. Source editing
sizes are separate from final physical size and are never journal standards.

## Patch schema

`configure_layer(graph_name, changes, layer_index=None, layer_id=None)` accepts:

```json
{
  "axes": {
    "x": {"from": 0, "to": 64, "inc": 16, "scale": "linear"},
    "y2": {
      "title": "Temperature (K)", "title_font": "Arial",
      "title_size_pt": 24, "title_bold": true,
      "tick_font": "Arial", "tick_size_pt": 20,
      "tick_bold": true, "thickness_pt": 2
    }
  },
  "geometry": {
    "units": "parent_percent", "left": 0, "top": 0,
    "width": 100, "height": 100
  }
}
```

Title text and title boldness are independent of JSON key order.
Axis keys are bottom X (`x`), left Y (`y`), top X (`x2`) and right Y (`y2`).
Right-Y uses the `YR` title object. Geometry units are current, page_percent,
parent_percent, inch, cm, mm, pixel or point. Geometry requires at least one
position/size. Unsupported or unrecognized patch fields are rejected.

Reports include per-property `requested`, `actual`, and `status` values,
`not_applied` and `unverifiable` lists. Font names resolve to Windows font indices;
reports include the requested font name alongside index comparisons. Unavailable
reads remain null. COM success and readback success are not visual verification.

`inspect_graph` reads all layers, graph ranges and source dataset names, axes,
tick fonts/sizes, titles, widths, legends and physical page size. Width/height in
Origin dots are converted using page.resx/resy, never by guessing a fixed DPI.
Source dataset names and X-binding names can include Origin's sheet suffixes; no source values are
rewritten for inspection. `range` identifies the exact plot position even if a
dataset appears more than once. Some plot properties remain unavailable on some
builds and are null.

`set_plot_style(diagnostics=True)` returns the same report shape for widths,
symbols, colors and requested error-bar dimensions. Its default text result
remains available to existing callers.

## Export contract

`export_graph` retains old arguments and adds `area`, layer selectors and
`powerpoint_compatible`. The old raster/PDF result messages remain supported;
the new SVG and advanced routes return JSON metadata.

| Area | Behavior |
| --- | --- |
| page | Native expGraph with explicit page margins |
| content | Native tight content bounds |
| layer | SVG semantic scope selection, complete layer content bounds |
| plot_area | Keep selected plot scopes, remove whole axis/text objects, clip to the native plot rectangle |

Semantic crops support SVG/PDF. Unknown SVG scopes or ambiguous plot rectangles
fail explicitly. A transformed export keeps `.native.svg` before the derived
asset. XML parsing rejects custom entities/internal DTDs. Compatibility conversion
accepts only the recognized opaque white rectangular mask subset, with user-space
coordinates and no effects/transforms/CSS references. Other masks are preserved
and the compatibility request fails. This is not a general SVG-to-PowerPoint
converter. Page/source values and source layout are not changed; Origin may
remember export preferences internally.

Native exports use a unique staging file so an old destination cannot masquerade
as a successful export. Optional PyMuPDF supplies semantic content bounds,
cropped PDF conversion and visual QA. Whole-page native SVG/PDF do not need it.
MuPDF can substitute fonts during SVG-to-PDF conversion; verify typography at the
final size. Export reports never claim a visual check they did not perform.

## Template contract

The packaged `figure_templates.v1.json` catalog separates data mapping, layout and
style. Inputs refer to existing worksheets/matrices. Template ownership and the
mapping digest are stored in the main layer's comments. Existing unowned graphs
or changed bindings are rejected to avoid modifying an unrelated user graph.
Repeated calls with the same mapping update layout/style and reuse objects.
Right-Y reuse also checks the actual X binding, so stale metadata after manual
editing cannot authorize a different X source.

Supported families: single_axis, dual_y, inset, grouped_stacked_column, heatmap.
Grouped stacked columns use native plot 213 and explicit group X positions from
the caller. They do not compute offsets, aggregate rows or infer category order.
Heatmap coordinate bounds must be explicit and match the existing matrix.
Color scales include linear, positive log10, and symmetric zero_centered. The
last is a linear map with caller-supplied symmetric limits; choose a diverging
palette explicitly when required. Nonlinear asymmetric diverging maps are not
implied. Missing cells remain missing.

SD/SEM/CI error columns and their meaning must be supplied. Templates never infer
sample count or recalculate statistics. The right-axis mapping accepts the same
explicit error columns as the main series, preserving them on repeated calls. Confidence interval columns represent
explicit error magnitudes, not automatically interpreted lower/upper bounds.
Use an existing native template for asymmetric interval endpoints.

## Verification and compatibility boundaries

- Unit tests cover point/raw conversion, unsupported reads, no-op diagnostics,
  layer name/index resolution, state preservation, repeated configuration, safe
  masks/crops, input validation and explicit error provenance.
- `tests/test_live_graph_contract.py` uses only an isolated DispatchEx application
  and synthetic data. It covers all families/scales, repeated calls, property
  readback, unchanged source numbers, SVG/PDF exports and its own temporary
  project save/reload. Never run older shared-instance integration tests against
  a user-opened project.
- Origin 2026 is the currently tested installation. Historical Origin 2020 tests
  remain in the repository, but the new implementation is not claimed verified
  on 2020 or other untested releases.
- Five representative compatible SVGs were inserted into an owned temporary
  PowerPoint presentation and exported to PDF: zero raster images and nonzero
  vector drawings on every page. This does not prove identical behavior on all
  Office versions or all SVG shapes/fonts.
- Windows regression testing exposed atomic sidecar replacement competing with
  polling readers: memory reported an ended session while the file stayed live.
  Lockfile/session ledger writes now retry PermissionError up to four times,
  preserving atomic replacement. No session cleanup policy changed.

On the reviewed Windows installation, `Origin.Application.Exit()` occasionally
emitted native RPC diagnostic `0x800706be` during owned test-instance teardown.
All functional assertions had completed; pytest exited with code 0 and its JUnit
report recorded zero failures and errors. This observation is retained rather
than treating functional graph verification as proof of every shutdown path.
