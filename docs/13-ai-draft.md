# AI Draft

## Ziel

`plan ai-draft` ist der erste echte OpenAI-gestuetzte Planer in VAZer.

Er nimmt:

- `sync_map`
- optional `analysis_map`
- optional `transcript`
- ein `visual_packet`

und fragt damit ein multimodales Modell nach einem ersten theater-spezifischen Schnittentwurf.

## Wichtige Einschraenkung

Der aktuelle AI-Draft arbeitet auf der Zeitspanne, die im `visual_packet` steckt.

Das bedeutet:

- fuer kleine Test-Slices oder lokale Teilfenster ist ein einzelner AI-Call sinnvoll
- fuer eine komplette 1-3h Auffuehrung ist ein einziger Mega-Call nicht die Zielarchitektur

Der realistischere Weg fuer lange Shows ist:

1. grober oder lokaler `visual_packet`
2. AI-Draft fuer Teilbereiche
3. `validate`
4. `repair`
5. nur bei Bedarf nochmal lokaler AI-Call

## Warum nicht die ganze Show in einem Call

- zu viele Bilder
- zu viel Transcript
- teurer und schwerer debugbar
- schlechtere Kontrolle ueber lokale Fehlentscheidungen

## CLI

```powershell
$env:PYTHONPATH='src'
python -m vazer plan ai-draft --sync-map .\artifacts\sync_map.json --visual-packet .\artifacts\visual_packet.json --analysis .\artifacts\analysis_map.json --transcript .\artifacts\transcript.json --out .\artifacts\cut_plan.ai.json
```

Optionale Tuning-Flags:

- `--model`
- `--max-output-tokens`
- `--temperature`
- `--notes`
- `--master-start`
- `--master-end`

## Aktueller Modellpfad

Der aktuelle Default ist:

- `gpt-4.1-mini`

Der Prompt kommt aus:

- [11-llm-draft-prompt.md](./11-llm-draft-prompt.md)
- [draft_prompt.py](../src/vazer/draft_prompt.py)

## Output

Das Ergebnis ist direkt ein `vazer.cut_plan.v1` mit zusaetzlichem `ai_draft`-Block:

```json
{
  "ai_draft": {
    "provider": "openai",
    "model": "gpt-4.1-mini",
    "response_id": "resp_...",
    "summary": "...",
    "raw_segments": [],
    "usage": {},
    "fallback_asset_id": "Clip0004"
  }
}
```

## Aktueller Teststand

Der neue Call wurde bereits auf einem kleinen 20s-Testfenster mit Bildern und Transcript erfolgreich ausgefuehrt.

Das zeigt:

- OpenAI-Call funktioniert
- Bilder plus Text plus strukturierte Ausgabe funktionieren
- das Modell kann direkt in ein `cut_plan` kompiliert werden

## Bewusste Grenzen von v1

- noch keine Chunk-Orchestrierung ueber eine komplette Auffuehrung
- noch keine automatische AI-Re-Planung nur fuer Problemcuts
- noch kein Prompt-Caching oder Kostensteuerung ueber groessere Batches
