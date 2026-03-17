# cut_validation und repair

## Ziel

Der aktuelle Review-Loop fuer VAZer ist bewusst lokal und billig:

1. `plan draft`
2. `plan validate`
3. `plan repair`

Der Validator rechnet nicht das ganze Material neu durch. Er schaut nur auf die vorgeschlagenen Cut-Stellen.

## Warum dieser Weg

Das ist fuer den MVP besser als ein kompletter zweiter AI-Plan:

- billiger
- reproduzierbarer
- leichter debugbar
- technische Checks bleiben deterministisch

## `cut_validation`

`cut_validation` bewertet jeden Kamerawechsel lokal ueber:

- Transcript-Wortgrenzen in einem kleinen Suchfenster
- billige Analysefenster aus `analysis_map`
- sparse Cut-Frame-Probes direkt aus den Originaldateien
- optionale Alternativkameras aus `sync_map`

Top-Level:

```json
{
  "schema_version": "vazer.cut_validation.v1",
  "generated_at_utc": "2026-03-17T21:45:00Z",
  "source_cut_plan": {},
  "source_sync_map": null,
  "source_analysis_map": null,
  "source_transcript": null,
  "options": {},
  "cuts": [],
  "summary": {}
}
```

Ein einzelner Cut:

```json
{
  "id": "cut_0007",
  "status": "warn",
  "current_cut_seconds": 1720.27,
  "outgoing_segment_id": "video_0007",
  "incoming_segment_id": "video_0008",
  "transcript": {
    "has_words": true,
    "speech_near_cut": true,
    "preferred_boundary": {
      "target_cut_seconds": 1720.22,
      "delta_seconds": -0.05
    }
  },
  "issues": [
    {
      "code": "better_alternative_available",
      "severity": "warn",
      "message": "Clip0004 scores materially better than the current incoming camera around this cut."
    }
  ],
  "recommended_action": {
    "target_cut_seconds": 1720.22,
    "shift_seconds": -0.05,
    "preferred_incoming_asset_id": "Clip0004"
  }
}
```

## Aktuelle Regeln

Der Validator markiert aktuell unter anderem:

- `off_word_boundary`
- `outgoing_soft_analysis`
- `incoming_soft_analysis`
- `outgoing_soft_frame`
- `incoming_soft_frame`
- `better_alternative_available`

Das ist absichtlich noch keine perfekte semantische Bewertung. Es ist ein technischer Guard-Rail-Layer.

## `repair`

`plan repair` nimmt ein bestehendes `cut_plan` plus `cut_validation` und versucht nur lokale, deterministische Korrekturen:

- Cut leicht an eine bessere Wortgrenze verschieben
- eingehende Kamera gegen eine lokal bessere Alternative tauschen
- danach benachbarte identische Segmente wieder zusammenfuehren

Das Ergebnis bleibt ein normales `vazer.cut_plan.v1`, aber mit:

- `planning_stage = repaired`
- `source_validation_report`
- `repair.applied_cut_actions`

## CLI

Validierung:

```powershell
$env:PYTHONPATH='src'
python -m vazer plan validate --cut-plan .\artifacts\cut_plan.json --out .\artifacts\cut_validation.json
```

Reparatur:

```powershell
$env:PYTHONPATH='src'
python -m vazer plan repair --cut-plan .\artifacts\cut_plan.json --validation .\artifacts\cut_validation.json --out .\artifacts\cut_plan.repaired.json
```

## Aktueller Teststand

Es gibt jetzt zwei sinnvolle Smoke-Tests:

- realer Mehrkamera-Draft auf `D:\VAZ_Chaos\Medien`
  - `62` gepruefte Cuts
  - `32 ok`, `29 warn`, `1 fail`
- synthetischer 20s-Slice mit absichtlich schiefen Cuts
  - `2` reparierbare Cuts
  - beide Cuts wurden verschoben
  - beide eingehenden Kameras wurden lokal gegen `Clip0004` getauscht

## Bewusste Grenzen von v1

- noch keine semantische Cut-Reparatur ueber groessere Bereiche
- noch keine Blenden oder Transition-Rewrites
- noch keine manuelle Review-UI
- lokale Frame-Probes sind guenstig, aber auf vielen Cuts trotzdem merklich langsamer als reine JSON-Regeln
