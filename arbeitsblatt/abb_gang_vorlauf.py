#!/usr/bin/env python3
"""Hilfsabbildung zum Arbeitsblatt: Gang und Vorlauf der Satellitenuhr E14.

Berechnet aus den Broadcast-Bahndaten (ohne Beobachtungsdaten):
  oben:   Abstand zum Erdmittelpunkt
  Mitte:  Gang der Uhr (ns pro Stunde) relativ zum mittleren Gang
  unten:  Vorlauf (ns) = aufsummierter Gang, wie im Hauptplot
und eine Vorlage fuer Aufgabe 3 (Vorlauf oben, leeres Gang-Diagramm unten).

Aufruf (im Hauptverzeichnis des Repositorys):
  python arbeitsblatt/abb_gang_vorlauf.py
"""
import math
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HIER))
from gnss_relativitaet import read_nav, EphPicker, sat_state, MU  # noqa: E402

NAV = os.path.join(os.path.dirname(HIER), "BRDM00DLR_S_20262630000_01D_MN.rnx")
SAT = "E14"

pick = EphPicker(read_nav(NAV), galileo_fnav=True)
lst, keys = pick.tab[SAT]
day0 = math.floor(keys[len(keys) // 2] / 86400) * 86400

T, rel, rad = [], [], []
for t in np.arange(day0, day0 + 86400 + 1, 60.0):
    e = pick(SAT, t)
    if e is None:
        continue
    pos, _, r = sat_state(e, t, MU["E"])
    T.append(t)
    rel.append(r * 1e9)
    rad.append(np.linalg.norm(pos) / 1e6)
T, rel, rad = map(np.array, (T, rel, rad))
h = (T - day0) / 3600.0
gang = np.gradient(rel, h)          # ns pro Stunde

i_min = [i for i in range(1, len(rad) - 1) if rad[i] < rad[i - 1] and rad[i] <= rad[i + 1]]
i_max = [i for i in range(1, len(rad) - 1) if rad[i] > rad[i - 1] and rad[i] >= rad[i + 1]]
print("Perigaeum bei", [f"{h[i]:.2f} h" for i in i_min], " Apogaeum bei", [f"{h[i]:.2f} h" for i in i_max])
print(f"Gang: min {gang.min():.0f} ns/h, max {gang.max():.0f} ns/h;  Vorlauf: +-{np.ptp(rel) / 2:.0f} ns")

fig, (a1, a2, a3) = plt.subplots(3, 1, sharex=True, figsize=(10, 8.5),
                                 gridspec_kw={"height_ratios": [1, 1.4, 1.4]})
a1.plot(h, rad, color="tab:blue", lw=2)
a1.set_ylabel("Abstand zum\nErdmittelpunkt\n(1000 km)")
a2.plot(h, gang, color="tab:green", lw=2)
a2.fill_between(h, gang, 0, where=gang > 0, color="tab:green", alpha=0.15)
a2.fill_between(h, gang, 0, where=gang < 0, color="tab:red", alpha=0.15)
a2.axhline(0, color="k", lw=0.8)
a2.set_ylabel("Gang der Uhr\n(ns pro Stunde)")
a2.text(7.5, 140, "Uhr geht schneller als im Mittel", color="tab:green", ha="center", va="center", fontsize=9)
a2.text(13.0, -190, "Uhr geht langsamer als im Mittel", color="tab:red", ha="center", va="center", fontsize=9)
a3.plot(h, rel, color="tab:red", lw=2)
a3.axhline(0, color="k", lw=0.8)
a3.set_ylabel("Vorlauf der\nSatellitenuhr (ns)")
a3.set_xlabel("Uhrzeit (GPS-Zeit) in h")
for ax in (a1, a2, a3):
    ax.grid(alpha=0.3)
    for i in i_min:
        ax.axvline(h[i], color="0.4", ls="--", lw=1)
    for i in i_max:
        ax.axvline(h[i], color="0.4", ls=":", lw=1.2)
for i in i_min:
    a1.annotate("Perigäum", (h[i], rad[i]), xytext=(0, 8), textcoords="offset points", ha="center", va="bottom")
for i in i_max:
    a1.annotate("Apogäum", (h[i], rad[i]), xytext=(0, -10), textcoords="offset points", ha="center", va="top")
a1.set_ylim(rad.min() - 2, rad.max() + 2)
fig.suptitle(f"Satellit {SAT}, 20.09.2026: Gang (Mitte) und Vorlauf (unten) der Uhr\n"
             "Vorlauf = aufsummierter Gang  ·  gestrichelt: Perigäum, gepunktet: Apogäum")
fig.tight_layout()
out = os.path.join(HIER, "abb_gang_vorlauf.png")
fig.savefig(out, dpi=130)
print("gespeichert:", out)

# Vorlage fuer Aufgabe 3: Vorlauf vorgegeben, Gang selbst skizzieren
fig, (b1, b2) = plt.subplots(2, 1, sharex=True, figsize=(10, 7))
b1.plot(h, rel, color="tab:red", lw=2)
b1.axhline(0, color="k", lw=0.8)
b1.set_ylabel("Vorlauf der\nSatellitenuhr (ns)")
b2.axhline(0, color="k", lw=1.2)
b2.set_ylim(-260, 260)
b2.set_yticks(np.arange(-250, 251, 50))
b2.set_ylabel("Gang der Uhr\n(ns pro Stunde)")
b2.set_xlabel("Uhrzeit (GPS-Zeit) in h")
b2.text(0.5, 0.97, "hier den Gang skizzieren", transform=b2.transAxes, ha="center", va="top", color="0.5")
for ax in (b1, b2):
    ax.grid(alpha=0.4)
    ax.set_xticks(np.arange(0, 25, 1), minor=True)
    ax.grid(which="minor", alpha=0.15)
fig.suptitle(f"Vorlage zu Aufgabe 3: Satellit {SAT}, 20.09.2026")
fig.tight_layout()
out = os.path.join(HIER, "vorlage_aufgabe3.png")
fig.savefig(out, dpi=130)
print("gespeichert:", out)
