---
name: vectorize-icon
description: Rebuild a raster logo, mascot, character or app icon as a clean, editable, pixel-accurate SVG (or inline SVG in HTML), with smooth curves and fitted gradients, shading and shadows, verified against the original. Use when the user asks to vectorize, trace, convert to SVG, recreate an icon/logo/mascot "pixel perfect" (also when they mention Figma for it), or says a vector's edges are jagged, lumpy or not smooth.
argument-hint: "[image] [which icon, e.g. 'bottom right']"
---

# Vectorize an icon

Deliver an SVG that matches the reference closely and is a real vector: a few dozen named shapes (tile, shadow, body, eyes, highlights...), smooth curves, gradients and filters. Never embed the raster (`<image>`, data URIs) and never deliver a mosaic of thousands of tiny traced paths.

Tools are the `lightbox` CLI (on the Bash tool's PATH via this plugin; `lightbox.cmd` on Windows shells). Run `lightbox doctor` first and `lightbox setup` if anything is missing.

If the user wants the result in Figma: build the SVG here (vectors are easier to measure and verify in code), then import it (drag the `.svg` into Figma, or use a Figma MCP upload when connected). The paths stay editable.

## 1. Crop the target

`lightbox tiles sheet.png --out ref/` finds each icon on a sheet (row-major numbering, padded so the drop shadow is included) and writes `tiles.json`. Pick the one the user means ("bottom right" = last item of the last row). Keep the crop's page colour: sample a corner, it is the `--bg` for renders.

## 2. Decompose and measure

List every visible part and its paint: tile (rounded square with gradient), tile drop shadow, body silhouette, secondary shapes (hat, tuft, hand, door, slope), small details (badge, keyhole, pupils, eye whites, alert dashes), rim lights, glows, inner shading. Measure with `lightbox zoom`, `lightbox sample` (points, `--row`/`--col` profiles across edges, `--avg` regions) and `lightbox extent --select SPEC` (bounding boxes of e.g. the eyes, to place ellipses).

## 3. Build each shape, simplest construction first

1. **Geometric primitive** wherever the design is geometric: `rect rx` / 4-arc rounded rect for tiles, circle/ellipse for pupils and eye whites (an eye with a pupil cut out is one crescent: ellipse minus ellipse with `fill-rule="evenodd"`), capsules (two lines + two arcs) for dashes, a keyhole as a circle head + tapered slot with rounded bottom corners, a shield from 4-6 cubics. Make it symmetric when the design is symmetric; copy the asymmetry when the reference is visibly skewed, and say which you chose.
2. **Smoothed trace** for organic silhouettes (blobs, hands, hats):

   ```bash
   lightbox trace ref.png --select "L<60" --keep 1 --fill-holes --out blob.path      # dark body, holes filled
   lightbox trace ref.png --select "#e8e9ef~30" --box 300 230 390 340 --keep 1        # a light part inside a box
   lightbox smooth blob.path                                                           # check every trace at once
   ```

   `trace` upscales the mask 4x, blurs it (`--blur 1.5` default), re-thresholds and fits potrace curves, so stair steps become curves. If `trace` or `smooth` reports STAIR-STEPPED, raise `--blur` (2-2.5); lower it to keep sharper corners. Concave notches (where a head meets a slot, a fist's knuckles) stay real corners. Fewer, longer cubics read cleaner than many short ones; edit the result if needed.

   Trace bodies with `--fill-holes` and draw eyes, keyholes and other details on top as their own shapes. A traced hole never lines up exactly with a primitive drawn over it, and the layer below shows through as a thin rim.

   Quote mask specs in the shell (`"L<60"`, `"#e8e9ef~30"`): unquoted `<`, `>` and `#` are shell syntax.

   Never trace a raw pixel mask without smoothing: that produces the "lumpy keyhole" users notice immediately, while barely moving the diff score.

## 4. Paint

- Gradients: sample stops along the gradient's axis (3-5 points) and use those exact colours; radial gradients for domed bodies.
- Rim light / edge glow: a stroke or inset copy of the shape, clipped to the shape and blurred (`feGaussianBlur`), coloured from the sampled rim.
- Drop shadow: a blurred, offset copy of the tile/body; fit opacity and blur from a profile sampled below the shape (see the shadow-fitting note in the `replicate-screen` skill).
- Inner shading and highlights: soft gradient overlays clipped to the part; keep them few and fitted, not blotchy contour layers.
- SVG `viewBox` = crop size in px; everything outside the tile and its shadow transparent.

## 5. Verify (every round)

```bash
lightbox render icon.svg out.png --size 440x480 --bg "#fdfdfd"
lightbox diff out.png ref/icon.png --out cmp                 # score, worst cells, cmp_side.png
lightbox iou out.png ref/icon.png --mask "dark:L<60" --min 0.97 --out iou
lightbox iou out.png ref/icon.png --mask "eyes:L>235" --box 150 200 330 320 --min 0.97   # box it: white masks also match a light page
lightbox smooth icon.svg                                     # exit 1 = stair-stepped paths or embedded rasters
lightbox render icon.svg big.png --size 440x480 --bg "#fdfdfd" --scale 4
lightbox zoom big.png key.png --box 1280 920 1560 1360 --scale 1   # inspect each small element at 4x
```

- `diff`: whole crop and tile-only box. Good replicas reach ~1.5-2.5 mean_abs_diff.
- `iou`: shape agreement of the dark body, white eyes, pupils etc. (>= 0.97 each). Masks cover the whole image unless you pass `--box`, so box light-coloured parts that could match the page. `--out` writes red (only in reference) / blue (only in render) mismatch maps that show where an outline is off.
- `smooth` must pass, and you must look at the 4x crops of every small element (keyhole, badge, pupils, dashes, notches). Metrics don't catch lumpy edges; eyes do.

At least 5 rounds: outlines first, then fills/gradients, then shading, shadows and highlights.

## 6. Refinement pass with fresh eyes

When quality matters, give the finished SVG to a second agent (`lightbox:icon-vectorizer` in refine mode, or the `vectorize-icons` workflow, which does build -> refine automatically). It measures first instead of trusting the builder's report, lists the 5 largest error regions with the element in each, fixes them, and keeps only changes that lower the error.

## 7. Deliver

- `name.svg` per icon, plus a comparison page: `lightbox gallery --pair name.svg ref/name.png "Name" --out index.html` (repeat `--pair`).
- Report: score, IoUs, that `smooth` passes, and the largest remaining mismatch (usually soft shading). Mention construction choices the user may want changed (symmetry, merged layers, renamed ids).
