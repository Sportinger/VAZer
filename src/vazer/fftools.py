from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(slots=True)
class AudioStreamInfo:
    absolute_stream_index: int
    map_specifier: str
    codec_name: str | None
    sample_rate: int | None
    channels: int | None
    duration_seconds: float | None
    bit_rate: int | None
    tags: dict[str, str]


@dataclass(slots=True)
class VideoStreamInfo:
    absolute_stream_index: int
    codec_name: str | None
    duration_seconds: float | None
    tags: dict[str, str]


@dataclass(slots=True)
class MediaInfo:
    path: str
    format_name: str | None
    duration_seconds: float | None
    tags: dict[str, str]
    audio_streams: list[AudioStreamInfo]
    video_streams: list[VideoStreamInfo]


def _run_command(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, check=True, capture_output=True)


def _optional_int(value: Any) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def probe_media(path: str) -> MediaInfo:
    result = _run_command(
        [
            "ffprobe",
            "-hide_banner",
            "-loglevel",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            path,
        ]
    )

    payload = json.loads(result.stdout.decode("utf-8"))
    format_payload = payload.get("format", {})
    format_duration = _optional_float(format_payload.get("duration"))

    audio_streams: list[AudioStreamInfo] = []
    video_streams: list[VideoStreamInfo] = []

    for stream in payload.get("streams", []):
        if not isinstance(stream, dict):
            continue

        index = stream.get("index")
        if not isinstance(index, int):
            continue

        codec_type = stream.get("codec_type")
        if codec_type == "audio":
            audio_streams.append(
                AudioStreamInfo(
                    absolute_stream_index=index,
                    map_specifier=f"0:{index}",
                    codec_name=stream.get("codec_name"),
                    sample_rate=_optional_int(stream.get("sample_rate")),
                    channels=_optional_int(stream.get("channels")),
                    duration_seconds=_optional_float(stream.get("duration")) or format_duration,
                    bit_rate=_optional_int(stream.get("bit_rate")),
                    tags=dict(stream.get("tags", {})),
                )
            )
        elif codec_type == "video":
            video_streams.append(
                VideoStreamInfo(
                    absolute_stream_index=index,
                    codec_name=stream.get("codec_name"),
                    duration_seconds=_optional_float(stream.get("duration")) or format_duration,
                    tags=dict(stream.get("tags", {})),
                )
            )

    return MediaInfo(
        path=path,
        format_name=format_payload.get("format_name"),
        duration_seconds=format_duration,
        tags=dict(format_payload.get("tags", {})),
        audio_streams=audio_streams,
        video_streams=video_streams,
    )


def decode_audio(
    path: str,
    *,
    map_specifier: str | None = None,
    start_seconds: float | None = None,
    duration_seconds: float | None = None,
    sample_rate: int,
    filters: list[str] | None = None,
) -> np.ndarray:
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error"]

    if start_seconds is not None:
        args.extend(["-ss", f"{start_seconds:.6f}"])

    args.extend(["-i", path])

    if duration_seconds is not None:
        args.extend(["-t", f"{duration_seconds:.6f}"])

    if map_specifier is not None:
        args.extend(["-map", map_specifier])

    args.extend(["-vn", "-ac", "1", "-ar", str(sample_rate)])

    if filters:
        args.extend(["-af", ",".join(filters)])

    args.extend(["-f", "f32le", "pipe:1"])
    result = _run_command(args)
    return np.frombuffer(result.stdout, dtype=np.float32).copy()
