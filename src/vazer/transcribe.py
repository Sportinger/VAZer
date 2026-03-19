from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from . import __version__
from .fftools import probe_media
from .process_manager import run_managed


DEFAULT_TRANSCRIBE_MODEL = "whisper-1"
DEFAULT_AUDIO_SAMPLE_RATE = 16000
DEFAULT_AUDIO_BITRATE = "64k"
DEFAULT_CHUNK_SECONDS = 600.0
PROMPT_TAIL_CHARS = 600


@dataclass(slots=True)
class TranscriptionOptions:
    model: str = DEFAULT_TRANSCRIBE_MODEL
    language: str | None = None
    prompt: str | None = None
    chunk_seconds: float = DEFAULT_CHUNK_SECONDS
    audio_sample_rate: int = DEFAULT_AUDIO_SAMPLE_RATE
    audio_bitrate: str = DEFAULT_AUDIO_BITRATE


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_api_key() -> str:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError as error:
        raise ValueError(
            "python-dotenv is not installed. Run the project dependencies first, for example `pip install -e .`."
        ) from error

    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set. Put it in .env or your environment.")
    return api_key


def _build_client() -> Any:
    try:
        from openai import OpenAI
    except ModuleNotFoundError as error:
        raise ValueError(
            "openai is not installed. Run the project dependencies first, for example `pip install -e .`."
        ) from error

    return OpenAI(api_key=_load_api_key())


def _resolve_master_path(master_path: str | None, sync_map_path: str | None) -> str:
    if master_path:
        return master_path

    if not sync_map_path:
        raise ValueError("Either --master or --sync-map is required.")

    payload = json.loads(Path(sync_map_path).read_text(encoding="utf-8-sig"))
    master_payload = payload.get("master")
    if not isinstance(master_payload, dict) or not isinstance(master_payload.get("path"), str):
        raise ValueError("sync_map does not expose a usable master path.")
    return master_payload["path"]


def _export_audio_chunk(
    source_path: str,
    *,
    start_seconds: float,
    duration_seconds: float,
    sample_rate: int,
    audio_bitrate: str,
    output_path: str,
) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_seconds:.6f}",
        "-t",
        f"{duration_seconds:.6f}",
        "-i",
        source_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        output_path,
    ]
    run_managed(command, check=True, capture_output=True)


