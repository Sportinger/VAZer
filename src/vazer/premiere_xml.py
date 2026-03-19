from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
import xml.etree.ElementTree as ET

from . import __version__
from .fftools import probe_media


@dataclass(slots=True)
class _FrameRate:
    fps: float
    timebase: int
    ntsc: bool


@dataclass(slots=True)
class _VideoSource:
    file_id: str
    path: Path
    name: str
    rate: _FrameRate
    duration_frames: int
    width: int
    height: int


@dataclass(slots=True)
class _AudioSource:
    file_id: str
    path: Path
    name: str
    rate: _FrameRate
    duration_frames: int
    sample_rate: int
    channel_count: int


@dataclass(slots=True)
class _AngleData:
    asset_id: str
    source: _VideoSource
    overlap_start_seconds: float
    overlap_end_seconds: float
    source_in_seconds: float
    source_out_seconds: float


def _audio_layout(channel_count: int) -> str | None:
    if channel_count <= 1:
        return "mono"
    if channel_count == 2:
        return "stereo"
    return None


def _audio_channel_description(channel_count: int) -> str | None:
    if channel_count <= 1:
        return "mono"
    if channel_count == 2:
        return "stereo"
    return None


def _audio_channel_labels(channel_count: int) -> list[str]:
    if channel_count <= 1:
        return ["discrete"]
    if channel_count == 2:
        return ["left", "right"]
    return ["discrete" for _ in range(channel_count)]


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _pathurl(path: Path) -> str:
    resolved = path.expanduser().resolve()
    normalized = resolved.as_posix()
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return f"file://localhost{quote(normalized, safe='/:')}"


def _text(parent: ET.Element, tag: str, value: Any | None = None, **attrs: Any) -> ET.Element:
    element = ET.SubElement(parent, tag, {key: str(val) for key, val in attrs.items() if val is not None})
    if value is not None:
        element.text = str(value)
    return element


def _normalized_rate(value: float | None, *, fallback_fps: float) -> _FrameRate:
    fps = float(value or fallback_fps)
    known_rates = (
        (23.976, 24, True),
        (24.0, 24, False),
        (25.0, 25, False),
        (29.97, 30, True),
        (30.0, 30, False),
        (50.0, 50, False),
        (59.94, 60, True),
        (60.0, 60, False),
    )
    for known_fps, timebase, ntsc in known_rates:
        if abs(fps - known_fps) <= 0.01:
            return _FrameRate(fps=known_fps, timebase=timebase, ntsc=ntsc)
    return _FrameRate(fps=fps, timebase=max(1, int(round(fps))), ntsc=False)


def _seconds_to_frames(value: float, rate: _FrameRate) -> int:
    return max(0, int(round(float(value) * rate.fps)))


def _append_rate(parent: ET.Element, rate: _FrameRate) -> ET.Element:
    node = _text(parent, "rate")
    _text(node, "timebase", rate.timebase)
    _text(node, "ntsc", "TRUE" if rate.ntsc else "FALSE")
    return node


def _append_sequence_timecode(parent: ET.Element, rate: _FrameRate) -> ET.Element:
    node = _text(parent, "timecode")
    _append_rate(node, rate)
    _text(node, "string", "00:00:00:00")
    _text(node, "frame", 0)
    _text(node, "displayformat", "NDF")
    return node


def _append_video_samplecharacteristics(
    parent: ET.Element,
    *,
    rate: _FrameRate,
    width: int,
    height: int,
) -> ET.Element:
    node = _text(parent, "samplecharacteristics")
    _append_rate(node, rate)
    _text(node, "width", width)
    _text(node, "height", height)
    _text(node, "anamorphic", "FALSE")
    _text(node, "pixelaspectratio", "square")
    _text(node, "fielddominance", "none")
    _text(node, "colordepth", 24)
    return node


def _append_audio_samplecharacteristics(parent: ET.Element, *, sample_rate: int) -> ET.Element:
    node = _text(parent, "samplecharacteristics")
    _text(node, "depth", 16)
    _text(node, "samplerate", sample_rate)
    return node


def _append_audio_channel_metadata(parent: ET.Element, *, channel_count: int) -> None:
    _text(parent, "channelcount", channel_count)
    description = _audio_channel_description(channel_count)
    if description is not None:
        _text(parent, "channeldescription", description)
    layout = _audio_layout(channel_count)
    if layout is not None:
        _text(parent, "layout", layout)
    for index, label in enumerate(_audio_channel_labels(channel_count), start=1):
        audio_channel = _text(parent, "audiochannel")
        _text(audio_channel, "channellabel", label)
        _text(audio_channel, "sourcechannel", index)


