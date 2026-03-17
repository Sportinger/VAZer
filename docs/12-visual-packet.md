# visual_packet

## Zweck

`visual_packet` ist das Brueckenartefakt zwischen rein strukturierten Daten und spaeterem multimodalem AI-Planning.

Es beantwortet genau diese Frage:

- welche Bilder sollen wir der AI zeigen, ohne ihr die komplette Show als riesigen Bilderteppich zu schicken?

## Grundidee

VAZer schickt der AI nicht einfach alle Frames aller Kameras.

Stattdessen baut `visual_packet` gezielte Fenster mit:

- einem Master-Zeitpunkt
- einem kurzen Transcript-Ausschnitt
- optionalen Analyse-Scores
- genau einem Still pro aktiver Kamera

Damit bleibt der Kontext klein genug, um wirklich nuetzlich zu sein.

## Zwei Modi

### `overview`

Fuer den ersten AI-Draft.

- sparse Samples ueber die ganze Auffuehrung
- typische Bildlage jeder Kamera
- Text- und Signal-Kontext pro Fenster

### `cuts`

Fuer lokalen Review oder spaetere AI-Reparatur.

- Fenster direkt an bestehenden Cut-Stellen
- je Kamera ein Bild am selben Master-Zeitpunkt
- Transcript-Kontext um die konkrete Entscheidung

## Top-Level

```json
{
  "schema_version": "vazer.visual_packet.v1",
  "generated_at_utc": "2026-03-17T21:55:00Z",
  "source_sync_map": {},
  "source_analysis_map": null,
  "source_transcript": null,
  "source_cut_plan": null,
  "master_audio": {},
  "options": {},
  "windows": [],
  "summary": {}
}
```

## Window

```json
{
  "id": "window_0001",
  "kind": "overview_sample",
  "master_center_seconds": 2220.0,
  "master_start_seconds": 2214.0,
  "master_end_seconds": 2226.0,
  "transcript": {
    "text": "...",
    "word_count": 18,
    "segment_count": 0
  },
  "images": []
}
```

## Image Entry

```json
{
  "asset_id": "VAZ Chaos Close",
  "role": "close",
  "asset_path": "D:\\VAZ_Chaos\\Medien\\VAZ Chaos Close.MXF",
  "image_path": ".\\artifacts\\visuals\\images\\window_0001\\vaz-chaos-close.jpg",
  "image_width": 640,
  "image_height": 360,
  "master_center_seconds": 2220.0,
  "source_seconds": 2373.69,
  "confidence": "high",
  "signals": {
    "has_analysis": true,
    "usable_score": 0.45,
    "sharpness_score": 0.48,
    "stability_score": 0.39
  }
}
```

## Rollen

`visual_packet` kennt bewusst Theaterrollen:

- `close`
- `halbtotale`
- `totale`

Diese Rollen koennen ueber `--role ASSET_ID=ROLE` explizit gesetzt werden.

Ohne Override versucht VAZer eine grobe Heuristik ueber Dateiname und Asset-ID.

## CLI

Overview:

```powershell
$env:PYTHONPATH='src'
python -m vazer analyze visuals --sync-map .\artifacts\sync_map.json --analysis .\artifacts\analysis_map.json --out .\artifacts\visual_packet.json --out-dir .\artifacts\visuals
```

Mit Rollen-Overrides:

```powershell
$env:PYTHONPATH='src'
python -m vazer analyze visuals --sync-map .\artifacts\sync_map.json --analysis .\artifacts\analysis_map.json --out .\artifacts\visual_packet.json --out-dir .\artifacts\visuals --role "Clip0004=totale" --role "VAZ Chaos Close=close" --role "VAZ Chaos HT=halbtotale"
```

Cut-fokussiert:

```powershell
$env:PYTHONPATH='src'
python -m vazer analyze visuals --sync-map .\artifacts\sync_map.json --analysis .\artifacts\analysis_map.json --transcript .\artifacts\transcript.json --cut-plan .\artifacts\cut_plan.json --mode cuts --out .\artifacts\visual_packet.cuts.json --out-dir .\artifacts\visuals-cuts
```

## Wie das spaeter an die AI geht

Nicht:

- alle Frames
- die ganze Show als Bilderwand

Sondern eher:

1. `sync_map`
2. `analysis_map`
3. `transcript`
4. kleines `visual_packet overview`

und spaeter bei unklaren Stellen eventuell:

5. lokales `visual_packet cuts`

## Aktueller Nutzen

Das Artefakt ist noch kein AI-Call. Es ist bewusst die vorbereitete multimodale Eingabe, damit der spaetere LLM-Draft-Planer nicht wieder generisch oder zu teuer wird.