def _response_to_dict(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    raise ValueError("Unsupported transcription response type.")


def _prompt_for_chunk(base_prompt: str | None, previous_text_tail: str | None) -> str | None:
    pieces = []
    if base_prompt:
        pieces.append(base_prompt.strip())
    if previous_text_tail:
        pieces.append(previous_text_tail.strip())

    prompt = "\n\n".join(piece for piece in pieces if piece)
    return prompt or None


def _timestamp_granularities_for_model(model: str) -> list[str]:
    if model == "whisper-1":
        return ["segment", "word"]
    return ["segment"]


def build_master_transcript(
    master_path: str,
    *,
    source_sync_map_path: str | None = None,
    options: TranscriptionOptions | None = None,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    transcription_options = options or TranscriptionOptions()
    client = _build_client()
    master_info = probe_media(master_path)
    if master_info.duration_seconds is None or master_info.duration_seconds <= 0:
        raise ValueError("Master audio does not expose a usable duration.")

    duration_seconds = float(master_info.duration_seconds)
    chunk_count = max(1, math.ceil(duration_seconds / transcription_options.chunk_seconds))
    chunk_ranges = [
        (
            index,
            index * transcription_options.chunk_seconds,
            min(duration_seconds, (index + 1) * transcription_options.chunk_seconds),
        )
        for index in range(chunk_count)
    ]

    chunks: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    full_text_parts: list[str] = []
    previous_text_tail: str | None = None
    detected_language: str | None = None

    if callable(on_progress):
        on_progress(0, chunk_count, "Preparing transcript chunks.")

    with tempfile.TemporaryDirectory(prefix="vazer-transcribe-") as temp_dir:
        temp_root = Path(temp_dir)
        for index, chunk_start_seconds, chunk_end_seconds in chunk_ranges:
            chunk_duration_seconds = chunk_end_seconds - chunk_start_seconds
            chunk_path = temp_root / f"chunk_{index + 1:04d}.m4a"
            _export_audio_chunk(
                master_path,
                start_seconds=chunk_start_seconds,
                duration_seconds=chunk_duration_seconds,
                sample_rate=transcription_options.audio_sample_rate,
                audio_bitrate=transcription_options.audio_bitrate,
                output_path=str(chunk_path),
            )

            prompt = _prompt_for_chunk(transcription_options.prompt, previous_text_tail)
            request_kwargs: dict[str, Any] = {
                "model": transcription_options.model,
                "response_format": "verbose_json",
                "timestamp_granularities": _timestamp_granularities_for_model(transcription_options.model),
            }
            if transcription_options.language:
                request_kwargs["language"] = transcription_options.language
            if prompt:
                request_kwargs["prompt"] = prompt

            with chunk_path.open("rb") as audio_file:
                response = client.audio.transcriptions.create(
                    file=audio_file,
                    **request_kwargs,
                )

            payload = _response_to_dict(response)
            chunk_text = str(payload.get("text") or "").strip()
            chunk_language = payload.get("language")
            if isinstance(chunk_language, str) and chunk_language:
                detected_language = chunk_language

            raw_segments = payload.get("segments", [])
            normalized_chunk_segments: list[dict[str, Any]] = []
            for raw_segment in raw_segments if isinstance(raw_segments, list) else []:
                if not isinstance(raw_segment, dict):
                    continue
                start_value = raw_segment.get("start")
                end_value = raw_segment.get("end")
                if start_value is None or end_value is None:
                    continue

                start_seconds = float(start_value) + chunk_start_seconds
                end_seconds = float(end_value) + chunk_start_seconds
                text = str(raw_segment.get("text") or "").strip()
                segment = {
                    "start_seconds": start_seconds,
                    "end_seconds": end_seconds,
                    "text": text,
                    "speaker": None,
                    "chunk_index": index + 1,
                }
                normalized_chunk_segments.append(segment)
                segments.append(segment)

            raw_words = payload.get("words", [])
            normalized_chunk_words: list[dict[str, Any]] = []
            for raw_word in raw_words if isinstance(raw_words, list) else []:
                if not isinstance(raw_word, dict):
                    continue
                start_value = raw_word.get("start")
                end_value = raw_word.get("end")
                if start_value is None or end_value is None:
                    continue

                word = {
                    "start_seconds": float(start_value) + chunk_start_seconds,
                    "end_seconds": float(end_value) + chunk_start_seconds,
                    "text": str(raw_word.get("word") or raw_word.get("text") or "").strip(),
                    "chunk_index": index + 1,
                }
                if word["text"]:
                    normalized_chunk_words.append(word)
                    words.append(word)

            chunks.append(
                {
                    "index": index + 1,
                    "start_seconds": chunk_start_seconds,
                    "end_seconds": chunk_end_seconds,
                    "duration_seconds": chunk_duration_seconds,
                    "upload_format": "m4a",
                    "audio_sample_rate": transcription_options.audio_sample_rate,
                    "audio_bitrate": transcription_options.audio_bitrate,
                    "language": chunk_language,
                    "text": chunk_text,
                    "segment_count": len(normalized_chunk_segments),
                    "word_count": len(normalized_chunk_words),
                }
            )
            if chunk_text:
                full_text_parts.append(chunk_text)
                previous_text_tail = chunk_text[-PROMPT_TAIL_CHARS:]

            if callable(on_progress):
                on_progress(
                    index + 1,
                    chunk_count,
                    f"Transcript chunk {index + 1}/{chunk_count}",
                )

    full_text = "\n\n".join(part for part in full_text_parts if part).strip()
    return {
        "schema_version": "vazer.transcript.v1",
        "generated_at_utc": _utc_timestamp(),
        "tool": {
            "name": "vazer",
            "version": __version__,
        },
        "provider": {
            "name": "openai",
            "model": transcription_options.model,
        },
        "source_sync_map": None
        if source_sync_map_path is None
        else {
            "schema_version": "vazer.sync_map.v1",
            "path": source_sync_map_path,
        },
        "master_audio": {
            "path": master_path,
            "duration_seconds": duration_seconds,
            "format_name": master_info.format_name,
        },
        "options": {
            "language": transcription_options.language,
            "chunk_seconds": transcription_options.chunk_seconds,
            "audio_sample_rate": transcription_options.audio_sample_rate,
            "audio_bitrate": transcription_options.audio_bitrate,
            "prompt_supplied": bool(transcription_options.prompt),
        },
        "language": detected_language or transcription_options.language,
        "text": full_text,
        "chunks": chunks,
        "segments": segments,
        "words": words,
        "summary": {
            "chunk_count": len(chunks),
            "segment_count": len(segments),
            "word_count": len(words),
            "character_count": len(full_text),
        },
    }


def write_transcript_artifact(transcript_artifact: dict[str, Any], output_path: str) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(transcript_artifact, indent=2), encoding="utf-8")
    return destination
