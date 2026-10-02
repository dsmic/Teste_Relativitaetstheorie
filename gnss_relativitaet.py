#!/usr/bin/env python3
"""
Relativistischer Gang einer GNSS-Satellitenuhr aus Rohdaten (RINEX 3).

Zeigt den periodischen Effekt der Bahnexzentrizitaet
    dt_rel = F * e * sqrt(A) * sin(E),   F = -2*sqrt(mu)/c^2
fuer EINEN Satelliten, gemessen an EINER Station.

Vorgehen (alles hier im Skript, keine externe GNSS-Software):
  1. Pseudoranges (ionosphaerenfreie Zweifrequenz-Kombination) aus der Beobachtungsdatei
  2. Satellitenposition und -uhr aus den Broadcast-Ephemeriden (IS-GPS-200 / Galileo OS-SIS-ICD)
  3. Stationskoordinate fest (Header oder --xyz), einfaches Troposphaerenmodell
  4. Empfaengeruhr pro Epoche aus allen ANDEREN Satelliten desselben Systems (Median)
  5. Zielsatellit: Residuum OHNE Relativitaetsterm -> gemessener Uhrstand
     Vergleich mit dem berechneten Term (Theorie) und Residuum MIT Term (Kontrolle)

Benoetigt: numpy, matplotlib. Unterstuetzt GPS (G) und Galileo (E).

Beispiel:
  python gnss_relativitaet.py PTBB00DEU_R_20240150000_01D_30S_MO.rnx \
         BRDC00WRD_S_20240150000_01D_MN.rnx E14

Hatanaka-komprimierte Dateien (.crx) vorher entpacken:
  pip install hatanaka
  python -c "import hatanaka; hatanaka.decompress_on_disk('DATEI.crx.gz')"
"""
import argparse
import math
import os
import sys
from bisect import bisect_left
from datetime import datetime, timedelta

import numpy as np
import matplotlib.pyplot as plt

C = 299792458.0
OMEGA_E = 7.2921151467e-5
MU = {"G": 3.986005e14, "E": 3.986004418e14}
GPS_EPOCH = datetime(1980, 1, 6)
WEEK = 604800.0

# Frequenzen und bevorzugte Codes (erste vorhandene wird genommen)
FREQ = {"G": (1575.42e6, 1227.60e6), "E": (1575.42e6, 1176.45e6)}
CODES = {"G": (["C1W", "C1C", "C1P"], ["C2W", "C2L", "C2X", "C2P"]),
         "E": (["C1C", "C1X"], ["C5Q", "C5X", "C5I"])}


def gps_seconds(y, mo, d, h, mi, s):
    """Kalenderdatum (GPS-Zeitskala) -> Sekunden seit GPS-Epoche."""
    dt = datetime(y, mo, d, h, mi) - GPS_EPOCH
    return dt.days * 86400.0 + dt.seconds + s


def fnum(s):
    s = s.strip().replace("D", "E").replace("d", "e")
    return float(s) if s else float("nan")


# ----------------------------------------------------------------------------
# RINEX-3-Navigationsdatei
# ----------------------------------------------------------------------------
NAV_KEYS = ["af0", "af1", "af2",
            "iode", "Crs", "dn", "M0",
            "Cuc", "e", "Cus", "sqrtA",
            "toe", "Cic", "Omega0", "Cis",
            "i0", "Crc", "omega", "Omegadot",
            "idot", "src", "week", "spare",
            "acc", "health", "tgd", "x26",
            "ttm", "fit"]


