from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import base64
import json
import os
from pathlib import Path
import re
from typing import Any, Literal

import cv2
from pydantic import BaseModel, Field

from . import __version__
from .fftools import probe_media

DEFAULT_CAMERA_ROLE_MODEL = "gpt-4.1-mini"


@dataclass(slots=True)
class CameraRoleOptions:
    model: str = DEFAULT_CAMERA_ROLE_MODEL
    image_width: int = 960
    image_quality: int = 88
    temperature: float = 0.0
    max_output_tokens: int = 1200
    user_notes: str | None = None


class CameraRoleAssignment(BaseModel):
    asset_id: str = Field(..., description="Camera asset id from the input list.")
    role: Literal["totale", "halbtotale", "close"] = Field(
        ...,
        description="Assigned theater camera role.",
    )
    confidence: Literal["low", "medium", "high"] = Field(
        ...,
        description="How confident the model is in the role assignment.",
    )
    reason: str = Field(..., description="Short reason based on framing and filename hints.")


class CameraRoleResult(BaseModel):
    summary: str = Field(..., description="Short summary of the camera-role read.")
    assignments: list[CameraRoleAssignment] = Field(
        ...,
        description="Exactly one role assignment per provided asset.",
    )


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_api_key() -> str:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError as error:
        raise ValueError(
            "python-dotenv is not installed. Run the project dependencies first, for example `pip install -e .`."
        ) from error

    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set. Put it in .env or your environment.")
    return api_key


def _build_client() -> Any:
    try:
        from openai import OpenAI
    except ModuleNotFoundError as error:
        raise ValueError(
            "openai is not installed. Run the project dependencies first, for example `pip install -e .`."
        ) from error

    return OpenAI(api_key=_load_api_key())


def _slugify(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = lowered.strip("-")
    return lowered or "asset"


def infer_camera_role_from_name(asset_id: str, path: str) -> str:
    name = f"{asset_id} {Path(path).stem}".lower()
    if "close" in name or "nah" in name:
        return "close"
    if "halbtotale" in name or "halb" in name or re.search(r"\bht\b", name):
        return "halbtotale"
    if "total" in name or "totale" in name or "wide" in name:
        return "totale"
    return "halbtotale"


def _data_url_for_image(path: str) -> str:
    image_bytes = Path(path).read_bytes()
    encoded = base64.b64encode(image_bytes).decode("ascii")
    suffix = Path(path).suffix.lower()
    mime_type = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    return f"data:{mime_type};base64,{encoded}"


def _export_middle_frame(
    *,
    source_path: str,
    output_path: Path,
    image_width: int,
    image_quality: int,
) -> dict[str, Any]:
    media_info = probe_media(source_path)
    if not media_info.video_streams:
        raise ValueError(f"{source_path} does not contain a video stream.")

    duration_seconds = float(media_info.duration_seconds or media_info.video_streams[0].duration_seconds or 0.0)
    if duration_seconds <= 0:
        raise ValueError(f"{source_path} does not expose a usable duration.")

    capture = cv2.VideoCapture(source_path)
    if not capture.isOpened():
        raise ValueError(f"OpenCV could not open {source_path}.")

    try:
        middle_seconds = duration_seconds / 2.0
        capture.set(cv2.CAP_PROP_POS_MSEC, middle_seconds * 1000.0)
        ok, frame = capture.read()
        if not ok or frame is None:
            raise ValueError(f"Could not decode the middle frame from {source_path}.")

        height, width = frame.shape[:2]
        if width > image_width:
            target_width = image_width
            target_height = max(2, int(round(height * target_width / width / 2.0) * 2))
            frame = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
        else:
            target_width = width
            target_height = height

        output_path.parent.mkdir(parents=True, exist_ok=True)
        success = cv2.imwrite(str(output_path), frame, [cv2.IMWRITE_JPEG_QUALITY, int(image_quality)])
        if not success:
            raise ValueError(f"Failed to write middle-frame preview to {output_path}.")

        return {
            "middle_seconds": middle_seconds,
            "image_path": str(output_path),
            "image_width": target_width,
            "image_height": target_height,
            "duration_seconds": duration_seconds,
        }
    finally:
        capture.release()


def _build_input_content(
    exported_assets: list[dict[str, Any]],
    options: CameraRoleOptions,
) -> list[dict[str, Any]]:
    asset_brief = [
        {
            "asset_id": asset["asset_id"],
            "display_name": asset["display_name"],
            "filename": Path(asset["path"]).name,
            "duration_seconds": asset["duration_seconds"],
            "middle_seconds": asset["middle_seconds"],
        }
        for asset in exported_assets
    ]
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": (
                "Assign theater camera roles for this multicam stage recording. "
                "Allowed roles are only: totale, halbtotale, close.\n\n"
                "Role definitions:\n"
                "- totale: widest full-stage or safest fallback view, usually static and spatially descriptive.\n"
                "- halbtotale: medium / bridge view for group dynamics and stage geography.\n"
                "- close: tight speaker- or subject-focused shot.\n\n"
                "Use both the filename and the attached middle frame. "
                "Return exactly one assignment per asset_id. "
                "When there are three cameras, a distribution of close + halbtotale + totale is usually expected, "
                "but prioritize what the actual images show.\n\n"
                f"Structured asset list:\n{json.dumps(asset_brief, ensure_ascii=True, indent=2)}\n\n"
                f"User notes: {options.user_notes or '[none]'}"
            ),
        }
    ]

    for asset in exported_assets:
        content.append(
            {
                "type": "input_text",
                "text": (
                    f"Camera asset follows: asset_id={asset['asset_id']}, "
                    f"filename={Path(asset['path']).name}, display_name={asset['display_name']}, "
                    f"middle_seconds={asset['middle_seconds']:.3f}."
                ),
            }
        )
        content.append(
            {
                "type": "input_image",
                "image_url": _data_url_for_image(asset["image_path"]),
            }
        )

    return content