def _append_audio_outputs(parent: ET.Element, *, channel_count: int) -> None:
    outputs = _text(parent, "outputs")
    group = _text(outputs, "group")
    _text(group, "index", 1)
    _text(group, "numchannels", channel_count)
    if channel_count == 2:
        _text(group, "downmix", 4)
    for index in range(1, channel_count + 1):
        channel = _text(group, "channel")
        _text(channel, "index", index)


def _video_source_from_segments(
    asset_path: str,
    *,
    file_id: str,
    fallback_fps: float,
    fallback_width: int,
    fallback_height: int,
    source_end_seconds: float,
) -> _VideoSource:
    path = Path(asset_path).expanduser().resolve()
    media_info = probe_media(str(path))
    stream = media_info.video_streams[0] if media_info.video_streams else None
    if stream is None:
        raise ValueError(f"No video stream found in '{path}'.")

    rate = _normalized_rate(stream.frame_rate, fallback_fps=fallback_fps)
    duration_seconds = max(
        float(stream.duration_seconds or 0.0),
        float(media_info.duration_seconds or 0.0),
        float(source_end_seconds),
    )
    return _VideoSource(
        file_id=file_id,
        path=path,
        name=path.name,
        rate=rate,
        duration_frames=_seconds_to_frames(duration_seconds, rate),
        width=int(stream.width or fallback_width),
        height=int(stream.height or fallback_height),
    )


def _video_source_from_sync_entry(
    entry: dict[str, Any],
    *,
    file_id: str,
    fallback_fps: float,
    fallback_width: int,
    fallback_height: int,
) -> _VideoSource:
    path = Path(entry["path"]).expanduser().resolve()
    media = entry.get("media") or {}
    primary_video = media.get("primary_video") or {}
    rate = _normalized_rate(primary_video.get("frame_rate"), fallback_fps=fallback_fps)
    duration_seconds = max(
        float(primary_video.get("duration_seconds") or 0.0),
        float(media.get("duration_seconds") or 0.0),
    )
    return _VideoSource(
        file_id=file_id,
        path=path,
        name=path.name,
        rate=rate,
        duration_frames=_seconds_to_frames(duration_seconds, rate),
        width=int(primary_video.get("width") or fallback_width),
        height=int(primary_video.get("height") or fallback_height),
    )


def _audio_source_from_cut_plan(
    cut_plan: dict[str, Any],
    *,
    file_id: str,
    sequence_rate: _FrameRate,
) -> _AudioSource:
    path = Path(str(cut_plan["master_audio"]["path"])).expanduser().resolve()
    media_info = probe_media(str(path))
    stream = media_info.audio_streams[0] if media_info.audio_streams else None
    if stream is None:
        raise ValueError(f"No audio stream found in '{path}'.")

    audio_segments = [segment for segment in cut_plan.get("audio_segments") or [] if isinstance(segment, dict)]
    max_source_end = max((float(segment["source_end_seconds"]) for segment in audio_segments), default=0.0)
    duration_seconds = max(
        float(stream.duration_seconds or 0.0),
        float(media_info.duration_seconds or 0.0),
        float(max_source_end),
    )
    return _AudioSource(
        file_id=file_id,
        path=path,
        name=path.name,
        rate=sequence_rate,
        duration_frames=_seconds_to_frames(duration_seconds, sequence_rate),
        sample_rate=max(1, int(stream.sample_rate or 48_000)),
        channel_count=max(1, int(stream.channels or 2)),
    )


def _audio_source_from_sync_map(
    sync_map: dict[str, Any],
    *,
    file_id: str,
    sequence_rate: _FrameRate,
) -> _AudioSource:
    master = sync_map["master"]
    path = Path(str(master["path"])).expanduser().resolve()
    media_info = probe_media(str(path))
    stream = media_info.audio_streams[0] if media_info.audio_streams else None
    if stream is None:
        raise ValueError(f"No audio stream found in '{path}'.")
    duration_seconds = max(
        float(stream.duration_seconds or 0.0),
        float(media_info.duration_seconds or 0.0),
        float(master.get("duration_seconds") or 0.0),
    )
    return _AudioSource(
        file_id=file_id,
        path=path,
        name=path.name,
        rate=sequence_rate,
        duration_frames=_seconds_to_frames(duration_seconds, sequence_rate),
        sample_rate=max(1, int(stream.sample_rate or 48_000)),
        channel_count=max(1, int(stream.channels or 2)),
    )


