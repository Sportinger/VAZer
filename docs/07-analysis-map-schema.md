# analysis_map Schema

## Zweck

`analysis_map` haengt technische Signale an ein bestehendes `sync_map`.

Aktuell kommen zwei Signalarten hinein:

- speech-like Aktivitaet auf der Masterspur
- grobe Kameraqualitaet pro Zeitfenster auf der Master-Timeline

Wichtiger Punkt fuer den aktuellen MVP:

- keine Proxies
- keine Voll-Dekodierung
- billiges CV direkt auf den Originaldateien ueber sparse Seeks

Das Artefakt ist bewusst noch technisch und regelbasiert. Es soll dem Planer helfen, bessere Kamerawechsel zu treffen, ohne schon semantische Szenenerkennung oder ein LLM zu brauchen.

## Top-Level

```json
{
  "schema_version": "vazer.analysis_map.v1",
  "generated_at_utc": "2026-03-17T20:00:00Z",
  "tool": {
    "name": "vazer",
    "version": "0.1.0"
  },
  "source_sync_map": {},
  "master": {},
  "options": {},
  "master_audio_activity": {},
  "entries": [],
  "summary": {}
}
```

## Master Audio Activity

Die Masterspur wird in kurze Audioframes zerlegt und grob auf speech-like Aktivitaet bewertet.

Aktuell:

- Mono-Decode ueber `ffmpeg`
- `highpass` plus `lowpass`
- RMS pro Audioframe
- dynamischer Schwellwert aus Quantilen
- Merge naher Aktivitaetsinseln zu Segmenten

Beispiel:

```json
{
  "segments": [
    {
      "start_seconds": 486.5,
      "end_seconds": 492.0,
      "kind": "speech_like",
      "mean_level_dbfs": -41.7,
      "peak_level_dbfs": -36.9
    }
  ],
  "summary": {
    "segment_count": 382,
    "threshold_dbfs": -48.73,
    "frame_seconds": 0.5
  }
}
```

## Kameraanalyse

Jede `synced`-Kamera wird ueber sparse Frame-Seeks analysiert.

Wichtige Designentscheidung:

- keine Voll-Dekodierung der kompletten 4K-Datei
- stattdessen zeitlich ausgeduennte Einzel-Frames via OpenCV-Seeks
- dadurch wird der Analysepfad fuer lange Drehs praktikabler

Pro Sample:

- Schaerfe ueber Laplacian-Varianz
- Bewegung ueber Mean Absolute Difference zum vorherigen Sample

Diese Rohwerte werden dann auf grobe Master-Zeitfenster aggregiert und normiert.

Beispiel eines analysierten Kamera-Eintrags:

```json
{
  "asset_id": "Clip0004",
  "path": "D:\\VAZ_Chaos\\Medien\\Clip0004.MXF",
  "status": "analyzed",
  "sampling": {
    "method": "opencv_sparse_seek",
    "sample_interval_seconds": 15.0,
    "requested_samples": 302,
    "frame_count": 301
  },
  "windows": [
    {
      "master_start_seconds": 480.0,
      "master_end_seconds": 510.0,
      "sample_count": 2,
      "sharpness_score": 0.81,
      "stability_score": 0.67,
      "usable_score": 0.76,
      "flags": {
        "soft": true,
        "stable": true,
        "sharp": true
      }
    }
  ],
  "summary": {
    "window_count": 151,
    "mean_sharpness_score": 0.58,
    "mean_stability_score": 0.54,
    "usable_window_ratio": 0.57
  }
}
```

## CLI

```powershell
$env:PYTHONPATH='src'
python -m vazer analyze technical --sync-map .\artifacts\sync_map.json --out .\artifacts\analysis_map.json
```

Optionale Tuning-Flags:

- `--audio-rate`
- `--audio-frame`
- `--speech-merge-gap`
- `--speech-min-segment`
- `--video-sample-interval`
- `--video-window`
- `--analysis-width`

## Aktueller Einsatz im Planer

`plan draft` kann jetzt optional auf `analysis_map` und ein externes Transcript zugreifen:

```powershell
$env:PYTHONPATH='src'
python -m vazer plan draft --sync-map .\artifacts\sync_map.json --analysis .\artifacts\analysis_map.json --out .\artifacts\cut_plan.json
```

Bei speech-like Intervallen priorisiert der Planer aktuell staerkere technische Kamerafenster. In ruhigen Bereichen bleibt Sync-Konfidenz weiter ein wichtiges Signal.

## Bewusste Grenzen von v1

- noch keine Shot-Boundaries
- noch keine Face-/Framing-Signale
- noch keine semantische Transcript-Auswertung
- noch keine Proxy-Pipeline fuer wirklich grosse Mengen an Material
