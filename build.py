#!/usr/bin/env python3
"""
Build del sitio de Equipos MR  —  equiposmr.com.co

    python build.py

Se corre desde la raiz del repo. Toma el fuente y las fotos de sitio/ y escribe
el sitio publicable en la raiz, que es lo que GitHub Pages sirve:

    sitio/index.src.html  ─┐
    sitio/img/*           ─┴─►  index.html + img/*

sitio/ tambien queda dentro del repo para que los fuentes esten respaldados y
cualquiera pueda reconstruir el sitio. Pages lo sirve igual que todo lo demas
(hay un .nojekyll), asi que robots.txt lo bloquea para que no se indexe como
copia duplicada de la portada.

Antes esto se hacia a mano embebiendo cada foto en base64 dentro del HTML,
lo que dejaba un index.html de 897 KB que el navegador tenia que descargar
completo antes de pintar el primer pixel. Ahora las fotos salen como archivos
aparte: el HTML queda en ~30 KB, el navegador las pide en paralelo, las cachea
por separado y sin base64 no hay 33 % de inflado.

Que hace, en orden:
  1. Optimiza cada foto de sitio/img/ hacia deploy/img/ (redimensiona si supera
     el ancho objetivo, recomprime progresivo, borra metadatos EXIF).
  2. Genera deploy/img/favicon.png a partir del logo.
  3. Reescribe cada <img>: prefija img/, le pone width/height reales y
     loading="lazy" a todo lo que no se ve al abrir la pagina.
  4. Inyecta el preload del hero en el <head>.

No toca CNAME, .nojekyll, robots.txt ni sitemap.xml — solo avisa si faltan.

Requiere Pillow:  pip install Pillow
"""

from __future__ import annotations

import io
import re
import shutil
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("Falta Pillow.  Instalalo con:  pip install Pillow")

# build.py vive en la raiz del repo, que es tambien lo que GitHub Pages publica:
# los fuentes quedan en sitio/ (bloqueado en robots.txt) y la salida en la raiz.
ROOT = Path(__file__).resolve().parent
SRC_HTML = ROOT / "sitio" / "index.src.html"
SRC_IMG = ROOT / "sitio" / "img"
OUT = ROOT
OUT_IMG = OUT / "img"

# Ancho maximo y calidad por imagen. El ancho objetivo es ~2x el tamano con el
# que se muestra, para pantallas retina; nunca se agranda una foto que ya venga
# mas chica que su objetivo.
TARGETS: dict[str, tuple[int, int]] = {
    "hero.jpg": (1920, 82),           # full-bleed, arriba de todo
    "band-andamios.jpg": (1400, 82),  # franja full-bleed
    "equipo.jpg": (900, 84),          # columna de ~660 px
    "logo.png": (321, 0),             # PNG, se deja tal cual
}
DEFAULT_TARGET = (620, 82)            # tarjetas de servicio: se muestran a ~306 px

# Las primeras imagenes del documento se ven sin hacer scroll, asi que se
# cargan con prioridad; el resto va con loading="lazy".
EAGER_COUNT = 2                       # logo del nav + hero
HERO = "hero.jpg"
FAVICON_PX = 64

# Archivos que el build no genera pero que el sitio necesita en deploy/.
PASSTHROUGH = ["CNAME", ".nojekyll", "robots.txt", "sitemap.xml"]

IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
ATTR_SRC = re.compile(r"""\bsrc\s*=\s*["']([^"']+)["']""", re.I)
# atributos que este build controla: se limpian antes de volver a ponerlos,
# asi correr el build dos veces da el mismo resultado
MANAGED = re.compile(
    r"""\s+(?:width|height|loading|decoding|fetchpriority)\s*=\s*["'][^"']*["']""", re.I
)


def human(n: int) -> str:
    return f"{n / 1024:,.0f} KB" if n < 1024 * 1024 else f"{n / 1048576:,.2f} MB"


def optimize(src: Path, dst: Path) -> tuple[int, int, int, int]:
    """Escribe la version optimizada. Devuelve (w, h, bytes_antes, bytes_despues)."""
    target_w, quality = TARGETS.get(src.name, DEFAULT_TARGET)
    before = src.stat().st_size
    im = Image.open(src)

    if im.width > target_w:
        h = round(im.height * target_w / im.width)
        im = im.resize((target_w, h), Image.LANCZOS)

    buf = io.BytesIO()
    if src.suffix.lower() == ".png":
        im.save(buf, "PNG", optimize=True)
    else:
        im.convert("RGB").save(
            buf, "JPEG", quality=quality, optimize=True, progressive=True
        )

    data = buf.getvalue()
    # si recomprimir no ayuda (ya venia bien optimizada y no hubo resize),
    # se copia el original y no se pierde calidad a cambio de nada
    if len(data) >= before and im.size == Image.open(src).size:
        dst.write_bytes(src.read_bytes())
        return im.width, im.height, before, before

    dst.write_bytes(data)
    return im.width, im.height, before, len(data)


