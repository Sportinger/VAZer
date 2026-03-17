# VAZer Initialidee

## Kurzfassung

VAZer soll eine sehr kleine, effiziente Terminal-App sein, die Video- und Audio-Dateien einsammelt, sie an einer deklarierten Masterspur synchronisiert, das Master-Audio transkribiert, technische Problemstellen in den Videoquellen analysiert und daraus per LLM einen Schnittplan erzeugt, der lokal ausgefuehrt wird.

## Zielbild

Der Nutzer soll aus mehreren Kamera- und Audioquellen moeglichst wenig manuell schneiden muessen.

Der grobe Ablauf:

1. Medienquellen einlesen
2. Masterspur festlegen
3. Alle Quellen an der Masterspur syncen
4. Master-Audio transkribieren
5. Jede Videoquelle auf Unschaerfe und starke Kamerabewegung analysieren
6. Alle Signale an ein LLM schicken
7. Einen strukturierten Schnittplan zurueckbekommen
8. Den Plan lokal auf die Originaldateien anwenden

## Kernprinzipien

- Terminal-first, kein GUI-Zwang
- Kleine, robuste Pipeline statt grosser NLE
- Analyse und Entscheidung trennen
- Deterministische lokale Ausfuehrung des finalen Schnitts
- Austauschbare Backends, falls sich ein Tool als zu eng erweist

## Pipeline

### 1. Ingest

Input kann nahezu alles sein, was sich in Audio und Frames dekodieren laesst:

- Video-Dateien
- reine Audio-Dateien
- unterschiedliche Framerates und Samplerates

Beim Ingest sollten pro Asset sofort Metadaten erzeugt werden:

- Dateipfad
- Dauer
- Video-Streams und Audio-Streams
- Framerate
- Sample-Rate
- Timecode, falls vorhanden

### 2. Sync an einer Masterspur

Eine Datei wird explizit als Master definiert. Alle anderen Quellen werden relativ dazu ausgerichtet.

Pragmatischer Ansatz fuer MVP:

- Audio aus allen Quellen extrahieren
- auf ein gemeinsames PCM-Format normieren
- Offset per Cross-Correlation oder Audio-Fingerprint bestimmen
- pro Clip einen sicheren Startoffset und optional ein Konfidenz-Mass speichern

Output dieses Schritts ist eine gemeinsame Timeline.

### 3. Transkription

Nur die Masterspur wird transkribiert. Das reduziert Kosten und vereinfacht die spaetere Planlogik.

Relevante Outputs:

- Segment mit `start`, `end`, `text`
- optional Sprecherwechsel
- optionale Filler- oder Pause-Marker

### 4. Videoanalyse

Zwei Signale sind fuer den ersten Schnitt besonders sinnvoll:

### Unschaerfe

Ein einfacher MVP-Score kann ueber die Varianz des Laplacian oder aehnliche Hochfrequenz-Metriken laufen.

Output:

- Intervalle mit Blur-Score
- Schwellwerte fuer `usable`, `borderline`, `bad`

### Kamerabewegung

Wichtig ist nicht Objektbewegung im Bild, sondern Bewegung der Kamera selbst.

Dafuer sollte die Analyse globale Bewegung zwischen Frames schaetzen:

- Feature Tracking zwischen benachbarten Frames
- Hintergrunddominante Punkte bevorzugen
- Ausreisser mit RANSAC verwerfen
- Kameramotion ueber globale Transformationen oder deren Magnitude abschaetzen

Output:

- Intervalle mit starker Kamerabewegung
- optional Ereignisse wie `shake`, `pan`, `reframe`

### 5. LLM-Planung

Das LLM sollte keine Rohdaten bekommen, sondern verdichtete strukturierte Signale:

- Transcript-Segmente
- Sync-Timeline
- pro Quelle technische Qualitaetsintervalle
- optionale Zusatzregeln wie "nehme immer beste stabile Totalen fuer Sprachpassagen"

Erwarteter Output ist kein Freitext, sondern ein maschinenlesbarer Schnittplan, zum Beispiel:

