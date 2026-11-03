#!/usr/bin/env python3
"""facsimile: measure, render and compare tools for pixel-accurate UI and icon replicas.

Run `facsimile <command> -h` for each command's options.
"""
import argparse
import hashlib
import html
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def die(msg, code=1):
    print(f"facsimile: {msg}", file=sys.stderr)
    sys.exit(code)


def need(module, pip_name=None):
    try:
        return __import__(module)
    except ImportError:
        die(f"missing Python package '{pip_name or module}'. Run `facsimile setup` (or pip install -r requirements.txt).")


# ---------------------------------------------------------------- image helpers

def load_rgb(path):
    """Open an image as RGB; transparent pixels are composited over white."""
    need("PIL", "pillow")
    from PIL import Image
    if not os.path.exists(path):
        die(f"not found: {path}")
    img = Image.open(path)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        base = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(base, rgba).convert("RGB")
    return img.convert("RGB")


def clamp_box(box, w, h):
    x0, y0, x1, y1 = box
    x0, x1 = max(0, min(x0, w)), max(0, min(x1, w))
    y0, y1 = max(0, min(y0, h)), max(0, min(y1, h))
    if x1 <= x0 or y1 <= y0:
        die(f"box {box} is empty or outside the {w}x{h} image")
    if (x0, y0, x1, y1) != tuple(box):
        print(f"note: box clamped to {x0},{y0}-{x1},{y1}", file=sys.stderr)
    return x0, y0, x1, y1


def as_array(img):
    np = need("numpy")
    return np.asarray(img, dtype=float)


def hexc(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb[:3])


def parse_xy(text):
    try:
        x, y = text.split(",")
        return int(round(float(x))), int(round(float(y)))
    except ValueError:
        die(f"expected x,y but got '{text}'")


def parse_box(values):
    if values is None:
        return None
    if len(values) == 1:
        values = values[0].replace(",", " ").split()
    if len(values) != 4:
        die("a box is four numbers: x0 y0 x1 y1")
    return tuple(int(round(float(v))) for v in values)


def parse_size(text):
    m = re.fullmatch(r"(\d+)[x,](\d+)", text or "")
    if not m:
        die(f"expected WxH but got '{text}'")
    return int(m.group(1)), int(m.group(2))


def same_size(img, ref):
    """Resize img to ref's size (renders taken at a device scale factor > 1 come back larger)."""
    from PIL import Image
    if img.size != ref.size:
        print(f"note: resized {img.size[0]}x{img.size[1]} -> {ref.size[0]}x{ref.size[1]} to match the reference", file=sys.stderr)
        img = img.resize(ref.size, Image.LANCZOS)
    return img


def mask_from_spec(arr, spec):
    """Mask spec: 'L<60', 'L>230' (luminance) or '#rrggbb~30' (max channel distance)."""
    np = need("numpy")
    spec = spec.strip()
    m = re.fullmatch(r"L\s*([<>])\s*(\d+(?:\.\d+)?)", spec)
    if m:
        lum = arr[..., 0] * 0.2126 + arr[..., 1] * 0.7152 + arr[..., 2] * 0.0722
        t = float(m.group(2))
        return lum < t if m.group(1) == "<" else lum > t
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})\s*~\s*(\d+)", spec)
    if m:
        c = np.array([int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)], float)
        return np.abs(arr[..., :3] - c).max(axis=2) <= float(m.group(2))
    die(f"bad mask spec '{spec}' (use L<60, L>230 or #rrggbb~30)")


def named_specs(items, defaults):
    out = []
    for item in items or defaults:
        name, _, spec = item.partition(":") if ":" in item else (item, "", item)
        out.append((name, spec))
    return out


# ---------------------------------------------------------------- browser

def launch_browser(p):
    """Prefer an installed Chrome or Edge (real system fonts and colour handling), else Playwright's Chromium."""
    errors = []
    for channel in ("chrome", "msedge", None):
        try:
            return (p.chromium.launch(channel=channel) if channel else p.chromium.launch()), channel or "chromium"
        except Exception as e:  # noqa: BLE001 - try the next browser
            errors.append(f"{channel or 'chromium'}: {str(e).splitlines()[0]}")
    die("no browser available for Playwright. Run `facsimile setup`.\n  " + "\n  ".join(errors))


def cmd_render(a):
    need("playwright")
    from playwright.sync_api import sync_playwright
    src = pathlib.Path(a.file).resolve()
    if not src.exists():
        die(f"not found: {src}")
    w, h = parse_size(a.size)
    tmp = None
    if src.suffix.lower() in IMAGE_EXT | {".svg"}:
        # Wrap a bare SVG/image in a harness page so it renders 1:1 on a chosen page colour.
        tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
        bg = "transparent" if a.transparent else a.bg
        tmp.write(f'<!doctype html><html><body style="margin:0;background:{bg}">'
                  f'<img src="{src.as_uri()}" style="display:block;width:{w}px;height:{h}px"></body></html>')
        tmp.close()
        url = pathlib.Path(tmp.name).as_uri()
    else:
        url = src.as_uri() + (f"?{a.query}" if a.query else "")
    try:
        with sync_playwright() as p:
            browser, used = launch_browser(p)
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=a.scale,
                                    color_scheme=a.color_scheme)
            page.goto(url)
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(a.wait)
            if a.selector:
                page.locator(a.selector).first.screenshot(path=a.out, omit_background=a.transparent)
            else:
                page.screenshot(path=a.out, full_page=a.full_page, omit_background=a.transparent)
            browser.close()
    finally:
        if tmp:
            os.unlink(tmp.name)
    print(f"wrote {a.out} ({w}x{h} @ {a.scale}x, {used})")


# ---------------------------------------------------------------- diff

