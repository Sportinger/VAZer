from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import gzip
from pathlib import Path
import uuid
from typing import Any
import xml.etree.ElementTree as ET

from . import __version__
from .fftools import AudioStreamInfo, VideoStreamInfo, probe_media

TICKS_PER_SECOND = 254_016_000_000
VIDEO_MEDIA_TYPE_ID = "228cda18-3625-4d2d-951e-348879e4ed93"
AUDIO_MEDIA_TYPE_ID = "80b8e3d5-6dca-4195-aefb-cb5f407ab009"
DATA_MEDIA_TYPE_ID = "d8143ffe-eec4-4d2a-a909-d5f7bf094dc5"

PROJECT_CLASS_ID = "62ad66dd-0dcd-42da-a660-6d8fbde94876"
ROOT_PROJECT_ITEM_CLASS_ID = "1c307a89-9318-47d7-a583-bf2553736543"
CLIP_PROJECT_ITEM_CLASS_ID = "cb4e0ed7-aca1-4171-8525-e3658dec06dd"
MASTER_CLIP_CLASS_ID = "fb11c33a-b0a9-4465-aa94-b6d5db2628cf"
CLIP_LOGGING_INFO_CLASS_ID = "77ab7fdd-dcdf-465d-9906-7a330ca1e738"
MARKERS_CLASS_ID = "bee50706-b524-416c-9f03-b596ce5f6866"
VIDEO_CLIP_CLASS_ID = "9308dbef-2440-4acb-9ab2-953b9a4e82ec"
AUDIO_CLIP_CLASS_ID = "b8830d03-de02-41ee-84ec-fe566dc70cd9"
SECONDARY_CONTENT_CLASS_ID = "f9d004b5-cb04-4e2f-af6f-64fadc2c4be9"
MEDIA_CLASS_ID = "7a5c103e-f3ac-4391-b6b4-7cc3d2f9a7ff"
VIDEO_STREAM_CLASS_ID = "a36e4719-3ec6-4a0c-ab11-8b4aab377aa5"
AUDIO_STREAM_CLASS_ID = "0b5cf52f-2b85-4863-890b-8844b64ecfe9"
VIDEO_MEDIA_SOURCE_CLASS_ID = "e64ddf74-8fac-4682-8aa8-0e0ca2248949"
AUDIO_MEDIA_SOURCE_CLASS_ID = "f588da05-fc2a-4fbc-9383-74d653b379e3"
VIDEO_SEQUENCE_SOURCE_CLASS_ID = "4752dfa9-7a7e-4a3b-a25b-cafde1a8d036"
AUDIO_SEQUENCE_SOURCE_CLASS_ID = "e8d4cc83-38cb-491f-9d94-e5f7e3b205ee"
SEQUENCE_CLASS_ID = "6a15d903-8739-11d5-af2d-9b7855ad8974"
VIDEO_TRACK_GROUP_CLASS_ID = "9e9abf7a-0918-49c2-91ae-991b5dde77bb"
AUDIO_TRACK_GROUP_CLASS_ID = "9b9238b9-53a8-4cc3-b03f-b36246d052e6"
DATA_TRACK_GROUP_CLASS_ID = "b714b71d-6838-48dd-9b77-db19088ced7e"
VIDEO_CLIP_TRACK_CLASS_ID = "f68dcd81-8805-11d5-af2d-9bfa89d4ddd4"
AUDIO_CLIP_TRACK_CLASS_ID = "097f6203-99ae-11d5-84f2-8cf14bde7040"
VIDEO_CLIP_TRACK_ITEM_CLASS_ID = "368b0406-29e3-4923-9fcd-094fbf9a1089"
AUDIO_CLIP_TRACK_ITEM_CLASS_ID = "064ec682-9ba6-11d5-af2d-9ca32c7d6164"
SUBCLIP_CLASS_ID = "e0c58dc9-dbdd-4166-aef7-5db7e3f22e84"
VIDEO_COMPONENT_CHAIN_CLASS_ID = "0970e08a-f58f-4108-b29a-1a717b8e12e2"
AUDIO_COMPONENT_CHAIN_CLASS_ID = "3cb131d1-d3c0-47ae-a19a-bdf75ea11674"
AUDIO_MIX_TRACK_CLASS_ID = "4b1d8400-e89e-11d5-abc4-a1a13b1e80a0"
DEFAULT_PAN_PROCESSOR_CLASS_ID = "33a94282-ee2c-11d5-abc4-c1cd7f9e3c10"
STEREO_TO_STEREO_PAN_PROCESSOR_CLASS_ID = "7bf86a01-efbe-11d5-abc4-c1ce2b1e9090"
AUDIO_TRACK_INLET_CLASS_ID = "be3af080-e8c6-11d5-abc4-a1c6d5dee670"
AUDIO_FADER_CLASS_ID = "1a38c583-ed5c-11d5-abc4-c1cbf61ec590"
AUDIO_METER_CLASS_ID = "72ea4700-f615-11d5-abc4-c186585e63e0"
AUDIO_COMPONENT_PARAM_FLOAT_CLASS_ID = "a714635e-a628-4b27-9d59-77eba47dbc1a"
AUDIO_COMPONENT_PARAM_BOOL_CLASS_ID = "32657501-3aa4-445f-a49b-d09ecb9fa1ae"
CLIP_CHANNEL_GROUP_VECTOR_CLASS_ID = "a3127a8c-95d4-456e-a7f5-171b3f922426"
CLIP_CHANNEL_VECTOR_CLASS_ID = "333d203b-3a53-4195-8894-fc7523ff3dc7"
CLIP_CHANNEL_SERIALIZER_CLASS_ID = "5c89aa7a-89a6-4483-becd-f2b1def42316"
PROJECT_SETTINGS_CLASS_ID = "50c16708-a1a1-4d2f-98d5-4e283ae28353"
COMPILE_SETTINGS_CLASS_ID = "18a35d66-597e-4157-b783-938b5bec3547"
VIDEO_SETTINGS_CLASS_ID = "58474264-30c4-43a2-bba5-dc0812df8a3a"
AUDIO_SETTINGS_CLASS_ID = "6baf5521-b132-4634-840e-13cec5bc86a4"
VIDEO_COMPILE_SETTINGS_CLASS_ID = "db372db5-7de2-4d3c-98ae-f42659d77b22"
AUDIO_COMPILE_SETTINGS_CLASS_ID = "34b10007-ab6d-49a7-bac5-7b60d919e387"
DUMMY_CAPTURE_SETTINGS_CLASS_ID = "328c2aa2-47f9-4211-805b-b6a6dbd4ca29"
DEFAULT_SEQUENCE_SETTINGS_CLASS_ID = "567bdf53-d6d9-4d61-b2f1-f4834bebea9b"
SCRATCH_DISK_SETTINGS_CLASS_ID = "4c6ed82b-a81c-4df1-8bd0-750504c4b560"
INGEST_SETTINGS_CLASS_ID = "2db8f76b-2c37-48ee-925d-9a4f7278152d"
WORKSPACE_SETTINGS_CLASS_ID = "c4372273-e1aa-4683-98aa-a2ceadf3066c"

DEFAULT_EDITING_MODE_GUID = "9678af98-a7b7-4bdb-b477-7ac9c8df4a4e"
DEFAULT_PREVIEW_FORMAT_ID = "41384a52-7e4a-3c48-e0ad-4939000000ea"
DEFAULT_VIDEO_CODEC_TYPE = 1_635_150_896
DEFAULT_BUILD_VERSION = "26.0.2x2"
DEFAULT_LANGUAGE_LABEL_COLORS = {
    "master_audio": ("BE.Prefs.LabelColors.2", 480554),
    "sequence": ("BE.Prefs.LabelColors.5", 19005),
    "video_asset": ("BE.Prefs.LabelColors.1", 6769408),
    "video_segment": ("BE.Prefs.LabelColors.1", 6769408),
    "audio_segment": ("BE.Prefs.LabelColors.2", 480554),
}
SOURCE_INFINITE_KEYFRAME_TIME = "-91445760000000000"


@dataclass(slots=True)
class _RefAllocator:
    next_object_id: int = 2

    def alloc_id(self) -> int:
        value = self.next_object_id
        self.next_object_id += 1
        return value

    @staticmethod
    def alloc_uid() -> str:
        return str(uuid.uuid4())


@dataclass(slots=True)
class _VideoAsset:
    path: Path
    name: str
    clip_project_item_uid: str
    master_clip_uid: str
    media_uid: str
    project_order: int
    clip_logging_id: int
    markers_id: int
    video_stream_id: int
    video_media_source_id: int
    master_video_clip_id: int
    stream_info: VideoStreamInfo
    duration_ticks: int


@dataclass(slots=True)
class _AudioAsset:
    path: Path
    name: str
    clip_project_item_uid: str
    master_clip_uid: str
    media_uid: str
    project_order: int
    clip_logging_id: int
    markers_id: int
    audio_stream_id: int
    audio_media_source_id: int
    master_audio_clip_id: int
    audio_component_chain_id: int
    clip_channel_group_id: int
    clip_channel_vector_id: int
    clip_channel_serializer_ids: list[int]
    secondary_content_ids: list[int]
    stream_info: AudioStreamInfo
    duration_ticks: int
    channel_count: int


@dataclass(slots=True)
class _VideoSegmentRef:
    asset: _VideoAsset
    track_item_id: int


@dataclass(slots=True)
class _AudioSegmentRef:
    track_item_id: int


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(parent: ET.Element, tag: str, value: Any | None = None, **attrs: Any) -> ET.Element:
    element = ET.SubElement(parent, tag, {key: str(val) for key, val in attrs.items() if val is not None})
    if value is not None:
        element.text = str(value)
    return element


def _ticks_from_seconds(value: float) -> int:
    return int(round(float(value) * TICKS_PER_SECOND))


def _frame_ticks_from_fps(value: float | None, *, fallback_fps: float) -> int:
    fps = float(value or fallback_fps)
    if fps <= 0:
        fps = fallback_fps
    return max(1, int(round(TICKS_PER_SECOND / fps)))


