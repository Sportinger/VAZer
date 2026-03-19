from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import __version__
from .fftools import decode_audio, probe_media
from .process_manager import popen_managed, unregister_process

EPSILON = 1e-6


@dataclass(slots=True)
class AnalysisOptions:
    audio_rate: int = 2000
    audio_frame_seconds: float = 0.5
    speech_merge_gap_seconds: float = 0.75
    speech_min_segment_seconds: float = 1.0
    video_sample_interval_seconds: float = 1.0
    video_window_seconds: float = 4.0
    analysis_width: int = 480
    decoder_preference: str = "auto"
    prefer_gpu: bool = True
    block_grid_size: int = 4
    block_highlight_ratio: float = 0.35
    local_dense_fps: float = 8.0
    local_dense_context_seconds: float = 2.0
    local_dense_width: int = 640


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


def _quantile(values: list[float] | np.ndarray, q: float, default: float = 0.0) -> float:
    if len(values) == 0:
        return float(default)
    return float(np.quantile(np.asarray(values, dtype=np.float64), q))


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


def _target_frame_size(source_width: int, source_height: int, target_width: int) -> tuple[int, int]:
    width = max(16, min(target_width, max(16, source_width)))
    height = max(2, int(round(source_height * width / max(source_width, 1) / 2.0) * 2))
    return width, height


def _sample_count_for_duration(duration_seconds: float, sample_interval_seconds: float) -> int:
    if duration_seconds <= EPSILON:
        return 1
    interval = max(sample_interval_seconds, 1e-3)
    return max(1, int(math.floor(duration_seconds / interval + EPSILON)) + 1)


def _hwaccel_ladder(decoder_preference: str, prefer_gpu: bool) -> list[tuple[str, list[str]]]:
    normalized = str(decoder_preference or "auto").strip().lower()
    if normalized == "cpu":
        return [("ffmpeg_cpu", [])]
    if normalized == "cuda":
        return [("ffmpeg_cuda", ["-hwaccel", "cuda"]), ("ffmpeg_cpu", [])]
    if prefer_gpu:
        return [
            ("ffmpeg_cuda", ["-hwaccel", "cuda"]),
            ("ffmpeg_auto", ["-hwaccel", "auto"]),
            ("ffmpeg_cpu", []),
        ]
    return [("ffmpeg_auto", ["-hwaccel", "auto"]), ("ffmpeg_cpu", [])]


