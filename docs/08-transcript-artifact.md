# transcript Artifact

## Zweck

`transcript` ist das textuelle Gegenstueck zum `analysis_map`.

Wichtige Regel fuer VAZer:

- transkribiert wird nur die Masterspur
- Kamera-Audio wird nicht transkribiert
- Kamera-Audio bleibt reines Sync-Signal

Damit bleibt das Sprachmodell auf der besten Audioquelle und wir vermeiden unnoetige Mehrfachtranskription derselben Szene.

## Warum Chunking

Die OpenAI-Audio-Transcription akzeptiert keine beliebig grossen Einzeldateien. Deshalb zerlegt VAZer lange Master-Dateien in kleinere Upload-Chunks und setzt die Zeitstempel danach wieder auf die Master-Timeline zurueck.

Aktuell:

- Export pro Chunk als Mono-`m4a`
- `16 kHz`
- Standard-Bitrate `64k`
- Standard-Chunks `600 s`

## Warum standardmaessig `whisper-1`

VAZer nutzt fuer diesen ersten Transcription-Pfad standardmaessig `whisper-1`, weil wir Segment- und Wort-Zeitstempel brauchen.

Der aktuelle API-Pfad nutzt:

- `response_format=verbose_json`
- `timestamp_granularities=["segment", "word"]`

## Top-Level

```json
{
  "schema_version": "vazer.transcript.v1",
  "generated_at_utc": "2026-03-17T21:30:00Z",
  "tool": {
    "name": "vazer",
    "version": "0.1.0"
  },
  "provider": {
    "name": "openai",
    "model": "whisper-1"
  },
  "source_sync_map": null,
  "master_audio": {},
  "options": {},
  "language": "de",
  "text": "...",
  "chunks": [],
  "segments": [],
  "words": [],
  "summary": {}
}
```

## Chunk

```json
{
  "index": 1,
  "start_seconds": 0.0,
  "end_seconds": 600.0,
  "duration_seconds": 600.0,
  "upload_format": "m4a",
  "audio_sample_rate": 16000,
  "audio_bitrate": "64k",
  "language": "de",
  "text": "...",
  "segment_count": 42,
  "word_count": 511
}
```

## Segment

```json
{
  "start_seconds": 486.52,
  "end_seconds": 491.88,
  "text": "...",
  "speaker": null,
  "chunk_index": 1
}
```

## Word

```json
{
  "start_seconds": 490.14,
  "end_seconds": 490.42,
  "text": "geht",
  "chunk_index": 1
}
```

Das Wort-Level ist die Grundlage fuer:

- spaetere Satz- und Pausengrenzen im Draft-Plan
- lokale Cut-Validierung direkt an gesprochenen Grenzen
- deterministische Reparaturen ohne kompletten Re-Plan

## CLI

Direkt ueber die Master-Datei:

```powershell
$env:PYTHONPATH='src'
python -m vazer transcribe master --master .\audio\master.wav --out .\artifacts\transcript.json
```

Oder indirekt ueber ein `sync_map`:

```powershell
$env:PYTHONPATH='src'
python -m vazer transcribe master --sync-map .\artifacts\sync_map.json --out .\artifacts\transcript.json
```

## Konfiguration

Der API-Key liegt in `.env` und wird nicht committed.

Beispiel in `.env.example`:

```dotenv
OPENAI_API_KEY=your_openai_api_key_here
```
