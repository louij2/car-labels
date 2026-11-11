# car-labels

A small web portal for reprinting worn or damaged car part stickers. Pick the sticker,
type the numbers off the old one, and get a new one: a PNG or PDF for a DYMO LabelWriter
(59 × 190 mm 99019 labels), or an A4 PDF with the stickers at their real size for sticker
paper. On your own network it can also print straight to the DYMO and keep a library of
saved labels for reprints.

![icon](car_labels/static/icon.png)

## Templates

| Template | Sticker |
|---|---|
| `vag-engine-ecu` | VAG engine ECU plant sticker (Audi Brussels layout, Simos 18 and others) |
| `bmw-aisin-tcu` | BMW 8-speed auto TCU, the white Aisin AW sticker |
| `generic-part` | Maker, part number, description, serial + barcode, origin |

Every label can have an optional **info card**: a second sticker with a logo, a title
and two small lines (who tuned it, torque limit, original part numbers).

Barcodes are Code 128 of the number printed under them, which is what VAG and Aisin use.
The VAG one was checked bar for bar against a real sticker; check others before you rely
on a scanner reading them.

### Adding a template

One JSON file in `car_labels/templates/`: a size in mm, the fields the user fills in, and
elements (`text`, `box`, `line`, `barcode`) placed in mm with `{field}` placeholders. See
the docstring at the top of `car_labels/render.py`. `"wpc"` (mm per character) keeps a
value the length of the example exactly as wide as on the original sticker. Add a sample
to the tests, run them, open a PR.

## Run it

```sh
docker run -d --name car-labels -p 8710:8080 -v /path/to/data:/data louij2/car-labels
```

Then open http://localhost:8710. That gives you preview and downloads.

### Printing to a DYMO

Run a CUPS server with the DYMO queue (for example `olbat/cupsd`, which ships the DYMO
PPDs). Share its `/run/cups` with this container and name the queue:

```sh
docker run -d --name car-labels -p 8710:8080 --user 99:100 \
  -v /path/to/data:/data -v cups-sock:/run/cups \
  -e PRINTER=DYMO_LabelWriter_450 louij2/car-labels
```

Printing goes over the local socket because the default CUPS policy refuses to take the
document over TCP.

| Variable | Default | |
|---|---|---|
| `PRINTER` | empty | CUPS queue. Empty means download only. |
| `PRINT_OPTIONS` | `media=w167h539 scaling=100` | `lp -o` options. |
| `LAN_CIDRS` | RFC 1918 + 100.64/10 + loopback | Clients that may print and use the library. |
| `TRUSTED_PROXIES` | empty | Proxies whose `X-Forwarded-For` is honoured (e.g. your Caddy). |
| `DATA_DIR` | `/data` | `saved/` = library, `logos/` = shared logos (PNG). |

A request carrying Cloudflare headers never counts as LAN. If you publish the portal on
the internet, it's still safest to run a second container without `PRINTER` for that.

### Unraid

Template: [`unraid/car-labels.xml`](unraid/car-labels.xml).

## Develop

```sh
pip install -r requirements.txt pytest
python -m pytest -q
flask --app car_labels.app run --port 8710
```

Fonts are DejaVu (`fonts-dejavu-core` + `fonts-dejavu-extra`). Set `FONT_DIR` if yours
are somewhere else.

## Licence

MIT. For repairing labels on parts you own. Don't use it to pass a part off as something
it isn't.
