"""Car labels portal: pick a template, type the numbers off the damaged sticker, get a new one.

Everyone can preview and download (PNG for a DYMO, PDF for a DYMO or A4 sticker paper).
Requests from the LAN can also print straight to the DYMO and use the saved-label library.

Environment:
  PRINTER            CUPS queue name (empty = printing off)          e.g. DYMO_LabelWriter_450
  PRINT_OPTIONS      lp -o options, space separated                   default "media=w167h539 scaling=100"
  LAN_CIDRS          who counts as LAN (print + library)              default RFC1918 + CGNAT
  TRUSTED_PROXIES    reverse proxies whose X-Forwarded-For is honoured   default none
  DATA_DIR           saved labels (saved/) and shared logos (logos/)  default /data
A request carrying Cloudflare headers is never LAN, whatever address it comes from.
"""
import datetime
import ipaddress
import json
import os
import re
import subprocess
import tempfile

from flask import Flask, Response, abort, jsonify, request, send_from_directory

from . import __version__, render

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024

TEMPLATES = render.load_templates()
DATA_DIR = os.environ.get("DATA_DIR", "/data")
PRINTER = os.environ.get("PRINTER", "").strip()
PRINT_OPTIONS = os.environ.get("PRINT_OPTIONS", "media=w167h539 scaling=100").split()


def _nets(var, default):
    out = []
    for n in os.environ.get(var, default).replace(",", " ").split():
        out.append(ipaddress.ip_network(n, strict=False))
    return out


LAN = _nets("LAN_CIDRS", "10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 127.0.0.0/8 ::1/128 fc00::/7")
PROXIES = _nets("TRUSTED_PROXIES", "")


def _ip(s):
    try:
        return ipaddress.ip_address(s.strip())
    except ValueError:
        return None


def client_ip():
    peer = _ip(request.remote_addr or "")
    if peer is None:
        return None
    if any(peer in n for n in PROXIES):
        for hop in reversed(request.headers.get("X-Forwarded-For", "").split(",")):
            ip = _ip(hop)
            if ip is None:
                return None
            if not any(ip in n for n in PROXIES):
                return ip
    return peer


def is_lan():
    if request.headers.get("CF-Connecting-IP") or request.headers.get("CF-Ray"):
        return False
    ip = client_ip()
    return ip is not None and any(ip in n for n in LAN)


def lan_only():
    if not is_lan():
        abort(403, "only available on the local network")


