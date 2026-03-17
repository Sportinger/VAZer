# cut_plan Schema

## Zweck

`cut_plan` ist das erste Artefakt, das bereits schnittorientiert ist.

Es nimmt ein `sync_map` und macht daraus:

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
  "generated_at_utc": "2026-03-17T16:20:00Z",
  "tool": {
    "name": "vazer",
    "version": "0.1.0"
  },
  "source_sync_map": {},
  "master_audio": {},
  "render_defaults": {},
  "timeline": {},
  "video_segments": [],
  "audio_segments": [],
  "summary": {}
}
```

## Baseline-Planer

Der aktuelle Planer ist bewusst simpel:

- nimm alle `synced`-Eintraege aus dem `sync_map`
- berechne ihre echte Ueberdeckung auf der Master-Timeline
- teile die Timeline an allen Start-/Endgrenzen
- waehle pro Intervall die beste verfuegbare Kamera
- entferne Luecken aus dem Output
- baue Audio-Segmente aus derselben Master-Zeit

Im Mehrkamera-Testordner fuehrt das aktuell zu drei Video-Segmenten ueber die komplette Output-Laenge.

Aktuelle Auswahlregel:

- hoehere Konfidenz gewinnt
- dann mehr akzeptierte Anchors
- dann bessere grobe Peak-Ratio
- dann geringere prognostizierte Drift

## render_defaults

Der Baseline-Planer speichert empfohlene Render-Werte, damit der Render-Scaffold eine konkrete Zielnormalisierung hat.

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
  "strategy": "baseline_best_available",
  "asset_id": "Clip0004",
  "asset_path": "D:\\VAZ_Chaos\\Medien\\Clip0004.MXF",
  "confidence": "medium",
  "master_start_seconds": 71.143047,
  "master_end_seconds": 4568.0,
  "output_start_seconds": 0.0,
  "output_end_seconds": 4496.856953,
  "duration_seconds": 4496.856953,
  "source_start_seconds": 0.0,
  "source_end_seconds": 4496.271719,
  "speed": 0.999869857,
  "reason": "Only synced camera covering interval."
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

## Aktueller CLI-Command

```powershell
$env:PYTHONPATH='src'
python -m vazer plan baseline --sync-map .\artifacts\sync_map.json --out .\artifacts\cut_plan.json
```

## Bewusste Grenzen von v1

- noch keine Transcript- oder Analyse-Signale
- noch keine Blenden oder komplexen Transitionen
- noch keine manuelle Priorisierung einzelner Kameras
- noch keine Black/Gap-Filler-Segmente
