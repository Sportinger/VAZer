from __future__ import annotations

from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse
import uuid
import webbrowser

from . import __version__
from .analysis import (
    AnalysisOptions,
    analyze_camera_video_signals,
    analyze_master_audio_activity,
    compose_analysis_map,
    write_analysis_map,
)
from .camera_roles import build_camera_role_artifact, infer_camera_role_from_name, write_camera_role_artifact
from .cut_plan import write_cut_plan
from .cut_review import CutValidationOptions, build_cut_validation_report, repair_cut_plan, write_cut_validation_report
from .fftools import MediaInfo, probe_media
from .premiere_xml import export_premiere_xml
from .process_manager import terminate_registered_processes
from .render import apply_max_render_size, build_render_scaffold, run_render
from .sync import SyncOptions, analyze_sync
from .sync_map import write_sync_map
from .theater_pipeline import TheaterPipelineOptions, build_chunked_ai_draft_bundle
from .transcribe import TranscriptionOptions, build_master_transcript, write_transcript_artifact
from .visual_packet import write_visual_packet

IGNORED_IMPORT_FILENAMES = {
    ".ds_store",
    "thumbs.db",
    "desktop.ini",
}

LEGACY_ROOT_ARTIFACT_FILES = {
    "vazer.sync_map.partial.json",
    "vazer.sync_map.json",
    "vazer.camera_roles.json",
    "vazer.analysis_map.json",
    "vazer.visual_packet.json",
    "vazer.cut_plan.ai.json",
    "vazer.cut_validation.json",
    "vazer.cut_plan.repaired.json",
    "vazer.cut_plan.repaired.fhd.json",
}

OUTPUT_MODE_RENDER_AND_PREMIERE = "render_and_premiere"
OUTPUT_MODE_PREMIERE_ONLY = "premiere_only"


def normalize_output_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {
        "",
        OUTPUT_MODE_RENDER_AND_PREMIERE,
        "render",
        "mp4",
        "default",
        "both",
        "render+premiere",
        "mp4+premiere",
        "render+xml",
        "mp4+xml",
    }:
        return OUTPUT_MODE_RENDER_AND_PREMIERE
    if normalized in {
        OUTPUT_MODE_PREMIERE_ONLY,
        "premiere",
        "xml",
        "premiere_xml",
        "prproj",
        "premiere_project",
        "prproj_only",
        "xml_only",
    }:
        return OUTPUT_MODE_PREMIERE_ONLY
    raise ValueError(f"Unknown output mode: {value}")


def output_mode_label(value: Any) -> str:
    normalized = normalize_output_mode(value)
    if normalized == OUTPUT_MODE_PREMIERE_ONLY:
        return "Nur Premiere XML"
    return "MP4 + Premiere XML"


def should_ignore_import_file(path: Path) -> bool:
    name = path.name.strip()
    lowered = name.lower()
    return lowered.startswith(".") or lowered in IGNORED_IMPORT_FILENAMES


def resolve_default_output_dir(paths: list[Path]) -> str | None:
    project_dir = resolve_default_project_dir(paths)
    if project_dir is None:
        return None
    return str(project_dir / "output")


def resolve_default_artifacts_dir(paths: list[Path]) -> str | None:
    project_dir = resolve_default_project_dir(paths)
    if project_dir is None:
        return None
    return str(project_dir / "artifacts")


def resolve_default_project_dir(paths: list[Path]) -> Path | None:
    if not paths:
        return None

    parents = [str(path.parent) for path in paths]
    try:
        common_parent = Path(os.path.commonpath(parents))
    except ValueError:
        return None

    return (common_parent / "VAZer") if common_parent.exists() else None


def _slugify(value: str) -> str:
    lowered = str(value or "").strip().lower()
    collapsed = "".join(character if character.isalnum() else "-" for character in lowered)
    while "--" in collapsed:
        collapsed = collapsed.replace("--", "-")
    return collapsed.strip("-") or "vazer"


