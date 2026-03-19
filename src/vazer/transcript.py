from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any

TRANSCRIPT_SCHEMA_VERSION = "vazer.transcript.v1"


@dataclass(slots=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str
    speaker: str | None


@dataclass(slots=True)
class TranscriptWord:
    start_seconds: float
    end_seconds: float
    text: str


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


def _normalize_word(word: dict[str, Any]) -> TranscriptWord:
    start_value = word.get("start_seconds", word.get("start"))
    end_value = word.get("end_seconds", word.get("end"))
    text = word.get("text", word.get("word", ""))

    start_seconds = _coerce_float(start_value, "start_seconds")
    end_seconds = _coerce_float(end_value, "end_seconds")
    if end_seconds <= start_seconds:
        raise ValueError("Transcript word end must be greater than start.")

    normalized_text = "" if text is None else str(text).strip()
    if not normalized_text:
        raise ValueError("Transcript word text must not be empty.")

    return TranscriptWord(
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        text=normalized_text,
    )


def load_transcript_artifact(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    raw_words: list[Any] = []
    text = None

    if isinstance(payload, list):
        raw_segments = payload
        schema_version = "external.list"
        source = {"path": path, "schema_version": schema_version}
    elif isinstance(payload, dict):
        if payload.get("schema_version") == "vazer.transcript.v1":
            raw_segments = payload.get("segments", [])
            raw_words = payload.get("words", [])
            text = payload.get("text")
            source = {
                "path": path,
                "schema_version": payload["schema_version"],
            }
        elif isinstance(payload.get("segments"), list):
            raw_segments = payload["segments"]
            raw_words = payload.get("words", [])
            text = payload.get("text")
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
    if raw_words and not isinstance(raw_words, list):
        raise ValueError("Transcript words must be a list.")

    segments = [_normalize_segment(segment) for segment in raw_segments if isinstance(segment, dict)]
    segments.sort(key=lambda segment: (segment.start_seconds, segment.end_seconds))
    words = [_normalize_word(word) for word in raw_words if isinstance(word, dict)]
    words.sort(key=lambda word: (word.start_seconds, word.end_seconds))

    return {
        "source": source,
        "text": None if text is None else str(text),
        "segments": [
            {
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "text": segment.text,
                "speaker": segment.speaker,
            }
            for segment in segments
        ],
        "words": [
            {
                "start_seconds": word.start_seconds,
                "end_seconds": word.end_seconds,
                "text": word.text,
            }
            for word in words
        ],
    }


def transcript_source_metadata(
    transcript_artifact: dict[str, Any] | None,
    *,
    path: str | None = None,
) -> dict[str, Any] | None:
    if transcript_artifact is None:
        return None

    existing_source = transcript_artifact.get("source")
    if isinstance(existing_source, dict):
        schema_version = existing_source.get("schema_version")
        normalized = {
            **existing_source,
            "schema_version": str(schema_version or transcript_artifact.get("schema_version") or "external.transcript"),
        }
        if path is not None:
            normalized["path"] = path
        elif "path" not in normalized:
            normalized["path"] = None
        return normalized

    schema_version = transcript_artifact.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version:
        schema_version = "external.transcript"

    return {
        "schema_version": schema_version,
        "path": path,
    }
