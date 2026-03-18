# Docs

Zentraler Ort fuer Produktidee, Architektur, offene Fragen und spaetere Entscheidungen.

## Aktuelle Dokumente

- [00-initial-idea.md](./00-initial-idea.md): erste Produktskizze fuer eine terminal-first Editing-App
- [01-workflow-rules.md](./01-workflow-rules.md): Repo-Arbeitsweise fuer Docs, Commits und Pushes
- [02-sync-architecture.md](./02-sync-architecture.md): Sync-Modell, Datenmodell und aktuelle algorithmische Entscheidungen
- [03-implementation-status.md](./03-implementation-status.md): aktueller Code-Stand, CLI und naechste Bauschritte
- [04-sync-map-schema.md](./04-sync-map-schema.md): persistierbares Batch-Format fuer Master- und Kamera-Sync
- [05-cut-plan-schema.md](./05-cut-plan-schema.md): erstes persistierbares Schnitt-Artefakt aus `sync_map`
- [06-render-scaffold.md](./06-render-scaffold.md): ffmpeg-Scaffold aus `cut_plan`
- [07-analysis-map-schema.md](./07-analysis-map-schema.md): billiges no-proxy CV aus Master-Audio und Kamera-Video fuer Draft-Planung
- [08-transcript-artifact.md](./08-transcript-artifact.md): OpenAI-Transcription nur fuer das Master-Audio mit Wort-Zeitstempeln
- [09-cut-review-workflow.md](./09-cut-review-workflow.md): `draft -> validate -> repair` fuer lokale technische Pruefung von Cut-Stellen
- [10-theater-vaz-profile.md](./10-theater-vaz-profile.md): fester Produktscope fuer Theater-VAZ statt generischem Multicam
- [11-llm-draft-prompt.md](./11-llm-draft-prompt.md): erste feste System-Prompt-Vorlage fuer den spaeteren AI-Draft-Planer
- [12-visual-packet.md](./12-visual-packet.md): gezielte Stills pro Zeitfenster als multimodaler Input fuer spaetere AI-Planung
- [13-ai-draft.md](./13-ai-draft.md): erster echter OpenAI-Draft-Planer fuer kleine Theater-Teilfenster
- [14-sample-set.md](./14-sample-set.md): gestaffelte 1m/5m-Testsets fuer schnelle End-to-End-Pipeline-Checks
- [15-ui.md](./15-ui.md): minimaler Browser-Start fuer Drag-and-drop, Fortschritt und Pause/Resume
- [16-desktop-app.md](./16-desktop-app.md): nativer PySide6-Start fuer lokale Dateien, Jobs und Pause/Resume

## Naechste Docs

- manuelle Korrekturen und Review-Workflow ueber das neue Repair-Schema hinaus
- spaetere semantische Signale wie Sprecherwechsel, Shot-Boundaries und Face-Presence
