from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .ai_draft import AIDraftOptions, build_ai_draft_cut_plan
from .cut_plan import EPSILON, _merge_video_segments, _require_duration
from .visual_packet import VisualPacketOptions, build_visual_packet


@dataclass(slots=True)
class TheaterPipelineOptions:
    chunk_seconds: float = 300.0
    visual_interval_seconds: float = 90.0
    visual_window_context_seconds: float = 14.0
    visual_transcript_context_seconds: float = 18.0
    visual_image_width: int = 640
    visual_image_quality: int = 88
    ai_model: str = "gpt-4.1-mini"
    ai_temperature: float = 0.2
    ai_max_output_tokens: int = 6000
    ai_notes: str | None = None


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _rebuild_segment_timing(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rebuilt: list[dict[str, Any]] = []
    output_cursor = 0.0
    for index, segment in enumerate(segments, start=1):
        duration_seconds = float(segment["master_end_seconds"]) - float(segment["master_start_seconds"])
        if duration_seconds <= EPSILON:
            continue

        updated = dict(segment)
        updated["id"] = f"video_{index:04d}"
        updated["duration_seconds"] = duration_seconds
        updated["output_start_seconds"] = output_cursor
        output_cursor += duration_seconds
        updated["output_end_seconds"] = output_cursor
        rebuilt.append(updated)
    return rebuilt


def _audio_segments_from_video(video_segments: list[dict[str, Any]], master_path: str) -> list[dict[str, Any]]:
    return [
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


def _chunk_spans(duration_seconds: float, chunk_seconds: float) -> list[tuple[float, float]]:
    if duration_seconds <= EPSILON:
        raise ValueError("Master duration is too small for chunked planning.")
    actual_chunk_seconds = max(60.0, float(chunk_seconds))
    spans: list[tuple[float, float]] = []
    start_seconds = 0.0
    while start_seconds < duration_seconds - EPSILON:
        end_seconds = min(duration_seconds, start_seconds + actual_chunk_seconds)
        spans.append((start_seconds, end_seconds))
        start_seconds = end_seconds
    return spans


def _merge_chunk_cut_plans(
    *,
    chunk_plans: list[dict[str, Any]],
    sync_map: dict[str, Any],
    visual_packet_path: str | None,
    source_sync_map_path: str | None,
    source_analysis_path: str | None,
    source_transcript_path: str | None,
    options: TheaterPipelineOptions,
) -> dict[str, Any]:
    if not chunk_plans:
        raise ValueError("At least one chunk cut plan is required.")

    master_payload = sync_map["master"]
    master_path = master_payload["path"]
    master_duration_seconds = _require_duration(master_payload, "Master audio", master_path)
    raw_video_segments = [
        dict(segment)
        for chunk_plan in chunk_plans
        for segment in chunk_plan.get("video_segments", [])
        if isinstance(segment, dict)
    ]
    if not raw_video_segments:
        raise ValueError("Chunked AI planning produced no video segments.")

    raw_video_segments.sort(
        key=lambda segment: (float(segment["master_start_seconds"]), float(segment["master_end_seconds"]))
    )
    merged_video_segments = _rebuild_segment_timing(_merge_video_segments(raw_video_segments))
    audio_segments = _audio_segments_from_video(merged_video_segments, master_path)
    selected_assets = sorted({segment["asset_id"] for segment in merged_video_segments})
    synced_assets = sorted(
        {
            entry["asset_id"]
            for entry in sync_map.get("entries", [])
            if isinstance(entry, dict) and entry.get("status") == "synced"
        }
    )
    dropped_assets = sorted(set(synced_assets) - set(selected_assets))
    render_defaults = dict(chunk_plans[0]["render_defaults"])
    kept_intervals = [
        {
            "master_start_seconds": segment["master_start_seconds"],
            "master_end_seconds": segment["master_end_seconds"],
            "asset_id": segment["asset_id"],
        }
        for segment in merged_video_segments
    ]

    return {
        "schema_version": "vazer.cut_plan.v1",
        "planning_stage": "draft",
        "generated_at_utc": _utc_timestamp(),
        "tool": {
            "name": "vazer",
            "version": __version__,
        },
        "source_sync_map": {
            "schema_version": sync_map["schema_version"],
            "path": source_sync_map_path,
        },
        "source_analysis_map": None
        if source_analysis_path is None
        else {
            "schema_version": "vazer.analysis_map.v1",
            "path": source_analysis_path,
        },
        "source_transcript": None
        if source_transcript_path is None
        else {
            "schema_version": "vazer.transcript.v1",
            "path": source_transcript_path,
        },
        "source_visual_packet": None
        if visual_packet_path is None
        else {
            "schema_version": "vazer.visual_packet.v1",
            "path": visual_packet_path,
        },
        "master_audio": {
            "path": master_path,
            "duration_seconds": master_duration_seconds,
            "format_name": master_payload.get("format_name"),
        },
        "render_defaults": render_defaults,
        "timeline": {
            "master_span_start_seconds": 0.0,
            "master_span_end_seconds": master_duration_seconds,
            "output_duration_seconds": merged_video_segments[-1]["output_end_seconds"],
            "segment_count": len(merged_video_segments),
            "kept_intervals": kept_intervals,
        },
        "video_segments": merged_video_segments,
        "audio_segments": audio_segments,
        "summary": {
            "planning_stage": "draft",
            "selected_assets": selected_assets,
            "dropped_assets": dropped_assets,
            "synced_assets": len(synced_assets),
            "video_segments": len(merged_video_segments),
            "output_duration_seconds": merged_video_segments[-1]["output_end_seconds"],
            "signal_aware": True,
            "word_timestamps_available": True,
        },
        "ai_draft_chunked": {
            "chunk_count": len(chunk_plans),
            "chunk_seconds": options.chunk_seconds,
            "model": options.ai_model,
            "notes": options.ai_notes,
            "chunks": [
                {
                    "index": index,
                    "master_start_seconds": chunk_plan["timeline"]["master_span_start_seconds"],
                    "master_end_seconds": chunk_plan["timeline"]["master_span_end_seconds"],
                    "segment_count": len(chunk_plan["video_segments"]),
                    "response_id": chunk_plan.get("ai_draft", {}).get("response_id"),
                }
                for index, chunk_plan in enumerate(chunk_plans, start=1)
            ],
        },
    }


def build_chunked_ai_draft_bundle(
    sync_map: dict[str, Any],
    *,
    source_sync_map_path: str | None = None,
    analysis_map: dict[str, Any] | None = None,
    source_analysis_path: str | None = None,
    transcript_artifact: dict[str, Any] | None = None,
    source_transcript_path: str | None = None,
    role_overrides: dict[str, str] | None = None,
    output_dir: str,
    options: TheaterPipelineOptions | None = None,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    if sync_map.get("schema_version") != "vazer.sync_map.v1":
        raise ValueError("Unsupported sync_map schema version.")
    if analysis_map is None:
        raise ValueError("analysis_map is required for chunked theater AI planning.")
    if transcript_artifact is None:
        raise ValueError("transcript_artifact is required for chunked theater AI planning.")

    pipeline_options = options or TheaterPipelineOptions()
    output_root = Path(output_dir)
    visual_packet = build_visual_packet(
        sync_map,
        source_sync_map_path=source_sync_map_path,
        analysis_map=analysis_map,
        source_analysis_path=source_analysis_path,
        transcript_artifact=transcript_artifact,
        source_transcript_path=source_transcript_path,
        output_dir=str(output_root / "visual_packet"),
        options=VisualPacketOptions(
            mode="overview",
            interval_seconds=pipeline_options.visual_interval_seconds,
            window_context_seconds=pipeline_options.visual_window_context_seconds,
            transcript_context_seconds=pipeline_options.visual_transcript_context_seconds,
            image_width=pipeline_options.visual_image_width,
            image_quality=pipeline_options.visual_image_quality,
            role_overrides=role_overrides,
        ),
    )
    master_payload = sync_map["master"]
    master_path = master_payload["path"]
    master_duration_seconds = _require_duration(master_payload, "Master audio", master_path)
    spans = _chunk_spans(master_duration_seconds, pipeline_options.chunk_seconds)
    total_chunks = max(1, len(spans))

    chunk_plans: list[dict[str, Any]] = []
    for chunk_index, (chunk_start_seconds, chunk_end_seconds) in enumerate(spans, start=1):
        if callable(on_progress):
            on_progress(chunk_index - 1, total_chunks, f"KI-Chunk {chunk_index}/{total_chunks}")
        chunk_cut_plan = build_ai_draft_cut_plan(
            sync_map,
            source_sync_map_path=source_sync_map_path,
            visual_packet=visual_packet,
            source_visual_packet_path=str(output_root / "visual_packet.json"),
            analysis_map=analysis_map,
            source_analysis_path=source_analysis_path,
            transcript_artifact=transcript_artifact,
            source_transcript_path=source_transcript_path,
            options=AIDraftOptions(
                model=pipeline_options.ai_model,
                max_output_tokens=pipeline_options.ai_max_output_tokens,
                temperature=pipeline_options.ai_temperature,
                user_notes=pipeline_options.ai_notes,
                master_start_seconds=chunk_start_seconds,
                master_end_seconds=chunk_end_seconds,
            ),
        )
        chunk_cut_plan["ai_draft_chunk"] = {
            "index": chunk_index,
            "master_start_seconds": chunk_start_seconds,
            "master_end_seconds": chunk_end_seconds,
        }
        chunk_plans.append(chunk_cut_plan)
        if callable(on_progress):
            on_progress(chunk_index, total_chunks, f"KI-Chunk {chunk_index}/{total_chunks}")

    combined_cut_plan = _merge_chunk_cut_plans(
        chunk_plans=chunk_plans,
        sync_map=sync_map,
        visual_packet_path=str(output_root / "visual_packet.json"),
        source_sync_map_path=source_sync_map_path,
        source_analysis_path=source_analysis_path,
        source_transcript_path=source_transcript_path,
        options=pipeline_options,
    )
    if callable(on_progress):
        on_progress(total_chunks, total_chunks, "Chunks zusammengefuehrt")
    return {
        "visual_packet": visual_packet,
        "chunk_plans": chunk_plans,
        "combined_cut_plan": combined_cut_plan,
    }
