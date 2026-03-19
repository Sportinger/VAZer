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
- per-Datei Fortschrittsbalken, inklusive optionaler Unterfortschritte
- ein echter `Pause` / `Weiter`-Flow im Desktop
- ein minimaler `VAZ`-Startbutton
- eine Phasenleiste oben fuer den gesamten Theater-VAZ-Flow
- die Phasen-Badges laufen von unten nach oben voll statt eines klassischen Prozentbalkens
- die Analyse-Phase kann optional `Global` und `Local` getrennt beschriften, wenn der Job das hergibt
- Rollen-Review-Stopp vor dem Sync
- Transcript und technische Analyse laufen jetzt parallel zum Sync-/Media-Block
- Hintergrundjob laeuft jetzt bis zum geschnittenen FHD-Render durch
- pausierte Laeufe koennen nach einem App-Neustart aus vorhandenen Artefakten als neuer Job wieder aufgenommen werden

## Start

```powershell
$env:PYTHONPATH='src'
python -m vazer desktop
```

Alternativ direkt ueber den Launcher im Repo:

```text
VAZer Desktop.cmd
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
- kein separater HTTP-Server im Desktop-Modus
- bei Referenzprojekten liegen die wichtigsten Artefakte jetzt standardmaessig in einem Unterordner `VAZer` im gemeinsamen Quellordner
- darunter trennt VAZer jetzt sauber in `VAZer\artifacts`, `VAZer\output` und `VAZer\vazer.state.json`
- vorhandene Artefakte im `VAZer`-Ordner werden nach Moeglichkeit wiederverwendet
- vorhandene Transcript-/Analysis-/Sync-Artefakte aus aelteren Workspace-Projekten oder aus alten Root-Dateien koennen in den `VAZer`-Ordner uebernommen und dort weiterverwendet werden
- der finale Render landet bei Desktop-Referenzprojekten standardmaessig in `VAZer\output`
- wenn die Quelldateien aus verschiedenen Ordnern kommen, faellt VAZer auf den Projekt-Workspace als Output-Ziel zurueck
- offensichtliche Sidecar-/Hidden-Dateien wie `._*.MXF` werden beim Import ignoriert
- beim Schliessen werden laufende Jobs abgebrochen und registrierte `ffmpeg`/`ffprobe`-Prozesse beendet
- wenn bereits VAZer-Daten im Medienordner vorhanden sind, fragt die Desktop-App jetzt vor dem Start: `Fortsetzen`, `Neu beginnen` oder `Abbrechen`

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
   - die UI akzeptiert spaeter optional `analysis_pass=global|local` fuer klarere Beschriftung
   - pro Datei koennen mehrere kleine Fortschrittsbalken als `ui_sub_progress` erscheinen
9. chunked AI-Draft ueber die ganze Show bauen
10. Cuts validieren und lokal reparieren
11. FHD-Render ausgeben

Artefakte:

- `VAZer\artifacts\vazer.camera_roles.json`
- `VAZer\artifacts\vazer.sync_map.json`
- `VAZer\artifacts\vazer.sync_map.partial.json`
- `VAZer\artifacts\<master>.transcript.json`
- `VAZer\artifacts\vazer.analysis_map.json`
- `VAZer\artifacts\vazer.visual_packet.json`
- `VAZer\artifacts\vazer.cut_plan.ai.json`
- `VAZer\artifacts\vazer.cut_validation.json`
- `VAZer\artifacts\vazer.cut_plan.repaired.json`
- `VAZer\output\*.premiere.xml`
- `VAZer\output\*.mp4`
- `VAZer\vazer.state.json` als Start-/Resume-Hinweis fuer den letzten bekannten Zustand

## Was noch fehlt

- Preview/Player
- manuelle Overrides ueber die Rollenpruefung hinaus
- Packaging zu einer echten `.exe`

## Naechster Packaging-Schritt

Wenn die Desktop-App stabil genug ist, ist der naechste praktische Schritt:

- `PyInstaller` oder `Nuitka` fuer eine erste Windows-EXE

Das ist noch nicht Teil dieses Commits.