def _content_state() -> str:
    value = str(uuid.uuid4())
    return f"{value[:-2]}24"


def _encoded_binary_hash(value: str) -> str:
    return base64.b64encode(value.encode("utf-16le")).decode("ascii")


def _random_clip_id() -> str:
    return uuid.uuid4().hex.upper() + uuid.uuid4().hex.upper()


def _path_text(path: Path) -> str:
    return str(path.resolve())


def _relative_media_path(path: Path) -> str:
    return f"..\\..\\..\\..\\{_path_text(path)}"


def _actual_media_path(path: Path) -> str:
    resolved = _path_text(path)
    if resolved.startswith("\\\\?\\"):
        return resolved
    return f"\\\\?\\{resolved}"


def _channel_labels(channel_count: int) -> list[int]:
    if channel_count <= 1:
        return [0]
    if channel_count == 2:
        return [100, 101]
    return [0 for _ in range(channel_count)]


def _channel_layout_text(channel_count: int) -> str:
    labels = _channel_labels(channel_count)
    return "[" + ",".join(f'{{"channellabel":{label}}}' for label in labels) + "]"


def _channel_type(channel_count: int) -> int:
    return 1 if channel_count == 2 else 0


def _source_clip_label(role: str) -> tuple[str, int]:
    return DEFAULT_LANGUAGE_LABEL_COLORS[role]


def _cache_file_path(path: Path, *, suffix: str) -> str:
    roaming = Path.home() / "AppData" / "Roaming" / "Adobe" / "Common"
    date_root = datetime.now().strftime("%Y-%m-%d")
    if suffix == ".cfa":
        return str(roaming / "Media Cache Files" / f"{path.name} 48000{suffix}")
    return str(roaming / "Peak Files" / date_root / f"{path.name} 48000{suffix}")


def _append_component_chain_selection(component_chain: ET.Element, *, active_component_id: int) -> None:
    chain = _text(component_chain, "ComponentChain", Version="3")
    node = _text(chain, "Node", Version="1")
    props = _text(node, "Properties", Version="1")
    _text(props, "MZ.ComponentChain.ActiveComponentID", active_component_id)
    _text(props, "MZ.ComponentChain.ActiveComponentParamIndex", 4_294_967_295)


def _make_markers(nodes: list[ET.Element], refs: _RefAllocator) -> int:
    markers_id = refs.alloc_id()
    markers = ET.Element("Markers", ObjectID=str(markers_id), ClassID=MARKERS_CLASS_ID, Version="4")
    _text(markers, "ByGUID", "byGUID")
    _text(markers, "LastMetadataState", "00000000-0000-0000-0000-000000000000")
    _text(markers, "LastContentState", _content_state())
    nodes.append(markers)
    return markers_id


def _make_default_video_component_chain(nodes: list[ET.Element], refs: _RefAllocator) -> int:
    chain_id = refs.alloc_id()
    component_chain = ET.Element(
        "VideoComponentChain",
        ObjectID=str(chain_id),
        ClassID=VIDEO_COMPONENT_CHAIN_CLASS_ID,
        Version="3",
    )
    _text(component_chain, "DefaultMotion", "true")
    _text(component_chain, "DefaultOpacity", "true")
    _text(component_chain, "DefaultMotionComponentID", 1)
    _text(component_chain, "DefaultOpacityComponentID", 2)
    _append_component_chain_selection(component_chain, active_component_id=2)
    nodes.append(component_chain)
    return chain_id


def _make_empty_video_component_chain(nodes: list[ET.Element], refs: _RefAllocator) -> int:
    chain_id = refs.alloc_id()
    component_chain = ET.Element(
        "VideoComponentChain",
        ObjectID=str(chain_id),
        ClassID=VIDEO_COMPONENT_CHAIN_CLASS_ID,
        Version="3",
    )
    _text(component_chain, "ComponentChain", Version="3")
    nodes.append(component_chain)
    return chain_id


def _make_default_audio_component_chain(
    nodes: list[ET.Element],
    refs: _RefAllocator,
    *,
    channel_count: int,
    include_channel_volume: bool,
) -> int:
    chain_id = refs.alloc_id()
    component_chain = ET.Element(
        "AudioComponentChain",
        ObjectID=str(chain_id),
        ClassID=AUDIO_COMPONENT_CHAIN_CLASS_ID,
        Version="4",
    )
    _text(component_chain, "DefaultVol", "true")
    _text(component_chain, "DefaultVolumeComponentID", 1)
    if include_channel_volume:
        _text(component_chain, "DefaultChannelVolumeComponentID", 2)
    _append_component_chain_selection(component_chain, active_component_id=1)
    if channel_count > 1:
        _text(component_chain, "AudioChannelLayout", _channel_layout_text(channel_count))
        _text(component_chain, "ChannelType", _channel_type(channel_count))
    nodes.append(component_chain)
    return chain_id


def _make_audio_track_components(
    nodes: list[ET.Element],
    refs: _RefAllocator,
    *,
    channel_count: int,
    include_default_selection: bool,
) -> tuple[int, int]:
    chain_id = refs.alloc_id()
    fader_id = refs.alloc_id()
    meter_id = refs.alloc_id()
    panner_id = refs.alloc_id()
    volume_param_id = refs.alloc_id()
    mute_param_id = refs.alloc_id()
    balance_param_id = refs.alloc_id()

    component_chain = ET.Element(
        "AudioComponentChain",
        ObjectID=str(chain_id),
        ClassID=AUDIO_COMPONENT_CHAIN_CLASS_ID,
        Version="4",
    )
    chain = _text(component_chain, "ComponentChain", Version="3")
    if include_default_selection:
        node = _text(chain, "Node", Version="1")
        props = _text(node, "Properties", Version="1")
        _text(props, "MZ.ComponentChain.ActiveComponentID", 1)
        _text(props, "MZ.ComponentChain.ActiveComponentParamIndex", 4_294_967_295)
    components = _text(chain, "Components", Version="1")
    _text(components, "Component", Index="0", ObjectRef=fader_id)
    _text(components, "Component", Index="1", ObjectRef=meter_id)
    _text(component_chain, "AudioChannelLayout", _channel_layout_text(channel_count))
    _text(component_chain, "ChannelType", _channel_type(channel_count))
    nodes.append(component_chain)

    fader = ET.Element("AudioFader", ObjectID=str(fader_id), ClassID=AUDIO_FADER_CLASS_ID, Version="3")
    audio_component = _text(fader, "AudioComponent", Version="3")
    component = _text(audio_component, "Component", Version="7")
    params = _text(component, "Params", Version="1")
    _text(params, "Param", Index="0", ObjectRef=volume_param_id)
    _text(params, "Param", Index="1", ObjectRef=mute_param_id)
    _text(component, "ID", 1)
    _text(audio_component, "AudioChannelLayout", _channel_layout_text(channel_count))
    _text(audio_component, "ChannelType", _channel_type(channel_count))
    _text(audio_component, "FrameRate", _frame_ticks_from_fps(48_000, fallback_fps=48_000))
    _text(audio_component, "AudioComponentType", 1)
    nodes.append(fader)

    meter = ET.Element("AudioMeter", ObjectID=str(meter_id), ClassID=AUDIO_METER_CLASS_ID, Version="2")
    audio_component = _text(meter, "AudioComponent", Version="3")
    component = _text(audio_component, "Component", Version="7")
    _text(component, "ID", 2)
    _text(audio_component, "AudioChannelLayout", _channel_layout_text(channel_count))
    _text(audio_component, "ChannelType", _channel_type(channel_count))
    _text(audio_component, "FrameRate", _frame_ticks_from_fps(48_000, fallback_fps=48_000))
    _text(audio_component, "AudioComponentType", 2)
    nodes.append(meter)

    panner = ET.Element(
        "StereoToStereoPanProcessor",
        ObjectID=str(panner_id),
        ClassID=STEREO_TO_STEREO_PAN_PROCESSOR_CLASS_ID,
        Version="1",
    )
    pan_processor = _text(panner, "PanProcessor", Version="3")
    audio_component = _text(pan_processor, "AudioComponent", Version="3")
    component = _text(audio_component, "Component", Version="7")
    params = _text(component, "Params", Version="1")
    _text(params, "Param", Index="0", ObjectRef=balance_param_id)
    _text(component, "ID", 4_294_967_280)
    _text(audio_component, "AudioChannelLayout", _channel_layout_text(channel_count))
    _text(audio_component, "ChannelType", _channel_type(channel_count))
    _text(audio_component, "FrameRate", _frame_ticks_from_fps(48_000, fallback_fps=48_000))
    _text(audio_component, "AudioComponentType", 0)
    nodes.append(panner)

    volume_param = ET.Element(
        "AudioComponentParam",
        ObjectID=str(volume_param_id),
        ClassID=AUDIO_COMPONENT_PARAM_FLOAT_CLASS_ID,
        Version="10",
    )
    _text(volume_param, "UpperBound", 5.6234130859375)
    _text(volume_param, "RangeLocked", "false")
    _text(volume_param, "UnitsString", "dB")
    _text(volume_param, "Name", "Lautstärke")
    nodes.append(volume_param)

    mute_param = ET.Element(
        "AudioComponentParam",
        ObjectID=str(mute_param_id),
        ClassID=AUDIO_COMPONENT_PARAM_BOOL_CLASS_ID,
        Version="10",
    )
    _text(mute_param, "RangeLocked", "false")
    _text(mute_param, "Name", "Stumm")
    nodes.append(mute_param)

    balance_param = ET.Element(
        "AudioComponentParam",
        ObjectID=str(balance_param_id),
        ClassID=AUDIO_COMPONENT_PARAM_FLOAT_CLASS_ID,
        Version="10",
    )
    _text(balance_param, "StartKeyframe", f"{SOURCE_INFINITE_KEYFRAME_TIME},0.5,0,0,0,0,0,0")
    _text(balance_param, "CurrentValue", 0.5)
    _text(balance_param, "IsInverted", "true")
    _text(balance_param, "Name", "Ausgleich")
    nodes.append(balance_param)

    return chain_id, panner_id


