from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import base64
import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import __version__
from .cut_plan import (
    EPSILON,
    _analysis_signal_summary,
    _analysis_windows_by_asset,
    _candidate_score,
    _coverage_window,
    _extract_master_speech_segments,
    _merge_video_segments,
    _require_duration,
    _speech_overlap_summary,
    _transcript_excerpt,
)
from .draft_prompt import THEATER_VAZ_DECISION_RULES, THEATER_VAZ_SYSTEM_PROMPT
from .transcript import transcript_source_metadata

DEFAULT_AI_DRAFT_MODEL = "gpt-4.1-mini"


@dataclass(slots=True)
class AIDraftOptions:
    model: str = DEFAULT_AI_DRAFT_MODEL
    max_output_tokens: int = 6000
    temperature: float = 0.2
    user_notes: str | None = None
    master_start_seconds: float | None = None
    master_end_seconds: float | None = None


class AIDraftSegment(BaseModel):
    start_seconds: float = Field(..., description="Master-timeline start time in seconds.")
    end_seconds: float = Field(..., description="Master-timeline end time in seconds.")
    asset_id: str = Field(..., description="Chosen camera asset id.")
    reason: str = Field(..., description="Short rationale for the camera choice.")


class AIDraftResult(BaseModel):
    summary: str = Field(..., description="Short summary of the local edit strategy.")
    segments: list[AIDraftSegment] = Field(..., description="Ordered edit segments on the master timeline.")


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


def _data_url_for_image(path: str) -> str:
    image_bytes = Path(path).read_bytes()
    encoded = base64.b64encode(image_bytes).decode("ascii")
    suffix = Path(path).suffix.lower()
    mime_type = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime_type};base64,{encoded}"


def _span_from_visual_packet(
    visual_packet: dict[str, Any],
    options: AIDraftOptions,
) -> tuple[float, float]:
    if options.master_start_seconds is not None and options.master_end_seconds is not None:
        if options.master_end_seconds <= options.master_start_seconds:
            raise ValueError("AI draft master span end must be greater than start.")
        return float(options.master_start_seconds), float(options.master_end_seconds)

    windows = [window for window in visual_packet.get("windows", []) if isinstance(window, dict)]
    if not windows:
        raise ValueError("visual_packet does not contain any windows.")

    start_seconds = min(float(window["master_start_seconds"]) for window in windows)
    end_seconds = max(float(window["master_end_seconds"]) for window in windows)
    if end_seconds - start_seconds <= EPSILON:
        raise ValueError("visual_packet does not expose a usable planning span.")
    return start_seconds, end_seconds


def _coverage_by_asset(sync_map: dict[str, Any], master_duration_seconds: float) -> dict[str, Any]:
    coverages = {}
    for entry in sync_map.get("entries", []):
        if not isinstance(entry, dict) or entry.get("status") != "synced":
            continue
        coverage = _coverage_window(entry, master_duration_seconds)
        if coverage is not None:
            coverages[coverage.asset_id] = coverage
    if not coverages:
        raise ValueError("sync_map does not contain any synced coverage.")
    return coverages


def _max_coverage_end_seconds(coverages_by_asset: dict[str, Any]) -> float:
    return max(float(coverage.overlap_end_seconds) for coverage in coverages_by_asset.values())


def _fallback_asset_id(
    visual_packet: dict[str, Any],
    coverages_by_asset: dict[str, Any],
) -> str:
    role_order = {"totale": 0, "halbtotale": 1, "close": 2, "unknown": 3}
    packet_assets: dict[str, str] = {}
    for window in visual_packet.get("windows", []):
        if not isinstance(window, dict):
            continue
        for image in window.get("images", []):
            if not isinstance(image, dict):
                continue
            asset_id = image.get("asset_id")
            role = image.get("role")
            if isinstance(asset_id, str) and isinstance(role, str):
                packet_assets.setdefault(asset_id, role)

    candidates = sorted(
        coverages_by_asset.values(),
        key=lambda coverage: (
            role_order.get(packet_assets.get(coverage.asset_id, "unknown"), 3),
            -float(_candidate_score(coverage)[0]),
            coverage.asset_id,
        ),
    )
    return candidates[0].asset_id


