from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .fftools import AudioStreamInfo, MediaInfo, decode_audio, probe_media

SILENCE_RMS_THRESHOLD = 5e-4
DUPLICATE_SIMILARITY_THRESHOLD = 0.995
ALLOWED_MASTER_END_OVERHANG_SECONDS = 60.0
MAX_ACCEPTED_ANCHOR_RESIDUAL_RMSE_SECONDS = 0.2
MAX_ACCEPTED_ANCHOR_RESIDUAL_MAX_SECONDS = 0.35
MAX_ACCEPTED_ANCHOR_OFFSET_RANGE_SECONDS = 0.5


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
    coarse_candidate_limit: int = 6
    anchor_activity_step_seconds: float = 10.0
    anchor_min_spacing_seconds: float = 30.0


@dataclass(slots=True)
class AnchorStrategy:
    overlap_duration_seconds: float
    window_seconds: float
    min_spacing_seconds: float
    activity_step_seconds: float
    target_count: int


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
    validated: bool
    errors: list[str]
    diagnostics: dict[str, float | int | None]
    notes: list[str]


@dataclass(slots=True)
class SyncProbeReport:
    master: MediaSummary
    camera: CameraSummary
    coarse: CoarseSyncMeasurement
    anchors: dict[str, list[AnchorMeasurement]]
    mapping: SyncMapping
    summary: SyncSummary


@dataclass(slots=True)
class CandidateEvaluation:
    coarse: CoarseSyncMeasurement
    anchors: list[AnchorMeasurement]
    accepted_anchors: list[AnchorMeasurement]
    mapping: SyncMapping
    confidence: str
    diagnostics: dict[str, float | int | None]
    errors: list[str]


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