def _build_static_project_objects(
    nodes: list[ET.Element],
    refs: _RefAllocator,
) -> dict[str, Any]:
    project_settings_id = refs.alloc_id()
    compile_ids = [refs.alloc_id() for _ in range(5)]
    scratch_id = refs.alloc_id()
    ingest_id = refs.alloc_id()
    workspace_id = refs.alloc_id()
    project_video_settings_id = refs.alloc_id()
    project_audio_settings_id = refs.alloc_id()
    project_video_compile_id = refs.alloc_id()
    project_audio_compile_id = refs.alloc_id()
    capture_settings_id = refs.alloc_id()
    default_sequence_id = refs.alloc_id()

    compile_video_settings_ids = [refs.alloc_id() for _ in range(5)]
    compile_audio_settings_ids = [refs.alloc_id() for _ in range(5)]
    video_settings_ids = [project_video_settings_id, *[refs.alloc_id() for _ in range(5)]]
    audio_settings_ids = [project_audio_settings_id, *[refs.alloc_id() for _ in range(5)]]

    project_settings = ET.Element(
        "ProjectSettings",
        ObjectID=str(project_settings_id),
        ClassID=PROJECT_SETTINGS_CLASS_ID,
        Version="21",
    )
    _text(project_settings, "VideoSettings", ObjectRef=project_video_settings_id)
    _text(project_settings, "AudioSettings", ObjectRef=project_audio_settings_id)
    _text(project_settings, "VideoCompileSettings", ObjectRef=project_video_compile_id)
    _text(project_settings, "AudioCompileSettings", ObjectRef=project_audio_compile_id)
    _text(project_settings, "CaptureSettings", ObjectRef=capture_settings_id)
    _text(project_settings, "DefaultSequenceSettings", ObjectRef=default_sequence_id)
    _text(project_settings, "VideoTimeDisplay", 102)
    _text(project_settings, "AudioTimeDisplay", 200)
    _text(project_settings, "VideoTimeDisplayInitial", 102)
    _text(project_settings, "ActionSafeWidth", 10)
    _text(project_settings, "ActionSafeHeight", 10)
    _text(project_settings, "TitleSafeWidth", 20)
    _text(project_settings, "TitleSafeHeight", 20)
    _text(project_settings, "ShouldScaleMedia", "false")
    _text(project_settings, "EditingModeID", "00000000-0000-0000-0000-000000000000")
    _text(project_settings, "PreviewFileFormatID", "00000000-0000-0000-0000-000000000000")
    _text(project_settings, "UsePreviewCache", "false")
    _text(
        project_settings,
        "ColorManagementSettings",
        '{"enableLogColorManagement":2,"graphicsWhiteLuminance":203,"lutInterpolationMethod":1}',
    )
    _text(project_settings, "ColorAwareEffectsEnabled", 0)
    nodes.append(project_settings)

    compile_codecs = [1_685_288_560, 1_685_288_560, 1_685_288_560, 1_685_288_558, 1_685_288_558]
    for index, compile_id in enumerate(compile_ids, start=0):
        compile_settings = ET.Element(
            "CompileSettings",
            ObjectID=str(compile_id),
            ClassID=COMPILE_SETTINGS_CLASS_ID,
            Version="4",
        )
        _text(compile_settings, "VideoCompileSettings", ObjectRef=compile_video_settings_ids[index])
        _text(compile_settings, "AudioCompileSettings", ObjectRef=compile_audio_settings_ids[index])
        _text(compile_settings, "CompilerClassIDFourCC", 0 if index < 3 else 1_061_109_567)
        _text(compile_settings, "CompilerFourCC", 0 if index < 3 else 1_096_173_910)
        _text(compile_settings, "ExportVideo", "true")
        _text(compile_settings, "ExportAudio", "true")
        _text(compile_settings, "AddToProjectWhenFinished", "true")
        _text(compile_settings, "BeepWhenFinished", "false")
        _text(compile_settings, "ExportWorkAreaOnly", "false")
        _text(compile_settings, "EmbedProjectLink", "false")
        nodes.append(compile_settings)

    for index, object_id in enumerate(video_settings_ids):
        nodes.append(
            ET.Element(
                "VideoSettings",
                ObjectID=str(object_id),
                ClassID=VIDEO_SETTINGS_CLASS_ID,
                Version="10",
            )
        )
        nodes.append(
            ET.Element(
                "AudioSettings",
                ObjectID=str(audio_settings_ids[index]),
                ClassID=AUDIO_SETTINGS_CLASS_ID,
                Version="8",
            )
        )

    project_video_compile = ET.Element(
        "VideoCompileSettings",
        ObjectID=str(project_video_compile_id),
        ClassID=VIDEO_COMPILE_SETTINGS_CLASS_ID,
        Version="9",
    )
    _text(project_video_compile, "VideoSettings", ObjectRef=project_video_settings_id)
    _text(project_video_compile, "Compressor", 1_685_288_558)
    _text(project_video_compile, "VideoCompilerClassIDFourCC", 1_061_109_567)
    _text(project_video_compile, "VideoFileTypeFourCC", 1_096_173_910)
    _text(project_video_compile, "Depth", 24)
    _text(project_video_compile, "RenderDepth", 0)
    _text(project_video_compile, "Aspect43", "false")
    _text(project_video_compile, "Quality", 100)
    _text(project_video_compile, "UseDataRate", "false")
    _text(project_video_compile, "DataRate", 3500)
    _text(project_video_compile, "ForceRecompress", "true")
    _text(project_video_compile, "ForceRecompressValue", 2)
    _text(project_video_compile, "Deinterlace", "false")
    _text(project_video_compile, "IgnoreVideoFilters", "false")
    _text(project_video_compile, "OptimizeStills", "false")
    _text(project_video_compile, "FramesAtMarkers", "false")
    _text(project_video_compile, "RealTimePreview", "true")
    _text(project_video_compile, "VideoFieldType", 0)
    _text(project_video_compile, "DoKeyframeEveryNFrames", "false")
    _text(project_video_compile, "DoKeyframeEveryNFramesValue", 0)
    _text(project_video_compile, "AddKeyframesAtMarkers", "false")
    _text(project_video_compile, "AddKeyframesAtEdits", "false")
    _text(project_video_compile, "RelativeFrameSize", 1)
    nodes.append(project_video_compile)

    project_audio_compile = ET.Element(
        "AudioCompileSettings",
        ObjectID=str(project_audio_compile_id),
        ClassID=AUDIO_COMPILE_SETTINGS_CLASS_ID,
        Version="6",
    )
    _text(project_audio_compile, "AudioSettings", ObjectRef=project_audio_settings_id)
    _text(project_audio_compile, "SampleType", 3)
    _text(project_audio_compile, "Compressor", 1_380_013_856)
    _text(project_audio_compile, "Interleave", 1)
    nodes.append(project_audio_compile)

    for object_id, audio_compile_id, video_settings_id, audio_settings_id, codec in zip(
        compile_video_settings_ids,
        compile_audio_settings_ids,
        video_settings_ids[1:],
        audio_settings_ids[1:],
        compile_codecs,
        strict=True,
    ):
        video_compile = ET.Element(
            "VideoCompileSettings",
            ObjectID=str(object_id),
            ClassID=VIDEO_COMPILE_SETTINGS_CLASS_ID,
            Version="9",
        )
        _text(video_compile, "VideoSettings", ObjectRef=video_settings_id)
        _text(video_compile, "Compressor", codec)
        _text(video_compile, "VideoCompilerClassIDFourCC", 1_061_109_567)
        _text(video_compile, "VideoFileTypeFourCC", 1_096_173_910)
        _text(video_compile, "Depth", 24)
        _text(video_compile, "RenderDepth", 0)
        _text(video_compile, "Aspect43", "false")
        _text(video_compile, "Quality", 100)
        _text(video_compile, "UseDataRate", "false")
        _text(video_compile, "DataRate", 3500)
        _text(video_compile, "ForceRecompress", "true")
        _text(video_compile, "ForceRecompressValue", 2)
        _text(video_compile, "Deinterlace", "false")
        _text(video_compile, "IgnoreVideoFilters", "false")
        _text(video_compile, "OptimizeStills", "false")
        _text(video_compile, "FramesAtMarkers", "false")
        _text(video_compile, "RealTimePreview", "true")
        _text(video_compile, "VideoFieldType", 0)
        _text(video_compile, "DoKeyframeEveryNFrames", "false")
        _text(video_compile, "DoKeyframeEveryNFramesValue", 0)
        _text(video_compile, "AddKeyframesAtMarkers", "false")
        _text(video_compile, "AddKeyframesAtEdits", "false")
        _text(video_compile, "RelativeFrameSize", 1)
        nodes.append(video_compile)

        audio_compile = ET.Element(
            "AudioCompileSettings",
            ObjectID=str(audio_compile_id),
            ClassID=AUDIO_COMPILE_SETTINGS_CLASS_ID,
            Version="6",
        )
        _text(audio_compile, "AudioSettings", ObjectRef=audio_settings_id)
        _text(audio_compile, "SampleType", 3)
        _text(audio_compile, "Compressor", 1_380_013_856)
        _text(audio_compile, "Interleave", 1)
        nodes.append(audio_compile)

    nodes.append(
        ET.Element(
            "DummyCaptureSettings",
            ObjectID=str(capture_settings_id),
            ClassID=DUMMY_CAPTURE_SETTINGS_CLASS_ID,
            Version="1",
        )
    )

    default_sequence = ET.Element(
        "DefaultSequenceSettings",
        ObjectID=str(default_sequence_id),
        ClassID=DEFAULT_SEQUENCE_SETTINGS_CLASS_ID,
        Version="2",
    )
    _text(default_sequence, "TotalVideoTracks", 1)
    _text(default_sequence, "DefaultAudioStandardMonoTracks", 0)
    _text(default_sequence, "DefaultAudioStandardStereoTracks", 1)
    _text(default_sequence, "DefaultAudioStandard51Tracks", 0)
    _text(default_sequence, "DefaultAudioSubmixMonoTracks", 0)
    _text(default_sequence, "DefaultAudioSubmixStereoTracks", 0)
    _text(default_sequence, "DefaultAudioSubmix51Tracks", 0)
    nodes.append(default_sequence)

    scratch = ET.Element(
        "ScratchDiskSettings",
        ObjectID=str(scratch_id),
        ClassID=SCRATCH_DISK_SETTINGS_CLASS_ID,
        Version="4",
    )
    for tag in (
        "AudioPreviewLocation0",
        "VideoPreviewLocation0",
        "TransferMediaLocation0",
        "DVDEncodingLocation0",
        "CCLibrariesLocation0",
        "AutoSaveLocation0",
        "CapturedVideoLocation0",
        "CapsuleMediaLocation0",
    ):
        _text(scratch, tag, "SameAsProject")
    nodes.append(scratch)

    ingest = ET.Element(
        "IngestSettings",
        ObjectID=str(ingest_id),
        ClassID=INGEST_SETTINGS_CLASS_ID,
        Version="2",
    )
    _text(ingest, "Enabled", "false")
    _text(ingest, "Action", "copy")
    _text(
        ingest,
        "PresetPath",
        r"C:\Program Files\Adobe\Adobe Premiere Pro 2026\Settings\IngestPresets\Copy\Copy With MD5 Verification.epr",
    )
    _text(ingest, "CopyDestination", "SameAsProject")
    _text(ingest, "MachineID", str(uuid.uuid4()))
    nodes.append(ingest)

    nodes.append(
        ET.Element(
            "WorkspaceSettings",
            ObjectID=str(workspace_id),
            ClassID=WORKSPACE_SETTINGS_CLASS_ID,
            Version="1",
        )
    )

    return {
        "project_settings_id": project_settings_id,
        "compile_ids": compile_ids,
        "scratch_id": scratch_id,
        "ingest_id": ingest_id,
        "workspace_id": workspace_id,
    }