def _build_ffmpeg_gray_command(
    path: str,
    *,
    target_width: int,
    target_height: int,
    sample_interval_seconds: float,
    start_seconds: float,
    duration_seconds: float,
    hwaccel_args: list[str],
) -> list[str]:
    sample_fps = 1.0 / max(sample_interval_seconds, 1e-6)
    filters = [
        f"fps={sample_fps:.8f}",
        f"scale={target_width}:{target_height}:flags=fast_bilinear",
        "format=gray",
    ]
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        *hwaccel_args,
        "-ss",
        f"{start_seconds:.6f}",
        "-t",
        f"{duration_seconds:.6f}",
        "-i",
        path,
        "-vf",
        ",".join(filters),
        "-vsync",
        "0",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def _iter_ffmpeg_sampled_frames(
    path: str,
    width: int,
    sample_interval_seconds: float,
    *,
    start_seconds: float = 0.0,
    duration_seconds: float | None = None,
    decoder_preference: str = "auto",
    prefer_gpu: bool = True,
    on_progress: Any | None = None,
) -> tuple[list[tuple[float, np.ndarray]], dict[str, Any]]:
    media_info = probe_media(path)
    if not media_info.video_streams:
        raise ValueError(f"No video stream found in {path}.")

    primary_video = media_info.video_streams[0]
    source_duration_seconds = media_info.duration_seconds or primary_video.duration_seconds
    if source_duration_seconds is None or source_duration_seconds <= 0:
        raise ValueError(f"Unable to determine video duration for {path}.")

    clipped_start_seconds = max(0.0, float(start_seconds))
    remaining_duration_seconds = max(0.0, float(source_duration_seconds) - clipped_start_seconds)
    clipped_duration_seconds = (
        remaining_duration_seconds
        if duration_seconds is None
        else min(max(float(duration_seconds), sample_interval_seconds), remaining_duration_seconds)
    )
    if clipped_duration_seconds <= EPSILON:
        raise ValueError(f"Requested analysis span is empty for {path}.")

    source_width = primary_video.width or width
    source_height = primary_video.height or width
    target_width, target_height = _target_frame_size(source_width, source_height, width)
    requested_samples = _sample_count_for_duration(clipped_duration_seconds, sample_interval_seconds)
    frame_bytes = target_width * target_height
    last_error = "No decoder strategy attempted."

    for decoder_method, hwaccel_args in _hwaccel_ladder(decoder_preference, prefer_gpu):
        command = _build_ffmpeg_gray_command(
            path,
            target_width=target_width,
            target_height=target_height,
            sample_interval_seconds=sample_interval_seconds,
            start_seconds=clipped_start_seconds,
            duration_seconds=clipped_duration_seconds,
            hwaccel_args=hwaccel_args,
        )
        frames: list[tuple[float, np.ndarray]] = []
        process = popen_managed(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stderr_text = ""
        try:
            if process.stdout is None:
                raise ValueError("ffmpeg did not expose stdout for frame streaming.")
            if callable(on_progress):
                on_progress(0.0, f"Streaming frames via {decoder_method}.")

            sample_index = 0
            while True:
                raw_frame = process.stdout.read(frame_bytes)
                if not raw_frame:
                    break
                while len(raw_frame) < frame_bytes:
                    remainder = process.stdout.read(frame_bytes - len(raw_frame))
                    if not remainder:
                        break
                    raw_frame += remainder
                if len(raw_frame) < frame_bytes:
                    break

                frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((target_height, target_width)).copy()
                timestamp_seconds = min(
                    clipped_start_seconds + sample_index * sample_interval_seconds,
                    clipped_start_seconds + clipped_duration_seconds,
                )
                frames.append((float(timestamp_seconds), frame))
                sample_index += 1
                if callable(on_progress):
                    on_progress(
                        min(99.0, (sample_index / max(1, requested_samples)) * 100.0),
                        f"Streaming frame {sample_index}/{requested_samples}",
                    )

            if process.stderr is not None:
                stderr_text = process.stderr.read().decode("utf-8", errors="replace").strip()
            return_code = process.wait()
        finally:
            unregister_process(process)

        if return_code == 0 and frames:
            if callable(on_progress):
                on_progress(100.0, f"Streaming complete via {decoder_method}.")
            return frames, {
                "method": "ffmpeg_sequential_gray",
                "decoder_method": decoder_method,
                "source_width": source_width,
                "source_height": source_height,
                "sample_width": target_width,
                "sample_height": target_height,
                "frame_count": len(frames),
                "requested_samples": requested_samples,
                "skipped_samples": max(0, requested_samples - len(frames)),
                "sample_interval_seconds": sample_interval_seconds,
                "start_seconds": clipped_start_seconds,
                "duration_seconds": clipped_duration_seconds,
            }

        if return_code == 0 and not frames:
            last_error = f"{decoder_method} returned no frames."
        else:
            last_error = stderr_text or f"{decoder_method} failed with return code {return_code}."

    raise ValueError(f"Sequential ffmpeg decode failed for {path}: {last_error}")


def _iter_opencv_sparse_seek_frames(
    path: str,
    width: int,
    sample_interval_seconds: float,
    *,
    start_seconds: float = 0.0,
    duration_seconds: float | None = None,
    on_progress: Any | None = None,
) -> tuple[list[tuple[float, np.ndarray]], dict[str, Any]]:
    media_info = probe_media(path)
    if not media_info.video_streams:
        raise ValueError(f"No video stream found in {path}.")

    primary_video = media_info.video_streams[0]
    source_duration_seconds = media_info.duration_seconds or primary_video.duration_seconds
    if source_duration_seconds is None or source_duration_seconds <= 0:
        raise ValueError(f"Unable to determine video duration for {path}.")

    source_width = primary_video.width or width
    source_height = primary_video.height or width
    target_width, target_height = _target_frame_size(source_width, source_height, width)
    clipped_start_seconds = max(0.0, float(start_seconds))
    remaining_duration_seconds = max(0.0, float(source_duration_seconds) - clipped_start_seconds)
    clipped_duration_seconds = (
        remaining_duration_seconds
        if duration_seconds is None
        else min(max(float(duration_seconds), sample_interval_seconds), remaining_duration_seconds)
    )
    if clipped_duration_seconds <= EPSILON:
        raise ValueError(f"Requested analysis span is empty for {path}.")

    requested_samples = _sample_count_for_duration(clipped_duration_seconds, sample_interval_seconds)
    sample_timestamps = [
        clipped_start_seconds + index * sample_interval_seconds
        for index in range(requested_samples)
    ]

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise ValueError(f"OpenCV could not open {path}.")

    frames: list[tuple[float, np.ndarray]] = []
    skipped_samples = 0
    if callable(on_progress):
        on_progress(0.0, "Falling back to sparse OpenCV seek.")
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
        "decoder_method": "opencv_sparse_seek",
        "source_width": source_width,
        "source_height": source_height,
        "sample_width": target_width,
        "sample_height": target_height,
        "frame_count": len(frames),
        "requested_samples": len(sample_timestamps),
        "skipped_samples": skipped_samples,
        "sample_interval_seconds": sample_interval_seconds,
        "start_seconds": clipped_start_seconds,
        "duration_seconds": clipped_duration_seconds,
    }


