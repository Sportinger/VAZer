# Desktop App

## Ziel

Der Browser-Prototyp war nur ein schneller Interaktionscheck.
Der naechste sinnvolle Schritt fuer VAZer ist eine native Desktop-App.

Der aktuelle Minimalstand:

- PySide6-basierte Windows-Desktop-App
- lokale Dateien direkt vom Dateisystem
- Drag-and-drop oder Dateiauswahl
- Projektliste
- Jobliste
- Fortschritt
- Pause / Resume
- Hintergrundjob schreibt aktuell `sync_map.json`

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
2. Master und Kameras heuristisch erkennen
3. pro Kamera Audio-Sync gegen den Master laufen lassen
4. `sync_map.partial.json` und `sync_map.json` schreiben

## Was noch fehlt

- echtes Projekt-Setup fuer Rollen wie `totale`, `close`, `halbtotale`
- Transcript-, Analyse- und Plan-Schritte im Desktop-Runner
- Preview/Player
- manuelle Overrides
- Packaging zu einer echten `.exe`

## Naechster Packaging-Schritt

Wenn die Desktop-App stabil genug ist, ist der naechste praktische Schritt:

- `PyInstaller` oder `Nuitka` fuer eine erste Windows-EXE

Das ist noch nicht Teil dieses Commits.
