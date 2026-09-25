import csv
import os
import queue
import re
import statistics
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

import customtkinter as ctk
from PIL import Image, ImageTk
import cv2

import blob_engine
import pipeline_common as pc
import watershed_engine

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG_FRAME = ("#EBEBEB", "#2B2B2B")
BG_TILE = ("#DCDCDC", "#333333")
BORDER = ("#C6C6C6", "#565B5E")
TXT = ("#1A1A1A", "#DCE4EE")
TXT_DIM = ("#5B6165", "#8E9599")
LOG_BG = ("#F2F2F2", "#1D1E1E")
ACCENT_GREEN = "#2FA572"

# mehr Formate braucht es nicht, cv2 kann die alle lesen
UNTERSTUETZTE_ENDUNGEN = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}

SKALIERUNG_BASIS = 1.2

# grobe Annahme falls noch nie kalibriert wurde - besser als gar nichts anzuzeigen
STANDARD_MM_PRO_PX = pc.SCHALE_DURCHM_MM / (2 * pc.MASKEN_SCHRUMPF * 0.45 * pc.ARBEITSGROESSE)

DETEKTIONSMODUS_UI_ZU_INTERN = {
    "Hell auf dunkel": "hell_auf_dunkel",
    "Dunkel auf hell": "dunkel_auf_hell",
    "Automatisch": "automatisch",
}


class ToolTip:
    # kleiner Selbstbau, customtkinter hat keine eigenen Tooltips

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.fenster = None
        widget.bind("<Enter>", self._zeigen, add="+")
        widget.bind("<Leave>", self._verstecken, add="+")

    def _zeigen(self, _event=None):
        if self.fenster or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.fenster = tk.Toplevel(self.widget)
        self.fenster.wm_overrideredirect(True)
        self.fenster.wm_geometry(f"+{x}+{y}")
        tk.Label(self.fenster, text=self.text, justify="left", background="#2B2B2B",
                 foreground="#DCE4EE", relief="solid", borderwidth=1,
                 font=("Roboto", 10), wraplength=260, padx=8, pady=5).pack()

    def _verstecken(self, _event=None):
        if self.fenster:
            self.fenster.destroy()
            self.fenster = None


class ReglerZeile:

    def __init__(self, parent, label, value, lo, hi, formatter=None, on_change=None, hilfe=None):
        self.lo, self.hi = lo, hi
        # _sync verhindert Endlosschleife zwischen Slider und Textfeld
        self.formatter = formatter or (lambda w: f"{w:.2f}")
        self.on_change = on_change
        self._sync = False

        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=5)
        self.label_widget = ctk.CTkLabel(row, text=label, font=("Roboto", 12.5), width=104, anchor="w")
        self.label_widget.pack(side="left")
        self.entry = ctk.CTkEntry(row, width=62, font=("Roboto Mono", 11.5))
        self.entry.pack(side="right")
        self.slider = ctk.CTkSlider(row, from_=lo, to=hi, command=self._on_slider)
        self.slider.set(value)
        self.slider.pack(side="left", fill="x", expand=True, padx=8)

        self.entry.bind("<Return>", self._on_entry)
        self.entry.bind("<FocusOut>", self._on_entry)
        self._set_text(value)

        if hilfe:
            ToolTip(self.label_widget, hilfe)

    def _set_text(self, wert):
        self.entry.delete(0, "end")
        self.entry.insert(0, self.formatter(wert))

    def _on_slider(self, wert):
        if self._sync:
            return
        self._sync = True
        self._set_text(wert)
        self._sync = False
        if self.on_change:
            self.on_change(wert)

    def _on_entry(self, _event=None):
        if self._sync:
            return
        # Einheiten wie "mm²" oder "%" im Feld ignorieren, nur die Zahl rausholen
        treffer = re.search(r"-?\d+(\.\d+)?", self.entry.get())
        if not treffer:
            self._set_text(self.slider.get())
            return
        wert = max(self.lo, min(self.hi, float(treffer.group())))
        self._sync = True
        self.slider.set(wert)
        self._set_text(wert)
        self._sync = False
        if self.on_change:
            self.on_change(wert)

    def get(self):
        return self.slider.get()

    def set(self, wert):
        wert = max(self.lo, min(self.hi, wert))
        self._sync = True
        self.slider.set(wert)
        self._set_text(wert)
        self._sync = False