def _iter_sampled_frames(
    path: str,
    width: int,
    sample_interval_seconds: float,
    *,
    start_seconds: float = 0.0,
    duration_seconds: float | None = None,
    decoder_preference: str = "auto",
    prefer_gpu: bool = True,
    on_progress: Any | None = None,
) -> tuple[list[tuple[float, np.ndarray]], dict[str, Any]]:
    try:
        return _iter_ffmpeg_sampled_frames(
            path,
            width,
            sample_interval_seconds,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
            decoder_preference=decoder_preference,
            prefer_gpu=prefer_gpu,
            on_progress=on_progress,
        )
    except Exception:
        return _iter_opencv_sparse_seek_frames(
            path,
            width,
            sample_interval_seconds,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
            on_progress=on_progress,
        )


def _block_focus_value(frame: np.ndarray, grid_size: int, highlight_ratio: float) -> float:
    height, width = frame.shape
    block_scores: list[float] = []
    block_lumas: list[float] = []
    y_edges = np.linspace(0, height, max(2, grid_size + 1), dtype=int)
    x_edges = np.linspace(0, width, max(2, grid_size + 1), dtype=int)
    for row_index in range(len(y_edges) - 1):
        for column_index in range(len(x_edges) - 1):
            block = frame[y_edges[row_index] : y_edges[row_index + 1], x_edges[column_index] : x_edges[column_index + 1]]
            if block.size == 0:
                continue
            sobel_x = cv2.Sobel(block, cv2.CV_64F, 1, 0, ksize=3)
            sobel_y = cv2.Sobel(block, cv2.CV_64F, 0, 1, ksize=3)
            block_scores.append(float(np.mean(sobel_x * sobel_x + sobel_y * sobel_y)))
            block_lumas.append(float(np.mean(block) / 255.0))

    if not block_scores:
        return 0.0

    luma_threshold = _quantile(block_lumas, 0.25, default=0.0)
    candidates = [score for score, luma in zip(block_scores, block_lumas, strict=False) if luma >= luma_threshold]
    if not candidates:
        candidates = list(block_scores)
    candidates.sort(reverse=True)
    keep_count = max(1, int(math.ceil(len(candidates) * max(0.1, min(highlight_ratio, 1.0)))))
    return float(np.mean(candidates[:keep_count]))


