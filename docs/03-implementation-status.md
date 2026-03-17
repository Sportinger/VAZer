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

## Bereits funktional

- Media-Probe fuer Master und Kamera
- Kamera-Audiostreams auf Aktivitaet pruefen
- offensichtliche Duplikat-Spuren erkennen
- einen groben Startpunkt auf der Master-Timeline finden
- mehrere Fine-Sync-Anker messen
- lineares Zeitmodell `source_time = speed * master_time + offset` fitten
- mehrere Kamera-Dateien in ein gemeinsames `sync_map.json` schreiben

## Smoke-Test mit den Beispiel-Dateien

Verwendete Dateien:

- `D:\VAZ_Chaos\Medien\Clip0004.MXF`
- `D:\VAZ_Chaos\Medien\Chaos_Vaz.wav`

Der aktuelle Probe-Lauf liefert plausibel:

- Start der Kamera auf der Master-Timeline bei ca. `71.143 s`
- Modell `source_time = 0.999869857 * master_time - 71.133789`
- prognostizierte Drift ueber `1h` von ca. `-0.469 s`

Das ist noch kein finaler Produktionswert, aber ein belastbarer erster Kern fuer das `sync_map`.

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

## Was noch fehlt

- explizite Fehlerschwellen und Quality-Gates
- manuelle Overrides und Review-Flags im `sync_map`
- Render-Stufe aus `sync_map` und spaeterem `cut_plan`
