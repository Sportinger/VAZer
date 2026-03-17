from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .sync import SyncOptions, analyze_sync


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


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command != "sync" or args.sync_command != "probe":
        parser.print_help()
        return 1

    options = SyncOptions(
        coarse_rate=args.coarse_rate,
        fine_rate=args.fine_rate,
        anchor_count=args.anchor_count,
        anchor_window_seconds=args.anchor_window,
        anchor_search_seconds=args.anchor_search,
    )

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