def _compact_visual_summary(
    visual_packet: dict[str, Any],
    span_start_seconds: float,
    span_end_seconds: float,
) -> list[dict[str, Any]]:
    window_summaries: list[dict[str, Any]] = []
    for window in visual_packet.get("windows", []):
        if not isinstance(window, dict):
            continue
        center_seconds = float(window["master_center_seconds"])
        if center_seconds < span_start_seconds - EPSILON or center_seconds > span_end_seconds + EPSILON:
            continue
        images = []
        for image in window.get("images", []):
            if not isinstance(image, dict):
                continue
            signals = image.get("signals", {})
            images.append(
                {
                    "asset_id": image["asset_id"],
                    "role": image.get("role"),
                    "confidence": image.get("confidence"),
                    "usable_score": None if not isinstance(signals, dict) else signals.get("usable_score"),
                    "sharpness_score": None if not isinstance(signals, dict) else signals.get("sharpness_score"),
                    "stability_score": None if not isinstance(signals, dict) else signals.get("stability_score"),
                }
            )
        window_summaries.append(
            {
                "id": window["id"],
                "kind": window["kind"],
                "master_center_seconds": center_seconds,
                "transcript_excerpt": None
                if not isinstance(window.get("transcript"), dict)
                else window["transcript"].get("text"),
                "images": images,
            }
        )
    return window_summaries


def _build_input_content(
    *,
    sync_map: dict[str, Any],
    visual_packet: dict[str, Any],
    span_start_seconds: float,
    span_end_seconds: float,
    options: AIDraftOptions,
) -> list[dict[str, Any]]:
    planning_brief = {
        "task": "Create a theater multicam cut plan draft for the provided master-time span.",
        "master_span_start_seconds": span_start_seconds,
        "master_span_end_seconds": span_end_seconds,
        "duration_seconds": span_end_seconds - span_start_seconds,
        "decision_rules": THEATER_VAZ_DECISION_RULES,
        "available_assets": sorted(
            {
                image["asset_id"]
                for window in visual_packet.get("windows", [])
                if isinstance(window, dict)
                for image in window.get("images", [])
                if isinstance(image, dict)
            }
        ),
        "visual_windows": _compact_visual_summary(
            visual_packet,
            span_start_seconds,
            span_end_seconds,
        ),
        "user_notes": options.user_notes,
        "output_requirements": {
            "must_cover_full_span": True,
            "segments_must_be_time_ordered": True,
            "use_only_known_asset_ids": True,
            "prefer_longer_readable_shots_over_hyperactive_cutting": True,
        },
    }

    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": (
                "Plan a theater VAZ multicam draft from the following structured brief. "
                "Use the images as supporting evidence, not as isolated single-frame judgments.\n\n"
                + json.dumps(planning_brief, ensure_ascii=True, indent=2)
            ),
        }
    ]

    for window in visual_packet.get("windows", []):
        if not isinstance(window, dict):
            continue
        center_seconds = float(window["master_center_seconds"])
        if center_seconds < span_start_seconds - EPSILON or center_seconds > span_end_seconds + EPSILON:
            continue

        transcript_payload = window.get("transcript", {})
        transcript_text = None
        if isinstance(transcript_payload, dict):
            transcript_text = transcript_payload.get("text")
        content.append(
            {
                "type": "input_text",
                "text": (
                    f"Window {window['id']} ({window['kind']}) at master {center_seconds:.3f}s. "
                    f"Transcript excerpt: {transcript_text or '[none]'}"
                ),
            }
        )
        for image in window.get("images", []):
            if not isinstance(image, dict):
                continue
            signals = image.get("signals", {})
            content.append(
                {
                    "type": "input_text",
                    "text": (
                        f"Camera image follows: asset_id={image['asset_id']}, role={image.get('role')}, "
                        f"confidence={image.get('confidence')}, "
                        f"usable_score={None if not isinstance(signals, dict) else signals.get('usable_score')}, "
                        f"sharpness_score={None if not isinstance(signals, dict) else signals.get('sharpness_score')}, "
                        f"stability_score={None if not isinstance(signals, dict) else signals.get('stability_score')}."
                    ),
                }
            )
            content.append(
                {
                    "type": "input_image",
                    "image_url": _data_url_for_image(image["image_path"]),
                }
            )

    return content


