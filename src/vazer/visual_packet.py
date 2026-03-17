from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import re
from pathlib import Path
from typing import Any

import cv2

from . import __version__
from .fftools import probe_media

EPSILON = 1e-6
ROLE_ORDER = {
    "close": 0,
    "halbtotale": 1,
    "totale": 2,
    "unknown": 3,
}


@dataclass(slots=True)
class VisualPacketOptions:
    mode: str = "auto"
    interval_seconds: float = 120.0
    window_context_seconds: float = 12.0
    transcript_context_seconds: float = 14.0
    image_width: int = 640
    image_quality: int = 88
    cut_context_seconds: float = 1.5
    max_windows: int | None = None
    role_overrides: dict[str, str] | None = None


@dataclass(slots=True)
class AssetCoverage:
    asset_id: str
    path: str
    speed: float
    offset_seconds: float
    duration_seconds: float
    overlap_start_seconds: float
    overlap_end_seconds: float
    confidence: str
    role: str


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_duration(path: str, existing_duration_seconds: float | None = None) -> float:
    if isinstance(existing_duration_seconds, (int, float)) and existing_duration_seconds > 0:
        return float(existing_duration_seconds)

    media_info = probe_media(path)
    if media_info.duration_seconds is None or media_info.duration_seconds <= 0:
        raise ValueError(f"Unable to determine duration for {path}.")
    return float(media_info.duration_seconds)


def _infer_camera_role(asset_id: str, path: str) -> str:
    name = f"{asset_id} {Path(path).stem}".lower()
    if "close" in name or "nah" in name:
        return "close"
    if "halbtotale" in name or "halb" in name or re.search(r"\bht\b", name):
        return "halbtotale"
    if "total" in name or "totale" in name or "wide" in name:
        return "totale"
    return "unknown"


def _normalized_role(value: str) -> str:
    lowered = value.strip().lower()
    if lowered in {"totale", "close", "halbtotale"}:
        return lowered
    return "unknown"


def _build_coverages(
    sync_map: dict[str, Any],
    role_overrides: dict[str, str] | None,
) -> tuple[dict[str, AssetCoverage], float]:
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
        path = entry.get("path")
        mapping = entry.get("mapping")
        if not isinstance(asset_id, str) or not isinstance(path, str) or not isinstance(mapping, dict):
            continue

        speed = float(mapping["speed"])
        offset_seconds = float(mapping["offset_seconds"])
        if speed <= 0:
            continue

        media_payload = entry.get("media")
        duration_seconds = None
        if isinstance(media_payload, dict):
            duration_seconds = media_payload.get("duration_seconds")
        duration_seconds = _require_duration(path, duration_seconds)

        overlap_start_seconds = max(0.0, -offset_seconds / speed)
        overlap_end_seconds = min(master_duration_seconds, (duration_seconds - offset_seconds) / speed)
        if overlap_end_seconds - overlap_start_seconds <= EPSILON:
            continue

        role = _normalized_role(role_overrides.get(asset_id, "")) if role_overrides else "unknown"
        if role == "unknown":
            role = _infer_camera_role(asset_id, path)

        coverages[asset_id] = AssetCoverage(
            asset_id=asset_id,
            path=path,
            speed=speed,
            offset_seconds=offset_seconds,
            duration_seconds=duration_seconds,
            overlap_start_seconds=overlap_start_seconds,
            overlap_end_seconds=overlap_end_seconds,
            confidence=str(entry.get("summary", {}).get("confidence") or "unknown"),
            role=role,
        )

    return coverages, master_duration_seconds


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
        windows = [window for window in entry.get("windows", []) if isinstance(window, dict)]
        windows.sort(key=lambda item: (float(item["master_start_seconds"]), float(item["master_end_seconds"])))
        windows_by_asset[asset_id] = windows
    return windows_by_asset


def _interval_overlap(
    start_seconds: float,
    end_seconds: float,
    interval_start_seconds: float,
    interval_end_seconds: float,
) -> float:
    return max(0.0, min(end_seconds, interval_end_seconds) - max(start_seconds, interval_start_seconds))


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
            "usable_score": None,
            "sharpness_score": None,
            "stability_score": None,
        }

    return {
        "has_analysis": True,
        "usable_score": float(usable_sum / total_weight),
        "sharpness_score": float(sharpness_sum / total_weight),
        "stability_score": float(stability_sum / total_weight),
    }


