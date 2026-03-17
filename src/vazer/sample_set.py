from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
from typing import Any

from . import __version__
from .cut_plan import _coverage_window, _require_duration
from .visual_packet import _infer_camera_role, _normalized_role

EPSILON = 1e-6


@dataclass(slots=True)
class SampleSetOptions:
    duration_seconds: float = 60.0
    window_count: int = 3
    stagger_ratio: float = 0.15
    mode: str = "copy"
    role_overrides: dict[str, str] | None = None


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _role_for_asset(asset_id: str, path: str, overrides: dict[str, str] | None) -> str:
    if overrides and asset_id in overrides:
        return _normalized_role(overrides[asset_id])
    return _infer_camera_role(asset_id, path)


def _shift_for_role(role: str, stagger_seconds: float, window_index: int) -> float:
    direction = -1.0 if window_index % 2 == 1 else 1.0
    if role == "close":
        return -direction * stagger_seconds
    if role == "halbtotale":
        return direction * stagger_seconds
    if role == "totale":
        return 0.0
    return 0.5 * direction * stagger_seconds


def _copy_or_reencode(
    *,
    source_path: str,
    start_seconds: float,
    duration_seconds: float,
    output_path: str,
    mode: str,
) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_seconds:.6f}",
        "-i",
        source_path,
        "-t",
        f"{duration_seconds:.6f}",
    ]

    if mode == "copy":
        command.extend(
            [
                "-map",
                "0:v?",
                "-map",
                "0:a?",
                "-sn",
                "-dn",
                "-c",
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                output_path,
            ]
        )
    else:
        command.extend(
            [
                "-map",
                "0:v?",
                "-map",
                "0:a?",
                "-sn",
                "-dn",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                output_path,
            ]
        )

    subprocess.run(command, check=True, capture_output=True)


def _window_start_candidates(
    *,
    sync_map: dict[str, Any],
    duration_seconds: float,
    window_count: int,
    stagger_ratio: float,
    role_overrides: dict[str, str] | None,
) -> list[dict[str, Any]]:
    master_payload = sync_map.get("master")
    if not isinstance(master_payload, dict):
        raise ValueError("sync_map master payload is missing.")

    master_path = master_payload.get("path")
    if not isinstance(master_path, str) or not master_path:
        raise ValueError("sync_map master path is missing.")

    master_duration_seconds = _require_duration(master_payload, "Master audio", master_path)
    coverages = []
    for entry in sync_map.get("entries", []):
        if not isinstance(entry, dict) or entry.get("status") != "synced":
            continue
        coverage = _coverage_window(entry, master_duration_seconds)
        if coverage is None:
            continue
        role = _role_for_asset(coverage.asset_id, coverage.asset_path, role_overrides)
        coverages.append((entry, coverage, role))
    if not coverages:
        raise ValueError("sync_map does not contain any synced camera coverage.")

    stagger_seconds = duration_seconds * max(0.0, min(0.45, stagger_ratio))
    window_specs: list[dict[str, Any]] = []

    for window_index in range(window_count):
        lower_bounds = [0.0]
        upper_bounds = [master_duration_seconds - duration_seconds]
        asset_specs = []
        for entry, coverage, role in coverages:
            shift_seconds = _shift_for_role(role, stagger_seconds, window_index)
            lower_bounds.append(coverage.overlap_start_seconds - shift_seconds)
            upper_bounds.append(coverage.overlap_end_seconds - duration_seconds - shift_seconds)
            asset_specs.append(
                {
                    "asset_id": coverage.asset_id,
                    "path": coverage.asset_path,
                    "role": role,
                    "confidence": coverage.confidence,
                    "speed": coverage.speed,
                    "offset_seconds": coverage.offset_seconds,
                    "shift_seconds": shift_seconds,
                }
            )

        earliest_start_seconds = max(lower_bounds)
        latest_start_seconds = min(upper_bounds)
        if latest_start_seconds - earliest_start_seconds <= EPSILON:
            raise ValueError("No common sample range exists for the requested duration and stagger.")
        window_specs.append(
            {
                "window_index": window_index,
                "earliest_start_seconds": earliest_start_seconds,
                "latest_start_seconds": latest_start_seconds,
                "asset_specs": asset_specs,
            }
        )

    starts: list[float] = []
    if window_count == 1:
        start_seconds = (window_specs[0]["earliest_start_seconds"] + window_specs[0]["latest_start_seconds"]) / 2.0
        starts = [start_seconds]
    else:
        global_earliest = max(window["earliest_start_seconds"] for window in window_specs)
        global_latest = min(window["latest_start_seconds"] for window in window_specs)
        if global_latest - global_earliest > EPSILON:
            step = (global_latest - global_earliest) / max(1, window_count - 1)
            starts = [global_earliest + index * step for index in range(window_count)]
        else:
            starts = [
                (window["earliest_start_seconds"] + window["latest_start_seconds"]) / 2.0
                for window in window_specs
            ]

    final_windows = []
    for start_seconds, window_spec in zip(starts, window_specs, strict=False):
        base_start_seconds = min(
            max(float(start_seconds), float(window_spec["earliest_start_seconds"])),
            float(window_spec["latest_start_seconds"]),
        )
        final_windows.append(
            {
                "window_index": window_spec["window_index"],
                "base_start_seconds": base_start_seconds,
                "base_end_seconds": base_start_seconds + duration_seconds,
                "asset_specs": window_spec["asset_specs"],
            }
        )

    return final_windows


