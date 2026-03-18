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
- Fortschritt fuer den aktiven Lauf
- Rollen-Review-Stopp vor dem Sync
- Hintergrundjob schreibt aktuell `camera_roles.json` und `sync_map.json`

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

Das ist fuer lange 4K-Theatermitschnitte deutlich sinnvoller als ein Browser-Upload.

## Aktueller Job-Umfang

Der Desktop-Job macht bisher:

1. Dateien pruefen
2. Master und Kameras erkennen
3. pro Kamera einen Mittelframe exportieren
4. AI-Rollen in einem Call bestimmen (`totale` / `halbtotale` / `close`)
5. Rollen im Desktop pruefbar anzeigen
6. erst nach `Weiter` Audio-Sync gegen den Master starten
7. `camera_roles.json`, `sync_map.partial.json` und `sync_map.json` schreiben

## Was noch fehlt

- Transcript-, Analyse- und Plan-Schritte im Desktop-Runner
- Preview/Player
- manuelle Overrides ueber die Rollenpruefung hinaus
- Packaging zu einer echten `.exe`

## Naechster Packaging-Schritt

Wenn die Desktop-App stabil genug ist, ist der naechste praktische Schritt:

- `PyInstaller` oder `Nuitka` fuer eine erste Windows-EXE

Das ist noch nicht Teil dieses Commits.
