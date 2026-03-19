# Render Scaffold

## Zweck

Der aktuelle Render-Schritt ist noch kein finaler `render run`, sondern ein Scaffold.

Er erzeugt aus dem `cut_plan`:

- einen `filter_complex`-Graph als Textdatei
- eine konkrete ffmpeg-Commandline
- ein JSON-Manifest fuer Debugging und spaetere Ausfuehrung

## Warum zuerst ein Scaffold

Das ist absichtlich konservativ:

- der Filtergraph bleibt lesbar und diffbar
- der ffmpeg-Command bleibt kurz
- die Logik bleibt im Code statt in einem handgeschriebenen Monster-Command

## Top-Level Manifest

```json
{
  "schema_version": "vazer.render_scaffold.v2",
  "generated_at_utc": "2026-03-17T16:22:00Z",
  "tool": {},
  "source_cut_plan": {},
  "inputs": [],
  "output": {},
  "artifacts": {},
  "segments": [],
  "audio": {},
  "concat": {},
  "mux": {},
  "ffmpeg": {}
}
```

## Erzeugte Dateien

- `sample-render.audio.filtergraph.txt`
- `sample-render.concat.txt`
- `sample-render.ffmpeg.txt`
- `sample-render.render.json`
- `sample-render.segments\segment_0001.mp4`
- `sample-render.video.concat.mp4`
- `sample-render.audio.m4a`

## Aktueller CLI-Command

```powershell
$env:PYTHONPATH='src'
python -m vazer render scaffold --cut-plan .\artifacts\cut_plan.json --output-media .\out\final.mp4 --out-dir .\artifacts\render
```

## Aktueller ffmpeg-Ansatz

VAZer rendert jetzt segmentiert statt ueber einen einzigen grossen `filter_complex`-Graphen.

Pro Video-Segment:

- echtes Input-Seeking via `-ss` und `-t` direkt am Quelldatei-Input
- `setpts` zur Korrektur kleiner Sync-Drift
- `fps`, `scale`/`scale_cuda`, `pad`/`pad_cuda` zur Vereinheitlichung
- Ausgabe als temp Segment-Datei

Fuer Audio:

- eigener Audio-Only-Graph mit `atrim` + `concat` aus der Masterspur

Danach:

- Video-Segmente per Concat-Demuxer mit `-c copy` zusammenfuehren
- Audio separat rendern
- finales MP4 per schnellem Mux aus concat-Video + Audio

## CUDA-Renderpfad

Wenn `h264_nvenc` verfuegbar ist, nutzt VAZer jetzt standardmaessig den strikten CUDA-Pfad:

- GPU-Decode pro Video-Input via `-hwaccel cuda -hwaccel_output_format cuda`
- GPU-Scaling via `scale_cuda`
- GPU-Padding via `pad_cuda`
- GPU-Encode via `h264_nvenc`

CPU-Fallback fuer den finalen Render ist aktuell bewusst deaktiviert. Wenn CUDA-Komponenten in `ffmpeg` fehlen, endet der Render mit einem klaren Fehler.

Im Render-Manifest steht der aktive Pfad unter:

- `output.render_pipeline.video_path`
- `output.render_pipeline.input_args`

Der temp Segment-Render nutzt jetzt keinen riesigen Video-Filtergraphen mehr. Die dateibasierte ffmpeg-Variante bleibt nur fuer den Audio-Only-Graphen relevant.

## Beispiel aus dem Sample

```text
ffmpeg -ss 153.697123 -t 2.000000 -hwaccel cuda -hwaccel_output_format cuda -extra_hw_frames 8 -i "...Close.MXF" -an -vf setpts=...,fps=25.000000,scale_cuda=1920:1080:...:format=nv12,pad_cuda=... -c:v h264_nvenc ...
ffmpeg -f concat -safe 0 -i sample-render.concat.txt -c copy sample-render.video.concat.mp4
[0:a]atrim=...,asetpts=PTS-STARTPTS[a1]
[a1]concat=n=1:v=0:a=1[aout]
```

## Bewusste Grenzen von v1

- noch keine Ausfuehrung des Render-Commands durch VAZer
- noch keine Transitionen
- noch kein Multi-Track-Audio-Mix
- noch keine Validierung gegen exotische Codec-/Filter-Kombinationen
- viele sehr kurze Segmentdateien erzeugen weiterhin Overhead
- der grosse Geschwindigkeitshebel liegt jetzt im Input-Seeking; weitere Optimierung waere spaeter Block-Rendering statt Einzelsegment-Dateien

## Validierungsstand

Der Scaffold wurde gegen den Ordner `D:\VAZ_Chaos\Medien` mit drei MXF-Kameras plus Master-WAV sowohl fuer einen groben 3-Segment-Plan als auch fuer den signal-aware Plan erzeugt.

Wichtig:

- der neue segmentierte Runner wurde auf einem echten 2-Segment-4s-Slice erfolgreich durchgetestet
- ein echter 2-Minuten-FHD-Plan mit 3 Segmenten lief ueber den neuen Pfad in rund `15.9s`
- fuer sehr lange Shows bleibt die Gesamtzeit weiter segmentabhaengig, ist aber deutlich weniger decode-blockiert als beim alten Monolithen