def build_favicon(logo: Path, dst: Path) -> None:
    im = Image.open(logo).convert("RGBA")
    side = max(im.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
    square.resize((FAVICON_PX, FAVICON_PX), Image.LANCZOS).save(dst, "PNG", optimize=True)


def rewrite_html(html: str, dims: dict[str, tuple[int, int]]) -> tuple[str, int, int]:
    """Prefija img/, agrega dimensiones y lazy loading. Devuelve (html, n_img, n_lazy)."""
    seen = 0
    lazy = 0

    def one(m: re.Match[str]) -> str:
        nonlocal seen, lazy
        tag = MANAGED.sub("", m.group(0))
        sm = ATTR_SRC.search(tag)
        if not sm:
            return m.group(0)

        name = sm.group(1)
        if name not in dims:
            raise SystemExit(
                f"El HTML pide '{name}' pero no existe en {SRC_IMG.relative_to(ROOT)}/"
            )

        w, h = dims[name]
        tag = tag[: sm.start(1)] + f"img/{name}" + tag[sm.end(1) :]

        extra = f' width="{w}" height="{h}"'
        if seen >= EAGER_COUNT:
            extra += ' loading="lazy" decoding="async"'
            lazy += 1
        elif name == HERO:
            extra += ' fetchpriority="high"'

        seen += 1
        return tag[:-1].rstrip() + extra + ">"

    return IMG_TAG.sub(one, html), seen, lazy


def main() -> int:
    if not SRC_HTML.exists():
        sys.exit(f"No encuentro {SRC_HTML.relative_to(ROOT)}")
    if not SRC_IMG.is_dir():
        sys.exit(f"No encuentro {SRC_IMG.relative_to(ROOT)}/")

    OUT_IMG.mkdir(parents=True, exist_ok=True)

    # ---- 1. imagenes ----
    print(f"imagenes  {SRC_IMG.relative_to(ROOT)}/  ->  {OUT_IMG.relative_to(ROOT)}/")
    dims: dict[str, tuple[int, int]] = {}
    total_before = total_after = 0
    sources = sorted(
        p for p in SRC_IMG.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    if not sources:
        sys.exit(f"No hay imagenes en {SRC_IMG.relative_to(ROOT)}/")

    for p in sources:
        w, h, before, after = optimize(p, OUT_IMG / p.name)
        dims[p.name] = (w, h)
        total_before += before
        total_after += after
        delta = f"-{100 * (before - after) / before:.0f}%" if after < before else "="
        print(f"   {p.name:<22} {w:>5}x{h:<5} {human(after):>9}  {delta:>5}")

    logo = SRC_IMG / "logo.png"
    if logo.exists():
        build_favicon(logo, OUT_IMG / "favicon.png")
        print(f"   {'favicon.png':<22} {FAVICON_PX:>5}x{FAVICON_PX:<5} "
              f"{human((OUT_IMG / 'favicon.png').stat().st_size):>9}   nuevo")

    stale = {
        p.name for p in OUT_IMG.iterdir() if p.is_file()
    } - {p.name for p in sources} - {"favicon.png"}
    for name in sorted(stale):
        print(f"   aviso: deploy/img/{name} ya no se usa (borralo a mano si sobra)")

    # ---- 2. html ----
    html = SRC_HTML.read_text(encoding="utf-8")
    html, n_img, n_lazy = rewrite_html(html, dims)

    preload = (
        f'<link rel="preload" as="image" href="img/{HERO}" fetchpriority="high">'
    )
    if preload not in html:
        anchor = '<meta name="viewport" content="width=device-width, initial-scale=1">'
        if anchor not in html:
            sys.exit("No encuentro el <meta viewport> para anclar el preload del hero")
        html = html.replace(anchor, anchor + "\n" + preload, 1)

    out_html = OUT / "index.html"
    prev = out_html.stat().st_size if out_html.exists() else 0
    # newline="\n" a proposito: en Windows Python escribiria CRLF y el archivo
    # saldria distinto que en Linux o macOS. Asi el build es determinista.
    out_html.write_text(html, encoding="utf-8", newline="\n")

    print(f"\nhtml      {SRC_HTML.relative_to(ROOT)}  ->  {out_html.relative_to(ROOT)}")
    print(f"   {n_img} imagenes reescritas, {n_lazy} con loading=\"lazy\"")
    print(f"   {human(prev)} -> {human(len(html.encode('utf-8')))}")

    # ---- 3. lo que el build no genera ----
    missing = [f for f in PASSTHROUGH if not (OUT / f).exists()]
    if missing:
        print("\n   aviso: faltan en deploy/ -> " + ", ".join(missing))

    print(
        f"\nlisto.  HTML {human(len(html.encode('utf-8')))} + "
        f"imagenes {human(total_after)}"
    )
    print("Revisa index.html en el navegador antes de hacer commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