def read_nav(path):
    with open(path, errors="replace") as f:
        lines = f.readlines()
    version, i = None, 0
    while i < len(lines):
        line = lines[i]
        i += 1
        label = line[60:].strip()
        if label == "RINEX VERSION / TYPE":
            version = float(line[:9])
        if label == "END OF HEADER":
            break
    if version is None or not (3.0 <= version < 4.0):
        sys.exit(f"Navigationsdatei: RINEX-Version {version} wird nicht unterstuetzt (nur 3.x).")

    n_extra = {"G": 7, "E": 7, "C": 7, "J": 7, "I": 7, "R": 3, "S": 3}
    ephs = {}
    while i < len(lines):
        line = lines[i]
        sysc = line[:1]
        n = n_extra.get(sysc)
        if n is None:
            i += 1
            continue
        block = lines[i:i + 1 + n]
        i += 1 + n
        if sysc not in ("G", "E"):
            continue
        first = block[0].rstrip("\n").ljust(80)
        sv = first[0:3].replace(" ", "0")
        toc = gps_seconds(int(first[4:8]), int(first[9:11]), int(first[12:14]),
                          int(first[15:17]), int(first[18:20]), float(first[21:23]))
        vals = [fnum(first[23:42]), fnum(first[42:61]), fnum(first[61:80])]
        for b in block[1:]:
            b = b.rstrip("\n").ljust(80)
            vals += [fnum(b[4:23]), fnum(b[23:42]), fnum(b[42:61]), fnum(b[61:80])]
        eph = dict(zip(NAV_KEYS, vals))
        eph["toc"] = toc
        # toe absolut: Woche aus toc ableiten (robust gegen unterschiedliche Wochenzaehlung)
        toe_abs = math.floor(toc / WEEK) * WEEK + eph["toe"]
        if toe_abs - toc > WEEK / 2:
            toe_abs -= WEEK
        elif toc - toe_abs > WEEK / 2:
            toe_abs += WEEK
        eph["toe_abs"] = toe_abs
        ephs.setdefault(sv, []).append(eph)
    for sv in ephs:
        ephs[sv].sort(key=lambda e: e["toe_abs"])
    return ephs


class EphPicker:
    """Waehlt die zeitlich naechste Ephemeride (max. 4 h Abstand)."""

    def __init__(self, ephs, galileo_fnav):
        self.tab = {}
        for sv, lst in ephs.items():
            if sv[0] == "E":
                mask = 256 if galileo_fnav else 512   # Uhrparameter fuer E5a/E1 bzw. E5b/E1
                sel = [e for e in lst if not math.isnan(e["src"]) and int(e["src"]) & mask]
                lst = sel or lst
            self.tab[sv] = (lst, [e["toe_abs"] for e in lst])

    def __call__(self, sv, t):
        if sv not in self.tab:
            return None
        lst, keys = self.tab[sv]
        k = bisect_left(keys, t)
        best = min((j for j in (k - 1, k) if 0 <= j < len(lst)),
                   key=lambda j: abs(lst[j]["toe_abs"] - t), default=None)
        if best is None or abs(lst[best]["toe_abs"] - t) > 4 * 3600:
            return None
        return lst[best]


def sat_state(e, t, mu):
    """Position (ECEF, m), Uhrpolynom (s), Relativitaetsterm (s) zum Zeitpunkt t."""
    A = e["sqrtA"] ** 2
    tk = t - e["toe_abs"]
    n = math.sqrt(mu / A ** 3) + e["dn"]
    M = e["M0"] + n * tk
    ecc = e["e"]
    E = M
    for _ in range(30):
        dE = (M - E + ecc * math.sin(E)) / (1.0 - ecc * math.cos(E))
        E += dE
        if abs(dE) < 1e-14:
            break
    v = math.atan2(math.sqrt(1 - ecc ** 2) * math.sin(E), math.cos(E) - ecc)
    phi = v + e["omega"]
    s2, c2 = math.sin(2 * phi), math.cos(2 * phi)
    u = phi + e["Cus"] * s2 + e["Cuc"] * c2
    r = A * (1 - ecc * math.cos(E)) + e["Crs"] * s2 + e["Crc"] * c2
    inc = e["i0"] + e["idot"] * tk + e["Cis"] * s2 + e["Cic"] * c2
    x, y = r * math.cos(u), r * math.sin(u)
    Om = e["Omega0"] + (e["Omegadot"] - OMEGA_E) * tk - OMEGA_E * e["toe"]
    pos = np.array([x * math.cos(Om) - y * math.cos(inc) * math.sin(Om),
                    x * math.sin(Om) + y * math.cos(inc) * math.cos(Om),
                    y * math.sin(inc)])
    dtc = t - e["toc"]
    clk = e["af0"] + e["af1"] * dtc + e["af2"] * dtc ** 2
    rel = -2.0 * math.sqrt(mu) / C ** 2 * ecc * e["sqrtA"] * math.sin(E)
    return pos, clk, rel


# ----------------------------------------------------------------------------
# Geometrie und Troposphaere
# ----------------------------------------------------------------------------
def ecef2geo(xyz):
    a, f = 6378137.0, 1 / 298.257223563
    e2 = f * (2 - f)
    x, y, z = xyz
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat, h = math.atan2(z, p * (1 - e2)), 0.0
    for _ in range(10):
        N = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - N
        lat = math.atan2(z, p * (1 - e2 * N / (N + h)))
    return lat, lon, h


