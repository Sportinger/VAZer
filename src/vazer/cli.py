from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .analysis import AnalysisOptions, build_analysis_map, load_analysis_map, write_analysis_map
from .ai_draft import AIDraftOptions, build_ai_draft_cut_plan
from .camera_roles import (
    CameraRoleOptions,
    build_camera_role_artifact_from_sync_map,
    write_camera_role_artifact,
)
from .cut_plan import DraftPlanOptions, build_baseline_cut_plan, build_draft_cut_plan, load_json_artifact, write_cut_plan
from .cut_review import (
    CutValidationOptions,
    build_cut_validation_report,
    load_cut_validation_report,
    repair_cut_plan,
    write_cut_validation_report,
)
from .desktop_app import launch_desktop_app
from .premiere_xml import export_premiere_multicam_cut_xml, export_premiere_sync_multicam_xml, export_premiere_xml
from .render import build_render_scaffold, load_cut_plan
from .sample_set import SampleSetOptions, build_sample_set
from .sync import SyncOptions, analyze_sync
from .sync_map import build_sync_map, write_sync_map
from .transcribe import TranscriptionOptions, build_master_transcript, write_transcript_artifact
from .transcript import load_transcript_artifact
from .ui_server import serve_ui
from .visual_packet import VisualPacketOptions, build_visual_packet, load_visual_packet, write_visual_packet


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

    draft_parser = plan_subparsers.add_parser(
        "draft",
        help="Build a draft cut_plan from sync, cheap CV and optional transcript cues.",
    )
    draft_parser.add_argument("--sync-map", required=True, help="Path to a sync_map JSON file.")
    draft_parser.add_argument("--analysis", help="Optional analysis_map JSON file.")
    draft_parser.add_argument("--transcript", help="Optional transcript JSON file.")
    draft_parser.add_argument("--out", required=True, help="Path to the cut_plan JSON output.")
    draft_parser.add_argument(
        "--transcript-pause-boundary",
        type=float,
        default=DraftPlanOptions().transcript_pause_boundary_seconds,
        help="Treat transcript pauses at or above this threshold as draft cut boundaries.",
    )
    draft_parser.add_argument("--json", action="store_true", help="Print the generated cut_plan JSON to stdout.")

    validate_parser = plan_subparsers.add_parser(
        "validate",
        help="Validate only the proposed cut points with local transcript and cheap CV checks.",
    )
    validate_parser.add_argument("--cut-plan", required=True, help="Path to a cut_plan JSON file.")
    validate_parser.add_argument("--sync-map", help="Optional sync_map JSON file for alternate-camera checks.")
    validate_parser.add_argument("--analysis", help="Optional analysis_map JSON file.")
    validate_parser.add_argument("--transcript", help="Optional transcript JSON file.")
    validate_parser.add_argument("--out", required=True, help="Path to the cut_validation JSON output.")
    validate_parser.add_argument(
        "--transcript-search-window",
        type=float,
        default=CutValidationOptions().transcript_search_window_seconds,
        help="Search radius around each cut for a better transcript boundary.",
    )
    validate_parser.add_argument(
        "--transcript-pause-boundary",
        type=float,
        default=CutValidationOptions().transcript_pause_boundary_seconds,
        help="Transcript pause threshold used when ranking cut-boundary candidates.",
    )
    validate_parser.add_argument(
        "--cut-context",
        type=float,
        default=CutValidationOptions().cut_context_seconds,
        help="Master-time context window around a cut for local quality checks.",
    )
    validate_parser.add_argument(
        "--probe-delta",
        type=float,
        default=CutValidationOptions().local_probe_delta_seconds,
        help="Distance around the cut frame for sparse local frame probes.",
    )
    validate_parser.add_argument(
        "--probe-width",
        type=int,
        default=CutValidationOptions().local_probe_width,
        help="Maximum width for sparse cut-frame probes.",
    )
    validate_parser.add_argument(
        "--local-dense-context",
        type=float,
        default=CutValidationOptions().local_dense_context_seconds,
        help="Local source-time context around each cut for dense probe analysis.",
    )
    validate_parser.add_argument(
        "--local-dense-fps",
        type=float,
        default=CutValidationOptions().local_dense_fps,
        help="Dense local analysis FPS around each cut.",
    )
    validate_parser.add_argument(
        "--local-dense-width",
        type=int,
        default=CutValidationOptions().local_dense_width,
        help="Maximum width for dense local cut analysis.",
    )
    validate_parser.add_argument(
        "--local-dense-decoder",
        choices=["auto", "cuda", "cpu"],
        default=CutValidationOptions().local_dense_decoder_preference,
        help="Decoder preference for dense local cut analysis.",
    )
    validate_parser.add_argument(
        "--local-dense-no-gpu",
        action="store_true",
        help="Disable GPU preference for dense local cut analysis.",
    )
    validate_parser.add_argument("--json", action="store_true", help="Print the generated validation JSON to stdout.")

    repair_parser = plan_subparsers.add_parser(
        "repair",
        help="Apply deterministic local cut repairs from an existing cut_validation report.",
    )
    repair_parser.add_argument("--cut-plan", required=True, help="Path to a cut_plan JSON file.")
    repair_parser.add_argument("--validation", required=True, help="Path to a cut_validation JSON file.")
    repair_parser.add_argument("--sync-map", help="Optional sync_map JSON file for alternate-camera repairs.")
    repair_parser.add_argument("--analysis", help="Optional analysis_map JSON file.")
    repair_parser.add_argument("--transcript", help="Optional transcript JSON file.")
    repair_parser.add_argument("--out", required=True, help="Path to the repaired cut_plan JSON output.")
    repair_parser.add_argument(
        "--repair-min-segment",
        type=float,
        default=CutValidationOptions().repair_min_segment_seconds,
        help="Minimum segment duration that repair is allowed to preserve.",
    )
    repair_parser.add_argument("--json", action="store_true", help="Print the repaired cut_plan JSON to stdout.")

    ai_draft_parser = plan_subparsers.add_parser(
        "ai-draft",
        help="Ask OpenAI for a theater-specific draft cut_plan from transcript plus visual packet.",
    )
    ai_draft_parser.add_argument("--sync-map", required=True, help="Path to a sync_map JSON file.")
    ai_draft_parser.add_argument("--visual-packet", required=True, help="Path to a visual_packet JSON file.")
    ai_draft_parser.add_argument("--analysis", help="Optional analysis_map JSON file.")
    ai_draft_parser.add_argument("--transcript", help="Optional transcript JSON file.")
    ai_draft_parser.add_argument("--out", required=True, help="Path to the AI-generated cut_plan JSON output.")
    ai_draft_parser.add_argument("--model", default=AIDraftOptions().model)
    ai_draft_parser.add_argument("--max-output-tokens", type=int, default=AIDraftOptions().max_output_tokens)
    ai_draft_parser.add_argument("--temperature", type=float, default=AIDraftOptions().temperature)
    ai_draft_parser.add_argument("--notes", help="Optional extra planning notes for the AI.")
    ai_draft_parser.add_argument("--master-start", type=float, help="Optional explicit master-time span start.")
    ai_draft_parser.add_argument("--master-end", type=float, help="Optional explicit master-time span end.")
    ai_draft_parser.add_argument("--json", action="store_true", help="Print the generated cut_plan JSON to stdout.")

    analyze_parser = root_subparsers.add_parser("analyze", help="Technical signal analysis.")
    analyze_subparsers = analyze_parser.add_subparsers(dest="analyze_command")

    technical_parser = analyze_subparsers.add_parser(
        "technical",
        help="Build a cheap no-proxy analysis_map with speech-like activity plus sparse sharpness/motion windows.",
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
    technical_parser.add_argument(
        "--decoder",
        choices=["auto", "cuda", "cpu"],
        default=AnalysisOptions().decoder_preference,
        help="Preferred decoder path for sequential video analysis.",
    )
    technical_parser.add_argument(
        "--no-gpu",
        action="store_true",
        help="Disable GPU preference for the sequential analyzer.",
    )
    technical_parser.add_argument("--block-grid-size", type=int, default=AnalysisOptions().block_grid_size)
    technical_parser.add_argument(
        "--block-highlight-ratio",
        type=float,
        default=AnalysisOptions().block_highlight_ratio,
    )
    technical_parser.add_argument("--local-dense-fps", type=float, default=AnalysisOptions().local_dense_fps)
    technical_parser.add_argument(
        "--local-dense-context",
        type=float,
        default=AnalysisOptions().local_dense_context_seconds,
    )
    technical_parser.add_argument("--local-dense-width", type=int, default=AnalysisOptions().local_dense_width)
    technical_parser.add_argument("--json", action="store_true", help="Print the generated analysis_map JSON to stdout.")

    roles_parser = analyze_subparsers.add_parser(
        "roles",
        help="Ask OpenAI once to classify synced theater cameras into totale / halbtotale / close from middle frames.",
    )
    roles_parser.add_argument("--sync-map", required=True, help="Path to a sync_map JSON file.")
    roles_parser.add_argument("--out", required=True, help="Path to the camera_roles JSON output.")
    roles_parser.add_argument("--out-dir", required=True, help="Directory for exported middle-frame images.")
    roles_parser.add_argument("--model", default=CameraRoleOptions().model)
    roles_parser.add_argument("--image-width", type=int, default=CameraRoleOptions().image_width)
    roles_parser.add_argument("--image-quality", type=int, default=CameraRoleOptions().image_quality)
    roles_parser.add_argument("--temperature", type=float, default=CameraRoleOptions().temperature)
    roles_parser.add_argument("--max-output-tokens", type=int, default=CameraRoleOptions().max_output_tokens)
    roles_parser.add_argument("--notes", help="Optional extra notes for the AI role classifier.")
    roles_parser.add_argument("--json", action="store_true", help="Print the generated camera_roles JSON to stdout.")

    visuals_parser = analyze_subparsers.add_parser(
        "visuals",
        help="Build a visual_packet with sparse stills for AI planning or cut review.",
    )
    visuals_parser.add_argument("--sync-map", required=True, help="Path to a sync_map JSON file.")
    visuals_parser.add_argument("--analysis", help="Optional analysis_map JSON file.")
    visuals_parser.add_argument("--transcript", help="Optional transcript JSON file.")
    visuals_parser.add_argument("--cut-plan", help="Optional cut_plan JSON file. If present, visuals can focus on cuts.")
    visuals_parser.add_argument("--out", required=True, help="Path to the visual_packet JSON output.")
    visuals_parser.add_argument("--out-dir", required=True, help="Directory for exported still images.")
    visuals_parser.add_argument(
        "--mode",
        choices=["auto", "overview", "cuts"],
        default=VisualPacketOptions().mode,
        help="Choose overview sampling or cut-focused sampling.",
    )
    visuals_parser.add_argument(
        "--interval",
        type=float,
        default=VisualPacketOptions().interval_seconds,
        help="Master-time sampling interval for overview packets.",
    )
    visuals_parser.add_argument(
        "--window-context",
        type=float,
        default=VisualPacketOptions().window_context_seconds,
        help="Master-time context length stored around overview samples.",
    )
    visuals_parser.add_argument(
        "--transcript-context",
        type=float,
        default=VisualPacketOptions().transcript_context_seconds,
        help="Context length used for transcript excerpts around each visual window.",
    )
    visuals_parser.add_argument(
        "--image-width",
        type=int,
        default=VisualPacketOptions().image_width,
        help="Maximum exported still width.",
    )
    visuals_parser.add_argument(
        "--image-quality",
        type=int,
        default=VisualPacketOptions().image_quality,
        help="JPEG quality for exported stills.",
    )
    visuals_parser.add_argument(
        "--cut-context",
        type=float,
        default=VisualPacketOptions().cut_context_seconds,
        help="Half-context around each cut when mode=cuts.",
    )
    visuals_parser.add_argument(
        "--max-windows",
        type=int,
        help="Optional cap on the number of exported windows.",
    )
    visuals_parser.add_argument(
        "--role",
        action="append",
        default=[],
        help="Optional camera role override as ASSET_ID=totale|close|halbtotale.",
    )
    visuals_parser.add_argument("--json", action="store_true", help="Print the generated visual_packet JSON to stdout.")

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

    export_parser = root_subparsers.add_parser("export", help="NLE project export.")
    export_subparsers = export_parser.add_subparsers(dest="export_command")

    premiere_parser = export_subparsers.add_parser(
        "premiere",
        help="Build a Premiere-importable XML from a cut_plan and/or sync_map.",
    )
    premiere_parser.add_argument(
        "--mode",
        choices=["flat-cut", "sync-multicam", "multicam-cut"],
        default="flat-cut",
        help="Export mode: flat-cut (default), sync-multicam (angles only), multicam-cut (angles with cuts).",
    )
    premiere_parser.add_argument("--cut-plan", help="Path to a cut_plan JSON file (required for flat-cut and multicam-cut).")
    premiere_parser.add_argument("--sync-map", help="Path to a sync_map JSON file (required for sync-multicam and multicam-cut).")
    premiere_parser.add_argument("--out", required=True, help="Target .xml output path.")
    premiere_parser.add_argument("--name", help="Optional project/sequence name override.")
    premiere_parser.add_argument("--json", action="store_true", help="Print the export summary JSON to stdout.")

    sample_parser = root_subparsers.add_parser("sample", help="Generate small synced media subsets for pipeline tests.")
    sample_subparsers = sample_parser.add_subparsers(dest="sample_command")

    sample_set_parser = sample_subparsers.add_parser(
        "set",
        help="Build overlapping test windows with staggered camera starts from an existing sync_map.",
    )
    sample_set_parser.add_argument("--sync-map", required=True, help="Path to a sync_map JSON file.")
    sample_set_parser.add_argument("--out-dir", required=True, help="Directory for the generated sample set.")
    sample_set_parser.add_argument("--duration", type=float, default=SampleSetOptions().duration_seconds)
    sample_set_parser.add_argument("--window-count", type=int, default=SampleSetOptions().window_count)
    sample_set_parser.add_argument("--stagger-ratio", type=float, default=SampleSetOptions().stagger_ratio)
    sample_set_parser.add_argument(
        "--mode",
        choices=["copy", "reencode"],
        default=SampleSetOptions().mode,
        help="Fast stream copy or cleaner re-encode for the generated camera slices.",
    )
    sample_set_parser.add_argument(
        "--role",
        action="append",
        default=[],
        help="Optional camera role override as ASSET_ID=totale|close|halbtotale.",
    )
    sample_set_parser.add_argument("--json", action="store_true", help="Print the generated sample_set JSON to stdout.")

    ui_parser = root_subparsers.add_parser("ui", help="Local browser UI for ingest and job control.")
    ui_subparsers = ui_parser.add_subparsers(dest="ui_command")

    ui_serve_parser = ui_subparsers.add_parser(
        "serve",
        help="Start a lightweight local web UI with drag-and-drop, progress and pause/resume.",
    )
    ui_serve_parser.add_argument("--host", default="127.0.0.1")
    ui_serve_parser.add_argument("--port", type=int, default=8765)
    ui_serve_parser.add_argument("--workspace", default=str(Path("out") / "ui"))
    ui_serve_parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the local UI in the default browser after the server starts.",
    )

    desktop_parser = root_subparsers.add_parser("desktop", help="Launch the native desktop app.")
    desktop_parser.add_argument("--workspace", default=str(Path("out") / "desktop"))

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
        decoder_preference=args.decoder,
        prefer_gpu=not args.no_gpu,
        block_grid_size=args.block_grid_size,
        block_highlight_ratio=args.block_highlight_ratio,
        local_dense_fps=args.local_dense_fps,
        local_dense_context_seconds=args.local_dense_context,
        local_dense_width=args.local_dense_width,
    )