def _angles_from_sync_map(
    sync_map: dict[str, Any],
    *,
    fallback_fps: float,
    fallback_width: int,
    fallback_height: int,
) -> list[_AngleData]:
    master_duration = float(sync_map["master"]["duration_seconds"])
    angles: list[_AngleData] = []
    for index, entry in enumerate(
        (e for e in sync_map.get("entries", []) if e.get("status") == "synced"),
        start=1,
    ):
        mapping = entry["mapping"]
        speed = float(mapping["speed"])
        offset = float(mapping["offset_seconds"])
        media = entry.get("media") or {}
        camera_duration = float(media.get("duration_seconds") or 0.0)
        overlap_start = max(0.0, -offset / speed)
        overlap_end = min(master_duration, (camera_duration - offset) / speed)
        if overlap_end - overlap_start <= 0.001:
            continue
        source_in = max(0.0, speed * overlap_start + offset)
        source_out = min(camera_duration, speed * overlap_end + offset)
        source = _video_source_from_sync_entry(
            entry,
            file_id=f"video-file-{index}",
            fallback_fps=fallback_fps,
            fallback_width=fallback_width,
            fallback_height=fallback_height,
        )
        angles.append(_AngleData(
            asset_id=entry["asset_id"],
            source=source,
            overlap_start_seconds=overlap_start,
            overlap_end_seconds=overlap_end,
            source_in_seconds=source_in,
            source_out_seconds=source_out,
        ))
    return angles


def _append_video_file_reference(
    parent: ET.Element,
    source: _VideoSource,
    *,
    emitted_file_ids: set[str],
) -> None:
    file_element = ET.SubElement(parent, "file", {"id": source.file_id})
    _text(file_element, "name", source.name)
    _text(file_element, "pathurl", _pathurl(source.path))
    if source.file_id in emitted_file_ids:
        return
    emitted_file_ids.add(source.file_id)
    _append_rate(file_element, source.rate)
    _text(file_element, "duration", source.duration_frames)
    media = _text(file_element, "media")
    video = _text(media, "video")
    _text(video, "duration", source.duration_frames)
    _text(video, "trackcount", 1)
    _append_video_samplecharacteristics(video, rate=source.rate, width=source.width, height=source.height)


def _append_audio_file_reference(
    parent: ET.Element,
    source: _AudioSource,
    *,
    emitted_file_ids: set[str],
) -> None:
    file_element = ET.SubElement(parent, "file", {"id": source.file_id})
    _text(file_element, "name", source.name)
    _text(file_element, "pathurl", _pathurl(source.path))
    if source.file_id in emitted_file_ids:
        return
    emitted_file_ids.add(source.file_id)
    _append_rate(file_element, source.rate)
    _text(file_element, "duration", source.duration_frames)
    media = _text(file_element, "media")
    audio = _text(media, "audio")
    _append_rate(audio, source.rate)
    _text(audio, "duration", source.duration_frames)
    _append_audio_samplecharacteristics(audio, sample_rate=source.sample_rate)
    _append_audio_channel_metadata(audio, channel_count=source.channel_count)


def _append_multiclip(
    parent: ET.Element,
    *,
    multiclip_id: str,
    name: str,
    sequence_rate: _FrameRate,
    angles: list[_AngleData],
    active_asset_id: str | None,
    emitted_file_ids: set[str],
) -> ET.Element:
    mc = ET.SubElement(parent, "multiclip", {"id": multiclip_id})
    _text(mc, "name", name)
    _text(mc, "collapsed", "FALSE")
    _text(mc, "synctype", 1)
    active_index = _active_angle_index(angles, active_asset_id)
    for index, angle in enumerate(angles):
        angle_el = _text(mc, "angle")
        _text(angle_el, "activevideoangle", "TRUE" if index == active_index else "FALSE")
        clip = ET.SubElement(angle_el, "clip", {"id": f"angle-clip-{index + 1}"})
        _text(clip, "name", angle.source.name)
        _text(clip, "duration", angle.source.duration_frames)
        _append_rate(clip, angle.source.rate)
        _text(clip, "in", -1)
        _text(clip, "out", -1)
        _text(clip, "ismasterclip", "FALSE")
        clip_media = _text(clip, "media")
        clip_video = _text(clip_media, "video")
        clip_track = _text(clip_video, "track")
        ci = ET.SubElement(clip_track, "clipitem", {"id": f"angle-{index + 1}-clipitem"})
        _text(ci, "name", angle.source.name)
        _append_rate(ci, angle.source.rate)
        _text(ci, "duration", angle.source.duration_frames)
        _text(ci, "start", _seconds_to_frames(angle.overlap_start_seconds, sequence_rate))
        _text(ci, "end", _seconds_to_frames(angle.overlap_end_seconds, sequence_rate))
        _text(ci, "in", _seconds_to_frames(angle.source_in_seconds, angle.source.rate))
        _text(ci, "out", _seconds_to_frames(angle.source_out_seconds, angle.source.rate))
        _append_video_file_reference(ci, angle.source, emitted_file_ids=emitted_file_ids)
    return mc