def _build_video_asset(
    nodes: list[ET.Element],
    refs: _RefAllocator,
    *,
    path: Path,
    project_order: int,
    fallback_fps: float,
) -> _VideoAsset:
    media_info = probe_media(str(path))
    if not media_info.video_streams:
        raise ValueError(f"Video asset does not expose a video stream: {path}")
    stream = media_info.video_streams[0]
    duration_ticks = _ticks_from_seconds(stream.duration_seconds or media_info.duration_seconds or 0.0)
    if duration_ticks <= 0:
        raise ValueError(f"Video asset does not expose a usable duration: {path}")

    clip_project_item_uid = refs.alloc_uid()
    master_clip_uid = refs.alloc_uid()
    media_uid = refs.alloc_uid()
    clip_logging_id = refs.alloc_id()
    markers_id = _make_markers(nodes, refs)
    video_stream_id = refs.alloc_id()
    video_media_source_id = refs.alloc_id()
    master_video_clip_id = refs.alloc_id()

    clip_logging = ET.Element(
        "ClipLoggingInfo",
        ObjectID=str(clip_logging_id),
        ClassID=CLIP_LOGGING_INFO_CLASS_ID,
        Version="10",
    )
    _text(clip_logging, "ClipID", _random_clip_id())
    _text(clip_logging, "ClipName", path.name)
    _text(clip_logging, "TimecodeFormat", 101)
    _text(clip_logging, "MediaInPoint", 0)
    _text(clip_logging, "MediaOutPoint", duration_ticks)
    _text(clip_logging, "MediaFrameRate", _frame_ticks_from_fps(stream.frame_rate, fallback_fps=fallback_fps))
    nodes.append(clip_logging)

    video_clip = ET.Element(
        "VideoClip",
        ObjectID=str(master_video_clip_id),
        ClassID=VIDEO_CLIP_CLASS_ID,
        Version="11",
    )
    clip = _text(video_clip, "Clip", Version="18")
    node = _text(clip, "Node", Version="1")
    props = _text(node, "Properties", Version="1")
    label_name, label_color = _source_clip_label("video_asset")
    _text(props, "asl.clip.label.color", label_color)
    _text(props, "asl.clip.label.name", label_name)
    marker_owner = _text(clip, "MarkerOwner", Version="1")
    _text(marker_owner, "Markers", ObjectRef=markers_id)
    _text(clip, "Source", ObjectRef=video_media_source_id)
    _text(clip, "ClipID", str(uuid.uuid4()))
    _text(clip, "InUse", "false")
    nodes.append(video_clip)

    media_source = ET.Element(
        "VideoMediaSource",
        ObjectID=str(video_media_source_id),
        ClassID=VIDEO_MEDIA_SOURCE_CLASS_ID,
        Version="2",
    )
    media_source_root = _text(media_source, "MediaSource", Version="4")
    _text(media_source_root, "Content", Version="10")
    _text(media_source_root, "Media", ObjectURef=media_uid)
    _text(media_source, "OriginalDuration", duration_ticks)
    nodes.append(media_source)

    media = ET.Element("Media", ObjectUID=media_uid, ClassID=MEDIA_CLASS_ID, Version="30")
    _text(media, "VideoStream", ObjectRef=video_stream_id)
    binary_hash = _content_state()
    _text(
        media,
        "ModificationState",
        _encoded_binary_hash(binary_hash),
        Encoding="base64",
        BinaryHash=binary_hash,
    )
    _text(media, "RelativePath", _relative_media_path(path))
    _text(media, "Start", 0)
    _text(media, "FilePath", _path_text(path))
    _text(media, "ImplementationID", "1fa18bfa-255c-44b1-ad73-56bcd99fceaf")
    _text(media, "Title", path.name)
    _text(media, "FileKey", str(uuid.uuid4()))
    _text(media, "AlternateStart", 0)
    _text(media, "ContentAndMetadataState", _content_state())
    _text(media, "ActualMediaFilePath", _actual_media_path(path))
    nodes.append(media)

    video_stream = ET.Element(
        "VideoStream",
        ObjectID=str(video_stream_id),
        ClassID=VIDEO_STREAM_CLASS_ID,
        Version="22",
    )
    width = int(stream.width or 1920)
    height = int(stream.height or 1080)
    _text(video_stream, "FrameRate", _frame_ticks_from_fps(stream.frame_rate, fallback_fps=fallback_fps))
    _text(video_stream, "Duration", duration_ticks)
    _text(video_stream, "CodecType", DEFAULT_VIDEO_CODEC_TYPE)
    _text(video_stream, "FrameRect", f"0,0,{width},{height}")
    _text(
        video_stream,
        "OriginalColorSpace",
        '{"baseColorProfile":{"colorProfileData":"AQAAAP////8=","colorProfileName":"BT.709,8-bit,Display-Referred"},"baseProfileType":1}',
    )
    nodes.append(video_stream)

    master_clip = ET.Element("MasterClip", ObjectUID=master_clip_uid, ClassID=MASTER_CLIP_CLASS_ID, Version="12")
    _text(master_clip, "LoggingInfo", ObjectRef=clip_logging_id)
    clips = _text(master_clip, "Clips", Version="1")
    _text(clips, "Clip", Index="0", ObjectRef=master_video_clip_id)
    _text(master_clip, "Name", path.name)
    _text(master_clip, "MasterClipChangeVersion", 1)
    nodes.append(master_clip)

    clip_project_item = ET.Element(
        "ClipProjectItem",
        ObjectUID=clip_project_item_uid,
        ClassID=CLIP_PROJECT_ITEM_CLASS_ID,
        Version="1",
    )
    project_item = _text(clip_project_item, "ProjectItem", Version="1")
    node = _text(project_item, "Node", Version="1")
    props = _text(node, "Properties", Version="1")
    _text(props, "Column.PropertyText.Label", label_name)
    _text(props, "project.icon.view.grid.order", project_order)
    _text(project_item, "Name", path.name)
    _text(clip_project_item, "MasterClip", ObjectURef=master_clip_uid)
    nodes.append(clip_project_item)

    return _VideoAsset(
        path=path,
        name=path.name,
        clip_project_item_uid=clip_project_item_uid,
        master_clip_uid=master_clip_uid,
        media_uid=media_uid,
        project_order=project_order,
        clip_logging_id=clip_logging_id,
        markers_id=markers_id,
        video_stream_id=video_stream_id,
        video_media_source_id=video_media_source_id,
        master_video_clip_id=master_video_clip_id,
        stream_info=stream,
        duration_ticks=duration_ticks,
    )


