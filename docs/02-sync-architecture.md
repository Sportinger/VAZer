# Sync-Architektur

## Ziel

Der erste technische Kern von VAZer ist ein audiozentrierter Sync-Probe. Er beantwortet fuer eine Kamera-Datei und eine Master-Audio-Datei diese Fragen:

- Welche Kamera-Audiospur ist fuer Sync ueberhaupt brauchbar?
- Wann startet die Kamera auf der Master-Timeline?
- Reicht ein fixer Offset oder ist ueberschaubare Drift sichtbar?

## Festgezurrte Annahmen

1. Es gibt in der Praxis keinen brauchbaren Timecode.
2. Das Master-Audio ist die einzige kanonische Zeitachse.
3. Kamera-Audio ist Scratch-Audio fuer Sync, nicht finaler Mix.
4. Die Kamera ueberlappt den Master weitgehend.
5. Eine kleine End-Ueberhang-Toleranz ist erlaubt, weil Recorder nicht exakt gleich lang laufen.

## Zeitmodell

Intern wird nicht nur ein Startoffset gespeichert, sondern eine lineare Abbildung:

```text
source_time = speed * master_time + offset_seconds
```

Das bedeutet:

- `offset_seconds` beschreibt die Lage der Kamera relativ zur Master-Zeit
- `speed` modelliert kleine Clock-Differenzen und Drift
- `camera_starts_at_master_seconds = -offset_seconds / speed`

Spaeter kann dieses Modell auf mehrere Segmente erweitert werden, falls eine einzige Gerade nicht reicht.

## Aktueller Sync-Ablauf

### 1. Media-Probe

`ffprobe` liest Container-, Stream- und Dauerinformationen.

Wichtige Outputs:

- Dateidauer
- Audio-Stream-Indizes
- Sample-Rate
- Codec

### 2. Stream-Inspektion

Pro Kamera-Audiospur werden mehrere kurze Fenster decodiert und bewertet.

Aktuelle Regeln:

- sehr leise Spuren gelten als inaktiv
- stark aehnliche Spuren gelten als Duplikate
- fuer den Auto-Modus bleiben nur aktive, nicht duplizierte Scratch-Spuren uebrig

### 3. Coarse Sync

Der aktuelle Prototyp nutzt zwei Grobsuch-Stufen:

#### bounded_direct

Zuerst eine duration-bounded Suche:

- gefilterte Mono-Decodes mit `ffmpeg`
- Suchraum aus `Masterdauer - Kameradauer + End-Ueberhang`
- Korrelation auf einer fruehen oder lauten Kamera-Passage

Warum diese Einschraenkung bewusst ist:

- globale Korrelation ueber die komplette Datei springt bei langen Programmen leicht auf falsche Peaks
- mit der Dauerinformation laesst sich der plausible Suchraum drastisch reduzieren
- das passt zum aktuellen Zielbild mit einem langen Master-Audio und kuerzeren Kamera-Quellen

#### broad_cluster

Falls `bounded_direct` scheitert:

- mehrere Probe-Fenster derselben Kamera werden gegen die ganze Masterspur gesucht
- die resultierenden Startkandidaten werden geclustert
- der stabilste Cluster gewinnt

Das ist wichtig fuer Kameras, die deutlich vor oder nach der Masterspur anlaufen und mit der bounded-Suche durch das Raster fallen.

### 4. Fine Sync und Drift

Nach dem Grob-Offset werden mehrere Anchor-Fenster ueber die gemeinsame Ueberlappung verteilt.

Pro Anchor:

- Master-Fenster decodieren
- Kamera-Fenster um die erwartete Stelle decodieren
- Korrelation im engen Suchradius
- `source_minus_master` messen

Aus den akzeptierten Anchors wird eine Gerade gefittet.

## Datenobjekte

### Stream Inspection

```json
{
  "map_specifier": "0:1",
  "absolute_stream_index": 1,
  "loudest_rms": 0.0073,
  "active": true,
  "duplicate_of": null
}
```

### Coarse Sync

```json
{
  "method": "bounded_direct",
  "camera_starts_at_master_seconds": 71.14,
  "master_to_source_offset_seconds": -71.14,
  "peak_ratio": 1.38
}
```

### Anchor Measurement

```json
{
  "master_reference_seconds": 2100.0,
  "source_minus_master_seconds": -71.19,
  "peak_ratio": 1.32,
  "accepted": true
}
```

### Final Mapping

```json
{
  "speed": 0.99987,
  "offset_seconds": -71.13,
  "camera_starts_at_master_seconds": 71.14,
  "predicted_drift_over_hour_seconds": -0.47
}
```

## CLI-Stand

Aktuell implementiert:

```powershell
$env:PYTHONPATH='src'
python -m vazer sync probe --master <master.wav> --camera <clip.mxf>
```

Batch-Persistierung:

```powershell
$env:PYTHONPATH='src'
python -m vazer sync map --master <master.wav> --camera <cam1> --camera <cam2> --out .\artifacts\sync_map.json
```

Optional:

- `--stream 0:1`
- `--json`
- `--coarse-rate`
- `--fine-rate`
- `--anchor-count`
- `--anchor-window`
- `--anchor-search`

## Bekannte Grenzen

- Noch kein piecewise Sync bei harten Recorder-Spruengen.
- `sync_map` ist jetzt persistierbar, aber noch ohne manuelle Overrides oder Review-Flags.
- Der Sync speist jetzt bereits `analysis_map` und `cut_plan`, aber noch ohne explizite Review-Gates.
- Noch kein automatischer Render-Lauf, nur ein Render-Scaffold.

## Quality Gates

Ein formal berechneter Sync wird nicht mehr automatisch als gueltig behandelt.

Aktuell wird ein Mapping verworfen, wenn die akzeptierten Anchor-Fenster intern zu stark streuen.

Derzeitige harte Gates:

- Residual-RMS der akzeptierten Anchors groesser als `0.200 s`
- maximale Anchor-Abweichung groesser als `0.350 s`
- Offset-Range der akzeptierten Anchors groesser als `0.500 s`

Das Ziel ist simpel:

- ein unsicheres Mapping soll als klarer Fehler enden
- nicht als scheinbar brauchbarer `synced`-Eintrag weiter in Analyse und Schnitt rutschen

## Naechste technische Schritte

1. Residualfehler je Anchor explizit ausgeben und Grenzwerte haerter machen.
2. piecewise Sync einfuehren, wenn lineare Drift nicht reicht.
3. technische Analyse und spaetere semantische Signale enger an Sync-Confidence koppeln.
4. Proxy- oder Preview-Pfade fuer schnellere QA und Render-Checks bauen.
