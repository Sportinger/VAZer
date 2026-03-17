from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .fftools import AudioStreamInfo, MediaInfo, decode_audio, probe_media

SILENCE_RMS_THRESHOLD = 5e-4
DUPLICATE_SIMILARITY_THRESHOLD = 0.995
ALLOWED_MASTER_END_OVERHANG_SECONDS = 60.0


@dataclass(slots=True)
class SyncOptions:
    coarse_rate: int = 1000
    fine_rate: int = 4000
    envelope_bin_seconds: float = 0.1
    activity_rate: int = 2000
    activity_window_seconds: float = 12.0
    anchor_count: int = 6
    anchor_window_seconds: float = 45.0
    anchor_search_seconds: float = 1.5


@dataclass(slots=True)
class StreamInspection:
    map_specifier: str
    absolute_stream_index: int
    codec_name: str | None
    sample_rate: int | None
    channels: int | None
    loudest_rms: float
    loudest_window_start_seconds: float
    active: bool
    duplicate_of: str | None
    duplicate_similarity: float | None


@dataclass(slots=True)
class CoarseSyncMeasurement:
    map_specifier: str
    method: str
    camera_starts_at_master_seconds: float
    master_to_source_offset_seconds: float
    peak: float
    second_peak: float | None
    peak_ratio: float | None


@dataclass(slots=True)
class AnchorMeasurement:
    master_reference_seconds: float
    source_minus_master_seconds: float
    lag_seconds: float
    peak: float
    second_peak: float | None
    peak_ratio: float | None
    accepted: bool


@dataclass(slots=True)
class MediaSummary:
    path: str
    duration_seconds: float
    format_name: str | None


@dataclass(slots=True)
class CameraSummary:
    path: str
    duration_seconds: float
    format_name: str | None
    streams: list[StreamInspection]
    selected_stream: StreamInspection


@dataclass(slots=True)
class SyncMapping:
    speed: float
    offset_seconds: float
    camera_starts_at_master_seconds: float
    predicted_drift_over_hour_seconds: float
    model: str


@dataclass(slots=True)
class SyncSummary:
    confidence: str
    notes: list[str]


@dataclass(slots=True)
class SyncProbeReport:
    master: MediaSummary
    camera: CameraSummary
    coarse: CoarseSyncMeasurement
    anchors: dict[str, list[AnchorMeasurement]]
    mapping: SyncMapping
    summary: SyncSummary


def _analysis_filters() -> list[str]:
    return ["highpass=f=100", "lowpass=f=1800"]


def _require_duration(media: MediaInfo, label: str) -> float:
    if media.duration_seconds is None or media.duration_seconds <= 0:
        raise ValueError(f"{label} does not expose a usable duration.")
    return media.duration_seconds


def _rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def _normalize_series(samples: np.ndarray) -> np.ndarray:
    if samples.size == 0:
        return samples.astype(np.float64)

    values = samples.astype(np.float64)
    centered = values - values.mean()
    standard_deviation = centered.std()
    if standard_deviation < 1e-12:
        return centered
    return centered / standard_deviation


def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    length = min(left.size, right.size)
    if length == 0:
        return 0.0

    left_slice = left[:length]
    right_slice = right[:length]
    denominator = np.linalg.norm(left_slice) * np.linalg.norm(right_slice)
    if denominator == 0:
        return 0.0

    return float(np.dot(left_slice, right_slice) / denominator)