```json
{
  "timeline": [
    {
      "start": 12.4,
      "end": 18.9,
      "source": "cam_b",
      "reason": "stable close-up while transcript contains key statement"
    }
  ]
}
```

### 6. Lokale Ausfuehrung

Der Schnittplan wird lokal und deterministisch gerendert. Das LLM entscheidet also nicht direkt ueber Files, sondern nur ueber einen Plan.

Das ist wichtig fuer:

- Reproduzierbarkeit
- Debugbarkeit
- spaetere manuelle Korrekturen
- mehrere Render-Backends

## Datenobjekte fuer ein spaeteres MVP

- `asset`
- `master_track`
- `sync_map`
- `transcript_segment`
- `video_quality_event`
- `edit_decision`
- `cut_plan`

## Backend-Einschaetzung

### `MediaBunny`

`MediaBunny` sieht interessant aus, weil es in TypeScript sitzt und laut offizieller Doku fuer Medienverarbeitung in Browsern und in Node.js gedacht ist. Die offizielle Conversion-API beschreibt ausserdem Trimming und benutzerdefinierte Verarbeitungsschritte.

Gleichzeitig deutet die Doku darauf hin, dass man ausserhalb einer WebCodecs-Umgebung, etwa in Node.js, je nach Setup eigene Decoder oder Encoder registrieren muss. Das sollte validiert werden, bevor `MediaBunny` das einzige Render- oder Analyse-Backend wird.

Arbeitshypothese:

- `MediaBunny` ist ein guter Kandidat fuer Import, Export und moeglicherweise den spaeteren strukturierten Schnitt
- fuer einen sehr robusten MVP bleiben Sync, Audio-Extraktion und finales Rendering austauschbar
- ein Hybrid aus CLI-Orchestrierung plus spezialisierten Backends ist wahrscheinlich risikoaermer als ein frueher Full-Commit auf nur ein Medien-Toolkit

### Empfehlung fuer den Start

Die App selbst bleibt klein. Sie orchestriert nur die Pipeline und haelt das Datenmodell zusammen.

Ein moeglicher erster Schnitt:

- CLI in TypeScript
- Sync ueber Audio-Analyse
- Transkription ueber Whisper API
- Videoanalyse ueber CV-Modul
- LLM gibt JSON-Schnittplan zurueck
- lokaler Render-Schritt bleibt als Backend austauschbar

## Beispiel fuer spaetere CLI

```bash
vazer ingest ./media --master cam_a.mov
vazer sync
vazer transcribe
vazer analyze --blur --camera-motion
vazer plan --profile interview
vazer render
```

## MVP-Scope

Ein gutes erstes MVP sollte nur diese Dinge koennen:

- Medien einsammeln
- Master definieren
- Audio-basiert syncen
- Master transkribieren
- Blur und Kamerabewegung markieren
- LLM-basierten JSON-Schnittplan erzeugen
- Plan lokal rendern

Alles andere spaeter:

- Sprecherdiarisierung
- Objekt- oder Personenerkennung
- Shot-Type-Klassifikation
- manuelle Timeline-Korrektur
- mehrere Schnittstile

## Offene Fragen

- Soll die App nur schneiden oder auch automatisch Mehrspur-Audio mischen?
- Ist die Masterspur immer Audio-zentriert oder manchmal auch eine Master-Kamera?
- Soll der LLM-Plan streng maschinenlesbar sein oder zusaetzlich Erklaerungen enthalten?
- Wollen wir von Anfang an einen Dry-Run-Modus mit Visualisierung im Terminal?
- Welches Render-Backend ist fuer MVP wirklich am risikoaermsten?

## Naechster sinnvoller Schritt

Als naechstes sollten wir den Schnittplan als konkretes JSON-Schema definieren. Daran haengen fast alle weiteren Entscheidungen:

- welche Analysedaten wirklich noetig sind
- wie das LLM gepromptet wird
- wie der lokale Render-Schritt den Plan abarbeitet

## Referenzen

- MediaBunny Docs: https://mediabunny.dev/
- MediaBunny Conversion API: https://mediabunny.dev/guide/writing-media-files/converting-media
- MediaBunny Custom Coders: https://mediabunny.dev/guide/advanced/custom-coders