def cmd_diff(a):
    np = need("numpy")
    from PIL import Image
    ref = load_rgb(a.ref)
    ren = same_size(load_rgb(a.render), ref)
    A, R = as_array(ren), as_array(ref)
    box = parse_box(a.box)
    ox, oy = 0, 0
    if box:
        x0, y0, x1, y1 = clamp_box(box, ref.width, ref.height)
        A, R, ox, oy = A[y0:y1, x0:x1], R[y0:y1, x0:x1], x0, y0
    d = np.abs(A - R)
    per_px = d.mean(axis=2)
    print(f"mean_abs_diff {d.mean():.3f}  p95 {np.percentile(per_px, 95):.1f}  max {d.max():.0f}  "
          f"pixels>16 {100 * (per_px > 16).mean():.2f}%")
    cell = a.cell
    hgt, wid = per_px.shape
    cells = []
    for cy in range(0, hgt, cell):
        for cx in range(0, wid, cell):
            blk = per_px[cy:cy + cell, cx:cx + cell]
            cells.append((blk.mean(), cx, cy, min(cx + cell, wid), min(cy + cell, hgt)))
    cells.sort(reverse=True)
    print(f"worst {cell}px cells (ref coords): err  box  ref-avg  render-avg")
    for err, x0, y0, x1, y1 in cells[:a.top]:
        r_avg = R[y0:y1, x0:x1].reshape(-1, 3).mean(0)
        a_avg = A[y0:y1, x0:x1].reshape(-1, 3).mean(0)
        print(f"  {err:6.2f}  {x0 + ox},{y0 + oy}-{x1 + ox},{y1 + oy}  {hexc(r_avg)}  {hexc(a_avg)}")
    if a.out:
        Image.fromarray(np.clip(d * a.amp, 0, 255).astype("uint8")).save(f"{a.out}_diff.png")
        side = Image.new("RGB", (A.shape[1] * 2, A.shape[0]))
        side.paste(Image.fromarray(R.astype("uint8")), (0, 0))
        side.paste(Image.fromarray(A.astype("uint8")), (A.shape[1], 0))
        side.save(f"{a.out}_side.png")
        print(f"wrote {a.out}_diff.png (x{a.amp}) and {a.out}_side.png (reference | render)")


# ---------------------------------------------------------------- sample

def cmd_sample(a):
    np = need("numpy")
    img = load_rgb(a.image)
    arr = as_array(img)
    other = as_array(same_size(load_rgb(a.vs), img)) if a.vs else None
    r = a.radius
    if not (a.points or a.row or a.col or a.avg):
        die("nothing to sample: give x,y points, --row, --col or --avg")

    def at(src, x, y):
        return src[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1].reshape(-1, 3).mean(0)

    def line(label, x, y):
        if not (0 <= x < img.width and 0 <= y < img.height):
            print(f"{label}  outside the {img.width}x{img.height} image")
            return
        c = at(arr, x, y)
        if other is None:
            print(f"{label}  {hexc(c)}  rgb({int(c[0])},{int(c[1])},{int(c[2])})")
        else:
            o = at(other, x, y)
            print(f"{label}  {hexc(c)}  vs {hexc(o)}  max_channel_delta {np.abs(c - o).max():.0f}")

    for p in a.points:
        x, y = parse_xy(p)
        line(f"{x},{y}", x, y)
    if a.row:
        y, x0, x1 = a.row
        for x in range(x0, x1 + 1, a.step):
            line(f"x={x} y={y}", x, y)
    if a.col:
        x, y0, y1 = a.col
        for y in range(y0, y1 + 1, a.step):
            line(f"x={x} y={y}", x, y)
    if a.avg:
        x0, y0, x1, y1 = clamp_box(parse_box(a.avg), img.width, img.height)
        c = arr[y0:y1, x0:x1].reshape(-1, 3).mean(0)
        msg = f"avg {x0},{y0}-{x1},{y1}  {hexc(c)}"
        if other is not None:
            o = other[y0:y1, x0:x1].reshape(-1, 3).mean(0)
            msg += f"  vs {hexc(o)}  max_channel_delta {np.abs(c - o).max():.0f}"
        print(msg)


def cmd_zoom(a):
    from PIL import Image
    img = load_rgb(a.image)
    x0, y0, x1, y1 = clamp_box(parse_box(a.box), img.width, img.height)
    crop = img.crop((x0, y0, x1, y1))
    crop = crop.resize((crop.width * a.scale, crop.height * a.scale), Image.LANCZOS if a.smooth else Image.NEAREST)
    crop.save(a.out)
    print(f"wrote {a.out} ({crop.width}x{crop.height})")


# ---------------------------------------------------------------- tiles

def cmd_tiles(a):
    np = need("numpy")
    cv2 = need("cv2", "opencv-python-headless")
    img = load_rgb(a.image)
    arr = as_array(img)
    border = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]])
    bg = np.median(border, axis=0)
    mask = (np.abs(arr - bg).sum(axis=2) > a.threshold).astype("uint8")
    k = np.ones((a.close, a.close), "uint8")
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    boxes = [[int(v) for v in stats[i][:4]] for i in range(1, n) if stats[i][4] >= a.min_area]
    # Merge pieces of one object (a light tile on a light page often splits; badges sit inside tiles).
    g, merged = a.merge, True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                (x, y, w, h), (X, Y, W, H) = boxes[i], boxes[j]
                if x - g < X + W and X - g < x + w and y - g < Y + H and Y - g < y + h:
                    nx, ny = min(x, X), min(y, Y)
                    boxes[i] = [nx, ny, max(x + w, X + W) - nx, max(y + h, Y + H) - ny]
                    del boxes[j]
                    merged = True
                    break
            if merged:
                break
    rows = sorted(boxes, key=lambda b: b[1] + b[3] / 2)
    ordered, line = [], []
    for b in rows:  # group into rows, then sort each row left to right
        if line and (b[1] + b[3] / 2) - (line[0][1] + line[0][3] / 2) > line[0][3] / 2:
            ordered += sorted(line, key=lambda t: t[0])
            line = []
        line.append(b)
    ordered += sorted(line, key=lambda t: t[0])
    print(f"background {hexc(bg)}; {len(ordered)} objects (row-major):")
    result = []
    for i, (x, y, w, h) in enumerate(ordered, 1):
        p = a.pad
        bx = (max(0, x - p), max(0, y - p), min(img.width, x + w + p), min(img.height, y + h + p))
        result.append({"id": i, "box": bx, "core": [x, y, x + w, y + h]})
        print(f"  {i:2d}  box {bx[0]},{bx[1]}-{bx[2]},{bx[3]}  ({bx[2] - bx[0]}x{bx[3] - bx[1]})")
        if a.out:
            os.makedirs(a.out, exist_ok=True)
            img.crop(bx).save(os.path.join(a.out, f"tile_{i:02d}.png"))
    if a.out:
        with open(os.path.join(a.out, "tiles.json"), "w", encoding="utf-8") as f:
            json.dump({"source": os.path.abspath(a.image), "background": hexc(bg), "tiles": result}, f, indent=1)
        print(f"wrote crops and tiles.json to {a.out}")


