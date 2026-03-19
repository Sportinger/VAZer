from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import __version__
from .fftools import decode_audio, probe_media

EPSILON = 1e-6


@dataclass(slots=True)
class AnalysisOptions:
    audio_rate: int = 2000
    audio_frame_seconds: float = 0.5
    speech_merge_gap_seconds: float = 0.75
    speech_min_segment_seconds: float = 1.0
    video_sample_interval_seconds: float = 15.0
    video_window_seconds: float = 30.0
    analysis_width: int = 192


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _analysis_filters() -> list[str]:
    return ["highpass=f=100", "lowpass=f=1800"]


def _dbfs(value: float) -> float:
    return 20.0 * math.log10(max(value, 1e-9))


def _normalize_metric(value: float, low: float, high: float, invert: bool = False) -> float:
    if high - low < 1e-9:
        return 0.5
    normalized = (value - low) / (high - low)
    normalized = min(1.0, max(0.0, normalized))
    return 1.0 - normalized if invert else normalized


def _segmentize_mask(
    mask: np.ndarray,
    frame_seconds: float,
    merge_gap_seconds: float,
    min_segment_seconds: float,
    levels_dbfs: np.ndarray,
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    active_start_index: int | None = None

    for index, active in enumerate(mask.tolist()):
        if active and active_start_index is None:
            active_start_index = index
        if not active and active_start_index is not None:
            segments.append(
                {
                    "start_seconds": active_start_index * frame_seconds,
                    "end_seconds": index * frame_seconds,
                }
            )
            active_start_index = None

    if active_start_index is not None:
        segments.append(
            {
                "start_seconds": active_start_index * frame_seconds,
                "end_seconds": len(mask) * frame_seconds,
            }
        )

    merged: list[dict[str, Any]] = []
    for segment in segments:
        if not merged:
            merged.append(segment)
            continue

        previous = merged[-1]
        if segment["start_seconds"] - previous["end_seconds"] <= merge_gap_seconds + EPSILON:
            previous["end_seconds"] = segment["end_seconds"]
            continue

        merged.append(segment)

    filtered: list[dict[str, Any]] = []
    for segment in merged:
        if segment["end_seconds"] - segment["start_seconds"] < min_segment_seconds - EPSILON:
            continue

        start_index = int(segment["start_seconds"] / frame_seconds)
        end_index = max(start_index + 1, int(math.ceil(segment["end_seconds"] / frame_seconds)))
        segment_levels = levels_dbfs[start_index:end_index]
        filtered.append(
            {
                "start_seconds": segment["start_seconds"],
                "end_seconds": segment["end_seconds"],
                "kind": "speech_like",
                "mean_level_dbfs": float(np.mean(segment_levels)),
                "peak_level_dbfs": float(np.max(segment_levels)),
            }
        )

    return filtered


def analyze_master_audio_activity(master_path: str, options: AnalysisOptions) -> dict[str, Any]:
    samples = decode_audio(
        master_path,
        sample_rate=options.audio_rate,
        filters=_analysis_filters(),
    ).astype(np.float64)

    frame_size = max(1, round(options.audio_rate * options.audio_frame_seconds))
    usable = samples[: samples.size // frame_size * frame_size]
    if usable.size == 0:
        return {
            "segments": [],
            "summary": {
                "segment_count": 0,
                "threshold_dbfs": None,
            },
        }

    rms_frames = np.sqrt(np.mean(np.square(usable.reshape(-1, frame_size)), axis=1))
    levels_dbfs = np.array([_dbfs(float(value)) for value in rms_frames], dtype=np.float64)
    q15, q40, q85 = np.quantile(levels_dbfs, [0.15, 0.40, 0.85])
    threshold_dbfs = max(q15 + 0.45 * (q85 - q15), q40)
    active_mask = levels_dbfs >= threshold_dbfs

    segments = _segmentize_mask(
        active_mask,
        options.audio_frame_seconds,
        options.speech_merge_gap_seconds,
        options.speech_min_segment_seconds,
        levels_dbfs,
    )

    return {
        "segments": segments,
        "summary": {
            "segment_count": len(segments),
            "threshold_dbfs": float(threshold_dbfs),
            "frame_seconds": options.audio_frame_seconds,
        },
    }


def _iter_sampled_frames(
    path: str,
    width: int,
    sample_interval_seconds: float,
    *,
    on_progress: Any | None = None,
) -> tuple[list[tuple[float, np.ndarray]], dict[str, Any]]:
    media_info = probe_media(path)
    if not media_info.video_streams:
        raise ValueError(f"No video stream found in {path}.")

    primary_video = media_info.video_streams[0]
    duration_seconds = media_info.duration_seconds or primary_video.duration_seconds
    if duration_seconds is None or duration_seconds <= 0:
        raise ValueError(f"Unable to determine video duration for {path}.")

    source_width = primary_video.width or width
    source_height = primary_video.height or width
    target_width = min(width, source_width)
    target_height = max(2, int(round(source_height * target_width / source_width / 2.0) * 2))
    sample_timestamps = list(np.arange(0.0, duration_seconds + EPSILON, sample_interval_seconds))
    if not sample_timestamps or sample_timestamps[-1] < duration_seconds - EPSILON:
        sample_timestamps.append(duration_seconds)

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise ValueError(f"OpenCV could not open {path}.")

    frames: list[tuple[float, np.ndarray]] = []
    skipped_samples = 0
    if callable(on_progress):
        on_progress(0.0, "Sampling video frames.")
    try:
        total_samples = max(1, len(sample_timestamps))
        for sample_index, timestamp in enumerate(sample_timestamps, start=1):
            seek_milliseconds = max(0.0, float(timestamp) * 1000.0)
            capture.set(cv2.CAP_PROP_POS_MSEC, seek_milliseconds)
            ok, frame = capture.read()
            if not ok or frame is None:
                skipped_samples += 1
                if callable(on_progress):
                    on_progress((sample_index / total_samples) * 100.0, f"Sampling frame {sample_index}/{total_samples}")
                continue

            grayscale = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if grayscale.shape[1] != target_width or grayscale.shape[0] != target_height:
                grayscale = cv2.resize(grayscale, (target_width, target_height), interpolation=cv2.INTER_AREA)
            frames.append((float(timestamp), grayscale))
            if callable(on_progress):
                on_progress((sample_index / total_samples) * 100.0, f"Sampling frame {sample_index}/{total_samples}")
    finally:
        capture.release()

    if not frames:
        raise ValueError(f"No analysis frames could be sampled from {path}.")

    return frames, {
        "method": "opencv_sparse_seek",
        "source_width": source_width,
        "source_height": source_height,
        "sample_width": target_width,
        "sample_height": target_height,
        "frame_count": len(frames),
        "requested_samples": len(sample_timestamps),
        "skipped_samples": skipped_samples,
        "sample_interval_seconds": sample_interval_seconds,
    }


def _aggregate_video_windows(
    entry: dict[str, Any],
    sample_records: list[dict[str, float]],
    options: AnalysisOptions,
) -> list[dict[str, Any]]:
    mapping = entry["mapping"]
    speed = float(mapping["speed"])
    offset_seconds = float(mapping["offset_seconds"])
    asset_id = entry["asset_id"]
    windows: dict[float, dict[str, Any]] = {}

    for sample in sample_records:
        master_time_seconds = (sample["source_time_seconds"] - offset_seconds) / speed
        if master_time_seconds < 0:
            continue

        window_start_seconds = math.floor(master_time_seconds / options.video_window_seconds) * options.video_window_seconds
        bucket = windows.setdefault(
            window_start_seconds,
            {
                "asset_id": asset_id,
                "master_start_seconds": window_start_seconds,
                "master_end_seconds": window_start_seconds + options.video_window_seconds,
                "source_start_seconds": sample["source_time_seconds"],
                "source_end_seconds": sample["source_time_seconds"],
                "sharpness_values": [],
                "motion_values": [],
            },
        )
        bucket["source_start_seconds"] = min(bucket["source_start_seconds"], sample["source_time_seconds"])
        bucket["source_end_seconds"] = max(bucket["source_end_seconds"], sample["source_time_seconds"] + options.video_sample_interval_seconds)
        bucket["sharpness_values"].append(sample["sharpness_raw"])
        bucket["motion_values"].append(sample["motion_raw"])

    ordered_windows = [windows[key] for key in sorted(windows)]
    if not ordered_windows:
        return []

    sharpness_means = np.array(
        [float(np.mean(window["sharpness_values"])) for window in ordered_windows],
        dtype=np.float64,
    )
    motion_means = np.array(
        [float(np.mean(window["motion_values"])) for window in ordered_windows],
        dtype=np.float64,
    )
    sharpness_low, sharpness_high = np.quantile(sharpness_means, [0.25, 0.75])
    motion_low, motion_high = np.quantile(motion_means, [0.25, 0.75])

    normalized_windows: list[dict[str, Any]] = []
    for index, window in enumerate(ordered_windows):
        sharpness_raw = float(sharpness_means[index])
        motion_raw = float(motion_means[index])
        sharpness_score = _normalize_metric(sharpness_raw, float(sharpness_low), float(sharpness_high))
        stability_score = _normalize_metric(motion_raw, float(motion_low), float(motion_high), invert=True)
        usable_score = 0.65 * sharpness_score + 0.35 * stability_score
        normalized_windows.append(
            {
                "master_start_seconds": float(window["master_start_seconds"]),
                "master_end_seconds": float(window["master_end_seconds"]),
                "source_start_seconds": float(window["source_start_seconds"]),
                "source_end_seconds": float(window["source_end_seconds"]),
                "sample_count": len(window["sharpness_values"]),
                "sharpness_raw": sharpness_raw,
                "motion_raw": motion_raw,
                "sharpness_score": float(sharpness_score),
                "stability_score": float(stability_score),
                "usable_score": float(usable_score),
                "flags": {
                    "soft": usable_score >= 0.4,
                    "stable": stability_score >= 0.4,
                    "sharp": sharpness_score >= 0.4,
                },
            }
        )

    return normalized_windows


def analyze_camera_video_signals(
    entry: dict[str, Any],
    options: AnalysisOptions,
    *,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    path = entry["path"]
    frames, sampling_summary = _iter_sampled_frames(
        path,
        options.analysis_width,
        options.video_sample_interval_seconds,
        on_progress=on_progress,
    )
    previous_frame: np.ndarray | None = None
    sample_records: list[dict[str, float]] = []
    for source_time_seconds, frame in frames:
        laplacian = cv2.Laplacian(frame, cv2.CV_64F)
        sharpness_raw = float(laplacian.var())
        if previous_frame is None:
            motion_raw = 0.0
        else:
            motion_raw = float(np.mean(cv2.absdiff(frame, previous_frame)) / 255.0)
        sample_records.append(
            {
                "source_time_seconds": float(source_time_seconds),
                "sharpness_raw": sharpness_raw,
                "motion_raw": motion_raw,
            }
        )
        previous_frame = frame

    windows = _aggregate_video_windows(entry, sample_records, options)
    if windows:
        mean_sharpness = float(np.mean([window["sharpness_score"] for window in windows]))
        mean_stability = float(np.mean([window["stability_score"] for window in windows]))
        usable_ratio = float(np.mean([1.0 if window["flags"]["soft"] else 0.0 for window in windows]))
    else:
        mean_sharpness = 0.0
        mean_stability = 0.0
        usable_ratio = 0.0

    return {
        "asset_id": entry["asset_id"],
        "path": path,
        "status": "analyzed",
        "sampling": sampling_summary,
        "windows": windows,
        "summary": {
            "window_count": len(windows),
            "mean_sharpness_score": mean_sharpness,
            "mean_stability_score": mean_stability,
            "usable_window_ratio": usable_ratio,
        },
    }


def compose_analysis_map(
    sync_map: dict[str, Any],
    *,
    source_sync_map_path: str | None,
    options: AnalysisOptions,
    master_signals: dict[str, Any],
    analyzed_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "vazer.analysis_map.v1",
        "generated_at_utc": _utc_timestamp(),
        "tool": {
            "name": "vazer",
            "version": __version__,
        },
        "source_sync_map": {
            "schema_version": sync_map["schema_version"],
            "path": source_sync_map_path,
        },
        "master": sync_map["master"],
        "options": {
            "audio_rate": options.audio_rate,
            "audio_frame_seconds": options.audio_frame_seconds,
            "speech_merge_gap_seconds": options.speech_merge_gap_seconds,
            "speech_min_segment_seconds": options.speech_min_segment_seconds,
            "video_sample_interval_seconds": options.video_sample_interval_seconds,
            "video_window_seconds": options.video_window_seconds,
            "analysis_width": options.analysis_width,
            "video_sampler": "opencv_sparse_seek",
        },
        "master_audio_activity": master_signals,
        "entries": analyzed_entries,
        "summary": {
            "total": len(analyzed_entries),
            "analyzed": sum(1 for entry in analyzed_entries if entry["status"] == "analyzed"),
            "failed": sum(1 for entry in analyzed_entries if entry["status"] == "failed"),
        },
    }


def build_analysis_map(
    sync_map: dict[str, Any],
    *,
    source_sync_map_path: str | None = None,
    options: AnalysisOptions | None = None,
) -> dict[str, Any]:
    if sync_map.get("schema_version") != "vazer.sync_map.v1":
        raise ValueError("Unsupported sync_map schema version.")

    analysis_options = options or AnalysisOptions()
    master_payload = sync_map.get("master")
    if not isinstance(master_payload, dict):
        raise ValueError("sync_map master payload is missing.")

    master_path = master_payload.get("path")
    if not isinstance(master_path, str) or not master_path:
        raise ValueError("sync_map master path is missing.")

    master_signals = analyze_master_audio_activity(master_path, analysis_options)

    synced_entries = [
        entry
        for entry in sync_map.get("entries", [])
        if isinstance(entry, dict) and entry.get("status") == "synced"
    ]

    analyzed_entries: list[dict[str, Any]] = []
    if synced_entries:
        max_workers = min(len(synced_entries), max(1, os.cpu_count() or 1), 4)

        def _analyze_entry(entry: dict[str, Any]) -> dict[str, Any]:
            try:
                return analyze_camera_video_signals(entry, analysis_options)
            except Exception as error:
                return {
                    "asset_id": entry.get("asset_id"),
                    "path": entry.get("path"),
                    "status": "failed",
                    "error": str(error),
                }

        if max_workers == 1:
            analyzed_entries = [_analyze_entry(entry) for entry in synced_entries]
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                analyzed_entries = list(executor.map(_analyze_entry, synced_entries))

    return compose_analysis_map(
        sync_map,
        source_sync_map_path=source_sync_map_path,
        options=analysis_options,
        master_signals=master_signals,
        analyzed_entries=analyzed_entries,
    )


def load_analysis_map(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_analysis_map(analysis_map: dict[str, Any], output_path: str) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(analysis_map, indent=2), encoding="utf-8")
    return destination
