from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any


@dataclass(slots=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str
    speaker: str | None


def _coerce_float(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Transcript segment field '{field_name}' is not numeric.") from error
    return number


def _normalize_segment(segment: dict[str, Any]) -> TranscriptSegment:
    start_value = segment.get("start_seconds", segment.get("start"))
    end_value = segment.get("end_seconds", segment.get("end"))
    text = segment.get("text", "")

    start_seconds = _coerce_float(start_value, "start_seconds")
    end_seconds = _coerce_float(end_value, "end_seconds")
    if end_seconds <= start_seconds:
        raise ValueError("Transcript segment end must be greater than start.")

    return TranscriptSegment(
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        text="" if text is None else str(text).strip(),
        speaker=None if segment.get("speaker") is None else str(segment["speaker"]).strip(),
    )


def load_transcript_artifact(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))

    if isinstance(payload, list):
        raw_segments = payload
        schema_version = "external.list"
        source = {"path": path, "schema_version": schema_version}
    elif isinstance(payload, dict):
        if payload.get("schema_version") == "vazer.transcript.v1":
            raw_segments = payload.get("segments", [])
            source = {
                "path": path,
                "schema_version": payload["schema_version"],
            }
        elif isinstance(payload.get("segments"), list):
            raw_segments = payload["segments"]
            source = {
                "path": path,
                "schema_version": payload.get("schema_version", "external.segments"),
            }
        else:
            raise ValueError("Unsupported transcript artifact format.")
    else:
        raise ValueError("Unsupported transcript artifact format.")

    if not isinstance(raw_segments, list):
        raise ValueError("Transcript segments must be a list.")

    segments = [_normalize_segment(segment) for segment in raw_segments if isinstance(segment, dict)]
    segments.sort(key=lambda segment: (segment.start_seconds, segment.end_seconds))

    return {
        "source": source,
        "segments": [
            {
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "text": segment.text,
                "speaker": segment.speaker,
            }
            for segment in segments
        ],
    }