def _same_media_path(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    try:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
    except Exception:
        return Path(left).name == Path(right).name


def _artifact_layout(project: dict[str, Any], classification: dict[str, Any] | None = None) -> dict[str, Path]:
    artifacts_root = Path(project["artifacts_path"])
    artifacts_root.mkdir(parents=True, exist_ok=True)
    master_path = None
    if isinstance(classification, dict):
        master_path = classification.get("master_path") or classification.get("master_stored_path")
    master_stem = Path(str(master_path)).stem if isinstance(master_path, str) and master_path else "master"
    project_slug = _slugify(str(project.get("name") or "vazer"))
    return {
        "root": artifacts_root,
        "project_root": artifacts_root.parent,
        "state_path": artifacts_root.parent / "vazer.state.json",
        "sync_partial_path": artifacts_root / "vazer.sync_map.partial.json",
        "sync_map_path": artifacts_root / "vazer.sync_map.json",
        "camera_roles_path": artifacts_root / "vazer.camera_roles.json",
        "camera_roles_dir": artifacts_root / "vazer.camera_roles",
        "transcript_path": artifacts_root / f"{master_stem}.transcript.json",
        "analysis_map_path": artifacts_root / "vazer.analysis_map.json",
        "planning_root": artifacts_root / f"vazer.{project_slug}.planning",
        "visual_packet_path": artifacts_root / "vazer.visual_packet.json",
        "cut_plan_ai_path": artifacts_root / "vazer.cut_plan.ai.json",
        "cut_validation_path": artifacts_root / "vazer.cut_validation.json",
        "cut_plan_repaired_path": artifacts_root / "vazer.cut_plan.repaired.json",
        "cut_plan_render_path": artifacts_root / "vazer.cut_plan.repaired.fhd.json",
        "render_root": artifacts_root / "vazer.render",
    }


def _project_data_root(project: dict[str, Any]) -> Path:
    artifacts_root = Path(project["artifacts_path"])
    if artifacts_root.name.lower() == "artifacts":
        return artifacts_root.parent
    return artifacts_root


def _iter_legacy_artifact_paths(common_parent: Path) -> list[Path]:
    candidates: list[Path] = []
    for name in sorted(LEGACY_ROOT_ARTIFACT_FILES):
        candidate = common_parent / name
        if candidate.exists():
            candidates.append(candidate)
    for pattern in ("*.transcript.json", "vazer.*.planning", "vazer.camera_roles", "vazer.render"):
        for candidate in sorted(common_parent.glob(pattern)):
            if candidate.name == "VAZer":
                continue
            if candidate.exists():
                candidates.append(candidate)
    return candidates


def _load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _load_reusable_transcript_artifact(path: Path, master_path: str) -> dict[str, Any] | None:
    payload = _load_json_if_exists(path)
    if payload is None or payload.get("schema_version") != "vazer.transcript.v1":
        return None

    master_audio = payload.get("master_audio") or {}
    if not isinstance(master_audio, dict) or not _same_media_path(master_audio.get("path"), master_path):
        return None

    chunks = payload.get("chunks")
    summary = payload.get("summary") or {}
    if not isinstance(chunks, list) or not chunks:
        return None
    if int(summary.get("chunk_count") or 0) < 1:
        return None

    text = str(payload.get("text") or "").strip()
    if not text:
        return None

    try:
        media_info = probe_media(master_path)
        duration_seconds = float(media_info.duration_seconds or 0.0)
    except Exception:
        duration_seconds = 0.0

    if duration_seconds > 0:
        chunk_end = max(float(chunk.get("end_seconds") or 0.0) for chunk in chunks if isinstance(chunk, dict))
        if chunk_end < max(1.0, duration_seconds - 5.0):
            return None

    return payload


def _load_reusable_analysis_map(path: Path, master_path: str, camera_paths: list[str]) -> dict[str, Any] | None:
    payload = _load_json_if_exists(path)
    if payload is None or payload.get("schema_version") != "vazer.analysis_map.v1":
        return None

    master_payload = payload.get("master") or {}
    if not isinstance(master_payload, dict) or not _same_media_path(master_payload.get("path"), master_path):
        return None

    entries = payload.get("entries")
    if not isinstance(entries, list):
        return None

    analyzed_paths = {
        str(entry.get("path"))
        for entry in entries
        if isinstance(entry, dict) and entry.get("status") == "analyzed" and isinstance(entry.get("path"), str)
    }
    if not all(path in analyzed_paths for path in camera_paths):
        return None

    return payload


def _load_reusable_cut_plan(
    path: Path,
    master_path: str,
    camera_paths: list[str],
    *,
    planning_stage: str | None = None,
    source_sync_map_path: str | None = None,
    source_analysis_map_path: str | None = None,
    source_transcript_path: str | None = None,
) -> dict[str, Any] | None:
    payload = _load_json_if_exists(path)
    if payload is None or payload.get("schema_version") != "vazer.cut_plan.v1":
        return None

    if planning_stage is not None and str(payload.get("planning_stage") or "").strip().lower() != planning_stage:
        return None

    master_audio = payload.get("master_audio") or {}
    if not isinstance(master_audio, dict) or not _same_media_path(master_audio.get("path"), master_path):
        return None

    video_segments = payload.get("video_segments")
    if not isinstance(video_segments, list) or not video_segments:
        return None

    camera_path_set = set(camera_paths)
    referenced_paths = {
        str(segment.get("asset_path") or "")
        for segment in video_segments
        if isinstance(segment, dict)
    }
    if not referenced_paths or not referenced_paths.issubset(camera_path_set):
        return None

    if source_sync_map_path is not None:
        source_sync_map = payload.get("source_sync_map") or {}
        if not isinstance(source_sync_map, dict) or not _same_media_path(source_sync_map.get("path"), source_sync_map_path):
            return None

    if source_analysis_map_path is not None:
        source_analysis_map = payload.get("source_analysis_map") or {}
        if not isinstance(source_analysis_map, dict) or not _same_media_path(
            source_analysis_map.get("path"),
            source_analysis_map_path,
        ):
            return None

    if source_transcript_path is not None:
        source_transcript = payload.get("source_transcript") or {}
        if not isinstance(source_transcript, dict) or not _same_media_path(
            source_transcript.get("path"),
            source_transcript_path,
        ):
            return None

    return payload


def _load_reusable_cut_validation(
    path: Path,
    *,
    source_cut_plan_path: str | None = None,
    source_sync_map_path: str | None = None,
    source_analysis_map_path: str | None = None,
    source_transcript_path: str | None = None,
) -> dict[str, Any] | None:
    payload = _load_json_if_exists(path)
    if payload is None or payload.get("schema_version") != "vazer.cut_validation.v1":
        return None

    expected_sources = (
        ("source_cut_plan", source_cut_plan_path),
        ("source_sync_map", source_sync_map_path),
        ("source_analysis_map", source_analysis_map_path),
        ("source_transcript", source_transcript_path),
    )
    for source_key, expected_path in expected_sources:
        if expected_path is None:
            continue
        source_payload = payload.get(source_key) or {}
        if not isinstance(source_payload, dict) or not _same_media_path(source_payload.get("path"), expected_path):
            return None

    summary = payload.get("summary")
    cuts = payload.get("cuts")
    if not isinstance(summary, dict) or not isinstance(cuts, list):
        return None

    return payload


def _load_reusable_sync_map(
    path: Path,
    master_path: str,
    camera_paths: list[str],
    *,
    require_complete: bool,
) -> dict[str, Any] | None:
    payload = _load_json_if_exists(path)
    if payload is None or payload.get("schema_version") != "vazer.sync_map.v1":
        return None

    master_payload = payload.get("master") or {}
    if not isinstance(master_payload, dict) or not _same_media_path(master_payload.get("path"), master_path):
        return None

    entries = payload.get("entries")
    if not isinstance(entries, list):
        return None

    relevant_entries_by_path: dict[str, dict[str, Any]] = {}
    camera_path_set = set(camera_paths)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_path = str(entry.get("path") or "")
        if entry_path not in camera_path_set:
            continue
        status = str(entry.get("status") or "").lower()
        if require_complete:
            if status not in {"synced", "failed"}:
                continue
        elif status != "synced":
            continue
        relevant_entries_by_path[entry_path] = entry

    if require_complete:
        if not all(path in relevant_entries_by_path for path in camera_paths):
            return None
    elif not relevant_entries_by_path:
        return None

    filtered_entries = [relevant_entries_by_path[path] for path in camera_paths if path in relevant_entries_by_path]
    reusable_payload = dict(payload)
    reusable_payload["entries"] = filtered_entries
    reusable_payload["summary"] = {
        "total": len(filtered_entries),
        "synced": sum(1 for entry in filtered_entries if str(entry.get("status") or "") == "synced"),
        "failed": sum(1 for entry in filtered_entries if str(entry.get("status") or "") == "failed"),
    }
    return reusable_payload


def _load_reusable_camera_roles_artifact(path: Path, camera_paths: list[str]) -> dict[str, Any] | None:
    payload = _load_json_if_exists(path)
    if payload is None or payload.get("schema_version") != "vazer.camera_roles.v1":
        return None

    assignments = payload.get("assignments")
    if not isinstance(assignments, list):
        return None

    valid_roles = {"totale", "halbtotale", "close"}
    assignments_by_path: dict[str, dict[str, Any]] = {}
    for assignment in assignments:
        if not isinstance(assignment, dict):
            continue
        assignment_path = str(assignment.get("path") or "")
        if not assignment_path:
            continue
        role = str(assignment.get("role") or "").strip().lower()
        if role not in valid_roles:
            continue
        assignments_by_path[assignment_path] = assignment

    if not all(camera_path in assignments_by_path for camera_path in camera_paths):
        return None

    filtered_payload = dict(payload)
    filtered_payload["assignments"] = [assignments_by_path[camera_path] for camera_path in camera_paths]
    return filtered_payload

INDEX_HTML = """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>VAZer UI</title>
  <style>
    :root{--bg:#101319;--card:#171c24;--card2:#202734;--line:rgba(255,255,255,.08);--text:#f3eee3;--muted:#b9b2a3;--accent:#ef9d4d;--accent2:#ef6b3c;--ok:#7fd0a6;--warn:#eec46d;--bad:#e47a7a;--font:"Aptos","Segoe UI Variable","Segoe UI",sans-serif;--display:"Bahnschrift","Trebuchet MS",sans-serif}
    *{box-sizing:border-box}body{margin:0;color:var(--text);font-family:var(--font);background:
    radial-gradient(circle at 15% 10%,rgba(239,157,77,.18),transparent 24%),
    linear-gradient(160deg,#0f1217 0%,#171b22 55%,#0d1014 100%)}
    .wrap{width:min(1180px,calc(100vw - 28px));margin:24px auto 48px}
    .grid{display:grid;gap:18px}.hero{grid-template-columns:1.3fr 1fr}.body{grid-template-columns:1.1fr .9fr}
    .panel{background:rgba(23,28,36,.88);border:1px solid var(--line);border-radius:20px;padding:22px;box-shadow:0 22px 60px rgba(0,0,0,.3)}
    .eyebrow{margin:0 0 8px;color:var(--accent);font-size:12px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}
    h1{margin:0;font:700 clamp(28px,4vw,46px)/.95 var(--display);letter-spacing:-.03em}
    .sub,.muted,.mini{color:var(--muted);line-height:1.5}.mini{font-size:13px}
    .drop{min-height:220px;border:1.5px dashed rgba(239,157,77,.45);border-radius:24px;padding:24px;background:linear-gradient(180deg,rgba(239,157,77,.08),rgba(239,107,60,.03));display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;cursor:pointer;transition:.14s transform ease,.14s border-color ease}
    .drop.dragover{transform:translateY(-1px);border-color:var(--accent2)}
    .drop h2{margin:0 0 10px;font:700 24px/1 var(--display)}
    .actions,.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.row{justify-content:space-between}
    button,select{appearance:none;border:0;border-radius:999px;padding:11px 16px;font:700 14px var(--font)}
    button{cursor:pointer}
    select{background:rgba(255,255,255,.06);color:var(--text);border:1px solid var(--line);padding-right:38px}
    .modeControl{display:flex;gap:10px;align-items:center;color:var(--muted);font-size:13px;font-weight:700;letter-spacing:.03em}
    .solid{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#18110d}.ghost{background:rgba(255,255,255,.06);color:var(--text);border:1px solid var(--line)}button:disabled{opacity:.45;cursor:default}
    .metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:18px}
    .metric,.card{background:rgba(255,255,255,.04);border:1px solid var(--line);border-radius:16px;padding:14px}
    .metric small{display:block;color:var(--muted);text-transform:uppercase;letter-spacing:.12em;font-size:12px}.metric strong{display:block;margin-top:8px;font-size:22px}
    .stack{display:grid;gap:12px}.badge{display:inline-flex;padding:6px 10px;border-radius:999px;font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.08em}
    .queued{background:rgba(255,255,255,.08);color:var(--muted)}.running,.completed{background:rgba(127,208,166,.14);color:var(--ok)}.paused,.pause_requested,.needs_attention{background:rgba(238,196,109,.14);color:var(--warn)}.failed{background:rgba(228,122,122,.14);color:var(--bad)}
    .progress{height:10px;border-radius:999px;overflow:hidden;background:rgba(255,255,255,.08);margin:12px 0}.progress>span{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2))}
    .meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:10px}.meta div{color:var(--muted);font-size:13px;line-height:1.4}.meta strong{display:block;color:var(--text);font-size:14px;margin-bottom:2px}
    code{font-family:"Cascadia Code","Consolas",monospace;color:#ffddb6}input[type=file]{display:none}
    @media (max-width:980px){.hero,.body,.metrics,.meta{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="grid hero">
      <div class="panel">
        <p class="eyebrow">Theater VAZ</p>
        <h1>Clips rein. Fortschritt sichtbar. Pause und Resume im Browser.</h1>
        <p class="sub">Dieses Minimal-Interface kopiert gedroppte Dateien lokal ins Workspace, prueft die Medien und baut im Hintergrund ein <code>sync_map</code>.</p>
        <div class="metrics" id="metrics"></div>
      </div>
      <div class="panel">
        <label class="drop" id="dropzone" for="picker">
          <input id="picker" type="file" multiple>
          <h2>Clips hier droppen</h2>
          <p class="sub" style="max-width:32ch;margin:0">Oder klicken und mehrere Dateien waehlen. Fuer den ersten Wurf werden die Dateien lokal hochgeladen.</p>
        </label>
        <div class="actions" style="margin-top:14px">
          <button class="solid" id="pickButton" type="button">Dateien auswaehlen</button>
          <button class="ghost" id="refreshButton" type="button">Status aktualisieren</button>
          <label class="modeControl" for="outputMode"><span>Output</span><select id="outputMode"><option value="render_and_premiere">MP4 + Premiere XML</option><option value="premiere_only">Nur Premiere XML</option></select></label>
        </div>
      </div>
    </section>
    <section class="grid body" style="margin-top:18px">
      <div class="panel">
        <div class="row" style="margin-bottom:14px"><h2 style="margin:0">Jobs</h2><div class="mini" id="workspaceLabel"></div></div>
        <div class="stack" id="uploadArea"></div>
        <div class="stack" id="jobs"></div>
      </div>
      <div class="panel">
        <div class="row" style="margin-bottom:14px"><h2 style="margin:0">Projekte</h2><div class="mini">Zuletzt zuerst</div></div>
        <div class="stack" id="projects"></div>
      </div>
    </section>
  </div>
  <script>
    const state={upload:null,snapshot:null};
    const dropzone=document.getElementById("dropzone");
    const picker=document.getElementById("picker");
    const pickButton=document.getElementById("pickButton");
    const refreshButton=document.getElementById("refreshButton");
    const outputModeEl=document.getElementById("outputMode");
    const uploadArea=document.getElementById("uploadArea");
    const jobsEl=document.getElementById("jobs");
    const projectsEl=document.getElementById("projects");
    const metricsEl=document.getElementById("metrics");
    const workspaceLabel=document.getElementById("workspaceLabel");

    async function fetchJson(url,options={}){const response=await fetch(url,options);const payload=await response.json().catch(()=>({error:response.statusText}));if(!response.ok)throw new Error(payload.error||response.statusText);return payload;}
    function fmtTime(value){return value?new Date(value).toLocaleString("de-DE"):"-";}
    function outputModeLabel(value){return value==="premiere_only"?"Nur Premiere XML":"MP4 + Premiere XML";}
    function renderMetrics(snapshot){const jobs=snapshot?.jobs||[];const projects=snapshot?.projects||[];const running=jobs.filter(job=>["running","pause_requested","paused"].includes(job.status)).length;const completed=jobs.filter(job=>job.status==="completed").length;metricsEl.innerHTML=[["Projekte",projects.length],["Aktiv",running],["Fertig",completed]].map(metric=>`<div class="metric"><small>${metric[0]}</small><strong>${metric[1]}</strong></div>`).join("");}
    function renderUpload(){if(!state.upload){uploadArea.innerHTML="";return;}const upload=state.upload;const progress=upload.totalBytes>0?Math.min(100,Math.round(upload.sentBytes/upload.totalBytes*100)):0;uploadArea.innerHTML=`<div class="card"><div class="row"><strong>Upload laeuft</strong><span class="badge running">${upload.phase}</span></div><div class="progress"><span style="width:${progress}%"></span></div><div class="mini">${upload.message}</div><div class="meta"><div><strong>Dateien</strong>${upload.fileIndex}/${upload.fileCount}</div><div><strong>Fortschritt</strong>${progress}%</div></div></div>`;}
    function renderJobs(snapshot){const jobs=snapshot?.jobs||[];if(!jobs.length){jobsEl.innerHTML='<div class="muted">Noch keine Jobs. Zieh oben ein paar Clips hinein.</div>';return;}jobsEl.innerHTML=jobs.map(job=>`<div class="card"><div class="row"><strong>${job.project_name}</strong><span class="badge ${job.status}">${job.status.replace("_"," ")}</span></div><div class="progress"><span style="width:${job.progress_percent||0}%"></span></div><div class="mini">${job.stage_label||"wartend"} · ${job.message||"-"}</div><div class="meta"><div><strong>Aktualisiert</strong>${fmtTime(job.updated_at_utc)}</div><div><strong>Fortschritt</strong>${Math.round(job.progress_percent||0)}%</div><div><strong>Master</strong>${job.details?.master_asset||"-"}</div><div><strong>Kameras</strong>${job.details?.camera_count??"-"}</div></div><div class="actions" style="margin-top:12px"><button class="ghost" ${job.status==="running"||job.status==="pause_requested"?"":"disabled"} onclick="pauseJob('${job.id}')">Pause</button><button class="solid" ${job.status==="paused"?"":"disabled"} onclick="resumeJob('${job.id}')">Weiter</button></div></div>`).join("");}
    function renderProjects(snapshot){const projects=snapshot?.projects||[];if(!projects.length){projectsEl.innerHTML='<div class="muted">Noch keine Projekte im Workspace.</div>';return;}projectsEl.innerHTML=projects.map(project=>{const classification=project.classification||{};const files=(project.files||[]).map(file=>file.original_path).join("<br>");const artifacts=project.artifacts||{};const syncLine=artifacts.sync_map_path?`sync_map: <code>${artifacts.sync_map_path}</code>`:"Noch kein sync_map geschrieben.";const premierePath=artifacts.premiere_xml_path||artifacts.premiere_project_path;const premiereLine=premierePath?`Premiere XML: <code>${premierePath}</code>`:"Noch kein Premiere XML geschrieben.";return `<div class="card"><div class="row"><strong>${project.name}</strong><span class="mini">${fmtTime(project.created_at_utc)}</span></div><div class="meta"><div><strong>Master</strong>${classification.master_asset||"nicht erkannt"}</div><div><strong>Kameras</strong>${classification.camera_count??0}</div><div><strong>Output</strong>${outputModeLabel(project.output_mode)}</div><div><strong>Projekt</strong>${premierePath?"XML bereit":"In Arbeit"}</div></div><div class="mini" style="margin-top:8px">${files}</div><div class="mini" style="margin-top:10px">${syncLine}<br>${premiereLine}</div></div>`;}).join("");}
    function render(snapshot){state.snapshot=snapshot;workspaceLabel.textContent=snapshot.workspace||"";renderMetrics(snapshot);renderUpload();renderJobs(snapshot);renderProjects(snapshot);}
    async function loadState(){try{render(await fetchJson("/api/state"));}catch(error){console.error(error);}}
    function uploadSingleFile(sessionId,file,index,totalCount,totalBytes,counter){return new Promise((resolve,reject)=>{const path=encodeURIComponent(file.webkitRelativePath||file.name);const xhr=new XMLHttpRequest();xhr.open("POST",`/api/uploads/${sessionId}/files?path=${path}`);xhr.upload.onprogress=(event)=>{if(!state.upload)return;state.upload={...state.upload,phase:"upload",fileIndex:index,fileCount:totalCount,sentBytes:counter.baseBytes+event.loaded,totalBytes,message:`${file.name} wird hochgeladen`};renderUpload();};xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300){counter.baseBytes+=file.size;resolve(JSON.parse(xhr.responseText));return;}reject(new Error(xhr.responseText||`Upload fehlgeschlagen: ${file.name}`));};xhr.onerror=()=>reject(new Error(`Upload fehlgeschlagen: ${file.name}`));xhr.send(file);});}
    async function handleFiles(fileList){const files=Array.from(fileList||[]);if(!files.length)return;const totalBytes=files.reduce((sum,file)=>sum+file.size,0);state.upload={phase:"vorbereitung",fileIndex:0,fileCount:files.length,sentBytes:0,totalBytes,message:"Upload-Session wird erstellt"};renderUpload();try{const session=await fetchJson("/api/uploads/session",{method:"POST"});const counter={baseBytes:0};for(let index=0;index<files.length;index+=1){await uploadSingleFile(session.session_id,files[index],index+1,files.length,totalBytes,counter);}state.upload={...state.upload,phase:"projekt",sentBytes:totalBytes,message:"Projekt und Hintergrundjob werden angelegt"};renderUpload();const name=(files[0]?.name||"VAZ Projekt").replace(/\\.[^.]+$/,"");await fetchJson("/api/projects",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({session_id:session.session_id,name,output_mode:outputModeEl?.value||"render_and_premiere"})});}catch(error){alert(error.message);}finally{state.upload=null;renderUpload();await loadState();}}
    async function pauseJob(jobId){await fetchJson(`/api/jobs/${jobId}/pause`,{method:"POST"});await loadState();}
    async function resumeJob(jobId){await fetchJson(`/api/jobs/${jobId}/resume`,{method:"POST"});await loadState();}
    window.pauseJob=pauseJob;window.resumeJob=resumeJob;
    dropzone.addEventListener("dragover",event=>{event.preventDefault();dropzone.classList.add("dragover");});
    dropzone.addEventListener("dragleave",()=>dropzone.classList.remove("dragover"));
    dropzone.addEventListener("drop",event=>{event.preventDefault();dropzone.classList.remove("dragover");handleFiles(event.dataTransfer.files);});
    picker.addEventListener("change",event=>handleFiles(event.target.files));
    pickButton.addEventListener("click",()=>picker.click());
    refreshButton.addEventListener("click",loadState);
    loadState();setInterval(loadState,1500);
  </script>
</body>
</html>
"""


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _derive_asset_ids(paths: list[str]) -> list[str]:
    stems = [Path(path).stem.strip() or "asset" for path in paths]
    counts = Counter(stems)
    seen: dict[str, int] = {}
    asset_ids: list[str] = []
    for stem in stems:
        if counts[stem] == 1:
            asset_ids.append(stem)
            continue
        next_index = seen.get(stem, 0) + 1
        seen[stem] = next_index
        asset_ids.append(f"{stem}_{next_index:02d}")
    return asset_ids


def _safe_relative_path(path_text: str) -> Path:
    normalized = path_text.replace("\\", "/").strip()
    if not normalized:
        raise ValueError("Upload path is empty.")
    pure_path = PurePosixPath(normalized)
    clean_parts: list[str] = []
    for part in pure_path.parts:
        if part in {"", ".", "/"}:
            continue
        if part == ".." or ":" in part:
            raise ValueError("Upload path contains invalid traversal segments.")
        clean_parts.append(part)
    if not clean_parts:
        raise ValueError("Upload path did not contain a usable filename.")
    return Path(*clean_parts)


def _media_info_to_dict(media_info: MediaInfo) -> dict[str, Any]:
    return {
        "path": media_info.path,
        "format_name": media_info.format_name,
        "duration_seconds": media_info.duration_seconds,
        "audio_stream_count": len(media_info.audio_streams),
        "video_stream_count": len(media_info.video_streams),
        "primary_video": None
        if not media_info.video_streams
        else {
            "absolute_stream_index": media_info.video_streams[0].absolute_stream_index,
            "codec_name": media_info.video_streams[0].codec_name,
            "duration_seconds": media_info.video_streams[0].duration_seconds,
            "width": media_info.video_streams[0].width,
            "height": media_info.video_streams[0].height,
            "frame_rate": media_info.video_streams[0].frame_rate,
        },
    }


def _format_camera_note(role: str | None, message: str) -> str:
    normalized_role = str(role or "").strip().lower()
    if normalized_role in {"totale", "halbtotale", "close"}:
        return f"{normalized_role} | {message}"
    return message


def _build_sync_entry(asset_id: str, camera_path: str, report: dict[str, Any]) -> dict[str, Any]:
    media_info = probe_media(camera_path)
    selected_stream = report["camera"]["selected_stream"]
    if not report["summary"]["validated"]:
        return {
            "asset_id": asset_id,
            "path": camera_path,
            "status": "failed",
            "error": " ".join(report["summary"]["errors"]),
            "selected_stream": {
                "map_specifier": selected_stream["map_specifier"],
                "absolute_stream_index": selected_stream["absolute_stream_index"],
            },
            "coarse": report["coarse"],
            "anchors": report["anchors"],
            "summary": report["summary"],
        }

    return {
        "asset_id": asset_id,
        "path": camera_path,
        "status": "synced",
        "media": _media_info_to_dict(media_info),
        "selected_stream": {
            "map_specifier": selected_stream["map_specifier"],
            "absolute_stream_index": selected_stream["absolute_stream_index"],
        },
        "mapping": report["mapping"],
        "coarse": report["coarse"],
        "anchors": report["anchors"],
        "summary": report["summary"],
    }


class JobCanceledError(RuntimeError):
    pass


class UIState:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.sessions_root = self.workspace / "sessions"
        self.projects_root = self.workspace / "projects"
        self.state_path = self.workspace / "state.json"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._projects: dict[str, dict[str, Any]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._runtimes: dict[str, dict[str, Any]] = {}
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
        except Exception:
            return

        for project in payload.get("projects", []) if isinstance(payload.get("projects"), list) else []:
            if isinstance(project, dict) and isinstance(project.get("id"), str):
                project["output_mode"] = normalize_output_mode(project.get("output_mode"))
                if not isinstance(project.get("artifacts_path"), str) or not project.get("artifacts_path"):
                    project["artifacts_path"] = str(Path(project["root_path"]) / "artifacts")
                self._projects[project["id"]] = project

        for job in payload.get("jobs", []) if isinstance(payload.get("jobs"), list) else []:
            if not isinstance(job, dict) or not isinstance(job.get("id"), str):
                continue
            if job.get("status") in {"running", "pause_requested", "queued", "review_required"}:
                job["status"] = "failed"
                job["message"] = "Server restart interrupted the previous background job."
                job["stage_label"] = "unterbrochen"
            self._jobs[job["id"]] = job

    def _persist_state(self) -> None:
        payload = {
            "schema_version": "vazer.ui_state.v1",
            "generated_at_utc": _utc_timestamp(),
            "projects": list(self._projects.values()),
            "jobs": list(self._jobs.values()),
        }
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: item["created_at_utc"], reverse=True)
            projects = sorted(self._projects.values(), key=lambda item: item["created_at_utc"], reverse=True)
            payload = {
                "schema_version": "vazer.ui_snapshot.v1",
                "generated_at_utc": _utc_timestamp(),
                "workspace": str(self.workspace),
                "tool": {
                    "name": "vazer",
                    "version": __version__,
                },
                "jobs": jobs,
                "projects": projects,
            }
        return json.loads(json.dumps(payload))

    def create_upload_session(self) -> dict[str, Any]:
        session_id = f"up_{uuid.uuid4().hex[:10]}"
        session_dir = self.sessions_root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return {"session_id": session_id}

    def write_upload_file(self, session_id: str, relative_path: str, body_stream: Any, content_length: int) -> dict[str, Any]:
        session_dir = self.sessions_root / session_id
        if not session_dir.exists():
            raise ValueError("Unknown upload session.")

        safe_relative = _safe_relative_path(relative_path)
        destination = session_dir / safe_relative
        destination.parent.mkdir(parents=True, exist_ok=True)

        remaining = content_length
        with destination.open("wb") as handle:
            while remaining > 0:
                chunk = body_stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                handle.write(chunk)
                remaining -= len(chunk)

        if remaining != 0:
            raise ValueError(f"Incomplete upload for {relative_path}.")

        return {
            "session_id": session_id,
            "stored_path": str(destination),
            "relative_path": str(safe_relative),
            "size_bytes": destination.stat().st_size,
        }

    def create_project(
        self,
        session_id: str,
        name: str | None = None,
        output_mode: str | None = None,
    ) -> dict[str, Any]:
        session_dir = self.sessions_root / session_id
        if not session_dir.exists():
            raise ValueError("Unknown upload session.")

        file_paths = sorted(
            path for path in session_dir.rglob("*") if path.is_file() and not should_ignore_import_file(path)
        )
        if not file_paths:
            raise ValueError("The upload session does not contain any files.")

        project_id = f"proj_{uuid.uuid4().hex[:10]}"
        project_name = (name or f"Projekt {datetime.now().strftime('%Y-%m-%d %H-%M')}").strip() or project_id
        normalized_output_mode = normalize_output_mode(output_mode)
        project_root = self.projects_root / project_id
        inputs_root = project_root / "inputs"
        artifacts_root = project_root / "artifacts"
        artifacts_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(session_dir), str(inputs_root))

        files = [
            {
                "original_path": str(path.relative_to(inputs_root)),
                "stored_path": str(path),
                "source_mode": "uploaded_copy",
                "display_name": path.name,
                "ui_status": "queued",
                "ui_note": "Waiting to start.",
            }
            for path in sorted(inputs_root.rglob("*"))
            if path.is_file()
        ]
        return self._register_project_and_start_job(
            project_id=project_id,
            project_name=project_name,
            project_root=project_root,
            inputs_path=str(inputs_root),
            default_output_dir=None,
            artifacts_path_override=None,
            files=files,
            output_mode=normalized_output_mode,
        )

    def create_project_from_paths(
        self,
        paths: list[str],
        name: str | None = None,
        output_mode: str | None = None,
        *,
        reset_existing: bool = False,
    ) -> dict[str, Any]:
        resolved_paths = [
            Path(path).expanduser().resolve()
            for path in paths
            if not should_ignore_import_file(Path(path).expanduser())
        ]
        if not resolved_paths:
            raise ValueError("At least one file path is required.")
        missing = [path for path in resolved_paths if not path.is_file()]
        if missing:
            raise ValueError(f"One or more files do not exist: {missing[0]}")

        if reset_existing:
            self.reset_existing_source_run([str(path) for path in resolved_paths])
        self._prepare_source_project_dir([str(path) for path in resolved_paths])

        project_id = f"proj_{uuid.uuid4().hex[:10]}"
        project_name = (name or resolved_paths[0].stem or project_id).strip() or project_id
        normalized_output_mode = normalize_output_mode(output_mode)
        project_root = self.projects_root / project_id
        project_root.mkdir(parents=True, exist_ok=True)
        files = [
            {
                "original_path": str(path),
                "stored_path": str(path),
                "source_mode": "external_reference",
                "display_name": path.name,
                "ui_status": "queued",
                "ui_note": "Waiting to start.",
            }
            for path in resolved_paths
        ]
        return self._register_project_and_start_job(
            project_id=project_id,
            project_name=project_name,
            project_root=project_root,
            inputs_path=None,
            default_output_dir=resolve_default_output_dir(resolved_paths),
            artifacts_path_override=resolve_default_artifacts_dir(resolved_paths),
            files=files,
            output_mode=normalized_output_mode,
        )

    def _prepare_source_project_dir(self, paths: list[str]) -> dict[str, Any] | None:
        resolved_paths = [
            Path(path).expanduser().resolve()
            for path in paths
            if not should_ignore_import_file(Path(path).expanduser())
        ]
        if not resolved_paths:
            return None
        project_dir = resolve_default_project_dir(resolved_paths)
        if project_dir is None:
            return None
        project_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir = project_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        output_dir = project_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        common_parent = project_dir.parent
        migrated: list[str] = []
        for legacy_path in _iter_legacy_artifact_paths(common_parent):
            destination = artifacts_dir / legacy_path.name
            if legacy_path.is_dir():
                if destination.exists():
                    continue
                shutil.move(str(legacy_path), str(destination))
                migrated.append(legacy_path.name)
                continue
            if destination.exists():
                continue
            shutil.move(str(legacy_path), str(destination))
            migrated.append(legacy_path.name)
        return {
            "project_dir": str(project_dir),
            "artifacts_dir": str(artifacts_dir),
            "output_dir": str(output_dir),
            "migrated": migrated,
        }

    def inspect_existing_source_run(self, paths: list[str]) -> dict[str, Any] | None:
        resolved_paths = [
            Path(path).expanduser().resolve()
            for path in paths
            if not should_ignore_import_file(Path(path).expanduser())
        ]
        if not resolved_paths:
            return None
        project_dir = resolve_default_project_dir(resolved_paths)
        if project_dir is None:
            return None
        common_parent = project_dir.parent
        legacy_paths = _iter_legacy_artifact_paths(common_parent)
        state_path = project_dir / "vazer.state.json"
        artifacts_dir = project_dir / "artifacts"
        output_dir = project_dir / "output"
        if not project_dir.exists() and not legacy_paths:
            return None

        state_payload = _load_json_if_exists(state_path)
        latest_job = state_payload.get("latest_job") if isinstance(state_payload, dict) else None
        latest_status = str((latest_job or {}).get("status") or "").strip().lower()
        latest_stage = str((latest_job or {}).get("stage_label") or (latest_job or {}).get("stage") or "").strip()
        saved_output_mode = state_payload.get("output_mode") if isinstance(state_payload, dict) else None
        artifact_flags = {
            "project_state": state_path.exists(),
            "camera_roles": (artifacts_dir / "vazer.camera_roles.json").exists(),
            "sync_partial": (artifacts_dir / "vazer.sync_map.partial.json").exists(),
            "sync_complete": (artifacts_dir / "vazer.sync_map.json").exists(),
            "transcript": any(artifacts_dir.glob("*.transcript.json")),
            "analysis": (artifacts_dir / "vazer.analysis_map.json").exists(),
            "planning": (artifacts_dir / "vazer.cut_plan.ai.json").exists(),
            "validation": (artifacts_dir / "vazer.cut_validation.json").exists(),
            "repair": (artifacts_dir / "vazer.cut_plan.repaired.json").exists(),
            "render_cut": (artifacts_dir / "vazer.cut_plan.repaired.fhd.json").exists(),
            "premiere_xml": any(output_dir.glob("*.premiere.xml")),
            "premiere_project": any(output_dir.glob("*.prproj")),
            "render": any(output_dir.glob("*.mp4")),
        }
        artifact_flags["premiere"] = artifact_flags["premiere_xml"] or artifact_flags["premiere_project"]

        if latest_status == "completed" or artifact_flags["render"] or artifact_flags["premiere"]:
            summary = "Abgeschlossen"
        elif latest_status in {"running", "pause_requested", "paused", "review_required", "queued"}:
            summary = f"Unterbrochen bei {latest_stage or 'einem Verarbeitungsschritt'}"
        elif latest_status == "failed":
            summary = f"Fehler bei {latest_stage or 'einem Verarbeitungsschritt'}"
        elif artifact_flags["repair"] or artifact_flags["planning"]:
            summary = "Teilweise fertig bis Schnittplanung"
        elif artifact_flags["analysis"]:
            summary = "Teilweise fertig bis Analyse"
        elif artifact_flags["transcript"]:
            summary = "Teilweise fertig bis Transcript"
        elif artifact_flags["sync_complete"] or artifact_flags["sync_partial"]:
            summary = "Teilweise fertig bis Sync"
        elif artifact_flags["camera_roles"]:
            summary = "Teilweise fertig bis Rollen-Erkennung"
        elif legacy_paths:
            summary = "Alte VAZer-Artefakte im Medienordner gefunden"
        else:
            summary = "VAZer-Projektordner vorhanden"

        return {
            "project_dir": str(project_dir),
            "artifacts_dir": str(artifacts_dir),
            "output_dir": str(output_dir),
            "state_path": str(state_path),
            "summary": summary,
            "latest_status": latest_status or None,
            "latest_stage": latest_stage or None,
            "output_mode": saved_output_mode,
            "artifact_flags": artifact_flags,
            "legacy_count": len(legacy_paths),
        }

    def reset_existing_source_run(self, paths: list[str]) -> None:
        resolved_paths = [
            Path(path).expanduser().resolve()
            for path in paths
            if not should_ignore_import_file(Path(path).expanduser())
        ]
        if not resolved_paths:
            return
        project_dir = resolve_default_project_dir(resolved_paths)
        if project_dir is not None and project_dir.exists():
            shutil.rmtree(project_dir, ignore_errors=True)
        common_parent = project_dir.parent if project_dir is not None else None
        if common_parent is not None:
            for legacy_path in _iter_legacy_artifact_paths(common_parent):
                if legacy_path.is_dir():
                    shutil.rmtree(legacy_path, ignore_errors=True)
                else:
                    try:
                        legacy_path.unlink()
                    except FileNotFoundError:
                        pass

    def _register_project_and_start_job(
        self,
        *,
        project_id: str,
        project_name: str,
        project_root: Path,
        inputs_path: str | None,
        default_output_dir: str | None,
        artifacts_path_override: str | None,
        files: list[dict[str, Any]],
        output_mode: str,
    ) -> dict[str, Any]:
        artifacts_root = (
            Path(artifacts_path_override).expanduser().resolve()
            if artifacts_path_override
            else project_root / "artifacts"
        )
        artifacts_root.mkdir(parents=True, exist_ok=True)
        fallback_output_dir = project_root / "output"
        normalized_output_mode = normalize_output_mode(output_mode)
        project = {
            "schema_version": "vazer.ui_project.v1",
            "id": project_id,
            "name": project_name,
            "created_at_utc": _utc_timestamp(),
            "updated_at_utc": _utc_timestamp(),
            "root_path": str(project_root),
            "inputs_path": inputs_path,
            "artifacts_path": str(artifacts_root),
            "default_output_dir": default_output_dir or str(fallback_output_dir),
            "output_mode": normalized_output_mode,
            "files": files,
            "classification": {},
            "artifacts": {},
            "job_ids": [],
        }

        job_id = f"job_{uuid.uuid4().hex[:10]}"
        job = {
            "id": job_id,
            "project_id": project_id,
            "project_name": project_name,
            "created_at_utc": _utc_timestamp(),
            "updated_at_utc": _utc_timestamp(),
            "status": "queued",
            "stage": "queued",
            "stage_label": "wartend",
            "message": "Job ist angelegt und startet gleich.",
            "progress_percent": 0.0,
            "details": {
                "file_count": len(project["files"]),
                "camera_count": 0,
                "master_asset": None,
                "output_mode": normalized_output_mode,
            },
            "artifacts": {},
        }

        with self._lock:
            project["job_ids"].append(job_id)
            self._projects[project_id] = project
            self._jobs[job_id] = job
            self._runtimes[job_id] = {
                "condition": threading.Condition(),
                "pause_requested": False,
                "awaiting_confirmation": False,
                "cancel_requested": False,
                "executor": None,
            }
            self._persist_state()
        self._write_project_manifest(project_id)

        thread = threading.Thread(target=self._run_project_job_v2, args=(project_id, job_id), daemon=True)
        self._runtimes[job_id]["thread"] = thread
        thread.start()
        return {
            "project_id": project_id,
            "job_id": job_id,
        }

    def pause_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            runtime = self._runtimes.get(job_id)
            job = self._jobs.get(job_id)
            if runtime is None or job is None:
                raise ValueError("Unknown job.")
            if job["status"] not in {"running", "pause_requested"}:
                return {"job_id": job_id, "status": job["status"]}
            runtime["pause_requested"] = True
            job["status"] = "pause_requested"
            job["stage_label"] = "pausiert nach aktuellem Schritt"
            job["message"] = "Pause angefordert. Der Job stoppt am nächsten sicheren Schritt."
            job["updated_at_utc"] = _utc_timestamp()
            self._persist_state()
        return {"job_id": job_id, "status": "pause_requested"}

    def resume_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            runtime = self._runtimes.get(job_id)
            job = self._jobs.get(job_id)
            if runtime is None or job is None:
                raise ValueError("Unknown job.")
            runtime["pause_requested"] = False
            job["status"] = "running"
            job["stage_label"] = "läuft"
            job["message"] = "Job wurde fortgesetzt."
            job["updated_at_utc"] = _utc_timestamp()
            self._persist_state()
            condition = runtime["condition"]
        with condition:
            condition.notify_all()
        return {"job_id": job_id, "status": "running"}

    def confirm_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            runtime = self._runtimes.get(job_id)
            job = self._jobs.get(job_id)
            if runtime is None or job is None:
                raise ValueError("Unknown job.")
            runtime["awaiting_confirmation"] = False
            if job.get("status") == "review_required":
                job["status"] = "running"
                job["stage_label"] = "laeuft"
                job["message"] = "Review bestaetigt. Job laeuft weiter."
                job["updated_at_utc"] = _utc_timestamp()
                self._persist_state()
            condition = runtime["condition"]
        with condition:
            condition.notify_all()
        return {"job_id": job_id, "status": self._jobs[job_id]["status"]}

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            runtime = self._runtimes.get(job_id)
            job = self._jobs.get(job_id)
            if runtime is None or job is None:
                raise ValueError("Unknown job.")
            runtime["cancel_requested"] = True
            runtime["awaiting_confirmation"] = False
            runtime["pause_requested"] = False
            job["status"] = "canceled"
            job["stage"] = "canceled"
            job["stage_label"] = "Abgebrochen"
            job["message"] = "Job wurde vom User abgebrochen."
            job["updated_at_utc"] = _utc_timestamp()
            self._persist_state()
            condition = runtime["condition"]
        with condition:
            condition.notify_all()
        return {"job_id": job_id, "status": "canceled"}

    def shutdown(self) -> None:
        with self._lock:
            active_job_ids = [
                job_id
                for job_id, job in self._jobs.items()
                if job.get("status") in {"queued", "running", "pause_requested", "paused", "review_required"}
            ]
            runtimes = [self._runtimes.get(job_id) for job_id in active_job_ids]

        for job_id in active_job_ids:
            try:
                self.cancel_job(job_id)
            except Exception:
                pass

        executors = []
        threads = []
        for runtime in runtimes:
            if not isinstance(runtime, dict):
                continue
            executor = runtime.get("executor")
            thread = runtime.get("thread")
            if executor is not None:
                executors.append(executor)
            if isinstance(thread, threading.Thread):
                threads.append(thread)
            condition = runtime.get("condition")
            if isinstance(condition, threading.Condition):
                with condition:
                    condition.notify_all()

        for executor in executors:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)
            except Exception:
                pass

        terminate_registered_processes()

        for thread in threads:
            try:
                thread.join(timeout=2.0)
            except Exception:
                pass

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
        except Exception:
            return

        for project in payload.get("projects", []) if isinstance(payload.get("projects"), list) else []:
            if isinstance(project, dict) and isinstance(project.get("id"), str):
                project["output_mode"] = normalize_output_mode(project.get("output_mode"))
                if not isinstance(project.get("artifacts_path"), str) or not project.get("artifacts_path"):
                    project["artifacts_path"] = str(Path(project["root_path"]) / "artifacts")
                self._projects[project["id"]] = project

        for job in payload.get("jobs", []) if isinstance(payload.get("jobs"), list) else []:
            if not isinstance(job, dict) or not isinstance(job.get("id"), str):
                continue
            if job.get("status") in {"running", "pause_requested", "queued", "review_required"}:
                job["status"] = "failed"
                job["message"] = "Server restart interrupted the previous background job."
                job["stage_label"] = "unterbrochen"
            self._jobs[job["id"]] = job

    def resume_job(self, job_id: str) -> dict[str, Any]:
        restart_payload: dict[str, Any] | None = None
        with self._lock:
            runtime = self._runtimes.get(job_id)
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError("Unknown job.")
            thread = runtime.get("thread") if isinstance(runtime, dict) else None
            if (
                isinstance(runtime, dict)
                and isinstance(thread, threading.Thread)
                and thread.is_alive()
            ):
                runtime["pause_requested"] = False
                job["status"] = "running"
                job["stage_label"] = "laeuft"
                job["message"] = "Job wurde fortgesetzt."
                job["updated_at_utc"] = _utc_timestamp()
                self._persist_state()
                condition = runtime["condition"]
            else:
                if str(job.get("status") or "") != "paused":
                    raise ValueError("Only paused jobs can be resumed after restart.")
                project = self._projects.get(str(job.get("project_id") or ""))
                if not isinstance(project, dict):
                    raise ValueError("Project for paused job was not found.")
                restart_payload = {
                    "paths": [str(file_info.get("stored_path") or "") for file_info in project.get("files") or []],
                    "name": str(project.get("name") or "VAZ Projekt"),
                    "output_mode": normalize_output_mode(project.get("output_mode")),
                }
                job["status"] = "resumed"
                job["stage_label"] = "fortgesetzt"
                job["message"] = "Dieser pausierte Lauf wurde als neuer Job wieder aufgenommen."
                job["updated_at_utc"] = _utc_timestamp()
                self._persist_state()
                condition = None
        if restart_payload is not None:
            result = self.create_project_from_paths(
                restart_payload["paths"],
                name=restart_payload["name"],
                output_mode=restart_payload["output_mode"],
                reset_existing=False,
            )
            return {
                "job_id": result["job_id"],
                "project_id": result["project_id"],
                "status": "running",
                "resumed_from_job_id": job_id,
            }
        with condition:
            condition.notify_all()
        return {"job_id": job_id, "project_id": job.get("project_id"), "status": "running"}

    def shutdown(self, *, preserve_paused: bool = False) -> None:
        with self._lock:
            active_job_ids = [
                job_id
                for job_id, job in self._jobs.items()
                if job.get("status") in {"queued", "running", "pause_requested", "review_required"}
                or (job.get("status") == "paused" and not preserve_paused)
            ]
            runtimes = [self._runtimes.get(job_id) for job_id in active_job_ids]

        for job_id in active_job_ids:
            try:
                self.cancel_job(job_id)
            except Exception:
                pass

        executors = []
        threads = []
        for runtime in runtimes:
            if not isinstance(runtime, dict):
                continue
            executor = runtime.get("executor")
            thread = runtime.get("thread")
            if executor is not None:
                executors.append(executor)
            if isinstance(thread, threading.Thread):
                threads.append(thread)
            condition = runtime.get("condition")
            if isinstance(condition, threading.Condition):
                with condition:
                    condition.notify_all()

        for executor in executors:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)
            except Exception:
                pass

        terminate_registered_processes()

        for thread in threads:
            try:
                thread.join(timeout=2.0)
            except Exception:
                pass

    def _update_project(self, project_id: str, **changes: Any) -> None:
        with self._lock:
            project = self._projects[project_id]
            project.update(changes)
            project["updated_at_utc"] = _utc_timestamp()
            self._persist_state()

    def _update_project_file(
        self,
        project_id: str,
        stored_path: str,
        *,
        ui_status: str | None = None,
        ui_note: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            project = self._projects[project_id]
            files = project["files"]
            for file_info in files:
                if file_info.get("stored_path") != stored_path:
                    continue
                if ui_status is not None:
                    file_info["ui_status"] = ui_status
                if ui_note is not None:
                    file_info["ui_note"] = ui_note
                if extra_fields:
                    file_info.update(extra_fields)
                break
            project["updated_at_utc"] = _utc_timestamp()
            self._persist_state()

    def _update_job(self, job_id: str, **changes: Any) -> None:
        project_id: str | None = None
        with self._lock:
            job = self._jobs[job_id]
            job.update(changes)
            job["updated_at_utc"] = _utc_timestamp()
            project_id = str(job.get("project_id") or "") or None
            self._persist_state()
        if project_id:
            self._write_project_manifest(project_id)

    def _write_project_manifest(self, project_id: str) -> None:
        with self._lock:
            project = self._projects[project_id]
            root = Path(project["root_path"])
            manifest_path = root / "project.json"
            manifest_path.write_text(json.dumps(project, indent=2), encoding="utf-8")
            job_ids = list(project.get("job_ids") or [])
            latest_job = self._jobs.get(job_ids[-1]) if job_ids else None
            data_root = _project_data_root(project)
            if project.get("inputs_path") is None:
                data_root.mkdir(parents=True, exist_ok=True)
                state_payload = {
                    "schema_version": "vazer.state.v1",
                    "project_id": project.get("id"),
                    "project_name": project.get("name"),
                    "updated_at_utc": _utc_timestamp(),
                    "artifacts_path": project.get("artifacts_path"),
                    "default_output_dir": project.get("default_output_dir"),
                    "output_mode": project.get("output_mode"),
                    "files": [str(file_info.get("stored_path") or "") for file_info in project.get("files") or []],
                    "latest_job": latest_job or None,
                    "artifacts": dict(project.get("artifacts") or {}),
                }
                (data_root / "vazer.state.json").write_text(
                    json.dumps(state_payload, indent=2),
                    encoding="utf-8",
                )

    def _wait_if_paused(self, job_id: str) -> None:
        runtime = self._runtimes[job_id]
        condition: threading.Condition = runtime["condition"]
        with condition:
            if runtime["pause_requested"]:
                self._update_job(
                    job_id,
                    status="paused",
                    stage_label="pausiert",
                    message="Job ist pausiert und wartet auf Resume.",
                )
            while runtime["pause_requested"]:
                condition.wait(timeout=0.5)
            job = self._jobs[job_id]
            if job["status"] == "paused":
                self._update_job(
                    job_id,
                    status="running",
                    stage_label="läuft",
                    message="Job läuft weiter.",
                )

    def _raise_if_canceled(self, job_id: str) -> None:
        runtime = self._runtimes[job_id]
        if runtime.get("cancel_requested"):
            raise JobCanceledError("Job was canceled by the user.")

    def _wait_for_role_review(self, job_id: str) -> None:
        runtime = self._runtimes[job_id]
        condition: threading.Condition = runtime["condition"]
        with condition:
            runtime["awaiting_confirmation"] = True
            self._update_job(
                job_id,
                status="review_required",
                stage="role_review",
                stage_label="Rollen pruefen",
                message="Pruefe die AI-Rollen und klicke Weiter oder Abbrechen.",
            )
            while runtime["awaiting_confirmation"] and not runtime["cancel_requested"]:
                condition.wait(timeout=0.5)
        self._raise_if_canceled(job_id)

    def _classify_files(self, files: list[dict[str, Any]]) -> dict[str, Any]:
        audio_only: list[dict[str, Any]] = []
        video_files: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []

        for file_info in files:
            probe = file_info.get("probe") or {}
            audio_count = int(probe.get("audio_stream_count") or 0)
            video_count = int(probe.get("video_stream_count") or 0)
            if video_count > 0:
                video_files.append(file_info)
            elif audio_count > 0:
                audio_only.append(file_info)
            else:
                unsupported.append(file_info)

        warnings: list[str] = []
        master_file: dict[str, Any] | None = None
        if len(audio_only) == 1:
            master_file = audio_only[0]
        elif len(audio_only) > 1:
            master_file = max(audio_only, key=lambda item: float((item.get("probe") or {}).get("duration_seconds") or 0.0))
            warnings.append("Mehrere audio-only Dateien erkannt. Die längste wurde als Master gewählt.")
        else:
            warnings.append("Keine eindeutige audio-only Master-Datei erkannt.")

        return {
            "master_asset": None if master_file is None else master_file["original_path"],
            "master_path": None if master_file is None else master_file["stored_path"],
            "camera_assets": [item["original_path"] for item in video_files],
            "camera_paths": [item["stored_path"] for item in video_files],
            "camera_count": len(video_files),
            "unsupported_count": len(unsupported),
            "warnings": warnings,
        }

    def _run_project_job(self, project_id: str, job_id: str) -> None:
        try:
            with self._lock:
                project = self._projects[project_id]
                files = list(project["files"])
            total_files = max(1, len(files))

            self._update_job(
                job_id,
                status="running",
                stage="probing",
                stage_label="Dateien prüfen",
                message="ffprobe läuft über die gedroppten Dateien.",
                progress_percent=2.0,
            )

            for index, file_info in enumerate(files, start=1):
                self._wait_if_paused(job_id)
                self._update_job(
                    job_id,
                    stage="probing",
                    stage_label="Dateien prüfen",
                    message=f"Prüfe {file_info['original_path']} ({index}/{total_files})",
                    progress_percent=2.0 + 28.0 * ((index - 1) / total_files),
                )
                media_info = probe_media(file_info["stored_path"])
                file_info["probe"] = _media_info_to_dict(media_info)
                self._update_project(project_id, files=files)
                self._write_project_manifest(project_id)

            classification = self._classify_files(files)
            self._update_project(project_id, files=files, classification=classification)
            self._write_project_manifest(project_id)
            artifact_paths = _artifact_layout(self._projects[project_id], classification)
            self._update_job(
                job_id,
                stage="classified",
                stage_label="Dateien sortiert",
                message="Master und Kameras wurden erkannt.",
                progress_percent=35.0,
                details={
                    "file_count": len(files),
                    "camera_count": classification["camera_count"],
                    "master_asset": classification["master_asset"],
                },
            )

            master_path = classification.get("master_path")
            camera_paths = classification.get("camera_paths") or []
            if not master_path or not camera_paths:
                self._update_job(
                    job_id,
                    status="needs_attention",
                    stage="classified",
                    stage_label="Eingriff nötig",
                    message="Master oder Kameras konnten nicht eindeutig erkannt werden.",
                    progress_percent=100.0,
                )
                return

            sync_options = SyncOptions()
            asset_ids = _derive_asset_ids(camera_paths)
            entries: list[dict[str, Any]] = []
            master_summary: dict[str, Any] | None = None
            artifacts_root = Path(self._projects[project_id]["artifacts_path"])
            partial_path = artifacts_root / "sync_map.partial.json"

            for index, (asset_id, camera_path) in enumerate(zip(asset_ids, camera_paths, strict=True), start=1):
                self._wait_if_paused(job_id)
                self._update_job(
                    job_id,
                    stage="syncing",
                    stage_label="Audio-Sync",
                    message=f"Synce {Path(camera_path).name} ({index}/{len(camera_paths)})",
                    progress_percent=35.0 + 55.0 * ((index - 1) / len(camera_paths)),
                )
                try:
                    report = analyze_sync(master_path, camera_path, options=sync_options)
                except Exception as error:
                    entries.append(
                        {
                            "asset_id": asset_id,
                            "path": camera_path,
                            "status": "failed",
                            "error": str(error),
                        }
                    )
                else:
                    if master_summary is None:
                        master_summary = report["master"]
                    entries.append(_build_sync_entry(asset_id, camera_path, report))

                partial_sync_map = {
                    "schema_version": "vazer.sync_map.v1",
                    "generated_at_utc": _utc_timestamp(),
                    "tool": {
                        "name": "vazer",
                        "version": __version__,
                    },
                    "master": master_summary
                    or {
                        "path": master_path,
                        "duration_seconds": None,
                        "format_name": None,
                    },
                    "options": {
                        "coarse_rate": sync_options.coarse_rate,
                        "fine_rate": sync_options.fine_rate,
                        "envelope_bin_seconds": sync_options.envelope_bin_seconds,
                        "activity_rate": sync_options.activity_rate,
                        "activity_window_seconds": sync_options.activity_window_seconds,
                        "anchor_count": sync_options.anchor_count,
                        "anchor_window_seconds": sync_options.anchor_window_seconds,
                        "anchor_search_seconds": sync_options.anchor_search_seconds,
                        "coarse_candidate_limit": sync_options.coarse_candidate_limit,
                        "anchor_activity_step_seconds": sync_options.anchor_activity_step_seconds,
                        "anchor_min_spacing_seconds": sync_options.anchor_min_spacing_seconds,
                    },
                    "entries": entries,
                    "summary": {
                        "total": len(entries),
                        "synced": sum(1 for entry in entries if entry["status"] == "synced"),
                        "failed": sum(1 for entry in entries if entry["status"] == "failed"),
                    },
                }
                write_sync_map(partial_sync_map, str(partial_path))
                self._update_project(
                    project_id,
                    artifacts={
                        **self._projects[project_id]["artifacts"],
                        "partial_sync_map_path": str(partial_path),
                    },
                )
                self._write_project_manifest(project_id)

            final_sync_map = json.loads(partial_path.read_text(encoding="utf-8-sig"))
            sync_map_path = artifacts_root / "sync_map.json"
            write_sync_map(final_sync_map, str(sync_map_path))
            self._update_project(
                project_id,
                artifacts={
                    **self._projects[project_id]["artifacts"],
                    "sync_map_path": str(sync_map_path),
                },
            )
            self._write_project_manifest(project_id)

            self._update_job(
                job_id,
                status="completed" if final_sync_map["summary"]["synced"] > 0 else "failed",
                stage="completed",
                stage_label="Fertig",
                message=(
                    f"sync_map geschrieben. "
                    f"{final_sync_map['summary']['synced']} synced, {final_sync_map['summary']['failed']} failed."
                ),
                progress_percent=100.0,
                artifacts={
                    "sync_map_path": str(sync_map_path),
                },
                details={
                    "file_count": len(files),
                    "camera_count": classification["camera_count"],
                    "master_asset": classification["master_asset"],
                },
            )
        except JobCanceledError:
            return
        except JobCanceledError:
            return
        except Exception as error:
            self._update_job(
                job_id,
                status="failed",
                stage="failed",
                stage_label="Fehler",
                message=str(error),
            )

    def _classify_files_v2(self, files: list[dict[str, Any]]) -> dict[str, Any]:
        audio_only: list[dict[str, Any]] = []
        video_files: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []

        for file_info in files:
            probe = file_info.get("probe") or {}
            audio_count = int(probe.get("audio_stream_count") or 0)
            video_count = int(probe.get("video_stream_count") or 0)
            if video_count > 0:
                video_files.append(file_info)
            elif audio_count > 0:
                audio_only.append(file_info)
            else:
                unsupported.append(file_info)

        warnings: list[str] = []
        master_file: dict[str, Any] | None = None
        if len(audio_only) == 1:
            master_file = audio_only[0]
        elif len(audio_only) > 1:
            master_file = max(audio_only, key=lambda item: float((item.get("probe") or {}).get("duration_seconds") or 0.0))
            warnings.append("Multiple audio-only files detected. The longest one was chosen as master.")
        else:
            warnings.append("No unambiguous audio-only master file was detected.")

        return {
            "master_asset": None if master_file is None else master_file["original_path"],
            "master_path": None if master_file is None else master_file["stored_path"],
            "master_stored_path": None if master_file is None else master_file["stored_path"],
            "camera_assets": [item["original_path"] for item in video_files],
            "camera_paths": [item["stored_path"] for item in video_files],
            "camera_count": len(video_files),
            "unsupported_count": len(unsupported),
            "warnings": warnings,
        }

    def _run_project_job_v2(self, project_id: str, job_id: str) -> None:
        media_executor = None
        try:
            with self._lock:
                project = self._projects[project_id]
                files = list(project["files"])
                output_mode = normalize_output_mode(project.get("output_mode"))
            total_files = max(1, len(files))
            artifacts_root = Path(self._projects[project_id]["artifacts_path"])

            self._update_job(
                job_id,
                status="running",
                stage="probing",
                stage_label="Probing files",
                message="Inspecting dropped files with ffprobe.",
                progress_percent=2.0,
            )

            for index, file_info in enumerate(files, start=1):
                self._wait_if_paused(job_id)
                self._raise_if_canceled(job_id)
                self._update_project_file(
                    project_id,
                    file_info["stored_path"],
                    ui_status="probing",
                    ui_note=f"Inspecting file {index}/{total_files}",
                )
                self._update_job(
                    job_id,
                    stage="probing",
                    stage_label="Probing files",
                    message=f"Inspecting {file_info['display_name']} ({index}/{total_files})",
                    progress_percent=2.0 + 28.0 * ((index - 1) / total_files),
                )
                media_info = probe_media(file_info["stored_path"])
                file_info["probe"] = _media_info_to_dict(media_info)
                file_info["ui_status"] = "ready"
                file_info["ui_note"] = "Media info loaded."
                self._update_project(project_id, files=files)
                self._write_project_manifest(project_id)

            classification = self._classify_files_v2(files)
            master_stored_path = classification.get("master_stored_path")
            camera_path_list = list(classification.get("camera_paths") or [])
            camera_paths = set(camera_path_list)
            asset_ids = _derive_asset_ids(camera_path_list)
            asset_id_by_path = dict(zip(camera_path_list, asset_ids, strict=True))
            artifact_paths = _artifact_layout(self._projects[project_id], classification)
            classification["camera_asset_ids"] = asset_ids
            classification["camera_roles"] = {}
            classification["camera_role_source"] = None
            for file_info in files:
                stored_path = file_info["stored_path"]
                if stored_path == master_stored_path:
                    file_info["ui_status"] = "master"
                    file_info["ui_note"] = "Using this file as master audio."
                elif stored_path in camera_paths:
                    asset_id = asset_id_by_path[stored_path]
                    file_info["asset_id"] = asset_id
                    file_info["ui_status"] = "camera"
                    file_info["ui_note"] = "Camera candidate ready for role check."
                else:
                    file_info["ui_status"] = "ignored"
                    file_info["ui_note"] = "No usable audio/video role detected."

            self._update_project(project_id, files=files, classification=classification)
            self._write_project_manifest(project_id)
            self._update_job(
                job_id,
                stage="classified",
                stage_label="Files classified",
                message="Master and camera candidates were identified.",
                progress_percent=35.0,
                details={
                    "file_count": len(files),
                    "camera_count": classification["camera_count"],
                    "master_asset": classification["master_asset"],
                    "output_mode": output_mode,
                },
            )

            if camera_path_list:
                self._wait_if_paused(job_id)
                self._raise_if_canceled(job_id)
                camera_role_path = artifact_paths["camera_roles_path"]
                reusable_camera_roles = _load_reusable_camera_roles_artifact(camera_role_path, camera_path_list)
                camera_role_artifact = reusable_camera_roles
                if camera_role_artifact is None:
                    self._update_job(
                        job_id,
                        stage="roles",
                        stage_label="Camera roles",
                        message="Classifying middle frames into totale / halbtotale / close.",
                        progress_percent=40.0,
                    )
                    for index, camera_path in enumerate(camera_path_list, start=1):
                        asset_id = asset_id_by_path[camera_path]
                        self._update_project_file(
                            project_id,
                            camera_path,
                            ui_status="role_check",
                            ui_note=f"Preparing AI role check {index}/{len(camera_path_list)}",
                            extra_fields={"asset_id": asset_id},
                        )

                    role_frame_dir = artifact_paths["camera_roles_dir"]
                    try:
                        camera_role_artifact = build_camera_role_artifact(
                            [
                                {
                                    "asset_id": asset_id_by_path[camera_path],
                                    "path": camera_path,
                                    "display_name": Path(camera_path).name,
                                }
                                for camera_path in camera_path_list
                            ],
                            output_dir=str(role_frame_dir),
                        )
                    except Exception as error:
                        role_assignments = [
                            {
                                "asset_id": asset_id_by_path[camera_path],
                                "path": camera_path,
                                "display_name": Path(camera_path).name,
                                "duration_seconds": (probe_media(camera_path).duration_seconds or 0.0),
                                "middle_seconds": (probe_media(camera_path).duration_seconds or 0.0) / 2.0,
                                "frame_path": None,
                                "image_width": None,
                                "image_height": None,
                                "role": infer_camera_role_from_name(asset_id_by_path[camera_path], camera_path),
                                "confidence": "low",
                                "reason": "Fallback from filename hints after AI role classification failed.",
                            }
                            for camera_path in camera_path_list
                        ]
                        camera_role_artifact = {
                            "schema_version": "vazer.camera_roles.v1",
                            "generated_at_utc": _utc_timestamp(),
                            "tool": {
                                "name": "vazer",
                                "version": __version__,
                            },
                            "provider": {
                                "name": "fallback",
                                "model": None,
                            },
                            "summary": {
                                "asset_count": len(role_assignments),
                                "role_counts": {
                                    role: sum(1 for assignment in role_assignments if assignment["role"] == role)
                                    for role in ("close", "halbtotale", "totale")
                                },
                                "summary_text": "Filename fallback was used because AI role classification failed.",
                            },
                            "assignments": role_assignments,
                            "warning": str(error),
                        }
                        classification.setdefault("warnings", []).append(
                            f"AI camera role classification failed. Fell back to filename hints: {error}"
                        )

                    write_camera_role_artifact(camera_role_artifact, str(camera_role_path))
                classification["camera_roles"] = {
                    assignment["asset_id"]: assignment["role"]
                    for assignment in camera_role_artifact["assignments"]
                }
                classification["camera_role_source"] = (
                    "ai_middle_frame"
                    if camera_role_artifact.get("provider", {}).get("name") == "openai"
                    else "filename_fallback"
                )
                self._update_project(
                    project_id,
                    files=files,
                    classification=classification,
                    artifacts={
                        **self._projects[project_id]["artifacts"],
                        "camera_roles_path": str(camera_role_path),
                    },
                )
                self._write_project_manifest(project_id)
                for camera_path in camera_path_list:
                    asset_id = asset_id_by_path[camera_path]
                    role = classification["camera_roles"].get(asset_id)
                    self._update_project_file(
                        project_id,
                        camera_path,
                        ui_status="camera",
                        ui_note=_format_camera_note(role, "Camera candidate ready for sync."),
                        extra_fields={"asset_id": asset_id, "camera_role": role},
                    )
                self._update_job(
                    job_id,
                    stage="roles",
                    stage_label="Camera roles",
                    message=(
                        "Using existing camera roles."
                        if reusable_camera_roles is not None
                        else "Camera roles assigned."
                    ),
                    progress_percent=45.0,
                )
                if reusable_camera_roles is None:
                    self._wait_for_role_review(job_id)
                    self._update_job(
                        job_id,
                        status="running",
                        stage="roles",
                        stage_label="Camera roles",
                        message="Role review confirmed. Continuing with audio sync.",
                        progress_percent=46.0,
                    )
                else:
                    self._update_job(
                        job_id,
                        status="running",
                        stage="roles",
                        stage_label="Camera roles",
                        message="Existing camera roles confirmed. Continuing with audio sync.",
                        progress_percent=46.0,
                    )

            master_path = classification.get("master_path")
            if not master_path or not camera_path_list:
                self._update_job(
                    job_id,
                    status="needs_attention",
                    stage="classified",
                    stage_label="Needs attention",
                    message="Master or camera files could not be identified cleanly.",
                    progress_percent=100.0,
                )
                return

            sync_options = SyncOptions()
            analysis_options = AnalysisOptions()
            transcription_options = TranscriptionOptions(model="whisper-1")
            entries: list[dict[str, Any]] = []
            master_summary: dict[str, Any] | None = None
            partial_path = artifact_paths["sync_partial_path"]
            analysis_futures: dict[str, Future[dict[str, Any]]] = {}
            transcript_future: Future[dict[str, Any]] | None = None
            reusable_sync_map = _load_reusable_sync_map(
                artifact_paths["sync_map_path"],
                str(master_path),
                camera_path_list,
                require_complete=True,
            )
            reusable_partial_sync_map = None if reusable_sync_map is not None else _load_reusable_sync_map(
                artifact_paths["sync_partial_path"],
                str(master_path),
                camera_path_list,
                require_complete=False,
            )
            reusable_transcript = _load_reusable_transcript_artifact(artifact_paths["transcript_path"], str(master_path))
            reusable_analysis_map = _load_reusable_analysis_map(artifact_paths["analysis_map_path"], str(master_path), camera_path_list)

            media_progress_lock = threading.Lock()
            analysis_progress_by_asset = {asset_id_by_path[path]: 0.0 for path in camera_path_list}
            media_progress: dict[str, dict[str, Any]] = {
                "sync": {
                    "label": "Sync",
                    "progress_percent": 0.0,
                    "status": "running",
                    "detail": "Preparing camera sync.",
                },
                "transcript": {
                    "label": "Text",
                    "progress_percent": 0.0,
                    "status": "running",
                    "detail": "Preparing Whisper transcript.",
                },
                "analysis": {
                    "label": "Bild",
                    "progress_percent": 0.0,
                    "status": "pending",
                    "detail": "Waiting for synced cameras.",
                },
            }

            def _publish_media_progress() -> None:
                with media_progress_lock:
                    snapshot = json.loads(json.dumps(media_progress))
                    sync_progress = float(snapshot["sync"]["progress_percent"])
                    transcript_progress = float(snapshot["transcript"]["progress_percent"])
                    analysis_progress = float(snapshot["analysis"]["progress_percent"])
                overall_progress = 46.0 + 28.0 * (
                    0.4 * (sync_progress / 100.0)
                    + 0.3 * (transcript_progress / 100.0)
                    + 0.3 * (analysis_progress / 100.0)
                )
                parts = []
                for task_id in ("sync", "transcript", "analysis"):
                    task = snapshot[task_id]
                    if task.get("status") == "pending":
                        continue
                    parts.append(f"{task['label']} {task['progress_percent']:.0f}%")
                self._update_job(
                    job_id,
                    stage="media_parallel",
                    stage_label="Sync + Text + Bild",
                    message=" | ".join(parts) or "Preparing media tasks.",
                    progress_percent=overall_progress,
                    parallel_progress=snapshot,
                    analysis_pass="global",
                    analysis_progress=snapshot.get("analysis"),
                )

            def _set_media_progress(task_id: str, progress_percent: float, detail: str, status: str) -> None:
                with media_progress_lock:
                    media_progress[task_id]["progress_percent"] = max(0.0, min(100.0, float(progress_percent)))
                    media_progress[task_id]["detail"] = detail
                    media_progress[task_id]["status"] = status
                _publish_media_progress()

            def _transcript_progress(completed_chunks: int, total_chunks: int, detail: str) -> None:
                progress_percent = 100.0 * completed_chunks / max(1, total_chunks)
                self._update_project_file(
                    project_id,
                    master_path,
                    ui_status="transcribing",
                    ui_note=detail,
                    extra_fields={
                        "ui_progress_percent": progress_percent,
                        "ui_progress_label": "Whisper",
                        "ui_progress_color": "#70abff",
                    },
                )
                _set_media_progress("transcript", progress_percent, detail, "running")

            def _analysis_progress(asset_id: str, camera_path: str, role: str | None, progress_percent: float, detail: str) -> None:
                analysis_progress_by_asset[asset_id] = progress_percent
                aggregate = sum(analysis_progress_by_asset.values()) / max(1, len(analysis_progress_by_asset))
                self._update_project_file(
                    project_id,
                    camera_path,
                    ui_status="analyzing",
                    ui_note=_format_camera_note(role, detail),
                    extra_fields={
                        "ui_progress_percent": progress_percent,
                        "ui_progress_label": "CV",
                        "ui_progress_color": "#63c178",
                        "ui_sub_progress": [
                            {
                                "label": "Global",
                                "percent": progress_percent,
                                "color": "#63c178",
                            }
                        ],
                    },
                )
                _set_media_progress("analysis", aggregate, detail, "running")

            def _run_camera_analysis(sync_entry: dict[str, Any], camera_path: str, role: str | None) -> dict[str, Any]:
                asset_id = sync_entry["asset_id"]
                try:
                    result = analyze_camera_video_signals(
                        sync_entry,
                        analysis_options,
                        on_progress=lambda progress_percent, detail: _analysis_progress(
                            asset_id,
                            camera_path,
                            role,
                            progress_percent,
                            detail,
                        ),
                    )
                except Exception as error:
                    analysis_progress_by_asset[asset_id] = 100.0
                    aggregate = sum(analysis_progress_by_asset.values()) / max(1, len(analysis_progress_by_asset))
                    self._update_project_file(
                        project_id,
                        camera_path,
                        ui_status="failed",
                        ui_note=_format_camera_note(role, f"Analysis failed: {error}"),
                        extra_fields={
                            "ui_progress_percent": 100.0,
                            "ui_progress_label": "CV",
                            "ui_progress_color": "#e26060",
                            "ui_sub_progress": [
                                {
                                    "label": "Global",
                                    "percent": 100.0,
                                    "color": "#e26060",
                                }
                            ],
                        },
                    )
                    _set_media_progress("analysis", aggregate, "Analyzing synced cameras.", "running")
                    return {
                        "asset_id": asset_id,
                        "path": camera_path,
                        "status": "failed",
                        "error": str(error),
                    }

                analysis_progress_by_asset[asset_id] = 100.0
                aggregate = sum(analysis_progress_by_asset.values()) / max(1, len(analysis_progress_by_asset))
                self._update_project_file(
                    project_id,
                    camera_path,
                    ui_status="analyzed",
                    ui_note=_format_camera_note(role, "CV analysis ready."),
                    extra_fields={
                        "ui_progress_percent": 100.0,
                        "ui_progress_label": "CV",
                        "ui_progress_color": "#63c178",
                        "ui_sub_progress": [
                            {
                                "label": "Global",
                                "percent": 100.0,
                                "color": "#63c178",
                            }
                        ],
                    },
                )
                _set_media_progress("analysis", aggregate, "Analyzing synced cameras.", "running")
                return result

            media_executor = ThreadPoolExecutor(max_workers=max(2, min(len(camera_path_list) + 1, 4)))
            with self._lock:
                runtime = self._runtimes.get(job_id)
                if isinstance(runtime, dict):
                    runtime["executor"] = media_executor
            if reusable_transcript is None:
                self._update_project_file(
                    project_id,
                    master_path,
                    ui_status="transcribing",
                    ui_note="Preparing Whisper transcript.",
                    extra_fields={
                        "ui_progress_percent": 0.0,
                        "ui_progress_label": "Whisper",
                        "ui_progress_color": "#70abff",
                    },
                )
                transcript_future = media_executor.submit(
                    build_master_transcript,
                    master_path,
                    source_sync_map_path=None,
                    options=transcription_options,
                    on_progress=_transcript_progress,
                )
            else:
                self._update_project_file(
                    project_id,
                    master_path,
                    ui_status="master",
                    ui_note=f"Using existing transcript: {artifact_paths['transcript_path'].name}",
                    extra_fields={
                        "ui_progress_percent": 100.0,
                        "ui_progress_label": "Whisper",
                        "ui_progress_color": "#63c178",
                    },
                )
                self._update_project(
                    project_id,
                    artifacts={
                        **self._projects[project_id]["artifacts"],
                        "transcript_path": str(artifact_paths["transcript_path"]),
                    },
                )
                self._write_project_manifest(project_id)
                _set_media_progress("transcript", 100.0, "Using existing transcript.", "done")
            _publish_media_progress()
            cached_sync_entries_by_path: dict[str, dict[str, Any]] = {}
            if reusable_sync_map is not None:
                entries = list(reusable_sync_map.get("entries") or [])
                master_summary = reusable_sync_map.get("master") if isinstance(reusable_sync_map.get("master"), dict) else None
                cached_sync_entries_by_path = {
                    str(entry.get("path") or ""): entry
                    for entry in entries
                    if isinstance(entry, dict) and isinstance(entry.get("path"), str)
                }
                for camera_path in camera_path_list:
                    asset_id = asset_id_by_path[camera_path]
                    role = classification["camera_roles"].get(asset_id)
                    sync_entry = cached_sync_entries_by_path.get(camera_path)
                    if sync_entry is None:
                        continue
                    entry_status = "synced" if str(sync_entry.get("status") or "") == "synced" else "failed"
                    if entry_status != "synced":
                        analysis_progress_by_asset[asset_id] = 100.0
                    self._update_project_file(
                        project_id,
                        camera_path,
                        ui_status=entry_status,
                        ui_note=_format_camera_note(
                            role,
                            "Using existing sync data."
                            if entry_status == "synced"
                            else str(sync_entry.get("error") or "Existing sync data marked this camera as failed."),
                        ),
                        extra_fields={
                            "camera_role": role,
                            "ui_progress_percent": 100.0,
                            "ui_progress_label": "Sync",
                            "ui_progress_color": "#63c178" if entry_status == "synced" else "#e26060",
                        },
                    )
                self._update_project(
                    project_id,
                    artifacts={
                        **self._projects[project_id]["artifacts"],
                        "sync_map_path": str(artifact_paths["sync_map_path"]),
                    },
                )
                self._write_project_manifest(project_id)
                _set_media_progress("sync", 100.0, "Using existing sync map.", "done")
            elif reusable_partial_sync_map is not None:
                entries = list(reusable_partial_sync_map.get("entries") or [])
                master_summary = reusable_partial_sync_map.get("master") if isinstance(reusable_partial_sync_map.get("master"), dict) else None
                cached_sync_entries_by_path = {
                    str(entry.get("path") or ""): entry
                    for entry in entries
                    if isinstance(entry, dict) and isinstance(entry.get("path"), str)
                }
                for camera_path in camera_path_list:
                    sync_entry = cached_sync_entries_by_path.get(camera_path)
                    if sync_entry is None:
                        continue
                    asset_id = asset_id_by_path[camera_path]
                    role = classification["camera_roles"].get(asset_id)
                    self._update_project_file(
                        project_id,
                        camera_path,
                        ui_status="synced",
                        ui_note=_format_camera_note(role, "Using existing sync data from previous run."),
                        extra_fields={
                            "camera_role": role,
                            "ui_progress_percent": 100.0,
                            "ui_progress_label": "Sync",
                            "ui_progress_color": "#63c178",
                        },
                    )
                self._update_project(
                    project_id,
                    artifacts={
                        **self._projects[project_id]["artifacts"],
                        "partial_sync_map_path": str(artifact_paths["sync_partial_path"]),
                    },
                )
                self._write_project_manifest(project_id)
                _set_media_progress(
                    "sync",
                    100.0 * len(cached_sync_entries_by_path) / max(1, len(camera_path_list)),
                    f"Resuming from existing sync data ({len(cached_sync_entries_by_path)}/{len(camera_path_list)}).",
                    "running",
                )

            for index, camera_path in enumerate(camera_path_list, start=1):
                self._wait_if_paused(job_id)
                self._raise_if_canceled(job_id)
                asset_id = asset_id_by_path[camera_path]
                role = classification["camera_roles"].get(asset_id)
                existing_sync_entry = cached_sync_entries_by_path.get(camera_path)
                if isinstance(existing_sync_entry, dict) and (
                    reusable_sync_map is not None or str(existing_sync_entry.get("status") or "") == "synced"
                ):
                    if reusable_analysis_map is None and str(existing_sync_entry.get("status") or "") == "synced":
                        analysis_futures[asset_id] = media_executor.submit(
                            _run_camera_analysis,
                            existing_sync_entry,
                            camera_path,
                            role,
                        )
                    _set_media_progress(
                        "sync",
                        100.0 * len(cached_sync_entries_by_path) / max(1, len(camera_path_list)),
                        f"Resuming sync ({len(cached_sync_entries_by_path)}/{len(camera_path_list)} cameras already cached).",
                        "done" if reusable_sync_map is not None else "running",
                    )
                    continue
                self._update_project_file(
                    project_id,
                    camera_path,
                    ui_status="syncing",
                    ui_note=_format_camera_note(role, f"Syncing camera {index}/{len(camera_path_list)}"),
                    extra_fields={
                        "asset_id": asset_id,
                        "ui_progress_percent": 10.0,
                        "ui_progress_label": "Sync",
                        "ui_progress_color": "#ef9d4d",
                    },
                )
                _set_media_progress(
                    "sync",
                    100.0 * (index - 1) / max(1, len(camera_path_list)),
                    f"Syncing {Path(camera_path).name} ({index}/{len(camera_path_list)})",
                    "running",
                )
                try:
                    report = analyze_sync(master_path, camera_path, options=sync_options)
                except Exception as error:
                    entries.append(
                        {
                            "asset_id": asset_id,
                            "path": camera_path,
                            "status": "failed",
                            "error": str(error),
                        }
                    )
                    analysis_progress_by_asset[asset_id] = 100.0
                    self._update_project_file(
                        project_id,
                        camera_path,
                        ui_status="failed",
                        ui_note=_format_camera_note(role, f"Sync failed: {error}"),
                        extra_fields={
                            "ui_progress_percent": 100.0,
                            "ui_progress_label": "Sync",
                            "ui_progress_color": "#e26060",
                        },
                    )
                else:
                    if master_summary is None:
                        master_summary = report["master"]
                    sync_entry = _build_sync_entry(asset_id, camera_path, report)
                    entries.append(sync_entry)
                    entry_status = "synced" if sync_entry["status"] == "synced" else "failed"
                    self._update_project_file(
                        project_id,
                        camera_path,
                        ui_status=entry_status,
                        ui_note=_format_camera_note(
                            role,
                            (
                                "Audio sync locked."
                                if entry_status == "synced"
                                else str(sync_entry.get("error") or "Sync failed.")
                            ),
                        ),
                        extra_fields={
                            "camera_role": role,
                            "ui_progress_percent": 100.0,
                            "ui_progress_label": "Sync",
                            "ui_progress_color": "#63c178" if entry_status == "synced" else "#e26060",
                        },
                    )
                    if entry_status == "synced":
                        if reusable_analysis_map is None:
                            analysis_futures[asset_id] = media_executor.submit(
                                _run_camera_analysis,
                                sync_entry,
                                camera_path,
                                role,
                            )
                    else:
                        analysis_progress_by_asset[asset_id] = 100.0

                partial_sync_map = {
                    "schema_version": "vazer.sync_map.v1",
                    "generated_at_utc": _utc_timestamp(),
                    "tool": {
                        "name": "vazer",
                        "version": __version__,
                    },
                    "master": master_summary
                    or {
                        "path": master_path,
                        "duration_seconds": None,
                        "format_name": None,
                    },
                    "options": {
                        "coarse_rate": sync_options.coarse_rate,
                        "fine_rate": sync_options.fine_rate,
                        "envelope_bin_seconds": sync_options.envelope_bin_seconds,
                        "activity_rate": sync_options.activity_rate,
                        "activity_window_seconds": sync_options.activity_window_seconds,
                        "anchor_count": sync_options.anchor_count,
                        "anchor_window_seconds": sync_options.anchor_window_seconds,
                        "anchor_search_seconds": sync_options.anchor_search_seconds,
                        "coarse_candidate_limit": sync_options.coarse_candidate_limit,
                        "anchor_activity_step_seconds": sync_options.anchor_activity_step_seconds,
                        "anchor_min_spacing_seconds": sync_options.anchor_min_spacing_seconds,
                    },
                    "entries": entries,
                    "summary": {
                        "total": len(entries),
                        "synced": sum(1 for entry in entries if entry["status"] == "synced"),
                        "failed": sum(1 for entry in entries if entry["status"] == "failed"),
                    },
                }
                write_sync_map(partial_sync_map, str(partial_path))
                self._update_project(
                    project_id,
                    artifacts={
                        **self._projects[project_id]["artifacts"],
                        "partial_sync_map_path": str(partial_path),
                    },
                )
                self._write_project_manifest(project_id)
                _set_media_progress(
                    "sync",
                    100.0 * index / max(1, len(camera_path_list)),
                    f"Synced {index}/{len(camera_path_list)} camera files.",
                    "done" if index == len(camera_path_list) else "running",
                )

            if reusable_sync_map is not None:
                final_sync_map = reusable_sync_map
            else:
                final_sync_map = json.loads(partial_path.read_text(encoding="utf-8-sig"))
            sync_map_path = artifact_paths["sync_map_path"]
            write_sync_map(final_sync_map, str(sync_map_path))
            self._update_project(
                project_id,
                artifacts={
                    **self._projects[project_id]["artifacts"],
                    "sync_map_path": str(sync_map_path),
                },
            )
            self._write_project_manifest(project_id)

            project_root = Path(self._projects[project_id]["root_path"])
            role_overrides = dict(classification.get("camera_roles") or {})

            transcript_artifact = reusable_transcript if transcript_future is None else transcript_future.result()
            if transcript_artifact is None:
                raise ValueError("Transcript task did not return a result.")
            transcript_artifact["source_sync_map"] = {
                "schema_version": "vazer.sync_map.v1",
                "path": str(sync_map_path),
            }
            transcript_path = artifact_paths["transcript_path"]
            write_transcript_artifact(transcript_artifact, str(transcript_path))
            self._update_project_file(
                project_id,
                master_path,
                ui_status="master",
                ui_note="Transcript ready.",
                extra_fields={
                    "ui_progress_percent": 100.0,
                    "ui_progress_label": "Whisper",
                    "ui_progress_color": "#63c178",
                },
            )
            self._update_project(
                project_id,
                artifacts={
                    **self._projects[project_id]["artifacts"],
                    "transcript_path": str(transcript_path),
                },
            )
            self._write_project_manifest(project_id)
            _set_media_progress("transcript", 100.0, "Transcript complete.", "done")

            analysis_map_path = artifact_paths["analysis_map_path"]
            if reusable_analysis_map is not None:
                analysis_map = reusable_analysis_map
                self._update_project(
                    project_id,
                    artifacts={
                        **self._projects[project_id]["artifacts"],
                        "analysis_map_path": str(analysis_map_path),
                    },
                )
                for entry in final_sync_map.get("entries", []):
                    if not isinstance(entry, dict) or entry.get("status") != "synced":
                        continue
                    camera_path = str(entry.get("path") or "")
                    role = classification["camera_roles"].get(str(entry.get("asset_id")))
                    self._update_project_file(
                        project_id,
                        camera_path,
                        ui_status="analyzed",
                        ui_note=_format_camera_note(role, "Using existing analysis map."),
                        extra_fields={
                            "ui_progress_percent": 100.0,
                            "ui_progress_label": "CV",
                            "ui_progress_color": "#63c178",
                            "ui_sub_progress": [
                                {
                                    "label": "Global",
                                    "percent": 100.0,
                                    "color": "#63c178",
                                }
                            ],
                        },
                    )
                self._write_project_manifest(project_id)
                _set_media_progress("analysis", 100.0, "Using existing analysis map.", "done")
            else:
                master_signals = analyze_master_audio_activity(master_path, analysis_options)
                analyzed_entries: list[dict[str, Any]] = []
                for entry in final_sync_map.get("entries", []):
                    if not isinstance(entry, dict) or entry.get("status") != "synced":
                        continue
                    asset_id = str(entry.get("asset_id"))
                    future = analysis_futures.get(asset_id)
                    if future is None:
                        continue
                    analyzed_entries.append(future.result())

                analysis_map = compose_analysis_map(
                    final_sync_map,
                    source_sync_map_path=str(sync_map_path),
                    options=analysis_options,
                    master_signals=master_signals,
                    analyzed_entries=analyzed_entries,
                )
                write_analysis_map(analysis_map, str(analysis_map_path))
                self._update_project(
                    project_id,
                    artifacts={
                        **self._projects[project_id]["artifacts"],
                        "analysis_map_path": str(analysis_map_path),
                    },
                )
                self._write_project_manifest(project_id)
                _set_media_progress("analysis", 100.0, "Analysis complete.", "done")
            media_executor.shutdown(wait=True)
            with self._lock:
                runtime = self._runtimes.get(job_id)
                if isinstance(runtime, dict):
                    runtime["executor"] = None

            visual_packet_path = artifact_paths["visual_packet_path"]
            ai_cut_plan_path = artifact_paths["cut_plan_ai_path"]
            validation_path = artifact_paths["cut_validation_path"]
            repaired_cut_plan_path = artifact_paths["cut_plan_repaired_path"]
            render_ready_cut_plan_path = artifact_paths["cut_plan_render_path"]
            planning_root = artifact_paths["planning_root"]

            reusable_ai_cut_plan = _load_reusable_cut_plan(
                ai_cut_plan_path,
                str(master_path),
                camera_path_list,
                planning_stage="draft",
                source_sync_map_path=str(sync_map_path),
                source_analysis_map_path=str(analysis_map_path),
                source_transcript_path=str(transcript_path),
            )
            reusable_validation_report = _load_reusable_cut_validation(
                validation_path,
                source_cut_plan_path=str(ai_cut_plan_path),
                source_sync_map_path=str(sync_map_path),
                source_analysis_map_path=str(analysis_map_path),
                source_transcript_path=str(transcript_path),
            )
            reusable_repaired_cut_plan = _load_reusable_cut_plan(
                repaired_cut_plan_path,
                str(master_path),
                camera_path_list,
                planning_stage="repaired",
                source_sync_map_path=str(sync_map_path),
                source_analysis_map_path=str(analysis_map_path),
                source_transcript_path=str(transcript_path),
            )
            reusable_render_ready_cut_plan = _load_reusable_cut_plan(
                render_ready_cut_plan_path,
                str(master_path),
                camera_path_list,
                planning_stage="repaired",
                source_sync_map_path=str(sync_map_path),
                source_analysis_map_path=str(analysis_map_path),
                source_transcript_path=str(transcript_path),
            )

            self._wait_if_paused(job_id)
            self._raise_if_canceled(job_id)
            if reusable_ai_cut_plan is not None:
                ai_cut_plan = reusable_ai_cut_plan
                self._update_job(
                    job_id,
                    stage="planning",
                    stage_label="AI Schnitt",
                    message="Using existing AI cut plan.",
                    progress_percent=82.0,
                )
            else:
                self._update_job(
                    job_id,
                    stage="planning",
                    stage_label="AI Schnitt",
                    message="Building chunked AI draft for the full theater recording.",
                    progress_percent=74.0,
                )
                draft_bundle = build_chunked_ai_draft_bundle(
                    final_sync_map,
                    source_sync_map_path=str(sync_map_path),
                    analysis_map=analysis_map,
                    source_analysis_path=str(analysis_map_path),
                    transcript_artifact=transcript_artifact,
                    source_transcript_path=str(transcript_path),
                    role_overrides=role_overrides,
                    output_dir=str(planning_root),
                    options=TheaterPipelineOptions(),
                )
                write_visual_packet(draft_bundle["visual_packet"], str(visual_packet_path))
                for chunk_index, chunk_plan in enumerate(draft_bundle["chunk_plans"], start=1):
                    chunk_path = planning_root / "chunks" / f"chunk_{chunk_index:04d}.cut_plan.ai.json"
                    write_cut_plan(chunk_plan, str(chunk_path))
                ai_cut_plan = draft_bundle["combined_cut_plan"]
                write_cut_plan(ai_cut_plan, str(ai_cut_plan_path))
            self._update_project(
                project_id,
                artifacts={
                    **self._projects[project_id]["artifacts"],
                    "cut_plan_ai_path": str(ai_cut_plan_path),
                    **(
                        {"visual_packet_path": str(visual_packet_path)}
                        if visual_packet_path.exists()
                        else {}
                    ),
                },
            )
            self._write_project_manifest(project_id)

            self._wait_if_paused(job_id)
            self._raise_if_canceled(job_id)
            def _validation_progress(completed: int, total: int, detail: str) -> None:
                local_progress = 100.0 * completed / max(1, total)
                self._update_job(
                    job_id,
                    stage="validate",
                    stage_label="Cuts pruefen",
                    message=detail,
                    progress_percent=82.0 + 4.0 * (local_progress / 100.0),
                    analysis_pass="local",
                )

            if reusable_validation_report is not None:
                validation_report = reusable_validation_report
                self._update_job(
                    job_id,
                    stage="validate",
                    stage_label="Cuts pruefen",
                    message="Using existing cut validation.",
                    progress_percent=86.0,
                    analysis_pass="local",
                )
            else:
                self._update_job(
                    job_id,
                    stage="validate",
                    stage_label="Cuts pruefen",
                    message="Validating proposed cut points.",
                    progress_percent=82.0,
                    analysis_pass="local",
                )
                validation_report = build_cut_validation_report(
                    ai_cut_plan,
                    sync_map=final_sync_map,
                    source_cut_plan_path=str(ai_cut_plan_path),
                    source_sync_map_path=str(sync_map_path),
                    analysis_map=analysis_map,
                    source_analysis_path=str(analysis_map_path),
                    transcript_artifact=transcript_artifact,
                    source_transcript_path=str(transcript_path),
                    options=CutValidationOptions(),
                    on_progress=_validation_progress,
                )
                write_cut_validation_report(validation_report, str(validation_path))

            self._wait_if_paused(job_id)
            self._raise_if_canceled(job_id)
            if reusable_repaired_cut_plan is not None:
                repaired_cut_plan = reusable_repaired_cut_plan
                render_ready_cut_plan = reusable_render_ready_cut_plan or apply_max_render_size(
                    repaired_cut_plan,
                    max_width=1920,
                    max_height=1080,
                )
                if reusable_render_ready_cut_plan is None:
                    write_cut_plan(render_ready_cut_plan, str(render_ready_cut_plan_path))
                self._update_job(
                    job_id,
                    stage="repair",
                    stage_label="Cuts reparieren",
                    message="Using existing repaired cut plan.",
                    progress_percent=89.0,
                )
            else:
                self._update_job(
                    job_id,
                    stage="repair",
                    stage_label="Cuts reparieren",
                    message="Applying deterministic local cut repairs.",
                    progress_percent=86.0,
                )
                repaired_cut_plan = repair_cut_plan(
                    ai_cut_plan,
                    validation_report,
                    sync_map=final_sync_map,
                    source_cut_plan_path=str(ai_cut_plan_path),
                    source_validation_path=str(validation_path),
                    analysis_map=analysis_map,
                    transcript_artifact=transcript_artifact,
                    options=CutValidationOptions(),
                )
                write_cut_plan(repaired_cut_plan, str(repaired_cut_plan_path))
                render_ready_cut_plan = apply_max_render_size(repaired_cut_plan, max_width=1920, max_height=1080)
                write_cut_plan(render_ready_cut_plan, str(render_ready_cut_plan_path))
            self._update_project(
                project_id,
                artifacts={
                    **self._projects[project_id]["artifacts"],
                    **(
                        {"cut_validation_path": str(validation_path)}
                        if validation_path.exists()
                        else {}
                    ),
                    **(
                        {"cut_plan_repaired_path": str(repaired_cut_plan_path)}
                        if repaired_cut_plan_path.exists()
                        else {}
                    ),
                    "cut_plan_render_path": str(render_ready_cut_plan_path),
                },
            )
            self._write_project_manifest(project_id)

            self._wait_if_paused(job_id)
            self._raise_if_canceled(job_id)
            output_root = Path(project.get("default_output_dir") or (project_root / "output"))
            output_root.mkdir(parents=True, exist_ok=True)
            premiere_xml_path = output_root / f"{project['name']}.premiere.xml"
            self._update_job(
                job_id,
                stage="exporting_premiere_xml",
                stage_label="Premiere XML",
                message="Writing Premiere XML file.",
                progress_percent=89.0,
            )
            premiere_summary = export_premiere_xml(
                repaired_cut_plan,
                output_xml_path=str(premiere_xml_path),
                cut_plan_path=str(repaired_cut_plan_path),
                project_name=project["name"],
            )
            self._update_project(
                project_id,
                artifacts={
                    **self._projects[project_id]["artifacts"],
                    "cut_validation_path": str(validation_path),
                    "cut_plan_repaired_path": str(repaired_cut_plan_path),
                    "cut_plan_render_path": str(render_ready_cut_plan_path),
                    "premiere_xml_path": str(premiere_xml_path),
                },
            )
            self._write_project_manifest(project_id)

            if output_mode == OUTPUT_MODE_PREMIERE_ONLY:
                self._update_job(
                    job_id,
                    status="completed",
                    stage="completed",
                    stage_label="Done",
                    message=(
                        f"Premiere XML export complete. "
                        f"{final_sync_map['summary']['synced']} synced, "
                        f"{validation_report['summary']['fail']} failed cuts after validation. "
                        f"Output: {premiere_xml_path}"
                    ),
                    progress_percent=100.0,
                    artifacts={
                        "sync_map_path": str(sync_map_path),
                        "transcript_path": str(transcript_path),
                        "analysis_map_path": str(analysis_map_path),
                        "visual_packet_path": str(visual_packet_path),
                        "cut_plan_ai_path": str(ai_cut_plan_path),
                        "cut_validation_path": str(validation_path),
                        "cut_plan_repaired_path": str(repaired_cut_plan_path),
                        "premiere_xml_path": str(premiere_summary["output"]["path"]),
                    },
                    details={
                        "file_count": len(files),
                        "camera_count": classification["camera_count"],
                        "master_asset": classification["master_asset"],
                        "output_mode": output_mode,
                    },
                )
                return

            self._wait_if_paused(job_id)
            self._raise_if_canceled(job_id)
            self._update_job(
                job_id,
                stage="rendering",
                stage_label="Render",
                message="Rendering final FHD cut.",
                progress_percent=90.0,
            )
            output_media_path = output_root / f"{project['name']}.fhd.mp4"
            render_manifest = build_render_scaffold(
                render_ready_cut_plan,
                cut_plan_path=str(render_ready_cut_plan_path),
                output_media_path=str(output_media_path),
                scaffold_dir=str(artifact_paths["render_root"]),
            )

            def _render_progress(progress_percent: float, _state: str) -> None:
                self._update_job(
                    job_id,
                    stage="rendering",
                    stage_label="Render",
                    message=f"Rendering final FHD cut. {progress_percent:.0f}%",
                    progress_percent=90.0 + 10.0 * (progress_percent / 100.0),
                )

            run_render(render_manifest, on_progress=_render_progress)
            self._update_project(
                project_id,
                artifacts={
                    **self._projects[project_id]["artifacts"],
                    "render_manifest_path": str(render_manifest["artifacts"]["manifest_path"]),
                    "render_output_path": str(output_media_path),
                },
            )
            self._write_project_manifest(project_id)

            self._update_job(
                job_id,
                status="completed",
                stage="completed",
                stage_label="Done",
                message=(
                    f"Render complete. "
                    f"{final_sync_map['summary']['synced']} synced, "
                    f"{validation_report['summary']['fail']} failed cuts after validation. "
                    f"Output: {output_media_path}"
                ),
                progress_percent=100.0,
                artifacts={
                    "sync_map_path": str(sync_map_path),
                    "transcript_path": str(transcript_path),
                    "analysis_map_path": str(analysis_map_path),
                    "visual_packet_path": str(visual_packet_path),
                    "cut_plan_ai_path": str(ai_cut_plan_path),
                    "cut_validation_path": str(validation_path),
                    "cut_plan_repaired_path": str(repaired_cut_plan_path),
                    "premiere_xml_path": str(premiere_summary["output"]["path"]),
                    "render_output_path": str(output_media_path),
                },
                details={
                    "file_count": len(files),
                    "camera_count": classification["camera_count"],
                    "master_asset": classification["master_asset"],
                    "output_mode": output_mode,
                },
            )
        except JobCanceledError:
            return
        except Exception as error:
            self._update_job(
                job_id,
                status="failed",
                stage="failed",
                stage_label="Error",
                message=str(error),
            )
        finally:
            if media_executor is not None:
                try:
                    media_executor.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    media_executor.shutdown(wait=False)
                except Exception:
                    pass
            with self._lock:
                runtime = self._runtimes.get(job_id)
                if isinstance(runtime, dict):
                    runtime["executor"] = None


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _html_response(handler: BaseHTTPRequestHandler, html: str) -> None:
    data = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def create_ui_handler(app_state: UIState) -> type[BaseHTTPRequestHandler]:
    class UIHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                _html_response(self, INDEX_HTML)
                return
            if parsed.path == "/api/state":
                _json_response(self, 200, app_state.snapshot())
                return
            _json_response(self, 404, {"error": "Not found."})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/uploads/session":
                    _json_response(self, 200, app_state.create_upload_session())
                    return

                if parsed.path.startswith("/api/uploads/") and parsed.path.endswith("/files"):
                    parts = [part for part in parsed.path.split("/") if part]
                    if len(parts) != 4:
                        raise ValueError("Invalid upload path.")
                    session_id = parts[2]
                    query = parse_qs(parsed.query)
                    relative_path = query.get("path", [None])[0]
                    if not relative_path:
                        raise ValueError("Missing upload path.")
                    content_length = int(self.headers.get("Content-Length") or "0")
                    if content_length <= 0:
                        raise ValueError("Upload body is empty.")
                    _json_response(
                        self,
                        200,
                        app_state.write_upload_file(session_id, relative_path, self.rfile, content_length),
                    )
                    return

                if parsed.path == "/api/projects":
                    content_length = int(self.headers.get("Content-Length") or "0")
                    payload = json.loads(self.rfile.read(content_length).decode("utf-8") or "{}")
                    if not isinstance(payload, dict):
                        raise ValueError("Project payload must be a JSON object.")
                    _json_response(
                        self,
                        200,
                        app_state.create_project(
                            str(payload.get("session_id") or ""),
                            None if payload.get("name") is None else str(payload.get("name")),
                            None if payload.get("output_mode") is None else str(payload.get("output_mode")),
                        ),
                    )
                    return

                if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/pause"):
                    parts = [part for part in parsed.path.split("/") if part]
                    if len(parts) != 4:
                        raise ValueError("Invalid pause route.")
                    _json_response(self, 200, app_state.pause_job(parts[2]))
                    return

                if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/resume"):
                    parts = [part for part in parsed.path.split("/") if part]
                    if len(parts) != 4:
                        raise ValueError("Invalid resume route.")
                    _json_response(self, 200, app_state.resume_job(parts[2]))
                    return

                if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/confirm"):
                    parts = [part for part in parsed.path.split("/") if part]
                    if len(parts) != 4:
                        raise ValueError("Invalid confirm route.")
                    _json_response(self, 200, app_state.confirm_job(parts[2]))
                    return

                if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
                    parts = [part for part in parsed.path.split("/") if part]
                    if len(parts) != 4:
                        raise ValueError("Invalid cancel route.")
                    _json_response(self, 200, app_state.cancel_job(parts[2]))
                    return
            except Exception as error:
                _json_response(self, 400, {"error": str(error)})
                return

            _json_response(self, 404, {"error": "Not found."})

    return UIHandler


def serve_ui(*, host: str, port: int, workspace: str, open_browser: bool = False) -> None:
    app_state = UIState(Path(workspace))
    server = ThreadingHTTPServer((host, port), create_ui_handler(app_state))
    if open_browser:
        threading.Thread(target=lambda: webbrowser.open(f"http://{host}:{port}/"), daemon=True).start()

    print(f"VAZer UI listening on http://{host}:{port}/")
    print(f"Workspace: {Path(workspace).resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        server.server_close()
