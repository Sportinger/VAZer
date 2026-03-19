from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from . import __version__
from .analysis import AnalysisOptions, analyze_local_dense_window
from .cut_plan import _merge_video_segments
from .fftools import probe_media
from .transcript import transcript_source_metadata

EPSILON = 1e-6
SENTENCE_ENDINGS = (".", "!", "?", ";", ":")


@dataclass(slots=True)
class CutValidationOptions:
    transcript_search_window_seconds: float = 0.75
    transcript_pause_boundary_seconds: float = 0.2
    cut_context_seconds: float = 1.0
    local_probe_delta_seconds: float = 0.12
    local_probe_width: int = 256
    local_dense_context_seconds: float = 2.0
    local_dense_fps: float = 8.0
    local_dense_width: int = 640
    local_dense_decoder_preference: str = "auto"
    local_dense_prefer_gpu: bool = True
    analysis_soft_threshold: float = 0.35
    analysis_fail_threshold: float = 0.2
    alternate_margin: float = 0.12
    repair_min_segment_seconds: float = 0.5


@dataclass(slots=True)
class AssetCoverage:
    asset_id: str
    path: str
    confidence: str
    speed: float
    offset_seconds: float
    duration_seconds: float
    overlap_start_seconds: float
    overlap_end_seconds: float


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_cut_validation_report(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_cut_validation_report(report: dict[str, Any], output_path: str) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return destination


def _confidence_rank(value: str) -> int:
    return {
        "high": 3,
        "medium": 2,
        "low": 1,
    }.get(value, 0)


def _interval_overlap(
    start_seconds: float,
    end_seconds: float,
    interval_start_seconds: float,
    interval_end_seconds: float,
) -> float:
    return max(0.0, min(end_seconds, interval_end_seconds) - max(start_seconds, interval_start_seconds))


def _require_duration(path: str, existing_duration_seconds: float | None = None) -> float:
    if isinstance(existing_duration_seconds, (int, float)) and existing_duration_seconds > 0:
        return float(existing_duration_seconds)

    media_info = probe_media(path)
    if media_info.duration_seconds is None or media_info.duration_seconds <= 0:
        raise ValueError(f"Unable to determine duration for {path}.")
    return float(media_info.duration_seconds)


def _build_coverages_from_sync_map(sync_map: dict[str, Any]) -> dict[str, AssetCoverage]:
    master_payload = sync_map.get("master")
    if not isinstance(master_payload, dict):
        raise ValueError("sync_map master payload is missing.")

    master_path = master_payload.get("path")
    if not isinstance(master_path, str) or not master_path:
        raise ValueError("sync_map master path is missing.")

    master_duration_seconds = _require_duration(master_path, master_payload.get("duration_seconds"))
    coverages: dict[str, AssetCoverage] = {}

    for entry in sync_map.get("entries", []):
        if not isinstance(entry, dict) or entry.get("status") != "synced":
            continue

        asset_id = entry.get("asset_id")
        asset_path = entry.get("path")
        mapping = entry.get("mapping")
        if not isinstance(asset_id, str) or not isinstance(asset_path, str) or not isinstance(mapping, dict):
            continue

        speed = float(mapping["speed"])
        offset_seconds = float(mapping["offset_seconds"])
        if speed <= 0:
            continue

        media_payload = entry.get("media")
        duration_seconds = None
        if isinstance(media_payload, dict):
            duration_seconds = media_payload.get("duration_seconds")
        duration_seconds = _require_duration(asset_path, duration_seconds)

        overlap_start_seconds = max(0.0, -offset_seconds / speed)
        overlap_end_seconds = min(master_duration_seconds, (duration_seconds - offset_seconds) / speed)
        if overlap_end_seconds - overlap_start_seconds <= EPSILON:
            continue

        coverages[asset_id] = AssetCoverage(
            asset_id=asset_id,
            path=asset_path,
            confidence=str(entry.get("summary", {}).get("confidence") or "unknown"),
            speed=speed,
            offset_seconds=offset_seconds,
            duration_seconds=duration_seconds,
            overlap_start_seconds=overlap_start_seconds,
            overlap_end_seconds=overlap_end_seconds,
        )

    return coverages


def _build_coverages_from_cut_plan(cut_plan: dict[str, Any]) -> dict[str, AssetCoverage]:
    coverages: dict[str, AssetCoverage] = {}
    for segment in cut_plan.get("video_segments", []):
        asset_id = segment["asset_id"]
        speed = float(segment["speed"])
        offset_seconds = float(segment["source_start_seconds"]) - speed * float(segment["master_start_seconds"])
        current = coverages.get(asset_id)
        overlap_start_seconds = float(segment["master_start_seconds"])
        overlap_end_seconds = float(segment["master_end_seconds"])
        duration_seconds = float(segment["source_end_seconds"]) - offset_seconds
        if current is None:
            coverages[asset_id] = AssetCoverage(
                asset_id=asset_id,
                path=segment["asset_path"],
                confidence=str(segment.get("confidence") or "unknown"),
                speed=speed,
                offset_seconds=offset_seconds,
                duration_seconds=max(overlap_end_seconds, duration_seconds),
                overlap_start_seconds=overlap_start_seconds,
                overlap_end_seconds=overlap_end_seconds,
            )
            continue

        current.overlap_start_seconds = min(current.overlap_start_seconds, overlap_start_seconds)
        current.overlap_end_seconds = max(current.overlap_end_seconds, overlap_end_seconds)

    return coverages


def _analysis_windows_by_asset(analysis_map: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    if analysis_map is None:
        return {}

    windows_by_asset: dict[str, list[dict[str, Any]]] = {}
    for entry in analysis_map.get("entries", []):
        if not isinstance(entry, dict) or entry.get("status") != "analyzed":
            continue
        asset_id = entry.get("asset_id")
        if not isinstance(asset_id, str):
            continue
        windows = [
            window
            for window in entry.get("windows", [])
            if isinstance(window, dict)
        ]
        windows.sort(key=lambda item: (float(item["master_start_seconds"]), float(item["master_end_seconds"])))
        windows_by_asset[asset_id] = windows
    return windows_by_asset


def _analysis_signal_summary(
    asset_id: str,
    interval_start_seconds: float,
    interval_end_seconds: float,
    windows_by_asset: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    total_weight = 0.0
    usable_sum = 0.0
    sharpness_sum = 0.0
    stability_sum = 0.0

    for window in windows_by_asset.get(asset_id, []):
        overlap_seconds = _interval_overlap(
            interval_start_seconds,
            interval_end_seconds,
            float(window["master_start_seconds"]),
            float(window["master_end_seconds"]),
        )
        if overlap_seconds <= EPSILON:
            continue

        total_weight += overlap_seconds
        usable_sum += overlap_seconds * float(window["usable_score"])
        sharpness_sum += overlap_seconds * float(window["sharpness_score"])
        stability_sum += overlap_seconds * float(window["stability_score"])

    if total_weight <= EPSILON:
        return {
            "has_analysis": False,
            "usable_score": 0.5,
            "sharpness_score": 0.5,
            "stability_score": 0.5,
        }

    return {
        "has_analysis": True,
        "usable_score": float(usable_sum / total_weight),
        "sharpness_score": float(sharpness_sum / total_weight),
        "stability_score": float(stability_sum / total_weight),
    }


def _speech_segments(
    analysis_map: dict[str, Any] | None,
    transcript_artifact: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    if analysis_map is not None:
        for segment in analysis_map.get("master_audio_activity", {}).get("segments", []):
            if not isinstance(segment, dict):
                continue
            segments.append(
                {
                    "start_seconds": float(segment["start_seconds"]),
                    "end_seconds": float(segment["end_seconds"]),
                    "source": "analysis",
                }
            )
    if transcript_artifact is not None:
        for segment in transcript_artifact.get("segments", []):
            if not isinstance(segment, dict):
                continue
            segments.append(
                {
                    "start_seconds": float(segment["start_seconds"]),
                    "end_seconds": float(segment["end_seconds"]),
                    "source": "transcript",
                }
            )
    segments.sort(key=lambda item: (item["start_seconds"], item["end_seconds"]))
    return segments


def _speech_overlap_summary(
    interval_start_seconds: float,
    interval_end_seconds: float,
    speech_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    total_overlap_seconds = 0.0
    sources: set[str] = set()

    for segment in speech_segments:
        overlap_seconds = _interval_overlap(
            interval_start_seconds,
            interval_end_seconds,
            segment["start_seconds"],
            segment["end_seconds"],
        )
        if overlap_seconds <= EPSILON:
            continue
        total_overlap_seconds += overlap_seconds
        sources.add(str(segment["source"]))

    duration_seconds = interval_end_seconds - interval_start_seconds
    overlap_ratio = 0.0 if duration_seconds <= EPSILON else total_overlap_seconds / duration_seconds
    return {
        "speech_like": overlap_ratio >= 0.25,
        "speech_overlap_ratio": float(overlap_ratio),
        "speech_sources": sorted(sources),
    }


def _transcript_excerpt(
    interval_start_seconds: float,
    interval_end_seconds: float,
    transcript_artifact: dict[str, Any] | None,
) -> tuple[int, str | None]:
    if transcript_artifact is None:
        return 0, None

    overlapping_segments = [
        segment
        for segment in transcript_artifact.get("segments", [])
        if isinstance(segment, dict)
        and _interval_overlap(
            interval_start_seconds,
            interval_end_seconds,
            float(segment["start_seconds"]),
            float(segment["end_seconds"]),
        )
        > EPSILON
    ]
    if not overlapping_segments:
        return 0, None

    combined_text = " ".join(
        str(segment.get("text") or "").strip()
        for segment in overlapping_segments[:2]
        if str(segment.get("text") or "").strip()
    ).strip()
    if len(combined_text) > 120:
        combined_text = combined_text[:117].rstrip() + "..."

    return len(overlapping_segments), combined_text or None


def _transcript_words(transcript_artifact: dict[str, Any] | None) -> list[dict[str, Any]]:
    if transcript_artifact is None:
        return []

    words = [
        word
        for word in transcript_artifact.get("words", [])
        if isinstance(word, dict)
    ]
    words.sort(key=lambda item: (float(item["start_seconds"]), float(item["end_seconds"])))
    return words


def _preferred_transcript_boundary(
    cut_seconds: float,
    transcript_artifact: dict[str, Any] | None,
    *,
    search_window_seconds: float,
    pause_boundary_seconds: float,
) -> dict[str, Any]:
    words = _transcript_words(transcript_artifact)
    nearby_text = None
    if words:
        nearby_words = [
            str(word.get("text") or "").strip()
            for word in words
            if float(word["start_seconds"]) <= cut_seconds + 2.0
            and float(word["end_seconds"]) >= cut_seconds - 2.0
        ]
        nearby_text = " ".join(piece for piece in nearby_words if piece).strip() or None

    candidates: list[dict[str, Any]] = []
    speech_near_cut = False
    for index, word in enumerate(words):
        start_seconds = float(word["start_seconds"])
        end_seconds = float(word["end_seconds"])
        text = str(word.get("text") or "").strip()
        next_word = words[index + 1] if index + 1 < len(words) else None
        previous_word = words[index - 1] if index > 0 else None
        pause_before_seconds = (
            start_seconds - float(previous_word["end_seconds"])
            if previous_word is not None
            else 0.0
        )
        pause_after_seconds = (
            float(next_word["start_seconds"]) - end_seconds
            if next_word is not None
            else 0.0
        )
        punctuation = text.endswith(SENTENCE_ENDINGS)

        if start_seconds <= cut_seconds + 0.35 and end_seconds >= cut_seconds - 0.35:
            speech_near_cut = True

        for boundary_seconds, boundary_kind, pause_seconds in (
            (start_seconds, "word_start", pause_before_seconds),
            (end_seconds, "word_end", pause_after_seconds),
        ):
            delta_seconds = boundary_seconds - cut_seconds
            if abs(delta_seconds) > search_window_seconds + EPSILON:
                continue

            score = 1.0 - (abs(delta_seconds) / max(search_window_seconds, EPSILON))
            score += min(1.0, max(0.0, pause_seconds) / max(pause_boundary_seconds, EPSILON)) * 0.35
            if punctuation and boundary_kind == "word_end":
                score += 0.2

            candidates.append(
                {
                    "target_cut_seconds": boundary_seconds,
                    "delta_seconds": delta_seconds,
                    "kind": boundary_kind,
                    "pause_seconds": max(0.0, pause_seconds),
                    "word_text": text,
                    "punctuation": punctuation and boundary_kind == "word_end",
                    "score": score,
                }
            )

    if not candidates:
        return {
            "has_words": bool(words),
            "speech_near_cut": speech_near_cut,
            "preferred_boundary": None,
            "nearby_text": nearby_text,
        }

    preferred_boundary = max(
        candidates,
        key=lambda item: (
            float(item["score"]),
            -abs(float(item["delta_seconds"])),
            float(item["pause_seconds"]),
        ),
    )
    return {
        "has_words": bool(words),
        "speech_near_cut": speech_near_cut,
        "preferred_boundary": preferred_boundary,
        "candidate_count": len(candidates),
        "nearby_text": nearby_text,
    }


class _FrameProbePool:
    def __init__(
        self,
        width: int,
        delta_seconds: float,
        *,
        dense_context_seconds: float,
        dense_fps: float,
        dense_width: int,
        decoder_preference: str,
        prefer_gpu: bool,
    ) -> None:
        self.width = width
        self.delta_seconds = delta_seconds
        self.dense_context_seconds = dense_context_seconds
        self.dense_fps = dense_fps
        self.dense_width = dense_width
        self.decoder_preference = decoder_preference
        self.prefer_gpu = prefer_gpu

    def close(self) -> None:
        return None

    def probe(self, path: str, timestamp_seconds: float) -> dict[str, Any]:
        try:
            return analyze_local_dense_window(
                path,
                center_seconds=timestamp_seconds,
                context_seconds=max(self.dense_context_seconds, self.delta_seconds * 4.0),
                width=max(self.width, self.dense_width),
                sample_fps=self.dense_fps,
                decoder_preference=self.decoder_preference,
                prefer_gpu=self.prefer_gpu,
                options=AnalysisOptions(
                    analysis_width=max(self.width, self.dense_width),
                ),
            )
        except Exception as error:
            return {
                "success": False,
                "timestamp_seconds": float(timestamp_seconds),
                "error": str(error),
            }


def _local_probe_timestamp(segment: dict[str, Any], side: str, delta_seconds: float) -> float:
    if side == "outgoing":
        return max(
            float(segment["source_start_seconds"]),
            float(segment["source_end_seconds"]) - max(0.01, delta_seconds),
        )
    return min(
        float(segment["source_end_seconds"]),
        float(segment["source_start_seconds"]) + max(0.01, delta_seconds),
    )


def _candidate_quality_score(signal_summary: dict[str, Any], confidence: str) -> float:
    return (
        0.65 * float(signal_summary["usable_score"])
        + 0.2 * float(signal_summary["stability_score"])
        + 0.1 * float(signal_summary["sharpness_score"])
        + 0.05 * (_confidence_rank(confidence) / 3.0)
    )


def _active_candidate_assets(
    incoming_segment: dict[str, Any],
    cut_seconds: float,
    *,
    coverages_by_asset: dict[str, AssetCoverage],
    windows_by_asset: dict[str, list[dict[str, Any]]],
    context_seconds: float,
) -> list[dict[str, Any]]:
    interval_end_seconds = min(float(incoming_segment["master_end_seconds"]), cut_seconds + context_seconds)
    if interval_end_seconds - cut_seconds <= EPSILON:
        interval_end_seconds = float(incoming_segment["master_end_seconds"])

    candidates: list[dict[str, Any]] = []
    for coverage in coverages_by_asset.values():
        if coverage.overlap_start_seconds > cut_seconds + EPSILON:
            continue
        if coverage.overlap_end_seconds < interval_end_seconds - EPSILON:
            continue

        signal_summary = _analysis_signal_summary(
            coverage.asset_id,
            cut_seconds,
            interval_end_seconds,
            windows_by_asset,
        )
        candidates.append(
            {
                "asset_id": coverage.asset_id,
                "asset_path": coverage.path,
                "confidence": coverage.confidence,
                "covers_entire_incoming_segment": coverage.overlap_end_seconds >= float(incoming_segment["master_end_seconds"]) - EPSILON,
                "signal_summary": signal_summary,
                "quality_score": _candidate_quality_score(signal_summary, coverage.confidence),
            }
        )

    candidates.sort(
        key=lambda item: (
            float(item["quality_score"]),
            float(_confidence_rank(str(item["confidence"]))),
            item["asset_id"],
        ),
        reverse=True,
    )
    return candidates


def build_cut_validation_report(
    cut_plan: dict[str, Any],
    *,
    sync_map: dict[str, Any] | None = None,
    source_cut_plan_path: str | None = None,
    source_sync_map_path: str | None = None,
    analysis_map: dict[str, Any] | None = None,
    source_analysis_path: str | None = None,
    transcript_artifact: dict[str, Any] | None = None,
    source_transcript_path: str | None = None,
    options: CutValidationOptions | None = None,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    if cut_plan.get("schema_version") != "vazer.cut_plan.v1":
        raise ValueError("Unsupported cut_plan schema version.")

    validation_options = options or CutValidationOptions()
    video_segments = [
        segment
        for segment in cut_plan.get("video_segments", [])
        if isinstance(segment, dict)
    ]
    if len(video_segments) < 2:
        raise ValueError("cut_plan does not contain enough video segments to validate cuts.")

    coverages_by_asset = (
        _build_coverages_from_sync_map(sync_map)
        if sync_map is not None
        else _build_coverages_from_cut_plan(cut_plan)
    )
    windows_by_asset = _analysis_windows_by_asset(analysis_map)

    cuts: list[dict[str, Any]] = []
    frame_pool = _FrameProbePool(
        width=validation_options.local_probe_width,
        delta_seconds=validation_options.local_probe_delta_seconds,
        dense_context_seconds=validation_options.local_dense_context_seconds,
        dense_fps=validation_options.local_dense_fps,
        dense_width=validation_options.local_dense_width,
        decoder_preference=validation_options.local_dense_decoder_preference,
        prefer_gpu=validation_options.local_dense_prefer_gpu,
    )
    total_cut_pairs = max(1, len(video_segments) - 1)
    try:
        for cut_index, (outgoing_segment, incoming_segment) in enumerate(
            zip(video_segments[:-1], video_segments[1:], strict=False),
            start=1,
        ):
            if callable(on_progress):
                on_progress(cut_index - 1, total_cut_pairs, f"Local pass {cut_index}/{total_cut_pairs}")
            if outgoing_segment["asset_id"] == incoming_segment["asset_id"]:
                continue

            current_cut_seconds = float(outgoing_segment["master_end_seconds"])
            outgoing_interval_start = max(
                float(outgoing_segment["master_start_seconds"]),
                current_cut_seconds - validation_options.cut_context_seconds,
            )
            incoming_interval_end = min(
                float(incoming_segment["master_end_seconds"]),
                current_cut_seconds + validation_options.cut_context_seconds,
            )
            if incoming_interval_end - current_cut_seconds <= EPSILON:
                incoming_interval_end = float(incoming_segment["master_end_seconds"])

            outgoing_signal = _analysis_signal_summary(
                outgoing_segment["asset_id"],
                outgoing_interval_start,
                current_cut_seconds,
                windows_by_asset,
            )
            incoming_signal = _analysis_signal_summary(
                incoming_segment["asset_id"],
                current_cut_seconds,
                incoming_interval_end,
                windows_by_asset,
            )
            transcript_boundary = _preferred_transcript_boundary(
                current_cut_seconds,
                transcript_artifact,
                search_window_seconds=validation_options.transcript_search_window_seconds,
                pause_boundary_seconds=validation_options.transcript_pause_boundary_seconds,
            )
            alternatives = _active_candidate_assets(
                incoming_segment,
                current_cut_seconds,
                coverages_by_asset=coverages_by_asset,
                windows_by_asset=windows_by_asset,
                context_seconds=validation_options.cut_context_seconds,
            )
            outgoing_probe = frame_pool.probe(
                outgoing_segment["asset_path"],
                _local_probe_timestamp(outgoing_segment, "outgoing", validation_options.local_probe_delta_seconds),
            )
            incoming_probe = frame_pool.probe(
                incoming_segment["asset_path"],
                _local_probe_timestamp(incoming_segment, "incoming", validation_options.local_probe_delta_seconds),
            )

            issues: list[dict[str, Any]] = []
            preferred_boundary = transcript_boundary.get("preferred_boundary")
            if transcript_boundary.get("speech_near_cut") and preferred_boundary is not None:
                boundary_delta_seconds = abs(float(preferred_boundary["delta_seconds"]))
                if boundary_delta_seconds > 0.4:
                    issues.append(
                        {
                            "code": "off_word_boundary",
                            "severity": "fail",
                            "message": "Cut sits far away from the nearest plausible word boundary.",
                        }
                    )
                elif boundary_delta_seconds > 0.18:
                    issues.append(
                        {
                            "code": "off_word_boundary",
                            "severity": "warn",
                            "message": "Cut is not close to the nearest plausible word boundary.",
                        }
                    )

            for side, signal_summary in (("outgoing", outgoing_signal), ("incoming", incoming_signal)):
                if not signal_summary["has_analysis"]:
                    continue
                usable_score = float(signal_summary["usable_score"])
                if usable_score < validation_options.analysis_fail_threshold:
                    issues.append(
                        {
                            "code": f"{side}_soft_analysis",
                            "severity": "fail",
                            "message": f"{side.capitalize()} camera scores too low in the local quality window.",
                        }
                    )
                elif usable_score < validation_options.analysis_soft_threshold:
                    issues.append(
                        {
                            "code": f"{side}_soft_analysis",
                            "severity": "warn",
                            "message": f"{side.capitalize()} camera is weak in the local quality window.",
                        }
                    )

            for side, probe in (("outgoing", outgoing_probe), ("incoming", incoming_probe)):
                if not probe["success"]:
                    issues.append(
                        {
                            "code": f"{side}_frame_probe_failed",
                            "severity": "warn",
                            "message": f"{side.capitalize()} cut-frame probe could not decode a frame.",
                        }
                    )
                    continue

                if probe["soft"]:
                    issues.append(
                        {
                            "code": f"{side}_soft_frame",
                            "severity": "warn",
                            "message": f"{side.capitalize()} cut frame looks soft on the sparse probe.",
                        }
                    )
                if probe["dark"]:
                    issues.append(
                        {
                            "code": f"{side}_dark_frame",
                            "severity": "warn",
                            "message": f"{side.capitalize()} cut frame is unusually dark on the sparse probe.",
                        }
                    )
                if probe.get("unstable"):
                    issues.append(
                        {
                            "code": f"{side}_motion_spike",
                            "severity": "warn",
                            "message": f"{side.capitalize()} cut window contains strong local camera motion.",
                        }
                    )

            preferred_incoming_asset_id = None
            current_candidate = next(
                (
                    candidate
                    for candidate in alternatives
                    if candidate["asset_id"] == incoming_segment["asset_id"]
                ),
                None,
            )
            current_candidate_score = (
                float(current_candidate["quality_score"])
                if current_candidate is not None
                else _candidate_quality_score(incoming_signal, str(incoming_segment.get("confidence") or "unknown"))
            )
            best_alternative = next(
                (
                    candidate
                    for candidate in alternatives
                    if candidate["asset_id"] != incoming_segment["asset_id"]
                    and candidate["covers_entire_incoming_segment"]
                ),
                None,
            )
            if best_alternative is not None and (
                float(best_alternative["quality_score"]) - current_candidate_score
            ) >= validation_options.alternate_margin:
                preferred_incoming_asset_id = str(best_alternative["asset_id"])
                issues.append(
                    {
                        "code": "better_alternative_available",
                        "severity": "warn",
                        "message": (
                            f"{preferred_incoming_asset_id} scores materially better than the current "
                            "incoming camera around this cut."
                        ),
                    }
                )

            status = "ok"
            if any(issue["severity"] == "fail" for issue in issues):
                status = "fail"
            elif issues:
                status = "warn"

            target_cut_seconds = None
            shift_seconds = None
            if preferred_boundary is not None:
                shift_seconds = float(preferred_boundary["delta_seconds"])
                if abs(shift_seconds) >= 0.04:
                    target_cut_seconds = float(preferred_boundary["target_cut_seconds"])

            cuts.append(
                {
                    "id": f"cut_{cut_index:04d}",
                    "cut_index": cut_index,
                    "status": status,
                    "current_cut_seconds": current_cut_seconds,
                    "outgoing_segment_id": outgoing_segment["id"],
                    "incoming_segment_id": incoming_segment["id"],
                    "outgoing_asset_id": outgoing_segment["asset_id"],
                    "incoming_asset_id": incoming_segment["asset_id"],
                    "transcript": transcript_boundary,
                    "outgoing": {
                        "segment_id": outgoing_segment["id"],
                        "asset_id": outgoing_segment["asset_id"],
                        "analysis": outgoing_signal,
                        "frame_probe": outgoing_probe,
                    },
                    "incoming": {
                        "segment_id": incoming_segment["id"],
                        "asset_id": incoming_segment["asset_id"],
                        "analysis": incoming_signal,
                        "frame_probe": incoming_probe,
                    },
                    "alternatives": alternatives,
                    "issues": issues,
                    "recommended_action": {
                        "target_cut_seconds": target_cut_seconds,
                        "shift_seconds": shift_seconds,
                        "preferred_incoming_asset_id": preferred_incoming_asset_id,
                    },
                }
            )
            if callable(on_progress):
                on_progress(cut_index, total_cut_pairs, f"Local pass {cut_index}/{total_cut_pairs}")
    finally:
        frame_pool.close()

    return {
        "schema_version": "vazer.cut_validation.v1",
        "generated_at_utc": _utc_timestamp(),
        "tool": {
            "name": "vazer",
            "version": __version__,
        },
        "source_cut_plan": {
            "schema_version": cut_plan["schema_version"],
            "path": source_cut_plan_path,
        },
        "source_sync_map": None
        if sync_map is None
        else {
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
        "options": {
            "transcript_search_window_seconds": validation_options.transcript_search_window_seconds,
            "transcript_pause_boundary_seconds": validation_options.transcript_pause_boundary_seconds,
            "cut_context_seconds": validation_options.cut_context_seconds,
            "local_probe_delta_seconds": validation_options.local_probe_delta_seconds,
            "local_probe_width": validation_options.local_probe_width,
            "local_dense_context_seconds": validation_options.local_dense_context_seconds,
            "local_dense_fps": validation_options.local_dense_fps,
            "local_dense_width": validation_options.local_dense_width,
            "local_dense_decoder_preference": validation_options.local_dense_decoder_preference,
            "local_dense_prefer_gpu": validation_options.local_dense_prefer_gpu,
            "analysis_soft_threshold": validation_options.analysis_soft_threshold,
            "analysis_fail_threshold": validation_options.analysis_fail_threshold,
            "alternate_margin": validation_options.alternate_margin,
            "repair_min_segment_seconds": validation_options.repair_min_segment_seconds,
        },
        "cuts": cuts,
        "summary": {
            "cuts_total": len(cuts),
            "ok": sum(1 for cut in cuts if cut["status"] == "ok"),
            "warn": sum(1 for cut in cuts if cut["status"] == "warn"),
            "fail": sum(1 for cut in cuts if cut["status"] == "fail"),
            "repairable": sum(
                1
                for cut in cuts
                if cut["recommended_action"]["target_cut_seconds"] is not None
                or cut["recommended_action"]["preferred_incoming_asset_id"] is not None
            ),
        },
    }


def _resolve_asset_coverage(
    asset_id: str,
    coverages_by_asset: dict[str, AssetCoverage],
    fallback_segment: dict[str, Any],
) -> AssetCoverage:
    coverage = coverages_by_asset.get(asset_id)
    if coverage is not None:
        return coverage

    speed = float(fallback_segment["speed"])
    offset_seconds = float(fallback_segment["source_start_seconds"]) - speed * float(fallback_segment["master_start_seconds"])
    return AssetCoverage(
        asset_id=asset_id,
        path=str(fallback_segment["asset_path"]),
        confidence=str(fallback_segment.get("confidence") or "unknown"),
        speed=speed,
        offset_seconds=offset_seconds,
        duration_seconds=float(fallback_segment["source_end_seconds"]),
        overlap_start_seconds=float(fallback_segment["master_start_seconds"]),
        overlap_end_seconds=float(fallback_segment["master_end_seconds"]),
    )


def _segment_with_updated_mapping(segment: dict[str, Any], coverage: AssetCoverage) -> dict[str, Any]:
    updated = dict(segment)
    updated["asset_id"] = coverage.asset_id
    updated["asset_path"] = coverage.path
    updated["confidence"] = coverage.confidence
    updated["speed"] = coverage.speed
    updated["source_start_seconds"] = max(
        0.0,
        coverage.speed * float(updated["master_start_seconds"]) + coverage.offset_seconds,
    )
    updated["source_end_seconds"] = min(
        coverage.duration_seconds,
        coverage.speed * float(updated["master_end_seconds"]) + coverage.offset_seconds,
    )
    updated["duration_seconds"] = float(updated["master_end_seconds"]) - float(updated["master_start_seconds"])
    return updated


def _rebuild_segment_timing(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rebuilt: list[dict[str, Any]] = []
    output_cursor = 0.0
    for segment in segments:
        duration_seconds = float(segment["master_end_seconds"]) - float(segment["master_start_seconds"])
        if duration_seconds <= EPSILON:
            continue

        updated = dict(segment)
        updated["duration_seconds"] = duration_seconds
        updated["output_start_seconds"] = output_cursor
        output_cursor += duration_seconds
        updated["output_end_seconds"] = output_cursor
        rebuilt.append(updated)
    return rebuilt


def _refresh_segment_signals(
    segments: list[dict[str, Any]],
    *,
    analysis_map: dict[str, Any] | None,
    transcript_artifact: dict[str, Any] | None,
) -> None:
    windows_by_asset = _analysis_windows_by_asset(analysis_map)
    speech_segments = _speech_segments(analysis_map, transcript_artifact)

    for segment in segments:
        master_start_seconds = float(segment["master_start_seconds"])
        master_end_seconds = float(segment["master_end_seconds"])
        speech_summary = _speech_overlap_summary(master_start_seconds, master_end_seconds, speech_segments)
        transcript_overlap_count, transcript_excerpt = _transcript_excerpt(
            master_start_seconds,
            master_end_seconds,
            transcript_artifact,
        )
        signal_summary = _analysis_signal_summary(
            segment["asset_id"],
            master_start_seconds,
            master_end_seconds,
            windows_by_asset,
        )
        segment["signals"] = {
            **speech_summary,
            "transcript_overlap_count": transcript_overlap_count,
            "transcript_excerpt": transcript_excerpt,
            "usable_score": signal_summary["usable_score"],
            "sharpness_score": signal_summary["sharpness_score"],
            "stability_score": signal_summary["stability_score"],
            "has_analysis": signal_summary["has_analysis"],
        }


def repair_cut_plan(
    cut_plan: dict[str, Any],
    validation_report: dict[str, Any],
    *,
    sync_map: dict[str, Any] | None = None,
    source_cut_plan_path: str | None = None,
    source_validation_path: str | None = None,
    analysis_map: dict[str, Any] | None = None,
    transcript_artifact: dict[str, Any] | None = None,
    options: CutValidationOptions | None = None,
) -> dict[str, Any]:
    if cut_plan.get("schema_version") != "vazer.cut_plan.v1":
        raise ValueError("Unsupported cut_plan schema version.")
    if validation_report.get("schema_version") != "vazer.cut_validation.v1":
        raise ValueError("Unsupported cut_validation schema version.")

    repair_options = options or CutValidationOptions()
    coverages_by_asset = (
        _build_coverages_from_sync_map(sync_map)
        if sync_map is not None
        else _build_coverages_from_cut_plan(cut_plan)
    )
    segments = [dict(segment) for segment in cut_plan.get("video_segments", []) if isinstance(segment, dict)]
    if not segments:
        raise ValueError("cut_plan does not contain any video segments.")

    applied_actions: list[dict[str, Any]] = []
    for cut in validation_report.get("cuts", []):
        if not isinstance(cut, dict):
            continue

        outgoing_segment_id = cut.get("outgoing_segment_id")
        incoming_segment_id = cut.get("incoming_segment_id")
        if not isinstance(outgoing_segment_id, str) or not isinstance(incoming_segment_id, str):
            continue

        adjacent_index = next(
            (
                index
                for index in range(len(segments) - 1)
                if segments[index]["id"] == outgoing_segment_id
                and segments[index + 1]["id"] == incoming_segment_id
            ),
            None,
        )
        if adjacent_index is None:
            continue

        outgoing_segment = dict(segments[adjacent_index])
        incoming_segment = dict(segments[adjacent_index + 1])
        current_cut_seconds = float(outgoing_segment["master_end_seconds"])
        min_cut_seconds = float(outgoing_segment["master_start_seconds"]) + repair_options.repair_min_segment_seconds
        max_cut_seconds = float(incoming_segment["master_end_seconds"]) - repair_options.repair_min_segment_seconds

        recommended_action = cut.get("recommended_action", {})
        target_cut_seconds = recommended_action.get("target_cut_seconds")
        applied_cut_seconds = current_cut_seconds
        if isinstance(target_cut_seconds, (int, float)):
            applied_cut_seconds = min(max(float(target_cut_seconds), min_cut_seconds), max_cut_seconds)

        preferred_incoming_asset_id = recommended_action.get("preferred_incoming_asset_id")
        applied_incoming_asset_id = incoming_segment["asset_id"]
        if isinstance(preferred_incoming_asset_id, str):
            candidate_coverage = coverages_by_asset.get(preferred_incoming_asset_id)
            if candidate_coverage is not None:
                if (
                    candidate_coverage.overlap_start_seconds <= applied_cut_seconds + EPSILON
                    and candidate_coverage.overlap_end_seconds >= float(incoming_segment["master_end_seconds"]) - EPSILON
                ):
                    applied_incoming_asset_id = preferred_incoming_asset_id

        outgoing_segment["master_end_seconds"] = applied_cut_seconds
        incoming_segment["master_start_seconds"] = applied_cut_seconds
        outgoing_coverage = _resolve_asset_coverage(outgoing_segment["asset_id"], coverages_by_asset, outgoing_segment)
        incoming_coverage = _resolve_asset_coverage(applied_incoming_asset_id, coverages_by_asset, incoming_segment)
        outgoing_segment = _segment_with_updated_mapping(outgoing_segment, outgoing_coverage)
        incoming_segment = _segment_with_updated_mapping(incoming_segment, incoming_coverage)

        if outgoing_segment["duration_seconds"] <= EPSILON or incoming_segment["duration_seconds"] <= EPSILON:
            continue

        if applied_cut_seconds != current_cut_seconds or applied_incoming_asset_id != incoming_segment["asset_id"]:
            outgoing_segment["reason"] = "Locally repaired after cut validation."
            incoming_segment["reason"] = "Locally repaired after cut validation."
            segments[adjacent_index] = outgoing_segment
            segments[adjacent_index + 1] = incoming_segment
            applied_actions.append(
                {
                    "cut_id": cut.get("id"),
                    "old_cut_seconds": current_cut_seconds,
                    "new_cut_seconds": applied_cut_seconds,
                    "shift_seconds": applied_cut_seconds - current_cut_seconds,
                    "incoming_asset_before": cut.get("incoming_asset_id"),
                    "incoming_asset_after": applied_incoming_asset_id,
                }
            )

    rebuilt_segments = _rebuild_segment_timing(segments)
    merged_segments = _merge_video_segments(rebuilt_segments)
    merged_segments = _rebuild_segment_timing(merged_segments)
    _refresh_segment_signals(
        merged_segments,
        analysis_map=analysis_map,
        transcript_artifact=transcript_artifact,
    )

    audio_segments = [
        {
            "id": f"audio_{index:04d}",
            "type": "master_audio",
            "source_path": cut_plan["master_audio"]["path"],
            "master_start_seconds": segment["master_start_seconds"],
            "master_end_seconds": segment["master_end_seconds"],
            "output_start_seconds": segment["output_start_seconds"],
            "output_end_seconds": segment["output_end_seconds"],
            "duration_seconds": segment["duration_seconds"],
            "source_start_seconds": segment["master_start_seconds"],
            "source_end_seconds": segment["master_end_seconds"],
        }
        for index, segment in enumerate(merged_segments, start=1)
    ]

    kept_intervals = [
        {
            "master_start_seconds": segment["master_start_seconds"],
            "master_end_seconds": segment["master_end_seconds"],
            "asset_id": segment["asset_id"],
        }
        for segment in merged_segments
    ]
    selected_assets = sorted({segment["asset_id"] for segment in merged_segments})
    synced_assets = {
        entry["asset_id"]
        for entry in sync_map.get("entries", [])
        if isinstance(entry, dict) and entry.get("status") == "synced"
    } if sync_map is not None else set(selected_assets)
    dropped_assets = sorted(synced_assets - set(selected_assets))

    repaired_cut_plan = dict(cut_plan)
    repaired_cut_plan["planning_stage"] = "repaired"
    repaired_cut_plan["generated_at_utc"] = _utc_timestamp()
    repaired_cut_plan["source_cut_plan"] = {
        "schema_version": cut_plan["schema_version"],
        "path": source_cut_plan_path,
    }
    repaired_cut_plan["source_validation_report"] = {
        "schema_version": validation_report["schema_version"],
        "path": source_validation_path,
    }
    repaired_cut_plan["timeline"] = {
        "master_span_start_seconds": float(merged_segments[0]["master_start_seconds"]),
        "master_span_end_seconds": float(merged_segments[-1]["master_end_seconds"]),
        "output_duration_seconds": float(merged_segments[-1]["output_end_seconds"]),
        "segment_count": len(merged_segments),
        "kept_intervals": kept_intervals,
    }
    repaired_cut_plan["video_segments"] = merged_segments
    repaired_cut_plan["audio_segments"] = audio_segments
    repaired_cut_plan["repair"] = {
        "source_validation_report": repaired_cut_plan["source_validation_report"],
        "applied_cut_actions": applied_actions,
        "summary": {
            "applied_cut_actions": len(applied_actions),
            "shifted_cuts": sum(1 for action in applied_actions if abs(float(action["shift_seconds"])) > EPSILON),
            "asset_swaps": sum(
                1
                for action in applied_actions
                if str(action["incoming_asset_before"]) != str(action["incoming_asset_after"])
            ),
        },
    }
    repaired_cut_plan["summary"] = {
        **dict(cut_plan.get("summary", {})),
        "planning_stage": "repaired",
        "selected_assets": selected_assets,
        "dropped_assets": dropped_assets,
        "synced_assets": len(synced_assets),
        "video_segments": len(merged_segments),
        "output_duration_seconds": float(merged_segments[-1]["output_end_seconds"]),
        "repair_applied": bool(applied_actions),
        "repair_actions": len(applied_actions),
    }
    return repaired_cut_plan
