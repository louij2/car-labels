"""Render car part stickers from JSON templates, for a DYMO LabelWriter or for paper.

A template (car_labels/templates/*.json) describes one sticker in millimetres:

    {"id": ..., "name": ..., "size": [w, h], "frame": 2.5, "scale": 1,
     "fields": [{"key": "serial", "label": ..., "example": ..., "hint": ...}],
     "elements": [{"t": "text", "s": "{serial}", "x": 2, "y": 3, "h": 2.4}, ...]}

Element types: text, box, line, barcode, image. Any "s" may hold {field} placeholders.
"if": "<field>" skips an element when that field is empty. Text sizing: "h" is the ink
height in mm; the width is natural, or "w" (fixed), or "wpc" (mm per character, capped at
"wmax"), so a value as long as the example renders exactly like the original sticker.
"spaced": true prints part numbers VAG style, with double spaces between groups;
"letterspace": true puts a space between every letter (the Aisin maker line).

The optional info card (a second sticker for the tuner, owner or notes) is the same for
every template: a logo, a bold title and two smaller lines.

Barcodes are Code 128 of the printed text (set C for an even run of digits, otherwise
set B). That is what VAG and Aisin use, but check a reprint against the original
before relying on a scanner reading it.
"""
import io
import json
import math
import os
import re
from PIL import Image, ImageDraw, ImageFont, ImageOps

Image.MAX_IMAGE_PIXELS = 40_000_000

DPI = 300
MM = DPI / 25.4
LABEL_W, LABEL_H = 59, 190               # DYMO 99019, mm
W, H = round(LABEL_W * MM), round(LABEL_H * MM)
A4_W, A4_H = 210, 297
FONT_DIR = os.environ.get("FONT_DIR", "/usr/share/fonts/truetype/dejavu")
FONTS = {"sans": "DejaVuSans.ttf", "sansbold": "DejaVuSans-Bold.ttf",
         "cond": "DejaVuSansCondensed.ttf", "bold": "DejaVuSansCondensed-Bold.ttf",
         "mono": "DejaVuSansMono.ttf"}
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

C128 = ("212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 221312 231212 "
        "112232 122132 122231 113222 123122 123221 223211 221132 221231 213212 223112 312131 "
        "311222 321122 321221 312212 322112 322211 212123 212321 232121 111323 131123 131321 "
        "112313 132113 132311 211313 231113 231311 112133 112331 132131 113123 113321 133121 "
        "313121 211331 231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
        "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 112412 122114 "
        "122411 142112 142211 241211 221114 413111 241112 134111 111242 121142 121241 114212 "
        "124112 124211 411212 421112 421211 212141 214121 412121 111143 111341 131141 114113 "
        "114311 411113 411311 113141 114131 311141 411131 211412 211214 211232 2331112").split()


class LabelError(ValueError):
    """Bad input: shown to the user as is."""


def code128(data):
    """Bar/space module widths. Set C for an even run of digits, else set B."""
    if data.isdigit() and len(data) % 2 == 0:
        vals = [105] + [int(data[i:i + 2]) for i in range(0, len(data), 2)]
    else:
        bad = sorted({c for c in data if not 32 <= ord(c) < 127})
        if bad:
            raise LabelError(f"a barcode can't hold {' '.join(bad)}")
        vals = [104] + [ord(c) - 32 for c in data]
    check = (vals[0] + sum(v * i for i, v in enumerate(vals[1:], 1))) % 103
    return "".join(C128[v] for v in vals + [check]) + C128[106]


def load_templates():
    out = {}
    for name in sorted(os.listdir(TEMPLATE_DIR)):
        if name.endswith(".json"):
            with open(os.path.join(TEMPLATE_DIR, name)) as f:
                t = json.load(f)
            out[t["id"]] = t
    return out


def fill(s, fields):
    return re.sub(r"\{(\w+)\}", lambda m: str(fields.get(m.group(1), "")).strip(), s)