def _transcript_excerpt(
    transcript_artifact: dict[str, Any] | None,
    center_seconds: float,
    context_seconds: float,
) -> dict[str, Any]:
    if transcript_artifact is None:
        return {
            "text": None,
            "word_count": 0,
            "segment_count": 0,
        }

    start_seconds = max(0.0, center_seconds - context_seconds / 2.0)
    end_seconds = center_seconds + context_seconds / 2.0
    words = [
        word
        for word in transcript_artifact.get("words", [])
        if isinstance(word, dict)
        and float(word["start_seconds"]) <= end_seconds + EPSILON
        and float(word["end_seconds"]) >= start_seconds - EPSILON
    ]
    if words:
        excerpt = " ".join(str(word.get("text") or "").strip() for word in words if str(word.get("text") or "").strip()).strip()
        return {
            "text": excerpt or None,
            "word_count": len(words),
            "segment_count": 0,
        }

    segments = [
        segment
        for segment in transcript_artifact.get("segments", [])
        if isinstance(segment, dict)
        and float(segment["start_seconds"]) <= end_seconds + EPSILON
        and float(segment["end_seconds"]) >= start_seconds - EPSILON
    ]
    excerpt = " ".join(str(segment.get("text") or "").strip() for segment in segments if str(segment.get("text") or "").strip()).strip()
    return {
        "text": excerpt or None,
        "word_count": 0,
        "segment_count": len(segments),
    }