# ---------------------------------------------------------------- extent

def cmd_extent(a):
    """Bounding boxes of pixels matching a mask: text runs, buttons, icons, eyes."""
    np = need("numpy")
    cv2 = need("cv2", "opencv-python-headless")
    img = load_rgb(a.image)
    arr = as_array(img)
    ox = oy = 0
    box = parse_box(a.box)
    if box:
        x0, y0, x1, y1 = clamp_box(box, img.width, img.height)
        arr, ox, oy = arr[y0:y1, x0:x1], x0, y0
    m = mask_from_spec(arr, a.select)
    if not m.any():
        die(f"no pixels match {a.select}")
    ys, xs = np.where(m)
    print(f"all {int(m.sum())} px: box {xs.min() + ox},{ys.min() + oy}-{xs.max() + 1 + ox},{ys.max() + 1 + oy}"
          f"  ({xs.max() - xs.min() + 1}x{ys.max() - ys.min() + 1})")
    if a.join:  # merge glyphs of one word or line into one region
        m = cv2.dilate(m.astype("uint8"), np.ones((1, a.join), "uint8")).astype(bool) & np.ones_like(m)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m.astype("uint8"), 8)
    order = sorted(range(1, n), key=lambda i: (stats[i][1], stats[i][0]))
    order = [i for i in order if stats[i][4] >= a.min_area][: a.top]
    for i in order:
        x, y, w, h, area = (int(v) for v in stats[i])
        c = arr[lab == i].mean(0)
        print(f"  box {x + ox},{y + oy}-{x + w + ox},{y + h + oy}  ({w}x{h})  {area} px  {hexc(c)}")


# ---------------------------------------------------------------- iou

def cmd_iou(a):
    from PIL import Image
    np = need("numpy")
    ref = load_rgb(a.ref)
    R = as_array(ref)
    A = as_array(same_size(load_rgb(a.render), ref))
    box = parse_box(a.box)
    if box:
        x0, y0, x1, y1 = clamp_box(box, ref.width, ref.height)
        A, R = A[y0:y1, x0:x1], R[y0:y1, x0:x1]
    worst = 1.0
    for name, spec in named_specs(a.mask, ["dark:L<60", "light:L>225"]):
        ma, mr = mask_from_spec(A, spec), mask_from_spec(R, spec)
        union = (ma | mr).sum()
        iou = (ma & mr).sum() / union if union else 1.0
        worst = min(worst, iou)
        print(f"{name:10s} {spec:14s} IoU {iou:.4f}  ref_px {int(mr.sum())}  render_px {int(ma.sum())}")
        if a.out:
            vis = np.zeros(ma.shape + (3,), "uint8")
            vis[mr & ~ma] = (255, 60, 60)   # only in reference: red
            vis[ma & ~mr] = (60, 140, 255)  # only in render: blue
            vis[ma & mr] = (90, 90, 90)
            Image.fromarray(vis).save(f"{a.out}_{name}.png")
    if a.min and worst < a.min:
        sys.exit(1)


# ---------------------------------------------------------------- svg path parsing (smooth)

TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def path_segments(d):
    """Parse SVG path data into subpaths of (x0, y0, x1, y1, kind) chords between on-curve nodes.
    kind is 'line' (M/L/H/V/Z), 'curve' (C/S/Q/T) or 'arc' (A)."""
    toks = TOKEN.findall(d)
    subpaths, cur, pos, start, cmd, i = [], [], (0.0, 0.0), (0.0, 0.0), None, 0

    def take(n, arc):
        nonlocal i
        out = []
        while len(out) < n:
            if i >= len(toks) or toks[i].isalpha():
                raise ValueError(f"path data ends inside a {cmd} command")
            t = toks[i]
            if arc and len(out) in (3, 4) and t[0] in "01" and len(t) > 1 and "e" not in t.lower():
                # compact arc flags such as 'a5 5 0 011 1': each flag is one character
                out.append(float(t[0]))
                toks[i] = t[1:]
                continue
            out.append(float(t))
            i += 1
        return out

    while i < len(toks):
        if toks[i].isalpha():
            cmd, i = toks[i], i + 1
            if cmd in "Zz":
                if cur and (pos[0], pos[1]) != start:
                    cur.append((pos[0], pos[1], start[0], start[1], "line"))
                if cur:
                    subpaths.append(cur)
                cur, pos = [], start
                continue
        if cmd is None:
            raise ValueError("path data must start with M")
        u, rel = cmd.upper(), cmd.islower()
        args = take(ARITY[u], u == "A")
        bx, by = pos if rel else (0.0, 0.0)
        if u == "H":
            new = (args[0] + (pos[0] if rel else 0), pos[1])
        elif u == "V":
            new = (pos[0], args[0] + (pos[1] if rel else 0))
        else:
            new = (args[-2] + bx, args[-1] + by)
        if u == "M":
            if cur:
                subpaths.append(cur)
            cur, start = [], new
            cmd = "l" if rel else "L"  # extra pairs after M are lines
        else:
            kind = "arc" if u == "A" else "curve" if u in "CSQT" else "line"
            cur.append((pos[0], pos[1], new[0], new[1], kind))
        pos = new
    if cur:
        subpaths.append(cur)
    return subpaths