def _sample_record_for_frame(
    source_time_seconds: float,
    frame: np.ndarray,
    previous_frame: np.ndarray | None,
    options: AnalysisOptions,
) -> dict[str, float]:
    laplacian_raw = float(cv2.Laplacian(frame, cv2.CV_64F).var())
    sobel_x = cv2.Sobel(frame, cv2.CV_64F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(frame, cv2.CV_64F, 0, 1, ksize=3)
    tenengrad_raw = float(np.mean(sobel_x * sobel_x + sobel_y * sobel_y))
    block_sharpness_raw = _block_focus_value(frame, options.block_grid_size, options.block_highlight_ratio)
    mean_luma = float(np.mean(frame) / 255.0)
    if previous_frame is None:
        motion_raw = 0.0
    else:
        motion_raw = float(np.mean(cv2.absdiff(frame, previous_frame)) / 255.0)

    combined_sharpness_raw = (
        0.5 * math.log1p(max(tenengrad_raw, 0.0))
        + 0.35 * math.log1p(max(block_sharpness_raw, 0.0))
        + 0.15 * math.log1p(max(laplacian_raw, 0.0))
    )
    return {
        "source_time_seconds": float(source_time_seconds),
        "sharpness_raw": float(combined_sharpness_raw),
        "laplacian_raw": laplacian_raw,
        "tenengrad_raw": tenengrad_raw,
        "block_sharpness_raw": block_sharpness_raw,
        "motion_raw": motion_raw,
        "mean_luma": mean_luma,
    }


def _build_sample_records(
    frames: list[tuple[float, np.ndarray]],
    options: AnalysisOptions,
) -> list[dict[str, float]]:
    previous_frame: np.ndarray | None = None
    sample_records: list[dict[str, float]] = []
    for source_time_seconds, frame in frames:
        sample_records.append(_sample_record_for_frame(source_time_seconds, frame, previous_frame, options))
        previous_frame = frame
    return sample_records


def _collect_sample_records_streaming(
    path: str,
    options: AnalysisOptions,
    *,
    on_progress: Any | None = None,
) -> tuple[list[dict[str, float]], dict[str, Any]]:
    media_info = probe_media(path)
    if not media_info.video_streams:
        raise ValueError(f"No video stream found in {path}.")

    primary_video = media_info.video_streams[0]
    source_duration_seconds = media_info.duration_seconds or primary_video.duration_seconds
    if source_duration_seconds is None or source_duration_seconds <= 0:
        raise ValueError(f"Unable to determine video duration for {path}.")

    source_width = primary_video.width or options.analysis_width
    source_height = primary_video.height or options.analysis_width
    target_width, target_height = _target_frame_size(source_width, source_height, options.analysis_width)
    requested_samples = _sample_count_for_duration(source_duration_seconds, options.video_sample_interval_seconds)
    frame_bytes = target_width * target_height
    last_error = "No decoder strategy attempted."

    for decoder_method, hwaccel_args in _hwaccel_ladder(options.decoder_preference, options.prefer_gpu):
        command = _build_ffmpeg_gray_command(
            path,
            target_width=target_width,
            target_height=target_height,
            sample_interval_seconds=options.video_sample_interval_seconds,
            start_seconds=0.0,
            duration_seconds=float(source_duration_seconds),
            hwaccel_args=hwaccel_args,
        )
        sample_records: list[dict[str, float]] = []
        previous_frame: np.ndarray | None = None
        stderr_text = ""
        process = popen_managed(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            if process.stdout is None:
                raise ValueError("ffmpeg did not expose stdout for frame streaming.")
            if callable(on_progress):
                on_progress(0.0, f"Streaming frames via {decoder_method}.")

            sample_index = 0
            while True:
                raw_frame = process.stdout.read(frame_bytes)
                if not raw_frame:
                    break
                while len(raw_frame) < frame_bytes:
                    remainder = process.stdout.read(frame_bytes - len(raw_frame))
                    if not remainder:
                        break
                    raw_frame += remainder
                if len(raw_frame) < frame_bytes:
                    break

                frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((target_height, target_width)).copy()
                timestamp_seconds = min(
                    sample_index * options.video_sample_interval_seconds,
                    float(source_duration_seconds),
                )
                sample_records.append(
                    _sample_record_for_frame(
                        timestamp_seconds,
                        frame,
                        previous_frame,
                        options,
                    )
                )
                previous_frame = frame
                sample_index += 1
                if callable(on_progress):
                    on_progress(
                        min(99.0, (sample_index / max(1, requested_samples)) * 100.0),
                        f"Streaming frame {sample_index}/{requested_samples}",
                    )

            if process.stderr is not None:
                stderr_text = process.stderr.read().decode("utf-8", errors="replace").strip()
            return_code = process.wait()
        finally:
            unregister_process(process)

        if return_code == 0 and sample_records:
            if callable(on_progress):
                on_progress(100.0, f"Streaming complete via {decoder_method}.")
            return sample_records, {
                "method": "ffmpeg_sequential_gray",
                "decoder_method": decoder_method,
                "source_width": source_width,
                "source_height": source_height,
                "sample_width": target_width,
                "sample_height": target_height,
                "frame_count": len(sample_records),
                "requested_samples": requested_samples,
                "skipped_samples": max(0, requested_samples - len(sample_records)),
                "sample_interval_seconds": options.video_sample_interval_seconds,
                "start_seconds": 0.0,
                "duration_seconds": float(source_duration_seconds),
            }

        if return_code == 0 and not sample_records:
            last_error = f"{decoder_method} returned no frames."
        else:
            last_error = stderr_text or f"{decoder_method} failed with return code {return_code}."

    frames, sampling_summary = _iter_opencv_sparse_seek_frames(
        path,
        options.analysis_width,
        options.video_sample_interval_seconds,
        on_progress=on_progress,
    )
    return _build_sample_records(frames, options), sampling_summary


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
                "luma_values": [],
            },
        )
        bucket["source_start_seconds"] = min(bucket["source_start_seconds"], sample["source_time_seconds"])
        bucket["source_end_seconds"] = max(bucket["source_end_seconds"], sample["source_time_seconds"] + options.video_sample_interval_seconds)
        bucket["sharpness_values"].append(sample["sharpness_raw"])
        bucket["motion_values"].append(sample["motion_raw"])
        bucket["luma_values"].append(sample["mean_luma"])

    ordered_windows = [windows[key] for key in sorted(windows)]
    if not ordered_windows:
        return []

    sharpness_floors = [_quantile(window["sharpness_values"], 0.2) for window in ordered_windows]
    sharpness_medians = [_quantile(window["sharpness_values"], 0.5) for window in ordered_windows]
    motion_means = [_quantile(window["motion_values"], 0.5) for window in ordered_windows]
    motion_peaks = [_quantile(window["motion_values"], 0.9) for window in ordered_windows]

    sharpness_low = _quantile(sharpness_floors, 0.1, default=min(sharpness_floors))
    sharpness_high = _quantile(sharpness_floors, 0.9, default=max(sharpness_floors))
    motion_low = _quantile(motion_peaks, 0.1, default=min(motion_peaks))
    motion_high = _quantile(motion_peaks, 0.9, default=max(motion_peaks))

    normalized_windows: list[dict[str, Any]] = []
    for index, window in enumerate(ordered_windows):
        sharpness_floor_raw = float(sharpness_floors[index])
        sharpness_median_raw = float(sharpness_medians[index])
        motion_mean_raw = float(motion_means[index])
        motion_peak_raw = float(motion_peaks[index])
        mean_luma = float(np.mean(window["luma_values"]))
        sharpness_score = _normalize_metric(sharpness_floor_raw, sharpness_low, sharpness_high)
        stability_score = _normalize_metric(motion_peak_raw, motion_low, motion_high, invert=True)
        usable_score = 0.6 * sharpness_score + 0.4 * stability_score
        normalized_windows.append(
            {
                "master_start_seconds": float(window["master_start_seconds"]),
                "master_end_seconds": float(window["master_end_seconds"]),
                "source_start_seconds": float(window["source_start_seconds"]),
                "source_end_seconds": float(window["source_end_seconds"]),
                "sample_count": len(window["sharpness_values"]),
                "sharpness_raw": sharpness_median_raw,
                "sharpness_floor_raw": sharpness_floor_raw,
                "motion_raw": motion_mean_raw,
                "motion_peak_raw": motion_peak_raw,
                "mean_luma": mean_luma,
                "sharpness_score": float(sharpness_score),
                "stability_score": float(stability_score),
                "usable_score": float(usable_score),
                "flags": {
                    "soft": sharpness_score < 0.35,
                    "stable": stability_score >= 0.55,
                    "sharp": sharpness_score >= 0.55,
                    "motion_spike": motion_peak_raw > max(motion_high, motion_low + 0.02),
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
    sample_records, sampling_summary = _collect_sample_records_streaming(
        path,
        options,
        on_progress=on_progress,
    )

    windows = _aggregate_video_windows(entry, sample_records, options)
    if windows:
        mean_sharpness = float(np.mean([window["sharpness_score"] for window in windows]))
        mean_stability = float(np.mean([window["stability_score"] for window in windows]))
        usable_ratio = float(np.mean([1.0 if window["usable_score"] >= 0.45 else 0.0 for window in windows]))
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


def analyze_local_dense_window(
    path: str,
    *,
    center_seconds: float,
    context_seconds: float,
    width: int,
    sample_fps: float,
    decoder_preference: str = "auto",
    prefer_gpu: bool = True,
    options: AnalysisOptions | None = None,
) -> dict[str, Any]:
    dense_options = options or AnalysisOptions()
    sample_interval_seconds = 1.0 / max(sample_fps, 1e-6)
    start_seconds = max(0.0, float(center_seconds) - max(context_seconds, sample_interval_seconds) / 2.0)
    frames, sampling_summary = _iter_sampled_frames(
        path,
        width,
        sample_interval_seconds,
        start_seconds=start_seconds,
        duration_seconds=max(context_seconds, sample_interval_seconds),
        decoder_preference=decoder_preference,
        prefer_gpu=prefer_gpu,
        on_progress=None,
    )
    sample_records = _build_sample_records(frames, dense_options)
    if not sample_records:
        raise ValueError("Local dense analysis returned no samples.")

    center_record = min(
        sample_records,
        key=lambda item: abs(float(item["source_time_seconds"]) - float(center_seconds)),
    )
    sharpness_values = [float(item["sharpness_raw"]) for item in sample_records]
    motion_values = [float(item["motion_raw"]) for item in sample_records]
    luma_values = [float(item["mean_luma"]) for item in sample_records]
    sharpness_p15 = _quantile(sharpness_values, 0.15, default=center_record["sharpness_raw"])
    sharpness_median = _quantile(sharpness_values, 0.5, default=center_record["sharpness_raw"])
    motion_peak = _quantile(motion_values, 0.9, default=center_record["motion_raw"])

    return {
        "success": True,
        "timestamp_seconds": float(center_seconds),
        "window_start_seconds": float(sample_records[0]["source_time_seconds"]),
        "window_end_seconds": float(sample_records[-1]["source_time_seconds"]),
        "sample_count": len(sample_records),
        "sharpness_raw": float(center_record["sharpness_raw"]),
        "motion_raw": float(center_record["motion_raw"]),
        "mean_luma": float(center_record["mean_luma"]),
        "sharpness_p15": float(sharpness_p15),
        "sharpness_median": float(sharpness_median),
        "motion_peak": float(motion_peak),
        "soft": float(center_record["sharpness_raw"]) < max(sharpness_p15, sharpness_median * 0.65),
        "dark": float(center_record["mean_luma"]) < 0.06,
        "unstable": float(center_record["motion_raw"]) > max(motion_peak, 0.08),
        "decoder_method": sampling_summary.get("decoder_method"),
        "sampling": sampling_summary,
        "summary": {
            "mean_luma": float(np.mean(luma_values)),
            "sharpness_mean": float(np.mean(sharpness_values)),
            "motion_mean": float(np.mean(motion_values)),
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
    decoder_methods = sorted(
        {
            str(entry.get("sampling", {}).get("decoder_method"))
            for entry in analyzed_entries
            if isinstance(entry, dict) and entry.get("status") == "analyzed" and entry.get("sampling", {}).get("decoder_method")
        }
    )
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
            "decoder_preference": options.decoder_preference,
            "prefer_gpu": options.prefer_gpu,
            "block_grid_size": options.block_grid_size,
            "block_highlight_ratio": options.block_highlight_ratio,
            "local_dense_fps": options.local_dense_fps,
            "local_dense_context_seconds": options.local_dense_context_seconds,
            "local_dense_width": options.local_dense_width,
            "video_sampler": "ffmpeg_sequential_gray",
        },
        "master_audio_activity": master_signals,
        "entries": analyzed_entries,
        "summary": {
            "total": len(analyzed_entries),
            "analyzed": sum(1 for entry in analyzed_entries if entry["status"] == "analyzed"),
            "failed": sum(1 for entry in analyzed_entries if entry["status"] == "failed"),
            "decoder_methods": decoder_methods,
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
