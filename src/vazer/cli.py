from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .analysis import AnalysisOptions, build_analysis_map, load_analysis_map, write_analysis_map
from .cut_plan import build_baseline_cut_plan, load_json_artifact, write_cut_plan
from .render import build_render_scaffold, load_cut_plan
from .sync import SyncOptions, analyze_sync
from .sync_map import build_sync_map, write_sync_map
from .transcribe import TranscriptionOptions, build_master_transcript, write_transcript_artifact
from .transcript import load_transcript_artifact


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

    plan_parser = root_subparsers.add_parser("plan", help="Cut-plan generation.")
    plan_subparsers = plan_parser.add_subparsers(dest="plan_command")

    baseline_parser = plan_subparsers.add_parser(
        "baseline",
        help="Build a baseline cut_plan from an existing sync_map.",
    )
    baseline_parser.add_argument("--sync-map", required=True, help="Path to a sync_map JSON file.")
    baseline_parser.add_argument("--analysis", help="Optional analysis_map JSON file.")
    baseline_parser.add_argument("--transcript", help="Optional transcript JSON file.")
    baseline_parser.add_argument("--out", required=True, help="Path to the cut_plan JSON output.")
    baseline_parser.add_argument("--json", action="store_true", help="Print the generated cut_plan JSON to stdout.")

    analyze_parser = root_subparsers.add_parser("analyze", help="Technical signal analysis.")
    analyze_subparsers = analyze_parser.add_subparsers(dest="analyze_command")

    technical_parser = analyze_subparsers.add_parser(
        "technical",
        help="Build an analysis_map with speech-like master activity and camera quality windows.",
    )
    technical_parser.add_argument("--sync-map", required=True, help="Path to a sync_map JSON file.")
    technical_parser.add_argument("--out", required=True, help="Path to the analysis_map JSON output.")
    technical_parser.add_argument("--audio-rate", type=int, default=AnalysisOptions().audio_rate)
    technical_parser.add_argument("--audio-frame", type=float, default=AnalysisOptions().audio_frame_seconds)
    technical_parser.add_argument("--speech-merge-gap", type=float, default=AnalysisOptions().speech_merge_gap_seconds)
    technical_parser.add_argument("--speech-min-segment", type=float, default=AnalysisOptions().speech_min_segment_seconds)
    technical_parser.add_argument("--video-sample-interval", type=float, default=AnalysisOptions().video_sample_interval_seconds)
    technical_parser.add_argument("--video-window", type=float, default=AnalysisOptions().video_window_seconds)
    technical_parser.add_argument("--analysis-width", type=int, default=AnalysisOptions().analysis_width)
    technical_parser.add_argument("--json", action="store_true", help="Print the generated analysis_map JSON to stdout.")

    transcribe_parser = root_subparsers.add_parser("transcribe", help="Master-audio transcription.")
    transcribe_subparsers = transcribe_parser.add_subparsers(dest="transcribe_command")

    master_transcribe_parser = transcribe_subparsers.add_parser(
        "master",
        help="Transcribe only the canonical master audio via OpenAI.",
    )
    master_source_group = master_transcribe_parser.add_mutually_exclusive_group(required=True)
    master_source_group.add_argument("--master", help="Path to the master audio file.")
    master_source_group.add_argument("--sync-map", help="Path to an existing sync_map JSON file.")
    master_transcribe_parser.add_argument("--out", required=True, help="Path to the transcript JSON output.")
    master_transcribe_parser.add_argument("--model", default=TranscriptionOptions().model)
    master_transcribe_parser.add_argument("--language", help="Optional language code, for example de.")
    master_transcribe_parser.add_argument("--prompt", help="Optional transcription prompt/style primer.")
    master_transcribe_parser.add_argument("--chunk-seconds", type=float, default=TranscriptionOptions().chunk_seconds)
    master_transcribe_parser.add_argument("--audio-sample-rate", type=int, default=TranscriptionOptions().audio_sample_rate)
    master_transcribe_parser.add_argument("--audio-bitrate", default=TranscriptionOptions().audio_bitrate)
    master_transcribe_parser.add_argument("--json", action="store_true", help="Print the generated transcript JSON to stdout.")

    render_parser = root_subparsers.add_parser("render", help="Render scaffolding.")
    render_subparsers = render_parser.add_subparsers(dest="render_command")

    scaffold_parser = render_subparsers.add_parser(
        "scaffold",
        help="Build ffmpeg scaffold files from a cut_plan.",
    )
    scaffold_parser.add_argument("--cut-plan", required=True, help="Path to a cut_plan JSON file.")
    scaffold_parser.add_argument("--output-media", required=True, help="Target media file path for the ffmpeg command.")
    scaffold_parser.add_argument("--out-dir", required=True, help="Directory for scaffold artifacts.")
    scaffold_parser.add_argument("--json", action="store_true", help="Print the render manifest JSON to stdout.")

    return parser