def path_nodes(d):
    """On-curve nodes of each subpath (absolute coordinates)."""
    return [[(s[0][0], s[0][1])] + [(x1, y1) for _, _, x1, y1, _ in s] for s in path_segments(d)]


def subpath_stats(segs):
    np = need("numpy")
    lens, grid, axis = [], 0, 0
    for x0, y0, x1, y1, kind in segs:
        dx, dy = x1 - x0, y1 - y0
        ln = (dx * dx + dy * dy) ** 0.5
        if ln < 1e-6 or kind == "arc":  # arcs are smooth by construction
            continue
        lens.append(ln)
        if all(abs(v * 2 - round(v * 2)) < 0.02 for v in (x1, y1)):
            grid += 1
        if ln < 6 and min(abs(dx), abs(dy)) < 0.6:
            axis += 1
    if not lens:
        return None
    n = len(lens)
    lens = np.array(lens)
    return {"segments": n, "perimeter": float(lens.sum()), "median": float(np.median(lens)), "min": float(lens.min()),
            "micro": float((lens < 1.2).mean()), "grid": grid / n, "axis": axis / n}


def path_stats(d):
    """Stats of the worst subpath (the one most like a pixel staircase), or None for an empty path."""
    subs = [s for s in (subpath_stats(x) for x in path_segments(d)) if s]
    if not subs:
        return None
    bad = [s for s in subs if is_staircase(s)]
    return dict(max(bad or subs, key=lambda s: s["segments"]), subpaths=len(subs))


def is_staircase(s):
    """Pixel-traced outlines: many sub-pixel segments, nodes snapped to the half-pixel grid, or
    short near-axis-aligned runs (the 'run then jog' pattern of a traced pixel edge). Small
    subpaths (fewer than 12 segments or under 40px around) are ignored."""
    if s["segments"] < 12 or s["perimeter"] < 40:
        return False
    return s["micro"] > 0.30 or (s["grid"] > 0.5 and s["median"] < 6) or s["axis"] > 0.5


def cmd_smooth(a):
    flagged = 0
    for f in a.files:
        if not os.path.exists(f):
            die(f"not found: {f}")
        text = pathlib.Path(f).read_text(encoding="utf-8")
        rows = []
        for m in re.finditer(r"<path\b([^>]*)>", text, re.S):
            attrs = m.group(1)
            d = re.search(r'\sd\s*=\s*"([^"]*)"', attrs) or re.search(r"\sd\s*=\s*'([^']*)'", attrs)
            if not d:
                continue
            pid = re.search(r'\sid\s*=\s*"([^"]*)"', attrs)
            name = pid.group(1) if pid else f"path@line{text.count(chr(10), 0, m.start()) + 1}"
            try:
                s = path_stats(d.group(1))
            except ValueError as e:
                print(f"  warning: could not parse {name}: {e}", file=sys.stderr)
                continue
            if s:
                rows.append((name, s))
        bad = [(n, s) for n, s in rows if is_staircase(s)]
        flagged += len(bad)
        rasters = len(re.findall(r"<image\b", text))
        print(f"{f}: {len(rows)} paths, {len(bad)} stair-stepped, {rasters} <image> tags")
        for name, s in (rows if a.verbose else bad):
            tag = "STAIRS" if is_staircase(s) else "ok"
            print(f"  {tag:6s} {name:24s} subpaths {s['subpaths']:3d}  worst: segs {s['segments']:4d}  median {s['median']:5.1f}px  min {s['min']:4.2f}px  "
                  f"micro {s['micro']:.0%}  half-px-grid {s['grid']:.0%}  axis-jogs {s['axis']:.0%}")
        if rasters and not a.allow_raster:
            flagged += rasters
    sys.exit(1 if flagged else 0)


# ---------------------------------------------------------------- trace

