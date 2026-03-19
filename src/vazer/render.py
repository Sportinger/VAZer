from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
from typing import Any

from . import __version__
from .fftools import probe_media
from .process_manager import popen_managed, unregister_process


_ENCODER_AVAILABILITY_CACHE: dict[str, bool] = {}
_FILTER_AVAILABILITY_CACHE: dict[str, bool] = {}
_HWACCEL_AVAILABILITY_CACHE: dict[str, bool] = {}


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_cut_plan(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _format_seconds(value: float) -> str:
    return f"{value:.6f}"


def _ffmpeg_has_encoder(name: str) -> bool:
    normalized = str(name or "").strip()
    if not normalized:
        return False
    cached = _ENCODER_AVAILABILITY_CACHE.get(normalized)
    if cached is not None:
        return cached

    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        available = result.returncode == 0 and normalized in (result.stdout or "")
    except Exception:
        available = False
    _ENCODER_AVAILABILITY_CACHE[normalized] = available
    return available


def _ffmpeg_has_filter(name: str) -> bool:
    normalized = str(name or "").strip()
    if not normalized:
        return False
    cached = _FILTER_AVAILABILITY_CACHE.get(normalized)
    if cached is not None:
        return cached

    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        available = result.returncode == 0 and normalized in (result.stdout or "")
    except Exception:
        available = False
    _FILTER_AVAILABILITY_CACHE[normalized] = available
    return available


def _ffmpeg_has_hwaccel(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    if not normalized:
        return False
    cached = _HWACCEL_AVAILABILITY_CACHE.get(normalized)
    if cached is not None:
        return cached

    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        available = result.returncode == 0 and normalized in (result.stdout or "").lower()
    except Exception:
        available = False
    _HWACCEL_AVAILABILITY_CACHE[normalized] = available
    return available


def _resolve_video_codec(requested_codec: str) -> str:
    normalized = str(requested_codec or "").strip().lower()
    if normalized in {"", "auto", "default", "libx264"}:
        normalized = "h264_nvenc"
    if not _ffmpeg_has_encoder(normalized):
        raise ValueError(
            f"Requested GPU encoder '{normalized}' is not available in ffmpeg. "
            "CPU fallback is currently disabled."
        )
    return normalized


def _video_codec_args(codec: str) -> list[str]:
    if codec == "h264_nvenc":
        return ["-preset", "p5", "-cq", "21", "-b:v", "0"]
    return []


def _cuda_surface_format(pixel_format: str) -> str:
    normalized = str(pixel_format or "").strip().lower()
    if normalized in {"", "yuv420p", "nv12"}:
        return "nv12"
    if normalized == "p010le":
        return "p010le"
    raise ValueError(
        f"Requested output pixel format '{pixel_format}' is not supported by the strict CUDA render path."
    )


def _resolve_render_pipeline(video_codec: str, pixel_format: str) -> dict[str, Any]:
    use_cuda_video = video_codec == "h264_nvenc"
    if not use_cuda_video:
        return {
            "video_path": "cpu",
            "input_args": [],
            "cuda_surface_format": None,
            "encoder_pixel_format": pixel_format,
        }

    missing: list[str] = []
    if not _ffmpeg_has_hwaccel("cuda"):
        missing.append("hwaccel cuda")
    if not _ffmpeg_has_filter("scale_cuda"):
        missing.append("scale_cuda")
    if not _ffmpeg_has_filter("pad_cuda"):
        missing.append("pad_cuda")
    if missing:
        raise ValueError(
            "Strict CUDA render path is enabled, but ffmpeg is missing required CUDA components: "
            + ", ".join(missing)
        )

    return {
        "video_path": "cuda",
        "input_args": ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-extra_hw_frames", "8"],
        "cuda_surface_format": _cuda_surface_format(pixel_format),
        "encoder_pixel_format": _cuda_surface_format(pixel_format),
    }


def _audio_intermediate_extension(audio_codec: str) -> str:
    normalized = str(audio_codec or "").strip().lower()
    if normalized in {"aac", "libfdk_aac"}:
        return ".m4a"
    if normalized in {"pcm_s16le", "pcm_s24le", "pcm_f32le"}:
        return ".wav"
    return ".mka"


def _build_audio_filtergraph(cut_plan: dict[str, Any]) -> str:
    audio_segments = cut_plan.get("audio_segments", [])
    if not audio_segments:
        raise ValueError("cut_plan does not contain any audio segments.")

    lines: list[str] = []
    for index, segment in enumerate(audio_segments, start=1):
        lines.append(
            f"[0:a]"
            f"atrim=start={_format_seconds(segment['source_start_seconds'])}:"
            f"end={_format_seconds(segment['source_end_seconds'])},"
            "asetpts=PTS-STARTPTS"
            f"[a{index}]"
        )

    audio_concat_inputs = "".join(f"[a{index}]" for index in range(1, len(audio_segments) + 1))
    lines.append(f"{audio_concat_inputs}concat=n={len(audio_segments)}:v=0:a=1[aout]")
    return ";\n".join(lines) + "\n"


def _build_segment_video_filter_chain(
    segment: dict[str, Any],
    *,
    render_defaults: dict[str, Any],
    render_pipeline: dict[str, Any],
) -> str:
    width = int(render_defaults["width"])
    height = int(render_defaults["height"])
    fps = float(render_defaults["fps"])
    pixel_format = str(render_defaults["pixel_format"])
    use_cuda_video = render_pipeline.get("video_path") == "cuda"
    cuda_surface_format = render_pipeline.get("cuda_surface_format")
    video_filters = [
        f"setpts=(PTS-STARTPTS)/{segment['speed']:.12f}",
        f"fps={fps:.6f}",
    ]
    if use_cuda_video:
        video_filters.extend(
            [
                f"scale_cuda={width}:{height}:force_original_aspect_ratio=decrease:format={cuda_surface_format}",
                f"pad_cuda={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            ]
        )
    else:
        video_filters.extend(
            [
                f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
                f"format={pixel_format}",
            ]
        )
    return ",".join(video_filters)


def _segment_output_filename(index: int) -> str:
    return f"segment_{index:04d}.mp4"


def _concat_list_entry(path: Path) -> str:
    escaped = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{escaped}'"


def _command_line_text(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def _validate_video_segments(video_segments: list[dict[str, Any]]) -> None:
    duration_cache: dict[str, float] = {}
    invalid_segments: list[str] = []
    for segment in video_segments:
        asset_path = str(segment.get("asset_path") or "")
        if not asset_path:
            invalid_segments.append(f"{segment.get('id') or '?'} missing asset_path")
            continue
        duration_seconds = duration_cache.get(asset_path)
        if duration_seconds is None:
            media_info = probe_media(asset_path)
            duration_seconds = float(media_info.duration_seconds or 0.0)
            duration_cache[asset_path] = duration_seconds
        try:
            source_start_seconds = float(segment.get("source_start_seconds") or 0.0)
            source_end_seconds = float(segment.get("source_end_seconds") or 0.0)
        except (TypeError, ValueError):
            invalid_segments.append(f"{segment.get('id') or '?'} has non-numeric source bounds")
            continue
        if duration_seconds <= 0:
            invalid_segments.append(f"{segment.get('id') or '?'} {Path(asset_path).name} has unknown duration")
            continue
        if source_start_seconds < -0.01:
            invalid_segments.append(
                f"{segment.get('id') or '?'} {Path(asset_path).name} starts before 0s ({source_start_seconds:.3f}s)"
            )
        if source_end_seconds - source_start_seconds <= 0.01:
            invalid_segments.append(
                f"{segment.get('id') or '?'} {Path(asset_path).name} has non-positive span "
                f"({source_start_seconds:.3f}s..{source_end_seconds:.3f}s)"
            )
        if source_start_seconds >= duration_seconds - 0.01:
            invalid_segments.append(
                f"{segment.get('id') or '?'} {Path(asset_path).name} starts after clip end "
                f"({source_start_seconds:.3f}s >= {duration_seconds:.3f}s)"
            )
        if source_end_seconds > duration_seconds + 0.25:
            invalid_segments.append(
                f"{segment.get('id') or '?'} {Path(asset_path).name} ends after clip end "
                f"({source_end_seconds:.3f}s > {duration_seconds:.3f}s)"
            )
    if invalid_segments:
        preview = "; ".join(invalid_segments[:6])
        extra = "" if len(invalid_segments) <= 6 else f"; plus {len(invalid_segments) - 6} more"
        raise ValueError(
            "Cut plan contains source ranges outside the available media. "
            + preview
            + extra
            + ". Rebuild the cut plan instead of reusing the stale artifact."
        )


def _stage_command_text(manifest: dict[str, Any]) -> str:
    lines: list[str] = []
    for segment in manifest.get("segments", []):
        lines.append(f"[segment {segment['index']}/{len(manifest.get('segments', []))}]")
        lines.append(str(segment["ffmpeg"]["command_line"]))
        lines.append("")
    for key in ("audio", "concat", "mux"):
        stage = manifest.get(key)
        if not isinstance(stage, dict):
            continue
        lines.append(f"[{key}]")
        lines.append(str(stage["ffmpeg"]["command_line"]))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _run_ffmpeg_command(
    command: list[str],
    *,
    overwrite: bool,
    loglevel: str,
    output_duration_seconds: float | None = None,
    on_progress: Any | None = None,
    state: str,
) -> dict[str, Any]:
    argv = list(command)
    extra = ["-hide_banner", "-loglevel", loglevel, "-nostats", "-progress", "pipe:1"]
    if overwrite:
        extra.append("-y")
    else:
        extra.append("-n")
    argv[1:1] = extra

    process = popen_managed(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    latest_progress = 0.0
    progress_payload: dict[str, str] = {}
    stderr_output = ""
    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            progress_payload[key] = value
            if key not in {"out_time_ms", "out_time_us", "progress"}:
                continue

            out_time_seconds = None
            if "out_time_us" in progress_payload:
                try:
                    out_time_seconds = float(progress_payload["out_time_us"]) / 1_000_000.0
                except ValueError:
                    out_time_seconds = None
            elif "out_time_ms" in progress_payload:
                try:
                    out_time_seconds = float(progress_payload["out_time_ms"]) / 1_000_000.0
                except ValueError:
                    out_time_seconds = None

            if out_time_seconds is not None and output_duration_seconds and output_duration_seconds > 0:
                latest_progress = min(100.0, max(0.0, (out_time_seconds / output_duration_seconds) * 100.0))
                if callable(on_progress):
                    on_progress(latest_progress, state)

        if process.stderr is not None:
            stderr_output = process.stderr.read()
        return_code = process.wait()
    finally:
        unregister_process(process)

    if return_code != 0:
        raise RuntimeError(stderr_output.strip() or f"ffmpeg exited with code {return_code}.")
    return {
        "return_code": return_code,
        "progress_percent": latest_progress,
    }


def build_render_scaffold(
    cut_plan: dict[str, Any],
    *,
    cut_plan_path: str | None,
    output_media_path: str,
    scaffold_dir: str,
) -> dict[str, Any]:
    if cut_plan.get("schema_version") != "vazer.cut_plan.v1":
        raise ValueError("Unsupported cut_plan schema version.")

    video_segments = cut_plan.get("video_segments", [])
    audio_segments = cut_plan.get("audio_segments", [])
    if not video_segments:
        raise ValueError("cut_plan does not contain any video segments.")
    if not audio_segments:
        raise ValueError("cut_plan does not contain any audio segments.")
    _validate_video_segments(video_segments)

    master_path = cut_plan["master_audio"]["path"]
    output_path = Path(output_media_path)
    scaffold_root = Path(scaffold_dir)
    scaffold_root.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    stem = output_path.stem or "vazer_render"
    segments_root = scaffold_root / f"{stem}.segments"
    segments_root.mkdir(parents=True, exist_ok=True)
    audio_filtergraph_path = scaffold_root / f"{stem}.audio.filtergraph.txt"
    concat_list_path = scaffold_root / f"{stem}.concat.txt"
    command_path = scaffold_root / f"{stem}.ffmpeg.txt"
    manifest_path = scaffold_root / f"{stem}.render.json"
    concat_video_path = scaffold_root / f"{stem}.video.concat.mp4"
    audio_output_path = scaffold_root / f"{stem}.audio{_audio_intermediate_extension(str(cut_plan['render_defaults']['audio_codec']))}"

    render_defaults = json.loads(json.dumps(cut_plan["render_defaults"]))
    resolved_video_codec = _resolve_video_codec(str(render_defaults.get("video_codec") or ""))
    render_defaults["video_codec"] = resolved_video_codec
    render_pipeline = _resolve_render_pipeline(resolved_video_codec, str(render_defaults["pixel_format"]))
    audio_filtergraph = _build_audio_filtergraph(cut_plan)
    audio_filtergraph_path.write_text(audio_filtergraph, encoding="utf-8")

    segment_payloads: list[dict[str, Any]] = []
    concat_entries: list[str] = []
    for index, segment in enumerate(video_segments, start=1):
        segment_output_path = segments_root / _segment_output_filename(index)
        segment_command = [
            "ffmpeg",
            "-ss",
            _format_seconds(float(segment["source_start_seconds"])),
            "-t",
            _format_seconds(float(segment["duration_seconds"])),
            *render_pipeline["input_args"],
            "-i",
            str(segment["asset_path"]),
            "-an",
            "-vf",
            _build_segment_video_filter_chain(
                segment,
                render_defaults=render_defaults,
                render_pipeline=render_pipeline,
            ),
            "-c:v",
            resolved_video_codec,
            *_video_codec_args(resolved_video_codec),
        ]
        if render_pipeline["video_path"] != "cuda":
            segment_command.extend(["-pix_fmt", str(render_pipeline["encoder_pixel_format"])])
        segment_command.append(str(segment_output_path))
        segment_payloads.append(
            {
                "index": index,
                "segment_id": str(segment.get("id") or f"segment_{index}"),
                "asset_id": str(segment.get("asset_id") or ""),
                "asset_path": str(segment["asset_path"]),
                "source_start_seconds": float(segment["source_start_seconds"]),
                "source_end_seconds": float(segment["source_end_seconds"]),
                "duration_seconds": float(segment["duration_seconds"]),
                "speed": float(segment["speed"]),
                "output_path": str(segment_output_path),
                "ffmpeg": {
                    "argv": segment_command,
                    "command_line": _command_line_text(segment_command),
                },
            }
        )
        concat_entries.append(_concat_list_entry(segment_output_path))
    concat_list_path.write_text("\n".join(concat_entries) + "\n", encoding="utf-8")

    audio_command = [
        "ffmpeg",
        "-i",
        master_path,
        "-filter_complex_script",
        str(audio_filtergraph_path),
        "-map",
        "[aout]",
        "-c:a",
        str(render_defaults["audio_codec"]),
        str(audio_output_path),
    ]
    concat_command = [
        "ffmpeg",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-c",
        "copy",
        str(concat_video_path),
    ]
    mux_command = [
        "ffmpeg",
        "-i",
        str(concat_video_path),
        "-i",
        str(audio_output_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    manifest = {
        "schema_version": "vazer.render_scaffold.v2",
        "generated_at_utc": _utc_timestamp(),
        "tool": {
            "name": "vazer",
            "version": __version__,
        },
        "source_cut_plan": {
            "schema_version": cut_plan["schema_version"],
            "path": cut_plan_path,
        },
        "inputs": [
            {
                "input_index": 0,
                "role": "master_audio",
                "path": master_path,
            },
            *[
                {
                    "input_index": index,
                    "role": "video_asset",
                    "path": path,
                }
                for index, path in enumerate(
                    list(dict.fromkeys(str(segment["asset_path"]) for segment in video_segments)),
                    start=1,
                )
            ],
        ],
        "output": {
            "path": str(output_path),
            "duration_seconds": cut_plan["timeline"]["output_duration_seconds"],
            "render_defaults": render_defaults,
            "render_pipeline": render_pipeline,
            "render_strategy": "segmented_v1",
        },
        "artifacts": {
            "filtergraph_path": str(audio_filtergraph_path),
            "audio_filtergraph_path": str(audio_filtergraph_path),
            "concat_list_path": str(concat_list_path),
            "segments_dir": str(segments_root),
            "audio_output_path": str(audio_output_path),
            "concat_video_path": str(concat_video_path),
            "command_path": str(command_path),
            "manifest_path": str(manifest_path),
        },
        "segments": segment_payloads,
        "audio": {
            "output_path": str(audio_output_path),
            "duration_seconds": cut_plan["timeline"]["output_duration_seconds"],
            "ffmpeg": {
                "argv": audio_command,
                "command_line": _command_line_text(audio_command),
            },
        },
        "concat": {
            "output_path": str(concat_video_path),
            "ffmpeg": {
                "argv": concat_command,
                "command_line": _command_line_text(concat_command),
            },
        },
        "mux": {
            "output_path": str(output_path),
            "ffmpeg": {
                "argv": mux_command,
                "command_line": _command_line_text(mux_command),
            },
        },
        "ffmpeg": {
            "strategy": "segmented_v1",
            "command_line": "See per-stage commands in artifacts.command_path.",
        },
    }

    command_path.write_text(_stage_command_text(manifest), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def apply_max_render_size(
    cut_plan: dict[str, Any],
    *,
    max_width: int,
    max_height: int,
) -> dict[str, Any]:
    adjusted = json.loads(json.dumps(cut_plan))
    render_defaults = adjusted.setdefault("render_defaults", {})
    current_width = int(render_defaults.get("width") or max_width)
    current_height = int(render_defaults.get("height") or max_height)

    scale_ratio = min(1.0, max_width / max(1, current_width), max_height / max(1, current_height))
    target_width = max(2, int(round(current_width * scale_ratio / 2.0) * 2))
    target_height = max(2, int(round(current_height * scale_ratio / 2.0) * 2))

    render_defaults["width"] = target_width
    render_defaults["height"] = target_height
    return adjusted


def run_render(
    manifest: dict[str, Any],
    *,
    overwrite: bool = True,
    loglevel: str = "error",
    on_progress: Any | None = None,
) -> dict[str, Any]:
    render_strategy = str((manifest.get("output") or {}).get("render_strategy") or "segmented_v1")
    if render_strategy != "segmented_v1":
        raise ValueError(f"Unsupported render strategy: {render_strategy}")

    segments = manifest.get("segments") or []
    if not isinstance(segments, list) or not segments:
        raise ValueError("Render manifest does not contain any video segments.")
    _validate_video_segments(segments)

    output_duration_seconds = float(manifest["output"]["duration_seconds"])
    total_video_duration = sum(max(0.0, float(segment.get("duration_seconds") or 0.0)) for segment in segments) or output_duration_seconds
    video_weight = 88.0
    concat_weight = 2.0
    audio_weight = 7.0
    mux_weight = 3.0
    latest_progress = 0.0
    completed_video_duration = 0.0

    for index, segment in enumerate(segments, start=1):
        segment_duration = max(0.001, float(segment.get("duration_seconds") or 0.0))
        base_percent = (completed_video_duration / total_video_duration) * video_weight
        span_percent = (segment_duration / total_video_duration) * video_weight

        def _segment_progress(progress_percent: float, _state: str, *, _index: int = index) -> None:
            nonlocal latest_progress
            latest_progress = min(100.0, base_percent + span_percent * (progress_percent / 100.0))
            if callable(on_progress):
                on_progress(latest_progress, f"segment {_index}/{len(segments)}")

        _run_ffmpeg_command(
            list(segment["ffmpeg"]["argv"]),
            overwrite=overwrite,
            loglevel=loglevel,
            output_duration_seconds=segment_duration,
            on_progress=_segment_progress,
            state=f"segment {index}/{len(segments)}",
        )
        completed_video_duration += segment_duration

    latest_progress = video_weight
    if callable(on_progress):
        on_progress(latest_progress, "concat")
    _run_ffmpeg_command(
        list(manifest["concat"]["ffmpeg"]["argv"]),
        overwrite=overwrite,
        loglevel=loglevel,
        output_duration_seconds=None,
        on_progress=None,
        state="concat",
    )

    latest_progress = video_weight + concat_weight
    if callable(on_progress):
        on_progress(latest_progress, "audio")

    def _audio_progress(progress_percent: float, _state: str) -> None:
        nonlocal latest_progress
        latest_progress = min(100.0, video_weight + concat_weight + audio_weight * (progress_percent / 100.0))
        if callable(on_progress):
            on_progress(latest_progress, "audio")

    _run_ffmpeg_command(
        list(manifest["audio"]["ffmpeg"]["argv"]),
        overwrite=overwrite,
        loglevel=loglevel,
        output_duration_seconds=output_duration_seconds,
        on_progress=_audio_progress,
        state="audio",
    )

    latest_progress = video_weight + concat_weight + audio_weight
    if callable(on_progress):
        on_progress(latest_progress, "mux")
    _run_ffmpeg_command(
        list(manifest["mux"]["ffmpeg"]["argv"]),
        overwrite=overwrite,
        loglevel=loglevel,
        output_duration_seconds=None,
        on_progress=None,
        state="mux",
    )

    if callable(on_progress):
        on_progress(100.0, "end")
    return {
        "return_code": 0,
        "progress_percent": 100.0,
        "output_path": manifest["output"]["path"],
    }
