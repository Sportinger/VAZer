from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
import os
import subprocess
from typing import Any, Iterable, Iterator

import cv2
import numpy as np

from .fftools import probe_media
from .process_manager import popen_managed, unregister_process

EPSILON = 1e-6


@dataclass(slots=True)
class StreamRequest:
    path: str
    sample_fps: float
    max_width: int
    max_height: int | None = None
    decoder_preference: str = "auto"
    prefer_gpu: bool = True
    prefer_opencv_fallback: bool = True


@dataclass(slots=True)
class StreamFrame:
    index: int
    timestamp_seconds: float
    frame: np.ndarray


def _even(value: float) -> int:
    integer = max(2, int(round(value)))
    return integer if integer % 2 == 0 else integer + 1


def _decode_duration(path: str) -> tuple[float, dict[str, Any]]:
    media_info = probe_media(path)
    duration_seconds = media_info.duration_seconds
    if duration_seconds is None or duration_seconds <= 0:
        raise ValueError(f"Unable to determine usable duration for {path}.")
    metadata = {
        "source_width": media_info.video_streams[0].width if media_info.video_streams else None,
        "source_height": media_info.video_streams[0].height if media_info.video_streams else None,
        "source_fps": media_info.video_streams[0].frame_rate if media_info.video_streams else None,
        "duration_seconds": float(duration_seconds),
        "format_name": media_info.format_name,
    }
    return float(duration_seconds), metadata


@lru_cache(maxsize=1)
def probe_ffmpeg_hwaccels() -> tuple[str, ...]:
    try:
        completed = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ()

    accelerations: list[str] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line or line.lower() == "hardware acceleration methods:":
            continue
        accelerations.append(line.lower())
    return tuple(accelerations)


def resolve_sample_fps(*, sample_fps: float | None = None, sample_interval_seconds: float | None = None) -> float:
    if sample_interval_seconds is not None and sample_interval_seconds > 0:
        return 1.0 / float(sample_interval_seconds)
    if sample_fps is not None and sample_fps > 0:
        return float(sample_fps)
    raise ValueError("Either sample_fps or sample_interval_seconds must be provided.")


def compute_target_dimensions(
    source_width: int | None,
    source_height: int | None,
    *,
    max_width: int,
    max_height: int | None = None,
) -> tuple[int, int]:
    width = max(2, int(source_width or max_width))
    height = max(2, int(source_height or max_width))
    target_width = min(max_width, width)
    if max_height is not None:
        target_height = min(max_height, height)
        if target_height <= 0:
            target_height = height
        scale = min(target_width / width, target_height / height)
    else:
        scale = target_width / width
    if scale <= 0:
        scale = 1.0
    return _even(width * scale), _even(height * scale)


def _build_ffmpeg_filters(
    *,
    sample_fps: float,
    target_width: int,
    target_height: int,
    use_hwaccel: bool,
) -> list[str]:
    base_filter = f"fps={sample_fps:.8f},scale={target_width}:{target_height}:flags=fast_bilinear,format=gray"
    if not use_hwaccel:
        return ["-vf", base_filter]

    # Keep the graph conservative: download to CPU frames before scaling.
    hw_filter = (
        f"hwdownload,format=gray,fps={sample_fps:.8f},"
        f"scale={target_width}:{target_height}:flags=fast_bilinear,format=gray"
    )
    return ["-vf", hw_filter]


def _ffmpeg_command(
    path: str,
    *,
    sample_fps: float,
    target_width: int,
    target_height: int,
    hwaccel: str | None,
) -> list[str]:
    args = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
    ]
    if hwaccel is not None:
        args.extend(["-hwaccel", hwaccel])
        if hwaccel == "cuda":
            args.extend(["-hwaccel_output_format", "cuda"])
    args.extend(
        [
            "-i",
            path,
            "-an",
            "-sn",
            "-dn",
        ]
    )
    args.extend(_build_ffmpeg_filters(
        sample_fps=sample_fps,
        target_width=target_width,
        target_height=target_height,
        use_hwaccel=hwaccel is not None,
    ))
    args.extend([
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "pipe:1",
    ])
    return args