def build_sample_set(
    sync_map: dict[str, Any],
    *,
    source_sync_map_path: str | None = None,
    output_dir: str,
    options: SampleSetOptions | None = None,
) -> dict[str, Any]:
    if sync_map.get("schema_version") != "vazer.sync_map.v1":
        raise ValueError("Unsupported sync_map schema version.")

    sample_options = options or SampleSetOptions()
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    master_payload = sync_map["master"]
    master_path = master_payload["path"]

    windows = _window_start_candidates(
        sync_map=sync_map,
        duration_seconds=sample_options.duration_seconds,
        window_count=sample_options.window_count,
        stagger_ratio=sample_options.stagger_ratio,
        role_overrides=sample_options.role_overrides,
    )

    window_manifests: list[dict[str, Any]] = []
    for window in windows:
        window_id = f"sample_{window['window_index'] + 1:04d}"
        window_root = output_root / window_id
        window_root.mkdir(parents=True, exist_ok=True)

        master_output_path = window_root / "master.wav"
        _copy_or_reencode(
            source_path=master_path,
            start_seconds=window["base_start_seconds"],
            duration_seconds=sample_options.duration_seconds,
            output_path=str(master_output_path),
            mode="copy",
        )

        camera_outputs = []
        for asset_spec in window["asset_specs"]:
            asset_slug = asset_spec["asset_id"].replace(" ", "_")
            camera_output_path = window_root / f"{asset_slug}.mkv"
            camera_master_start_seconds = window["base_start_seconds"] + float(asset_spec["shift_seconds"])
            source_start_seconds = float(asset_spec["speed"]) * camera_master_start_seconds + float(asset_spec["offset_seconds"])
            _copy_or_reencode(
                source_path=asset_spec["path"],
                start_seconds=source_start_seconds,
                duration_seconds=sample_options.duration_seconds,
                output_path=str(camera_output_path),
                mode=sample_options.mode,
            )
            camera_outputs.append(
                {
                    "asset_id": asset_spec["asset_id"],
                    "role": asset_spec["role"],
                    "source_path": asset_spec["path"],
                    "output_path": str(camera_output_path),
                    "master_equivalent_start_seconds": camera_master_start_seconds,
                    "master_equivalent_end_seconds": camera_master_start_seconds + sample_options.duration_seconds,
                    "shift_seconds": asset_spec["shift_seconds"],
                    "confidence": asset_spec["confidence"],
                }
            )

        manifest = {
            "id": window_id,
            "master_source_path": master_path,
            "master_output_path": str(master_output_path),
            "master_start_seconds": window["base_start_seconds"],
            "master_end_seconds": window["base_end_seconds"],
            "duration_seconds": sample_options.duration_seconds,
            "cameras": camera_outputs,
        }
        manifest_path = window_root / "sample_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        window_manifests.append(manifest)

    packet = {
        "schema_version": "vazer.sample_set.v1",
        "generated_at_utc": _utc_timestamp(),
        "tool": {
            "name": "vazer",
            "version": __version__,
        },
        "source_sync_map": {
            "schema_version": sync_map["schema_version"],
            "path": source_sync_map_path,
        },
        "options": {
            "duration_seconds": sample_options.duration_seconds,
            "window_count": sample_options.window_count,
            "stagger_ratio": sample_options.stagger_ratio,
            "mode": sample_options.mode,
            "role_overrides": sample_options.role_overrides or {},
        },
        "windows": window_manifests,
        "summary": {
            "window_count": len(window_manifests),
            "camera_files": sum(len(window["cameras"]) for window in window_manifests),
            "duration_seconds": sample_options.duration_seconds,
        },
    }
    manifest_path = output_root / "sample_set.json"
    manifest_path.write_text(json.dumps(packet, indent=2), encoding="utf-8")
    return packet