def _build_audio_asset(
    nodes: list[ET.Element],
    refs: _RefAllocator,
    *,
    path: Path,
    project_order: int,
) -> _AudioAsset:
    media_info = probe_media(str(path))
    if not media_info.audio_streams:
        raise ValueError(f"Master audio does not expose an audio stream: {path}")
    stream = media_info.audio_streams[0]
    duration_ticks = _ticks_from_seconds(stream.duration_seconds or media_info.duration_seconds or 0.0)
    if duration_ticks <= 0:
        raise ValueError(f"Master audio does not expose a usable duration: {path}")

    channel_count = max(1, int(stream.channels or 2))
    if channel_count > 2:
        channel_count = 2

    clip_project_item_uid = refs.alloc_uid()
    master_clip_uid = refs.alloc_uid()
    media_uid = refs.alloc_uid()
    clip_logging_id = refs.alloc_id()
    markers_id = _make_markers(nodes, refs)
    audio_stream_id = refs.alloc_id()
    audio_media_source_id = refs.alloc_id()
    master_audio_clip_id = refs.alloc_id()
    audio_component_chain_id = _make_default_audio_component_chain(
        nodes,
        refs,
        channel_count=channel_count,
        include_channel_volume=True,
    )
    clip_channel_group_id = refs.alloc_id()
    clip_channel_vector_id = refs.alloc_id()
    clip_channel_serializer_ids = [refs.alloc_id() for _ in range(channel_count)]
    secondary_content_ids = [refs.alloc_id() for _ in range(channel_count)]

    clip_logging = ET.Element(
        "ClipLoggingInfo",
        ObjectID=str(clip_logging_id),
        ClassID=CLIP_LOGGING_INFO_CLASS_ID,
        Version="10",
    )
    _text(clip_logging, "ClipName", path.name)
    nodes.append(clip_logging)

    audio_clip = ET.Element(
        "AudioClip",
        ObjectID=str(master_audio_clip_id),
        ClassID=AUDIO_CLIP_CLASS_ID,
        Version="8",
    )
    clip = _text(audio_clip, "Clip", Version="18")
    node = _text(clip, "Node", Version="1")
    props = _text(node, "Properties", Version="1")
    label_name, label_color = _source_clip_label("master_audio")
    _text(props, "asl.clip.label.color", label_color)
    _text(props, "asl.clip.label.name", label_name)
    marker_owner = _text(clip, "MarkerOwner", Version="1")
    _text(marker_owner, "Markers", ObjectRef=markers_id)
    _text(clip, "Source", ObjectRef=audio_media_source_id)
    _text(clip, "ClipID", str(uuid.uuid4()))
    _text(clip, "InUse", "false")
    secondary_contents = _text(audio_clip, "SecondaryContents", Version="1")
    for index, object_id in enumerate(secondary_content_ids):
        _text(secondary_contents, "SecondaryContentItem", Index=str(index), ObjectRef=object_id)
    _text(audio_clip, "AudioChannelLayout", _channel_layout_text(channel_count))
    nodes.append(audio_clip)

    for index, object_id in enumerate(secondary_content_ids):
        secondary = ET.Element(
            "SecondaryContent",
            ObjectID=str(object_id),
            ClassID=SECONDARY_CONTENT_CLASS_ID,
            Version="1",
        )
        _text(secondary, "Content", ObjectRef=audio_media_source_id)
        _text(secondary, "ChannelIndex", index)
        nodes.append(secondary)

    media_source = ET.Element(
        "AudioMediaSource",
        ObjectID=str(audio_media_source_id),
        ClassID=AUDIO_MEDIA_SOURCE_CLASS_ID,
        Version="2",
    )
    media_source_root = _text(media_source, "MediaSource", Version="4")
    _text(media_source_root, "Content", Version="10")
    _text(media_source_root, "Media", ObjectURef=media_uid)
    _text(media_source, "OriginalDuration", duration_ticks)
    nodes.append(media_source)

    media = ET.Element("Media", ObjectUID=media_uid, ClassID=MEDIA_CLASS_ID, Version="30")
    _text(media, "AudioStream", ObjectRef=audio_stream_id)
    binary_hash = _content_state()
    _text(
        media,
        "ModificationState",
        _encoded_binary_hash(binary_hash),
        Encoding="base64",
        BinaryHash=binary_hash,
    )
    _text(media, "RelativePath", _relative_media_path(path))
    _text(media, "FilePath", _path_text(path))
    _text(media, "ImplementationID", "1fa18bfa-255c-44b1-ad73-56bcd99fceaf")
    _text(media, "Title", path.name)
    _text(media, "FileKey", str(uuid.uuid4()))
    _text(media, "ConformedAudioRate", _frame_ticks_from_fps(stream.sample_rate or 48_000, fallback_fps=48_000))
    _text(media, "ContentAndMetadataState", _content_state())
    _text(media, "ActualMediaFilePath", _path_text(path))
    nodes.append(media)

    audio_stream = ET.Element(
        "AudioStream",
        ObjectID=str(audio_stream_id),
        ClassID=AUDIO_STREAM_CLASS_ID,
        Version="8",
    )
    _text(audio_stream, "AudioChannelLayout", _channel_layout_text(channel_count))
    _text(audio_stream, "FrameRate", _frame_ticks_from_fps(stream.sample_rate or 48_000, fallback_fps=48_000))
    _text(audio_stream, "Duration", duration_ticks)
    _text(audio_stream, "ConformedAudioPath", _cache_file_path(path, suffix=".cfa"))
    _text(audio_stream, "PeakFilePath", _cache_file_path(path, suffix=".pek"))
    nodes.append(audio_stream)

    clip_channel_group = ET.Element(
        "ClipChannelGroupVectorSerializer",
        ObjectID=str(clip_channel_group_id),
        ClassID=CLIP_CHANNEL_GROUP_VECTOR_CLASS_ID,
        Version="1",
    )
    vectors = _text(clip_channel_group, "ClipChannelVectors", Version="1")
    _text(vectors, "ClipChannelVectorItem", Index="0", ObjectRef=clip_channel_vector_id)
    nodes.append(clip_channel_group)

    clip_channel_vector = ET.Element(
        "ClipChannelVectorSerializer",
        ObjectID=str(clip_channel_vector_id),
        ClassID=CLIP_CHANNEL_VECTOR_CLASS_ID,
        Version="1",
    )
    channels = _text(clip_channel_vector, "ClipChannels", Version="1")
    for index, object_id in enumerate(clip_channel_serializer_ids):
        _text(channels, "ClipChannelItem", Index=str(index), ObjectRef=object_id)
    _text(clip_channel_vector, "ChannelType", _channel_type(channel_count))
    nodes.append(clip_channel_vector)

    for index, object_id in enumerate(clip_channel_serializer_ids):
        serializer = ET.Element(
            "ClipChannelSerializer",
            ObjectID=str(object_id),
            ClassID=CLIP_CHANNEL_SERIALIZER_CLASS_ID,
            Version="1",
        )
        _text(serializer, "SourceClipIndex", 0)
        _text(serializer, "mSourceChannelIndex", index)
        nodes.append(serializer)

    master_clip = ET.Element("MasterClip", ObjectUID=master_clip_uid, ClassID=MASTER_CLIP_CLASS_ID, Version="12")
    _text(master_clip, "LoggingInfo", ObjectRef=clip_logging_id)
    audio_chains = _text(master_clip, "AudioComponentChains", Version="1")
    _text(audio_chains, "AudioComponentChain", Index="0", ObjectRef=audio_component_chain_id)
    clips = _text(master_clip, "Clips", Version="1")
    _text(clips, "Clip", Index="0", ObjectRef=master_audio_clip_id)
    _text(master_clip, "AudioClipChannelGroups", ObjectRef=clip_channel_group_id)
    _text(master_clip, "Name", path.name)
    _text(master_clip, "MasterClipChangeVersion", 1)
    nodes.append(master_clip)

    clip_project_item = ET.Element(
        "ClipProjectItem",
        ObjectUID=clip_project_item_uid,
        ClassID=CLIP_PROJECT_ITEM_CLASS_ID,
        Version="1",
    )
    project_item = _text(clip_project_item, "ProjectItem", Version="1")
    node = _text(project_item, "Node", Version="1")
    props = _text(node, "Properties", Version="1")
    _text(props, "Column.PropertyText.Label", label_name)
    _text(props, "project.icon.view.grid.order", project_order)
    _text(project_item, "Name", path.name)
    _text(clip_project_item, "MasterClip", ObjectURef=master_clip_uid)
    nodes.append(clip_project_item)

    return _AudioAsset(
        path=path,
        name=path.name,
        clip_project_item_uid=clip_project_item_uid,
        master_clip_uid=master_clip_uid,
        media_uid=media_uid,
        project_order=project_order,
        clip_logging_id=clip_logging_id,
        markers_id=markers_id,
        audio_stream_id=audio_stream_id,
        audio_media_source_id=audio_media_source_id,
        master_audio_clip_id=master_audio_clip_id,
        audio_component_chain_id=audio_component_chain_id,
        clip_channel_group_id=clip_channel_group_id,
        clip_channel_vector_id=clip_channel_vector_id,
        clip_channel_serializer_ids=clip_channel_serializer_ids,
        secondary_content_ids=secondary_content_ids,
        stream_info=stream,
        duration_ticks=duration_ticks,
        channel_count=channel_count,
    )


def _build_video_segment_objects(
    nodes: list[ET.Element],
    refs: _RefAllocator,
    *,
    segments: list[dict[str, Any]],
    asset_by_path: dict[str, _VideoAsset],
    width: int,
    height: int,
) -> list[_VideoSegmentRef]:
    built: list[_VideoSegmentRef] = []
    for segment in segments:
        asset = asset_by_path[str(Path(segment["asset_path"]).resolve())]
        clip_id = refs.alloc_id()
        subclip_id = refs.alloc_id()
        component_chain_id = _make_default_video_component_chain(nodes, refs)
        track_item_id = refs.alloc_id()
        in_ticks = _ticks_from_seconds(float(segment["source_start_seconds"]))
        out_ticks = _ticks_from_seconds(float(segment["source_end_seconds"]))
        end_ticks = _ticks_from_seconds(float(segment["output_end_seconds"]))

        video_clip = ET.Element("VideoClip", ObjectID=str(clip_id), ClassID=VIDEO_CLIP_CLASS_ID, Version="11")
        clip = _text(video_clip, "Clip", Version="18")
        node = _text(clip, "Node", Version="1")
        props = _text(node, "Properties", Version="1")
        label_name, label_color = _source_clip_label("video_segment")
        _text(props, "asl.clip.label.color", label_color)
        _text(props, "asl.clip.label.name", label_name)
        marker_owner = _text(clip, "MarkerOwner", Version="1")
        _text(marker_owner, "Markers", ObjectRef=asset.markers_id)
        _text(clip, "Source", ObjectRef=asset.video_media_source_id)
        _text(clip, "ClipID", str(uuid.uuid4()))
        _text(clip, "InPoint", in_ticks)
        _text(clip, "OutPoint", out_ticks)
        nodes.append(video_clip)

        subclip = ET.Element("SubClip", ObjectID=str(subclip_id), ClassID=SUBCLIP_CLASS_ID, Version="6")
        _text(subclip, "Clip", ObjectRef=clip_id)
        _text(subclip, "MasterClip", ObjectURef=asset.master_clip_uid)
        _text(subclip, "OrigChGrp", 0)
        _text(subclip, "Name", asset.name)
        nodes.append(subclip)

        track_item = ET.Element(
            "VideoClipTrackItem",
            ObjectID=str(track_item_id),
            ClassID=VIDEO_CLIP_TRACK_ITEM_CLASS_ID,
            Version="8",
        )
        clip_track_item = _text(track_item, "ClipTrackItem", Version="8")
        owner = _text(clip_track_item, "ComponentOwner", Version="1")
        _text(owner, "Components", ObjectRef=component_chain_id)
        track_ref = _text(clip_track_item, "TrackItem", Version="4")
        _text(track_ref, "End", end_ticks)
        _text(clip_track_item, "SubClip", ObjectRef=subclip_id)
        _text(track_item, "FrameRect", f"0,0,{width},{height}")
        _text(track_item, "PixelAspectRatio", "1,1")
        _text(track_item, "ToneMapSettings", '{"peak":-1,"version":3}')
        nodes.append(track_item)

        built.append(_VideoSegmentRef(asset=asset, track_item_id=track_item_id))
    return built


