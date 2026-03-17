# cut_plan Schema

## Zweck

`cut_plan` ist das erste Artefakt, das bereits schnittorientiert ist.

Es nimmt `sync_map`, billige technische Signale und optional ein Transcript und macht daraus:

- eine Auswahl der verwendeten Kameraquellen
- konkrete Segmente auf der Master-Timeline
- eine eigene Output-Timeline ohne Luecken
- eine direkte Grundlage fuer den spaeteren Render-Schritt

## Wichtigste Designentscheidung

`cut_plan` trennt Master-Zeit und Output-Zeit.

Das bedeutet:

- `master_*` beschreibt, wo ein Segment im Originalgeschehen liegt
- `output_*` beschreibt, wo dieses Segment im fertigen Export landet

Damit muessen Pausen oder unbedeckte Master-Bereiche nicht als schwarze Luecken gerendert werden.

## Top-Level

```json
{
  "schema_version": "vazer.cut_plan.v1",
  "planning_stage": "draft",
  "generated_at_utc": "2026-03-17T16:20:00Z",
  "tool": {
    "name": "vazer",
    "version": "0.1.0"
  },
  "source_sync_map": {},
  "source_analysis_map": null,
  "source_transcript": null,
  "source_validation_report": null,
  "draft_options": {},
  "master_audio": {},
  "render_defaults": {},
  "timeline": {},
  "video_segments": [],
  "audio_segments": [],
  "summary": {}
}
```

`planning_stage` ist aktuell entweder:

- `draft`
- `repaired`

## Draft-Planer

Der aktuelle Draft-Planer hat zwei CLI-Einstiege:

- `plan baseline`
- `plan draft`

`baseline` bleibt kompatibel zum alten MVP.

`draft` ist der klarere neue Einstieg fuer:

- `sync_map`
- billiges `analysis_map`
- optionales Transcript mit Wort-Zeitstempeln

Die Kernlogik bleibt bewusst simpel:

- nimm alle `synced`-Eintraege aus dem `sync_map`
- berechne ihre echte Ueberdeckung auf der Master-Timeline
- teile die Timeline an allen Start-/Endgrenzen
- erweitere die Grenzen optional um speech-like Segmente und Analysefenster
- erweitere die Grenzen zusaetzlich um starke Transcript-Pausen und Wortgrenzen
- waehle pro Intervall die beste verfuegbare Kamera
- entferne Luecken aus dem Output
- baue Audio-Segmente aus derselben Master-Zeit

## render_defaults

Der Draft-Planer speichert empfohlene Render-Werte, damit der Render-Scaffold eine konkrete Zielnormalisierung hat.

```json
{
  "width": 3840,
  "height": 2160,
  "fps": 25.0,
  "pixel_format": "yuv420p",
  "video_codec": "libx264",
  "audio_codec": "aac"
}
```

## Video Segment

```json
{
  "id": "video_0001",
  "type": "camera",
  "strategy": "signal_aware_best_available",
  "asset_id": "Clip0004",
  "asset_path": "D:\\VAZ_Chaos\\Medien\\Clip0004.MXF",
  "confidence": "medium",
  "master_start_seconds": 487.0,
  "master_end_seconds": 493.0,
  "output_start_seconds": 487.0,
  "output_end_seconds": 493.0,
  "duration_seconds": 6.0,
  "source_start_seconds": 415.802258,
  "source_end_seconds": 421.801477,
  "speed": 0.999869857,
  "reason": "Preferred more stable/sharp camera during speech-like interval.",
  "signals": {
    "speech_like": true,
    "speech_overlap_ratio": 1.0,
    "speech_sources": [
      "analysis",
      "transcript"
    ],
    "transcript_overlap_count": 1,
    "transcript_excerpt": "...",
    "usable_score": 0.75,
    "sharpness_score": 0.81,
    "stability_score": 0.67,
    "has_analysis": true
  }
}
```

## Audio Segment

```json
{
  "id": "audio_0001",
  "type": "master_audio",
  "source_path": "D:\\VAZ_Chaos\\Medien\\Chaos_Vaz.wav",
  "master_start_seconds": 71.143047,
  "master_end_seconds": 4568.0,
  "output_start_seconds": 0.0,
  "output_end_seconds": 4496.856953,
  "duration_seconds": 4496.856953,
  "source_start_seconds": 71.143047,
  "source_end_seconds": 4568.0
}
```

## Repair-Metadaten

Ein reparierter Plan behaelt dasselbe Schema, bekommt aber zusaetzlich:

```json
{
  "planning_stage": "repaired",
  "source_validation_report": {
    "schema_version": "vazer.cut_validation.v1",
    "path": ".\\artifacts\\cut_validation.json"
  },
  "repair": {
    "source_validation_report": {},
    "applied_cut_actions": [],
    "summary": {
      "applied_cut_actions": 2,
      "shifted_cuts": 2,
      "asset_swaps": 1
    }
  }
}
```

## CLI

Draft:

```powershell
$env:PYTHONPATH='src'
python -m vazer plan draft --sync-map .\artifacts\sync_map.json --analysis .\artifacts\analysis_map.json --transcript .\artifacts\transcript.json --out .\artifacts\cut_plan.json
```

Validierung:

```powershell
$env:PYTHONPATH='src'
python -m vazer plan validate --cut-plan .\artifacts\cut_plan.json --out .\artifacts\cut_validation.json
```

Reparatur:

```powershell
$env:PYTHONPATH='src'
python -m vazer plan repair --cut-plan .\artifacts\cut_plan.json --validation .\artifacts\cut_validation.json --out .\artifacts\cut_plan.repaired.json
```

## Bewusste Grenzen von v1

- noch keine Blenden oder komplexen Transitionen
- noch keine manuelle Priorisierung einzelner Kameras
- noch keine Black/Gap-Filler-Segmente
- Validierung und Reparatur sind lokal und deterministisch, nicht semantisch vollstaendig
