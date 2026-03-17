# sample_set

## Ziel

`sample set` erzeugt kleine echte Medienpakete aus dem grossen Multicam-Material, damit die komplette Pipeline schnell testbar bleibt.

Das ist fuer VAZer besonders nuetzlich, weil:

- 1-3h Material fuer jede Iteration zu teuer ist
- Sync, Transcript, Analyse, Visuals und AI-Draft auf kurzen Fenstern viel schneller pruefbar sind

## Idee

Ein Sample-Fenster enthaelt:

- eine Master-Audio-Datei
- je Kamera einen echten Videoclip
- weitgehend ueberlappenden Inhalt
- aber absichtlich unterschiedliche Startlagen pro Kamera

Damit muss der Sync spaeter wieder echte Arbeit leisten, statt triviale identische Starts vorzufinden.

## CLI

```powershell
$env:PYTHONPATH='src'
python -m vazer sample set --sync-map .\artifacts\sync_map.json --out-dir .\artifacts\sample_set --duration 60 --window-count 3
```

Optionale Tuning-Flags:

- `--duration`
- `--window-count`
- `--stagger-ratio`
- `--mode copy|reencode`
- `--role ASSET_ID=totale|close|halbtotale`

## 1 Minute oder 5 Minuten

Empfohlene Praxis:

- `1 Minute`
  - fuer schnelle End-to-End-Checks
  - fuer Sync, Transcript, Visual Packet und AI-Draft in kurzer Schleife
- `5 Minuten`
  - fuer realistischere Rhythmus- und Schnittentscheidungen
  - fuer stabilere Bewertung ueber mehr Text und Buehnenbewegung

Darum ist ein Mischansatz sinnvoll:

1. zuerst 1-Minuten-Slices
2. danach einzelne 5-Minuten-Slices

## copy vs reencode

### `copy`

- schneller
- codec-naher am Original
- Dateien koennen sehr gross werden

### `reencode`

- sauberere kleine Testdateien
- langsamer beim Erzeugen
- besser fuer handliche Team- oder CI-Testsets

## Artefakte

Top-Level:

```json
{
  "schema_version": "vazer.sample_set.v1",
  "generated_at_utc": "2026-03-17T22:10:00Z",
  "source_sync_map": {},
  "options": {},
  "windows": [],
  "summary": {}
}
```

Ein Fenster:

```json
{
  "id": "sample_0001",
  "master_output_path": ".\\sample_0001\\master.wav",
  "master_start_seconds": 1698.4,
  "master_end_seconds": 1758.4,
  "duration_seconds": 60.0,
  "cameras": [
    {
      "asset_id": "Clip0004",
      "role": "totale",
      "output_path": ".\\sample_0001\\Clip0004.mkv",
      "master_equivalent_start_seconds": 1698.4,
      "shift_seconds": 0.0
    }
  ]
}
```

## Aktueller Teststand

Der Generator wurde bereits erfolgreich auf dem echten Multicam-Testmaterial mit einem 60s-Fenster ausgefuehrt.