def cmd_trace(a):
    np = need("numpy")
    cv2 = need("cv2", "opencv-python-headless")
    potrace = need("potrace", "potracer")
    if a.mask_png:
        m = as_array(load_rgb(a.mask_png)).mean(axis=2) > 127
    else:
        if not a.select:
            die("give --select SPEC (e.g. 'L<60' or '#1b1c22~25') or --mask-png")
        m = mask_from_spec(as_array(load_rgb(a.image)), a.select)
    ox = oy = 0
    box = parse_box(a.box)
    if box:
        x0, y0, x1, y1 = box
        m, ox, oy = m[y0:y1, x0:x1], x0, y0
    m = m.astype("uint8")
    if a.open:
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((a.open, a.open), "uint8"))
    if a.close:
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((a.close, a.close), "uint8"))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    comps = sorted(range(1, n), key=lambda i: -stats[i][4])
    keep = [i for i in comps if stats[i][4] >= a.min_area][: a.keep or None]
    m = np.isin(lab, keep)
    if a.fill_holes:  # outer outlines only; eyes, keyholes etc. are drawn on top as their own shapes
        cs, _ = cv2.findContours(m.astype("uint8"), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        m = np.zeros(m.shape, "uint8")
        cv2.drawContours(m, cs, -1, 1, thickness=-1)
        m = m.astype(bool)
    # Upscale + blur + re-threshold turns pixel stairs into a smooth edge before potrace fits curves.
    U = a.upscale
    big = cv2.resize(m.astype("float32"), (m.shape[1] * U, m.shape[0] * U), interpolation=cv2.INTER_LINEAR)
    if a.blur > 0:
        big = cv2.GaussianBlur(big, (0, 0), a.blur * U)
    big = big > 0.5
    curves = potrace.Bitmap(~big).trace(turdsize=a.turdsize * U * U, alphamax=a.alphamax,
                                        opticurve=True, opttolerance=a.opttolerance)

    def pt(p):
        return f"{p.x / U + ox:.2f},{p.y / U + oy:.2f}"

    parts, nodes = [], 0
    for c in curves:
        seg = [f"M{pt(c.start_point)}"]
        for s in c.segments:
            seg.append(f"L{pt(s.c)}L{pt(s.end_point)}" if s.is_corner else f"C{pt(s.c1)} {pt(s.c2)} {pt(s.end_point)}")
            nodes += 1
        parts.append("".join(seg) + "Z")
    d = "".join(parts)
    stats_ = path_stats(d)
    info = f"{len(curves)} subpaths, {nodes} segments"
    if stats_:
        info += f", median segment {stats_['median']:.1f}px" + (" (STAIR-STEPPED: raise --blur/--upscale)" if is_staircase(stats_) else "")
    print(info, file=sys.stderr)
    out = f'<path d="{d}" fill="{a.fill}" fill-rule="evenodd"/>'
    if a.svg:
        h, w = (m.shape if not box else (box[3] - box[1], box[2] - box[0]))
        vb = f"{ox} {oy} {w} {h}"
        out = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" width="{w}" height="{h}">{out}</svg>'
    if a.out:
        pathlib.Path(a.out).write_text(out + "\n", encoding="utf-8")
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(out)


# ---------------------------------------------------------------- gallery

GALLERY_CSS = """
:root{color-scheme:dark;--page:#0e0f11;--text:#cfd2d6;--muted:#8b9096}
@media (prefers-color-scheme: light){:root{color-scheme:light;--page:#f4f5f7;--text:#25272b;--muted:#6b7078}}
*{box-sizing:border-box}
html,body{margin:0;background:var(--page);color:var(--text);font-family:"Segoe UI Variable Text","Segoe UI",system-ui,sans-serif}
main{max-width:1700px;margin:0 auto;padding:40px 16px 80px}
h1{font-size:20px;font-weight:600;margin:0 0 28px}
main>section,main>.pairs{display:flex;flex-direction:column;align-items:center;gap:40px;margin-bottom:40px}
figure.card{margin:0;display:grid;grid-template-columns:var(--w,800px) var(--w,800px);column-gap:24px;align-items:start;max-width:100%}
figure.card>figcaption{grid-column:1/-1;font-size:12px;color:var(--muted);margin:0 0 8px;display:flex;justify-content:space-between}
figure.card>figcaption::after{content:"Original";width:var(--w,800px)}
figure.card>.ref,figure.card>.replica{width:var(--w,800px);max-width:100%;height:auto;display:block}
figure.card>.ref{grid-column:2;grid-row:2}
figure.card>iframe.replica{border:0;aspect-ratio:var(--ar,16/10);height:auto}
@media (max-width:1700px){figure.card{grid-template-columns:minmax(0,var(--w,800px))}figure.card>.ref{grid-column:1;grid-row:3;margin-top:12px}figure.card>figcaption::after{content:none}}
"""


def rel_url(target, out_dir):
    try:
        rel = os.path.relpath(os.path.abspath(target), out_dir).replace(os.sep, "/")
    except ValueError:  # another drive on Windows: fall back to an absolute file URL
        return pathlib.Path(target).resolve().as_uri()
    return urllib.parse.quote(rel)


def cmd_gallery(a):
    out = os.path.abspath(a.out)
    out_dir = os.path.dirname(out)
    body = []
    if a.parts:
        refs = json.load(open(a.refs, encoding="utf-8")) if a.refs else {}
        base = os.path.dirname(os.path.abspath(a.refs)) if a.refs else out_dir
        files = sorted(p for p in pathlib.Path(a.parts).glob("*.html"))
        for fp in files:
            s = fp.read_text(encoding="utf-8")

            def add_ref(m):
                fid = m.group(2)
                img = ""
                if fid in refs:
                    path = os.path.join(base, refs[fid])
                    if not os.path.exists(path):
                        print(f"warning: reference for {fid} not found: {path}", file=sys.stderr)
                    img = f'<img class="ref" src="{rel_url(path, out_dir)}" alt="Original {html.escape(fid)}" loading="lazy">'
                return m.group(1) + m.group(3) + img + m.group(4)
            body.append(re.sub(r"""(<figure\b[^>]*?\sid=["']([^"']+)["'][^>]*>)(.*?)(</figure>)""", add_ref, s, flags=re.S))
        missing = [k for k in refs if f'id="{k}"' not in "".join(body)]
        if missing:
            print(f"warning: refs without a matching figure: {', '.join(missing)}", file=sys.stderr)
    if a.pair:
        from PIL import Image
        items = []
        for replica, ref, *label in a.pair:
            w, h = Image.open(ref).size
            dw = min(800, w)
            name = html.escape(label[0] if label else os.path.basename(replica))
            src = rel_url(replica, out_dir)
            if replica.lower().endswith((".html", ".htm")):
                el = f'<iframe class="replica" src="{src}" style="--ar:{w}/{h}" loading="lazy"></iframe>'
            else:
                el = f'<img class="replica" src="{src}" alt="{name}">'
            items.append(f'<figure class="card" style="--w:{dw}px"><figcaption>{name}</figcaption>{el}'
                         f'<img class="ref" src="{rel_url(ref, out_dir)}" alt="Original {name}" loading="lazy"></figure>')
        body.append('<div class="pairs">' + "\n".join(items) + "</div>")
    if not body:
        die("nothing to show: pass --parts DIR and/or --pair REPLICA REF [LABEL]")
    page = (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{html.escape(a.title)}</title><style>{GALLERY_CSS}</style></head><body><main><h1>{html.escape(a.title)} &mdash; replica (left) vs original (right)</h1>\n"
            + "\n".join(body) + "\n</main></body></html>\n")
    pathlib.Path(out).write_text(page, encoding="utf-8")
    print(f"wrote {out} ({len(page) // 1024} KB)")


# ---------------------------------------------------------------- inventory helpers

def cmd_images(a):
    """List images in a folder (including extensionless ones), flag duplicates, optionally copy to clean names."""
    from PIL import Image
    seen, rows = {}, []
    for p in sorted(pathlib.Path(a.dir).iterdir()):
        if not p.is_file():
            continue
        try:
            with Image.open(p) as im:
                fmt, size = im.format, im.size
        except Exception:  # noqa: BLE001 - not an image
            continue
        digest = hashlib.md5(p.read_bytes()).hexdigest()
        dup = seen.get(digest)
        seen.setdefault(digest, p.name)
        path = p
        ext = "." + fmt.lower().replace("jpeg", "jpg")
        wrong = p.suffix.lower() not in ({ext, ".jpeg"} if ext == ".jpg" else {ext})
        if a.fix_ext and wrong:
            # extensionless downloads or mislabelled files: copy under a name browsers sniff correctly
            os.makedirs(a.fix_ext, exist_ok=True)
            stem = (p.stem if p.suffix.lower() in IMAGE_EXT else p.name)[:60].rstrip(".") or "image"
            path, k = pathlib.Path(a.fix_ext) / f"{stem}{ext}", 2
            while path.exists() and hashlib.md5(path.read_bytes()).hexdigest() != digest:
                path, k = pathlib.Path(a.fix_ext) / f"{stem}-{k}{ext}", k + 1
            shutil.copyfile(p, path)
        rows.append({"file": str(path), "format": fmt, "size": list(size), "duplicate_of": dup,
                     "wrong_extension": wrong and path == p})
    if a.json:
        print(json.dumps(rows, indent=1))
        return
    for r in rows:
        note = f"  DUPLICATE of {r['duplicate_of']}" if r["duplicate_of"] else ""
        note += "  (extension does not match the format: use --fix-ext)" if r["wrong_extension"] else ""
        print(f"{r['format']:5s} {r['size'][0]:5d}x{r['size'][1]:<5d} {r['file']}{note}")


# ---------------------------------------------------------------- doctor / setup / selftest

def probe_modules():
    status = {}
    for mod, pip_name in (("PIL", "pillow"), ("numpy", "numpy"), ("cv2", "opencv-python-headless"),
                          ("potrace", "potracer"), ("playwright", "playwright")):
        try:
            __import__(mod)
            status[pip_name] = True
        except ImportError:
            status[pip_name] = False
    return status


def probe_browser():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b, used = launch_browser(p)
            b.close()
            return used
    except SystemExit:
        return None
    except Exception:  # noqa: BLE001
        return None


def cmd_doctor(a):
    ok = True
    print(f"python {sys.version.split()[0]} at {sys.executable}")
    for name, present in probe_modules().items():
        print(f"  {'ok     ' if present else 'MISSING'} {name}")
        ok &= present
    used = probe_browser() if probe_modules()["playwright"] else None
    print(f"  {'ok     ' if used else 'MISSING'} browser for rendering{f' ({used})' if used else ''}")
    ok &= bool(used)
    print("ready" if ok else "not ready: run `facsimile setup`")
    sys.exit(0 if ok else 1)


def cmd_setup(a):
    req = ROOT / "requirements.txt"
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(req)] + (["--user"] if a.user else [])
    print("$ " + " ".join(cmd))
    if subprocess.call(cmd):
        die("pip install failed")
    if not probe_browser():
        cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
        print("$ " + " ".join(cmd))
        if subprocess.call(cmd):
            die("playwright browser install failed")
    print("setup done; run `facsimile doctor` to confirm")


