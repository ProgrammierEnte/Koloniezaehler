import os

import cv2
import numpy as np

# alles wird auf diese Grösse normiert, sonst passen die area-Schwellen nicht mehr je nach Kamera/Foto
ARBEITSGROESSE = 1000

MASKEN_SCHRUMPF = 0.78
BLACKHAT_GROESSE = 75

FLACHFELD_SIGMA = 100

SCHALE_DURCHM_MM = 90.0  # Standardplatten

RAND_RING_BREITE = 6


def lade_bild(pfad, arbeitsgroesse=ARBEITSGROESSE):
    daten = np.fromfile(pfad, dtype=np.uint8)
    bild = cv2.imdecode(daten, cv2.IMREAD_COLOR)
    if bild is None:
        raise FileNotFoundError(f"Bild nicht gefunden oder unlesbar: {pfad}")
    return cv2.resize(bild, (arbeitsgroesse, arbeitsgroesse))


def speichere_bild(pfad, bild):
    os.makedirs(os.path.dirname(pfad) or ".", exist_ok=True)
    cv2.imencode(".png", bild)[1].tofile(pfad)


def bild_anpassen(bgr, helligkeit=0, kontrast=0, saettigung=0):
    alpha = 1.0 + (kontrast / 50.0)
    beta = float(helligkeit)
    angepasst = cv2.convertScaleAbs(bgr, alpha=alpha, beta=beta)

    if saettigung != 0:
        hsv = cv2.cvtColor(angepasst, cv2.COLOR_BGR2HSV).astype(np.float32)
        faktor = 1.0 + (saettigung / 50.0)
        hsv[..., 1] = np.clip(hsv[..., 1] * faktor, 0, 255)
        angepasst = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    return angepasst


def flachfeld_korrektur(grau, sigma=FLACHFELD_SIGMA):
    hintergrund = cv2.GaussianBlur(grau, (0, 0), sigma)
    # Hintergrund stark weichzeichnen und rausdividieren -> Schatten/Verlauf im Foto verschwinden
    korrigiert = grau.astype(np.float32) / (hintergrund.astype(np.float32) + 1e-6) * 128.0
    return np.clip(korrigiert, 0, 255).astype(np.uint8)


def finde_schale(grau):
    g = cv2.medianBlur(grau, 5)
    breite = g.shape[1]
    # param1/param2/minDist per Ausprobieren gefunden, funktioniert bei meinen Fotos gut genug
    kreise = cv2.HoughCircles(
        g, cv2.HOUGH_GRADIENT, dp=1.5, minDist=breite,
        param1=100, param2=50,
        minRadius=int(breite * 0.25), maxRadius=int(breite * 0.5))
    if kreise is None:
        return None
    kreise = np.uint16(np.around(kreise))
    groesster = max(kreise[0, :], key=lambda c: c[2])
    return int(groesster[0]), int(groesster[1]), int(groesster[2])


def agar_maske(grau, masken_schrumpf=MASKEN_SCHRUMPF):
    schale = finde_schale(grau)
    if schale is None:
        # Fallback wenn keine Schale gefunden wird: einfach ein Kreis in der Bildmitte
        h, w = grau.shape
        m = np.zeros(grau.shape, np.uint8)
        cv2.circle(m, (w // 2, h // 2), int(w * 0.45), 255, -1)
        return m, (w // 2, h // 2, int(w * 0.45))
    cx, cy, r = schale

    suchbereich = np.zeros(grau.shape, np.uint8)
    cv2.circle(suchbereich, (cx, cy), r, 255, -1)

    schwelle, _ = cv2.threshold(grau[suchbereich == 255], 0, 255,
                                 cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    hell = cv2.bitwise_and(cv2.inRange(grau, int(schwelle), 255), suchbereich)
    hell = cv2.morphologyEx(hell, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    anzahl, labels, stats, schwerpunkte = cv2.connectedComponentsWithStats(
        hell, connectivity=8)
    mx, my = cx, cy
    if anzahl > 1:
        groesste = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        hx, hy = int(schwerpunkte[groesste][0]), int(schwerpunkte[groesste][1])
        max_verschiebung = 0.06 * r
        # nur übernehmen wenn nah am Kreismittelpunkt, sonst schnappt er sich eine Reflexion statt dem Agar
        if np.hypot(hx - cx, hy - cy) <= max_verschiebung:
            mx, my = hx, hy

    rad = int(r * masken_schrumpf)
    maske = np.zeros(grau.shape, np.uint8)
    cv2.circle(maske, (mx, my), rad, 255, -1)
    return maske, (mx, my, rad)


def maske_voll(grau):
    h, w = grau.shape
    maske = np.full(grau.shape, 255, np.uint8)
    return maske, (w // 2, h // 2, int(min(w, h) / 2))


def hebe_flecken_hervor(grau_n, modus="dunkel_auf_hell", blackhat_groesse=BLACKHAT_GROESSE):
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (blackhat_groesse, blackhat_groesse))
    # "automatisch" ist nur eine grobe Schätzung über die mittlere Helligkeit
    if modus == "automatisch":
        modus = "dunkel_auf_hell" if float(np.mean(grau_n)) >= 127 else "hell_auf_dunkel"
    op = cv2.MORPH_TOPHAT if modus == "hell_auf_dunkel" else cv2.MORPH_BLACKHAT
    return cv2.morphologyEx(grau_n, op, kern)


def berechne_mm_pro_px(radius_px, schale_durchmesser_mm=SCHALE_DURCHM_MM):
    if radius_px <= 0:
        return None
    return schale_durchmesser_mm / (2.0 * radius_px)


def mm2_zu_px2(flaeche_mm2, mm_pro_px):
    if not mm_pro_px:
        return flaeche_mm2
    return flaeche_mm2 / (mm_pro_px ** 2)


def rand_ring_maske(maske, ring_breite=RAND_RING_BREITE):
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ring_breite * 2 + 1,) * 2)
    erodiert = cv2.erode(maske, kern, iterations=1)
    return cv2.subtract(maske, erodiert)


def ist_randberuehrend(flaeche_maske, rand_ring):
    return bool(np.any(flaeche_maske & (rand_ring > 0)))