class KolonieZaehlerApp(ctk.CTk):
    def __init__(self):
        ctk.set_widget_scaling(SKALIERUNG_BASIS)
        super().__init__()
        self.title("Kolonie-Zähler — Abklatschplatten-Analyse")
        self.geometry("1440x900")
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Zustand der App
        self.batch_pfade = []
        self.aktueller_pfad = None
        self.aktueller_ordner = None
        self.ergebnisse = {}
        self._rohbild = None
        self.letzte_mm_pro_px = None
        self.benutzer_kalibrierungen = {}
        self._aufloesung_cache = {}

        # Ansicht: zoom + Verschiebung in Canvas-Pixeln
        self.zoom = 1.0
        self.pan_x, self.pan_y = 0, 0
        self.werkzeug = "move"
        self._drag_start = None
        self._folgt_einpassung = True
        self._kalibrier_start = None

        # Analyse-Thread Kram
        self._laeuft_analyse = False
        self._analyse_queue = None
        self._analyse_gesamt = 0
        self._analyse_fertig = 0
        self._abschluss_modus = None
        self._live_debounce_id = None

        self._tk_img = None  # Referenz halten, sonst räumt Python das Bild weg und das Canvas bleibt leer

        self._sidebar_bauen()
        self._vorschau_bereich_bauen()
        self._einstellungen_und_ergebnisse_bauen()

        self._render_vorschau()

    def _sidebar_bauen(self):
        side = ctk.CTkFrame(self, width=228, corner_radius=0)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_propagate(False)
        side.grid_rowconfigure(6, weight=1)

        ctk.CTkLabel(side, text="Kolonie-\nZähler", font=("Roboto", 21, "bold"),
                     justify="left").grid(row=0, column=0, padx=20, pady=(18, 0), sticky="w")
        ctk.CTkLabel(side, text="Automatisches Auszählen von\nBakterienkolonien",
                     font=("Roboto", 11), text_color=TXT_DIM, justify="left"
                     ).grid(row=1, column=0, padx=20, pady=(2, 12), sticky="w")

        btns = ctk.CTkFrame(side, fg_color="transparent")
        btns.grid(row=2, column=0, padx=20, sticky="ew")
        btns.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(btns, text="Bild auswählen …", height=34, fg_color="transparent",
                      border_width=2, border_color=BORDER,
                      command=self._bild_auswaehlen).grid(row=0, column=0, pady=5, sticky="ew")
        ctk.CTkButton(btns, text="Ordner auswählen …", height=34, fg_color="transparent",
                      border_width=2, border_color=BORDER,
                      command=self._ordner_auswaehlen).grid(row=1, column=0, pady=5, sticky="ew")
        ctk.CTkButton(btns, text="Analyse starten", height=34,
                      command=self._analyse_starten).grid(row=2, column=0, pady=5, sticky="ew")

        ctk.CTkLabel(side, text="Ausgewählter Pfad", font=("Roboto", 11), text_color=TXT_DIM
                     ).grid(row=3, column=0, padx=20, pady=(16, 4), sticky="w")
        self._pfad_anzeige = ctk.CTkEntry(side, font=("Roboto Mono", 11))
        self._pfad_anzeige.insert(0, "— kein Bild ausgewählt —")
        self._pfad_anzeige.configure(state="disabled")
        self._pfad_anzeige.grid(row=4, column=0, padx=20, sticky="ew")

        self._bilder_gefunden_label = ctk.CTkLabel(
            side, text="Kein Bild ausgewählt", font=("Roboto", 11), text_color=TXT_DIM, anchor="w")
        self._bilder_gefunden_label.grid(row=5, column=0, padx=20, pady=(4, 0), sticky="w")

        bottom = ctk.CTkFrame(side, fg_color="transparent")
        bottom.grid(row=7, column=0, padx=20, pady=16, sticky="ew")

        # Live-Vorschau standardmässig aus, auf dem Laptop ist es sonst zu träge
        self.sw_live_vorschau = ctk.CTkSwitch(bottom, text="Live-Vorschau", font=("Roboto", 12),
                                               command=self._live_vorschau_ausloesen)
        self.sw_live_vorschau.deselect()
        self.sw_live_vorschau.pack(anchor="w", pady=(0, 14))
        ToolTip(self.sw_live_vorschau,
                "Wenn aktiv: die Vorschau des aktuellen Bilds wird automatisch neu berechnet, "
                "sobald sich ein Regler/Schalter ändert (kurze Verzögerung, kein Batch-Lauf). "
                "'Vorschau aktualisieren' funktioniert weiterhin zusätzlich per Klick.")

        ctk.CTkLabel(bottom, text="Erscheinungsbild:", font=("Roboto", 12)).pack(anchor="w")
        ctk.CTkOptionMenu(bottom, values=["Dark", "Light", "System"],
                          command=ctk.set_appearance_mode).pack(fill="x", pady=(4, 10))
        ctk.CTkLabel(bottom, text="UI-Skalierung:", font=("Roboto", 12)).pack(anchor="w")
        self.opt_ui_skalierung = ctk.CTkOptionMenu(bottom, values=["80 %", "100 %", "120 %"],
                                                     command=self._ui_skalierung_geaendert)
        self.opt_ui_skalierung.set("100 %")
        self.opt_ui_skalierung.pack(fill="x", pady=(4, 0))

    def _vorschau_bereich_bauen(self):
        center = ctk.CTkFrame(self, fg_color="transparent")
        center.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        center.grid_rowconfigure(1, weight=1)
        center.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(center, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        ctk.CTkLabel(head, text="Vorschau", font=("Roboto", 15, "bold")).pack(side="left")
        self._status_label = ctk.CTkLabel(head, text="Bereit", font=("Roboto", 12), text_color=TXT_DIM)
        self._status_label.pack(side="left", padx=10)

        btn_zurueck = ctk.CTkButton(head, text="‹", width=28, height=28, command=lambda: self._navigiere(-1))
        btn_zurueck.pack(side="left", padx=(6, 2))
        ToolTip(btn_zurueck, "Vorheriges Bild aus dem Ordner ansehen (auch schon analysierte erneut prüfen)")
        btn_weiter = ctk.CTkButton(head, text="›", width=28, height=28, command=lambda: self._navigiere(1))
        btn_weiter.pack(side="left", padx=2)
        ToolTip(btn_weiter, "Nächstes Bild aus dem Ordner ansehen (auch schon analysierte erneut prüfen)")

        ctk.CTkButton(head, text="Zurücksetzen", width=100, height=28,
                      command=self._einpassen).pack(side="right", padx=3)
        ctk.CTkButton(head, text="Einpassen", width=90, height=28,
                      command=self._einpassen).pack(side="right", padx=3)
        ctk.CTkButton(head, text="+", width=30, height=28,
                      command=lambda: self._zoom_schritt(1.25)).pack(side="right", padx=3)
        self._zoom_label = ctk.CTkLabel(head, text="100 %", width=48, font=("Roboto Mono", 11))
        self._zoom_label.pack(side="right", padx=3)
        ctk.CTkButton(head, text="−", width=30, height=28,
                      command=lambda: self._zoom_schritt(1 / 1.25)).pack(side="right", padx=3)

        preview = ctk.CTkFrame(center, fg_color=BG_FRAME)
        preview.grid(row=1, column=0, sticky="nsew")
        self.canvas = ctk.CTkCanvas(preview, bg="#0d0f10", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)
        # Maus-Events fürs Zoomen/Verschieben/Kalibrieren
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<Button-3>", lambda e: self._zoom_bei(1 / 1.25, e.x, e.y))
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)

        self._werkzeugleiste_bauen(preview)
        self._badge_label = ctk.CTkLabel(
            preview, text="Kein Ergebnis", fg_color=BG_TILE, corner_radius=14,
            padx=12, pady=6, font=("Roboto", 12))
        self._badge_label.place(relx=1.0, rely=1.0, anchor="se", x=-16, y=-16)

        info = ctk.CTkFrame(center, fg_color=BG_FRAME, height=40)
        info.grid(row=2, column=0, sticky="ew", pady=(9, 0))
        self._info_label = ctk.CTkLabel(info, text="Kein Bild geladen", font=("Roboto", 12),
                                         text_color=TXT_DIM)
        self._info_label.pack(anchor="w", padx=14, pady=10)

        prog = ctk.CTkFrame(center, fg_color=BG_FRAME)
        prog.grid(row=3, column=0, sticky="ew", pady=(9, 0))
        ctk.CTkLabel(prog, text="Stapelverarbeitung", font=("Roboto", 12)).pack(side="left", padx=14, pady=10)
        self._fortschritt_label = ctk.CTkLabel(prog, text="0 / 0", font=("Roboto Mono", 12))
        self._fortschritt_label.pack(side="right", padx=14)
        self._progressbar = ctk.CTkProgressBar(prog)
        self._progressbar.set(0)
        self._progressbar.pack(side="left", fill="x", expand=True, padx=8)

    def _werkzeugleiste_bauen(self, parent):
        frame = ctk.CTkFrame(parent, fg_color=BG_TILE, corner_radius=6)
        frame.place(x=10, y=10)
        self._werkzeug_buttons = {}
        werkzeuge = [
            ("move", "✥", "Verschieben: Bild mit gedrückter Maustaste ziehen."),
            ("zoom", "🔍", "Zoom: Klick = näher ran, Rechtsklick (überall) = weiter weg."),
            ("massstab", "📏", "Massstab setzen: zwei Punkte einer bekannten Strecke anklicken, "
                              "dann deren Länge in mm eingeben. Gilt danach für alle Bilder mit "
                              "derselben nativen Auflösung."),
        ]
        for i, (key, glyph, tip) in enumerate(werkzeuge):
            btn = ctk.CTkButton(frame, text=glyph, width=36, height=32,
                                 command=lambda k=key: self._werkzeug_waehlen(k))
            btn.grid(row=i, column=0, padx=4, pady=(4 if i == 0 else 2, 4 if i == len(werkzeuge) - 1 else 2))
            self._werkzeug_buttons[key] = btn
            ToolTip(btn, tip)
        self._werkzeug_waehlen("move")

    def _werkzeug_waehlen(self, key):
        self.werkzeug = key
        self._kalibrier_start = None
        for k, btn in self._werkzeug_buttons.items():
            btn.configure(fg_color=("#1F6AA5" if k == key else "transparent"))

    def _einstellungen_und_ergebnisse_bauen(self):
        right = ctk.CTkFrame(self, fg_color="transparent", width=372)
        right.grid(row=0, column=2, sticky="nsew", padx=(0, 12), pady=12)
        right.grid_propagate(False)
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        settings = ctk.CTkFrame(right, fg_color=BG_FRAME)
        settings.grid(row=0, column=0, sticky="nsew")
        settings.grid_rowconfigure(1, weight=1)
        settings.grid_columnconfigure(0, weight=1)

        kopf = ctk.CTkFrame(settings, fg_color="transparent")
        kopf.grid(row=0, column=0, padx=16, pady=(14, 8), sticky="ew")
        kopf.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(kopf, text="Einstellungen", font=("Roboto", 15, "bold")).grid(row=0, column=0, sticky="w")
        btn_standard = ctk.CTkButton(kopf, text="Standardwerte", width=112, height=26,
                                      fg_color="transparent", border_width=2, border_color=BORDER,
                                      command=self._standardwerte_zuruecksetzen)
        btn_standard.grid(row=0, column=1, sticky="e")
        ToolTip(btn_standard, "Setzt alle Regler/Schalter auf die kalibrierten Standardwerte "
                              "des aktuell gewählten Modus (Blob-Counter/Watershed) zurück.")

        scroll = ctk.CTkScrollableFrame(settings, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew", padx=8)
        scroll.grid_columnconfigure(0, weight=1)

        self.modus_var = ctk.CTkSegmentedButton(scroll, values=["Blob-Counter", "Watershed"],
                                                  command=self._modus_geaendert)
        self.modus_var.set("Watershed")
        self.modus_var.pack(fill="x", pady=(0, 12))

        # Regler, Reihenfolge = Reihenfolge in der Oberfläche
        self.regler = {}
        self.regler["Helligkeit"] = ReglerZeile(
            scroll, "Helligkeit", 0, -50, 50, formatter=lambda w: f"{w:+.0f}",
            on_change=self._live_vorschau_ausloesen,
            hilfe="Hellt das Bild vor der Analyse auf (positiv) oder ab (negativ).")
        self.regler["Kontrast"] = ReglerZeile(
            scroll, "Kontrast", 0, -50, 50, formatter=lambda w: f"{w:+.0f}",
            on_change=self._live_vorschau_ausloesen,
            hilfe="Erhöht/verringert den Bildkontrast vor der Analyse.")
        self.regler["Sättigung"] = ReglerZeile(
            scroll, "Sättigung", 0, -50, 50, formatter=lambda w: f"{w:+.0f}",
            on_change=self._live_vorschau_ausloesen,
            hilfe="Erhöht/verringert die Farbsättigung vor der Analyse. Wirkt vor der "
                  "Umwandlung in Graustufen — der Effekt auf die Erkennung ist meist gering, "
                  "da hauptsächlich Helligkeit/Kontrast in die Graustufen einfliessen.")
        self.regler["Min. Koloniegrösse"] = ReglerZeile(
            scroll, "Min. Koloniegrösse", self._min_flaeche_mm2_default("Watershed"), 0.05, 2.0,
            formatter=lambda w: f"{w:.2f} mm²", on_change=self._live_vorschau_ausloesen,
            hilfe="Flecken kleiner als dieser Wert (in mm²) werden ignoriert, z.B. Bildrauschen "
                  "oder Kratzer. Wird über die erkannte Schalengrösse in Pixel² umgerechnet.")
        self.regler["Kreisförmigkeit"] = ReglerZeile(
            scroll, "Kreisförmigkeit", watershed_engine.MIN_SOLIDITAET, 0.0, 1.0,
            formatter=lambda w: f"{w:.2f}", on_change=self._live_vorschau_ausloesen,
            hilfe="Wie kompakt/rund ein Fleck sein muss (genauer: Fläche ÷ Fläche der "
                  "konvexen Hülle = Solidität). 0 = beliebige Form, 1 = nur sehr kompakte Formen.")
        self.regler["Schwellenwert"] = ReglerZeile(
            scroll, "Schwellenwert", 128, 0, 255, formatter=lambda w: f"{w:.0f}",
            on_change=self._live_vorschau_ausloesen,
            hilfe="Verschiebt die Empfindlichkeit der automatischen (adaptiven) Schwelle. "
                  "128 = kalibrierter Standardwert. Höher = strenger (weniger, aber sicherere "
                  "Treffer), niedriger = empfindlicher (mehr Treffer, aber auch mehr Fehltreffer). "
                  "Keine feste 0–255-Grenze wie bei einem einfachen Schwarz/Weiss-Bild.")

        self.sw_kleine_objekte = ctk.CTkSwitch(scroll, text="Kleine Objekte entfernen", font=("Roboto", 12.5),
                                                command=self._live_vorschau_ausloesen)
        self.sw_kleine_objekte.select()
        self.sw_kleine_objekte.pack(fill="x", pady=5)
        ToolTip(self.sw_kleine_objekte,
                "Blendet Flecken aus, die kleiner sind als 'Min. Koloniegrösse'. "
                "Aus = auch winzige Flecken (z.B. Bildrauschen) werden mitgezählt.")

        self.sw_plattenrand = ctk.CTkSwitch(scroll, text="Plattenrand ausschliessen", font=("Roboto", 12.5),
                                             command=self._live_vorschau_ausloesen)
        self.sw_plattenrand.select()
        self.sw_plattenrand.pack(fill="x", pady=5)
        ToolTip(self.sw_plattenrand,
                "Erkennt die Petrischale automatisch und wertet nur den Innenbereich aus. "
                "Aus = das ganze Bild wird ausgewertet, keine mm/px-Kalibrierung möglich.")

        self.regler["Maskengrösse"] = ReglerZeile(
            scroll, "Maskengrösse", pc.MASKEN_SCHRUMPF * 100, 50, 110,
            formatter=lambda w: f"{w:.0f} %", on_change=self._live_vorschau_ausloesen,
            hilfe="Wie viel von der erkannten Schale ausgewertet wird, in % des erkannten "
                  "Schalenradius (nur wirksam, wenn 'Plattenrand ausschliessen' an ist). "
                  "Standard 78 % — kleiner schliesst mehr Rand/Reflexionsring aus (weniger "
                  "Fehltreffer, aber evtl. echte Randkolonien verloren), grösser bezieht mehr "
                  "vom Bildrand ein (mehr Randkolonien erkennbar, aber Risiko von Reflexionen/"
                  "Beschriftung als Fehltreffer).")

        self.sw_randkolonien = ctk.CTkSwitch(scroll, text="Kolonien am Rand zählen", font=("Roboto", 12.5),
                                              command=self._live_vorschau_ausloesen)
        self.sw_randkolonien.deselect()
        self.sw_randkolonien.pack(fill="x", pady=5)
        ToolTip(self.sw_randkolonien,
                "Kolonien, die den erkannten Schalenrand berühren (evtl. angeschnitten), "
                "werden bei 'Aus' (Standard) nicht mitgezählt.")

        lbl_modus = ctk.CTkLabel(scroll, text="Detektionsmodus", font=("Roboto", 12.5))
        lbl_modus.pack(anchor="w", pady=(10, 4))
        ToolTip(lbl_modus,
                "'Dunkel auf hell' = dunkle Kolonien auf hellem Agar (Standardfall). "
                "'Hell auf dunkel' = umgekehrt. 'Automatisch' schätzt anhand der mittleren "
                "Bildhelligkeit (einfache Heuristik, keine gelernte Erkennung).")
        self.opt_detektionsmodus = ctk.CTkOptionMenu(
            scroll, values=["Hell auf dunkel", "Dunkel auf hell", "Automatisch"],
            command=self._live_vorschau_ausloesen)
        self.opt_detektionsmodus.set("Dunkel auf hell")
        self.opt_detektionsmodus.pack(fill="x")

        footer = ctk.CTkFrame(settings, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 14))
        ctk.CTkButton(footer, text="Vorschau aktualisieren", fg_color="transparent",
                      border_width=2, border_color=BORDER, height=32,
                      command=self._vorschau_aktualisieren).pack(side="left", expand=True, fill="x", padx=(0, 5))
        ctk.CTkButton(footer, text="Analyse starten", height=32,
                      command=self._analyse_starten).pack(side="left", expand=True, fill="x", padx=(5, 0))

        results = ctk.CTkFrame(right, fg_color=BG_FRAME)
        results.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        results.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkLabel(results, text="Ergebnisse", font=("Roboto", 15, "bold")
                     ).grid(row=0, column=0, columnspan=3, padx=16, pady=(14, 8), sticky="w")

        big = ctk.CTkFrame(results, fg_color=BG_TILE)
        big.grid(row=1, column=0, columnspan=3, padx=16, sticky="ew")
        self._anzahl_label = ctk.CTkLabel(big, text="—", font=("Roboto Mono", 34, "bold"))
        self._anzahl_label.pack(side="left", padx=(14, 10), pady=12)
        ctk.CTkLabel(big, text="Kolonien im aktuellen Bild", font=("Roboto", 12.5),
                     text_color=TXT_DIM).pack(side="left")

        self._stat_labels = {}
        for i, label in enumerate(["Mittelwert", "Median", "Platten"]):
            tile = ctk.CTkFrame(results, fg_color=BG_TILE)
            tile.grid(row=2, column=i, padx=(16 if i == 0 else 4, 16 if i == 2 else 4), pady=10, sticky="ew")
            ctk.CTkLabel(tile, text=label, font=("Roboto", 10.5), text_color=TXT_DIM).pack(anchor="w", padx=11, pady=(9, 0))
            val = ctk.CTkLabel(tile, text="—", font=("Roboto Mono", 17))
            val.pack(anchor="w", padx=11, pady=(0, 9))
            self._stat_labels[label] = val
        ToolTip(self._stat_labels["Platten"].master,
                "Bezieht sich auf die aktuelle Auswahl (das eine Bild bzw. den aktuellen Ordner) — "
                "wird bei einer neuen Bild-/Ordnerauswahl zurückgesetzt.")

        self.log_box = ctk.CTkTextbox(results, height=74, fg_color=LOG_BG, font=("Roboto Mono", 11))
        self.log_box.grid(row=3, column=0, columnspan=3, padx=16, sticky="ew")
        self.log_box.insert("1.0", "[bereit] Noch keine Analyse durchgeführt.\n")
        self.log_box.configure(state="disabled")

        actions = ctk.CTkFrame(results, fg_color="transparent")
        actions.grid(row=4, column=0, columnspan=3, padx=16, pady=12, sticky="ew")
        ctk.CTkButton(actions, text="CSV exportieren", height=32,
                      command=self._csv_exportieren).pack(side="left", expand=True, fill="x", padx=(0, 5))
        ctk.CTkButton(actions, text="Ordner öffnen", height=32, width=120, fg_color="transparent",
                      border_width=2, border_color=BORDER,
                      command=self._ordner_oeffnen).pack(side="left", padx=(5, 0))

    def _bild_auswaehlen(self):
        pfad = filedialog.askopenfilename(
            title="Bild auswählen",
            filetypes=[("Bilddateien", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff"), ("Alle Dateien", "*.*")],
        )
        if pfad:
            self._lade_einzelbild(pfad)

    def _lade_einzelbild(self, pfad):
        self.batch_pfade = []
        self.ergebnisse = {}
        self.aktueller_pfad = pfad
        self.aktueller_ordner = str(Path(pfad).parent)
        self._pfad_anzeige_setzen(pfad)
        self._bilder_gefunden_label.configure(text="1 Bild ausgewählt (kein Ordner)")
        self._status_label.configure(text="Bereit · Bild 1 von 1")
        self._lade_rohbild_fuer_anzeige(pfad)
        self._aktualisiere_ergebnis_panel()
        self._fortschritt_setzen(0, 1)

    def _ordner_auswaehlen(self):
        pfad = filedialog.askdirectory(title="Ordner auswählen")
        if not pfad:
            return
        dateien = sorted(p for p in Path(pfad).iterdir()
                          if p.is_file() and p.suffix.lower() in UNTERSTUETZTE_ENDUNGEN)
        if not dateien:
            messagebox.showwarning("Keine Bilder gefunden",
                                    f"Im Ordner\n{pfad}\nwurden keine unterstützten Bilddateien gefunden.")
            return
        self.batch_pfade = dateien
        self.ergebnisse = {}
        self.aktueller_pfad = str(dateien[0])
        self.aktueller_ordner = pfad
        self._pfad_anzeige_setzen(pfad)
        self._bilder_gefunden_label.configure(text=f"{len(dateien)} Bilder gefunden")
        self._status_label.configure(text=f"Bereit · Bild 1 von {len(dateien)}")
        self._lade_rohbild_fuer_anzeige(self.aktueller_pfad)
        self._aktualisiere_ergebnis_panel()
        self._fortschritt_setzen(0, len(dateien))

    def _pfad_anzeige_setzen(self, text):
        self._pfad_anzeige.configure(state="normal")
        self._pfad_anzeige.delete(0, "end")
        self._pfad_anzeige.insert(0, text)
        self._pfad_anzeige.configure(state="disabled")

    def _lade_rohbild_fuer_anzeige(self, pfad):
        try:
            self._rohbild = pc.lade_bild(pfad)
        except Exception as exc:
            messagebox.showerror("Bild konnte nicht geladen werden", str(exc))
            self._rohbild = None
            return
        self._aktualisiere_infozeile(pfad)
        self._folgt_einpassung = True
        self.after(50, self._einpassen)

    def _native_aufloesung(self, pfad):
        if pfad in self._aufloesung_cache:
            return self._aufloesung_cache[pfad]
        try:
            with Image.open(pfad) as im:
                aufloesung = im.size
        except Exception:
            aufloesung = None
        self._aufloesung_cache[pfad] = aufloesung
        return aufloesung

    def _aktualisiere_infozeile(self, pfad):
        p = Path(pfad)
        aufloesung = self._native_aufloesung(pfad)
        try:
            groesse_mb = p.stat().st_size / (1024 * 1024)
        except OSError:
            groesse_mb = None

        ergebnis = self.ergebnisse.get(pfad)
        manuell = aufloesung in self.benutzer_kalibrierungen if aufloesung else False
        if manuell:
            mm_pro_px = self.benutzer_kalibrierungen[aufloesung]
        else:
            mm_pro_px = (ergebnis.get("mm_pro_px") if ergebnis else None) or self.letzte_mm_pro_px

        teile = [p.name]
        if aufloesung:
            teile.append(f"Auflösung: {aufloesung[0]} × {aufloesung[1]} px")
        if groesse_mb is not None:
            teile.append(f"Dateigrösse: {groesse_mb:.1f} MB")
        if mm_pro_px:
            quelle = "manuell" if manuell else "automatisch"
            teile.append(f"Skalierung: {mm_pro_px:.3f} mm/px ({quelle}, bei {pc.ARBEITSGROESSE}px Arbeitsgrösse)")
        else:
            teile.append("Skalierung: unbekannt (noch nicht analysiert)")
        self._info_label.configure(text="     ".join(teile))

    def _navigiere(self, richtung):
        if not self.batch_pfade:
            if not self.aktueller_pfad:
                messagebox.showinfo("Kein Ordner", "Nur innerhalb eines geladenen Ordners möglich.")
            return
        liste = [str(p) for p in self.batch_pfade]
        try:
            idx = liste.index(self.aktueller_pfad)
        except ValueError:
            idx = 0
        idx = max(0, min(len(liste) - 1, idx + richtung))
        self.aktueller_pfad = liste[idx]
        self._pfad_anzeige_setzen(self.aktueller_pfad)
        self._lade_rohbild_fuer_anzeige(self.aktueller_pfad)
        self._aktualisiere_ergebnis_panel()
        analysiert = " (bereits analysiert)" if self.ergebnisse.get(self.aktueller_pfad) else " (noch nicht analysiert)"
        self._status_label.configure(text=f"Bild {idx + 1} von {len(liste)}{analysiert}")

    def _beste_mm_pro_px(self):
        aufloesung = self._native_aufloesung(self.aktueller_pfad) if self.aktueller_pfad else None
        manuell = self.benutzer_kalibrierungen.get(aufloesung) if aufloesung else None
        return manuell or self.letzte_mm_pro_px or STANDARD_MM_PRO_PX

    def _einstellungen_sammeln(self):
        helligkeit = self.regler["Helligkeit"].get()
        kontrast = self.regler["Kontrast"].get()
        saettigung = self.regler["Sättigung"].get()
        kreisformigkeit = self.regler["Kreisförmigkeit"].get()
        schwellenwert = self.regler["Schwellenwert"].get()
        schwellen_offset = (schwellenwert - 128) / 255 * 40  # 128 = Mitte = kein Offset, 40 nach Gefühl

        kleine_objekte = bool(self.sw_kleine_objekte.get())
        plattenrand = bool(self.sw_plattenrand.get())
        randkolonien = bool(self.sw_randkolonien.get())
        detektionsmodus = DETEKTIONSMODUS_UI_ZU_INTERN.get(
            self.opt_detektionsmodus.get(), "dunkel_auf_hell")

        min_flaeche_mm2 = self.regler["Min. Koloniegrösse"].get()
        min_flaeche_px = pc.mm2_zu_px2(min_flaeche_mm2, self._beste_mm_pro_px())
        masken_groesse = self.regler["Maskengrösse"].get() / 100.0

        kwargs = dict(
            helligkeit=helligkeit, kontrast=kontrast, saettigung=saettigung,
            plattenrand_ausschliessen=plattenrand,
            kleine_objekte_entfernen=kleine_objekte,
            rand_kolonien_zaehlen=randkolonien,
            detektionsmodus=detektionsmodus,
            min_flaeche=min_flaeche_px,
            masken_groesse=masken_groesse,
        )
        # die zwei Engines heissen die Parameter unterschiedlich
        if self.modus_var.get() == "Watershed":
            kwargs["min_soliditaet"] = kreisformigkeit
            kwargs["schwelle_c_offset"] = schwellen_offset
        else:
            kwargs["min_konvexitaet"] = kreisformigkeit
            kwargs["schwellen_fenster_offset"] = schwellen_offset
        return kwargs

    def _min_flaeche_mm2_default(self, modus):
        mm_pro_px = self._beste_mm_pro_px() if hasattr(self, "aktueller_pfad") else STANDARD_MM_PRO_PX
        px2 = watershed_engine.MIN_FLAECHE if modus == "Watershed" else blob_engine.BLOB_MIN_FLAECHE
        return max(0.05, min(2.0, px2 * mm_pro_px ** 2))

    def _modus_geaendert(self, modus):

        default_solid = watershed_engine.MIN_SOLIDITAET if modus == "Watershed" else blob_engine.BLOB_MIN_KONVEXITAET
        self.regler["Kreisförmigkeit"].set(default_solid)
        self.regler["Min. Koloniegrösse"].set(self._min_flaeche_mm2_default(modus))
        self._live_vorschau_ausloesen()

    def _standardwerte_zuruecksetzen(self):
        modus = self.modus_var.get()
        self.regler["Helligkeit"].set(0)
        self.regler["Kontrast"].set(0)
        self.regler["Sättigung"].set(0)
        self.regler["Schwellenwert"].set(128)
        self.regler["Kreisförmigkeit"].set(
            watershed_engine.MIN_SOLIDITAET if modus == "Watershed" else blob_engine.BLOB_MIN_KONVEXITAET)
        self.regler["Min. Koloniegrösse"].set(self._min_flaeche_mm2_default(modus))
        self.regler["Maskengrösse"].set(pc.MASKEN_SCHRUMPF * 100)
        self.sw_kleine_objekte.select()
        self.sw_plattenrand.select()
        self.sw_randkolonien.deselect()
        self.opt_detektionsmodus.set("Dunkel auf hell")
        self._log(f"[{datetime.now().strftime('%H:%M:%S')}] Einstellungen ({modus}) auf Standardwerte zurückgesetzt.")
        self._live_vorschau_ausloesen()

    def _live_vorschau_ausloesen(self, *_args):
        if not getattr(self, "sw_live_vorschau", None) or not self.sw_live_vorschau.get():
            return
        if not self.aktueller_pfad or self._laeuft_analyse:
            return
        if self._live_debounce_id is not None:
            self.after_cancel(self._live_debounce_id)
        # kurz warten statt bei jedem Slider-Tick neu zu rechnen
        self._live_debounce_id = self.after(400, self._vorschau_aktualisieren)

    def _ziel_engine(self):
        return watershed_engine if self.modus_var.get() == "Watershed" else blob_engine

    def _analyse_starten(self):
        if self._laeuft_analyse:
            return
        ziel_liste = list(self.batch_pfade) if self.batch_pfade else (
            [self.aktueller_pfad] if self.aktueller_pfad else [])
        if not ziel_liste:
            messagebox.showwarning("Kein Bild", "Bitte zuerst ein Bild oder einen Ordner auswählen.")
            return

        engine = self._ziel_engine()
        einstellungen = self._einstellungen_sammeln()
        self._laeuft_analyse = True
        self._abschluss_modus = "batch"
        self._analyse_queue = queue.Queue()
        self._analyse_gesamt = len(ziel_liste)
        self._analyse_fertig = 0
        self._fortschritt_setzen(0, self._analyse_gesamt)
        self._status_label.configure(text=f"Analysiert Bild 1 von {self._analyse_gesamt}")

        threading.Thread(target=self._analyse_worker, args=(engine, ziel_liste, einstellungen),
                          daemon=True).start()
        self.after(50, self._analyse_queue_abfragen)

    def _vorschau_aktualisieren(self):
        if self._laeuft_analyse:
            return
        if not self.aktueller_pfad:
            messagebox.showwarning("Kein Bild", "Bitte zuerst ein Bild auswählen.")
            return
        engine = self._ziel_engine()
        einstellungen = self._einstellungen_sammeln()
        self._laeuft_analyse = True
        self._abschluss_modus = "einzeln"
        self._analyse_queue = queue.Queue()
        self._analyse_gesamt = 1
        self._analyse_fertig = 0
        self._status_label.configure(text="Vorschau wird berechnet …")

        threading.Thread(target=self._analyse_worker, args=(engine, [self.aktueller_pfad], einstellungen),
                          daemon=True).start()
        self.after(50, self._analyse_queue_abfragen)

    def _analyse_worker(self, engine, pfade, einstellungen):
        # läuft im Hintergrund-Thread, Ergebnisse gehen über die Queue zurück - tkinter mag
        # keine Widget-Updates aus einem fremden Thread
        for pfad in pfade:
            try:
                ergebnis = engine.analysiere(str(pfad), **einstellungen)
                self._analyse_queue.put(("ok", str(pfad), ergebnis))
            except Exception as exc:
                self._analyse_queue.put(("fehler", str(pfad), str(exc)))
        self._analyse_queue.put(("fertig", None, None))

    def _analyse_queue_abfragen(self):
        try:
            while True:
                status, pfad, payload = self._analyse_queue.get_nowait()
                if status == "ok":
                    self._analyse_fertig += 1
                    aufloesung = self._native_aufloesung(pfad)
                    manuell = self.benutzer_kalibrierungen.get(aufloesung) if aufloesung else None
                    # manuelle Kalibrierung hat Vorrang vor der automatisch erkannten
                    if manuell:
                        payload["mm_pro_px"] = manuell
                    elif payload.get("mm_pro_px"):
                        self.letzte_mm_pro_px = payload["mm_pro_px"]
                    zeitstempel = datetime.now().strftime("%H:%M:%S")
                    self.ergebnisse[pfad] = {**payload, "zeit": zeitstempel, "modus": self.modus_var.get()}
                    self._log(f"[{zeitstempel}] {Path(pfad).name}  → {payload['anzahl']} Kolonien")
                    self.aktueller_pfad = pfad
                    self._render_vorschau()
                    self._aktualisiere_infozeile(pfad)
                    self._aktualisiere_ergebnis_panel()
                    self._fortschritt_setzen(self._analyse_fertig, self._analyse_gesamt)
                    if self._analyse_fertig < self._analyse_gesamt:
                        self._status_label.configure(
                            text=f"Analysiert Bild {self._analyse_fertig + 1} von {self._analyse_gesamt}")
                elif status == "fehler":
                    zeitstempel = datetime.now().strftime("%H:%M:%S")
                    self._log(f"[{zeitstempel}] FEHLER bei {Path(pfad).name}: {payload}")
                elif status == "fertig":
                    self._laeuft_analyse = False
                    if self._abschluss_modus == "batch":
                        self._log(f"[{datetime.now().strftime('%H:%M:%S')}] "
                                   f"{self.modus_var.get()}, Schwellenwert {self.regler['Schwellenwert'].get():.0f}")
                        self._status_label.configure(
                            text=f"Analyse abgeschlossen · Bild {self._analyse_gesamt} von {self._analyse_gesamt}")
                    else:
                        self._status_label.configure(text="Vorschau aktualisiert")
                    return
        except queue.Empty:
            pass
        # noch nicht fertig -> in 50ms nochmal nachschauen
        if self._laeuft_analyse:
            self.after(50, self._analyse_queue_abfragen)

    def _aktualisiere_ergebnis_panel(self):
        ergebnis = self.ergebnisse.get(self.aktueller_pfad)
        self._anzahl_label.configure(text=str(ergebnis["anzahl"]) if ergebnis else "—")

        anzahlen = [e["anzahl"] for e in self.ergebnisse.values()]
        if anzahlen:
            self._stat_labels["Mittelwert"].configure(text=f"{statistics.mean(anzahlen):.1f}")
            self._stat_labels["Median"].configure(text=f"{statistics.median(anzahlen):.0f}")
        else:
            self._stat_labels["Mittelwert"].configure(text="—")
            self._stat_labels["Median"].configure(text="—")
        self._stat_labels["Platten"].configure(text=str(len(anzahlen)))

    def _fortschritt_setzen(self, fertig, gesamt):
        self._fortschritt_label.configure(text=f"{fertig} / {gesamt}")
        self._progressbar.set((fertig / gesamt) if gesamt else 0)

    def _log(self, zeile):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", zeile + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _on_canvas_resize(self, _event=None):
        if self._folgt_einpassung:
            self._einpassen()
        else:
            self._render_vorschau()

    def _einpassen(self):
        if self._rohbild is None:
            self._render_vorschau()
            return
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        h, w = self._rohbild.shape[:2]
        self.zoom = max(min(cw / w, ch / h) * 0.96, 0.02)  # etwas Rand lassen, 0.96 statt 1.0
        self.pan_x = (cw - w * self.zoom) / 2
        self.pan_y = (ch - h * self.zoom) / 2
        self._folgt_einpassung = True
        self._render_vorschau()

    def _zoom_schritt(self, faktor):
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        self._zoom_bei(faktor, cw / 2, ch / 2)

    def _zoom_bei(self, faktor, cx, cy):
        if self._rohbild is None:
            return
        # Punkt unter dem Cursor soll beim Zoomen an derselben Stelle bleiben
        alter_zoom = self.zoom
        neuer_zoom = max(0.02, min(8.0, alter_zoom * faktor))
        bx = (cx - self.pan_x) / alter_zoom
        by = (cy - self.pan_y) / alter_zoom
        self.zoom = neuer_zoom
        self.pan_x = cx - bx * neuer_zoom
        self.pan_y = cy - by * neuer_zoom
        self._folgt_einpassung = False
        self._render_vorschau()

    def _on_mousewheel(self, event):
        faktor = 1.1 if event.delta > 0 else (1 / 1.1)
        self._zoom_bei(faktor, event.x, event.y)

    def _bild_koordinate(self, sx, sy):
        return (sx - self.pan_x) / self.zoom, (sy - self.pan_y) / self.zoom

    def _on_canvas_press(self, event):
        if self.werkzeug == "move":
            self._drag_start = (event.x, event.y, self.pan_x, self.pan_y)
        elif self.werkzeug == "zoom":
            self._zoom_bei(1.25, event.x, event.y)
        elif self.werkzeug == "massstab":
            self._kalibrier_klick(event.x, event.y)

    def _on_canvas_drag(self, event):
        if self.werkzeug != "move" or not self._drag_start:
            return
        sx, sy, px, py = self._drag_start
        self.pan_x = px + (event.x - sx)
        self.pan_y = py + (event.y - sy)
        self._folgt_einpassung = False
        self._render_vorschau()

    def _on_canvas_release(self, _event=None):
        self._drag_start = None

    def _kalibrier_klick(self, sx, sy):
        if self._rohbild is None or not self.aktueller_pfad:
            return
        bx, by = self._bild_koordinate(sx, sy)
        if self._kalibrier_start is None:
            # erster von zwei Klicks, der zweite kommt beim nächsten Aufruf
            self._kalibrier_start = (bx, by)
            self._log("Massstab: ersten Punkt gesetzt — jetzt den zweiten Punkt derselben "
                      "bekannten Strecke anklicken.")
            return
        x0, y0 = self._kalibrier_start
        self._kalibrier_start = None
        pixel_laenge = ((bx - x0) ** 2 + (by - y0) ** 2) ** 0.5
        if pixel_laenge < 2:
            self._log("Massstab: Strecke zu kurz, abgebrochen.")
            return
        laenge_mm = simpledialog.askfloat(
            "Massstab setzen", "Länge dieser Strecke in mm (z.B. 90 für 9 cm):",
            minvalue=0.1, parent=self)
        if not laenge_mm:
            self._log("Massstab: abgebrochen (keine Länge eingegeben).")
            return
        aufloesung = self._native_aufloesung(self.aktueller_pfad)
        if aufloesung is None:
            self._log("Massstab: native Auflösung konnte nicht bestimmt werden, nicht gespeichert.")
            return
        mm_pro_px = laenge_mm / pixel_laenge
        # pro Auflösung speichern, andere Kameras/Einstellungen haben ja einen anderen Massstab
        self.benutzer_kalibrierungen[aufloesung] = mm_pro_px
        self.letzte_mm_pro_px = mm_pro_px
        self._log(f"[{datetime.now().strftime('%H:%M:%S')}] Massstab gesetzt: {mm_pro_px:.4f} mm/px "
                  f"für alle Bilder mit {aufloesung[0]}×{aufloesung[1]} px (Anzeige + "
                  f"'Min. Koloniegrösse'-Umrechnung).")
        if self.ergebnisse.get(self.aktueller_pfad):
            self.ergebnisse[self.aktueller_pfad]["mm_pro_px"] = mm_pro_px
        self._aktualisiere_infozeile(self.aktueller_pfad)
        self._render_vorschau()

    def _render_vorschau(self):
        canvas = self.canvas
        canvas.delete("all")
        self._zoom_label.configure(text=f"{self.zoom * 100:.0f} %")

        if self._rohbild is None:
            cw = max(canvas.winfo_width(), 1)
            ch = max(canvas.winfo_height(), 1)
            canvas.create_text(cw / 2, ch / 2, text="Kein Bild geladen —\n„Bild auswählen“ oder „Ordner auswählen“ klicken",
                                fill="#8E9599", justify="center", font=("Roboto", 13))
            self._aktualisiere_badge(None)
            return

        ergebnis = self.ergebnisse.get(self.aktueller_pfad)
        basis = ergebnis["bild_basis"] if ergebnis else self._rohbild
        h, w = basis.shape[:2]
        ziel_w, ziel_h = max(int(w * self.zoom), 1), max(int(h * self.zoom), 1)
        # cv2 arbeitet in BGR, PIL erwartet RGB
        rgb = cv2.cvtColor(basis, cv2.COLOR_BGR2RGB)
        pil_bild = Image.fromarray(rgb).resize((ziel_w, ziel_h), Image.BILINEAR)
        self._tk_img = ImageTk.PhotoImage(pil_bild)
        canvas.create_image(self.pan_x, self.pan_y, anchor="nw", image=self._tk_img)

        if ergebnis:
            mx, my = ergebnis["maske_mittelpunkt"]
            mr = ergebnis["maske_radius"]
            sx, sy = self.pan_x + mx * self.zoom, self.pan_y + my * self.zoom
            canvas.create_oval(sx - mr * self.zoom, sy - mr * self.zoom,
                                sx + mr * self.zoom, sy + mr * self.zoom,
                                outline="#1E3A8A", dash=(4, 3), width=5)
            for (x, y, r) in ergebnis["mittelpunkte"]:
                sx, sy = self.pan_x + x * self.zoom, self.pan_y + y * self.zoom
                sr = max(r * self.zoom, 3)
                canvas.create_oval(sx - sr, sy - sr, sx + sr, sy + sr,
                                    outline=ACCENT_GREEN, width=max(1.5 * self.zoom, 1))

        self._zeichne_massstab(canvas, ergebnis)
        self._aktualisiere_badge(ergebnis)

    def _zeichne_massstab(self, canvas, ergebnis):
        cw, ch = max(canvas.winfo_width(), 1), max(canvas.winfo_height(), 1)
        x0, y0 = 16, ch - 22
        aufloesung = self._native_aufloesung(self.aktueller_pfad) if self.aktueller_pfad else None
        manuell = self.benutzer_kalibrierungen.get(aufloesung) if aufloesung else None
        mm_pro_px = manuell or (ergebnis.get("mm_pro_px") if ergebnis else None)
        if mm_pro_px:
            # Balken steht für 10mm, Länge begrenzt damit er nie riesig oder unsichtbar wird
            laenge = max(20, min((10.0 / mm_pro_px) * self.zoom, 220))
            canvas.create_line(x0, y0, x0 + laenge, y0, fill="#DCE4EE", width=2)
            canvas.create_line(x0, y0 - 5, x0, y0 + 5, fill="#DCE4EE", width=2)
            canvas.create_line(x0 + laenge, y0 - 5, x0 + laenge, y0 + 5, fill="#DCE4EE", width=2)
            beschriftung = "10 mm (manuell)" if manuell else "10 mm"
            canvas.create_text(x0, y0 + 13, text=beschriftung, fill="#8E9599", anchor="w", font=("Roboto", 10))
        else:
            canvas.create_text(x0, y0, text="Massstab unbekannt (Plattenrand-Erkennung aus/fehlgeschlagen)",
                                fill="#8E9599", anchor="w", font=("Roboto", 10))

    def _aktualisiere_badge(self, ergebnis):
        if ergebnis:
            self._badge_label.configure(text=f"●  {ergebnis['anzahl']} Kolonien erkannt", text_color=ACCENT_GREEN)
        else:
            self._badge_label.configure(text="●  Kein Ergebnis", text_color=TXT_DIM)

    def _ui_skalierung_geaendert(self, wert):
        try:
            prozent = float(wert.replace("%", "").strip())
            ctk.set_widget_scaling(SKALIERUNG_BASIS * prozent / 100.0)
        except ValueError:
            pass

    def _csv_exportieren(self):
        if not self.ergebnisse:
            messagebox.showwarning("Keine Ergebnisse", "Es liegen noch keine Analyseergebnisse vor.")
            return
        ziel = filedialog.asksaveasfilename(
            title="CSV exportieren",
            initialfile=f"kolonien_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not ziel:
            return
        try:
            # utf-8 wegen der Umlaute in Dateinamen/Pfaden
            with open(ziel, "w", newline="", encoding="utf-8") as datei:
                writer = csv.DictWriter(datei, fieldnames=["dateiname", "pfad", "modus", "anzahl", "zeit"])
                writer.writeheader()
                for pfad, ergebnis in self.ergebnisse.items():
                    writer.writerow({
                        "dateiname": Path(pfad).name,
                        "pfad": pfad,
                        "modus": ergebnis.get("modus", ""),
                        "anzahl": ergebnis["anzahl"],
                        "zeit": ergebnis.get("zeit", ""),
                    })
            self._log(f"[{datetime.now().strftime('%H:%M:%S')}] CSV exportiert: {ziel}")
        except OSError as exc:
            messagebox.showerror("Export fehlgeschlagen", str(exc))

    def _ordner_oeffnen(self):
        # TODO: mac/linux Zweig nur oberflächlich getestet, os.startfile ist der Hauptpfad
        if not self.aktueller_ordner:
            messagebox.showwarning("Kein Ordner", "Es ist noch kein Bild/Ordner ausgewählt.")
            return
        try:
            if sys.platform == "win32":
                os.startfile(self.aktueller_ordner)
            elif sys.platform == "darwin":
                subprocess.run(["open", self.aktueller_ordner], check=True)
            else:
                subprocess.run(["xdg-open", self.aktueller_ordner], check=True)
        except Exception as exc:
            messagebox.showerror("Ordner konnte nicht geöffnet werden", str(exc))

if __name__ == "__main__":
    app = KolonieZaehlerApp()
    app.mainloop()
