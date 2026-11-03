---
name: replicate-screen
description: Rebuild UI screenshots (app windows, dialogs, start menus, settings pages, dashboards, web pages) as pixel-accurate HTML/CSS, verified by rendering and diffing against the original. Use when the user shares a screenshot and says replicate, rebuild, recreate, clone, "build this", "pixel perfect", or asks to match borders, shadows, background colours or the dark mood of a reference; also for a folder of screenshots combined into one page with each original beside its replica.
argument-hint: "[screenshot or folder] [notes]"
---

# Replicate a screen

The goal is a replica a viewer can't tell from the screenshot at a glance, proven by numbers, not by eye. Never guess a colour, size or shadow: measure it from the reference, build, render, diff, fix, repeat.

All tools are the `facsimile` CLI, which this plugin puts on the Bash tool's PATH (on Windows PowerShell/cmd use `facsimile.cmd`). Run `facsimile <command> -h` for options.

## 0. Check the toolchain

Run `facsimile doctor`. If it reports anything missing, run `facsimile setup` (pip-installs Pillow, NumPy, OpenCV, potracer, Playwright and, only if no Chrome/Edge is installed, Playwright's Chromium). If several Pythons exist, set `FACSIMILE_PYTHON` to the one that has the packages.

## 1. Pin down the reference

- Read the image. Note its pixel size and guess the device pixel ratio: menu-bar text ~28px tall means a 2x retina capture. The stage in CSS px = image px / DPR. Render at `--scale DPR` so the comparison is pixel to pixel.
- A sheet with several items: `facsimile tiles sheet.png --out refs/` crops each object with padding (its drop shadow included).
- A folder of references: `facsimile images DIR` lists formats, sizes and duplicates (identical files are listed as `DUPLICATE of`); `--fix-ext refs/` copies extensionless downloads to proper `.jpg/.png` names, which browsers and the gallery need.

## 2. Measure before writing CSS

Work from numbers. Typical calls:

```bash
facsimile zoom ref.png z.png --box 290 130 400 260 --scale 6     # look at corners, strokes, icons
facsimile sample ref.png 200,215 375,300 335,186                    # exact fills
facsimile sample ref.png --row 350 360 392                          # profile across a right edge
facsimile sample ref.png --col 200 140 160                          # profile across a top edge
facsimile sample ref.png --avg 10 300 120 400                       # region average (gradients: sample several)
facsimile extent ref.png --select "L>200" --box 60 160 300 210 --join 6   # text runs and buttons: boxes and colours
```

Quote mask specs (`"L>200"`, `"#4cc2ff~20"`): unquoted `<`, `>` and `#` are shell syntax.

Read edges from the profiles. A dark-mode window edge often reads, from outside in: a 1 device-px near-black hairline (an outer `0 0 0 .5px` ring at 2x), a 1 CSS px lighter highlight stroke (the border), then the fill. Write those as distinct layers. Record for every surface (window, title bar, nav pane, panel, card, list row incl. hover/selected, button, input, flyout, menu, taskbar, divider):

- fill at 2-3 interior points, to catch gradients and mica/acrylic tint;
- each visible edge: stroke colour, width, and any 1px inner highlight (often a lighter top edge in dark mode);
- shadow: a 0-40px profile outward from the bottom, the sides and the top;
- corner radius, from a zoomed crop (where the curve leaves the straight edge);
- text: colour, weight, size from cap height (cap height / 0.7 ~ font-size), baseline, and start/end x of each run so widths can be matched (`extent` gives the boxes; `--join` merges glyphs into words).

### Fitting a shadow

Sample the darkening ratio just outside the edge (`edge = render/background`) and again 20-30px out. For a Gaussian shadow with offset 0 the darkening at the edge is about alpha/2, and the blur radius B corresponds to sigma = B/2. So: alpha ~ 2 x edge darkening; pick sigma so the far sample matches `1 - Phi(d/sigma)`; a darker bottom than top means a positive y offset. Layer two shadows (a tight contact shadow plus a wide soft one) when one curve can't fit both samples. Verify by sampling the same profile on your render.

## 3. Build

- One self-contained HTML file. Colours as tokens on `:root` with the sampled hex/rgba values. Absolute or flex layout in CSS px inside a fixed `#stage` of the reference size.
- Fonts: the platform font when it is the platform's UI (`"Segoe UI Variable Text","Segoe UI",system-ui` for Windows 11; a close Google font such as Inter for macOS SF). If text cells dominate the worst-region list, try the sibling families first (plain `"Segoe UI"` vs the Variable versions), then match measured widths with `letter-spacing`, and only then move positions.
- Icons: draw inline SVG. Simple glyphs as paths; for complex logos see the `vectorize-icon` skill.
- Wallpapers/photos/video frames behind the UI: CSS gradients from sampled stops, or a blurred colour grid. For a grid, average the reference into ~16-24px cells (masking out the windows), draw the cells, and blur them (`filter: blur()`, or an SVG `feGaussianBlur` with `color-interpolation-filters="sRGB"` so colours don't shift). It reproduces the colour field, not detail; say so in the report.
- No external images of the reference itself; the replica must stand on its own.

## 4. Verify loop (at least 3 rounds per screen)

```bash
facsimile render page.html out.png --size 259x306 --scale 2          # html query: --query dark
facsimile diff out.png ref.png --out cmp                             # score + worst regions + cmp_side.png
facsimile diff out.png ref.png --box 0 140 386 612                   # score one region
facsimile sample ref.png 387,450 438,450 --vs out.png                # compare surfaces point by point
```

`diff` prints `mean_abs_diff` (0-255; under ~3 is very close for a flat UI, photographic backgrounds score higher), the worst 32px cells with the reference vs render average colour in each, and writes `<prefix>_side.png` (reference | render) and an amplified `<prefix>_diff.png`. Each round: look at the side-by-side, fix the worst cells (shape/position first, then fills, then strokes, then shadows and highlights), re-render, keep only changes that lower the error. Target every surface within ~3 per RGB channel of the reference where the structure allows, and keep going while any surface misses by more than 6.

Report honestly: the final score, rounds done, and the largest remaining mismatch named as surface + reference colour vs render colour. Don't claim rounds you didn't run.

## 5. Surface pass (when the user asks for closer borders, shadows and backgrounds)

A focused second pass on an existing replica: inventory every surface (list above), sample fill/edge/shadow/radius in both the reference and the render (`sample --vs`), fix each to within ~3 per channel, and get the alpha of 1px dividers right against the actual fill underneath them. Don't trade away layout or text fidelity; if a change worsens the overall `diff` score, find out why before keeping it.

## 6. Many screenshots (batch)

For a folder of screenshots, run the `replicate-screens` workflow (`/facsimile:replicate-screens`) when workflows are available, or fan out with the Agent tool yourself: groups of 3-4 screenshots per agent (`facsimile:screen-replicator`), in parallel.

Contract for each agent's output so the pieces combine:

```html
<section class="sec-A">
  <style> /* every selector prefixed with .sec-A */ </style>
  <figure class="card" id="A1"><figcaption>Short title</figcaption><div class="stage">...</div></figure>
</section>
```

- `.stage` is exactly 800px wide, height = round(800 x refH / refW), `position:relative; overflow:hidden`. Authoring at the reference size and scaling with a transform inside the stage is fine.
- No global selectors (`body`, `html`, bare `figure`); the combined page provides the background.
- Each agent writes only `parts/<KEY>.html` and keeps scratch files in its own subfolder (shared scratch folders get overwritten by sibling agents).

Combine with each original beside its replica:

```bash
facsimile gallery --parts parts --refs refs.json --out replicas.html --title "Windows 11"
```

`refs.json` maps figure id to the reference image path, relative to the JSON file: `{"A1": "shots/start-menu.png"}`. For standalone files use `--pair replica.html ref.png "label"` (repeatable). Then render the gallery or open it and check that every reference image loads: lazy images report `naturalWidth` 0 until scrolled into view, and paths with spaces must be URL-encoded (the gallery does this).

Sub-agent reports overstate. Before relaying numbers, re-render a sample yourself and check the files exist and load.