def _format_signed_seconds(value: float) -> str:
    prefix = "+" if value >= 0 else ""
    return f"{prefix}{value:.3f} s"


def _build_sync_options(args: argparse.Namespace) -> SyncOptions:
    return SyncOptions(
        coarse_rate=args.coarse_rate,
        fine_rate=args.fine_rate,
        anchor_count=args.anchor_count,
        anchor_window_seconds=args.anchor_window,
        anchor_search_seconds=args.anchor_search,
    )


def _build_analysis_options(args: argparse.Namespace) -> AnalysisOptions:
    return AnalysisOptions(
        audio_rate=args.audio_rate,
        audio_frame_seconds=args.audio_frame,
        speech_merge_gap_seconds=args.speech_merge_gap,
        speech_min_segment_seconds=args.speech_min_segment,
        video_sample_interval_seconds=args.video_sample_interval,
        video_window_seconds=args.video_window,
        analysis_width=args.analysis_width,
    )


def _build_transcription_options(args: argparse.Namespace) -> TranscriptionOptions:
    return TranscriptionOptions(
        model=args.model,
        language=args.language,
        prompt=args.prompt,
        chunk_seconds=args.chunk_seconds,
        audio_sample_rate=args.audio_sample_rate,
        audio_bitrate=args.audio_bitrate,
    )


def _print_probe_summary(report: dict[str, Any]) -> None:
    selected_stream = report["camera"]["selected_stream"]
    mapping = report["mapping"]
    summary = report["summary"]
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
    print(f"Confidence: {summary['confidence']}")
    print(f"Validation: {'passed' if summary['validated'] else 'failed'}")
    diagnostics = summary.get("diagnostics", {})
    residual_rmse = diagnostics.get("residual_rmse_seconds")
    residual_max = diagnostics.get("residual_max_abs_seconds")
    offset_range = diagnostics.get("accepted_offset_range_seconds")
    if residual_rmse is not None:
        print(f"Residual RMS: {residual_rmse:.3f} s")
    if residual_max is not None:
        print(f"Residual Max: {residual_max:.3f} s")
    if offset_range is not None:
        print(f"Accepted Offset Range: {offset_range:.3f} s")
    for error in summary.get("errors", []):
        print(f"Error: {error}")


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


def _print_cut_plan_summary(cut_plan: dict[str, Any], output_path: Path) -> None:
    summary = cut_plan["summary"]
    timeline = cut_plan["timeline"]
    print(f"Cut plan written to: {output_path}")
    print(
        f"Output duration: {timeline['output_duration_seconds']:.3f} s "
        f"across {summary['video_segments']} video segments"
    )
    print(f"Selected assets: {', '.join(summary['selected_assets'])}")
    if summary["dropped_assets"]:
        print(f"Dropped synced assets: {', '.join(summary['dropped_assets'])}")
    print(f"Signal-aware planning: {'yes' if summary['signal_aware'] else 'no'}")


def _print_analysis_map_summary(analysis_map: dict[str, Any], output_path: Path) -> None:
    summary = analysis_map["summary"]
    speech_summary = analysis_map["master_audio_activity"]["summary"]
    threshold_dbfs = speech_summary["threshold_dbfs"]
    print(f"Analysis map written to: {output_path}")
    print(
        f"Entries: {summary['total']} total, {summary['analyzed']} analyzed, {summary['failed']} failed"
    )
    print(
        f"Master speech-like segments: {speech_summary['segment_count']} "
        f"(threshold {'n/a' if threshold_dbfs is None else f'{threshold_dbfs:.2f} dBFS'})"
    )
    for entry in analysis_map["entries"]:
        if entry["status"] == "analyzed":
            entry_summary = entry["summary"]
            print(
                f"[ok] {Path(entry['path']).name}: {entry_summary['window_count']} windows, "
                f"usable ratio {entry_summary['usable_window_ratio']:.3f}"
            )
        else:
            print(f"[failed] {Path(entry['path']).name}: {entry['error']}")


