# sync_map Schema

## Zweck

`sync_map` ist das erste persistierbare Artefakt zwischen Ingest/Sync und spaeterem Planning/Rendering.

Es speichert:

- die verwendete Masterspur
- die Sync-Optionen des Laufs
- pro Kamera die gefundene Abbildung auf die Master-Timeline
- pro Kamera eine kleine Media-Zusammenfassung
- Anchor-Messungen und Konfidenz
- auch Fehlerfaelle, damit Batch-Laeufe nicht alles abbrechen

## Top-Level

```json
{
  "schema_version": "vazer.sync_map.v1",
  "generated_at_utc": "2026-03-17T15:58:00Z",
  "tool": {
    "name": "vazer",
    "version": "0.1.0"
  },
  "master": {},
  "options": {},
  "entries": [],
  "summary": {}
}
```

## Master

```json
{
  "path": "D:\\VAZ_Chaos\\Medien\\Chaos_Vaz.wav",
  "duration_seconds": 4568.0,
  "format_name": "wav"
}
```

## Options

Die Laufparameter werden mitgespeichert, damit Sync-Ergebnisse reproduzierbar bleiben.

```json
{
  "coarse_rate": 1000,
  "fine_rate": 4000,
  "activity_rate": 2000,
  "activity_window_seconds": 12.0,
  "anchor_count": 6,
  "anchor_window_seconds": 45.0,
  "anchor_search_seconds": 1.5
}
```

## Entry Status

Jeder Kamera-Eintrag hat einen Status:

- `synced`
- `failed`

### Synced Entry

```json
{
  "asset_id": "Clip0004",
  "path": "D:\\VAZ_Chaos\\Medien\\Clip0004.MXF",
  "status": "synced",
  "media": {
    "format_name": "mxf",
    "duration_seconds": 4510.56,
    "audio_stream_count": 4,
    "video_stream_count": 1,
    "primary_video": {
      "absolute_stream_index": 0,
      "codec_name": "h264",
      "duration_seconds": 4510.56,
      "width": 3840,
      "height": 2160,
      "frame_rate": 25.0
    }
  },
  "selected_stream": {
    "map_specifier": "0:1",
    "absolute_stream_index": 1
  },
  "mapping": {
    "speed": 0.999869857,
    "offset_seconds": -71.133789,
    "camera_starts_at_master_seconds": 71.143047,
    "predicted_drift_over_hour_seconds": -0.468514,
    "model": "source_time = speed * master_time + offset_seconds"
  },
  "coarse": {
    "map_specifier": "0:1",
    "method": "bounded_direct",
    "camera_starts_at_master_seconds": 71.325846,
    "master_to_source_offset_seconds": -71.325846,
    "peak_ratio": 1.294713
  },
  "anchors": {},
  "summary": {}
}
```

### Failed Entry

```json
{
  "asset_id": "Clip0007",
  "path": "D:\\VAZ_Chaos\\Medien\\Clip0007.MXF",
  "status": "failed",
  "error": "No active camera audio stream was found for sync."
}
```

## asset_id

`asset_id` wird aktuell aus dem Dateinamen abgeleitet.

Falls derselbe Stem mehrfach vorkommt, wird durchnummeriert:

- `cam_a`
- `cam_a_01`
- `cam_a_02`

## Aktueller CLI-Command

```powershell
$env:PYTHONPATH='src'
python -m vazer sync map --master <master.wav> --camera <cam1> --camera <cam2> --out .\artifacts\sync_map.json
```

## Bewusste Grenzen von v1

- noch kein manuelles Override pro Kamera im selben Batch-Lauf
- noch kein piecewise Sync pro Asset
- noch keine separaten Quality-Gates auf Basis von Residualfehlern
- noch keine Review-Notizen oder manuelle Korrekturen im Artefakt