def _cross_correlate_gcc_phat(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    output_length = left.size + right.size - 1
    fft_size = 1 << (output_length - 1).bit_length()
    left_fft = np.fft.rfft(left, fft_size)
    right_fft = np.fft.rfft(right, fft_size)
    cross_power = left_fft * np.conj(right_fft)
    cross_power /= np.maximum(np.abs(cross_power), 1e-12)
    circular = np.fft.irfft(cross_power, fft_size)
    tail = circular[-(right.size - 1) :] if right.size > 1 else np.empty(0, dtype=np.float64)
    head = circular[: left.size]
    correlation = np.concatenate((tail, head))
    return correlation[:output_length]


def _build_energy_envelope(
    samples: np.ndarray,
    sample_rate: int,
    envelope_bin_seconds: float,
) -> tuple[np.ndarray, float]:
    if samples.size == 0:
        return samples.astype(np.float64), float(sample_rate)

    bin_size = max(1, round(sample_rate * envelope_bin_seconds))
    usable_length = max(bin_size, samples.size // bin_size * bin_size)
    padded = samples[:usable_length]
    if padded.size < bin_size:
        padded = np.pad(padded, (0, bin_size - padded.size))

    frames = padded.reshape(-1, bin_size).astype(np.float64)
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    onset = np.abs(np.diff(rms, prepend=rms[0]))
    envelope = 0.7 * _normalize_series(rms) + 0.3 * _normalize_series(onset)
    return envelope.astype(np.float64), float(sample_rate / bin_size)


def _hybrid_correlation(
    left: np.ndarray,
    right: np.ndarray,
) -> np.ndarray:
    normalized_left = _normalize_series(left)
    normalized_right = _normalize_series(right)
    raw = _normalize_series(_cross_correlate(normalized_left, normalized_right))
    phat = _normalize_series(_cross_correlate_gcc_phat(normalized_left, normalized_right))
    return 0.45 * raw + 0.55 * phat


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


def _coarse_candidate_sort_key(candidate: CoarseSyncMeasurement) -> tuple[float, float]:
    return (
        float(candidate.peak_ratio or 0.0),
        float(candidate.peak),
    )


def _dedupe_coarse_candidates(
    candidates: list[CoarseSyncMeasurement],
    *,
    tolerance_seconds: float,
    limit: int,
) -> list[CoarseSyncMeasurement]:
    ordered = sorted(candidates, key=_coarse_candidate_sort_key, reverse=True)
    unique: list[CoarseSyncMeasurement] = []
    for candidate in ordered:
        if any(
            abs(candidate.camera_starts_at_master_seconds - existing.camera_starts_at_master_seconds)
            <= tolerance_seconds
            for existing in unique
        ):
            continue
        unique.append(candidate)
        if len(unique) >= limit:
            break
    return unique


def _generate_coarse_candidates(
    master_path: str,
    master_duration_seconds: float,
    camera_path: str,
    camera_duration_seconds: float,
    stream: StreamInspection,
    options: SyncOptions,
) -> list[CoarseSyncMeasurement]:
    def bounded_search() -> list[CoarseSyncMeasurement]:
        probe_seconds = min(300.0, camera_duration_seconds)
        search_max_seconds = max(0.0, master_duration_seconds - camera_duration_seconds + ALLOWED_MASTER_END_OVERHANG_SECONDS)
        probe_starts = sorted(
            {
                0.0,
                min(stream.loudest_window_start_seconds, max(camera_duration_seconds - probe_seconds, 0.0)),
            }
        )

        bounded_candidates: list[CoarseSyncMeasurement] = []
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

            lag_min_samples = round(probe_start_seconds * options.coarse_rate)
            lag_max_samples = round((probe_start_seconds + search_max_seconds) * options.coarse_rate)
            waveform_candidates = _top_correlation_candidates(
                _hybrid_correlation(master_excerpt, camera_excerpt),
                camera_excerpt.size,
                lag_min_samples,
                lag_max_samples,
                max(1, round(options.coarse_rate)),
                options.coarse_candidate_limit,
            )
            envelope_master, envelope_rate = _build_energy_envelope(
                master_excerpt,
                options.coarse_rate,
                options.envelope_bin_seconds,
            )
            envelope_camera, _camera_envelope_rate = _build_energy_envelope(
                camera_excerpt,
                options.coarse_rate,
                options.envelope_bin_seconds,
            )
            envelope_candidates = _top_correlation_candidates(
                _cross_correlate(_normalize_series(envelope_master), _normalize_series(envelope_camera)),
                envelope_camera.size,
                round(probe_start_seconds * envelope_rate),
                round((probe_start_seconds + search_max_seconds) * envelope_rate),
                max(1, round(envelope_rate)),
                max(2, options.coarse_candidate_limit // 2),
            )

            for method_name, candidate_pack, sample_rate in (
                ("bounded_direct", waveform_candidates, float(options.coarse_rate)),
                ("bounded_direct_envelope", envelope_candidates, float(envelope_rate)),
            ):
                for lag_samples, peak, second_peak, peak_ratio in candidate_pack:
                    camera_start_seconds = lag_samples / sample_rate - probe_start_seconds
                    if camera_start_seconds < -1.0:
                        continue
                    if camera_start_seconds + camera_duration_seconds > master_duration_seconds + ALLOWED_MASTER_END_OVERHANG_SECONDS:
                        continue
                    bounded_candidates.append(
                        CoarseSyncMeasurement(
                            map_specifier=stream.map_specifier,
                            method=method_name,
                            camera_starts_at_master_seconds=float(camera_start_seconds),
                            master_to_source_offset_seconds=float(-camera_start_seconds),
                            peak=float(peak),
                            second_peak=None if second_peak is None else float(second_peak),
                            peak_ratio=None if peak_ratio is None else float(peak_ratio),
                        )
                    )

        return _dedupe_coarse_candidates(
            bounded_candidates,
            tolerance_seconds=2.0,
            limit=options.coarse_candidate_limit,
        )

    def broad_cluster_search() -> list[CoarseSyncMeasurement]:
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
            return []

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

            correlation = _hybrid_correlation(master_reference, camera_excerpt)
            candidates = _top_correlation_candidates(
                correlation,
                camera_excerpt.size,
                0,
                master_reference.size - 1,
                max(1, round(options.coarse_rate)),
                options.coarse_candidate_limit,
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
            return []

        ordered_clusters = sorted(
            clusters,
            key=lambda item: (len(item["probe_indices"]), item["score_sum"], item["best_peak_ratio"] or 0.0),
            reverse=True,
        )
        return _dedupe_coarse_candidates(
            [
                CoarseSyncMeasurement(
                    map_specifier=stream.map_specifier,
                    method="broad_cluster",
                    camera_starts_at_master_seconds=float(cluster["camera_start_seconds"]),
                    master_to_source_offset_seconds=float(-cluster["camera_start_seconds"]),
                    peak=float(cluster["best_peak"]),
                    second_peak=None if cluster["best_second_peak"] is None else float(cluster["best_second_peak"]),
                    peak_ratio=None if cluster["best_peak_ratio"] is None else float(cluster["best_peak_ratio"]),
                )
                for cluster in ordered_clusters
            ],
            tolerance_seconds=2.0,
            limit=options.coarse_candidate_limit,
        )

    candidates = bounded_search()
    candidates.extend(broad_cluster_search())
    candidates = _dedupe_coarse_candidates(
        candidates,
        tolerance_seconds=2.0,
        limit=options.coarse_candidate_limit,
    )
    if not candidates:
        raise ValueError(f"Unable to derive a coarse sync candidate for stream {stream.map_specifier}.")
    return candidates


def _measure_coarse_offset(
    master_path: str,
    master_duration_seconds: float,
    camera_path: str,
    camera_duration_seconds: float,
    stream: StreamInspection,
    options: SyncOptions,
) -> CoarseSyncMeasurement:
    return _generate_coarse_candidates(
        master_path,
        master_duration_seconds,
        camera_path,
        camera_duration_seconds,
        stream,
        options,
    )[0]


def _linspace(count: int, start: float, end: float) -> list[float]:
    if count <= 1:
        return [start]
    return [float(value) for value in np.linspace(start, end, num=count)]


def _fallback_anchor_reference_times(
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


def _build_anchor_reference_times(
    master_activity_samples: np.ndarray | None,
    activity_rate: int,
    overlap_start_seconds: float,
    overlap_end_seconds: float,
    window_seconds: float,
    min_spacing_seconds: float,
    step_seconds: float,
    anchor_count: int,
) -> list[float]:
    fallback = _fallback_anchor_reference_times(
        overlap_start_seconds,
        overlap_end_seconds,
        window_seconds,
        anchor_count,
    )
    if master_activity_samples is None or master_activity_samples.size == 0:
        return fallback

    usable_start = overlap_start_seconds + window_seconds / 2
    usable_end = overlap_end_seconds - window_seconds / 2
    if usable_end <= usable_start:
        return fallback

    window_sample_count = max(1, round(window_seconds * activity_rate))
    step_sample_count = max(1, round(step_seconds * activity_rate))

    candidates: list[tuple[float, float]] = []
    start_index = round(usable_start * activity_rate)
    end_index = round(usable_end * activity_rate)
    for center_index in range(start_index, end_index + 1, step_sample_count):
        window_start_index = max(0, center_index - window_sample_count // 2)
        window_end_index = min(master_activity_samples.size, window_start_index + window_sample_count)
        segment = master_activity_samples[window_start_index:window_end_index]
        if segment.size < max(32, window_sample_count // 4):
            continue

        energy = _rms(segment)
        onset = float(np.mean(np.abs(np.diff(segment.astype(np.float64))))) if segment.size > 1 else 0.0
        center_seconds = center_index / activity_rate
        candidates.append((energy + 0.8 * onset, center_seconds))

    if not candidates:
        return fallback

    selected: list[float] = []
    for _score, center_seconds in sorted(candidates, key=lambda item: item[0], reverse=True):
        if any(abs(center_seconds - existing) < min_spacing_seconds for existing in selected):
            continue
        selected.append(center_seconds)
        if len(selected) >= anchor_count:
            break

    if len(selected) < anchor_count:
        for center_seconds in fallback:
            if any(abs(center_seconds - existing) < min_spacing_seconds * 0.5 for existing in selected):
                continue
            selected.append(center_seconds)
            if len(selected) >= anchor_count:
                break

    return sorted(selected[:anchor_count]) if selected else fallback


def _resolve_anchor_strategy(overlap_duration_seconds: float, options: SyncOptions) -> AnchorStrategy:
    window_seconds = min(
        options.anchor_window_seconds,
        max(12.0, overlap_duration_seconds / 6.0),
    )
    min_spacing_seconds = min(
        options.anchor_min_spacing_seconds,
        max(6.0, window_seconds * 0.6),
    )
    activity_step_seconds = min(
        options.anchor_activity_step_seconds,
        max(2.0, window_seconds * 0.5),
    )
    usable_duration_seconds = max(0.0, overlap_duration_seconds - window_seconds)
    target_count = max(
        1,
        min(
            options.anchor_count,
            int(usable_duration_seconds / max(min_spacing_seconds, window_seconds * 0.8)) + 1,
        ),
    )
    return AnchorStrategy(
        overlap_duration_seconds=float(overlap_duration_seconds),
        window_seconds=float(window_seconds),
        min_spacing_seconds=float(min_spacing_seconds),
        activity_step_seconds=float(activity_step_seconds),
        target_count=int(target_count),
    )


def _refine_anchors(
    master: MediaInfo,
    camera: MediaInfo,
    stream: StreamInspection,
    coarse: CoarseSyncMeasurement,
    options: SyncOptions,
    *,
    master_activity_samples: np.ndarray | None = None,
) -> tuple[list[AnchorMeasurement], AnchorStrategy]:
    master_duration = _require_duration(master, "Master")
    camera_duration = _require_duration(camera, "Camera")
    overlap_start = max(coarse.camera_starts_at_master_seconds, 0.0)
    overlap_end = min(master_duration, coarse.camera_starts_at_master_seconds + camera_duration)
    overlap_duration_seconds = overlap_end - overlap_start
    strategy = _resolve_anchor_strategy(overlap_duration_seconds, options)

    if overlap_duration_seconds <= max(6.0, strategy.window_seconds / 2):
        raise ValueError("Not enough overlap to compute anchor measurements.")

    measurements: list[AnchorMeasurement] = []
    for reference_seconds in _build_anchor_reference_times(
        master_activity_samples,
        options.activity_rate,
        overlap_start,
        overlap_end,
        strategy.window_seconds,
        strategy.min_spacing_seconds,
        strategy.activity_step_seconds,
        strategy.target_count,
    ):
        master_window_start = max(0.0, reference_seconds - strategy.window_seconds / 2)
        expected_source_start = master_window_start - coarse.camera_starts_at_master_seconds
        camera_window_start = max(0.0, expected_source_start - options.anchor_search_seconds)
        expected_lag_seconds = camera_window_start - expected_source_start

        master_window = decode_audio(
            master.path,
            start_seconds=master_window_start,
            duration_seconds=strategy.window_seconds,
            sample_rate=options.fine_rate,
            filters=_analysis_filters(),
        )
        camera_window = decode_audio(
            camera.path,
            map_specifier=stream.map_specifier,
            start_seconds=camera_window_start,
            duration_seconds=strategy.window_seconds + 2 * options.anchor_search_seconds,
            sample_rate=options.fine_rate,
            filters=_analysis_filters(),
        )

        if master_window.size == 0 or camera_window.size == 0:
            continue

        correlation = _hybrid_correlation(master_window, camera_window)
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

    return measurements, strategy


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


def _calculate_sync_diagnostics(
    anchors: list[AnchorMeasurement],
    fit_source: list[AnchorMeasurement],
    coarse_peak_ratio: float | None,
    speed: float,
    offset_seconds: float,
    strategy: AnchorStrategy,
) -> dict[str, float | int | None]:
    accepted = [anchor for anchor in anchors if anchor.accepted]
    reference_anchors = fit_source or anchors
    slope = speed - 1.0

    residuals: list[float] = []
    for anchor in reference_anchors:
        predicted = slope * anchor.master_reference_seconds + offset_seconds
        residuals.append(anchor.source_minus_master_seconds - predicted)

    residual_rmse_seconds = None
    residual_max_abs_seconds = None
    if residuals:
        squared = [residual * residual for residual in residuals]
        residual_rmse_seconds = float(np.sqrt(np.mean(np.array(squared, dtype=np.float64))))
        residual_max_abs_seconds = float(max(abs(residual) for residual in residuals))

    accepted_offsets = [anchor.source_minus_master_seconds for anchor in accepted]
    accepted_offset_range_seconds = None
    if accepted_offsets:
        accepted_offset_range_seconds = float(max(accepted_offsets) - min(accepted_offsets))

    mean_accepted_peak_ratio = None
    if accepted:
        mean_accepted_peak_ratio = float(
            sum((anchor.peak_ratio or 1.0) for anchor in accepted) / len(accepted)
        )

    return {
        "anchor_count": len(anchors),
        "anchor_target_count": strategy.target_count,
        "accepted_anchor_count": len(accepted),
        "accepted_anchor_ratio": None if not anchors else float(len(accepted) / len(anchors)),
        "coarse_peak_ratio": None if coarse_peak_ratio is None else float(coarse_peak_ratio),
        "mean_accepted_peak_ratio": mean_accepted_peak_ratio,
        "accepted_offset_range_seconds": accepted_offset_range_seconds,
        "residual_rmse_seconds": residual_rmse_seconds,
        "residual_max_abs_seconds": residual_max_abs_seconds,
        "overlap_duration_seconds": strategy.overlap_duration_seconds,
        "anchor_window_seconds_used": strategy.window_seconds,
        "anchor_min_spacing_seconds_used": strategy.min_spacing_seconds,
    }


def _single_anchor_short_clip_is_trustworthy(diagnostics: dict[str, float | int | None]) -> bool:
    accepted_anchor_count = int(diagnostics["accepted_anchor_count"] or 0)
    anchor_count = int(diagnostics["anchor_count"] or 0)
    overlap_duration_seconds = diagnostics["overlap_duration_seconds"]
    mean_accepted_peak_ratio = float(diagnostics["mean_accepted_peak_ratio"] or 0.0)
    coarse_peak_ratio = float(diagnostics["coarse_peak_ratio"] or 0.0)

    return (
        accepted_anchor_count == 1
        and anchor_count <= 2
        and isinstance(overlap_duration_seconds, float)
        and overlap_duration_seconds <= 300.0
        and mean_accepted_peak_ratio >= 1.3
        and coarse_peak_ratio >= 1.03
    )


def _validate_sync_diagnostics(diagnostics: dict[str, float | int | None]) -> list[str]:
    errors: list[str] = []
    accepted_anchor_count = int(diagnostics["accepted_anchor_count"] or 0)
    residual_rmse_seconds = diagnostics["residual_rmse_seconds"]
    residual_max_abs_seconds = diagnostics["residual_max_abs_seconds"]
    accepted_offset_range_seconds = diagnostics["accepted_offset_range_seconds"]

    if accepted_anchor_count == 0:
        errors.append("Sync rejected: no anchor windows passed the peak-ratio acceptance threshold.")
        return errors

    if accepted_anchor_count < 2 and not _single_anchor_short_clip_is_trustworthy(diagnostics):
        errors.append(
            "Sync rejected: fewer than 2 accepted anchor windows survived validation, so the mapping is unstable."
        )

    if (
        isinstance(residual_rmse_seconds, float)
        and residual_rmse_seconds > MAX_ACCEPTED_ANCHOR_RESIDUAL_RMSE_SECONDS
    ):
        errors.append(
            "Sync rejected: accepted anchors do not fit a stable line "
            f"(residual RMS {residual_rmse_seconds:.3f}s > {MAX_ACCEPTED_ANCHOR_RESIDUAL_RMSE_SECONDS:.3f}s)."
        )

    if (
        isinstance(residual_max_abs_seconds, float)
        and residual_max_abs_seconds > MAX_ACCEPTED_ANCHOR_RESIDUAL_MAX_SECONDS
    ):
        errors.append(
            "Sync rejected: at least one accepted anchor deviates too far from the fitted mapping "
            f"({residual_max_abs_seconds:.3f}s > {MAX_ACCEPTED_ANCHOR_RESIDUAL_MAX_SECONDS:.3f}s)."
        )

    if (
        isinstance(accepted_offset_range_seconds, float)
        and accepted_offset_range_seconds > MAX_ACCEPTED_ANCHOR_OFFSET_RANGE_SECONDS
    ):
        errors.append(
            "Sync rejected: accepted anchor offsets disagree too much across the clip "
            f"({accepted_offset_range_seconds:.3f}s > {MAX_ACCEPTED_ANCHOR_OFFSET_RANGE_SECONDS:.3f}s)."
        )

    return errors


def _evaluate_candidate(
    master: MediaInfo,
    camera: MediaInfo,
    stream: StreamInspection,
    coarse: CoarseSyncMeasurement,
    options: SyncOptions,
    *,
    master_activity_samples: np.ndarray | None = None,
) -> CandidateEvaluation:
    anchors, strategy = _refine_anchors(
        master,
        camera,
        stream,
        coarse,
        options,
        master_activity_samples=master_activity_samples,
    )
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
    mapping = SyncMapping(
        speed=float(speed),
        offset_seconds=float(offset_seconds),
        camera_starts_at_master_seconds=float(-offset_seconds / speed),
        predicted_drift_over_hour_seconds=float(slope * 3600.0),
        model="source_time = speed * master_time + offset_seconds",
    )
    confidence = _summarize_confidence(anchors, mapping.predicted_drift_over_hour_seconds)
    diagnostics = _calculate_sync_diagnostics(
        anchors,
        fit_source,
        coarse.peak_ratio,
        mapping.speed,
        mapping.offset_seconds,
        strategy,
    )
    errors = _validate_sync_diagnostics(diagnostics)
    return CandidateEvaluation(
        coarse=coarse,
        anchors=anchors,
        accepted_anchors=accepted_anchors,
        mapping=mapping,
        confidence=confidence,
        diagnostics=diagnostics,
        errors=errors,
    )


def _candidate_evaluation_sort_key(candidate: CandidateEvaluation) -> tuple[float, ...]:
    diagnostics = candidate.diagnostics
    residual_rmse_seconds = diagnostics["residual_rmse_seconds"]
    residual_max_abs_seconds = diagnostics["residual_max_abs_seconds"]
    accepted_offset_range_seconds = diagnostics["accepted_offset_range_seconds"]
    return (
        1.0 if not candidate.errors else 0.0,
        float(diagnostics["accepted_anchor_count"] or 0),
        -float(residual_rmse_seconds) if residual_rmse_seconds is not None else -999.0,
        -float(residual_max_abs_seconds) if residual_max_abs_seconds is not None else -999.0,
        -float(accepted_offset_range_seconds) if accepted_offset_range_seconds is not None else -999.0,
        float(diagnostics["mean_accepted_peak_ratio"] or 0.0),
        float(candidate.coarse.peak_ratio or 0.0),
        -abs(candidate.mapping.predicted_drift_over_hour_seconds),
    )


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

    master_activity_samples = decode_audio(
        master.path,
        sample_rate=options.activity_rate,
        filters=_analysis_filters(),
    )

    best_stream: StreamInspection | None = None
    best_candidate: CandidateEvaluation | None = None
    candidate_count_evaluated = 0
    validated_candidate_count = 0
    for stream in eligible_streams:
        coarse_candidates = _generate_coarse_candidates(
            master.path,
            master_duration,
            camera.path,
            camera_duration,
            stream,
            options,
        )
        for coarse_candidate in coarse_candidates:
            candidate = _evaluate_candidate(
                master,
                camera,
                stream,
                coarse_candidate,
                options,
                master_activity_samples=master_activity_samples,
            )
            candidate_count_evaluated += 1
            if not candidate.errors:
                validated_candidate_count += 1
            if best_candidate is None or _candidate_evaluation_sort_key(candidate) > _candidate_evaluation_sort_key(best_candidate):
                best_stream = stream
                best_candidate = candidate

    if best_stream is None or best_candidate is None:
        raise ValueError("Unable to compute a coarse sync offset.")
    best_coarse = best_candidate.coarse
    anchors = best_candidate.anchors
    accepted_anchors = best_candidate.accepted_anchors
    mapping = best_candidate.mapping
    confidence = best_candidate.confidence
    diagnostics = dict(best_candidate.diagnostics)
    diagnostics["candidate_count_evaluated"] = candidate_count_evaluated
    diagnostics["validated_candidate_count"] = validated_candidate_count
    errors = list(best_candidate.errors)

    notes = [
        "Master audio is the canonical project timeline.",
        "Camera audio is used only for sync and should not be treated as the final mix.",
        f"Selected stream {best_stream.map_specifier} from {len(camera.audio_streams)} available camera audio streams.",
        f"Evaluated {candidate_count_evaluated} coarse candidate mappings and selected {best_coarse.method}.",
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
        mapping=mapping,
        summary=SyncSummary(
            confidence=confidence,
            validated=not errors,
            errors=errors,
            diagnostics=diagnostics,
            notes=notes,
        ),
    )
    return asdict(report)