def _select_asset_for_interval(
    interval_start_seconds: float,
    interval_end_seconds: float,
    proposals: list[AIDraftSegment],
    coverages_by_asset: dict[str, Any],
    fallback_asset_id: str,
) -> tuple[str, str]:
    candidates: list[tuple[float, int, AIDraftSegment]] = []
    for index, proposal in enumerate(proposals):
        proposal_start_seconds = max(interval_start_seconds, float(proposal.start_seconds))
        proposal_end_seconds = min(interval_end_seconds, float(proposal.end_seconds))
        overlap_seconds = proposal_end_seconds - proposal_start_seconds
        if overlap_seconds <= EPSILON:
            continue

        coverage = coverages_by_asset.get(proposal.asset_id)
        if coverage is None:
            continue
        if coverage.overlap_start_seconds > interval_start_seconds + EPSILON:
            continue
        if coverage.overlap_end_seconds < interval_end_seconds - EPSILON:
            continue

        candidates.append((overlap_seconds, -index, proposal))

    if not candidates:
        fallback_coverage = coverages_by_asset.get(fallback_asset_id)
        if (
            fallback_coverage is not None
            and fallback_coverage.overlap_start_seconds <= interval_start_seconds + EPSILON
            and fallback_coverage.overlap_end_seconds >= interval_end_seconds - EPSILON
        ):
            return fallback_asset_id, "Fallback camera selected because AI proposals did not cover this interval."

        covering_fallbacks = [
            coverage
            for coverage in coverages_by_asset.values()
            if coverage.overlap_start_seconds <= interval_start_seconds + EPSILON
            and coverage.overlap_end_seconds >= interval_end_seconds - EPSILON
        ]
        if covering_fallbacks:
            best_covering = max(covering_fallbacks, key=_candidate_score)
            return (
                best_covering.asset_id,
                "Best covering synced camera selected because the default fallback did not cover this interval.",
            )

        raise ValueError(
            "No synced camera covers interval "
            f"{interval_start_seconds:.3f}s..{interval_end_seconds:.3f}s."
        )

    best = max(candidates, key=lambda item: (item[0], item[1]))
    return best[2].asset_id, best[2].reason


