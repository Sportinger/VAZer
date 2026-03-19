from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
from typing import Any

from . import __version__
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


def _build_filtergraph(
    cut_plan: dict[str, Any],
    input_indexes: dict[str, int],
    *,
    render_pipeline: dict[str, Any],
) -> str:
    render_defaults = cut_plan["render_defaults"]
    width = int(render_defaults["width"])
    height = int(render_defaults["height"])
    fps = float(render_defaults["fps"])
    pixel_format = str(render_defaults["pixel_format"])
    use_cuda_video = render_pipeline.get("video_path") == "cuda"
    cuda_surface_format = render_pipeline.get("cuda_surface_format")

    lines: list[str] = []

    for index, segment in enumerate(cut_plan["video_segments"], start=1):
        input_index = input_indexes[segment["asset_path"]]
        video_filters = [
            f"trim=start={_format_seconds(segment['source_start_seconds'])}:"
            f"end={_format_seconds(segment['source_end_seconds'])}",
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

        lines.append(f"[{input_index}:v]{','.join(video_filters)}[v{index}]")

    video_concat_inputs = "".join(f"[v{index}]" for index in range(1, len(cut_plan["video_segments"]) + 1))
    lines.append(f"{video_concat_inputs}concat=n={len(cut_plan['video_segments'])}:v=1:a=0[vout]")

    for index, segment in enumerate(cut_plan["audio_segments"], start=1):
        lines.append(
            f"[0:a]"
            f"atrim=start={_format_seconds(segment['source_start_seconds'])}:"
            f"end={_format_seconds(segment['source_end_seconds'])},"
            "asetpts=PTS-STARTPTS"
            f"[a{index}]"
        )

    audio_concat_inputs = "".join(f"[a{index}]" for index in range(1, len(cut_plan["audio_segments"]) + 1))
    lines.append(f"{audio_concat_inputs}concat=n={len(cut_plan['audio_segments'])}:v=0:a=1[aout]")

    return ";\n".join(lines) + "\n"


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

    master_path = cut_plan["master_audio"]["path"]
    video_paths: list[str] = []
    for segment in video_segments:
        path = segment["asset_path"]
        if path not in video_paths:
            video_paths.append(path)

    input_indexes = {path: index for index, path in enumerate(video_paths, start=1)}
    output_path = Path(output_media_path)
    scaffold_root = Path(scaffold_dir)
    scaffold_root.mkdir(parents=True, exist_ok=True)

    stem = output_path.stem or "vazer_render"
    filtergraph_path = scaffold_root / f"{stem}.filtergraph.txt"
    command_path = scaffold_root / f"{stem}.ffmpeg.txt"
    manifest_path = scaffold_root / f"{stem}.render.json"

    render_defaults = json.loads(json.dumps(cut_plan["render_defaults"]))
    resolved_video_codec = _resolve_video_codec(str(render_defaults.get("video_codec") or ""))
    render_defaults["video_codec"] = resolved_video_codec
    render_pipeline = _resolve_render_pipeline(resolved_video_codec, str(render_defaults["pixel_format"]))
    filtergraph = _build_filtergraph(cut_plan, input_indexes, render_pipeline=render_pipeline)
    filtergraph_path.write_text(filtergraph, encoding="utf-8")
    command = [
        "ffmpeg",
        "-i",
        master_path,
    ]
    for path in video_paths:
        command.extend([*render_pipeline["input_args"], "-i", path])
    command.extend(
        [
            "-filter_complex_script",
            str(filtergraph_path),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            resolved_video_codec,
            *_video_codec_args(resolved_video_codec),
        ]
    )
    if render_pipeline["video_path"] != "cuda":
        command.extend(["-pix_fmt", str(render_pipeline["encoder_pixel_format"])])
    command.extend(
        [
            "-c:a",
            str(render_defaults["audio_codec"]),
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )

    command_path.write_text(subprocess.list2cmdline(command) + "\n", encoding="utf-8")

    manifest = {
        "schema_version": "vazer.render_scaffold.v1",
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
                    "input_index": input_indexes[path],
                    "role": "video_asset",
                    "path": path,
                }
                for path in video_paths
            ],
        ],
        "output": {
            "path": str(output_path),
            "duration_seconds": cut_plan["timeline"]["output_duration_seconds"],
            "render_defaults": render_defaults,
            "render_pipeline": render_pipeline,
        },
        "artifacts": {
            "filtergraph_path": str(filtergraph_path),
            "command_path": str(command_path),
            "manifest_path": str(manifest_path),
        },
        "ffmpeg": {
            "argv": command,
            "command_line": subprocess.list2cmdline(command),
        },
    }

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
    command = list(manifest["ffmpeg"]["argv"])
    extra = ["-hide_banner", "-loglevel", loglevel, "-nostats", "-progress", "pipe:1"]
    if overwrite:
        extra.append("-y")
    command[1:1] = extra

    output_duration_seconds = float(manifest["output"]["duration_seconds"])
    process = popen_managed(
        command,
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

            if out_time_seconds is not None and output_duration_seconds > 0:
                latest_progress = min(100.0, max(0.0, (out_time_seconds / output_duration_seconds) * 100.0))
                if callable(on_progress):
                    on_progress(latest_progress, progress_payload.get("progress") or "continue")

        if process.stderr is not None:
            stderr_output = process.stderr.read()
        return_code = process.wait()
    finally:
        unregister_process(process)
    if return_code != 0:
        raise RuntimeError(stderr_output.strip() or f"ffmpeg exited with code {return_code}.")

    if callable(on_progress):
        on_progress(100.0, "end")
    return {
        "return_code": return_code,
        "progress_percent": latest_progress,
        "output_path": manifest["output"]["path"],
    }
