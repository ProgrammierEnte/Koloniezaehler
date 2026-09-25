# Kolonie-Zähler

Programm zum automatischen Auszählen von Bakterienkolonien auf Abklatschplatten (Petrischalen). Man wählt ein Foto oder einen ganzen Ordner mit Fotos aus, das Programm erkennt die Kolonien, markiert sie im Bild und zählt sie. Die Ergebnisse lassen sich als CSV-Datei (z. B. für Excel) exportieren.

## Dateien

| Datei | Inhalt |
|---|---|
| `UI.py` | Die Oberfläche. Diese Datei wird gestartet. |
| `pipeline_common.py` | Gemeinsame Bildvorverarbeitung (Schale finden, Hintergrund ausgleichen usw.) |
| `blob_engine.py` | Erkennungsmethode 1: Blob-Counter |
| `watershed_engine.py` | Erkennungsmethode 2: Watershed |
| `requirements.txt` | Liste der benötigten Python-Pakete |

## Installation

### 1. Alle Dateien in einen Ordner legen

Alle fünf Dateien aus der Tabelle oben müssen im **gleichen Ordner** liegen, zum Beispiel in `C:\Kolonie-Zaehler`. Wenn eine Datei fehlt oder woanders liegt, startet das Programm nicht.

### 2. Python installieren

Das Programm braucht Python (Version 3.9 oder neuer).

- Download: <https://www.python.org/downloads/>
- Bei der Installation unter Windows unbedingt das Häkchen **"Add Python to PATH"** setzen, sonst findet das Terminal Python später nicht.

Ob Python installiert ist, kann man im Terminal (Schritt 3) prüfen:

```bash
python --version
```

Wenn eine Versionsnummer wie `Python 3.12.1` erscheint, passt alles.

### 3. Terminal im Ordner öffnen

- **Windows:** Den Ordner im Explorer öffnen, oben in die Adressleiste klicken, `cmd` eintippen und Enter drücken. Es öffnet sich ein schwarzes Fenster, das schon im richtigen Ordner steht.
- **Mac:** Rechtsklick auf den Ordner, dann "Neues Terminal beim Ordner".
- **Linux:** Rechtsklick im Ordner, dann "Im Terminal öffnen".

### 4. Benötigte Pakete installieren

Im Terminal eingeben und Enter drücken (das ist nur einmal nötig und dauert ein paar Minuten):

```bash
pip install -r requirements.txt
```

Wenn `pip` nicht gefunden wird, hilft stattdessen:

```bash
python -m pip install -r requirements.txt
```

### 5. Programm starten

```bash
python UI.py
```

Auf Mac und Linux heisst der Befehl meistens `python3 UI.py`. Danach öffnet sich das Fenster des Programms.

## Bedienung in Kürze

1. Links auf **Bild auswählen …** (ein einzelnes Foto) oder **Ordner auswählen …** (alle Fotos in einem Ordner) klicken.
2. Bei Bedarf die Einstellungen rechts anpassen (siehe unten). Die Standardwerte funktionieren für die meisten Fotos.
3. **Analyse starten** klicken. Bei einem Ordner werden alle Bilder nacheinander ausgewertet, der Fortschrittsbalken unten zeigt den Stand.
4. Die erkannten Kolonien sind grün eingekreist, die Zahl steht rechts unter "Ergebnisse".
5. Mit **CSV exportieren** speichert man alle Zählungen in einer Datei.

Unterstützte Bildformate: PNG, JPG, JPEG, BMP, TIF, TIFF.

## Einstellungen

Alle Einstellungen findet man im rechten Bereich. Wenn man mit der Maus kurz über einen Namen fährt, erscheint zusätzlich ein Hilfetext.