def _build_audio_segment_objects(
    nodes: list[ET.Element],
    refs: _RefAllocator,
    *,
    segments: list[dict[str, Any]],
    master_audio: _AudioAsset,
) -> list[_AudioSegmentRef]:
    built: list[_AudioSegmentRef] = []
    for segment in segments:
        clip_id = refs.alloc_id()
        subclip_id = refs.alloc_id()
        component_chain_id = _make_default_audio_component_chain(
            nodes,
            refs,
            channel_count=master_audio.channel_count,
            include_channel_volume=True,
        )
        track_item_id = refs.alloc_id()
        secondary_content_ids = [refs.alloc_id() for _ in range(master_audio.channel_count)]
        in_ticks = _ticks_from_seconds(float(segment["source_start_seconds"]))
        out_ticks = _ticks_from_seconds(float(segment["source_end_seconds"]))
        end_ticks = _ticks_from_seconds(float(segment["output_end_seconds"]))

        audio_clip = ET.Element("AudioClip", ObjectID=str(clip_id), ClassID=AUDIO_CLIP_CLASS_ID, Version="8")
        clip = _text(audio_clip, "Clip", Version="18")
        node = _text(clip, "Node", Version="1")
        props = _text(node, "Properties", Version="1")
        label_name, label_color = _source_clip_label("audio_segment")
        _text(props, "asl.clip.label.color", label_color)
        _text(props, "asl.clip.label.name", label_name)
        marker_owner = _text(clip, "MarkerOwner", Version="1")
        _text(marker_owner, "Markers", ObjectRef=master_audio.markers_id)
        _text(clip, "Source", ObjectRef=master_audio.audio_media_source_id)
        _text(clip, "ClipID", str(uuid.uuid4()))
        _text(clip, "InPoint", in_ticks)
        _text(clip, "OutPoint", out_ticks)
        secondary_contents = _text(audio_clip, "SecondaryContents", Version="1")
        for index, object_id in enumerate(secondary_content_ids):
            _text(secondary_contents, "SecondaryContentItem", Index=str(index), ObjectRef=object_id)
        _text(audio_clip, "AudioChannelLayout", _channel_layout_text(master_audio.channel_count))
        nodes.append(audio_clip)

        for index, object_id in enumerate(secondary_content_ids):
            secondary = ET.Element(
                "SecondaryContent",
                ObjectID=str(object_id),
                ClassID=SECONDARY_CONTENT_CLASS_ID,
                Version="1",
            )
            _text(secondary, "Content", ObjectRef=master_audio.audio_media_source_id)
            _text(secondary, "ChannelIndex", index)
            nodes.append(secondary)

        subclip = ET.Element("SubClip", ObjectID=str(subclip_id), ClassID=SUBCLIP_CLASS_ID, Version="6")
        _text(subclip, "Clip", ObjectRef=clip_id)
        _text(subclip, "MasterClip", ObjectURef=master_audio.master_clip_uid)
        _text(subclip, "OrigChGrp", 0)
        _text(subclip, "Name", master_audio.name)
        nodes.append(subclip)

        track_item = ET.Element(
            "AudioClipTrackItem",
            ObjectID=str(track_item_id),
            ClassID=AUDIO_CLIP_TRACK_ITEM_CLASS_ID,
            Version="11",
        )
        clip_track_item = _text(track_item, "ClipTrackItem", Version="8")
        owner = _text(clip_track_item, "ComponentOwner", Version="1")
        _text(owner, "Components", ObjectRef=component_chain_id)
        track_ref = _text(clip_track_item, "TrackItem", Version="4")
        _text(track_ref, "End", end_ticks)
        _text(clip_track_item, "SubClip", ObjectRef=subclip_id)
        _text(track_item, "PreRenderComponentChainHashVersion", 1)
        _text(track_item, "ID", str(uuid.uuid4()))
        nodes.append(track_item)

        built.append(_AudioSegmentRef(track_item_id=track_item_id))
    return built


def _append_project_root(
    root: ET.Element,
    *,
    output_path: Path,
    root_project_item_uid: str,
    root_children: list[str],
    static_refs: dict[str, Any],
) -> None:
    _text(root, "Project", ObjectRef="1")

    project = ET.Element("Project", ObjectID="1", ClassID=PROJECT_CLASS_ID, Version="45")
    node = _text(project, "Node", Version="1")
    props = _text(node, "Properties", Version="1")
    _text(props, "MZ.Project.GUID", str(uuid.uuid4()))
    _text(props, "MZ.Project.WorkspaceName", "Training")
    _text(props, "MZ.BuildVersion.Created", f"{DEFAULT_BUILD_VERSION} - {_utc_timestamp()}")
    _text(props, "MZ.BuildVersion.Modified", f"{DEFAULT_BUILD_VERSION} - {_utc_timestamp()}")
    _text(props, "MZ.Project.ApplicationID", "Pro")
    _text(props, "project.settings.lastknowngoodprojectpath", _actual_media_path(output_path))
    _text(project, "RootProjectItem", ObjectURef=root_project_item_uid)
    _text(project, "ProjectSettings", ObjectRef=static_refs["project_settings_id"])
    _text(project, "MovieCompileSettings", ObjectRef=static_refs["compile_ids"][0])
    _text(project, "StillCompileSettings", ObjectRef=static_refs["compile_ids"][1])
    _text(project, "AudioCompileSettings", ObjectRef=static_refs["compile_ids"][2])
    _text(project, "CustomCompileSettings", ObjectRef=static_refs["compile_ids"][3])
    _text(project, "VideoPreviewCompileSettings", ObjectRef=static_refs["compile_ids"][4])
    _text(project, "ScratchDiskSettings", ObjectRef=static_refs["scratch_id"])
    _text(project, "IngestSettings", ObjectRef=static_refs["ingest_id"])
    _text(project, "ProjectWorkspace", ObjectRef=static_refs["workspace_id"])
    _text(project, "NextID", 1_000_001)
    root.append(project)

    root_project = ET.Element(
        "RootProjectItem",
        ObjectUID=root_project_item_uid,
        ClassID=ROOT_PROJECT_ITEM_CLASS_ID,
        Version="1",
    )
    project_item = _text(root_project, "ProjectItem", Version="1")
    node = _text(project_item, "Node", Version="1")
    _text(node, "Properties", Version="1")
    _text(node, "ID", 1_000_000)
    _text(project_item, "Name", "Root Bin")
    container = _text(root_project, "ProjectItemContainer", Version="1")
    items = _text(container, "Items", Version="1")
    for index, object_uid in enumerate(root_children):
        _text(items, "Item", Index=str(index), ObjectURef=object_uid)
    root.append(root_project)


def _validate_root(root: ET.Element) -> None:
    object_ids: set[str] = set()
    object_uids: set[str] = set()
    for child in root:
        object_id = child.attrib.get("ObjectID")
        if object_id:
            if object_id in object_ids:
                raise ValueError(f"Duplicate ObjectID in generated project: {object_id}")
            object_ids.add(object_id)
        object_uid = child.attrib.get("ObjectUID")
        if object_uid:
            if object_uid in object_uids:
                raise ValueError(f"Duplicate ObjectUID in generated project: {object_uid}")
            object_uids.add(object_uid)

    for element in root.iter():
        ref = element.attrib.get("ObjectRef")
        if ref and ref not in object_ids:
            raise ValueError(f"Unresolved ObjectRef in generated project: {ref}")
        uref = element.attrib.get("ObjectURef")
        if uref and uref not in object_uids:
            raise ValueError(f"Unresolved ObjectURef in generated project: {uref}")


