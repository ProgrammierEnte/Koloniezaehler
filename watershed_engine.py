import cv2
import numpy as np
from skimage.feature import peak_local_max
from skimage.segmentation import watershed

import pipeline_common as pc

SCHWELLE_BLOCK = 41
SCHWELLE_C = 10
MIN_FLAECHE = 40
MAX_FLAECHE = 8000
MIN_ABSTAND = 15
MIN_SOLIDITAET = 0.75
MAX_SEITENVERHAELTNIS = 3.5
MIN_DUNKELHEIT = 12


def finde_kolonien(
    grau,
    maske,
    min_flaeche=MIN_FLAECHE,
    max_flaeche=MAX_FLAECHE,
    min_soliditaet=MIN_SOLIDITAET,
    max_seitenverhaeltnis=MAX_SEITENVERHAELTNIS,
    min_abstand=MIN_ABSTAND,
    min_dunkelheit=MIN_DUNKELHEIT,
    schwelle_c_offset=0,
    detektionsmodus="dunkel_auf_hell",
    rand_kolonien_zaehlen=True,
):
    grau_n = grau.copy()
    if np.any(maske == 255):
        grau_n[maske == 0] = int(grau[maske == 255].mean())
    else:
        grau_n[maske == 0] = 128

    gefiltert = pc.hebe_flecken_hervor(grau_n, detektionsmodus)
    gefiltert = cv2.bitwise_and(gefiltert, maske)

    c = float(np.clip(SCHWELLE_C + schwelle_c_offset, 1, 60))
    # adaptiv statt fixer Schwelle, weil die Ausleuchtung auf den Fotos selten gleichmässig ist
    binaer = cv2.adaptiveThreshold(
        gefiltert, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, SCHWELLE_BLOCK, -c)
    binaer = cv2.bitwise_and(binaer, maske)
    binaer = cv2.morphologyEx(
        binaer, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))

    rand_ring = pc.rand_ring_maske(maske)

    # Maxima der Distanzkarte = Kolonienmitten, daraus werden die Watershed-Marker
    dist = cv2.distanceTransform(binaer, cv2.DIST_L2, 5)
    gipfel = peak_local_max(dist, min_distance=min_abstand,
                             labels=binaer, exclude_border=False)
    marker = np.zeros(dist.shape, np.int32)
    for nr, (y, x) in enumerate(gipfel, start=1):
        marker[y, x] = nr
    labels = watershed(-dist, marker, mask=binaer.astype(bool))

    mittelpunkte, radien = [], []
    for label in range(1, labels.max() + 1):
        flaeche_maske = (labels == label)
        flaeche = int(flaeche_maske.sum())
        if not (min_flaeche <= flaeche <= max_flaeche):
            continue
        if gefiltert[flaeche_maske].mean() < min_dunkelheit:
            continue
        if not rand_kolonien_zaehlen and pc.ist_randberuehrend(flaeche_maske, rand_ring):
            continue
        konturen, _ = cv2.findContours(flaeche_maske.astype(np.uint8),
                                        cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if konturen:
            kontur = konturen[0]
            huelle_flaeche = cv2.contourArea(cv2.convexHull(kontur))
            soliditaet = flaeche / huelle_flaeche if huelle_flaeche > 0 else 0
            if soliditaet < min_soliditaet:
                continue
            (_, _), (rw, rh), _ = cv2.minAreaRect(kontur)
            seitenverhaeltnis = max(rw, rh) / max(min(rw, rh), 1.0)
            # zu lang/schmal ist meistens ein Kratzer oder zwei zusammengewachsene Kolonien, kein Blob
            if seitenverhaeltnis > max_seitenverhaeltnis:
                continue
        ys, xs = np.where(flaeche_maske)
        mittelpunkte.append((int(xs.mean()), int(ys.mean())))
        radien.append(max(int(np.sqrt(flaeche / np.pi)), 3))
    return mittelpunkte, radien, gefiltert


def analysiere(
    pfad_oder_array,
    *,
    helligkeit=0,
    kontrast=0,
    saettigung=0,
    plattenrand_ausschliessen=True,
    kleine_objekte_entfernen=True,
    rand_kolonien_zaehlen=True,
    detektionsmodus="dunkel_auf_hell",
    min_flaeche=MIN_FLAECHE,
    max_flaeche=MAX_FLAECHE,
    min_soliditaet=MIN_SOLIDITAET,
    max_seitenverhaeltnis=MAX_SEITENVERHAELTNIS,
    min_abstand=MIN_ABSTAND,
    min_dunkelheit=MIN_DUNKELHEIT,
    schwelle_c_offset=0,
    masken_groesse=pc.MASKEN_SCHRUMPF,
    arbeitsgroesse=pc.ARBEITSGROESSE,
    flachfeld_korrektur_aktiv=True,
):
    if isinstance(pfad_oder_array, (str,)):
        bild = pc.lade_bild(pfad_oder_array, arbeitsgroesse=arbeitsgroesse)
    else:
        bild = pfad_oder_array

    bild = pc.bild_anpassen(bild, helligkeit=helligkeit, kontrast=kontrast, saettigung=saettigung)
    grau = cv2.cvtColor(bild, cv2.COLOR_BGR2GRAY)
    if flachfeld_korrektur_aktiv:
        grau = pc.flachfeld_korrektur(grau)

    if not kleine_objekte_entfernen:
        min_flaeche = 0

    if plattenrand_ausschliessen:
        maske, (mx, my, rad) = pc.agar_maske(grau, masken_schrumpf=masken_groesse)
    else:
        maske, (mx, my, rad) = pc.maske_voll(grau)

    mittelpunkte, radien, _gefiltert = finde_kolonien(
        grau, maske,
        min_flaeche=min_flaeche, max_flaeche=max_flaeche,
        min_soliditaet=min_soliditaet, max_seitenverhaeltnis=max_seitenverhaeltnis,
        min_abstand=min_abstand, min_dunkelheit=min_dunkelheit,
        schwelle_c_offset=schwelle_c_offset, detektionsmodus=detektionsmodus,
        rand_kolonien_zaehlen=rand_kolonien_zaehlen,
    )
    anzahl = len(mittelpunkte)

    ergebnis = bild.copy()
    cv2.circle(ergebnis, (mx, my), rad, (255, 255, 0), 8)
    for (px, py), r in zip(mittelpunkte, radien):
        cv2.circle(ergebnis, (px, py), 8, (0, 0, 255), 2)
    cv2.putText(ergebnis, f"Kolonien: {anzahl}", (20, 45),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)

    return {
        "modus": "Watershed",
        "anzahl": anzahl,
        "bild": ergebnis,
        "bild_basis": bild,
        "mittelpunkte": [(x, y, r) for (x, y), r in zip(mittelpunkte, radien)],
        "maske_mittelpunkt": (mx, my),
        "maske_radius": rad,
        "mm_pro_px": pc.berechne_mm_pro_px(rad) if plattenrand_ausschliessen else None,
    }