| Einstellung | Was sie macht |
|---|---|
| **Modus** (Blob-Counter / Watershed) | Wählt die Erkennungsmethode. *Watershed* trennt Kolonien, die sich berühren, oft besser. *Blob-Counter* ist die einfachere Methode. Im Zweifel beide ausprobieren. |
| **Helligkeit** | Macht das Foto vor der Analyse heller (plus) oder dunkler (minus). |
| **Kontrast** | Verstärkt oder verringert den Unterschied zwischen hell und dunkel. |
| **Sättigung** | Verstärkt oder verringert die Farben. Der Effekt auf das Ergebnis ist meist klein. |
| **Min. Koloniegrösse** | Alles, was kleiner ist als dieser Wert (in mm²), wird ignoriert, zum Beispiel Staub, Kratzer oder Bildrauschen. |
| **Kreisförmigkeit** | Wie kompakt und rund ein Fleck sein muss, damit er als Kolonie zählt. 0 = jede Form, 1 = nur sehr runde Formen. |
| **Schwellenwert** | Empfindlichkeit der Erkennung. Höher = strenger (weniger, aber sicherere Treffer), niedriger = empfindlicher (mehr Treffer, aber auch mehr Fehltreffer). 128 ist der Standard. |
| **Kleine Objekte entfernen** | Wenn eingeschaltet, gilt die "Min. Koloniegrösse". Ausgeschaltet werden auch winzige Flecken mitgezählt. |
| **Plattenrand ausschliessen** | Das Programm sucht die Petrischale im Bild und wertet nur die Innenfläche aus. Ausgeschaltet wird das ganze Bild ausgewertet, dann ist aber kein Massstab in mm möglich. |
| **Maskengrösse** | Wie viel der erkannten Schale ausgewertet wird (in Prozent des Schalenradius). Kleiner = mehr Rand wird ausgeschlossen, grösser = mehr Rand wird mitgezählt. Wirkt nur, wenn "Plattenrand ausschliessen" an ist. |
| **Kolonien am Rand zählen** | Kolonien, die den Schalenrand berühren (und vielleicht angeschnitten sind), werden standardmässig nicht gezählt. Mit dieser Option schon. |
| **Detektionsmodus** | "Dunkel auf hell" = dunkle Kolonien auf hellem Agar (der Normalfall). "Hell auf dunkel" = umgekehrt. "Automatisch" schätzt das anhand der Bildhelligkeit. |

## Die wichtigsten Knöpfe

**Linke Seite**

| Knopf | Funktion |
|---|---|
| Bild auswählen … | Ein einzelnes Foto laden |
| Ordner auswählen … | Alle Fotos aus einem Ordner laden (Stapelverarbeitung) |
| Analyse starten | Zählt die Kolonien in allen geladenen Bildern |
| Live-Vorschau | Wenn eingeschaltet, wird das aktuelle Bild automatisch neu berechnet, sobald man einen Regler ändert |
| Erscheinungsbild | Dunkles oder helles Design |
| UI-Skalierung | Macht die ganze Oberfläche kleiner oder grösser (falls der Bildschirm sehr klein oder sehr gross ist) |

**Mitte (Vorschau)**

| Knopf | Funktion |
|---|---|
| ‹ und › | Vorheriges bzw. nächstes Bild aus dem Ordner anzeigen |
| + und − | Ins Bild hinein- oder herauszoomen (geht auch mit dem Mausrad) |
| Einpassen | Bild wieder ganz ins Fenster einpassen |
| Werkzeug ✥ (Verschieben) | Bild mit gedrückter Maustaste verschieben |
| Werkzeug 🔍 (Zoom) | Klick = näher ran, Rechtsklick = weiter weg |
| Werkzeug 📏 (Massstab) | Zwei Punkte einer bekannten Strecke anklicken (z. B. Lineal im Foto) und die Länge in mm eingeben. Damit werden die Grössenangaben genauer. |

**Rechte Seite**

| Knopf | Funktion |
|---|---|
| Standardwerte | Setzt alle Einstellungen auf die Ausgangswerte zurück |
| Vorschau aktualisieren | Rechnet nur das aktuell angezeigte Bild mit den neuen Einstellungen neu |
| CSV exportieren | Speichert Dateiname, Modus, Anzahl und Uhrzeit aller Zählungen in einer CSV-Datei |
| Ordner öffnen | Öffnet den Ordner, aus dem die Fotos stammen |

## Häufige Probleme

- **"python wird nicht erkannt"**: Python ist nicht installiert oder wurde ohne "Add Python to PATH" installiert. Python neu installieren und das Häkchen setzen.
- **"No module named ..." beim Start**: Schritt 4 (`pip install -r requirements.txt`) wurde noch nicht ausgeführt oder ist fehlgeschlagen.
- **"No module named blob_engine"** (oder ähnlich): Nicht alle Dateien liegen im gleichen Ordner, oder das Terminal wurde in einem anderen Ordner geöffnet.
- **Zu viele oder zu wenige Kolonien erkannt**: Den Schwellenwert und die Min. Koloniegrösse anpassen und die andere Methode (Blob-Counter / Watershed) ausprobieren. Mit der Live-Vorschau sieht man die Wirkung sofort.