def _append_multiclip_ref(
    parent: ET.Element,
    *,
    multiclip_id: str,
    angles: list[_AngleData],
    active_asset_id: str | None,
) -> ET.Element:
    mc = ET.SubElement(parent, "multiclip", {"id": multiclip_id})
    active_index = _active_angle_index(angles, active_asset_id)
    for index, _angle in enumerate(angles):
        angle_el = _text(mc, "angle")
        _text(angle_el, "activevideoangle", "TRUE" if index == active_index else "FALSE")
        ET.SubElement(angle_el, "clip", {"id": f"angle-clip-{index + 1}"})
    return mc


def _active_angle_index(angles: list[_AngleData], asset_id: str | None) -> int:
    if asset_id:
        for i, angle in enumerate(angles):
            if angle.asset_id == asset_id:
                return i
    return 0


def _resolve_project_name(output_path: Path, project_name: str | None) -> str:
    default_name = output_path.stem
    if default_name.lower().endswith(".premiere"):
        default_name = default_name[: -len(".premiere")]
    return str(project_name or default_name or "VAZer Export").strip() or "VAZer Export"


def _write_xmeml_file(root: ET.Element, output_path: Path) -> None:
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    xml_body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with output_path.open("wb") as handle:
        declaration, separator, remainder = xml_body.partition(b"\n")
        handle.write(declaration)
        handle.write(b"\n<!DOCTYPE xmeml>\n")
        if separator:
            handle.write(remainder)


