# Desktop App

## Ziel

Der Browser-Prototyp war nur ein schneller Interaktionscheck.
Der naechste sinnvolle Schritt fuer VAZer ist eine native Desktop-App.

Der aktuelle Minimalstand:

- PySide6-basierte Windows-Desktop-App
- lokale Dateien direkt vom Dateisystem
- Drag-and-drop ueber das ganze Fenster
- eine reduzierte Dateiliste plus Preview
- Mittelframe-Preview pro gewaehlter Datei
- ein minimaler `VAZ`-Startbutton
- eine Phasenleiste oben fuer den gesamten Theater-VAZ-Flow
- die Phasen-Badges laufen von unten nach oben voll statt eines klassischen Prozentbalkens
- Rollen-Review-Stopp vor dem Sync
- Hintergrundjob laeuft jetzt bis zum geschnittenen FHD-Render durch

## Start

```powershell
$env:PYTHONPATH='src'
python -m vazer desktop
```

Optional:

```powershell
$env:PYTHONPATH='src'
python -m vazer desktop --workspace .\out\desktop
```

## Wichtige Designentscheidung

Im Desktop-Fall werden Dateien aktuell direkt referenziert.

Das heisst:

- kein Browser-Upload
- keine Kopie ins Workspace nur fuer den Import
- Projektartefakte liegen trotzdem im lokalen VAZer-Workspace
- der finale Render landet bei Desktop-Referenzprojekten standardmaessig im gemeinsamen Quellordner
- wenn die Quelldateien aus verschiedenen Ordnern kommen, faellt VAZer auf den Projekt-Workspace als Output-Ziel zurueck
- offensichtliche Sidecar-/Hidden-Dateien wie `._*.MXF` werden beim Import ignoriert

Das ist fuer lange 4K-Theatermitschnitte deutlich sinnvoller als ein Browser-Upload.

## Aktueller Job-Umfang

Der Desktop-Job macht bisher:

1. Dateien pruefen
2. Master und Kameras erkennen
3. pro Kamera einen Mittelframe exportieren
4. AI-Rollen in einem Call bestimmen (`totale` / `halbtotale` / `close`)
5. Rollen im Desktop pruefbar anzeigen
6. erst nach `Weiter` Audio-Sync gegen den Master starten
7. Masterspur mit `whisper-1` transkribieren
8. billige technische Analyse rechnen
9. chunked AI-Draft ueber die ganze Show bauen
10. Cuts validieren und lokal reparieren
11. FHD-Render ausgeben

Artefakte:

- `camera_roles.json`
- `sync_map.json`
- `transcript.json`
- `analysis_map.json`
- `visual_packet.json`
- `cut_plan.ai.json`
- `cut_validation.json`
- `cut_plan.repaired.json`
- finaler FHD-Render im Projektordner

## Was noch fehlt

- Preview/Player
- manuelle Overrides ueber die Rollenpruefung hinaus
- Packaging zu einer echten `.exe`

## Naechster Packaging-Schritt

Wenn die Desktop-App stabil genug ist, ist der naechste praktische Schritt:

- `PyInstaller` oder `Nuitka` fuer eine erste Windows-EXE

Das ist noch nicht Teil dieses Commits.
