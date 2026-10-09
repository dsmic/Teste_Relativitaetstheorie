#!/usr/bin/env python3
"""Erzeugt arbeitsblatt.pdf und loesung.pdf aus den Markdown-Dateien.

Braucht das Paket `markdown` und Chromium (oder Google Chrome).
Aufruf (im Ordner arbeitsblatt oder im Hauptverzeichnis):
  python arbeitsblatt/pdf_erzeugen.py [--chrome PFAD]
"""
import argparse
import os
import re
import shutil
import subprocess
import tempfile

import markdown

HIER = os.path.dirname(os.path.abspath(__file__))

CSS = """
@page { size: A4; margin: 16mm 16mm 18mm 16mm; }
body { font-family: "DejaVu Sans", Arial, sans-serif; font-size: 10.5pt; line-height: 1.4; color: #000; }
h1 { font-size: 17pt; margin: 0 0 6pt 0; }
h2 { font-size: 13pt; margin: 14pt 0 4pt 0; border-bottom: 1px solid #888; padding-bottom: 2pt;
     break-after: avoid; }
p { margin: 4pt 0; }
img { max-width: 80%; display: block; margin: 6pt auto; }
table { border-collapse: collapse; margin: 6pt 0; width: 100%; break-inside: avoid; }
th, td { border: 1px solid #666; padding: 4pt 6pt; text-align: left; vertical-align: top; }
th { background: #eee; }
hr { border: 0; margin: 4pt 0; }
blockquote { border: 1px solid #666; margin: 8pt 0; padding: 4pt 10pt; line-height: 2.0; }
code, pre { font-family: "DejaVu Sans Mono", monospace; font-size: 9pt; }
pre { background: #f4f4f4; padding: 6pt; }
.luecke { display: inline-block; min-width: 2.5cm; border-bottom: 1px solid #000; }
.zeilen { margin: 2pt 0 8pt 0; }
.zeilen div { border-bottom: 1px solid #aaa; height: 0.75cm; }
.neueseite { break-before: page; }
.aufgabe { break-inside: avoid; }
"""


def vorbereiten(text, schreibzeilen):
    """Lueckenlinien und Schreibzeilen fuer den Ausdruck einfuegen."""
    zeilen = []
    for z in text.splitlines():
        if "___" in z:
            # jede Lueckenzeile als eigener Absatz, Unterstriche als Linie
            z = re.sub(r"_{3,}", lambda m: f'<span class="luecke" style="min-width:{len(m.group()) * 0.17:.1f}cm"></span>', z)
            zeilen += ["", z.strip(), ""]
        else:
            zeilen.append(z)
    text = "\n".join(zeilen)
    if not schreibzeilen:
        return text
    # nach Teilaufgaben ohne Luecke und ohne Tabelle: Platz zum Schreiben
    absaetze = re.split(r"\n\s*\n", text)
    aus = []
    for i, a in enumerate(absaetze):
        aus.append(a)
        frage = re.match(r"\s*([a-e]\)|\*\*Z\d\*\*)", a)
        naechster = absaetze[i + 1].lstrip() if i + 1 < len(absaetze) else ""
        if frage and "luecke" not in a and not naechster.startswith(("|", "!", "Tipp", "<span", "r_", "Perig")):
            n = 2 if a.strip().startswith(("a)", "b)")) and len(a) < 120 else 3
            aus.append('<div class="zeilen">' + "<div></div>" * n + "</div>")
    return "\n\n".join(aus)


def html(md_datei, schreibzeilen):
    with open(md_datei, encoding="utf-8") as f:
        text = f.read()
    text = vorbereiten(text, schreibzeilen)
    # Seitenumbruch vor Aufgabe 3 (die Vorlage braucht Platz)
    if schreibzeilen:
        for kopf in ("## Aufgabe 3",):
            text = text.replace(kopf, '<div class="neueseite"></div>\n\n' + kopf, 1)
    body = markdown.markdown(text, extensions=["tables"])
    body = body.replace("<hr />", "")
    return f'<!doctype html><html lang="de"><head><meta charset="utf-8"><style>{CSS}</style></head><body>{body}</body></html>'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chrome", help="Pfad zu Chromium/Chrome")
    args = ap.parse_args()
    chrome = args.chrome or next((p for p in (
        "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
        shutil.which("chromium"), shutil.which("chromium-browser"), shutil.which("google-chrome"))
        if p and os.path.exists(p)), None)
    if not chrome:
        raise SystemExit("Chromium nicht gefunden, bitte --chrome angeben.")
    for name, zeilen in (("arbeitsblatt", True), ("loesung", False)):
        # HTML im selben Ordner, damit die relativen Bildpfade stimmen
        fd, tmp = tempfile.mkstemp(suffix=".html", dir=HIER)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html(os.path.join(HIER, name + ".md"), zeilen))
        out = os.path.join(HIER, name + ".pdf")
        try:
            subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu",
                            "--no-pdf-header-footer", f"--print-to-pdf={out}", "file://" + tmp],
                           check=True, capture_output=True)
        finally:
            os.remove(tmp)
        print("gespeichert:", out)


if __name__ == "__main__":
    main()
