from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from . import __version__
from .fftools import MediaInfo, probe_media

EPSILON = 1e-6


@dataclass(slots=True)
class CoverageWindow:
    asset_id: str
    asset_path: str
    duration_seconds: float
    confidence: str
    accepted_anchor_count: int
    coarse_peak_ratio: float
    predicted_drift_over_hour_seconds: float
    speed: float
    offset_seconds: float
    overlap_start_seconds: float
    overlap_end_seconds: float
    primary_video: dict[str, Any] | None


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json_artifact(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_cut_plan(cut_plan: dict[str, Any], output_path: str) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(cut_plan, indent=2), encoding="utf-8")
    return destination


def _media_summary(media_info: MediaInfo) -> dict[str, Any]:
    primary_video = media_info.video_streams[0] if media_info.video_streams else None
    return {
        "format_name": media_info.format_name,
        "duration_seconds": media_info.duration_seconds,
        "audio_stream_count": len(media_info.audio_streams),
        "video_stream_count": len(media_info.video_streams),
        "primary_video": None
        if primary_video is None
        else {
            "absolute_stream_index": primary_video.absolute_stream_index,
            "codec_name": primary_video.codec_name,
            "duration_seconds": primary_video.duration_seconds,
            "width": primary_video.width,
            "height": primary_video.height,
            "frame_rate": primary_video.frame_rate,
        },
    }


def _require_duration(payload: dict[str, Any], label: str, fallback_path: str | None = None) -> float:
    duration = payload.get("duration_seconds")
    if isinstance(duration, (int, float)) and duration > 0:
        return float(duration)

    if fallback_path is None:
        raise ValueError(f"{label} does not expose a usable duration.")

    media_info = probe_media(fallback_path)
    if media_info.duration_seconds is None or media_info.duration_seconds <= 0:
        raise ValueError(f"{label} does not expose a usable duration.")

    payload["duration_seconds"] = media_info.duration_seconds
    return float(media_info.duration_seconds)


def _confidence_rank(value: str) -> int:
    return {
        "high": 3,
        "medium": 2,
        "low": 1,
    }.get(value, 0)


def _coverage_window(entry: dict[str, Any], master_duration_seconds: float) -> CoverageWindow | None:
    media_payload = entry.get("media")
    if not isinstance(media_payload, dict):
        media_payload = _media_summary(probe_media(entry["path"]))
        entry["media"] = media_payload

    duration_seconds = _require_duration(media_payload, f"Camera asset {entry['asset_id']}", entry["path"])
    mapping = entry["mapping"]
    speed = float(mapping["speed"])
    offset_seconds = float(mapping["offset_seconds"])
    if speed <= 0:
        return None

    overlap_start_seconds = max(0.0, -offset_seconds / speed)
    overlap_end_seconds = min(master_duration_seconds, (duration_seconds - offset_seconds) / speed)
    if overlap_end_seconds - overlap_start_seconds <= EPSILON:
        return None

    return CoverageWindow(
        asset_id=entry["asset_id"],
        asset_path=entry["path"],
        duration_seconds=duration_seconds,
        confidence=entry["summary"]["confidence"],
        accepted_anchor_count=len(entry["anchors"]["accepted"]),
        coarse_peak_ratio=float(entry["coarse"].get("peak_ratio") or 0.0),
        predicted_drift_over_hour_seconds=float(abs(entry["mapping"].get("predicted_drift_over_hour_seconds") or 0.0)),
        speed=speed,
        offset_seconds=offset_seconds,
        overlap_start_seconds=overlap_start_seconds,
        overlap_end_seconds=overlap_end_seconds,
        primary_video=media_payload.get("primary_video"),
    )


def _candidate_score(window: CoverageWindow) -> tuple[float, float, float, float, str]:
    return (
        float(_confidence_rank(window.confidence)),
        float(window.accepted_anchor_count),
        float(window.coarse_peak_ratio),
        -float(window.predicted_drift_over_hour_seconds),
        window.asset_id,
    )


def _merge_video_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not segments:
        return []

    merged: list[dict[str, Any]] = [segments[0].copy()]
    for current in segments[1:]:
        previous = merged[-1]
        if (
            previous["asset_id"] == current["asset_id"]
            and abs(previous["master_end_seconds"] - current["master_start_seconds"]) <= EPSILON
            and abs(previous["output_end_seconds"] - current["output_start_seconds"]) <= EPSILON
            and abs(previous["source_end_seconds"] - current["source_start_seconds"]) <= 1e-4
            and abs(previous["speed"] - current["speed"]) <= 1e-9
        ):
            previous["master_end_seconds"] = current["master_end_seconds"]
            previous["output_end_seconds"] = current["output_end_seconds"]
            previous["source_end_seconds"] = current["source_end_seconds"]
            previous["duration_seconds"] = previous["output_end_seconds"] - previous["output_start_seconds"]
            if previous["reason"] != current["reason"]:
                previous["reason"] = "Merged adjacent baseline selections."
            continue

        merged.append(current.copy())

    for index, segment in enumerate(merged, start=1):
        segment["id"] = f"video_{index:04d}"

    return merged