def _validated_assignments(
    exported_assets: list[dict[str, Any]],
    parsed: CameraRoleResult,
) -> list[dict[str, Any]]:
    expected_asset_ids = [asset["asset_id"] for asset in exported_assets]
    assignments_by_asset = {assignment.asset_id: assignment for assignment in parsed.assignments}
    missing_assets = [asset_id for asset_id in expected_asset_ids if asset_id not in assignments_by_asset]
    extra_assets = sorted(set(assignments_by_asset) - set(expected_asset_ids))
    if missing_assets or extra_assets:
        details = []
        if missing_assets:
            details.append(f"missing={', '.join(missing_assets)}")
        if extra_assets:
            details.append(f"extra={', '.join(extra_assets)}")
        raise ValueError(f"AI camera-role response did not match the expected assets ({'; '.join(details)}).")

    assignments: list[dict[str, Any]] = []
    for asset in exported_assets:
        assignment = assignments_by_asset[asset["asset_id"]]
        assignments.append(
            {
                "asset_id": asset["asset_id"],
                "path": asset["path"],
                "display_name": asset["display_name"],
                "duration_seconds": asset["duration_seconds"],
                "middle_seconds": asset["middle_seconds"],
                "frame_path": asset["image_path"],
                "image_width": asset["image_width"],
                "image_height": asset["image_height"],
                "role": assignment.role,
                "confidence": assignment.confidence,
                "reason": assignment.reason.strip(),
            }
        )
    return assignments


def build_camera_role_artifact(
    cameras: list[dict[str, Any]],
    *,
    output_dir: str,
    options: CameraRoleOptions | None = None,
    source_sync_map_path: str | None = None,
) -> dict[str, Any]:
    if not cameras:
        raise ValueError("At least one camera asset is required for AI role classification.")

    role_options = options or CameraRoleOptions()
    client = _build_client()
    output_root = Path(output_dir)
    exported_assets: list[dict[str, Any]] = []
    for index, camera in enumerate(cameras, start=1):
        asset_id = str(camera.get("asset_id") or "").strip()
        path = str(camera.get("path") or "").strip()
        if not asset_id or not path:
            raise ValueError("Each camera asset requires asset_id and path.")
        display_name = str(camera.get("display_name") or Path(path).name)
        frame_path = output_root / "frames" / f"{index:02d}-{_slugify(asset_id)}.jpg"
        frame_info = _export_middle_frame(
            source_path=path,
            output_path=frame_path,
            image_width=role_options.image_width,
            image_quality=role_options.image_quality,
        )
        exported_assets.append(
            {
                "asset_id": asset_id,
                "path": path,
                "display_name": display_name,
                **frame_info,
            }
        )

    response = client.responses.parse(
        model=role_options.model,
        input=[{"role": "user", "content": _build_input_content(exported_assets, role_options)}],
        text_format=CameraRoleResult,
        max_output_tokens=role_options.max_output_tokens,
        temperature=role_options.temperature,
    )
    parsed = getattr(response, "output_parsed", None)
    if parsed is None:
        raise ValueError("AI camera-role response did not contain a parsed structured output.")

    assignments = _validated_assignments(exported_assets, parsed)
    role_counts = {
        role: sum(1 for assignment in assignments if assignment["role"] == role)
        for role in ("close", "halbtotale", "totale")
    }
    usage = getattr(response, "usage", None)
    return {
        "schema_version": "vazer.camera_roles.v1",
        "generated_at_utc": _utc_timestamp(),
        "tool": {
            "name": "vazer",
            "version": __version__,
        },
        "provider": {
            "name": "openai",
            "model": role_options.model,
        },
        "source_sync_map": None
        if source_sync_map_path is None
        else {
            "schema_version": "vazer.sync_map.v1",
            "path": source_sync_map_path,
        },
        "options": {
            "image_width": role_options.image_width,
            "image_quality": role_options.image_quality,
            "temperature": role_options.temperature,
            "max_output_tokens": role_options.max_output_tokens,
            "user_notes": role_options.user_notes,
        },
        "summary": {
            "asset_count": len(assignments),
            "role_counts": role_counts,
            "summary_text": parsed.summary.strip(),
        },
        "assignments": assignments,
        "ai_response": {
            "response_id": getattr(response, "id", None),
            "usage": None if usage is None else getattr(usage, "model_dump", lambda: None)(),
        },
    }


def build_camera_role_artifact_from_sync_map(
    sync_map: dict[str, Any],
    *,
    output_dir: str,
    options: CameraRoleOptions | None = None,
    source_sync_map_path: str | None = None,
) -> dict[str, Any]:
    if sync_map.get("schema_version") != "vazer.sync_map.v1":
        raise ValueError("Unsupported sync_map schema version.")

    cameras = [
        {
            "asset_id": entry["asset_id"],
            "path": entry["path"],
            "display_name": Path(entry["path"]).name,
        }
        for entry in sync_map.get("entries", [])
        if isinstance(entry, dict) and entry.get("status") == "synced"
    ]
    if not cameras:
        raise ValueError("sync_map does not contain any synced camera entries.")

    return build_camera_role_artifact(
        cameras,
        output_dir=output_dir,
        options=options,
        source_sync_map_path=source_sync_map_path,
    )


def load_camera_role_artifact(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_camera_role_artifact(artifact: dict[str, Any], output_path: str) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return destination
