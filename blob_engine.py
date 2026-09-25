import cv2
import numpy as np

import pipeline_common as pc

BLOB_MIN_FLAECHE = 35
BLOB_MAX_FLAECHE = 8000
BLOB_MIN_KONVEXITAET = 0.70
BLOB_MIN_INERTIA = 0.15
BLOB_MIN_ABSTAND = 8
# detector testet mehrere Schwellen zwischen min/max durch und clustert die Treffer zusammen
BLOB_MIN_SCHWELLE = 6
BLOB_MAX_SCHWELLE = 250
BLOB_SCHWELLE_SCHRITT = 6
BLOB_MIN_WIEDERHOLBARKEIT = 2


def erstelle_blob_detector(
    min_flaeche=BLOB_MIN_FLAECHE,
    max_flaeche=BLOB_MAX_FLAECHE,
    min_konvexitaet=BLOB_MIN_KONVEXITAET,
    min_inertia=BLOB_MIN_INERTIA,
    min_abstand=BLOB_MIN_ABSTAND,
    schwellen_fenster_offset=0,
):
    min_schwelle = float(np.clip(BLOB_MIN_SCHWELLE + schwellen_fenster_offset, 0, 255))
    max_schwelle = float(np.clip(BLOB_MAX_SCHWELLE + schwellen_fenster_offset, 0, 255))
    if max_schwelle <= min_schwelle:
        max_schwelle = min_schwelle + BLOB_SCHWELLE_SCHRITT

    params = cv2.SimpleBlobDetector_Params()

    params.filterByColor = True
    params.blobColor = 255

    params.filterByArea = True
    params.minArea = float(min_flaeche)
    params.maxArea = float(max_flaeche)

    params.filterByCircularity = False  # Kolonien sind oft nicht rund, Circularity hat zu viele rausgeworfen

    params.filterByConvexity = True
    params.minConvexity = float(min_konvexitaet)

    params.filterByInertia = True
    params.minInertiaRatio = float(min_inertia)

    params.minThreshold = min_schwelle
    params.maxThreshold = max_schwelle
    params.thresholdStep = BLOB_SCHWELLE_SCHRITT
    params.minRepeatability = BLOB_MIN_WIEDERHOLBARKEIT

    params.minDistBetweenBlobs = float(min_abstand)

    return cv2.SimpleBlobDetector_create(params)


def finde_kolonien_blob(
    grau,
    maske,
    min_flaeche=BLOB_MIN_FLAECHE,
    max_flaeche=BLOB_MAX_FLAECHE,
    min_konvexitaet=BLOB_MIN_KONVEXITAET,
    min_inertia=BLOB_MIN_INERTIA,
    min_abstand=BLOB_MIN_ABSTAND,
    schwellen_fenster_offset=0,
    detektionsmodus="dunkel_auf_hell",
    rand_kolonien_zaehlen=True,
):
    grau_n = grau.copy()
    if np.any(maske == 255):
        # ausserhalb der Maske neutral einfärben, sonst hält der Detector den Rand für einen Blob
        grau_n[maske == 0] = int(grau[maske == 255].mean())
    else:
        grau_n[maske == 0] = 128

    hervorgehoben = pc.hebe_flecken_hervor(grau_n, detektionsmodus)
    hervorgehoben = cv2.bitwise_and(hervorgehoben, maske)
    hervorgehoben_glatt = cv2.GaussianBlur(hervorgehoben, (3, 3), 0)

    detector = erstelle_blob_detector(
        min_flaeche=min_flaeche, max_flaeche=max_flaeche,
        min_konvexitaet=min_konvexitaet, min_inertia=min_inertia,
        min_abstand=min_abstand, schwellen_fenster_offset=schwellen_fenster_offset,
    )
    keypoints = detector.detect(hervorgehoben_glatt)

    rand_ring = pc.rand_ring_maske(maske)
    mittelpunkte, radien = [], []
    for kp in keypoints:
        x, y = int(kp.pt[0]), int(kp.pt[1])
        r = max(int(kp.size / 2), 3)
        if not rand_kolonien_zaehlen:
            punkt_maske = np.zeros(maske.shape, np.uint8)
            cv2.circle(punkt_maske, (x, y), r, 255, -1)
            if pc.ist_randberuehrend(punkt_maske > 0, rand_ring):
                continue
        mittelpunkte.append((x, y))
        radien.append(r)
    return mittelpunkte, radien, hervorgehoben


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
    min_flaeche=BLOB_MIN_FLAECHE,
    max_flaeche=BLOB_MAX_FLAECHE,
    min_konvexitaet=BLOB_MIN_KONVEXITAET,
    min_inertia=BLOB_MIN_INERTIA,
    min_abstand=BLOB_MIN_ABSTAND,
    schwellen_fenster_offset=0,
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

    mittelpunkte, radien, _hervorgehoben = finde_kolonien_blob(
        grau, maske,
        min_flaeche=min_flaeche, max_flaeche=max_flaeche,
        min_konvexitaet=min_konvexitaet, min_inertia=min_inertia,
        min_abstand=min_abstand, schwellen_fenster_offset=schwellen_fenster_offset,
        detektionsmodus=detektionsmodus, rand_kolonien_zaehlen=rand_kolonien_zaehlen,
    )
    anzahl = len(mittelpunkte)

    ergebnis = bild.copy()
    cv2.circle(ergebnis, (mx, my), rad, (255, 255, 0), 8)
    for (px, py), r in zip(mittelpunkte, radien):
        cv2.circle(ergebnis, (px, py), r, (0, 0, 255), 2)
    cv2.putText(ergebnis, f"Kolonien: {anzahl}", (20, 45),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)

    return {
        "modus": "Blob-Counter",
        "anzahl": anzahl,
        "bild": ergebnis,
        "bild_basis": bild,
        "mittelpunkte": [(x, y, r) for (x, y), r in zip(mittelpunkte, radien)],
        "maske_mittelpunkt": (mx, my),
        "maske_radius": rad,
        "mm_pro_px": pc.berechne_mm_pro_px(rad) if plattenrand_ausschliessen else None,
    }