def _print_render_scaffold_summary(manifest: dict[str, Any]) -> None:
    artifacts = manifest["artifacts"]
    output = manifest["output"]
    print(f"Render scaffold written to: {artifacts['manifest_path']}")
    print(f"Filtergraph: {artifacts['filtergraph_path']}")
    print(f"Command file: {artifacts['command_path']}")
    print(f"Target media: {output['path']}")
    print(f"Output duration: {output['duration_seconds']:.3f} s")


def _print_transcript_summary(transcript_artifact: dict[str, Any], output_path: Path) -> None:
    summary = transcript_artifact["summary"]
    provider = transcript_artifact["provider"]
    master_audio = transcript_artifact["master_audio"]
    print(f"Transcript written to: {output_path}")
    print(f"Master: {Path(master_audio['path']).name}")
    print(f"Model: {provider['model']}")
    print(
        f"Chunks: {summary['chunk_count']}, "
        f"segments: {summary['segment_count']}, "
        f"chars: {summary['character_count']}"
    )
    if transcript_artifact.get("language"):
        print(f"Language: {transcript_artifact['language']}")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "sync":
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
                _print_probe_summary(report)
            return 0 if report["summary"]["validated"] else 1

        if args.sync_command == "map":
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

            return 0 if sync_map["summary"]["synced"] > 0 else 1

        parser.print_help()
        return 1

    if args.command == "plan" and args.plan_command == "baseline":
        try:
            sync_map = load_json_artifact(args.sync_map)
            analysis_map = None if not args.analysis else load_analysis_map(args.analysis)
            transcript_artifact = None if not args.transcript else load_transcript_artifact(args.transcript)
            cut_plan = build_baseline_cut_plan(
                sync_map,
                source_sync_map_path=args.sync_map,
                analysis_map=analysis_map,
                source_analysis_path=args.analysis,
                transcript_artifact=transcript_artifact,
                source_transcript_path=args.transcript,
            )
            output_path = write_cut_plan(cut_plan, args.out)
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")

        if args.json:
            print(json.dumps(cut_plan, indent=2))
        else:
            _print_cut_plan_summary(cut_plan, output_path)
        return 0

    if args.command == "analyze" and args.analyze_command == "technical":
        try:
            sync_map = load_json_artifact(args.sync_map)
            analysis_map = build_analysis_map(
                sync_map,
                source_sync_map_path=args.sync_map,
                options=_build_analysis_options(args),
            )
            output_path = write_analysis_map(analysis_map, args.out)
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")

        if args.json:
            print(json.dumps(analysis_map, indent=2))
        else:
            _print_analysis_map_summary(analysis_map, output_path)
        return 0 if analysis_map["summary"]["analyzed"] > 0 else 1

    if args.command == "transcribe" and args.transcribe_command == "master":
        try:
            master_path = args.master
            if args.sync_map:
                sync_map = load_json_artifact(args.sync_map)
                master_payload = sync_map.get("master")
                if not isinstance(master_payload, dict) or not isinstance(master_payload.get("path"), str):
                    raise ValueError("sync_map does not expose a usable master path.")
                master_path = master_payload["path"]

            if not master_path:
                raise ValueError("A master audio path is required.")

            transcript_artifact = build_master_transcript(
                master_path,
                source_sync_map_path=args.sync_map,
                options=_build_transcription_options(args),
            )
            output_path = write_transcript_artifact(transcript_artifact, args.out)
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")

        if args.json:
            print(json.dumps(transcript_artifact, indent=2))
        else:
            _print_transcript_summary(transcript_artifact, output_path)
        return 0

    if args.command == "render" and args.render_command == "scaffold":
        try:
            cut_plan = load_cut_plan(args.cut_plan)
            manifest = build_render_scaffold(
                cut_plan,
                cut_plan_path=args.cut_plan,
                output_media_path=args.output_media,
                scaffold_dir=args.out_dir,
            )
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")

        if args.json:
            print(json.dumps(manifest, indent=2))
        else:
            _print_render_scaffold_summary(manifest)
        return 0

    parser.print_help()
    return 1
