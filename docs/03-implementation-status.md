# Implementierungsstand

## Stand vom 2026-03-17

Der erste lauffaehige Code liegt jetzt als Python-CLI im Repo.

Warum Python fuer diesen Start:

- Audio-Sync, Korrelation und Drift-Fit sind damit deutlich einfacher als in TypeScript
- `numpy` reicht fuer die erste DSP-Schicht
- `ffmpeg` und `ffprobe` bleiben die externen Medien-Backends

## Aktuelle Dateien

- `pyproject.toml`: Python-Projektdefinition
- `src/vazer/cli.py`: CLI-Einstieg
- `src/vazer/fftools.py`: `ffprobe`- und `ffmpeg`-Bruecken
- `src/vazer/sync.py`: Stream-Auswahl, Coarse Sync, Anchor-Fit, Drift-Modell
- `src/vazer/analysis.py`: speech-like Master-Aktivitaet und technische Kameraanalyse
- `src/vazer/cut_plan.py`: baseline- und signal-aware Planer
- `src/vazer/render.py`: ffmpeg-Scaffold aus `cut_plan`
- `src/vazer/transcript.py`: Loader fuer externe Transcript-Artefakte

## Bereits funktional

- Media-Probe fuer Master und Kamera
- Kamera-Audiostreams auf Aktivitaet pruefen
- offensichtliche Duplikat-Spuren erkennen
- einen groben Startpunkt auf der Master-Timeline finden
- mehrere Fine-Sync-Anker messen
- lineares Zeitmodell `source_time = speed * master_time + offset` fitten
- mehrere Kamera-Dateien in ein gemeinsames `sync_map.json` schreiben
- ein `analysis_map.json` aus `sync_map` erzeugen
- ein baseline oder signal-aware `cut_plan.json` aus `sync_map` erzeugen
- einen ffmpeg-Render-Scaffold aus `cut_plan` erzeugen

## Smoke-Test mit den Beispiel-Dateien

Verwendete Dateien:

- `D:\VAZ_Chaos\Medien\Clip0004.MXF`
- `D:\VAZ_Chaos\Medien\VAZ Chaos Close.MXF`
- `D:\VAZ_Chaos\Medien\VAZ Chaos HT.MXF`
- `D:\VAZ_Chaos\Medien\Chaos_Vaz.wav`

Der aktuelle Sync-Lauf liefert fuer die Testdateien:

- alle drei MXF-Kameras bestehen jetzt die aktuellen Sync-Quality-Gates
- `Clip0004.MXF` wird ueber den neuen Rescue-Pfad auf ca. `-1062.798 s` zur Master-Timeline gelegt
- `VAZ Chaos Close.MXF` liegt bei ca. `-153.698 s`
- `VAZ Chaos HT.MXF` liegt bei ca. `-1046.204 s`
- die neuen Fits haben sehr kleine Residuen und durchgehend `6/6` akzeptierte Anchors
- die technische Analyse erzeugt ein `analysis_map` mit `382` speech-like Master-Segmenten
- der signal-aware `cut_plan` kann damit wieder auf allen drei Kameras arbeiten
- der Render-Scaffold fuer diesen Plan wird sauber erzeugt, ein voller ffmpeg-Smoke-Test ist bei langen 4K-H.264-Quellen aber noch bewusst teuer

Das ist noch kein finaler Produktionswert, aber ein belastbarer erster Kern fuer `sync_map -> analysis_map -> cut_plan -> render scaffold`.

## Arbeitsweise zum lokalen Test

```powershell
$env:PYTHONPATH='src'
python -m vazer sync probe --master 'D:\VAZ_Chaos\Medien\Chaos_Vaz.wav' --camera 'D:\VAZ_Chaos\Medien\Clip0004.MXF'
```

JSON-Ausgabe:

```powershell
$env:PYTHONPATH='src'
python -m vazer sync probe --master <master> --camera <camera> --json
```

Batch-Export:

```powershell
$env:PYTHONPATH='src'
python -m vazer sync map --master <master> --camera <cam1> --camera <cam2> --out .\artifacts\sync_map.json
```

Baseline-Plan:

```powershell
$env:PYTHONPATH='src'
python -m vazer plan baseline --sync-map .\artifacts\sync_map.json --out .\artifacts\cut_plan.json
```

Technische Analyse:

```powershell
$env:PYTHONPATH='src'
python -m vazer analyze technical --sync-map .\artifacts\sync_map.json --out .\artifacts\analysis_map.json
```

Signal-aware Plan:

```powershell
$env:PYTHONPATH='src'
python -m vazer plan baseline --sync-map .\artifacts\sync_map.json --analysis .\artifacts\analysis_map.json --out .\artifacts\cut_plan.json
```

Render-Scaffold:

```powershell
$env:PYTHONPATH='src'
python -m vazer render scaffold --cut-plan .\artifacts\cut_plan.json --output-media .\out\final.mp4 --out-dir .\artifacts\render
```

## Was noch fehlt

- manuelle Overrides und Review-Flags im `sync_map`
- Proxy-/Preview-Pipeline fuer schnellere technische Analyse und Render-Checks
- echtes `render run` statt nur Scaffold
- spaeter piecewise Sync fuer Clips, bei denen auch der Rescue-Pfad nicht reicht