def _build_sequence(
    nodes: list[ET.Element],
    refs: _RefAllocator,
    *,
    cut_plan: dict[str, Any],
    project_name: str,
    video_assets: list[_VideoAsset],
    master_audio: _AudioAsset,
) -> str:
    render_defaults = cut_plan["render_defaults"]
    width = int(render_defaults.get("width") or 1920)
    height = int(render_defaults.get("height") or 1080)
    sequence_duration_ticks = _ticks_from_seconds(float(cut_plan["timeline"]["output_duration_seconds"]))
    preview_frame_ticks = _frame_ticks_from_fps(render_defaults.get("fps"), fallback_fps=25.0)
    audio_frame_ticks = _frame_ticks_from_fps(master_audio.stream_info.sample_rate or 48_000, fallback_fps=48_000)

    sequence_project_item_uid = refs.alloc_uid()
    sequence_master_clip_uid = refs.alloc_uid()
    sequence_uid = refs.alloc_uid()
    video_track_uid = refs.alloc_uid()
    audio_track_uid = refs.alloc_uid()

    sequence_logging_id = refs.alloc_id()
    sequence_audio_chain_id = _make_default_audio_component_chain(
        nodes,
        refs,
        channel_count=master_audio.channel_count,
        include_channel_volume=True,
    )
    sequence_audio_clip_id = refs.alloc_id()
    sequence_video_clip_id = refs.alloc_id()
    sequence_channel_group_id = refs.alloc_id()
    sequence_channel_vector_id = refs.alloc_id()
    sequence_channel_serializer_ids = [refs.alloc_id() for _ in range(master_audio.channel_count)]
    sequence_secondary_content_ids = [refs.alloc_id() for _ in range(master_audio.channel_count)]
    audio_sequence_source_id = refs.alloc_id()
    video_sequence_source_id = refs.alloc_id()
    video_track_group_id = refs.alloc_id()
    audio_track_group_id = refs.alloc_id()
    data_track_group_id = refs.alloc_id()
    sequence_video_chain_id = _make_empty_video_component_chain(nodes, refs)
    mix_chain_id, mix_panner_id = _make_audio_track_components(
        nodes,
        refs,
        channel_count=master_audio.channel_count,
        include_default_selection=True,
    )
    audio_track_chain_id, audio_track_panner_id = _make_audio_track_components(
        nodes,
        refs,
        channel_count=master_audio.channel_count,
        include_default_selection=False,
    )
    audio_mix_track_id = refs.alloc_id()
    audio_track_inlet_id = refs.alloc_id()

    video_segment_refs = _build_video_segment_objects(
        nodes,
        refs,
        segments=list(cut_plan.get("video_segments") or []),
        asset_by_path={str(asset.path.resolve()): asset for asset in video_assets},
        width=width,
        height=height,
    )
    audio_segment_refs = _build_audio_segment_objects(
        nodes,
        refs,
        segments=list(cut_plan.get("audio_segments") or []),
        master_audio=master_audio,
    )

    nodes.append(
        ET.Element(
            "ClipLoggingInfo",
            ObjectID=str(sequence_logging_id),
            ClassID=CLIP_LOGGING_INFO_CLASS_ID,
            Version="10",
        )
    )

    label_name, label_color = _source_clip_label("sequence")

    sequence_audio_clip = ET.Element(
        "AudioClip",
        ObjectID=str(sequence_audio_clip_id),
        ClassID=AUDIO_CLIP_CLASS_ID,
        Version="8",
    )
    clip = _text(sequence_audio_clip, "Clip", Version="18")
    node = _text(clip, "Node", Version="1")
    props = _text(node, "Properties", Version="1")
    _text(props, "asl.clip.label.color", label_color)
    _text(props, "asl.clip.label.name", label_name)
    _text(clip, "Source", ObjectRef=audio_sequence_source_id)
    _text(clip, "ClipID", str(uuid.uuid4()))
    _text(clip, "InUse", "false")
    secondary_contents = _text(sequence_audio_clip, "SecondaryContents", Version="1")
    for index, object_id in enumerate(sequence_secondary_content_ids):
        _text(secondary_contents, "SecondaryContentItem", Index=str(index), ObjectRef=object_id)
    _text(sequence_audio_clip, "AudioChannelLayout", _channel_layout_text(master_audio.channel_count))
    nodes.append(sequence_audio_clip)

    for index, object_id in enumerate(sequence_secondary_content_ids):
        secondary = ET.Element(
            "SecondaryContent",
            ObjectID=str(object_id),
            ClassID=SECONDARY_CONTENT_CLASS_ID,
            Version="1",
        )
        _text(secondary, "Content", ObjectRef=audio_sequence_source_id)
        _text(secondary, "ChannelIndex", index)
        nodes.append(secondary)

    sequence_video_clip = ET.Element(
        "VideoClip",
        ObjectID=str(sequence_video_clip_id),
        ClassID=VIDEO_CLIP_CLASS_ID,
        Version="11",
    )
    clip = _text(sequence_video_clip, "Clip", Version="18")
    node = _text(clip, "Node", Version="1")
    props = _text(node, "Properties", Version="1")
    _text(props, "asl.clip.label.color", label_color)
    _text(props, "asl.clip.label.name", label_name)
    _text(clip, "Source", ObjectRef=video_sequence_source_id)
    _text(clip, "ClipID", str(uuid.uuid4()))
    _text(clip, "InUse", "false")
    nodes.append(sequence_video_clip)

    audio_sequence_source = ET.Element(
        "AudioSequenceSource",
        ObjectID=str(audio_sequence_source_id),
        ClassID=AUDIO_SEQUENCE_SOURCE_CLASS_ID,
        Version="7",
    )
    sequence_source = _text(audio_sequence_source, "SequenceSource", Version="4")
    _text(sequence_source, "Content", Version="10")
    _text(sequence_source, "Sequence", ObjectURef=sequence_uid)
    _text(audio_sequence_source, "OriginalDuration", sequence_duration_ticks)
    nodes.append(audio_sequence_source)

    video_sequence_source = ET.Element(
        "VideoSequenceSource",
        ObjectID=str(video_sequence_source_id),
        ClassID=VIDEO_SEQUENCE_SOURCE_CLASS_ID,
        Version="3",
    )
    sequence_source = _text(video_sequence_source, "SequenceSource", Version="4")
    _text(sequence_source, "Content", Version="10")
    _text(sequence_source, "Sequence", ObjectURef=sequence_uid)
    _text(video_sequence_source, "OriginalDuration", sequence_duration_ticks)
    nodes.append(video_sequence_source)

    sequence_channel_group = ET.Element(
        "ClipChannelGroupVectorSerializer",
        ObjectID=str(sequence_channel_group_id),
        ClassID=CLIP_CHANNEL_GROUP_VECTOR_CLASS_ID,
        Version="1",
    )
    vectors = _text(sequence_channel_group, "ClipChannelVectors", Version="1")
    _text(vectors, "ClipChannelVectorItem", Index="0", ObjectRef=sequence_channel_vector_id)
    nodes.append(sequence_channel_group)

    sequence_channel_vector = ET.Element(
        "ClipChannelVectorSerializer",
        ObjectID=str(sequence_channel_vector_id),
        ClassID=CLIP_CHANNEL_VECTOR_CLASS_ID,
        Version="1",
    )
    channels = _text(sequence_channel_vector, "ClipChannels", Version="1")
    for index, object_id in enumerate(sequence_channel_serializer_ids):
        _text(channels, "ClipChannelItem", Index=str(index), ObjectRef=object_id)
    _text(sequence_channel_vector, "ChannelType", _channel_type(master_audio.channel_count))
    nodes.append(sequence_channel_vector)

    for index, object_id in enumerate(sequence_channel_serializer_ids):
        serializer = ET.Element(
            "ClipChannelSerializer",
            ObjectID=str(object_id),
            ClassID=CLIP_CHANNEL_SERIALIZER_CLASS_ID,
            Version="1",
        )
        _text(serializer, "SourceClipIndex", 0)
        _text(serializer, "mSourceChannelIndex", index)
        nodes.append(serializer)

    sequence_master_clip = ET.Element(
        "MasterClip",
        ObjectUID=sequence_master_clip_uid,
        ClassID=MASTER_CLIP_CLASS_ID,
        Version="12",
    )
    _text(sequence_master_clip, "LoggingInfo", ObjectRef=sequence_logging_id)
    audio_chains = _text(sequence_master_clip, "AudioComponentChains", Version="1")
    _text(audio_chains, "AudioComponentChain", Index="0", ObjectRef=sequence_audio_chain_id)
    clips = _text(sequence_master_clip, "Clips", Version="1")
    _text(clips, "Clip", Index="0", ObjectRef=sequence_audio_clip_id)
    _text(clips, "Clip", Index="1", ObjectRef=sequence_video_clip_id)
    _text(sequence_master_clip, "AudioClipChannelGroups", ObjectRef=sequence_channel_group_id)
    _text(sequence_master_clip, "Name", project_name)
    _text(sequence_master_clip, "MasterClipChangeVersion", 1)
    nodes.append(sequence_master_clip)

    sequence_project_item = ET.Element(
        "ClipProjectItem",
        ObjectUID=sequence_project_item_uid,
        ClassID=CLIP_PROJECT_ITEM_CLASS_ID,
        Version="1",
    )
    project_item = _text(sequence_project_item, "ProjectItem", Version="1")
    node = _text(project_item, "Node", Version="1")
    props = _text(node, "Properties", Version="1")
    _text(props, "Column.PropertyText.Label", label_name)
    _text(props, "project.icon.view.grid.order", len(video_assets) + 1)
    _text(project_item, "Name", project_name)
    _text(sequence_project_item, "MasterClip", ObjectURef=sequence_master_clip_uid)
    nodes.append(sequence_project_item)

    sequence = ET.Element("Sequence", ObjectUID=sequence_uid, ClassID=SEQUENCE_CLASS_ID, Version="12")
    node = _text(sequence, "Node", Version="1")
    props = _text(node, "Properties", Version="1")
    _text(props, "MZ.WorkInPoint", 0)
    _text(props, "MZ.WorkOutPoint", sequence_duration_ticks)
    _text(props, "MZ.Sequence.VideoTimeDisplayFormat", 101)
    _text(props, "MZ.Sequence.AudioTimeDisplayFormat", 200)
    _text(props, "MZ.Sequence.EditingModeGUID", DEFAULT_EDITING_MODE_GUID)
    _text(props, "MZ.Sequence.PreviewUseMaxBitDepth", "false")
    _text(props, "MZ.Sequence.PreviewUseMaxRenderQuality", "false")
    _text(props, "MZ.Sequence.PreviewFrameSizeWidth", width)
    _text(props, "MZ.Sequence.PreviewFrameSizeHeight", height)
    _text(props, "MZ.Sequence.PreviewRenderingPresetCodec", 1_634_755_443)
    _text(props, "MZ.Sequence.PreviewRenderingClassID", 1_061_109_567)
    links_root = _text(_text(sequence, "PersistentGroupContainer", Version="1"), "LinkContainer", Version="1")
    _text(links_root, "Links", Version="1")
    track_groups = _text(sequence, "TrackGroups", Version="1")
    group = _text(track_groups, "TrackGroup", Version="1", Index="0")
    _text(group, "First", VIDEO_MEDIA_TYPE_ID)
    _text(group, "Second", ObjectRef=video_track_group_id)
    group = _text(track_groups, "TrackGroup", Version="1", Index="1")
    _text(group, "First", AUDIO_MEDIA_TYPE_ID)
    _text(group, "Second", ObjectRef=audio_track_group_id)
    group = _text(track_groups, "TrackGroup", Version="1", Index="2")
    _text(group, "First", DATA_MEDIA_TYPE_ID)
    _text(group, "Second", ObjectRef=data_track_group_id)
    _text(sequence, "Name", project_name)
    _text(sequence, "PreviewFormatIdentifier", DEFAULT_PREVIEW_FORMAT_ID)
    nodes.append(sequence)

    video_track_group = ET.Element(
        "VideoTrackGroup",
        ObjectID=str(video_track_group_id),
        ClassID=VIDEO_TRACK_GROUP_CLASS_ID,
        Version="13",
    )
    track_group = _text(video_track_group, "TrackGroup", Version="1")
    tracks = _text(track_group, "Tracks", Version="1")
    _text(tracks, "Track", Index="0", ObjectURef=video_track_uid)
    _text(track_group, "FrameRate", preview_frame_ticks)
    _text(track_group, "NextTrackID", 2)
    _text(video_track_group, "FrameRect", f"0,0,{width},{height}")
    owner = _text(video_track_group, "ComponentOwner", Version="1")
    _text(owner, "Components", ObjectRef=sequence_video_chain_id)
    nodes.append(video_track_group)

    audio_track_group = ET.Element(
        "AudioTrackGroup",
        ObjectID=str(audio_track_group_id),
        ClassID=AUDIO_TRACK_GROUP_CLASS_ID,
        Version="6",
    )
    track_group = _text(audio_track_group, "TrackGroup", Version="1")
    tracks = _text(track_group, "Tracks", Version="1")
    _text(tracks, "Track", Index="0", ObjectURef=audio_track_uid)
    _text(track_group, "FrameRate", audio_frame_ticks)
    _text(track_group, "NextTrackID", 3)
    _text(audio_track_group, "MasterTrack", ObjectRef=audio_mix_track_id)
    _text(audio_track_group, "AutomationSafeFlags", 0)
    _text(audio_track_group, "NumAdaptiveChannels", master_audio.channel_count)
    _text(audio_track_group, "ID", str(uuid.uuid4()))
    nodes.append(audio_track_group)

    data_track_group = ET.Element(
        "DataTrackGroup",
        ObjectID=str(data_track_group_id),
        ClassID=DATA_TRACK_GROUP_CLASS_ID,
        Version="1",
    )
    track_group = _text(data_track_group, "TrackGroup", Version="1")
    _text(track_group, "FrameRate", preview_frame_ticks)
    _text(track_group, "NextTrackID", 1)
    nodes.append(data_track_group)

    video_track = ET.Element("VideoClipTrack", ObjectUID=video_track_uid, ClassID=VIDEO_CLIP_TRACK_CLASS_ID, Version="1")
    clip_track = _text(video_track, "ClipTrack", Version="2")
    track = _text(clip_track, "Track", Version="4")
    node = _text(track, "Node", Version="1")
    props = _text(node, "Properties", Version="1")
    _text(props, "TL.SQTrackShy", 0)
    _text(props, "MZ.TrackTargeted", 1)
    _text(props, "MZ.SourceTrackState", 0)
    _text(props, "MZ.SourceTrackNumber", -1)
    _text(props, "TL.SQTrackExpanded", 0)
    _text(props, "TL.SQTrackExpandedHeight", 41)
    _text(track, "MediaType", VIDEO_MEDIA_TYPE_ID)
    _text(track, "Index", 0)
    _text(track, "ID", 1)
    clip_items = _text(clip_track, "ClipItems", Version="3")
    track_items = _text(clip_items, "TrackItems", Version="1")
    for index, item in enumerate(video_segment_refs):
        _text(track_items, "TrackItem", Index=str(index), ObjectRef=item.track_item_id)
    _text(clip_items, "MediaType", VIDEO_MEDIA_TYPE_ID)
    _text(clip_items, "Index", 0)
    transition_items = _text(clip_track, "TransitionItems", Version="3")
    _text(transition_items, "MediaType", VIDEO_MEDIA_TYPE_ID)
    _text(transition_items, "Index", 0)
    nodes.append(video_track)

    audio_track = ET.Element("AudioClipTrack", ObjectUID=audio_track_uid, ClassID=AUDIO_CLIP_TRACK_CLASS_ID, Version="7")
    clip_track = _text(audio_track, "ClipTrack", Version="2")
    track = _text(clip_track, "Track", Version="4")
    node = _text(track, "Node", Version="1")
    props = _text(node, "Properties", Version="1")
    _text(props, "TL.SQTrackShy", 0)
    _text(props, "TL.SQTrackAudioKeyframeStyle", 0)
    _text(props, "MZ.TrackTargeted", 1)
    _text(props, "MZ.SourceTrackState", 0)
    _text(props, "MZ.SourceTrackNumber", -1)
    _text(props, "CM.KeyframeMode", "true")
    _text(props, "TL.SQTrackExpanded", 0)
    _text(props, "TL.SQTrackExpandedHeight", 41)
    _text(track, "MediaType", AUDIO_MEDIA_TYPE_ID)
    _text(track, "Index", 0)
    _text(track, "ID", 2)
    clip_items = _text(clip_track, "ClipItems", Version="3")
    track_items = _text(clip_items, "TrackItems", Version="1")
    for index, item in enumerate(audio_segment_refs):
        _text(track_items, "TrackItem", Index=str(index), ObjectRef=item.track_item_id)
    _text(clip_items, "MediaType", AUDIO_MEDIA_TYPE_ID)
    _text(clip_items, "Index", 0)
    transition_items = _text(clip_track, "TransitionItems", Version="3")
    _text(transition_items, "MediaType", AUDIO_MEDIA_TYPE_ID)
    _text(transition_items, "Index", 0)
    audio_track_body = _text(audio_track, "AudioTrack", Version="12")
    owner = _text(audio_track_body, "ComponentOwner", Version="1")
    _text(owner, "Components", ObjectRef=audio_track_chain_id)
    _text(audio_track_body, "Panner", ObjectRef=audio_track_panner_id)
    _text(audio_track_body, "NextPannerID", 4_294_967_279)
    _text(audio_track_body, "ID", str(uuid.uuid4()))
    nodes.append(audio_track)

    mix_track = ET.Element("AudioMixTrack", ObjectID=str(audio_mix_track_id), ClassID=AUDIO_MIX_TRACK_CLASS_ID, Version="4")
    mix_audio_track = _text(mix_track, "AudioTrack", Version="12")
    owner = _text(mix_audio_track, "ComponentOwner", Version="1")
    _text(owner, "Components", ObjectRef=mix_chain_id)
    _text(mix_audio_track, "Panner", ObjectRef=mix_panner_id)
    _text(mix_audio_track, "SubType", 3)
    _text(mix_audio_track, "Assign", 0)
    _text(mix_audio_track, "NextPannerID", 4_294_967_279)
    _text(mix_audio_track, "ID", str(uuid.uuid4()))
    track = _text(mix_track, "Track", Version="4")
    node = _text(track, "Node", Version="1")
    props = _text(node, "Properties", Version="1")
    _text(props, "TL.SQTrackShy", 0)
    _text(props, "TL.SQTrackAudioKeyframeStyle", 2)
    _text(props, "TL.SQTrackExpanded", 0)
    _text(props, "TL.SQTrackExpandedHeight", 41)
    _text(track, "MediaType", AUDIO_MEDIA_TYPE_ID)
    _text(track, "Index", 0)
    _text(track, "ID", 1)
    _text(mix_track, "Inlet", ObjectRef=audio_track_inlet_id)
    nodes.append(mix_track)

    inlet = ET.Element("AudioTrackInlet", ObjectID=str(audio_track_inlet_id), ClassID=AUDIO_TRACK_INLET_CLASS_ID, Version="4")
    sources = _text(inlet, "Sources", Version="1")
    _text(sources, "Source", Index="0", ObjectURef=audio_track_uid)
    _text(inlet, "AudioChannelLayout", _channel_layout_text(master_audio.channel_count))
    nodes.append(inlet)

    return sequence_project_item_uid