def cmd_selftest(a):
    """End-to-end check of every command on synthetic inputs, with literal expected values."""
    np = need("numpy")
    from PIL import Image, ImageDraw
    me = [sys.executable, str(pathlib.Path(__file__).resolve())]
    failures = []

    def run(*args):
        r = subprocess.run(me + list(args), capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def check(name, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{'' if cond else '  ' + detail.strip()[:400]}")
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as t:
        P = lambda n: os.path.join(t, n)  # noqa: E731
        # 1. render an HTML page and diff it against a PIL drawing of the same thing
        pathlib.Path(P("page.html")).write_text(
            '<!doctype html><body style="margin:0;background:#202124">'
            '<div style="position:absolute;left:20px;top:30px;width:60px;height:40px;background:#4cc2ff"></div>', encoding="utf-8")
        ref = Image.new("RGB", (120, 100), "#202124")
        ImageDraw.Draw(ref).rectangle((20, 30, 79, 69), fill="#4cc2ff")
        ref.save(P("ref.png"))
        code, out = run("render", P("page.html"), P("out.png"), "--size", "120x100")
        check("render html", code == 0 and os.path.exists(P("out.png")), out)
        if code == 0:
            code, out = run("diff", P("out.png"), P("ref.png"), "--out", P("d"))
            m = re.search(r"mean_abs_diff ([\d.]+)", out)
            check("diff identical render = 0", code == 0 and m and float(m.group(1)) < 0.05, out)
            code, out = run("sample", P("out.png"), "50,50", "5,5")
            check("sample colours", "#4cc2ff" in out and "#202124" in out, out)
        # 2. iou of a disc against itself and against a shifted disc
        yy, xx = np.mgrid[:100, :100]
        disc = (((xx - 50) ** 2 + (yy - 50) ** 2) < 30 ** 2)
        Image.fromarray(np.where(disc[..., None], 10, 240).repeat(3, 2).astype("uint8")).save(P("disc.png"))
        shifted = (((xx - 56) ** 2 + (yy - 50) ** 2) < 30 ** 2)
        Image.fromarray(np.where(shifted[..., None], 10, 240).repeat(3, 2).astype("uint8")).save(P("disc2.png"))
        code, out = run("iou", P("disc.png"), P("disc.png"), "--mask", "dark:L<60")
        check("iou identical = 1", "IoU 1.0000" in out, out)
        code, out = run("iou", P("disc2.png"), P("disc.png"), "--mask", "dark:L<60", "--min", "0.97")
        check("iou shifted fails --min", code == 1, out)
        # 3. trace the disc: smooth result, few segments, close to the true circle
        code, out = run("trace", P("disc.png"), "--select", "L<60", "--svg", "--out", P("disc.svg"))
        check("trace disc", code == 0 and os.path.exists(P("disc.svg")), out)
        if code == 0:
            nodes = path_nodes(re.search(r' d="([^"]+)"', pathlib.Path(P("disc.svg")).read_text()).group(1))[0]
            # pixel (i, j) covers [i, i+1], so the disc drawn around pixel 50 is centred at 50.5
            radii = [((x - 50.5) ** 2 + (y - 50.5) ** 2) ** 0.5 for x, y in nodes]
            check("traced disc radius within 1px", max(abs(r - 30) for r in radii) < 1.0, str(radii[:6]))
            code, out = run("smooth", P("disc.svg"))
            check("smooth passes traced disc", code == 0, out)
        # 4. a pixel staircase path must be flagged
        stairs = "M0,0" + "".join(f"L{i + 1}.5,{i}.00L{i + 1}.5,{i + 1}.00" for i in range(30)) + "Z"
        pathlib.Path(P("stairs.svg")).write_text(f'<svg xmlns="http://www.w3.org/2000/svg"><path id="k" d="{stairs}"/></svg>')
        code, out = run("smooth", P("stairs.svg"))
        check("smooth flags staircase", code == 1 and "STAIRS" in out, out)
        # 4b. compact arc flags parse, and arcs never count as stairs
        check("arc flags parse", path_nodes("M0 0 A5 5 0 0110 0") == [[(0.0, 0.0), (10.0, 0.0)]], str(path_nodes("M0 0 A5 5 0 0110 0")))
        # 4c. extent reports the box of the drawn rectangle
        code, out = run("extent", P("ref.png"), "--select", "#4cc2ff~10")
        check("extent box", "box 20,30-80,70" in out, out)
        # 5. tiles finds two objects
        sheet = Image.new("RGB", (200, 80), "#f6f6f7")
        d = ImageDraw.Draw(sheet)
        d.rounded_rectangle((10, 10, 70, 70), 12, fill="#1b1c22")
        d.rounded_rectangle((110, 10, 170, 70), 12, fill="#8b7cf6")
        sheet.save(P("sheet.png"))
        code, out = run("tiles", P("sheet.png"), "--pad", "4")
        check("tiles finds 2 objects", "2 objects" in out and "6,6-75,75" in out, out)
        # 6. gallery pairs page
        code, out = run("gallery", "--pair", P("disc.svg"), P("disc.png"), "disc", "--out", P("g.html"))
        check("gallery pairs", code == 0 and "disc.svg" in pathlib.Path(P("g.html")).read_text(), out)
    print("selftest " + ("passed" if not failures else f"FAILED: {', '.join(failures)}"))
    sys.exit(1 if failures else 0)


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(prog="facsimile", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("render", help="screenshot an HTML/SVG/image file at an exact viewport size")
    p.add_argument("file")
    p.add_argument("out")
    p.add_argument("--size", required=True, help="viewport WxH in CSS px, e.g. 800x500")
    p.add_argument("--scale", type=float, default=1, help="device scale factor (use 2 for retina references, 4 to inspect edges)")
    p.add_argument("--bg", default="#ffffff", help="page colour behind a bare SVG/image (sample the reference's page colour)")
    p.add_argument("--query", default="", help="query string appended to an HTML file URL, e.g. dark")
    p.add_argument("--color-scheme", dest="color_scheme", choices=["light", "dark"], default="light")
    p.add_argument("--selector", help="screenshot only the first element matching this CSS selector")
    p.add_argument("--full-page", action="store_true")
    p.add_argument("--transparent", action="store_true", help="omit the page background (PNG alpha)")
    p.add_argument("--wait", type=int, default=400, help="ms to wait after fonts load")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("diff", help="compare a render with the reference: score, worst regions, diff images")
    p.add_argument("render")
    p.add_argument("ref")
    p.add_argument("--box", nargs=4, metavar=("X0", "Y0", "X1", "Y1"), help="x0 y0 x1 y1 in reference px: score only this region")
    p.add_argument("--out", help="prefix for <prefix>_diff.png and <prefix>_side.png")
    p.add_argument("--cell", type=int, default=32, help="size of the regions ranked in the worst list")
    p.add_argument("--top", type=int, default=8)
    p.add_argument("--amp", type=float, default=4, help="amplification of the diff image")
    p.set_defaults(fn=cmd_diff)

    p = sub.add_parser("sample", help="read exact colours: points, edge profiles, region averages")
    p.add_argument("image")
    p.add_argument("points", nargs="*", help="x,y points")
    p.add_argument("--vs", help="second image (e.g. your render) to compare the same coordinates against")
    p.add_argument("--row", nargs=3, type=int, metavar=("Y", "X0", "X1"), help="horizontal profile")
    p.add_argument("--col", nargs=3, type=int, metavar=("X", "Y0", "Y1"), help="vertical profile")
    p.add_argument("--step", type=int, default=1)
    p.add_argument("--avg", nargs=4, metavar=("X0", "Y0", "X1", "Y1"), help="x0 y0 x1 y1: mean colour of a region")
    p.add_argument("--radius", type=int, default=0, help="average a (2r+1)^2 window around each point")
    p.set_defaults(fn=cmd_sample)

    p = sub.add_parser("zoom", help="crop and enlarge a region to inspect edges, strokes and radii")
    p.add_argument("image")
    p.add_argument("out")
    p.add_argument("--box", nargs=4, metavar=("X0", "Y0", "X1", "Y1"), required=True)
    p.add_argument("--scale", type=int, default=6)
    p.add_argument("--smooth", action="store_true", help="LANCZOS instead of nearest-neighbour")
    p.set_defaults(fn=cmd_zoom)

    p = sub.add_parser("tiles", help="find the objects (icons, cards, tiles) on a sheet and crop them")
    p.add_argument("image")
    p.add_argument("--out", help="folder for tile_NN.png crops and tiles.json")
    p.add_argument("--threshold", type=float, default=24, help="summed RGB distance from the background")
    p.add_argument("--pad", type=int, default=16, help="padding around each object (keep its drop shadow)")
    p.add_argument("--close", type=int, default=7, help="morphological close kernel to merge parts of one object")
    p.add_argument("--min-area", dest="min_area", type=int, default=400)
    p.add_argument("--merge", type=int, default=8, help="merge objects whose boxes come within this many px")
    p.set_defaults(fn=cmd_tiles)

    p = sub.add_parser("extent", help="bounding boxes of the pixels matching a mask (text runs, buttons, icons)")
    p.add_argument("image")
    p.add_argument("--select", required=True, help="mask SPEC: L<60 | L>230 | #rrggbb~30 (quote it in shells)")
    p.add_argument("--box", nargs=4, metavar=("X0", "Y0", "X1", "Y1"))
    p.add_argument("--join", type=int, default=0, help="merge regions closer than this many px horizontally (glyphs into words)")
    p.add_argument("--min-area", dest="min_area", type=int, default=4)
    p.add_argument("--top", type=int, default=30)
    p.set_defaults(fn=cmd_extent)

    p = sub.add_parser("iou", help="shape agreement of colour/luminance masks between render and reference")
    p.add_argument("render")
    p.add_argument("ref")
    p.add_argument("--mask", action="append", help="name:SPEC, SPEC = L<60 | L>230 | #rrggbb~30 (repeatable)")
    p.add_argument("--box", nargs=4, metavar=("X0", "Y0", "X1", "Y1"))
    p.add_argument("--min", type=float, help="exit 1 if any IoU is below this")
    p.add_argument("--out", help="prefix for mismatch images (red = only in reference, blue = only in render)")
    p.set_defaults(fn=cmd_iou)

    p = sub.add_parser("smooth", help="flag stair-stepped (pixel-traced) SVG paths and embedded rasters")
    p.add_argument("files", nargs="+")
    p.add_argument("-v", "--verbose", action="store_true", help="list every path, not only flagged ones")
    p.add_argument("--allow-raster", action="store_true")
    p.set_defaults(fn=cmd_smooth)

    p = sub.add_parser("trace", help="trace one colour region of an image into a smooth SVG path")
    p.add_argument("image")
    p.add_argument("--select", help="mask SPEC: L<60 | L>230 | #rrggbb~30")
    p.add_argument("--mask-png", dest="mask_png", help="use a prepared mask image (white = shape) instead of --select")
    p.add_argument("--box", nargs=4, metavar=("X0", "Y0", "X1", "Y1"), help="x0 y0 x1 y1: only trace inside this box (coordinates stay global)")
    p.add_argument("--open", type=int, default=0, help="morphological open kernel (removes specks)")
    p.add_argument("--close", type=int, default=0, help="morphological close kernel (fills pinholes)")
    p.add_argument("--keep", type=int, default=0, help="keep only the N largest regions")
    p.add_argument("--min-area", dest="min_area", type=int, default=12)
    p.add_argument("--upscale", type=int, default=4)
    p.add_argument("--blur", type=float, default=1.5, help="Gaussian sigma in source px applied after upscaling (raise it if smooth flags the result)")
    p.add_argument("--fill-holes", dest="fill_holes", action="store_true", help="fill interior holes (eyes, keyholes) so details can be drawn over a solid body")
    p.add_argument("--alphamax", type=float, default=1.1, help="potrace corner threshold (higher = rounder)")
    p.add_argument("--opttolerance", type=float, default=0.6, help="potrace curve-merging tolerance")
    p.add_argument("--turdsize", type=int, default=4, help="ignore regions smaller than this (source px)")
    p.add_argument("--fill", default="#000")
    p.add_argument("--svg", action="store_true", help="wrap in a standalone <svg> for preview")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_trace)

    p = sub.add_parser("gallery", help="one page with every replica beside its original")
    p.add_argument("--out", required=True)
    p.add_argument("--title", default="Replicas")
    p.add_argument("--parts", help="folder of HTML fragments (<figure class=card id=..><div class=stage>...)")
    p.add_argument("--refs", help="JSON map of figure id -> reference image path (relative to the JSON file)")
    p.add_argument("--pair", nargs="+", action="append", metavar="ITEM", help="REPLICA REF [LABEL] (repeatable)")
    p.set_defaults(fn=cmd_gallery)

    p = sub.add_parser("images", help="inventory a folder of references: formats, sizes, duplicates")
    p.add_argument("dir")
    p.add_argument("--fix-ext", dest="fix_ext", help="copy extensionless images into this folder with a proper extension")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_images)

    sub.add_parser("doctor", help="check Python packages and the rendering browser").set_defaults(fn=cmd_doctor)
    p = sub.add_parser("setup", help="install the Python packages and, if needed, Playwright's Chromium")
    p.add_argument("--user", action="store_true", help="pip install --user")
    p.set_defaults(fn=cmd_setup)
    sub.add_parser("selftest", help="run every command on synthetic inputs").set_defaults(fn=cmd_selftest)

    a = ap.parse_args()
    if getattr(a, "pair", None):
        for item in a.pair:
            if len(item) not in (2, 3):
                die("--pair takes REPLICA REF [LABEL]")
    a.fn(a)


if __name__ == "__main__":
    main()
