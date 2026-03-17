from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from . import __version__
from .fftools import probe_media
from .sync import SyncOptions, analyze_sync


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _derive_asset_ids(paths: list[str]) -> list[str]:
    stems = [Path(path).stem.strip() or "asset" for path in paths]
    counts = Counter(stems)
    seen: dict[str, int] = {}
    asset_ids: list[str] = []

    for stem in stems:
        if counts[stem] == 1:
            asset_ids.append(stem)
            continue

        next_index = seen.get(stem, 0) + 1
        seen[stem] = next_index
        asset_ids.append(f"{stem}_{next_index:02d}")

    return asset_ids


def build_sync_map(
    master_path: str,
    camera_paths: list[str],
    *,
    options: SyncOptions | None = None,
) -> dict[str, Any]:
    if not camera_paths:
        raise ValueError("At least one camera path is required to build a sync_map.")

    sync_options = options or SyncOptions()
    asset_ids = _derive_asset_ids(camera_paths)
    entries: list[dict[str, Any]] = []
    master_summary: dict[str, Any] | None = None

    for asset_id, camera_path in zip(asset_ids, camera_paths, strict=True):
        try:
            report = analyze_sync(master_path, camera_path, options=sync_options)
        except Exception as error:
            entries.append(
                {
                    "asset_id": asset_id,
                    "path": camera_path,
                    "status": "failed",
                    "error": str(error),
                }
            )
            continue

        if master_summary is None:
            master_summary = report["master"]

        media_info = probe_media(camera_path)
        primary_video = media_info.video_streams[0] if media_info.video_streams else None
        selected_stream = report["camera"]["selected_stream"]
        if not report["summary"]["validated"]:
            entries.append(
                {
                    "asset_id": asset_id,
                    "path": camera_path,
                    "status": "failed",
                    "error": " ".join(report["summary"]["errors"]),
                    "selected_stream": {
                        "map_specifier": selected_stream["map_specifier"],
                        "absolute_stream_index": selected_stream["absolute_stream_index"],
                    },
                    "coarse": report["coarse"],
                    "anchors": report["anchors"],
                    "summary": report["summary"],
                }
            )
            continue

        entries.append(
            {
                "asset_id": asset_id,
                "path": camera_path,
                "status": "synced",
                "media": {
                    "format_name": media_info.format_name,
                    "duration_seconds": media_info.duration_seconds,
                    "audio_stream_count": len(media_info.audio_streams),
                    "video_stream_count": len(media_info.video_streams),
                    "primary_video": None
                    if primary_video is None
                    else {
                        "absolute_stream_index": primary_video.absolute_stream_index,
                        "codec_name": primary_video.codec_name,
                        "duration_seconds": primary_video.duration_seconds,
                        "width": primary_video.width,
                        "height": primary_video.height,
                        "frame_rate": primary_video.frame_rate,
                    },
                },
                "selected_stream": {
                    "map_specifier": selected_stream["map_specifier"],
                    "absolute_stream_index": selected_stream["absolute_stream_index"],
                },
                "mapping": report["mapping"],
                "coarse": report["coarse"],
                "anchors": report["anchors"],
                "summary": report["summary"],
            }
        )

    if master_summary is None:
        master_summary = {
            "path": master_path,
            "duration_seconds": None,
            "format_name": None,
        }

    synced_count = sum(1 for entry in entries if entry["status"] == "synced")
    failed_count = len(entries) - synced_count

    return {
        "schema_version": "vazer.sync_map.v1",
        "generated_at_utc": _utc_timestamp(),
        "tool": {
            "name": "vazer",
            "version": __version__,
        },
        "master": master_summary,
        "options": {
            "coarse_rate": sync_options.coarse_rate,
            "fine_rate": sync_options.fine_rate,
            "envelope_bin_seconds": sync_options.envelope_bin_seconds,
            "activity_rate": sync_options.activity_rate,
            "activity_window_seconds": sync_options.activity_window_seconds,
            "anchor_count": sync_options.anchor_count,
            "anchor_window_seconds": sync_options.anchor_window_seconds,
            "anchor_search_seconds": sync_options.anchor_search_seconds,
            "coarse_candidate_limit": sync_options.coarse_candidate_limit,
            "anchor_activity_step_seconds": sync_options.anchor_activity_step_seconds,
            "anchor_min_spacing_seconds": sync_options.anchor_min_spacing_seconds,
        },
        "entries": entries,
        "summary": {
            "total": len(entries),
            "synced": synced_count,
            "failed": failed_count,
        },
    }


def write_sync_map(sync_map: dict[str, Any], output_path: str) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(sync_map, indent=2), encoding="utf-8")
    return destination
