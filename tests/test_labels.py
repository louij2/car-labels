import base64
import io
import math

import pytest
from PIL import Image

from car_labels import app as app_mod, cable, render

ALL = render.load_templates()
TEMPLATES = {k: t for k, t in ALL.items() if t.get("kind") != "cable"}
CABLES = {k: t for k, t in ALL.items() if t.get("kind") == "cable"}


def example(t):
    return {f["key"]: f["example"] for f in t["fields"]}


def test_code128_known_value():
    # Luca's S1 ECU sticker was decoded bar for bar against this encoder (set C).
    bars = render.code128("46201544195434")
    assert bars.startswith("211232") and bars.endswith("2331112")
    assert sum(map(int, bars)) == 11 * 9 + 13


def test_code128_rejects_non_ascii():
    with pytest.raises(render.LabelError):
        render.code128("ÄBC")


@pytest.mark.parametrize("tid", sorted(TEMPLATES))
def test_every_template_renders(tid):
    t = TEMPLATES[tid]
    img, used = render.dymo_label(t, example(t), {"title": "TEST", "line2": "two", "line3": "three"})
    assert img.size == (render.W, render.H) and used < render.LABEL_H
    assert render.to_pdf(render.a4_sheet(t, example(t), {"title": "TEST"}, copies=3))[:4] == b"%PDF"


def test_empty_fields_still_render():
    for t in TEMPLATES.values():
        if t.get("card_only"):
            with pytest.raises(render.LabelError):
                render.dymo_label(t, {})
        else:
            render.dymo_label(t, {})


def test_logo_card():
    logo = Image.new("RGB", (300, 100), "white")
    logo.paste((0, 0, 0), (20, 20, 280, 80))
    buf = io.BytesIO(); logo.save(buf, "PNG")
    url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    t = TEMPLATES["bmw-aisin-tcu"]
    card = render.info_card({"title": "M135i - TCU"}, render.decode_logo(url))
    assert card.width == round(56 * render.MM)
    render.dymo_label(t, example(t), {"title": "x"}, render.decode_logo(url))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_mod, "PRINTER", "")
    return app_mod.app.test_client()


def test_lan_and_public(client):
    lan = client.get("/api/config", environ_base={"REMOTE_ADDR": "10.0.0.50"}).json
    pub = client.get("/api/config", environ_base={"REMOTE_ADDR": "81.2.3.4"}).json
    cf = client.get("/api/config", environ_base={"REMOTE_ADDR": "10.0.0.50"},
                    headers={"CF-Connecting-IP": "81.2.3.4"}).json
    assert lan["lan"] and not pub["lan"] and not cf["lan"]
    assert client.get("/api/saved", environ_base={"REMOTE_ADDR": "81.2.3.4"}).status_code == 403
    assert client.post("/api/print", json={}, environ_base={"REMOTE_ADDR": "81.2.3.4"}).status_code == 403


def test_trusted_proxy(client, monkeypatch):
    import ipaddress
    monkeypatch.setattr(app_mod, "PROXIES", [ipaddress.ip_network("10.0.0.2/32")])
    via = lambda xff: client.get("/api/config", environ_base={"REMOTE_ADDR": "10.0.0.2"},
                                 headers={"X-Forwarded-For": xff}).json["lan"]
    assert via("10.0.0.77") is True
    assert via("81.2.3.4") is False
    assert via("10.0.0.9, 81.2.3.4") is False      # client-supplied hop on the left is ignored


def test_render_and_library(client):
    t = TEMPLATES["generic-part"]
    lan = {"REMOTE_ADDR": "192.168.1.5"}
    r = client.post("/api/render", json={"template": t["id"], "fields": example(t)})
    assert r.status_code == 200 and r.data[:4] == b"\x89PNG"
    r = client.post("/api/render", json={"template": t["id"], "format": "pdf", "layout": "a4"})
    assert r.data[:4] == b"%PDF"
    assert client.post("/api/render", json={"template": "nope"}).status_code == 400
    assert client.post("/api/saved", json={"template": t["id"], "name": "Dan's car!"}, environ_base=lan).json == {"id": "dan-s-car"}
    assert client.get("/api/saved", environ_base=lan).json["saved"][0]["name"] == "Dan's car!"
    assert client.get("/api/saved/dan-s-car", environ_base=lan).json["template"] == t["id"]
    client.delete("/api/saved/dan-s-car", environ_base=lan)
    assert client.get("/api/saved", environ_base=lan).json["saved"] == []