def build_baseline_cut_plan(
    sync_map: dict[str, Any],
    *,
    source_sync_map_path: str | None = None,
) -> dict[str, Any]:
    if sync_map.get("schema_version") != "vazer.sync_map.v1":
        raise ValueError("Unsupported sync_map schema version.")

    master_payload = sync_map.get("master")
    if not isinstance(master_payload, dict):
        raise ValueError("sync_map master payload is missing.")

    master_path = master_payload.get("path")
    if not isinstance(master_path, str) or not master_path:
        raise ValueError("sync_map master path is missing.")

    master_duration_seconds = _require_duration(master_payload, "Master audio", master_path)

    synced_entries = [
        entry
        for entry in sync_map.get("entries", [])
        if isinstance(entry, dict) and entry.get("status") == "synced"
    ]
    if not synced_entries:
        raise ValueError("sync_map does not contain any synced entries.")

    coverage_windows = [
        coverage
        for entry in synced_entries
        if (coverage := _coverage_window(entry, master_duration_seconds)) is not None
    ]
    if not coverage_windows:
        raise ValueError("No synced entry overlaps the master timeline.")

    boundary_candidates = {
        round(coverage.overlap_start_seconds, 6)
        for coverage in coverage_windows
    } | {
        round(coverage.overlap_end_seconds, 6)
        for coverage in coverage_windows
    }
    boundaries = sorted(boundary_candidates)

    initial_segments: list[dict[str, Any]] = []
    output_cursor = 0.0
    for master_start_seconds, master_end_seconds in zip(boundaries[:-1], boundaries[1:], strict=False):
        duration_seconds = master_end_seconds - master_start_seconds
        if duration_seconds <= EPSILON:
            continue

        active_coverages = [
            coverage
            for coverage in coverage_windows
            if coverage.overlap_start_seconds <= master_start_seconds + EPSILON
            and coverage.overlap_end_seconds >= master_end_seconds - EPSILON
        ]
        if not active_coverages:
            continue

        selected = max(active_coverages, key=_candidate_score)
        source_start_seconds = selected.speed * master_start_seconds + selected.offset_seconds
        source_end_seconds = selected.speed * master_end_seconds + selected.offset_seconds
        source_start_seconds = max(0.0, source_start_seconds)
        source_end_seconds = min(selected.duration_seconds, source_end_seconds)

        initial_segments.append(
            {
                "id": "video_0000",
                "type": "camera",
                "strategy": "baseline_best_available",
                "asset_id": selected.asset_id,
                "asset_path": selected.asset_path,
                "confidence": selected.confidence,
                "master_start_seconds": master_start_seconds,
                "master_end_seconds": master_end_seconds,
                "output_start_seconds": output_cursor,
                "output_end_seconds": output_cursor + duration_seconds,
                "duration_seconds": duration_seconds,
                "source_start_seconds": source_start_seconds,
                "source_end_seconds": source_end_seconds,
                "speed": selected.speed,
                "reason": (
                    "Only synced camera covering interval."
                    if len(active_coverages) == 1
                    else "Highest-confidence synced camera covering interval."
                ),
            }
        )
        output_cursor += duration_seconds

    video_segments = _merge_video_segments(initial_segments)
    if not video_segments:
        raise ValueError("Failed to derive any baseline video segments from sync_map.")

    audio_segments = [
        {
            "id": f"audio_{index:04d}",
            "type": "master_audio",
            "source_path": master_path,
            "master_start_seconds": segment["master_start_seconds"],
            "master_end_seconds": segment["master_end_seconds"],
            "output_start_seconds": segment["output_start_seconds"],
            "output_end_seconds": segment["output_end_seconds"],
            "duration_seconds": segment["duration_seconds"],
            "source_start_seconds": segment["master_start_seconds"],
            "source_end_seconds": segment["master_end_seconds"],
        }
        for index, segment in enumerate(video_segments, start=1)
    ]

    best_render_window = max(coverage_windows, key=_candidate_score)
    primary_video = best_render_window.primary_video or {}
    render_defaults = {
        "width": primary_video.get("width") or 1920,
        "height": primary_video.get("height") or 1080,
        "fps": primary_video.get("frame_rate") or 25.0,
        "pixel_format": "yuv420p",
        "video_codec": "libx264",
        "audio_codec": "aac",
    }

    kept_intervals = [
        {
            "master_start_seconds": segment["master_start_seconds"],
            "master_end_seconds": segment["master_end_seconds"],
            "asset_id": segment["asset_id"],
        }
        for segment in video_segments
    ]
    selected_assets = sorted({segment["asset_id"] for segment in video_segments})
    dropped_assets = sorted({entry["asset_id"] for entry in synced_entries} - set(selected_assets))

    return {
        "schema_version": "vazer.cut_plan.v1",
        "generated_at_utc": _utc_timestamp(),
        "tool": {
            "name": "vazer",
            "version": __version__,
        },
        "source_sync_map": {
            "schema_version": sync_map["schema_version"],
            "path": source_sync_map_path,
        },
        "master_audio": {
            "path": master_path,
            "duration_seconds": master_duration_seconds,
            "format_name": master_payload.get("format_name"),
        },
        "render_defaults": render_defaults,
        "timeline": {
            "master_span_start_seconds": video_segments[0]["master_start_seconds"],
            "master_span_end_seconds": video_segments[-1]["master_end_seconds"],
            "output_duration_seconds": video_segments[-1]["output_end_seconds"],
            "segment_count": len(video_segments),
            "kept_intervals": kept_intervals,
        },
        "video_segments": video_segments,
        "audio_segments": audio_segments,
        "summary": {
            "selected_assets": selected_assets,
            "dropped_assets": dropped_assets,
            "synced_assets": len(synced_entries),
            "video_segments": len(video_segments),
            "output_duration_seconds": video_segments[-1]["output_end_seconds"],
        },
    }
