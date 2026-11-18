"""Cable labels: flags, wrap-around bands and keystone inserts, several names per label.

A cable template has "kind": "cable" and a "layout":

  flag      face A | wrap zone | face B. The wrap zone goes round the cable, then A and B
            stick back to back. Face A is printed upside down so both sides read upright
            when the flag hangs off the cable. (The HDMI switch flags, ported from
            dymo-cable-labels/hdmi_flags.py: face 19 mm, 7.5 mm cable, 54 mm along.)
  wrap      a band that goes right round the cable, with the name repeated in rows round
            the circumference so it reads from any side; every other row is upside down
            so it also reads from either end. About 1.5 turns, so it sticks to itself.
  keystone  a small insert for a keystone jack or patch panel label window, w x h mm.
  pair      a wrap band and a keystone insert for each name, side by side.
  ends      plain wrap bands that name the OTHER end of the cable. A line "GARAGE / LIVING ROOM"
            gives two bands side by side: LIVING ROOM (stick it on at the garage end) and
            GARAGE (for the living room end). A line with no "/" gives one band as typed.
            Same orientation all the way round, like DYMO's own cable-wrap mode.

Fields come from the user at print time (no names are baked in):
  names (one per line), each (copies of each name), cable (diameter mm), along (length
  along the cable mm), face (flag depth mm), win_w / win_h (keystone window mm).
Every piece gets crop marks at its corners; full-width pieces get a cut line between them.
Names that don't fit one label run on to more labels.
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

from . import render

MM = render.MM
FONT = os.path.join(render.FONT_DIR, render.FONTS["bold"])
SMALL = os.path.join(render.FONT_DIR, render.FONTS["cond"])
MAX_NAMES = 60
NUM = {   # key: (min, max) in mm, or a count
    "each": (1, 10), "cable": (2, 15), "along": (8, 55), "face": (6, 40),
    "win_w": (5, 55), "win_h": (4, 40), "turns": (1, 3),
}
EDGE, GAP = 0.5, 1.2       # mm: label edge to the first piece, and between pieces
USABLE_W = render.LABEL_W - 2 * 2.0   # the DYMO 450 can't print the outer ~2 mm of a 59 mm label


def px(mm):
    return int(round(mm * MM))


def num(fields, key, default):
    s = str(fields.get(key, "")).strip().replace(",", ".")
    if not s:
        return float(default)
    try:
        v = float(s)
    except ValueError:
        raise render.LabelError(f"{key} must be a number")
    lo, hi = NUM[key]
    if not lo <= v <= hi:
        raise render.LabelError(f"{key} must be {lo:g} to {hi:g}")
    return v


def names(fields):
    out = [s.strip() for s in str(fields.get("names", "")).replace("\r", "").split("\n")]
    out = [s[:40] for s in out if s]
    if len(out) > MAX_NAMES:
        raise render.LabelError(f"at most {MAX_NAMES} names at a time")
    return out


def _font(size, path=FONT):
    return ImageFont.truetype(path, max(6, int(size)))


def text_box(name, w, h, pad=0.12, two_lines=True):
    """name as big as it fits in w x h px, centred; one line, or two if that is bigger."""
    img = Image.new("L", (w, h), 255)
    if not name:
        return img
    d = ImageDraw.Draw(img)
    iw, ih = w * (1 - pad), h * (1 - 2 * pad)

    def best(lines, max_h):
        size = int(max_h * 1.4)
        while size > 6:
            f = _font(size)
            boxes = [d.textbbox((0, 0), s, font=f) for s in lines]
            if all(r - l <= iw for l, _, r, _ in boxes) and all(b - t <= max_h for _, t, _, b in boxes):
                return f
            size -= 1
        return _font(6)

    choice = ([name], best([name], ih))
    if two_lines and " " in name:
        cut = name.rfind(" ", 0, len(name) // 2 + 1)
        cut = cut if cut > 0 else name.find(" ")
        two = [name[:cut], name[cut + 1:]]
        f2 = best(two, ih / 2 * 0.86)
        if f2.size > choice[1].size * 1.15:
            choice = (two, f2)
    lines, f = choice
    boxes = [d.textbbox((0, 0), s, font=f) for s in lines]
    gap = f.size * 0.18
    block = sum(b - t for _, t, _, b in boxes) + gap * (len(lines) - 1)
    y = (h - block) / 2
    for s, (l, t, r, b) in zip(lines, boxes):
        d.text(((w - (r - l)) / 2 - l, y - t), s, font=f, fill=0)
        y += (b - t) + gap
    return img


def _dashed(d, x0, x1, y, dash=22, gap=14, width=3):
    x = x0
    while x < x1:
        d.line((x, y, min(x + dash, x1), y), fill=0, width=width)
        x += dash + gap


def flag(name, along, face, cable):
    """Face A (upside down) | wrap zone | face B, along mm wide."""
    wrap = math.pi * cable + 0.5
    w, fh, wh = px(along), px(face), px(wrap)
    img = Image.new("L", (w, 2 * fh + wh), 255)
    d = ImageDraw.Draw(img)
    up = Image.new("L", (w, fh), 255)
    ud = ImageDraw.Draw(up)
    ud.rounded_rectangle((0, 6, w - 1, fh - 7), radius=min(22, fh // 4), outline=0, width=5)
    t = text_box(name, w - 30, fh - 30, pad=0.04)
    up.paste(t, (15, 15))
    img.paste(up.rotate(180), (0, 0))
    img.paste(up, (0, fh + wh))
    _dashed(d, 0, w, fh + 4)
    _dashed(d, 0, w, fh + wh - 4)
    hint = "wrap round cable" if along >= 30 else "wrap"
    f = _font(min(24, wh * 0.28), SMALL)
    d.text((w / 2, fh + wh / 2), hint, font=f, fill=0, anchor="mm")
    return img


def wrap(name, along, cable, turns=1.5, flip=True):
    """A band round the cable: rows of the name across the circumference, every other one flipped."""
    circ = math.pi * (cable + 0.2)
    rows_per_turn = max(3, round(circ / 5.0))
    pitch = circ / rows_per_turn
    n = math.ceil(rows_per_turn * turns)
    w, rh = px(along), px(pitch)
    img = Image.new("L", (w, rh * n), 255)
    for i in range(n):
        row = text_box(name, w, rh, pad=0.1, two_lines=False)
        img.paste(row.rotate(180) if flip and i % 2 else row, (0, i * rh))
    return img


def keystone(name, ww, wh):
    w, h = px(ww), px(wh)
    img = Image.new("L", (w, h), 255)
    m = max(4, px(0.5))
    img.paste(text_box(name, w - 2 * m, h - 2 * m, pad=0.03), (m, m))
    ImageDraw.Draw(img).rectangle((0, 0, w - 1, h - 1), outline=0, width=1)   # hairline, cut on it
    return img


def pieces(tpl, fields):
    p = tpl.get("defaults", {})
    g = lambda k: num(fields, k, p.get(k, {"each": 1, "turns": 1.5}.get(k, 10)))
    items = []
    lay = tpl["layout"]
    each = int(g("each"))
    if lay == "ends":
        for line in names(fields):
            a, _, b = (x.strip() for x in line.partition("/"))
            far = [b, a] if (a and b) else [a or b]
            for _ in range(each):
                items += [wrap(n, g("along"), g("cable"), g("turns"), flip=False) for n in far]
        if not items:
            raise render.LabelError("type at least one cable, e.g. GARAGE / LIVING ROOM")
        return items
    for name in names(fields):
        for _ in range(each):
            if lay == "flag":
                items.append(flag(name, g("along"), g("face"), g("cable")))
            elif lay == "wrap":
                items.append(wrap(name, g("along"), g("cable"), g("turns")))
            elif lay == "keystone":
                items.append(keystone(name, g("win_w"), g("win_h")))
            elif lay == "pair":
                items.append(wrap(name, g("along"), g("cable"), g("turns")))
                items.append(keystone(name, g("win_w"), g("win_h")))
            else:
                raise ValueError(f"unknown cable layout {lay}")
    if not items:
        raise render.LabelError("type at least one name (one per line)")
    return items


def _crop(d, x0, y0, x1, y1, k):
    for x, sx in ((x0, -1), (x1, 1)):
        for y, sy in ((y0, -1), (y1, 1)):
            d.line((x + sx * 2, y, x + sx * (2 + k), y), fill=0, width=2)
            d.line((x, y + sy * 2, x, y + sy * (2 + k)), fill=0, width=2)


def pack(items, page_w, page_h, usable_w):
    """Rows, left to right, centred across usable_w. Returns a list of page images."""
    edge, gap, uw = px(EDGE), px(GAP), px(usable_w)
    for it in items:
        if it.width > uw:
            raise render.LabelError(f"a piece is {it.width / MM:.0f} mm wide; the label takes {usable_w:g} mm")
        if it.height > page_h - 2 * edge:
            raise render.LabelError(f"a piece is {it.height / MM:.0f} mm long; too long for one label")
    rows, row = [], []
    for it in items:
        if row and sum(r.width for r in row) + gap * len(row) + it.width > uw:
            rows.append(row)
            row = []
        row.append(it)
    rows.append(row)
    pages, y, cur = [], edge, None
    for row in rows:
        rh = max(r.height for r in row)
        if cur is None or y + rh > page_h - edge:
            cur = Image.new("L", (page_w, page_h), 255)
            pages.append(cur)
            y = edge
        d = ImageDraw.Draw(cur)
        total = sum(r.width for r in row) + gap * (len(row) - 1)
        x = (page_w - total) // 2
        for r in row:
            cur.paste(r, (x, y + (rh - r.height) // 2))
            if r.width < uw * 0.8:
                _crop(d, x, y + (rh - r.height) // 2, x + r.width, y + (rh + r.height) // 2, gap // 2 - 2)
            x += r.width + gap
        if len(row) == 1 and row[0].width >= uw * 0.8:
            cy = y + rh + gap // 2
            if cy < page_h - edge:
                for xx in range(0, page_w, 20):
                    d.line((xx, cy, min(xx + 10, page_w), cy), fill=0, width=2)
        y += rh + gap
    return pages


def dymo_labels(tpl, fields):
    items = pieces(tpl, fields)
    pages = pack(items, render.W, render.H, USABLE_W)
    return [p.point(lambda v: 0 if v < 128 else 255).convert("1") for p in pages]


def a4_pages(tpl, fields):
    w, h = round(render.A4_W * MM), round(render.A4_H * MM)
    pages = pack(pieces(tpl, fields), w, h, render.A4_W - 20)
    return [p.point(lambda v: 0 if v < 100 else 255 if v > 200 else v) for p in pages]


def side_by_side(pages):
    """A preview of several labels next to each other."""
    if len(pages) == 1:
        return pages[0]
    gap = px(4)
    out = Image.new("L", (sum(p.width for p in pages) + gap * (len(pages) - 1), pages[0].height), 200)
    x = 0
    for p in pages:
        out.paste(p.convert("L"), (x, 0))
        x += p.width + gap
    return out
