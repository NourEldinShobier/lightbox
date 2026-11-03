# Lightbox

A Claude Code plugin for pixel-accurate replicas. Give Claude a screenshot or an icon and it rebuilds it, as HTML/CSS for UI screens and as a clean, editable SVG for logos and mascots. Every colour, border and shadow is measured from the reference, and the result is verified by rendering it and diffing it against the original.

It packages a working method, not just prompts:

- **Measure, don't guess.** Exact fills, 1px strokes and inner highlights, shadow falloff, corner radii and text widths are read from the image with a CLI.
- **Verify in a loop.** Render the replica at the reference's size and pixel ratio, score it (`mean_abs_diff`), find the worst regions, fix, repeat.
- **Vectors that are really vectors.** Geometric primitives for geometric parts, smoothed potrace curves for organic shapes, and a check that rejects stair-stepped (pixel-traced) paths and embedded rasters.
- **Scale out.** Workflows fan a folder of screenshots or a set of icons out to parallel agents, run an independent refinement pass, and build one page with every replica beside its original.

## Install

```bash
claude plugin marketplace add NourEldinShobier/lightbox
claude plugin install lightbox@lightbox
```

Or inside a session: `/plugin install lightbox --marketplace NourEldinShobier/lightbox`.

Then install the Python tooling once (Python 3.9+):

```bash
lightbox setup     # pip installs Pillow, NumPy, OpenCV (headless), potracer, Playwright
lightbox doctor    # confirms packages and a browser for rendering
lightbox selftest  # runs the core commands on synthetic inputs
```

`lightbox` is on the Bash tool's PATH while the plugin is enabled (use `lightbox.cmd` from Windows cmd/PowerShell). Rendering uses an installed Chrome or Edge when present, otherwise `setup` downloads Playwright's Chromium. If you have several Pythons, set `LIGHTBOX_PYTHON` to the one with the packages. On a Python that refuses system-wide installs (PEP 668), use `lightbox setup --user`, or create a virtualenv, install `requirements.txt` into it, and point `LIGHTBOX_PYTHON` at its interpreter.

## Use

Just ask:

- "Replicate this screenshot as HTML, pixel perfect: `C:\shots\settings.png`"
- "Build every screenshot in `~/shots/windows11` into one HTML file, with each original next to its replica. Pay close attention to the dark mood, borders and shadows."
- "Vectorize the bottom-right mascot in `icons.png` as SVG."
- "The keyhole in `watchman.svg` isn't smooth."

| Component | Name | What it does |
| :- | :- | :- |
| Skill | `/lightbox:replicate-screen` | Screenshot to HTML/CSS: measure, build, render-diff loop, surface pass, batch contract |
| Skill | `/lightbox:vectorize-icon` | Raster icon to clean SVG: crop, decompose, primitives and smoothed traces, paint, verify |
| Workflow | `/lightbox:replicate-screens` | Folder of screenshots (top level), parallel agents, surface pass, side-by-side gallery. Args: `{ dir, images?, groupSize?, surfacePass?, title?, out? }` (`out` = fragments folder, default `dir/parts`) |
| Workflow | `/lightbox:vectorize-icons` | One builder and one independent refiner per icon, then a comparison page. Args: `{ out, icons: [{ key, ref, box?, desc? }] }` |
| Agent | `lightbox:screen-replicator` | Replicates 3-4 screenshots or runs a surface pass on them |
| Agent | `lightbox:icon-vectorizer` | Builds or refines one icon SVG |

Workflows need Claude Code's [dynamic workflows](https://code.claude.com/docs/en/workflows); without them the skills fan out with the Agent tool instead.

## The CLI

Mask specs (`L<60`, `L>230`, `#rrggbb~30`) select pixels by luminance or colour distance; quote them in shells.

| Command | Purpose |
| :- | :- |
| `lightbox render FILE OUT --size WxH [--scale 2] [--bg #hex]` | Screenshot an HTML, SVG or image file at an exact viewport and pixel ratio |
| `lightbox diff RENDER REF [--box x0 y0 x1 y1] [--out prefix]` | `mean_abs_diff`, p95, the worst regions with reference vs render colour, side-by-side and amplified diff images |
| `lightbox sample IMG x,y ... [--row Y X0 X1] [--col X Y0 Y1] [--avg ...] [--vs RENDER]` | Exact colours, edge and shadow profiles, region averages; compare against a render |
| `lightbox zoom IMG OUT --box ... [--scale 6]` | Enlarge a region to read strokes, radii and icons |
| `lightbox tiles SHEET [--out dir]` | Find and crop every icon or card on a sheet, drop shadow included |
| `lightbox extent IMG --select SPEC [--box ...] [--join N]` | Bounding boxes and colours of matching pixels: text runs, buttons, eyes |
| `lightbox iou RENDER REF --mask "dark:L<60" [--box ...] [--min 0.97]` | Shape agreement of colour masks, with mismatch maps |
| `lightbox trace IMG --select "L<60" [--box ...] [--fill-holes]` | Trace one region into a smooth SVG path (upscale, blur, potrace) |
| `lightbox smooth FILE.svg` | Exit 1 on stair-stepped paths or embedded rasters |
| `lightbox gallery --out PAGE --parts DIR --refs refs.json` / `--pair REPLICA REF [LABEL]` | One page with each replica beside its original |
| `lightbox images DIR [--fix-ext dir]` | Inventory references: formats, sizes, duplicates; copy extensionless or mislabelled files to proper names |
| `lightbox doctor` / `setup` / `selftest` | Check, install, test |

## How good is it?

On the Windows 11 and mascot-icon sets it was developed on, flat UI screens typically land at a `mean_abs_diff` of 2 to 6 out of 255, and icons at 1.5 to 2.5, with mask IoUs above 0.98. Screens with photographic wallpapers score higher, because backgrounds are rebuilt as blurred colour fields rather than copied. App icons and glyphs are redrawn by hand, so they are the usual remaining difference.

## Layout

```
.claude-plugin/plugin.json, marketplace.json
assets/icon.png, icon.svg
skills/replicate-screen/SKILL.md
skills/vectorize-icon/SKILL.md
agents/screen-replicator.md, icon-vectorizer.md
workflows/replicate-screens.js, vectorize-icons.js
bin/lightbox, lightbox.cmd      -> scripts/lightbox.py
requirements.txt
```

## License

MIT
