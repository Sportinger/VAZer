from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .sync import SyncOptions, analyze_sync
from .sync_map import build_sync_map, write_sync_map


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vazer")
    root_subparsers = parser.add_subparsers(dest="command")

    sync_parser = root_subparsers.add_parser("sync", help="Audio-first sync tooling.")
    sync_subparsers = sync_parser.add_subparsers(dest="sync_command")

    probe_parser = sync_subparsers.add_parser("probe", help="Inspect and align one camera file to the master audio.")
    probe_parser.add_argument("--master", required=True, help="Path to the master audio file.")
    probe_parser.add_argument("--camera", required=True, help="Path to the camera clip.")
    probe_parser.add_argument("--stream", help="Force a specific ffmpeg map specifier, for example 0:1.")
    probe_parser.add_argument("--coarse-rate", type=int, default=SyncOptions().coarse_rate)
    probe_parser.add_argument("--fine-rate", type=int, default=SyncOptions().fine_rate)
    probe_parser.add_argument("--anchor-count", type=int, default=SyncOptions().anchor_count)
    probe_parser.add_argument("--anchor-window", type=float, default=SyncOptions().anchor_window_seconds)
    probe_parser.add_argument("--anchor-search", type=float, default=SyncOptions().anchor_search_seconds)
    probe_parser.add_argument("--json", action="store_true", help="Print the full report as JSON.")

    map_parser = sync_subparsers.add_parser(
        "map",
        help="Build a persistierbares sync_map JSON fuer mehrere Kamera-Dateien.",
    )
    map_parser.add_argument("--master", required=True, help="Path to the master audio file.")
    map_parser.add_argument(
        "--camera",
        action="append",
        dest="cameras",
        required=True,
        help="Path to one camera file. Repeat for multiple inputs.",
    )
    map_parser.add_argument("--out", required=True, help="Path to the sync_map JSON output.")
    map_parser.add_argument("--coarse-rate", type=int, default=SyncOptions().coarse_rate)
    map_parser.add_argument("--fine-rate", type=int, default=SyncOptions().fine_rate)
    map_parser.add_argument("--anchor-count", type=int, default=SyncOptions().anchor_count)
    map_parser.add_argument("--anchor-window", type=float, default=SyncOptions().anchor_window_seconds)
    map_parser.add_argument("--anchor-search", type=float, default=SyncOptions().anchor_search_seconds)
    map_parser.add_argument("--json", action="store_true", help="Print the generated sync_map JSON to stdout.")

    return parser


def _format_signed_seconds(value: float) -> str:
    prefix = "+" if value >= 0 else ""
    return f"{prefix}{value:.3f} s"


def _print_summary(report: dict[str, Any]) -> None:
    selected_stream = report["camera"]["selected_stream"]
    mapping = report["mapping"]
    accepted = report["anchors"]["accepted"]
    mean_peak_ratio = None
    if accepted:
        mean_peak_ratio = sum(anchor["peak_ratio"] or 1.0 for anchor in accepted) / len(accepted)

    print(f"Master: {Path(report['master']['path']).name}")
    print(f"Camera: {Path(report['camera']['path']).name}")
    print(
        "Selected scratch stream: "
        f"{selected_stream['map_specifier']} (stream #{selected_stream['absolute_stream_index']})"
    )
    print(f"Camera starts on master timeline at: {mapping['camera_starts_at_master_seconds']:.3f} s")
    print(
        "Mapping: "
        f"source_time = {mapping['speed']:.9f} * master_time "
        f"{'+' if mapping['offset_seconds'] >= 0 else '-'} "
        f"{abs(mapping['offset_seconds']):.6f} s"
    )
    print(
        "Predicted drift over 1h: "
        f"{_format_signed_seconds(mapping['predicted_drift_over_hour_seconds'])}"
    )
    print(f"Accepted anchors: {len(accepted)}/{len(report['anchors']['measurements'])}")
    print(f"Mean peak ratio: {'n/a' if mean_peak_ratio is None else f'{mean_peak_ratio:.3f}'}")
    print(f"Confidence: {report['summary']['confidence']}")


def _print_sync_map_summary(sync_map: dict[str, Any], output_path: Path) -> None:
    summary = sync_map["summary"]
    print(f"Sync map written to: {output_path}")
    print(f"Master: {Path(sync_map['master']['path']).name}")
    print(f"Entries: {summary['total']} total, {summary['synced']} synced, {summary['failed']} failed")

    for entry in sync_map["entries"]:
        if entry["status"] == "synced":
            mapping = entry["mapping"]
            print(
                f"[ok] {Path(entry['path']).name}: start {mapping['camera_starts_at_master_seconds']:.3f} s, "
                f"stream {entry['selected_stream']['map_specifier']}, confidence {entry['summary']['confidence']}"
            )
            continue

        print(f"[failed] {Path(entry['path']).name}: {entry['error']}")


def _build_sync_options(args: argparse.Namespace) -> SyncOptions:
    return SyncOptions(
        coarse_rate=args.coarse_rate,
        fine_rate=args.fine_rate,
        anchor_count=args.anchor_count,
        anchor_window_seconds=args.anchor_window,
        anchor_search_seconds=args.anchor_search,
    )


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command != "sync" or args.sync_command not in {"probe", "map"}:
        parser.print_help()
        return 1

    options = _build_sync_options(args)

    if args.sync_command == "probe":
        try:
            report = analyze_sync(
                args.master,
                args.camera,
                requested_stream=args.stream,
                options=options,
            )
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")

        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _print_summary(report)

        return 0

    try:
        sync_map = build_sync_map(
            args.master,
            args.cameras,
            options=options,
        )
        output_path = write_sync_map(sync_map, args.out)
    except Exception as error:  # pragma: no cover - CLI surface
        parser.exit(1, f"VAZer error: {error}\n")

    if args.json:
        print(json.dumps(sync_map, indent=2))
    else:
        _print_sync_map_summary(sync_map, output_path)

    if sync_map["summary"]["synced"] == 0:
        return 1

    return 0
