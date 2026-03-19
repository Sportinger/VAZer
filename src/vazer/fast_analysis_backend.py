from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import __version__
from .fftools import probe_media
from .ffmpeg_stream import SequentialGrayFrameReader, StreamRequest, resolve_sample_fps

EPSILON = 1e-6


@dataclass(slots=True)
class FastAnalysisOptions:
    sample_fps: float = 1.0
    sample_interval_seconds: float | None = None
    analysis_width: int = 640
    analysis_height: int | None = None
    video_window_seconds: float = 30.0
    prefer_gpu: bool = True
    prefer_opencv_fallback: bool = True
    block_rows: int = 4
    block_cols: int = 4


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalized_metric(value: float, low: float, high: float, *, invert: bool = False) -> float:
    if high - low < 1e-9:
        return 0.5
    normalized = (value - low) / (high - low)
    normalized = min(1.0, max(0.0, normalized))
    return 1.0 - normalized if invert else normalized


def _decode_duration(path: str) -> tuple[float, dict[str, Any]]:
    media_info = probe_media(path)
    duration_seconds = media_info.duration_seconds
    if duration_seconds is None or duration_seconds <= 0:
        raise ValueError(f"Unable to determine usable duration for {path}.")

    primary_video = media_info.video_streams[0] if media_info.video_streams else None
    metadata = {
        "format_name": media_info.format_name,
        "duration_seconds": float(duration_seconds),
        "source_width": None if primary_video is None else primary_video.width,
        "source_height": None if primary_video is None else primary_video.height,
        "source_fps": None if primary_video is None else primary_video.frame_rate,
    }
    return float(duration_seconds), metadata