def _build_draft_plan_options(args: argparse.Namespace) -> DraftPlanOptions:
    return DraftPlanOptions(
        transcript_pause_boundary_seconds=args.transcript_pause_boundary,
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


def _build_cut_validation_options(args: argparse.Namespace) -> CutValidationOptions:
    return CutValidationOptions(
        transcript_search_window_seconds=args.transcript_search_window,
        transcript_pause_boundary_seconds=args.transcript_pause_boundary,
        cut_context_seconds=args.cut_context,
        local_probe_delta_seconds=args.probe_delta,
        local_probe_width=args.probe_width,
        local_dense_context_seconds=args.local_dense_context,
        local_dense_fps=args.local_dense_fps,
        local_dense_width=args.local_dense_width,
        local_dense_decoder_preference=args.local_dense_decoder,
        local_dense_prefer_gpu=not args.local_dense_no_gpu,
        repair_min_segment_seconds=getattr(args, "repair_min_segment", CutValidationOptions().repair_min_segment_seconds),
    )


def _build_ai_draft_options(args: argparse.Namespace) -> AIDraftOptions:
    return AIDraftOptions(
        model=args.model,
        max_output_tokens=args.max_output_tokens,
        temperature=args.temperature,
        user_notes=args.notes,
        master_start_seconds=args.master_start,
        master_end_seconds=args.master_end,
    )


def _build_camera_role_options(args: argparse.Namespace) -> CameraRoleOptions:
    return CameraRoleOptions(
        model=args.model,
        image_width=args.image_width,
        image_quality=args.image_quality,
        temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
        user_notes=args.notes,
    )


def _parse_role_overrides(values: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Invalid --role value '{value}'. Expected ASSET_ID=ROLE.")
        asset_id, role = value.split("=", 1)
        normalized_asset_id = asset_id.strip()
        normalized_role = role.strip().lower()
        if normalized_role not in {"totale", "close", "halbtotale"}:
            raise ValueError(
                f"Invalid role '{role}' for asset '{asset_id}'. Use totale, close, or halbtotale."
            )
        if not normalized_asset_id:
            raise ValueError(f"Invalid --role value '{value}'. Asset id is empty.")
        overrides[normalized_asset_id] = normalized_role
    return overrides


def _build_visual_packet_options(args: argparse.Namespace) -> VisualPacketOptions:
    return VisualPacketOptions(
        mode=args.mode,
        interval_seconds=args.interval,
        window_context_seconds=args.window_context,
        transcript_context_seconds=args.transcript_context,
        image_width=args.image_width,
        image_quality=args.image_quality,
        cut_context_seconds=args.cut_context,
        max_windows=args.max_windows,
        role_overrides=_parse_role_overrides(args.role),
    )


def _build_sample_set_options(args: argparse.Namespace) -> SampleSetOptions:
    return SampleSetOptions(
        duration_seconds=args.duration,
        window_count=args.window_count,
        stagger_ratio=args.stagger_ratio,
        mode=args.mode,
        role_overrides=_parse_role_overrides(args.role),
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
    print(f"Planning stage: {cut_plan.get('planning_stage', summary.get('planning_stage', 'draft'))}")
    print(
        f"Output duration: {timeline['output_duration_seconds']:.3f} s "
        f"across {summary['video_segments']} video segments"
    )
    print(f"Selected assets: {', '.join(summary['selected_assets'])}")
    if summary["dropped_assets"]:
        print(f"Dropped synced assets: {', '.join(summary['dropped_assets'])}")
    print(f"Signal-aware planning: {'yes' if summary['signal_aware'] else 'no'}")
    if summary.get("repair_applied"):
        print(f"Local repairs applied: {summary['repair_actions']}")


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


def _print_camera_role_summary(artifact: dict[str, Any], output_path: Path) -> None:
    summary = artifact["summary"]
    print(f"Camera roles written to: {output_path}")
    print(f"Assets: {summary['asset_count']}")
    print(
        "Role counts: "
        f"close {summary['role_counts']['close']}, "
        f"halbtotale {summary['role_counts']['halbtotale']}, "
        f"totale {summary['role_counts']['totale']}"
    )
    print(f"Summary: {summary['summary_text']}")
    for assignment in artifact["assignments"]:
        print(
            f"[{assignment['role']}] {assignment['asset_id']} "
            f"({assignment['confidence']}): {assignment['reason']}"
        )


def _print_render_scaffold_summary(manifest: dict[str, Any]) -> None:
    artifacts = manifest["artifacts"]
    output = manifest["output"]
    print(f"Render scaffold written to: {artifacts['manifest_path']}")
    print(f"Filtergraph: {artifacts['filtergraph_path']}")
    print(f"Command file: {artifacts['command_path']}")
    print(f"Target media: {output['path']}")
    print(f"Output duration: {output['duration_seconds']:.3f} s")


def _print_premiere_export_summary(summary: dict[str, Any]) -> None:
    output = summary["output"]
    details = summary["summary"]
    mode = output.get("mode", "flat-cut")
    print(f"Premiere XML written to: {output['path']}")
    print(f"Sequence name: {output['project_name']}")
    print(f"Mode: {mode}")
    if "angles" in details:
        print(f"Multicam angles: {details['angles']}")
    if "video_assets" in details:
        print(
            f"Assets: {details['video_assets']} video, "
            f"{details['video_segments']} video segments, "
            f"{details['audio_segments']} audio segments"
        )
    elif "video_segments" in details:
        print(
            f"Segments: {details['video_segments']} video, "
            f"{details['audio_segments']} audio"
        )
    print(f"Output duration: {details['duration_seconds']:.3f} s")


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
        f"words: {summary.get('word_count', 0)}, "
        f"chars: {summary['character_count']}"
    )
    if transcript_artifact.get("language"):
        print(f"Language: {transcript_artifact['language']}")


def _print_cut_validation_summary(report: dict[str, Any], output_path: Path) -> None:
    summary = report["summary"]
    print(f"Cut validation written to: {output_path}")
    print(
        f"Cuts: {summary['cuts_total']} total, "
        f"{summary['ok']} ok, {summary['warn']} warn, {summary['fail']} fail"
    )
    print(f"Repairable cuts: {summary['repairable']}")


def _print_visual_packet_summary(packet: dict[str, Any], output_path: Path) -> None:
    summary = packet["summary"]
    print(f"Visual packet written to: {output_path}")
    print(
        f"Mode: {summary['mode']}, "
        f"windows: {summary['window_count']}, "
        f"images: {summary['image_count']}"
    )
    print(f"Selected assets: {', '.join(summary['selected_assets'])}")
    role_counts = summary["role_image_counts"]
    print(
        "Role images: "
        f"close {role_counts['close']}, "
        f"halbtotale {role_counts['halbtotale']}, "
        f"totale {role_counts['totale']}, "
        f"unknown {role_counts['unknown']}"
    )


def _print_sample_set_summary(sample_set: dict[str, Any], output_dir: Path) -> None:
    summary = sample_set["summary"]
    print(f"Sample set written to: {output_dir}")
    print(
        f"Windows: {summary['window_count']}, "
        f"camera files: {summary['camera_files']}, "
        f"duration per window: {summary['duration_seconds']:.1f} s"
    )


def _resolve_optional_artifact_path(
    explicit_path: str | None,
    artifact_payload: dict[str, Any] | None,
) -> str | None:
    if explicit_path:
        return explicit_path
    if not isinstance(artifact_payload, dict):
        return None
    candidate = artifact_payload.get("path")
    return candidate if isinstance(candidate, str) and candidate else None


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

    if args.command == "plan" and args.plan_command in {"baseline", "draft"}:
        try:
            sync_map = load_json_artifact(args.sync_map)
            analysis_map = None if not args.analysis else load_analysis_map(args.analysis)
            transcript_artifact = None if not args.transcript else load_transcript_artifact(args.transcript)
            builder = build_baseline_cut_plan if args.plan_command == "baseline" else build_draft_cut_plan
            cut_plan = builder(
                sync_map,
                source_sync_map_path=args.sync_map,
                analysis_map=analysis_map,
                source_analysis_path=args.analysis,
                transcript_artifact=transcript_artifact,
                source_transcript_path=args.transcript,
                options=None if args.plan_command == "baseline" else _build_draft_plan_options(args),
            )
            output_path = write_cut_plan(cut_plan, args.out)
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")

        if args.json:
            print(json.dumps(cut_plan, indent=2))
        else:
            _print_cut_plan_summary(cut_plan, output_path)
        return 0

    if args.command == "plan" and args.plan_command == "validate":
        try:
            cut_plan = load_cut_plan(args.cut_plan)
            sync_map_path = _resolve_optional_artifact_path(args.sync_map, cut_plan.get("source_sync_map"))
            analysis_path = _resolve_optional_artifact_path(args.analysis, cut_plan.get("source_analysis_map"))
            transcript_path = _resolve_optional_artifact_path(args.transcript, cut_plan.get("source_transcript"))
            sync_map = None if not sync_map_path else load_json_artifact(sync_map_path)
            analysis_map = None if not analysis_path else load_analysis_map(analysis_path)
            transcript_artifact = None if not transcript_path else load_transcript_artifact(transcript_path)
            report = build_cut_validation_report(
                cut_plan,
                sync_map=sync_map,
                source_cut_plan_path=args.cut_plan,
                source_sync_map_path=sync_map_path,
                analysis_map=analysis_map,
                source_analysis_path=analysis_path,
                transcript_artifact=transcript_artifact,
                source_transcript_path=transcript_path,
                options=_build_cut_validation_options(args),
            )
            output_path = write_cut_validation_report(report, args.out)
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")

        if args.json:
            print(json.dumps(report, indent=2))
        else:
            _print_cut_validation_summary(report, output_path)
        return 0 if report["summary"]["fail"] == 0 else 1

    if args.command == "plan" and args.plan_command == "repair":
        try:
            cut_plan = load_cut_plan(args.cut_plan)
            validation_report = load_cut_validation_report(args.validation)
            sync_map_path = _resolve_optional_artifact_path(args.sync_map, cut_plan.get("source_sync_map"))
            analysis_path = _resolve_optional_artifact_path(args.analysis, cut_plan.get("source_analysis_map"))
            transcript_path = _resolve_optional_artifact_path(args.transcript, cut_plan.get("source_transcript"))
            sync_map = None if not sync_map_path else load_json_artifact(sync_map_path)
            analysis_map = None if not analysis_path else load_analysis_map(analysis_path)
            transcript_artifact = None if not transcript_path else load_transcript_artifact(transcript_path)
            repaired_cut_plan = repair_cut_plan(
                cut_plan,
                validation_report,
                sync_map=sync_map,
                source_cut_plan_path=args.cut_plan,
                source_validation_path=args.validation,
                analysis_map=analysis_map,
                transcript_artifact=transcript_artifact,
                options=CutValidationOptions(repair_min_segment_seconds=args.repair_min_segment),
            )
            output_path = write_cut_plan(repaired_cut_plan, args.out)
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")

        if args.json:
            print(json.dumps(repaired_cut_plan, indent=2))
        else:
            _print_cut_plan_summary(repaired_cut_plan, output_path)
        return 0

    if args.command == "plan" and args.plan_command == "ai-draft":
        try:
            sync_map = load_json_artifact(args.sync_map)
            visual_packet = load_visual_packet(args.visual_packet)
            analysis_map = None if not args.analysis else load_analysis_map(args.analysis)
            transcript_artifact = None if not args.transcript else load_transcript_artifact(args.transcript)
            ai_cut_plan = build_ai_draft_cut_plan(
                sync_map,
                source_sync_map_path=args.sync_map,
                visual_packet=visual_packet,
                source_visual_packet_path=args.visual_packet,
                analysis_map=analysis_map,
                source_analysis_path=args.analysis,
                transcript_artifact=transcript_artifact,
                source_transcript_path=args.transcript,
                options=_build_ai_draft_options(args),
            )
            output_path = write_cut_plan(ai_cut_plan, args.out)
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")

        if args.json:
            print(json.dumps(ai_cut_plan, indent=2))
        else:
            _print_cut_plan_summary(ai_cut_plan, output_path)
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

    if args.command == "analyze" and args.analyze_command == "roles":
        try:
            sync_map = load_json_artifact(args.sync_map)
            camera_role_artifact = build_camera_role_artifact_from_sync_map(
                sync_map,
                output_dir=args.out_dir,
                options=_build_camera_role_options(args),
                source_sync_map_path=args.sync_map,
            )
            output_path = write_camera_role_artifact(camera_role_artifact, args.out)
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")

        if args.json:
            print(json.dumps(camera_role_artifact, indent=2))
        else:
            _print_camera_role_summary(camera_role_artifact, output_path)
        return 0

    if args.command == "analyze" and args.analyze_command == "visuals":
        try:
            sync_map = load_json_artifact(args.sync_map)
            analysis_map = None if not args.analysis else load_analysis_map(args.analysis)
            transcript_artifact = None if not args.transcript else load_transcript_artifact(args.transcript)
            cut_plan = None if not args.cut_plan else load_cut_plan(args.cut_plan)
            visual_packet = build_visual_packet(
                sync_map,
                source_sync_map_path=args.sync_map,
                analysis_map=analysis_map,
                source_analysis_path=args.analysis,
                transcript_artifact=transcript_artifact,
                source_transcript_path=args.transcript,
                cut_plan=cut_plan,
                source_cut_plan_path=args.cut_plan,
                output_dir=args.out_dir,
                options=_build_visual_packet_options(args),
            )
            output_path = write_visual_packet(visual_packet, args.out)
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")

        if args.json:
            print(json.dumps(visual_packet, indent=2))
        else:
            _print_visual_packet_summary(visual_packet, output_path)
        return 0 if visual_packet["summary"]["image_count"] > 0 else 1

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

    if args.command == "export" and args.export_command == "premiere":
        mode = getattr(args, "mode", "flat-cut")
        try:
            if mode == "sync-multicam":
                if not args.sync_map:
                    parser.exit(1, "VAZer error: --sync-map is required for sync-multicam mode.\n")
                sync_map = load_json_artifact(args.sync_map)
                summary = export_premiere_sync_multicam_xml(
                    sync_map,
                    output_xml_path=args.out,
                    project_name=args.name,
                )
            elif mode == "multicam-cut":
                if not args.sync_map:
                    parser.exit(1, "VAZer error: --sync-map is required for multicam-cut mode.\n")
                if not args.cut_plan:
                    parser.exit(1, "VAZer error: --cut-plan is required for multicam-cut mode.\n")
                cut_plan = load_cut_plan(args.cut_plan)
                sync_map = load_json_artifact(args.sync_map)
                summary = export_premiere_multicam_cut_xml(
                    cut_plan,
                    sync_map=sync_map,
                    output_xml_path=args.out,
                    cut_plan_path=args.cut_plan,
                    sync_map_path=args.sync_map,
                    project_name=args.name,
                )
            else:
                if not args.cut_plan:
                    parser.exit(1, "VAZer error: --cut-plan is required for flat-cut mode.\n")
                cut_plan = load_cut_plan(args.cut_plan)
                summary = export_premiere_xml(
                    cut_plan,
                    output_xml_path=args.out,
                    cut_plan_path=args.cut_plan,
                    project_name=args.name,
                )
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")

        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            _print_premiere_export_summary(summary)
        return 0

    if args.command == "sample" and args.sample_command == "set":
        try:
            sync_map = load_json_artifact(args.sync_map)
            sample_set = build_sample_set(
                sync_map,
                source_sync_map_path=args.sync_map,
                output_dir=args.out_dir,
                options=_build_sample_set_options(args),
            )
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")

        if args.json:
            print(json.dumps(sample_set, indent=2))
        else:
            _print_sample_set_summary(sample_set, Path(args.out_dir))
        return 0

    if args.command == "ui" and args.ui_command == "serve":
        try:
            serve_ui(
                host=args.host,
                port=args.port,
                workspace=args.workspace,
                open_browser=args.open_browser,
            )
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")
        return 0

    if args.command == "desktop":
        try:
            return int(launch_desktop_app(workspace=args.workspace))
        except Exception as error:  # pragma: no cover - CLI surface
            parser.exit(1, f"VAZer error: {error}\n")

    parser.print_help()
    return 1