def _opencv_commandless_frames(
    path: str,
    *,
    sample_fps: float,
    target_width: int,
    target_height: int,
    on_progress: Any | None = None,
) -> tuple[Iterator[StreamFrame], dict[str, Any]]:
    duration_seconds, metadata = _decode_duration(path)
    total_samples = max(1, int(math.ceil(duration_seconds * sample_fps)))
    source_fps = metadata.get("source_fps") or sample_fps
    frames_per_sample = max(1, int(round(float(source_fps) / sample_fps)))
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise ValueError(f"OpenCV could not open {path}.")

    def _iterator() -> Iterator[StreamFrame]:
        frame_index = 0
        sample_index = 0
        try:
            if callable(on_progress):
                on_progress(0.0, "Opening OpenCV fallback stream.")
            while True:
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                if frame_index % frames_per_sample == 0:
                    timestamp_seconds = min(duration_seconds, sample_index / sample_fps)
                    grayscale = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    if grayscale.shape[1] != target_width or grayscale.shape[0] != target_height:
                        grayscale = cv2.resize(grayscale, (target_width, target_height), interpolation=cv2.INTER_AREA)
                    yield StreamFrame(
                        index=sample_index + 1,
                        timestamp_seconds=timestamp_seconds,
                        frame=grayscale,
                    )
                    sample_index += 1
                    if callable(on_progress):
                        on_progress(
                            min(100.0, 100.0 * sample_index / total_samples),
                            f"Sampling frame {sample_index}/{total_samples}",
                        )
                frame_index += 1
        finally:
            capture.release()

    metadata.update(
        {
            "decoder_family": "opencv",
            "decoder_method": "opencv_sequential",
            "decoder_acceleration": "cpu",
            "sample_fps": float(sample_fps),
            "sample_interval_seconds": 1.0 / sample_fps,
            "sample_width": target_width,
            "sample_height": target_height,
            "requested_samples": total_samples,
        }
    )
    return _iterator(), metadata