def test_fit_to_space_on_the_part():
    t = TEMPLATES["vag-engine-ecu"]
    s = render.fitted(t, example(t), (50, 100))          # given either way round
    assert (round(s.width / render.MM), round(s.height / render.MM)) == (100, 50)
    img, used = render.dymo_label(t, example(t), fit=(100, 50))
    assert 100 < used < 110
    with pytest.raises(render.LabelError):
        render.dymo_label(t, example(t), fit=(120, 80))  # wider than the label both ways
    assert render.to_pdf(render.a4_sheet(t, example(t), fit=(100, 50)))[:4] == b"%PDF"


def test_second_logo_and_caption():
    logo = Image.new("L", (400, 100), 255)
    logo.paste(0, (10, 10, 390, 90))
    plain = render.info_card({"title": "S1 - ECU"}, logo)
    both = render.info_card({"title": "S1 - ECU", "caption2": "tuned by", "_logo2": logo}, logo)
    assert both.height > plain.height and both.width == plain.width
    only2 = render.info_card({"_logo2": logo})
    assert only2.height >= round(12 * render.MM)
    t = TEMPLATES["vag-engine-ecu"]
    render.dymo_label(t, example(t), {"title": "x", "caption2": "TUNED BY", "_logo2": logo}, logo, fit=(100, 50))


@pytest.mark.parametrize("tid", sorted(CABLES))
def test_every_cable_template_renders(tid):
    t = CABLES[tid]
    pages = cable.dymo_labels(t, example(t))
    assert pages and all(p.size == (render.W, render.H) for p in pages)
    assert render.to_pdf_pages(cable.a4_pages(t, example(t)))[:4] == b"%PDF"
    with pytest.raises(render.LabelError):
        cable.dymo_labels(t, {"names": "  \n "})


def test_hdmi_flag_matches_the_printed_design():
    t = CABLES["hdmi-flag"]
    f = cable.flag("MAC", 54, 19, 7.5)
    assert round(f.height / render.MM) == 62 and round(f.width / render.MM) == 54   # 19 + 24 + 19
    assert len(cable.dymo_labels(t, {"names": "A\nB\nC"})) == 1                    # 3 per label, as before
    assert len(cable.dymo_labels(t, {"names": "A\nB\nC\nD"})) == 2


def test_wrap_covers_the_cable_and_repeats():
    w = cable.wrap("NAS", 26, 6)
    assert w.height / render.MM >= math.pi * 6 * 1.4                               # ~1.5 turns of Cat6
    assert round(w.width / render.MM) == 26


def test_cable_wrap_names_the_other_end():
    t = CABLES["cable-wrap"]
    assert len(cable.pieces(t, {"names": "GARAGE / LIVING ROOM\nNAS", "each": "2"})) == 6
    a = cable.pieces(t, {"names": "GARAGE / LIVING ROOM"})
    b = cable.pieces(t, {"names": "LIVING ROOM"})
    assert a[0].tobytes() == b[0].tobytes()                                         # first band = the far end
    assert len(cable.dymo_labels(t, {"names": "\n".join(f"R{i} / S{i}" for i in range(6))})) == 1


def test_cable_render_api(client):
    r = client.post("/api/render", json={"template": "cable-wrap", "fields": {"names": "\n".join(f"P{i} / Q{i}" for i in range(20))}})
    assert r.status_code == 200 and int(r.headers["X-Labels"]) > 1
    r = client.post("/api/render", json={"template": "cable-wrap", "format": "pdf", "fields": {"names": "A / B"}})
    assert r.data[:4] == b"%PDF"
    assert client.post("/api/render", json={"template": "cable-wrap", "fields": {}}).status_code == 400