class Canvas:
    def __init__(self, w_mm, h_mm, scale=1.0):
        self.k = scale
        self.img = Image.new("L", (self.px(w_mm), self.px(h_mm)), 255)
        self.d = ImageDraw.Draw(self.img)

    def px(self, mm):
        return int(round(mm * MM * self.k))

    def text(self, s, x, y, h, w=None, font="cond", anchor="l", fit=None):
        """Ink box h mm tall with its top at y. w squeezes to w mm; fit only ever shrinks."""
        if not s:
            return
        f = ImageFont.truetype(os.path.join(FONT_DIR, FONTS[font]), 200)
        l, t, r, b = f.getbbox(s)
        if r <= l or b <= t:
            return
        tile = Image.new("L", (r - l, b - t), 255)
        ImageDraw.Draw(tile).text((-l, -t), s, font=f, fill=0)
        th = max(1, self.px(h))
        tw = max(1, self.px(w) if w else int(tile.width * th / tile.height))
        if fit and tw > self.px(fit):
            th, tw = max(1, int(th * self.px(fit) / tw)), self.px(fit)
        tile = tile.resize((tw, th), Image.LANCZOS)
        x0 = self.px(x) - (tw // 2 if anchor == "c" else tw if anchor == "r" else 0)
        self.img.paste(tile, (x0, self.px(y)), ImageOps.invert(tile))

    def box(self, x0, y0, x1, y1, width=4):
        self.d.rectangle((self.px(x0), self.px(y0), self.px(x1), self.px(y1)), outline=0, width=width)

    def line(self, x0, x1, y, width=3):
        self.d.line((self.px(x0), self.px(y), self.px(x1), self.px(y)), fill=0, width=width)

    def barcode(self, data, x, y, h, w, anchor="l"):
        if not data:
            return
        bars = code128(data)
        modules = sum(map(int, bars))
        dot = math.floor(w * MM * self.k / modules + 0.05)
        if dot < 2:
            raise LabelError(f"barcode '{data}' is too long to print readably in {w:g} mm")
        total = dot * modules
        x0 = self.px(x) - (total // 2 if anchor == "c" else 0)
        y0, y1 = self.px(y), self.px(y + h)
        for i, m in enumerate(bars):
            wpx = int(m) * dot
            if i % 2 == 0:
                self.d.rectangle((x0, y0, x0 + wpx - 1, y1), fill=0)
            x0 += wpx

    def image(self, img, x, y, w, h=None, anchor="l"):
        """Greyscale art: solid blacks and whites stay solid, mid greys are dithered."""
        img = img.convert("L")
        tw = self.px(w)
        th = int(img.height * tw / img.width)
        if h and th > self.px(h):
            th = self.px(h)
            tw = int(img.width * th / img.height)
        img = img.resize((max(1, tw), max(1, th)), Image.LANCZOS)
        img = img.point(lambda v: 0 if v < 70 else 255 if v > 215 else int((v - 70) * 255 / 145))
        img = img.convert("1").convert("L")
        x0 = self.px(x) - (img.width // 2 if anchor == "c" else 0)
        self.img.paste(img, (x0, self.px(y)))


def draw_elements(c, elements, fields):
    for el in elements:
        if el.get("if") and not str(fields.get(el["if"], "")).strip():
            continue
        t = el["t"]
        if t == "text":
            s = fill(el["s"], fields)
            if el.get("spaced"):
                s = "  ".join(s.split())
            if el.get("upper"):
                s = s.upper()
            if el.get("letterspace"):
                s = " ".join(s)
            w = el.get("w")
            if el.get("wpc"):
                w = len(s) * el["wpc"]
                if el.get("wmax"):
                    w = min(w, el["wmax"])
            c.text(s, el["x"], el["y"], el["h"], w=w, font=el.get("font", "cond"),
                   anchor=el.get("anchor", "l"), fit=el.get("fit"))
        elif t == "box":
            c.box(el["x0"], el["y0"], el["x1"], el["y1"], width=el.get("width", 4))
        elif t == "line":
            c.line(el["x0"], el["x1"], el["y"], width=el.get("width", 3))
        elif t == "barcode":
            c.barcode(fill(el["s"], fields).strip(), el["x"], el["y"], el["h"], el["w"],
                      anchor=el.get("anchor", "l"))
        else:
            raise ValueError(f"unknown element type {t}")


def sticker(tpl, fields, scale=1.0):
    w, h = tpl["size"]
    c = Canvas(w, h, scale)
    if tpl.get("frame"):
        c.d.rounded_rectangle((2, 2, c.img.width - 3, c.img.height - 3),
                              radius=c.px(tpl["frame"]), outline=0, width=4)
    draw_elements(c, tpl["elements"], fields)
    return c.img


def decode_logo(data):
    """A data: URL or raw bytes -> greyscale image trimmed to its ink."""
    if isinstance(data, str):
        if "," not in data:
            raise LabelError("logo must be a data: URL")
        import base64
        data = base64.b64decode(data.split(",", 1)[1])
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (OSError, Image.DecompressionBombError) as e:
        raise LabelError(f"logo isn't a usable image ({e.__class__.__name__})")
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, "white")
        img = Image.alpha_composite(bg, img)
    img = img.convert("L")
    box = ImageOps.invert(img).point(lambda v: 255 if v > 40 else 0).getbbox()
    return img.crop(box) if box else img


def has_info(info, logo):
    return (bool(logo) or info.get("_logo2") is not None
            or any(str(info.get(k, "")).strip() for k in ("title", "line2", "line3", "caption2")))


def info_card(info, logo=None, width=56):
    """Logo on top (optional), then a bold title and two smaller lines, centred.
    With a logo the sizes match the Navardi card; without one, the older 3-line strip.
    info["_logo2"] (an image) adds a second logo along the bottom, e.g. the tuner's,
    with info["caption2"] in small capitals above it ("TUNED BY")."""
    lines = [str(info.get(k, "")).strip() for k in ("title", "line2", "line3")]
    if logo is not None:   # (ink height, font, gap to the next line)
        style = [(3.2, "sansbold", 5.5), (2.45, "sansbold", 4.4), (1.65, "sans", 0)]
        y = 16.3
    else:
        style = [(4.2, "bold", 7.0), (3.1, "cond", 5.5), (3.1, "cond", 0)]
        y = 3.0
    rows, end = [], 15.4 if logo is not None else 3.0
    for s, (h, font, gap) in zip(lines, style):
        if s:
            rows.append((s, y, h, font))
            end = y + h
            y += gap
    logo2, cap = info.get("_logo2"), str(info.get("caption2", "")).strip()
    if cap:
        y = end + 2.4 if rows or logo is not None else 3.0
        rows.append((" ".join(cap.upper()), y, 1.5, "sansbold"))
        end = y + 1.5
    l2 = None
    if logo2 is not None:
        aspect = logo2.width / logo2.height
        lw = min(width * 0.5, 6.5 * aspect)
        y = end + (1.4 if cap else 2.4) if (rows or logo is not None) else 3.0
        l2 = (y, lw)
        end = y + lw / aspect
    c = Canvas(width, max(12, end + 2.6))
    c.d.rounded_rectangle((2, 2, c.img.width - 3, c.img.height - 3), radius=c.px(2.5), outline=0, width=4)
    if logo is not None:
        c.image(logo, width / 2, 1.9, w=width * 0.645, h=13.5, anchor="c")
    for s, y, h, font in rows:
        c.text(s, width / 2, y, h, font=font, anchor="c", fit=width - 6)
    if l2:
        c.image(logo2, width / 2, l2[0], w=l2[1], anchor="c")
    return c.img


def _cut(d, cy):
    f = ImageFont.truetype(os.path.join(FONT_DIR, FONTS["cond"]), 22)
    for x in range(0, W, 20):
        d.line((x, cy, min(x + 10, W), cy), fill=0, width=2)
    d.rectangle((W / 2 - 60, cy - 14, W / 2 + 60, cy + 14), fill=255)
    d.text((W / 2, cy), "cut here", font=f, fill=0, anchor="mm")


def _marks(d, y0, y1, w_px):
    a, b, k = (W - w_px) // 2, (W + w_px) // 2, round(3 * MM)
    for x in (a, b):
        for y in (y0, y1):
            if 0 < x < W:
                d.line((x, y - k, x, y + k), fill=0, width=2)
            d.line((x - k, y, x + k, y), fill=0, width=2)


def fitted(tpl, fields, fit):
    """The sticker scaled to fit inside fit = (w, h) mm (turned to match its own orientation),
    centred on a canvas exactly that size, so the cut marks give the space on the part."""
    fw, fh = (float(v) for v in fit)
    sw, sh = tpl["size"]
    if (sw >= sh) != (fw >= fh):
        fw, fh = fh, fw
    if not (10 <= fw <= 280 and 10 <= fh <= 280):
        raise LabelError("size on the part must be 10 to 280 mm each way")
    s = sticker(tpl, fields, min(fw / sw, fh / sh))
    canvas = Image.new("L", (round(fw * MM), round(fh * MM)), 255)
    canvas.paste(s, ((canvas.width - s.width) // 2, (canvas.height - s.height) // 2))
    return canvas


def pieces(tpl, fields, info=None, logo=None, scale=None, fit=None):
    """The sticker (rotated later to run along the label when it is too wide) and the info card."""
    if tpl.get("card_only"):
        if info is None or not has_info(info, logo):
            raise LabelError("fill in the info card in step 3")
        return [info_card(info, logo)]
    if fit:
        s = fitted(tpl, fields, fit)
    else:
        s = sticker(tpl, fields, tpl.get("scale", 1.0) if scale is None else scale)
    out = [s]
    if info is not None and has_info(info, logo):
        out.append(info_card(info, logo))
    return out


def dymo_label(tpl, fields, info=None, logo=None, fit=None):
    """One DYMO 99019 label (59 x 190 mm, 300 dpi), pieces separated by cut lines.
    fit = (w, h) mm scales the sticker to that space on the part, with cut marks at it.
    Returns (1-bit image, mm of label used)."""
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    y = round(2 * MM)
    for i, p in enumerate(pieces(tpl, fields, info, logo, fit=fit)):
        if p.width > W:
            p = p.rotate(90, expand=True)
        if p.width > W:
            raise LabelError("too big for a 59 mm DYMO label both ways round; use the A4 PDF")
        if y + p.height > H:
            raise LabelError("this doesn't fit on one 190 mm DYMO label; shorten the info card")
        img.paste(p, ((W - p.width) // 2, y))
        if i == 0 and (fit or tpl.get("marks")):
            _marks(d, y, y + p.height, p.width)
        y += p.height + round(2 * MM)
        _cut(d, y)
        y += round(2 * MM)
    if H - y > round(10 * MM):
        f = ImageFont.truetype(os.path.join(FONT_DIR, FONTS["cond"]), 26)
        d.text((W / 2, (y + H) / 2), "spare - trim off", font=f, fill=0, anchor="mm")
    return img.point(lambda v: 0 if v < 128 else 255).convert("1"), y / MM


def a4_sheet(tpl, fields, info=None, logo=None, copies=1, fit=None):
    """True-size stickers on A4 for sticker paper, each with a thin cut outline."""
    page = Image.new("L", (round(A4_W * MM), round(A4_H * MM)), 255)
    d = ImageDraw.Draw(page)
    margin, gap = round(10 * MM), round(6 * MM)
    x = y = margin
    row_h = 0
    parts = pieces(tpl, fields, info, logo, scale=1.0, fit=fit)
    for _ in range(max(1, min(copies, 20))):
        for p in parts:
            if x + p.width > page.width - margin:
                x, y, row_h = margin, y + row_h + gap, 0
            if y + p.height > page.height - margin:
                break
            page.paste(p, (x, y))
            d.rectangle((x - 12, y - 12, x + p.width + 11, y + p.height + 11), outline=150, width=1)
            x += p.width + gap
            row_h = max(row_h, p.height)
    return page.point(lambda v: 0 if v < 100 else 255 if v > 200 else v)


def to_png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", dpi=(DPI, DPI))
    return buf.getvalue()


def to_pdf(img):
    buf = io.BytesIO()
    img.convert("L").save(buf, "PDF", resolution=DPI)
    return buf.getvalue()