def troposphere(el, lat, h):
    """Saastamoinen (Standardatmosphaere) + pauschal 10 cm feucht, Black-Eisner-Mapping."""
    P = 1013.25 * (1 - 2.2557e-5 * h) ** 5.2568
    zhd = 0.0022768 * P / (1 - 0.00266 * math.cos(2 * lat) - 0.00028 * h / 1000)
    return (zhd + 0.10) * 1.001 / math.sqrt(0.002001 + math.sin(el) ** 2)


# ----------------------------------------------------------------------------
# RINEX-3-Beobachtungsdatei
# ----------------------------------------------------------------------------
def read_obs(path, sysc):
    with open(path, errors="replace") as f:
        lines = f.readlines()
    types, xyz, version, cur, marker, i = {}, None, None, None, None, 0
    while i < len(lines):
        line = lines[i]
        i += 1
        label = line[60:].strip()
        if label == "RINEX VERSION / TYPE":
            version = float(line[:9])
        elif label == "MARKER NAME":
            marker = line[:60].strip()
        elif label == "APPROX POSITION XYZ":
            xyz = np.array([float(line[0:14]), float(line[14:28]), float(line[28:42])])
        elif label == "SYS / # / OBS TYPES":
            if line[0] != " ":
                cur = line[0]
                types[cur] = []
            types[cur] += line[7:60].split()
        elif label == "END OF HEADER":
            break
    if version is None or not (3.0 <= version < 5.0):
        sys.exit(f"Beobachtungsdatei: RINEX-Version {version} wird nicht unterstuetzt (nur 3.x/4.x).")
    if sysc not in types:
        sys.exit(f"Keine Beobachtungen fuer System {sysc} in der Datei.")

    def first(cands):
        return next((c for c in cands if c in types[sysc]), None)

    c1, c2 = first(CODES[sysc][0]), first(CODES[sysc][1])
    if not c1 or not c2:
        sys.exit(f"Keine passende Zweifrequenz-Kombination fuer {sysc}. Vorhanden: {types[sysc]}")
    j1, j2 = types[sysc].index(c1), types[sysc].index(c2)

    def field(l, j):
        s = l[3 + 16 * j: 3 + 16 * j + 14].strip()
        return float(s) if s else None

    epochs = []
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.startswith(">"):
            continue
        flag, ns = int(line[31]), int(line[32:35])
        if flag > 1:            # Ereignis-/Kommentarblock ueberspringen
            i += ns
            continue
        t = gps_seconds(int(line[2:6]), int(line[7:9]), int(line[10:12]),
                        int(line[13:15]), int(line[16:18]), float(line[18:29]))
        rec = {}
        for l in lines[i:i + ns]:
            if l[0] != sysc:
                continue
            p1, p2 = field(l, j1), field(l, j2)
            if p1 and p2:
                rec[l[0:3].replace(" ", "0")] = (p1, p2)
        i += ns
        epochs.append((t, rec))
    return epochs, xyz, (c1, c2), marker


