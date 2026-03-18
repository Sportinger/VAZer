# AI-Kamerarollen

## Ziel

VAZer kann die Theater-Kamerarollen jetzt in genau einem AI-Call zuordnen:

- pro Kamera ein Mittelframe aus der Dateimitte
- dazu der Dateiname / `asset_id`
- Ergebnis: `totale`, `halbtotale` oder `close`

Das ist bewusst kein kontinuierliches Vision-Tracking.
Es ist nur die einmalige Grundzuordnung der Kameras fuer den weiteren Schnitt-Workflow.

## Warum als Einmal-Call

Fuer Theater-VAZ ist die Rollenfrage meist statisch:

- eine sichere `totale`
- eine `close`
- oft eine `halbtotale`

Deshalb lohnt sich hier ein einmaliger Klassifikationsschritt viel mehr als spaeteres staendiges Neu-Raten.

## Artefakt

Das Ergebnis wird als `camera_roles.json` gespeichert.

Schema:

```json
{
  "schema_version": "vazer.camera_roles.v1",
  "provider": {
    "name": "openai",
    "model": "gpt-4.1-mini"
  },
  "summary": {
    "asset_count": 3,
    "role_counts": {
      "close": 1,
      "halbtotale": 1,
      "totale": 1
    },
    "summary_text": "..."
  },
  "assignments": [
    {
      "asset_id": "Clip0004",
      "role": "totale",
      "confidence": "high",
      "reason": "...",
      "frame_path": ".\\artifacts\\camera_roles\\frames\\01-clip0004.jpg"
    }
  ]
}
```

## CLI

```powershell
$env:PYTHONPATH='src'
python -m vazer analyze roles --sync-map .\artifacts\sync_map.json --out .\artifacts\camera_roles.json --out-dir .\artifacts\camera_roles
```

## Desktop-Workflow

Im Desktop-Lauf passiert jetzt:

1. Dateien pruefen
2. Master / Kameras erkennen
3. Mittelframes der Kameras exportieren
4. ein AI-Call fuer die Rollen-Zuordnung
5. Review-Stopp im Desktop:
   - Frames sichtbar
   - AI-Zuweisungen sichtbar
   - User kann `Weiter` oder `Abbrechen`
6. erst danach startet der Audio-Sync

## Fallback

Wenn der AI-Call scheitert, faellt VAZer aktuell auf Dateinamen-Hinweise zurueck und markiert das im Projekt als Fallback.