def export_premiere_xml(
    cut_plan: dict[str, Any],
    *,
    output_xml_path: str,
    cut_plan_path: str | None,
    project_name: str | None = None,
) -> dict[str, Any]:
    if cut_plan.get("schema_version") != "vazer.cut_plan.v1":
        raise ValueError("Unsupported cut_plan schema version.")

    video_segments = list(cut_plan.get("video_segments") or [])
    audio_segments = list(cut_plan.get("audio_segments") or [])
    if not video_segments:
        raise ValueError("cut_plan does not contain any video segments.")
    if not audio_segments:
        raise ValueError("cut_plan does not contain any audio segments.")

    output_path = Path(output_xml_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_project_name = _resolve_project_name(output_path, project_name)

    render_defaults = cut_plan.get("render_defaults") or {}
    fallback_fps = float(render_defaults.get("fps") or 25.0)
    fallback_width = int(render_defaults.get("width") or 1920)
    fallback_height = int(render_defaults.get("height") or 1080)
    sequence_rate = _normalized_rate(fallback_fps, fallback_fps=fallback_fps)

    video_sources: dict[str, _VideoSource] = {}
    for index, asset_path in enumerate(dict.fromkeys(str(segment["asset_path"]) for segment in video_segments), start=1):
        max_source_end = max(
            float(segment["source_end_seconds"])
            for segment in video_segments
            if str(segment["asset_path"]) == asset_path
        )
        video_sources[asset_path] = _video_source_from_segments(
            asset_path,
            file_id=f"video-file-{index}",
            fallback_fps=fallback_fps,
            fallback_width=fallback_width,
            fallback_height=fallback_height,
            source_end_seconds=max_source_end,
        )

    audio_source = _audio_source_from_cut_plan(
        cut_plan,
        file_id="master-audio-file-1",
        sequence_rate=sequence_rate,
    )

    root = ET.Element("xmeml", version="5")
    sequence = ET.SubElement(root, "sequence", {"id": "sequence-1"})
    _text(sequence, "name", resolved_project_name)
    sequence_duration_frames = max(
        _seconds_to_frames(float(cut_plan["timeline"]["output_duration_seconds"]), sequence_rate),
        max(_seconds_to_frames(float(segment["output_end_seconds"]), sequence_rate) for segment in video_segments),
        max(_seconds_to_frames(float(segment["output_end_seconds"]), sequence_rate) for segment in audio_segments),
    )
    _text(sequence, "duration", sequence_duration_frames)
    _append_rate(sequence, sequence_rate)
    _append_sequence_timecode(sequence, sequence_rate)

    media = _text(sequence, "media")

    video = _text(media, "video")
    video_format = _text(video, "format")
    _append_video_samplecharacteristics(
        video_format,
        rate=sequence_rate,
        width=fallback_width,
        height=fallback_height,
    )
    video_track = _text(video, "track")
    emitted_file_ids: set[str] = set()
    for index, segment in enumerate(video_segments, start=1):
        source = video_sources[str(segment["asset_path"])]
        clipitem = ET.SubElement(video_track, "clipitem", {"id": f"video-clipitem-{index}"})
        _text(clipitem, "name", source.name)
        _append_rate(clipitem, source.rate)
        _text(clipitem, "enabled", "TRUE")
        _text(clipitem, "duration", source.duration_frames)
        _text(clipitem, "start", _seconds_to_frames(float(segment["output_start_seconds"]), sequence_rate))
        _text(clipitem, "end", _seconds_to_frames(float(segment["output_end_seconds"]), sequence_rate))
        _text(clipitem, "in", _seconds_to_frames(float(segment["source_start_seconds"]), source.rate))
        _text(clipitem, "out", _seconds_to_frames(float(segment["source_end_seconds"]), source.rate))
        _append_video_file_reference(clipitem, source, emitted_file_ids=emitted_file_ids)
        sourcetrack = _text(clipitem, "sourcetrack")
        _text(sourcetrack, "mediatype", "video")
        _text(sourcetrack, "trackindex", 1)
    _text(video_track, "enabled", "TRUE")
    _text(video_track, "locked", "FALSE")
    _text(video, "trackcount", 1)

    audio = _text(media, "audio")
    audio_format = _text(audio, "format")
    _append_audio_samplecharacteristics(audio_format, sample_rate=audio_source.sample_rate)
    _append_audio_outputs(audio, channel_count=audio_source.channel_count)
    _append_audio_channel_metadata(audio, channel_count=audio_source.channel_count)
    for channel_index in range(1, audio_source.channel_count + 1):
        audio_track = _text(audio, "track")
        for index, segment in enumerate(audio_segments, start=1):
            clipitem = ET.SubElement(audio_track, "clipitem", {"id": f"audio-clipitem-{index}-ch{channel_index}"})
            _text(clipitem, "name", audio_source.name)
            _append_rate(clipitem, audio_source.rate)
            _text(clipitem, "enabled", "TRUE")
            _text(clipitem, "duration", audio_source.duration_frames)
            _text(clipitem, "start", _seconds_to_frames(float(segment["output_start_seconds"]), sequence_rate))
            _text(clipitem, "end", _seconds_to_frames(float(segment["output_end_seconds"]), sequence_rate))
            _text(clipitem, "in", _seconds_to_frames(float(segment["source_start_seconds"]), audio_source.rate))
            _text(clipitem, "out", _seconds_to_frames(float(segment["source_end_seconds"]), audio_source.rate))
            _append_audio_file_reference(clipitem, audio_source, emitted_file_ids=emitted_file_ids)
            sourcetrack = _text(clipitem, "sourcetrack")
            _text(sourcetrack, "mediatype", "audio")
            _text(sourcetrack, "trackindex", channel_index)
        _text(audio_track, "enabled", "TRUE")
        _text(audio_track, "locked", "FALSE")

    _write_xmeml_file(root, output_path)

    return {
        "schema_version": "vazer.premiere_xml.v1",
        "generated_at_utc": _utc_timestamp(),
        "tool": {
            "name": "vazer",
            "version": __version__,
        },
        "source_cut_plan": {
            "schema_version": cut_plan["schema_version"],
            "path": cut_plan_path,
        },
        "output": {
            "path": str(output_path),
            "project_name": resolved_project_name,
            "format": "final_cut_pro_xml",
            "xml_flavor": "xmeml_v5",
        },
        "summary": {
            "video_assets": len(video_sources),
            "video_segments": len(video_segments),
            "audio_segments": len(audio_segments),
            "duration_seconds": float(cut_plan["timeline"]["output_duration_seconds"]),
        },
    }


def export_premiere_sync_multicam_xml(
    sync_map: dict[str, Any],
    *,
    output_xml_path: str,
    project_name: str | None = None,
) -> dict[str, Any]:
    if sync_map.get("schema_version") != "vazer.sync_map.v1":
        raise ValueError("Unsupported sync_map schema version.")

    master = sync_map["master"]
    master_duration_seconds = float(master["duration_seconds"])

    output_path = Path(output_xml_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_project_name = _resolve_project_name(output_path, project_name)

    fallback_fps = 25.0
    fallback_width = 1920
    fallback_height = 1080
    for entry in sync_map.get("entries", []):
        if entry.get("status") == "synced":
            pv = (entry.get("media") or {}).get("primary_video") or {}
            fallback_fps = float(pv.get("frame_rate") or fallback_fps)
            fallback_width = int(pv.get("width") or fallback_width)
            fallback_height = int(pv.get("height") or fallback_height)
            break

    sequence_rate = _normalized_rate(fallback_fps, fallback_fps=fallback_fps)
    angles = _angles_from_sync_map(
        sync_map,
        fallback_fps=fallback_fps,
        fallback_width=fallback_width,
        fallback_height=fallback_height,
    )
    if not angles:
        raise ValueError("sync_map does not contain any synced entries with master overlap.")

    audio_source = _audio_source_from_sync_map(
        sync_map,
        file_id="master-audio-file-1",
        sequence_rate=sequence_rate,
    )

    sequence_duration_frames = _seconds_to_frames(master_duration_seconds, sequence_rate)

    root = ET.Element("xmeml", version="5")
    sequence = ET.SubElement(root, "sequence", {"id": "sequence-1"})
    _text(sequence, "name", resolved_project_name)
    _text(sequence, "duration", sequence_duration_frames)
    _append_rate(sequence, sequence_rate)
    _append_sequence_timecode(sequence, sequence_rate)

    media = _text(sequence, "media")

    video = _text(media, "video")
    video_format = _text(video, "format")
    _append_video_samplecharacteristics(
        video_format,
        rate=sequence_rate,
        width=fallback_width,
        height=fallback_height,
    )
    video_track = _text(video, "track")
    emitted_file_ids: set[str] = set()

    clipitem = ET.SubElement(video_track, "clipitem", {"id": "multicam-clipitem-1"})
    _text(clipitem, "name", resolved_project_name)
    _append_rate(clipitem, sequence_rate)
    _text(clipitem, "enabled", "TRUE")
    _text(clipitem, "duration", sequence_duration_frames)
    _text(clipitem, "start", 0)
    _text(clipitem, "end", sequence_duration_frames)
    _text(clipitem, "in", 0)
    _text(clipitem, "out", sequence_duration_frames)
    _append_multiclip(
        clipitem,
        multiclip_id="multicam-1",
        name=f"{resolved_project_name} Multicam",
        sequence_rate=sequence_rate,
        angles=angles,
        active_asset_id=angles[0].asset_id,
        emitted_file_ids=emitted_file_ids,
    )

    _text(video_track, "enabled", "TRUE")
    _text(video_track, "locked", "FALSE")

    audio = _text(media, "audio")
    audio_format = _text(audio, "format")
    _append_audio_samplecharacteristics(audio_format, sample_rate=audio_source.sample_rate)
    _append_audio_outputs(audio, channel_count=audio_source.channel_count)
    _append_audio_channel_metadata(audio, channel_count=audio_source.channel_count)
    for channel_index in range(1, audio_source.channel_count + 1):
        audio_track = _text(audio, "track")
        clipitem = ET.SubElement(audio_track, "clipitem", {"id": f"audio-clipitem-1-ch{channel_index}"})
        _text(clipitem, "name", audio_source.name)
        _append_rate(clipitem, audio_source.rate)
        _text(clipitem, "enabled", "TRUE")
        _text(clipitem, "duration", audio_source.duration_frames)
        _text(clipitem, "start", 0)
        _text(clipitem, "end", _seconds_to_frames(master_duration_seconds, sequence_rate))
        _text(clipitem, "in", 0)
        _text(clipitem, "out", audio_source.duration_frames)
        _append_audio_file_reference(clipitem, audio_source, emitted_file_ids=emitted_file_ids)
        sourcetrack = _text(clipitem, "sourcetrack")
        _text(sourcetrack, "mediatype", "audio")
        _text(sourcetrack, "trackindex", channel_index)
        _text(audio_track, "enabled", "TRUE")
        _text(audio_track, "locked", "FALSE")

    _write_xmeml_file(root, output_path)

    return {
        "schema_version": "vazer.premiere_xml.v1",
        "generated_at_utc": _utc_timestamp(),
        "tool": {"name": "vazer", "version": __version__},
        "source_sync_map": {"schema_version": sync_map["schema_version"]},
        "output": {
            "path": str(output_path),
            "project_name": resolved_project_name,
            "format": "final_cut_pro_xml",
            "xml_flavor": "xmeml_v5",
            "mode": "sync-multicam",
        },
        "summary": {
            "angles": len(angles),
            "duration_seconds": master_duration_seconds,
        },
    }


def export_premiere_multicam_cut_xml(
    cut_plan: dict[str, Any],
    *,
    sync_map: dict[str, Any],
    output_xml_path: str,
    cut_plan_path: str | None = None,
    sync_map_path: str | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    if cut_plan.get("schema_version") != "vazer.cut_plan.v1":
        raise ValueError("Unsupported cut_plan schema version.")
    if sync_map.get("schema_version") != "vazer.sync_map.v1":
        raise ValueError("Unsupported sync_map schema version.")

    video_segments = list(cut_plan.get("video_segments") or [])
    audio_segments = list(cut_plan.get("audio_segments") or [])
    if not video_segments:
        raise ValueError("cut_plan does not contain any video segments.")
    if not audio_segments:
        raise ValueError("cut_plan does not contain any audio segments.")

    output_path = Path(output_xml_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_project_name = _resolve_project_name(output_path, project_name)

    render_defaults = cut_plan.get("render_defaults") or {}
    fallback_fps = float(render_defaults.get("fps") or 25.0)
    fallback_width = int(render_defaults.get("width") or 1920)
    fallback_height = int(render_defaults.get("height") or 1080)
    sequence_rate = _normalized_rate(fallback_fps, fallback_fps=fallback_fps)

    angles = _angles_from_sync_map(
        sync_map,
        fallback_fps=fallback_fps,
        fallback_width=fallback_width,
        fallback_height=fallback_height,
    )
    if not angles:
        raise ValueError("sync_map does not contain any synced entries with master overlap.")

    audio_source = _audio_source_from_cut_plan(
        cut_plan,
        file_id="master-audio-file-1",
        sequence_rate=sequence_rate,
    )

    master_duration_seconds = float(sync_map["master"]["duration_seconds"])
    sequence_duration_frames = max(
        _seconds_to_frames(float(cut_plan["timeline"]["output_duration_seconds"]), sequence_rate),
        max(_seconds_to_frames(float(s["output_end_seconds"]), sequence_rate) for s in video_segments),
        max(_seconds_to_frames(float(s["output_end_seconds"]), sequence_rate) for s in audio_segments),
    )
    multiclip_duration_frames = _seconds_to_frames(master_duration_seconds, sequence_rate)

    root = ET.Element("xmeml", version="5")
    project = ET.SubElement(root, "project")
    _text(project, "name", resolved_project_name)
    children = _text(project, "children")

    # --- Bin: Multicam source clip (full definition, once) ---
    multicam_name = f"{resolved_project_name} Multicam"
    mc_bin = _text(children, "bin")
    _text(mc_bin, "name", "Multicam Sources")
    bin_children = _text(mc_bin, "children")

    mc_clip = ET.SubElement(bin_children, "clip", {"id": "multicam-master-clip"})
    _text(mc_clip, "name", multicam_name)
    _text(mc_clip, "duration", multiclip_duration_frames)
    _append_rate(mc_clip, sequence_rate)
    _text(mc_clip, "in", -1)
    _text(mc_clip, "out", -1)
    _text(mc_clip, "ismasterclip", "FALSE")
    mc_clip_media = _text(mc_clip, "media")
    mc_clip_video = _text(mc_clip_media, "video")
    mc_clip_track = _text(mc_clip_video, "track")
    mc_clip_item = ET.SubElement(mc_clip_track, "clipitem", {"id": "multicam-source-clipitem"})
    _text(mc_clip_item, "name", multicam_name)
    _append_rate(mc_clip_item, sequence_rate)
    _text(mc_clip_item, "duration", multiclip_duration_frames)
    emitted_file_ids: set[str] = set()
    _append_multiclip(
        mc_clip_item,
        multiclip_id="multicam-1",
        name=multicam_name,
        sequence_rate=sequence_rate,
        angles=angles,
        active_asset_id=angles[0].asset_id,
        emitted_file_ids=emitted_file_ids,
    )

    # --- Sequence: Cut sequence referencing the multiclip ---
    sequence = ET.SubElement(children, "sequence", {"id": "sequence-1"})
    _text(sequence, "name", resolved_project_name)
    _text(sequence, "duration", sequence_duration_frames)
    _append_rate(sequence, sequence_rate)
    _append_sequence_timecode(sequence, sequence_rate)

    media = _text(sequence, "media")

    video = _text(media, "video")
    video_format = _text(video, "format")
    _append_video_samplecharacteristics(
        video_format,
        rate=sequence_rate,
        width=fallback_width,
        height=fallback_height,
    )
    video_track = _text(video, "track")

    for index, segment in enumerate(video_segments, start=1):
        clipitem = ET.SubElement(video_track, "clipitem", {"id": f"multicam-clipitem-{index}"})
        _text(clipitem, "name", multicam_name)
        _append_rate(clipitem, sequence_rate)
        _text(clipitem, "enabled", "TRUE")
        _text(clipitem, "duration", multiclip_duration_frames)
        _text(clipitem, "start", _seconds_to_frames(float(segment["output_start_seconds"]), sequence_rate))
        _text(clipitem, "end", _seconds_to_frames(float(segment["output_end_seconds"]), sequence_rate))
        _text(clipitem, "in", _seconds_to_frames(float(segment["master_start_seconds"]), sequence_rate))
        _text(clipitem, "out", _seconds_to_frames(float(segment["master_end_seconds"]), sequence_rate))
        _append_multiclip_ref(
            clipitem,
            multiclip_id="multicam-1",
            angles=angles,
            active_asset_id=str(segment.get("asset_id") or ""),
        )

    _text(video_track, "enabled", "TRUE")
    _text(video_track, "locked", "FALSE")

    audio = _text(media, "audio")
    audio_format = _text(audio, "format")
    _append_audio_samplecharacteristics(audio_format, sample_rate=audio_source.sample_rate)
    _append_audio_outputs(audio, channel_count=audio_source.channel_count)
    _append_audio_channel_metadata(audio, channel_count=audio_source.channel_count)
    for channel_index in range(1, audio_source.channel_count + 1):
        audio_track = _text(audio, "track")
        for seg_index, segment in enumerate(audio_segments, start=1):
            ci = ET.SubElement(audio_track, "clipitem", {"id": f"audio-clipitem-{seg_index}-ch{channel_index}"})
            _text(ci, "name", audio_source.name)
            _append_rate(ci, audio_source.rate)
            _text(ci, "enabled", "TRUE")
            _text(ci, "duration", audio_source.duration_frames)
            _text(ci, "start", _seconds_to_frames(float(segment["output_start_seconds"]), sequence_rate))
            _text(ci, "end", _seconds_to_frames(float(segment["output_end_seconds"]), sequence_rate))
            _text(ci, "in", _seconds_to_frames(float(segment["source_start_seconds"]), audio_source.rate))
            _text(ci, "out", _seconds_to_frames(float(segment["source_end_seconds"]), audio_source.rate))
            _append_audio_file_reference(ci, audio_source, emitted_file_ids=emitted_file_ids)
            sourcetrack = _text(ci, "sourcetrack")
            _text(sourcetrack, "mediatype", "audio")
            _text(sourcetrack, "trackindex", channel_index)
        _text(audio_track, "enabled", "TRUE")
        _text(audio_track, "locked", "FALSE")

    _write_xmeml_file(root, output_path)

    return {
        "schema_version": "vazer.premiere_xml.v1",
        "generated_at_utc": _utc_timestamp(),
        "tool": {"name": "vazer", "version": __version__},
        "source_cut_plan": {
            "schema_version": cut_plan["schema_version"],
            "path": cut_plan_path,
        },
        "source_sync_map": {
            "schema_version": sync_map["schema_version"],
            "path": sync_map_path,
        },
        "output": {
            "path": str(output_path),
            "project_name": resolved_project_name,
            "format": "final_cut_pro_xml",
            "xml_flavor": "xmeml_v5",
            "mode": "multicam-cut",
        },
        "summary": {
            "angles": len(angles),
            "video_segments": len(video_segments),
            "audio_segments": len(audio_segments),
            "duration_seconds": float(cut_plan["timeline"]["output_duration_seconds"]),
        },
    }