# ----------------------------------------------------------------------------
# Auswertung
# ----------------------------------------------------------------------------
def residuals(sv, P, t_rx, dtr, rx, lat, lon, h, pick):
    """Residuum ohne und mit Relativitaetsterm (m), Elevation (rad), Gesundheit."""
    e = pick(sv, t_rx)
    if e is None:
        return None
    mu = MU[sv[0]]
    tau = P / C
    for _ in range(3):  # Signallaufzeit iterieren
        pos, clk, rel = sat_state(e, t_rx - dtr - tau, mu)
        th = OMEGA_E * tau  # Erddrehung waehrend der Laufzeit (Sagnac)
        pos = np.array([pos[0] * math.cos(th) + pos[1] * math.sin(th),
                        -pos[0] * math.sin(th) + pos[1] * math.cos(th), pos[2]])
        d = pos - rx
        rho = float(np.linalg.norm(d))
        tau = rho / C
    up = np.array([math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)])
    el = math.asin(float(d @ up) / rho)
    res_norel = P - rho + C * clk - troposphere(el, lat, h) if el > 0 else float("nan")
    return res_norel, res_norel + C * rel, el, e["health"] == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("obs", help="RINEX-3-Beobachtungsdatei (entpackt)")
    ap.add_argument("nav", help="RINEX-3-Navigationsdatei (Broadcast, z.B. BRDC...MN.rnx)")
    ap.add_argument("sat", help="Satellit, z.B. E14, E18, G05")
    ap.add_argument("--mask", type=float, default=10.0, help="Elevationsmaske in Grad (Standard 10)")
    ap.add_argument("--bin", type=float, default=5.0, help="Mittelung in Minuten, 0 = keine (Standard 5)")
    ap.add_argument("--xyz", type=float, nargs=3, help="Stationskoordinate ECEF in m (sonst aus Header)")
    ap.add_argument("--out", default="relativitaet.png", help="Ausgabedatei fuer den Plot")
    ap.add_argument("--quelle", help="Quellenangabe fuer den Plot, z.B. 'BKG GNSS Data Center (igs.bkg.bund.de)'")
    ap.add_argument("--station", help="Stationsname fuer den Plottitel, z.B. 'Euskirchen' (sonst Kuerzel aus dem Header)")
    args = ap.parse_args()

    sat = args.sat.upper()
    sat = sat[0] + sat[1:].zfill(2)
    sysc = sat[0]
    if sysc not in MU:
        sys.exit("Nur GPS (G..) und Galileo (E..) werden unterstuetzt.")

    print("Lese Navigationsdaten ...")
    pick = EphPicker(read_nav(args.nav), galileo_fnav=True)
    print("Lese Beobachtungen ...")
    epochs, xyz_hdr, codes, marker = read_obs(args.obs, sysc)
    station = args.station or (marker[:4] if marker else None)
    rx = np.array(args.xyz) if args.xyz else xyz_hdr
    if rx is None:
        sys.exit("Keine Stationskoordinate gefunden, bitte --xyz angeben.")
    lat, lon, h = ecef2geo(rx)
    f1, f2 = FREQ[sysc]
    a1, a2 = f1 ** 2 / (f1 ** 2 - f2 ** 2), f2 ** 2 / (f1 ** 2 - f2 ** 2)
    print(f"Codes: {codes[0]} / {codes[1]},  Station: {math.degrees(lat):.3f} N, {math.degrees(lon):.3f} E")
    if pick(sat, epochs[len(epochs) // 2][0]) is None:
        sys.exit(f"Keine Ephemeride fuer {sat} in der Navigationsdatei.")

    mask = math.radians(args.mask)
    T, meas, corr, unhealthy = [], [], [], False
    diag = dict(beob=0, eph=0, maske=0, ref=0, ungesund=0, ohne_eph=0)
    for t, rec in epochs:
        if sat not in rec:
            continue
        diag["beob"] += 1
        dtr, ok = 0.0, False
        for it in range(2):   # Empfaengeruhr zweimal schaetzen (geht in die Sendezeit ein)
            others, tgt, tgt_eph = [], None, False
            for sv, (p1, p2) in rec.items():
                r = residuals(sv, a1 * p1 - a2 * p2, t, dtr, rx, lat, lon, h, pick)
                if r is None:
                    if it == 0 and sv != sat:
                        diag["ohne_eph"] += 1
                    continue
                if sv == sat:
                    tgt_eph = True
                if not r[2] > mask:
                    continue
                if sv == sat:
                    tgt = r
                elif r[3]:
                    others.append(r[1])
                elif it == 0:
                    diag["ungesund"] += 1
            if it == 0:
                diag["eph"] += tgt_eph
                diag["maske"] += tgt is not None
                diag["ref"] += tgt is not None and len(others) >= 4
            ok = tgt is not None and len(others) >= 4
            if not ok:
                break
            dtr = float(np.median(others)) / C
        if ok:
            unhealthy |= not tgt[3]
            T.append(t)
            meas.append(-(tgt[0] - C * dtr) / C * 1e9)   # gemessener Uhrstand, ns
            corr.append(-(tgt[1] - C * dtr) / C * 1e9)   # nach Abzug der Theorie
    print(f"Diagnose {sat}: {diag['beob']} Epochen mit Zweifrequenz-Beobachtung, "
          f"{diag['eph']} davon mit Ephemeride, {diag['maske']} ueber der Elevationsmaske, "
          f"{diag['ref']} mit >=4 Referenzsatelliten.")
    print(f"         Andere Satelliten verworfen: {diag['ohne_eph']}x ohne Ephemeride, "
          f"{diag['ungesund']}x als ungesund markiert.")
    if diag["beob"] == 0:
        vorhanden = sorted({sv for _, rec in epochs for sv in rec})
        print(f"         {sat} kommt in den Beobachtungen nicht vor. Vorhanden: {' '.join(vorhanden)}")
    if len(T) < 10:
        sys.exit(f"Zu wenige gueltige Epochen fuer {sat} ({len(T)}).")
    if unhealthy:
        print(f"Hinweis: {sat} ist in den Navigationsdaten zeitweise als 'nicht gesund' markiert.")
    T, meas, corr = map(np.array, (T, meas, corr))

    # Theoriekurve und Bahnradius ueber die ganze Zeitspanne
    t0, t1 = epochs[0][0], epochs[-1][0]
    Tg, rel_g, rad_g = [], [], []
    for t in np.arange(t0, t1 + 1, 60.0):
        e = pick(sat, t)
        if e is None:
            continue
        pos, _, rel = sat_state(e, t, MU[sysc])
        Tg.append(t)
        rel_g.append(rel * 1e9)
        rad_g.append(np.linalg.norm(pos) / 1e6)
    Tg, rel_g, rad_g = map(np.array, (Tg, rel_g, rad_g))
    theo = np.interp(T, Tg, rel_g)

    # Wie gut passt die Theorie? Fit: Messung = k * Theorie + Konstante
    Amat = np.vstack([theo, np.ones_like(theo)]).T
    (k, c0), *_ = np.linalg.lstsq(Amat, meas, rcond=None)
    resid = meas - (k * theo + c0)
    sk = math.sqrt(np.sum(resid ** 2) / (len(T) - 2) / np.sum((theo - theo.mean()) ** 2))
    print(f"{len(T)} Epochen. Verhaeltnis Messung/Theorie k = {k:.3f} +- {sk:.3f}")
    print(f"Amplitude Theorie: {np.ptp(rel_g) / 2:.1f} ns,  Streuung nach Korrektur: {np.std(corr):.1f} ns")

    # Mittelung fuer die Darstellung
    if args.bin > 0:
        b = np.floor((T - t0) / (args.bin * 60)).astype(int)
        keys = np.unique(b)
        Tm = np.array([T[b == q].mean() for q in keys])
        Mm = np.array([meas[b == q].mean() for q in keys])
        Cm = np.array([corr[b == q].mean() for q in keys])
    else:
        Tm, Mm, Cm = T, meas, corr

    day0 = math.floor(t0 / 86400) * 86400
    hrs = lambda x: (x - day0) / 3600.0
    datum = (GPS_EPOCH + timedelta(seconds=day0)).strftime("%d.%m.%Y")

    # Sichtbarkeitsabschnitte
    gaps = np.where(np.diff(T) > 600)[0]
    starts = np.r_[T[0], T[gaps + 1]]
    ends = np.r_[T[gaps], T[-1]]

    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(10, 7),
                                   gridspec_kw={"height_ratios": [1, 2]})
    for s, e in zip(starts, ends):
        for ax in (ax1, ax2):
            ax.axvspan(hrs(s), hrs(e), color="gold", alpha=0.2, lw=0)

    ax1.plot(hrs(Tg), rad_g, color="tab:blue")
    ax1.set_ylabel("Abstand zum\nErdmittelpunkt\n(1000 km)")
    ort = f"Station {station}, " if station else ""
    ax1.set_title(f"Satellitenuhr {sat}: Relativitätstheorie vs. Messung  "
                  f"(gelb: Satellit sichtbar)\n{ort}{datum}")
    ax1.grid(alpha=0.3)

    ax2.plot(hrs(Tg), rel_g, color="tab:red", lw=2, label="Vorhersage Relativitätstheorie")
    ax2.plot(hrs(Tm), Mm, "o", ms=4, color="black", label="Messung")
    ax2.plot(hrs(Tm), Cm, "o", ms=3, color="grey", alpha=0.6, label="Messung minus Vorhersage")
    ax2.axhline(0, color="k", lw=0.5)
    ax2.set_ylabel("Vorlauf der Satellitenuhr (ns)")
    ax2.set_xlabel("Uhrzeit (GPS-Zeit) in h")
    ax2.grid(alpha=0.3)
    ax2.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=3, frameon=False)
    sec = ax2.secondary_yaxis("right", functions=(lambda x: x * C * 1e-9, lambda m: m / (C * 1e-9)))
    sec.set_ylabel("entspricht Abstandsfehler (m)")
    quelle = f"Daten: {os.path.basename(args.obs)}, {os.path.basename(args.nav)}"
    if args.quelle:
        quelle += f"   |   Quelle: {args.quelle}"
    fig.text(0.01, 0.01, f"Messung/Theorie = {k:.2f} ± {sk:.2f}   |   Codes {codes[0]}/{codes[1]}\n{quelle}",
             fontsize=8, color="grey")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(args.out, dpi=150)
    print(f"Plot gespeichert: {args.out}")
    plt.show()


if __name__ == "__main__":
    main()
