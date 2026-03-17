# Implementierungsstand

## Stand vom 2026-03-17

Der erste lauffaehige Code liegt jetzt als Python-CLI im Repo.

Warum Python fuer diesen Start:

- Audio-Sync, Korrelation und Drift-Fit sind damit deutlich einfacher als in TypeScript
- `numpy` reicht fuer die erste DSP-Schicht
- `ffmpeg` und `ffprobe` bleiben die externen Medien-Backends

Aktueller Produktscope:

- kein generischer Videoeditor
- vorerst nur Theater-VAZ
- durchgehender Multicam-Mitschnitt mit Master-Audio

## Aktuelle Dateien

- `pyproject.toml`: Python-Projektdefinition
- `src/vazer/cli.py`: CLI-Einstieg
- `src/vazer/fftools.py`: `ffprobe`- und `ffmpeg`-Bruecken
- `src/vazer/sync.py`: Stream-Auswahl, Coarse Sync, Anchor-Fit, Drift-Modell
- `src/vazer/analysis.py`: speech-like Master-Aktivitaet und technische Kameraanalyse
- `src/vazer/cut_plan.py`: baseline- und signal-aware Planer
- `src/vazer/cut_review.py`: lokale Cut-Validierung und deterministische Reparatur
- `src/vazer/draft_prompt.py`: feste Theater-VAZ-System-Prompt fuer den spaeteren AI-Draft-Planer
- `src/vazer/visual_packet.py`: gezielte Stills plus Transcript-/Signal-Kontext fuer spaetere multimodale AI-Aufrufe
- `src/vazer/ai_draft.py`: erster echter OpenAI-basierter Draft-Planer fuer kleine Theater-Fenster
- `src/vazer/sample_set.py`: gestaffelte Testfenster aus echtem Multicam-Material
- `src/vazer/render.py`: ffmpeg-Scaffold aus `cut_plan`
- `src/vazer/transcribe.py`: OpenAI-Transcription nur fuer das Master-Audio
- `src/vazer/transcript.py`: Loader fuer externe Transcript-Artefakte

## Bereits funktional

- Media-Probe fuer Master und Kamera
- Kamera-Audiostreams auf Aktivitaet pruefen
- offensichtliche Duplikat-Spuren erkennen
- einen groben Startpunkt auf der Master-Timeline finden
- mehrere Fine-Sync-Anker messen
- lineares Zeitmodell `source_time = speed * master_time + offset` fitten
- mehrere Kamera-Dateien in ein gemeinsames `sync_map.json` schreiben
- nur die Masterspur per OpenAI in ein `transcript.json` transkribieren
- Segment- und Wort-Zeitstempel aus `whisper-1` holen
- ein `analysis_map.json` aus `sync_map` erzeugen
- ein baseline oder draft `cut_plan.json` aus `sync_map` erzeugen
- lokale `cut_validation.json` nur an den Cut-Stellen berechnen
- einen bestehenden Plan deterministisch in ein `cut_plan.repaired.json` ueberfuehren
- einen ffmpeg-Render-Scaffold aus `cut_plan` erzeugen
- einen festen Theater-VAZ-Prompt und Domain-Profile im Repo halten
- ein `visual_packet` mit gezielten Kamera-Stills fuer AI-Planung oder lokale Review-Faelle bauen
- einen echten `plan ai-draft` OpenAI-Call auf kleinen Teilfenstern ausfuehren
- ein `sample_set` fuer 1m/5m Pipeline-Tests erzeugen

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
- der neue `plan draft` laeuft auf diesem billigen CV-Pfad ohne Proxies
- die Cut-Validierung prueft nur lokale Cut-Stellen statt erneut das ganze Material
- ein synthetischer 20s-Slice bestaetigt, dass `validate -> repair` Cuts verschieben und Assets lokal austauschen kann
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

Draft-Plan:

```powershell
$env:PYTHONPATH='src'
python -m vazer plan draft --sync-map .\artifacts\sync_map.json --analysis .\artifacts\analysis_map.json --transcript .\artifacts\transcript.json --out .\artifacts\cut_plan.json
```

Technische Analyse:

```powershell
$env:PYTHONPATH='src'
python -m vazer analyze technical --sync-map .\artifacts\sync_map.json --out .\artifacts\analysis_map.json
```

Cut-Validierung:

```powershell
$env:PYTHONPATH='src'
python -m vazer plan validate --cut-plan .\artifacts\cut_plan.json --out .\artifacts\cut_validation.json
```

Lokale Reparatur:

```powershell
$env:PYTHONPATH='src'
python -m vazer plan repair --cut-plan .\artifacts\cut_plan.json --validation .\artifacts\cut_validation.json --out .\artifacts\cut_plan.repaired.json
```

Signal-aware Plan:

```powershell
$env:PYTHONPATH='src'
python -m vazer plan baseline --sync-map .\artifacts\sync_map.json --analysis .\artifacts\analysis_map.json --out .\artifacts\cut_plan.json
```

Master-Transcription:

```powershell
$env:PYTHONPATH='src'
python -m vazer transcribe master --master <master.wav> --out .\artifacts\transcript.json
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
- dichtere CV-Signale wie Face-Presence, Shot-Boundaries und Framing