def _cross_correlate(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    output_length = left.size + right.size - 1
    fft_size = 1 << (output_length - 1).bit_length()
    correlation = np.fft.irfft(
        np.fft.rfft(left, fft_size) * np.fft.rfft(right[::-1], fft_size),
        fft_size,
    )
    return correlation[:output_length]


def _find_correlation_peak(
    correlation: np.ndarray,
    right_length: int,
    lag_min_samples: int,
    lag_max_samples: int,
    exclusion_radius_samples: int,
) -> tuple[float, float, float | None, float | None]:
    start_index = max(0, lag_min_samples + right_length - 1)
    end_index = min(correlation.size - 1, lag_max_samples + right_length - 1)
    if end_index < start_index:
        raise ValueError("Invalid lag search range.")

    window = correlation[start_index : end_index + 1]
    best_relative_index = int(np.argmax(window))
    best_index = start_index + best_relative_index
    peak = float(correlation[best_index])

    if 0 < best_index < correlation.size - 1:
        y0 = correlation[best_index - 1]
        y1 = correlation[best_index]
        y2 = correlation[best_index + 1]
        denominator = y0 - 2 * y1 + y2
        interpolation = 0.0 if abs(denominator) < 1e-12 else float(0.5 * (y0 - y2) / denominator)
    else:
        interpolation = 0.0

    exclusion_start = max(start_index, best_index - exclusion_radius_samples)
    exclusion_end = min(end_index, best_index + exclusion_radius_samples)

    second_peak: float | None = None
    if exclusion_start > start_index:
        second_peak = float(np.max(correlation[start_index:exclusion_start]))
    if exclusion_end < end_index:
        candidate = float(np.max(correlation[exclusion_end + 1 : end_index + 1]))
        second_peak = candidate if second_peak is None else max(second_peak, candidate)

    lag_samples = (best_index + interpolation) - (right_length - 1)
    peak_ratio = peak / second_peak if second_peak and second_peak > 0 else None
    return lag_samples, peak, second_peak, peak_ratio


def _top_correlation_candidates(
    correlation: np.ndarray,
    right_length: int,
    lag_min_samples: int,
    lag_max_samples: int,
    exclusion_radius_samples: int,
    count: int,
) -> list[tuple[float, float, float | None, float | None]]:
    start_index = max(0, lag_min_samples + right_length - 1)
    end_index = min(correlation.size - 1, lag_max_samples + right_length - 1)
    if end_index < start_index:
        return []

    window = correlation[start_index : end_index + 1].copy()
    candidates: list[tuple[float, float, float | None, float | None]] = []
    for _ in range(count):
        if window.size == 0 or not np.isfinite(window).any():
            break

        relative_index = int(np.argmax(window))
        peak = float(window[relative_index])
        if not np.isfinite(peak):
            break

        best_index = start_index + relative_index
        lag_samples = float(best_index - (right_length - 1))

        exclusion_start = max(0, relative_index - exclusion_radius_samples)
        exclusion_end = min(window.size - 1, relative_index + exclusion_radius_samples)
        second_peak: float | None = None
        if exclusion_start > 0:
            second_peak = float(np.max(window[:exclusion_start]))
        if exclusion_end < window.size - 1:
            candidate = float(np.max(window[exclusion_end + 1 :]))
            second_peak = candidate if second_peak is None else max(second_peak, candidate)

        peak_ratio = peak / second_peak if second_peak and second_peak > 0 else None
        candidates.append((lag_samples, peak, second_peak, peak_ratio))
        window[exclusion_start : exclusion_end + 1] = -np.inf

    return candidates


def _fit_weighted_line(points: list[tuple[float, float, float]]) -> tuple[float, float]:
    if not points:
        raise ValueError("At least one point is required for weighted fitting.")

    xs = np.array([point[0] for point in points], dtype=np.float64)
    ys = np.array([point[1] for point in points], dtype=np.float64)
    ws = np.array([max(point[2], 1e-6) for point in points], dtype=np.float64)

    sw = float(ws.sum())
    sx = float(np.sum(ws * xs))
    sy = float(np.sum(ws * ys))
    sxx = float(np.sum(ws * xs * xs))
    sxy = float(np.sum(ws * xs * ys))
    denominator = sw * sxx - sx * sx

    if abs(denominator) < 1e-12:
        return 0.0, sy / sw

    slope = (sw * sxy - sx * sy) / denominator
    intercept = (sy - slope * sx) / sw
    return float(slope), float(intercept)


def _build_activity_windows(duration_seconds: float, window_seconds: float) -> list[float]:
    max_start = max(duration_seconds - window_seconds, 0.0)
    if max_start == 0:
        return [0.0]
    fractions = [0.0, 0.25, 0.5, 0.75]
    return [max_start * fraction for fraction in fractions]


def _inspect_audio_stream(media: MediaInfo, stream: AudioStreamInfo, options: SyncOptions) -> tuple[StreamInspection, np.ndarray]:
    duration_seconds = stream.duration_seconds or _require_duration(media, media.path)
    loudest_rms = 0.0
    loudest_window_start = 0.0
    representative = np.empty(0, dtype=np.float32)

    for start_seconds in _build_activity_windows(duration_seconds, options.activity_window_seconds):
        samples = decode_audio(
            media.path,
            map_specifier=stream.map_specifier,
            start_seconds=start_seconds,
            duration_seconds=options.activity_window_seconds,
            sample_rate=options.activity_rate,
            filters=_analysis_filters(),
        )
        sample_rms = _rms(samples)
        if sample_rms > loudest_rms:
            loudest_rms = sample_rms
            loudest_window_start = start_seconds
            representative = samples

    inspection = StreamInspection(
        map_specifier=stream.map_specifier,
        absolute_stream_index=stream.absolute_stream_index,
        codec_name=stream.codec_name,
        sample_rate=stream.sample_rate,
        channels=stream.channels,
        loudest_rms=float(loudest_rms),
        loudest_window_start_seconds=float(loudest_window_start),
        active=loudest_rms > SILENCE_RMS_THRESHOLD,
        duplicate_of=None,
        duplicate_similarity=None,
    )
    return inspection, representative


def _inspect_camera_streams(media: MediaInfo, options: SyncOptions) -> list[StreamInspection]:
    contexts = [_inspect_audio_stream(media, stream, options) for stream in media.audio_streams]

    for left_index, (left_inspection, left_window) in enumerate(contexts):
        if not left_inspection.active or left_inspection.duplicate_of:
            continue

        normalized_left = _normalize_series(left_window)
        for right_index in range(left_index + 1, len(contexts)):
            right_inspection, right_window = contexts[right_index]
            if not right_inspection.active or right_inspection.duplicate_of:
                continue

            similarity = _cosine_similarity(normalized_left, _normalize_series(right_window))
            if similarity >= DUPLICATE_SIMILARITY_THRESHOLD:
                right_inspection.duplicate_of = left_inspection.map_specifier
                right_inspection.duplicate_similarity = similarity

    return [inspection for inspection, _window in contexts]


def _measure_coarse_offset(
    master_path: str,
    master_duration_seconds: float,
    camera_path: str,
    camera_duration_seconds: float,
    stream: StreamInspection,
    options: SyncOptions,
) -> CoarseSyncMeasurement:
    def bounded_search() -> tuple[float, float, float | None, float | None] | None:
        probe_seconds = min(300.0, camera_duration_seconds)
        search_max_seconds = max(0.0, master_duration_seconds - camera_duration_seconds + ALLOWED_MASTER_END_OVERHANG_SECONDS)
        probe_starts = sorted(
            {
                0.0,
                min(stream.loudest_window_start_seconds, max(camera_duration_seconds - probe_seconds, 0.0)),
            }
        )

        best_candidate: tuple[float, float, float | None, float | None] | None = None
        for probe_start_seconds in probe_starts:
            master_excerpt_duration = min(
                master_duration_seconds,
                search_max_seconds + probe_start_seconds + probe_seconds + 5.0,
            )
            master_excerpt = decode_audio(
                master_path,
                duration_seconds=master_excerpt_duration,
                sample_rate=options.coarse_rate,
                filters=_analysis_filters(),
            )
            camera_excerpt = decode_audio(
                camera_path,
                map_specifier=stream.map_specifier,
                start_seconds=probe_start_seconds,
                duration_seconds=probe_seconds,
                sample_rate=options.coarse_rate,
                filters=_analysis_filters(),
            )

            if master_excerpt.size == 0 or camera_excerpt.size == 0:
                continue

            correlation = _cross_correlate(_normalize_series(master_excerpt), _normalize_series(camera_excerpt))
            lag_samples, peak, second_peak, peak_ratio = _find_correlation_peak(
                correlation,
                camera_excerpt.size,
                round(probe_start_seconds * options.coarse_rate),
                round((probe_start_seconds + search_max_seconds) * options.coarse_rate),
                max(1, round(options.coarse_rate)),
            )

            camera_start_seconds = lag_samples / options.coarse_rate - probe_start_seconds
            if camera_start_seconds < -1.0:
                continue
            if camera_start_seconds + camera_duration_seconds > master_duration_seconds + ALLOWED_MASTER_END_OVERHANG_SECONDS:
                continue

            candidate = (camera_start_seconds, peak, second_peak, peak_ratio)
            if best_candidate is None or (peak_ratio or 0.0) > (best_candidate[3] or 0.0):
                best_candidate = candidate

        return best_candidate

    def broad_cluster_search() -> tuple[float, float, float | None, float | None] | None:
        probe_seconds = min(180.0, camera_duration_seconds)
        max_start = max(camera_duration_seconds - probe_seconds, 0.0)
        probe_starts = sorted(
            {
                0.0,
                min(stream.loudest_window_start_seconds, max_start),
                max_start * 0.25,
                max_start * 0.5,
                max_start * 0.75,
            }
        )

        master_reference = decode_audio(
            master_path,
            sample_rate=options.coarse_rate,
            filters=_analysis_filters(),
        )
        if master_reference.size == 0:
            return None
        master_reference = _normalize_series(master_reference)

        clusters: list[dict[str, Any]] = []
        min_overlap_seconds = max(options.anchor_window_seconds * 2, 120.0)
        for probe_index, probe_start_seconds in enumerate(probe_starts):
            camera_excerpt = decode_audio(
                camera_path,
                map_specifier=stream.map_specifier,
                start_seconds=probe_start_seconds,
                duration_seconds=probe_seconds,
                sample_rate=options.coarse_rate,
                filters=_analysis_filters(),
            )
            if camera_excerpt.size == 0:
                continue

            correlation = _cross_correlate(master_reference, _normalize_series(camera_excerpt))
            candidates = _top_correlation_candidates(
                correlation,
                camera_excerpt.size,
                0,
                master_reference.size - 1,
                max(1, round(options.coarse_rate)),
                6,
            )

            for lag_samples, peak, second_peak, peak_ratio in candidates:
                camera_start_seconds = lag_samples / options.coarse_rate - probe_start_seconds
                overlap_start_seconds = max(0.0, camera_start_seconds)
                overlap_end_seconds = min(master_duration_seconds, camera_start_seconds + camera_duration_seconds)
                if overlap_end_seconds - overlap_start_seconds < min_overlap_seconds:
                    continue

                score = (peak_ratio or 1.0) * max(peak, 1.0)
                cluster = next(
                    (
                        item
                        for item in clusters
                        if abs(item["camera_start_seconds"] - camera_start_seconds) <= 3.0
                    ),
                    None,
                )
                if cluster is None:
                    cluster = {
                        "camera_start_seconds": camera_start_seconds,
                        "weighted_sum": camera_start_seconds * score,
                        "score_sum": score,
                        "probe_indices": {probe_index},
                        "best_peak": peak,
                        "best_second_peak": second_peak,
                        "best_peak_ratio": peak_ratio,
                    }
                    clusters.append(cluster)
                else:
                    cluster["weighted_sum"] += camera_start_seconds * score
                    cluster["score_sum"] += score
                    cluster["probe_indices"].add(probe_index)
                    cluster["camera_start_seconds"] = cluster["weighted_sum"] / cluster["score_sum"]
                    if (peak_ratio or 0.0) > (cluster["best_peak_ratio"] or 0.0):
                        cluster["best_peak"] = peak
                        cluster["best_second_peak"] = second_peak
                        cluster["best_peak_ratio"] = peak_ratio

        if not clusters:
            return None

        best_cluster = max(
            clusters,
            key=lambda item: (len(item["probe_indices"]), item["score_sum"], item["best_peak_ratio"] or 0.0),
        )
        return (
            float(best_cluster["camera_start_seconds"]),
            float(best_cluster["best_peak"]),
            None if best_cluster["best_second_peak"] is None else float(best_cluster["best_second_peak"]),
            None if best_cluster["best_peak_ratio"] is None else float(best_cluster["best_peak_ratio"]),
        )

    best_candidate = bounded_search()
    method = "bounded_direct"
    if best_candidate is None:
        best_candidate = broad_cluster_search()
        method = "broad_cluster"

    if best_candidate is None:
        raise ValueError(f"Unable to derive a coarse sync candidate for stream {stream.map_specifier}.")

    return CoarseSyncMeasurement(
        map_specifier=stream.map_specifier,
        method=method,
        camera_starts_at_master_seconds=float(best_candidate[0]),
        master_to_source_offset_seconds=float(-best_candidate[0]),
        peak=float(best_candidate[1]),
        second_peak=None if best_candidate[2] is None else float(best_candidate[2]),
        peak_ratio=None if best_candidate[3] is None else float(best_candidate[3]),
    )


def _linspace(count: int, start: float, end: float) -> list[float]:
    if count <= 1:
        return [start]
    return [float(value) for value in np.linspace(start, end, num=count)]


def _build_anchor_reference_times(
    overlap_start_seconds: float,
    overlap_end_seconds: float,
    window_seconds: float,
    anchor_count: int,
) -> list[float]:
    usable_start = overlap_start_seconds + window_seconds / 2
    usable_end = overlap_end_seconds - window_seconds / 2

    if usable_end <= usable_start:
        return [max(overlap_start_seconds, overlap_end_seconds) / 2]

    return _linspace(anchor_count, usable_start, usable_end)


def _refine_anchors(
    master: MediaInfo,
    camera: MediaInfo,
    stream: StreamInspection,
    coarse: CoarseSyncMeasurement,
    options: SyncOptions,
) -> list[AnchorMeasurement]:
    master_duration = _require_duration(master, "Master")
    camera_duration = _require_duration(camera, "Camera")
    overlap_start = max(coarse.camera_starts_at_master_seconds, 0.0)
    overlap_end = min(master_duration, coarse.camera_starts_at_master_seconds + camera_duration)

    if overlap_end - overlap_start <= options.anchor_window_seconds / 2:
        raise ValueError("Not enough overlap to compute anchor measurements.")

    measurements: list[AnchorMeasurement] = []
    for reference_seconds in _build_anchor_reference_times(
        overlap_start,
        overlap_end,
        options.anchor_window_seconds,
        options.anchor_count,
    ):
        master_window_start = max(0.0, reference_seconds - options.anchor_window_seconds / 2)
        expected_source_start = master_window_start - coarse.camera_starts_at_master_seconds
        camera_window_start = max(0.0, expected_source_start - options.anchor_search_seconds)
        expected_lag_seconds = camera_window_start - expected_source_start

        master_window = decode_audio(
            master.path,
            start_seconds=master_window_start,
            duration_seconds=options.anchor_window_seconds,
            sample_rate=options.fine_rate,
            filters=_analysis_filters(),
        )
        camera_window = decode_audio(
            camera.path,
            map_specifier=stream.map_specifier,
            start_seconds=camera_window_start,
            duration_seconds=options.anchor_window_seconds + 2 * options.anchor_search_seconds,
            sample_rate=options.fine_rate,
            filters=_analysis_filters(),
        )

        if master_window.size == 0 or camera_window.size == 0:
            continue

        correlation = _cross_correlate(_normalize_series(master_window), _normalize_series(camera_window))
        lag_samples, peak, second_peak, peak_ratio = _find_correlation_peak(
            correlation,
            camera_window.size,
            round((expected_lag_seconds - options.anchor_search_seconds) * options.fine_rate),
            round((expected_lag_seconds + options.anchor_search_seconds) * options.fine_rate),
            max(1, round(0.2 * options.fine_rate)),
        )

        lag_seconds = lag_samples / options.fine_rate
        source_minus_master = camera_window_start - master_window_start - lag_seconds
        measurements.append(
            AnchorMeasurement(
                master_reference_seconds=float(reference_seconds),
                source_minus_master_seconds=float(source_minus_master),
                lag_seconds=float(lag_seconds),
                peak=float(peak),
                second_peak=None if second_peak is None else float(second_peak),
                peak_ratio=None if peak_ratio is None else float(peak_ratio),
                accepted=(peak_ratio or 0.0) >= 1.05,
            )
        )

    return measurements


def _summarize_confidence(anchors: list[AnchorMeasurement], predicted_drift_over_hour_seconds: float) -> str:
    accepted = [anchor for anchor in anchors if anchor.accepted]
    if not accepted:
        return "low"

    mean_peak_ratio = sum((anchor.peak_ratio or 1.0) for anchor in accepted) / len(accepted)
    absolute_drift = abs(predicted_drift_over_hour_seconds)

    if len(accepted) >= max(4, round(len(anchors) * 0.6)) and mean_peak_ratio >= 1.15 and absolute_drift <= 0.5:
        return "high"
    if len(accepted) >= 2 and mean_peak_ratio >= 1.05:
        return "medium"
    return "low"


def _resolve_requested_stream(requested_stream: str, streams: list[StreamInspection]) -> list[StreamInspection]:
    matches = [
        stream
        for stream in streams
        if stream.map_specifier == requested_stream or str(stream.absolute_stream_index) == requested_stream
    ]
    if not matches:
        raise ValueError(f"Requested stream {requested_stream} was not found.")
    return matches


def analyze_sync(
    master_path: str,
    camera_path: str,
    *,
    requested_stream: str | None = None,
    options: SyncOptions | None = None,
) -> dict[str, Any]:
    options = options or SyncOptions()
    master = probe_media(master_path)
    camera = probe_media(camera_path)

    master_duration = _require_duration(master, "Master")
    camera_duration = _require_duration(camera, "Camera")

    if not camera.audio_streams:
        raise ValueError("Camera file does not contain audio streams for sync.")

    inspected_streams = _inspect_camera_streams(camera, options)
    eligible_streams = [stream for stream in inspected_streams if stream.active and not stream.duplicate_of]
    if requested_stream is not None:
        eligible_streams = _resolve_requested_stream(requested_stream, inspected_streams)

    if not eligible_streams:
        raise ValueError("No active camera audio stream was found for sync.")

    best_stream: StreamInspection | None = None
    best_coarse: CoarseSyncMeasurement | None = None
    for stream in eligible_streams:
        coarse = _measure_coarse_offset(
            master.path,
            master_duration,
            camera.path,
            camera_duration,
            stream,
            options,
        )
        if best_coarse is None or (coarse.peak_ratio or 0.0) > (best_coarse.peak_ratio or 0.0):
            best_stream = stream
            best_coarse = coarse

    if best_stream is None or best_coarse is None:
        raise ValueError("Unable to compute a coarse sync offset.")

    anchors = _refine_anchors(master, camera, best_stream, best_coarse, options)
    accepted_anchors = [anchor for anchor in anchors if anchor.accepted]
    fit_source = accepted_anchors or anchors

    slope, intercept = _fit_weighted_line(
        [
            (anchor.master_reference_seconds, anchor.source_minus_master_seconds, anchor.peak_ratio or 1.0)
            for anchor in fit_source
        ]
    )

    speed = 1.0 + slope
    offset_seconds = intercept
    camera_starts_at_master_seconds = -offset_seconds / speed
    predicted_drift_over_hour_seconds = slope * 3600.0
    confidence = _summarize_confidence(anchors, predicted_drift_over_hour_seconds)

    notes = [
        "Master audio is the canonical project timeline.",
        "Camera audio is used only for sync and should not be treated as the final mix.",
        f"Selected stream {best_stream.map_specifier} from {len(camera.audio_streams)} available camera audio streams.",
    ]
    if any(stream.duplicate_of for stream in inspected_streams):
        notes.append("Duplicate scratch tracks were detected and ignored for auto-selection.")

    report = SyncProbeReport(
        master=MediaSummary(
            path=master.path,
            duration_seconds=master_duration,
            format_name=master.format_name,
        ),
        camera=CameraSummary(
            path=camera.path,
            duration_seconds=camera_duration,
            format_name=camera.format_name,
            streams=inspected_streams,
            selected_stream=best_stream,
        ),
        coarse=best_coarse,
        anchors={
            "measurements": anchors,
            "accepted": accepted_anchors,
        },
        mapping=SyncMapping(
            speed=float(speed),
            offset_seconds=float(offset_seconds),
            camera_starts_at_master_seconds=float(camera_starts_at_master_seconds),
            predicted_drift_over_hour_seconds=float(predicted_drift_over_hour_seconds),
            model="source_time = speed * master_time + offset_seconds",
        ),
        summary=SyncSummary(
            confidence=confidence,
            notes=notes,
        ),
    )
    return asdict(report)