def _compile_ai_segments(
    *,
    ai_result: AIDraftResult,
    span_start_seconds: float,
    span_end_seconds: float,
    coverages_by_asset: dict[str, Any],
    fallback_asset_id: str,
    analysis_map: dict[str, Any] | None,
    transcript_artifact: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    valid_proposals = [
        proposal
        for proposal in ai_result.segments
        if proposal.asset_id in coverages_by_asset
        and float(proposal.end_seconds) - float(proposal.start_seconds) > EPSILON
    ]
    boundaries = {round(span_start_seconds, 6), round(span_end_seconds, 6)}
    for coverage in coverages_by_asset.values():
        if coverage.overlap_end_seconds <= span_start_seconds + EPSILON:
            continue
        if coverage.overlap_start_seconds >= span_end_seconds - EPSILON:
            continue
        boundaries.add(round(max(span_start_seconds, float(coverage.overlap_start_seconds)), 6))
        boundaries.add(round(min(span_end_seconds, float(coverage.overlap_end_seconds)), 6))
    for proposal in valid_proposals:
        proposal_start_seconds = max(span_start_seconds, float(proposal.start_seconds))
        proposal_end_seconds = min(span_end_seconds, float(proposal.end_seconds))
        if proposal_end_seconds - proposal_start_seconds <= EPSILON:
            continue
        boundaries.add(round(proposal_start_seconds, 6))
        boundaries.add(round(proposal_end_seconds, 6))
    ordered_boundaries = sorted(boundaries)

    windows_by_asset = _analysis_windows_by_asset(analysis_map)
    speech_segments = _extract_master_speech_segments(analysis_map, transcript_artifact)
    segments: list[dict[str, Any]] = []
    output_cursor = 0.0
    for interval_start_seconds, interval_end_seconds in zip(ordered_boundaries[:-1], ordered_boundaries[1:], strict=False):
        if interval_end_seconds - interval_start_seconds <= EPSILON:
            continue
        asset_id, reason = _select_asset_for_interval(
            interval_start_seconds,
            interval_end_seconds,
            valid_proposals,
            coverages_by_asset,
            fallback_asset_id,
        )
        coverage = coverages_by_asset[asset_id]
        source_start_seconds = coverage.speed * interval_start_seconds + coverage.offset_seconds
        source_end_seconds = coverage.speed * interval_end_seconds + coverage.offset_seconds
        signal_summary = _analysis_signal_summary(
            asset_id,
            interval_start_seconds,
            interval_end_seconds,
            windows_by_asset,
        )
        speech_summary = _speech_overlap_summary(
            interval_start_seconds,
            interval_end_seconds,
            speech_segments,
        )
        transcript_overlap_count, transcript_excerpt = _transcript_excerpt(
            interval_start_seconds,
            interval_end_seconds,
            transcript_artifact,
        )
        duration_seconds = interval_end_seconds - interval_start_seconds
        segments.append(
            {
                "id": "video_0000",
                "type": "camera",
                "strategy": "ai_draft",
                "asset_id": asset_id,
                "asset_path": coverage.asset_path,
                "confidence": coverage.confidence,
                "master_start_seconds": interval_start_seconds,
                "master_end_seconds": interval_end_seconds,
                "output_start_seconds": output_cursor,
                "output_end_seconds": output_cursor + duration_seconds,
                "duration_seconds": duration_seconds,
                "source_start_seconds": max(0.0, source_start_seconds),
                "source_end_seconds": min(coverage.duration_seconds, source_end_seconds),
                "speed": coverage.speed,
                "reason": reason,
                "signals": {
                    **speech_summary,
                    "transcript_overlap_count": transcript_overlap_count,
                    "transcript_excerpt": transcript_excerpt,
                    "usable_score": signal_summary["usable_score"],
                    "sharpness_score": signal_summary["sharpness_score"],
                    "stability_score": signal_summary["stability_score"],
                    "has_analysis": signal_summary["has_analysis"],
                },
            }
        )
        output_cursor += duration_seconds

    merged_segments = _merge_video_segments(segments)
    if not merged_segments:
        raise ValueError("AI draft compilation produced no usable video segments.")
    return merged_segments


def build_ai_draft_cut_plan(
    sync_map: dict[str, Any],
    *,
    source_sync_map_path: str | None = None,
    visual_packet: dict[str, Any],
    source_visual_packet_path: str | None = None,
    analysis_map: dict[str, Any] | None = None,
    source_analysis_path: str | None = None,
    transcript_artifact: dict[str, Any] | None = None,
    source_transcript_path: str | None = None,
    options: AIDraftOptions | None = None,
) -> dict[str, Any]:
    if sync_map.get("schema_version") != "vazer.sync_map.v1":
        raise ValueError("Unsupported sync_map schema version.")
    if visual_packet.get("schema_version") != "vazer.visual_packet.v1":
        raise ValueError("Unsupported visual_packet schema version.")

    ai_options = options or AIDraftOptions()
    client = _build_client()
    master_payload = sync_map.get("master")
    if not isinstance(master_payload, dict):
        raise ValueError("sync_map master payload is missing.")
    master_path = master_payload.get("path")
    if not isinstance(master_path, str) or not master_path:
        raise ValueError("sync_map master path is missing.")

    master_duration_seconds = _require_duration(master_payload, "Master audio", master_path)
    coverages_by_asset = _coverage_by_asset(sync_map, master_duration_seconds)
    span_start_seconds, span_end_seconds = _span_from_visual_packet(visual_packet, ai_options)
    max_coverage_end_seconds = _max_coverage_end_seconds(coverages_by_asset)
    span_end_seconds = min(span_end_seconds, max_coverage_end_seconds)
    if span_end_seconds - span_start_seconds <= EPSILON:
        raise ValueError(
            "No synced camera covers requested AI planning span "
            f"{span_start_seconds:.3f}s..{ai_options.master_end_seconds if ai_options.master_end_seconds is not None else span_end_seconds:.3f}s."
        )
    fallback_asset_id = _fallback_asset_id(visual_packet, coverages_by_asset)
    input_content = _build_input_content(
        sync_map=sync_map,
        visual_packet=visual_packet,
        span_start_seconds=span_start_seconds,
        span_end_seconds=span_end_seconds,
        options=ai_options,
    )

    response = client.responses.parse(
        model=ai_options.model,
        instructions=THEATER_VAZ_SYSTEM_PROMPT,
        input=[{"role": "user", "content": input_content}],
        text_format=AIDraftResult,
        max_output_tokens=ai_options.max_output_tokens,
        temperature=ai_options.temperature,
    )
    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise ValueError("AI draft response did not contain a parsed structured output.")

    video_segments = _compile_ai_segments(
        ai_result=parsed,
        span_start_seconds=span_start_seconds,
        span_end_seconds=span_end_seconds,
        coverages_by_asset=coverages_by_asset,
        fallback_asset_id=fallback_asset_id,
        analysis_map=analysis_map,
        transcript_artifact=transcript_artifact,
    )
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

    best_render_window = max(coverages_by_asset.values(), key=_candidate_score)
    primary_video = best_render_window.primary_video or {}
    render_defaults = {
        "width": primary_video.get("width") or 1920,
        "height": primary_video.get("height") or 1080,
        "fps": primary_video.get("frame_rate") or 25.0,
        "pixel_format": "yuv420p",
        "video_codec": "libx264",
        "audio_codec": "aac",
    }
    selected_assets = sorted({segment["asset_id"] for segment in video_segments})
    dropped_assets = sorted(set(coverages_by_asset) - set(selected_assets))
    usage = getattr(response, "usage", None)

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
        if analysis_map is None
        else {
            "schema_version": analysis_map["schema_version"],
            "path": source_analysis_path,
        },
        "source_transcript": None
        if transcript_artifact is None
        else transcript_source_metadata(transcript_artifact, path=source_transcript_path),
        "source_visual_packet": {
            "schema_version": visual_packet["schema_version"],
            "path": source_visual_packet_path,
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
            "kept_intervals": [
                {
                    "master_start_seconds": segment["master_start_seconds"],
                    "master_end_seconds": segment["master_end_seconds"],
                    "asset_id": segment["asset_id"],
                }
                for segment in video_segments
            ],
        },
        "video_segments": video_segments,
        "audio_segments": audio_segments,
        "summary": {
            "planning_stage": "draft",
            "selected_assets": selected_assets,
            "dropped_assets": dropped_assets,
            "synced_assets": len(coverages_by_asset),
            "video_segments": len(video_segments),
            "output_duration_seconds": video_segments[-1]["output_end_seconds"],
            "signal_aware": analysis_map is not None or transcript_artifact is not None,
            "word_timestamps_available": bool(transcript_artifact and transcript_artifact.get("words")),
        },
        "ai_draft": {
            "provider": "openai",
            "model": ai_options.model,
            "response_id": getattr(response, "id", None),
            "summary": parsed.summary,
            "raw_segments": [segment.model_dump() for segment in parsed.segments],
            "usage": None if usage is None else getattr(usage, "model_dump", lambda: None)(),
            "fallback_asset_id": fallback_asset_id,
            "notes": ai_options.user_notes,
        },
    }