class SequentialGrayFrameReader:
    def __init__(self, path: str, request: StreamRequest, *, on_progress: Any | None = None) -> None:
        self.path = path
        self.request = request
        self.on_progress = on_progress
        self.metadata: dict[str, Any] = {}
        self._process: subprocess.Popen[Any] | None = None
        self._reader: Any = None
        self._stderr: Any = None
        self._frame_size_bytes: int = 0
        self._duration_seconds = 0.0
        self._total_samples = 0
        self._mode = "ffmpeg"
        self._attempts: list[str | None] = []

    def __enter__(self) -> "SequentialGrayFrameReader":
        self._open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
        if process is not None:
            unregister_process(process)
        self._process = None
        self._reader = None
        self._stderr = None

    def __iter__(self) -> Iterator[StreamFrame]:
        if self._reader is None:
            self._open()
        if self._mode == "opencv":
            yield from self._reader
            return
        yield from self._iter_ffmpeg_frames()
        return

    def _open(self) -> None:
        sample_fps = self.request.sample_fps
        duration_seconds, media_metadata = _decode_duration(self.path)
        source_width = media_metadata.get("source_width") or self.request.max_width
        source_height = media_metadata.get("source_height") or self.request.max_width
        target_width, target_height = compute_target_dimensions(
            int(source_width),
            int(source_height),
            max_width=self.request.max_width,
            max_height=self.request.max_height,
        )
        self._duration_seconds = duration_seconds
        self._total_samples = max(1, int(math.ceil(duration_seconds * sample_fps)))
        self._frame_size_bytes = target_width * target_height
        self.metadata = {
            **media_metadata,
            "sample_fps": float(sample_fps),
            "sample_interval_seconds": float(1.0 / sample_fps),
            "sample_width": target_width,
            "sample_height": target_height,
            "requested_samples": self._total_samples,
            "decoder_family": "ffmpeg",
            "decoder_method": None,
            "decoder_acceleration": None,
            "decode_attempts": [],
        }

        hwaccels = set(probe_ffmpeg_hwaccels())
        normalized = str(self.request.decoder_preference or "auto").strip().lower()
        attempts: list[str | None] = []
        if normalized == "cpu":
            attempts = [None]
        elif normalized == "cuda":
            attempts = ["cuda"] if "cuda" in hwaccels else []
            attempts.append(None)
        else:
            if self.request.prefer_gpu:
                if "cuda" in hwaccels:
                    attempts.append("cuda")
                if "auto" in hwaccels:
                    attempts.append("auto")
            attempts.append(None)
        self._attempts = attempts
        if not self._attempts:
            raise ValueError(f"Unable to open decode stream for {self.path}.")

    def _iter_ffmpeg_attempt(self, hwaccel: str | None) -> Iterator[StreamFrame]:
        cmd = _ffmpeg_command(
            self.path,
            sample_fps=self.request.sample_fps,
            target_width=int(self.metadata["sample_width"]),
            target_height=int(self.metadata["sample_height"]),
            hwaccel=hwaccel,
        )
        self.metadata["decode_attempts"].append(
            {
                "method": "ffmpeg",
                "hwaccel": hwaccel or "cpu",
                "command": cmd[:],
            }
        )
        try:
            process = popen_managed(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as error:
            raise ValueError(str(error)) from error

        self._process = process
        self._reader = process.stdout
        self._stderr = process.stderr
        self.metadata["decoder_method"] = "ffmpeg_hwaccel" if hwaccel else "ffmpeg_cpu"
        self.metadata["decoder_acceleration"] = "gpu" if hwaccel else "cpu"
        sample_index = 0
        sample_height = int(self.metadata["sample_height"])
        sample_width = int(self.metadata["sample_width"])
        total_samples = self._total_samples
        stream_exhausted = False
        if callable(self.on_progress):
            self.on_progress(0.0, f"Opening FFmpeg stream ({hwaccel or 'cpu'}).")

        try:
            while True:
                chunk = self._reader.read(self._frame_size_bytes)
                if not chunk:
                    break
                if len(chunk) < self._frame_size_bytes:
                    break
                sample_index += 1
                frame = np.frombuffer(chunk, dtype=np.uint8).reshape((sample_height, sample_width))
                timestamp_seconds = min(self._duration_seconds, (sample_index - 1) / self.request.sample_fps)
                yield StreamFrame(index=sample_index, timestamp_seconds=timestamp_seconds, frame=frame)
                if callable(self.on_progress):
                    self.on_progress(
                        min(100.0, 100.0 * sample_index / total_samples),
                        f"Sampling frame {sample_index}/{total_samples}",
                    )
            stream_exhausted = True
        finally:
            return_code = process.wait()
            stderr_text = ""
            if self._stderr is not None:
                try:
                    stderr_bytes = self._stderr.read() or b""
                    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
                except Exception:
                    stderr_text = ""
            unregister_process(process)
            self._process = None
            self._reader = None
            self._stderr = None
            if stream_exhausted and return_code != 0:
                raise ValueError(f"FFmpeg decode failed for {self.path}: {stderr_text or 'no decoder output'}")

    def _iter_ffmpeg_frames(self) -> Iterator[StreamFrame]:
        last_error: str | None = None
        for hwaccel in self._attempts:
            yielded_any = False
            try:
                for frame in self._iter_ffmpeg_attempt(hwaccel):
                    yielded_any = True
                    yield frame
                return
            except ValueError as error:
                last_error = str(error)
                if yielded_any:
                    raise
                continue

        if self.request.prefer_opencv_fallback:
            iterator, metadata = _opencv_commandless_frames(
                self.path,
                sample_fps=self.request.sample_fps,
                target_width=int(self.metadata["sample_width"]),
                target_height=int(self.metadata["sample_height"]),
                on_progress=self.on_progress,
            )
            self._mode = "opencv"
            self._reader = iterator
            self.metadata.update(metadata)
            self.metadata["decode_attempts"].append(
                {
                    "method": "opencv",
                    "hwaccel": "cpu",
                }
            )
            yield from self._reader
            return

        if last_error is not None:
            raise ValueError(last_error)
        raise ValueError(f"Unable to open decode stream for {self.path}.")

    def read_all(self) -> list[StreamFrame]:
        return list(iter(self))