def _window_centers(
    master_duration_seconds: float,
    cut_plan: dict[str, Any] | None,
    options: VisualPacketOptions,
) -> tuple[str, list[float]]:
    mode = options.mode
    if mode == "auto":
        mode = "cuts" if cut_plan is not None else "overview"

    centers: list[float] = []
    if mode == "cuts" and cut_plan is not None:
        video_segments = [segment for segment in cut_plan.get("video_segments", []) if isinstance(segment, dict)]
        for previous, current in zip(video_segments[:-1], video_segments[1:], strict=False):
            centers.append(float(previous["master_end_seconds"]))
    else:
        interval_seconds = max(options.interval_seconds, 1.0)
        center_seconds = min(master_duration_seconds / 2.0, interval_seconds / 2.0)
        while center_seconds < master_duration_seconds - EPSILON:
            centers.append(center_seconds)
            center_seconds += interval_seconds
        if not centers:
            centers = [master_duration_seconds / 2.0]

    if options.max_windows is not None and options.max_windows > 0 and len(centers) > options.max_windows:
        if options.max_windows == 1:
            centers = [centers[len(centers) // 2]]
        else:
            step = (len(centers) - 1) / max(1, options.max_windows - 1)
            selected: list[float] = []
            for index in range(options.max_windows):
                candidate = centers[round(index * step)]
                if not selected or abs(candidate - selected[-1]) > EPSILON:
                    selected.append(candidate)
            centers = selected

    return mode, centers


def _slugify(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = lowered.strip("-")
    return lowered or "asset"


class _FrameExportPool:
    def __init__(self) -> None:
        self._captures: dict[str, cv2.VideoCapture] = {}

    def close(self) -> None:
        for capture in self._captures.values():
            capture.release()
        self._captures.clear()

    def _capture_for_path(self, path: str) -> cv2.VideoCapture:
        capture = self._captures.get(path)
        if capture is None:
            capture = cv2.VideoCapture(path)
            self._captures[path] = capture
        return capture

    def export_frame(
        self,
        *,
        source_path: str,
        source_seconds: float,
        image_width: int,
        image_quality: int,
        output_path: Path,
    ) -> dict[str, Any]:
        capture = self._capture_for_path(source_path)
        if not capture.isOpened():
            raise ValueError(f"OpenCV could not open {source_path}.")

        capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(source_seconds)) * 1000.0)
        ok, frame = capture.read()
        if not ok or frame is None:
            raise ValueError(f"Could not decode frame at {source_seconds:.3f}s from {source_path}.")

        height, width = frame.shape[:2]
        if width > image_width:
            target_width = image_width
            target_height = max(2, int(round(height * target_width / width / 2.0) * 2))
            frame = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
        else:
            target_width = width
            target_height = height

        output_path.parent.mkdir(parents=True, exist_ok=True)
        success = cv2.imwrite(str(output_path), frame, [cv2.IMWRITE_JPEG_QUALITY, int(image_quality)])
        if not success:
            raise ValueError(f"Failed to write preview image to {output_path}.")

        return {
            "path": str(output_path),
            "width": target_width,
            "height": target_height,
        }


def build_visual_packet(
    sync_map: dict[str, Any],
    *,
    source_sync_map_path: str | None = None,
    analysis_map: dict[str, Any] | None = None,
    source_analysis_path: str | None = None,
    transcript_artifact: dict[str, Any] | None = None,
    source_transcript_path: str | None = None,
    cut_plan: dict[str, Any] | None = None,
    source_cut_plan_path: str | None = None,
    output_dir: str,
    options: VisualPacketOptions | None = None,
) -> dict[str, Any]:
    if sync_map.get("schema_version") != "vazer.sync_map.v1":
        raise ValueError("Unsupported sync_map schema version.")

    packet_options = options or VisualPacketOptions()
    coverages_by_asset, master_duration_seconds = _build_coverages(sync_map, packet_options.role_overrides)
    if not coverages_by_asset:
        raise ValueError("sync_map does not contain any synced camera coverage.")

    mode, centers = _window_centers(master_duration_seconds, cut_plan, packet_options)
    windows_by_asset = _analysis_windows_by_asset(analysis_map)
    master_payload = sync_map["master"]
    output_root = Path(output_dir)
    image_root = output_root / "images"

    packet_windows: list[dict[str, Any]] = []
    export_pool = _FrameExportPool()
    try:
        for window_index, center_seconds in enumerate(centers, start=1):
            window_id = f"window_{window_index:04d}"
            if mode == "cuts":
                context_seconds = packet_options.cut_context_seconds * 2.0
            else:
                context_seconds = packet_options.window_context_seconds
            master_start_seconds = max(0.0, center_seconds - context_seconds / 2.0)
            master_end_seconds = min(master_duration_seconds, center_seconds + context_seconds / 2.0)
            transcript_context = _transcript_excerpt(
                transcript_artifact,
                center_seconds,
                packet_options.transcript_context_seconds,
            )

            images: list[dict[str, Any]] = []
            for coverage in sorted(
                coverages_by_asset.values(),
                key=lambda item: (ROLE_ORDER.get(item.role, ROLE_ORDER["unknown"]), item.asset_id),
            ):
                if coverage.overlap_start_seconds > center_seconds + EPSILON:
                    continue
                if coverage.overlap_end_seconds < center_seconds - EPSILON:
                    continue

                source_seconds = coverage.speed * center_seconds + coverage.offset_seconds
                if source_seconds < -EPSILON or source_seconds > coverage.duration_seconds + EPSILON:
                    continue

                image_path = image_root / window_id / f"{_slugify(coverage.asset_id)}.jpg"
                image_export = export_pool.export_frame(
                    source_path=coverage.path,
                    source_seconds=source_seconds,
                    image_width=packet_options.image_width,
                    image_quality=packet_options.image_quality,
                    output_path=image_path,
                )
                signal_summary = _analysis_signal_summary(
                    coverage.asset_id,
                    master_start_seconds,
                    master_end_seconds,
                    windows_by_asset,
                )
                images.append(
                    {
                        "asset_id": coverage.asset_id,
                        "role": coverage.role,
                        "asset_path": coverage.path,
                        "image_path": image_export["path"],
                        "image_width": image_export["width"],
                        "image_height": image_export["height"],
                        "master_center_seconds": center_seconds,
                        "source_seconds": source_seconds,
                        "confidence": coverage.confidence,
                        "signals": signal_summary,
                    }
                )

            if not images:
                continue

            packet_windows.append(
                {
                    "id": window_id,
                    "kind": "cut_context" if mode == "cuts" else "overview_sample",
                    "master_center_seconds": center_seconds,
                    "master_start_seconds": master_start_seconds,
                    "master_end_seconds": master_end_seconds,
                    "transcript": transcript_context,
                    "images": images,
                }
            )
    finally:
        export_pool.close()

    selected_assets = sorted({image["asset_id"] for window in packet_windows for image in window["images"]})
    role_summary = {
        role: sum(1 for window in packet_windows for image in window["images"] if image["role"] == role)
        for role in ("close", "halbtotale", "totale", "unknown")
    }
    return {
        "schema_version": "vazer.visual_packet.v1",
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
        else {
            "schema_version": transcript_artifact["source"]["schema_version"],
            "path": source_transcript_path,
        },
        "source_cut_plan": None
        if cut_plan is None
        else {
            "schema_version": cut_plan["schema_version"],
            "path": source_cut_plan_path,
        },
        "master_audio": {
            "path": master_payload["path"],
            "duration_seconds": master_duration_seconds,
            "format_name": master_payload.get("format_name"),
        },
        "options": {
            "mode": mode,
            "interval_seconds": packet_options.interval_seconds,
            "window_context_seconds": packet_options.window_context_seconds,
            "transcript_context_seconds": packet_options.transcript_context_seconds,
            "image_width": packet_options.image_width,
            "image_quality": packet_options.image_quality,
            "cut_context_seconds": packet_options.cut_context_seconds,
            "max_windows": packet_options.max_windows,
            "role_overrides": packet_options.role_overrides or {},
        },
        "windows": packet_windows,
        "summary": {
            "mode": mode,
            "window_count": len(packet_windows),
            "image_count": sum(len(window["images"]) for window in packet_windows),
            "selected_assets": selected_assets,
            "role_image_counts": role_summary,
        },
    }


def load_visual_packet(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_visual_packet(packet: dict[str, Any], output_path: str) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(packet, indent=2), encoding="utf-8")
    return destination