@app.errorhandler(render.LabelError)
def label_error(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
@app.errorhandler(502)
def http_error(e):
    return jsonify(error=getattr(e, "description", str(e))), e.code


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": __version__, "templates": len(TEMPLATES)}


@app.get("/api/config")
def config():
    lan = is_lan()
    return {"version": __version__, "lan": lan, "can_print": lan and bool(PRINTER),
            "printer": PRINTER if lan else None,
            "templates": [{k: t[k] for k in ("id", "name", "make", "part", "description", "fields", "size")}
                          for t in TEMPLATES.values()]}


def _logo(spec):
    if not spec:
        return None
    if isinstance(spec, dict) and spec.get("library"):
        lan_only()
        name = os.path.basename(spec["library"])
        path = os.path.join(DATA_DIR, "logos", name + ".png")
        if not os.path.isfile(path):
            raise render.LabelError(f"no shared logo called {name}")
        with open(path, "rb") as f:
            return render.decode_logo(f.read())
    if isinstance(spec, str):
        return render.decode_logo(spec)
    raise render.LabelError("bad logo")


def _int(v, default=1):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise render.LabelError("copies must be a number")


def _job():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise render.LabelError("expected a JSON body")
    tpl = TEMPLATES.get(body.get("template"))
    if not tpl:
        raise render.LabelError("unknown template")
    known = {f["key"] for f in tpl["fields"]}
    fields = {k: str(v)[:80] for k, v in (body.get("fields") or {}).items() if k in known}
    info = {k: str(v)[:80] for k, v in (body.get("info") or {}).items() if k in ("title", "line2", "line3", "caption2")}
    fit = body.get("fit")
    if fit:
        try:
            fit = [float(fit[0]), float(fit[1])]
        except (TypeError, ValueError, IndexError, KeyError):
            raise render.LabelError("size on the part must be two numbers in mm")
    body["fit"] = fit or None
    logo2 = _logo(body.get("logo2"))
    if logo2 is not None:
        info["_logo2"] = logo2
    return tpl, fields, info, _logo(body.get("logo")), body


@app.post("/api/render")
def render_label():
    tpl, fields, info, logo, body = _job()
    fmt = body.get("format", "png")
    layout = body.get("layout", "dymo")
    if layout == "a4":
        img = render.a4_sheet(tpl, fields, info, logo, copies=_int(body.get("copies") or 1), fit=body["fit"])
    else:
        img, _ = render.dymo_label(tpl, fields, info, logo, fit=body["fit"])
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", body.get("filename") or tpl["id"]).strip("-") or "label"
    if fmt == "pdf":
        return Response(render.to_pdf(img), mimetype="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{name}-{layout}.pdf"'})
    return Response(render.to_png(img), mimetype="image/png",
                    headers={"Content-Disposition": f'inline; filename="{name}-{layout}.png"',
                             "Cache-Control": "no-store"})


@app.post("/api/print")
def print_label():
    lan_only()
    if not PRINTER:
        abort(400, "printing is not set up on this server")
    tpl, fields, info, logo, body = _job()
    copies = _int(body.get("copies") or 1)
    if not 1 <= copies <= 10:
        raise render.LabelError("copies must be 1 to 10")
    img, _ = render.dymo_label(tpl, fields, info, logo, fit=body["fit"])
    jobs = []
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        f.write(render.to_png(img))
        f.flush()
        cmd = ["lp", "-d", PRINTER]
        for o in PRINT_OPTIONS:
            cmd += ["-o", o]
        for _ in range(copies):
            r = subprocess.run(cmd + [f.name], capture_output=True, text=True, timeout=30)
            if r.returncode:
                abort(502, f"printer said: {(r.stderr or r.stdout).strip()}")
            jobs += re.findall(r"request id is (\S+)", r.stdout)
    app.logger.warning("printed %s x%d for %s: %s", tpl["id"], copies, client_ip(), " ".join(jobs))
    return {"jobs": jobs}


def _own(path):
    """Running as root (CUPS only lets root or the job's owner send the document over the
    socket), so hand files back to whoever owns DATA_DIR (99:100 on Unraid)."""
    try:
        st = os.stat(DATA_DIR)
        os.chown(path, st.st_uid, st.st_gid)
    except OSError:
        pass


def _saved_dir():
    d = os.path.join(DATA_DIR, "saved")
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        _own(d)
    return d


def _slug(s):
    s = re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:60]
    if not s:
        raise render.LabelError("give the label a name")
    return s


@app.get("/api/saved")
def saved_list():
    lan_only()
    out = []
    for name in sorted(os.listdir(_saved_dir())):
        if name.endswith(".json"):
            with open(os.path.join(_saved_dir(), name)) as f:
                e = json.load(f)
            out.append({"id": name[:-5], "name": e.get("name", name[:-5]),
                        "template": e.get("template"), "saved_at": e.get("saved_at")})
    out.sort(key=lambda e: e.get("saved_at") or "", reverse=True)
    return {"saved": out}


@app.get("/api/saved/<sid>")
def saved_get(sid):
    lan_only()
    path = os.path.join(_saved_dir(), _slug(sid) + ".json")
    if not os.path.isfile(path):
        abort(404, "no such saved label")
    with open(path) as f:
        return json.load(f)


@app.post("/api/saved")
def saved_put():
    lan_only()
    tpl, fields, info, _, body = _job()
    sid = _slug(body.get("name", ""))
    info.pop("_logo2", None)
    entry = {"name": str(body["name"])[:80], "template": tpl["id"], "fields": fields, "info": info,
             "logo": body.get("logo"), "logo2": body.get("logo2"), "fit": body["fit"], "notes": str(body.get("notes", ""))[:500],
             "saved_at": datetime.datetime.now().isoformat(timespec="seconds")}
    tmp = os.path.join(_saved_dir(), f".{sid}.tmp")
    with open(tmp, "w") as f:
        json.dump(entry, f, indent=1)
    os.replace(tmp, os.path.join(_saved_dir(), sid + ".json"))
    _own(os.path.join(_saved_dir(), sid + ".json"))
    return {"id": sid}


@app.delete("/api/saved/<sid>")
def saved_delete(sid):
    lan_only()
    path = os.path.join(_saved_dir(), _slug(sid) + ".json")
    if os.path.isfile(path):
        os.replace(path, path + ".deleted")    # kept on disk, just hidden
    return {"ok": True}


@app.get("/api/logos")
def logos():
    lan_only()
    d = os.path.join(DATA_DIR, "logos")
    names = sorted(n[:-4] for n in os.listdir(d) if n.endswith(".png")) if os.path.isdir(d) else []
    return {"logos": names}


@app.get("/api/logos/<name>.png")
def logo_file(name):
    lan_only()
    return send_from_directory(os.path.join(DATA_DIR, "logos"), os.path.basename(name) + ".png")