def _blockwise_laplacian(frame: np.ndarray, *, rows: int, cols: int) -> dict[str, float]:
    if rows <= 1 and cols <= 1:
        return {
            "block_laplacian_mean": float(cv2.Laplacian(frame, cv2.CV_64F).var()),
            "block_laplacian_std": 0.0,
            "block_laplacian_p90": float(cv2.Laplacian(frame, cv2.CV_64F).var()),
        }

    height, width = frame.shape[:2]
    block_width = max(1, width // max(1, cols))
    block_height = max(1, height // max(1, rows))
    values: list[float] = []
    for row in range(rows):
        y0 = row * block_height
        y1 = height if row == rows - 1 else min(height, (row + 1) * block_height)
        for col in range(cols):
            x0 = col * block_width
            x1 = width if col == cols - 1 else min(width, (col + 1) * block_width)
            block = frame[y0:y1, x0:x1]
            if block.size == 0:
                continue
            values.append(float(cv2.Laplacian(block, cv2.CV_64F).var()))

    if not values:
        return {
            "block_laplacian_mean": 0.0,
            "block_laplacian_std": 0.0,
            "block_laplacian_p90": 0.0,
        }

    block_values = np.asarray(values, dtype=np.float64)
    return {
        "block_laplacian_mean": float(np.mean(block_values)),
        "block_laplacian_std": float(np.std(block_values)),
        "block_laplacian_p90": float(np.quantile(block_values, 0.9)),
    }


def compute_frame_metrics(
    frame: np.ndarray,
    *,
    previous_frame: np.ndarray | None = None,
    block_rows: int = 0,
    block_cols: int = 0,
) -> dict[str, Any]:
    laplacian = cv2.Laplacian(frame, cv2.CV_64F)
    sobel_x = cv2.Sobel(frame, cv2.CV_64F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(frame, cv2.CV_64F, 0, 1, ksize=3)
    tenengrad_map = np.square(sobel_x) + np.square(sobel_y)
    if previous_frame is None:
        motion_raw = 0.0
    else:
        motion_raw = float(np.mean(cv2.absdiff(frame, previous_frame)) / 255.0)

    metrics: dict[str, Any] = {
        "sharpness_laplacian_raw": float(laplacian.var()),
        "sharpness_tenengrad_raw": float(np.mean(tenengrad_map)),
        "motion_raw": float(motion_raw),
        "mean_luma": float(np.mean(frame) / 255.0),
    }
    if block_rows > 0 and block_cols > 0:
        metrics.update(_blockwise_laplacian(frame, rows=block_rows, cols=block_cols))
    return metrics


def collect_video_samples(
    path: str,
    options: FastAnalysisOptions,
    *,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    duration_seconds, media_metadata = _decode_duration(path)
    sample_fps = resolve_sample_fps(
        sample_fps=options.sample_fps,
        sample_interval_seconds=options.sample_interval_seconds,
    )
    request = StreamRequest(
        path=path,
        sample_fps=sample_fps,
        max_width=options.analysis_width,
        max_height=options.analysis_height,
        prefer_gpu=options.prefer_gpu,
        prefer_opencv_fallback=options.prefer_opencv_fallback,
    )

    samples: list[dict[str, Any]] = []
    previous_frame: np.ndarray | None = None
    with SequentialGrayFrameReader(path, request, on_progress=on_progress) as reader:
        for frame_index, decoded in enumerate(reader, start=1):
            metrics = compute_frame_metrics(
                decoded.frame,
                previous_frame=previous_frame,
                block_rows=options.block_rows,
                block_cols=options.block_cols,
            )
            previous_frame = decoded.frame
            samples.append(
                {
                    "sample_index": frame_index,
                    "source_time_seconds": float(decoded.timestamp_seconds),
                    **metrics,
                }
            )

        sampling_metadata = dict(reader.metadata)

    return {
        "path": path,
        "duration_seconds": duration_seconds,
        "sampling": sampling_metadata,
        "samples": samples,
        "summary": {
            "sample_count": len(samples),
            "sample_fps": float(sample_fps),
            "sample_interval_seconds": float(1.0 / sample_fps),
            "decoder_method": sampling_metadata.get("decoder_method"),
            "decoder_acceleration": sampling_metadata.get("decoder_acceleration"),
            "source_width": media_metadata.get("source_width"),
            "source_height": media_metadata.get("source_height"),
            "sample_width": sampling_metadata.get("sample_width"),
            "sample_height": sampling_metadata.get("sample_height"),
        },
    }


def aggregate_video_windows(
    samples: list[dict[str, Any]],
    *,
    asset_id: str,
    mapping: dict[str, Any],
    video_window_seconds: float,
) -> list[dict[str, Any]]:
    speed = float(mapping["speed"])
    offset_seconds = float(mapping["offset_seconds"])
    if speed <= 0:
        raise ValueError("Mapping speed must be positive.")

    windows: dict[float, dict[str, Any]] = {}
    for sample in samples:
        master_time_seconds = (float(sample["source_time_seconds"]) - offset_seconds) / speed
        if master_time_seconds < 0:
            continue

        window_start_seconds = math.floor(master_time_seconds / video_window_seconds) * video_window_seconds
        bucket = windows.setdefault(
            window_start_seconds,
            {
                "asset_id": asset_id,
                "master_start_seconds": window_start_seconds,
                "master_end_seconds": window_start_seconds + video_window_seconds,
                "source_start_seconds": float(sample["source_time_seconds"]),
                "source_end_seconds": float(sample["source_time_seconds"]),
                "sample_count": 0,
                "sharpness_laplacian_values": [],
                "sharpness_tenengrad_values": [],
                "motion_values": [],
                "luma_values": [],
                "block_laplacian_values": [],
            },
        )
        bucket["sample_count"] += 1
        bucket["source_start_seconds"] = min(bucket["source_start_seconds"], float(sample["source_time_seconds"]))
        bucket["source_end_seconds"] = max(bucket["source_end_seconds"], float(sample["source_time_seconds"]))
        bucket["sharpness_laplacian_values"].append(float(sample["sharpness_laplacian_raw"]))
        bucket["sharpness_tenengrad_values"].append(float(sample["sharpness_tenengrad_raw"]))
        bucket["motion_values"].append(float(sample["motion_raw"]))
        bucket["luma_values"].append(float(sample["mean_luma"]))
        if "block_laplacian_mean" in sample:
            bucket["block_laplacian_values"].append(float(sample["block_laplacian_mean"]))

    ordered_windows = [windows[key] for key in sorted(windows)]
    if not ordered_windows:
        return []

    laplacian_means = np.array([float(np.mean(window["sharpness_laplacian_values"])) for window in ordered_windows], dtype=np.float64)
    tenengrad_means = np.array([float(np.mean(window["sharpness_tenengrad_values"])) for window in ordered_windows], dtype=np.float64)
    motion_means = np.array([float(np.mean(window["motion_values"])) for window in ordered_windows], dtype=np.float64)
    laplacian_low, laplacian_high = np.quantile(laplacian_means, [0.25, 0.75])
    tenengrad_low, tenengrad_high = np.quantile(tenengrad_means, [0.25, 0.75])
    motion_low, motion_high = np.quantile(motion_means, [0.25, 0.75])

    normalized_windows: list[dict[str, Any]] = []
    for index, window in enumerate(ordered_windows):
        laplacian_raw = float(laplacian_means[index])
        tenengrad_raw = float(tenengrad_means[index])
        motion_raw = float(motion_means[index])
        sharpness_laplacian_score = _normalized_metric(laplacian_raw, float(laplacian_low), float(laplacian_high))
        sharpness_tenengrad_score = _normalized_metric(tenengrad_raw, float(tenengrad_low), float(tenengrad_high))
        sharpness_score = 0.6 * sharpness_laplacian_score + 0.4 * sharpness_tenengrad_score
        stability_score = _normalized_metric(motion_raw, float(motion_low), float(motion_high), invert=True)
        usable_score = 0.65 * sharpness_score + 0.35 * stability_score
        block_values = np.asarray(window["block_laplacian_values"], dtype=np.float64) if window["block_laplacian_values"] else np.asarray([], dtype=np.float64)
        normalized_windows.append(
            {
                "asset_id": asset_id,
                "master_start_seconds": float(window["master_start_seconds"]),
                "master_end_seconds": float(window["master_end_seconds"]),
                "source_start_seconds": float(window["source_start_seconds"]),
                "source_end_seconds": float(window["source_end_seconds"]),
                "sample_count": int(window["sample_count"]),
                "sharpness_laplacian_raw": laplacian_raw,
                "sharpness_tenengrad_raw": tenengrad_raw,
                "motion_raw": motion_raw,
                "sharpness_laplacian_score": float(sharpness_laplacian_score),
                "sharpness_tenengrad_score": float(sharpness_tenengrad_score),
                "sharpness_score": float(sharpness_score),
                "stability_score": float(stability_score),
                "usable_score": float(usable_score),
                "block_laplacian_mean": float(np.mean(block_values)) if block_values.size else None,
                "block_laplacian_std": float(np.std(block_values)) if block_values.size else None,
                "block_laplacian_p90": float(np.quantile(block_values, 0.9)) if block_values.size else None,
                "flags": {
                    "soft": usable_score >= 0.4,
                    "stable": stability_score >= 0.4,
                    "sharp": sharpness_score >= 0.4,
                },
            }
        )

    return normalized_windows


def analyze_camera_video(
    entry: dict[str, Any],
    options: FastAnalysisOptions,
    *,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    sample_bundle = collect_video_samples(entry["path"], options, on_progress=on_progress)
    windows = aggregate_video_windows(
        sample_bundle["samples"],
        asset_id=entry["asset_id"],
        mapping=entry["mapping"],
        video_window_seconds=options.video_window_seconds,
    )
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
        "path": entry["path"],
        "status": "analyzed",
        "sampling": sample_bundle["sampling"],
        "windows": windows,
        "summary": {
            "window_count": len(windows),
            "mean_sharpness_score": mean_sharpness,
            "mean_stability_score": mean_stability,
            "usable_window_ratio": usable_ratio,
        },
    }


def compose_fast_analysis_map(
    sync_map: dict[str, Any],
    *,
    source_sync_map_path: str | None,
    options: FastAnalysisOptions,
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
            "sample_fps": options.sample_fps,
            "sample_interval_seconds": options.sample_interval_seconds,
            "analysis_width": options.analysis_width,
            "analysis_height": options.analysis_height,
            "video_window_seconds": options.video_window_seconds,
            "prefer_gpu": options.prefer_gpu,
            "prefer_opencv_fallback": options.prefer_opencv_fallback,
            "block_rows": options.block_rows,
            "block_cols": options.block_cols,
        },
        "master_audio_activity": master_signals,
        "entries": analyzed_entries,
        "summary": {
            "total": len(analyzed_entries),
            "analyzed": sum(1 for entry in analyzed_entries if entry["status"] == "analyzed"),
            "failed": sum(1 for entry in analyzed_entries if entry["status"] == "failed"),
        },
    }


def build_fast_camera_analysis(
    entry: dict[str, Any],
    options: FastAnalysisOptions,
    *,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    return analyze_camera_video(entry, options, on_progress=on_progress)


def build_fast_analysis_map(
    sync_map: dict[str, Any],
    *,
    source_sync_map_path: str | None,
    options: FastAnalysisOptions,
    master_signals: dict[str, Any],
    analyzed_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    return compose_fast_analysis_map(
        sync_map,
        source_sync_map_path=source_sync_map_path,
        options=options,
        master_signals=master_signals,
        analyzed_entries=analyzed_entries,
    )
