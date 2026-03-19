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
  "schema_version": "vazer.render_scaffold.v1",
  "generated_at_utc": "2026-03-17T16:22:00Z",
  "tool": {},
  "source_cut_plan": {},
  "inputs": [],
  "output": {},
  "artifacts": {},
  "ffmpeg": {}
}
```

## Erzeugte Dateien

- `sample-render.filtergraph.txt`
- `sample-render.ffmpeg.txt`
- `sample-render.render.json`

## Aktueller CLI-Command

```powershell
$env:PYTHONPATH='src'
python -m vazer render scaffold --cut-plan .\artifacts\cut_plan.json --output-media .\out\final.mp4 --out-dir .\artifacts\render
```

## Aktueller ffmpeg-Ansatz

Pro Video-Segment:

- `trim` auf die berechnete Source-Zeit
- `setpts` zur Korrektur kleiner Sync-Drift
- `fps`, `scale`/`scale_cuda`, `pad`/`pad_cuda` zur Vereinheitlichung

Pro Audio-Segment:

- `atrim` aus dem Master-Audio
- `asetpts`

Danach:

- Video-Segmente via `concat`
- Audio-Segmente via `concat`

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

Der generierte Command nutzt die dateibasierte ffmpeg-Variante `-/filter_complex`, damit der Filtergraph nicht als ein einziger Kommandozeilenblock im Shell-Command landen muss.

## Beispiel aus dem Sample

```text
[1:v]trim=...,setpts=...,fps=25.000000,scale_cuda=3840:2160:...:format=nv12,pad_cuda=...[v1]
[v1]concat=n=1:v=1:a=0[vout]
[0:a]atrim=...,asetpts=PTS-STARTPTS[a1]
[a1]concat=n=1:v=0:a=1[aout]
```

## Bewusste Grenzen von v1

- noch keine Ausfuehrung des Render-Commands durch VAZer
- noch keine Transitionen
- noch kein Multi-Track-Audio-Mix
- noch keine Validierung gegen exotische Codec-/Filter-Kombinationen
- tiefe `trim`-Starts sind weiterhin teuer, weil lange Quellen bis zur ersten benoetigten Stelle dekodiert werden muessen

## Validierungsstand

Der Scaffold wurde gegen den Ordner `D:\VAZ_Chaos\Medien` mit drei MXF-Kameras plus Master-WAV sowohl fuer einen groben 3-Segment-Plan als auch fuer den signal-aware Plan erzeugt.

Wichtig:

- die JSON- und Filtergraph-Erzeugung funktioniert fuer beide Plaene
- ein voller ffmpeg-Smoke-Test auf dem signal-aware Plan ist bei langen 4K-H.264-Quellen aktuell teuer
- fuer Entwicklung sollten deshalb Proxys oder kuerzere Preview-Render bevorzugt werden