def export_premiere_project(
    cut_plan: dict[str, Any],
    *,
    output_project_path: str,
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

    output_path = Path(output_project_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_project_name = project_name or output_path.stem or "VAZer Export"

    refs = _RefAllocator()
    root = ET.Element("PremiereData", Version="3")
    nodes: list[ET.Element] = []
    static_refs = _build_static_project_objects(nodes, refs)

    seen_video_paths: set[str] = set()
    video_asset_paths: list[Path] = []
    for segment in video_segments:
        asset_path = str(Path(segment["asset_path"]).resolve())
        if asset_path in seen_video_paths:
            continue
        seen_video_paths.add(asset_path)
        video_asset_paths.append(Path(asset_path))

    fallback_fps = float(cut_plan["render_defaults"].get("fps") or 25.0)
    video_assets = [
        _build_video_asset(nodes, refs, path=path, project_order=index + 1, fallback_fps=fallback_fps)
        for index, path in enumerate(video_asset_paths)
    ]
    master_audio = _build_audio_asset(
        nodes,
        refs,
        path=Path(str(cut_plan["master_audio"]["path"])).resolve(),
        project_order=0,
    )
    sequence_project_item_uid = _build_sequence(
        nodes,
        refs,
        cut_plan=cut_plan,
        project_name=resolved_project_name,
        video_assets=video_assets,
        master_audio=master_audio,
    )

    root_project_item_uid = refs.alloc_uid()
    root_children = [master_audio.clip_project_item_uid, *[asset.clip_project_item_uid for asset in video_assets], sequence_project_item_uid]
    _append_project_root(
        root,
        output_path=output_path,
        root_project_item_uid=root_project_item_uid,
        root_children=root_children,
        static_refs=static_refs,
    )

    for element in nodes:
        root.append(element)
    _validate_root(root)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="\t")
    with gzip.open(output_path, "wb") as handle:
        tree.write(handle, encoding="utf-8", xml_declaration=True)

    return {
        "schema_version": "vazer.premiere_project.v1",
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
        },
        "summary": {
            "video_assets": len(video_assets),
            "video_segments": len(video_segments),
            "audio_segments": len(audio_segments),
            "duration_seconds": float(cut_plan["timeline"]["output_duration_seconds"]),
        },
    }
