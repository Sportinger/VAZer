from __future__ import annotations

from collections import Counter
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
from .analysis import AnalysisOptions, build_analysis_map, write_analysis_map
from .camera_roles import build_camera_role_artifact, infer_camera_role_from_name, write_camera_role_artifact
from .cut_plan import write_cut_plan
from .cut_review import CutValidationOptions, build_cut_validation_report, repair_cut_plan, write_cut_validation_report
from .fftools import MediaInfo, probe_media
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


def should_ignore_import_file(path: Path) -> bool:
    name = path.name.strip()
    lowered = name.lower()
    return lowered.startswith(".") or lowered in IGNORED_IMPORT_FILENAMES


def resolve_default_output_dir(paths: list[Path]) -> str | None:
    if not paths:
        return None

    parents = [str(path.parent) for path in paths]
    try:
        common_parent = Path(os.path.commonpath(parents))
    except ValueError:
        return None

    return str(common_parent) if common_parent.exists() else None

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
    button{appearance:none;border:0;border-radius:999px;padding:11px 16px;font:700 14px var(--font);cursor:pointer}
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
    const uploadArea=document.getElementById("uploadArea");
    const jobsEl=document.getElementById("jobs");
    const projectsEl=document.getElementById("projects");
    const metricsEl=document.getElementById("metrics");
    const workspaceLabel=document.getElementById("workspaceLabel");

    async function fetchJson(url,options={}){const response=await fetch(url,options);const payload=await response.json().catch(()=>({error:response.statusText}));if(!response.ok)throw new Error(payload.error||response.statusText);return payload;}
    function fmtTime(value){return value?new Date(value).toLocaleString("de-DE"):"-";}
    function renderMetrics(snapshot){const jobs=snapshot?.jobs||[];const projects=snapshot?.projects||[];const running=jobs.filter(job=>["running","pause_requested","paused"].includes(job.status)).length;const completed=jobs.filter(job=>job.status==="completed").length;metricsEl.innerHTML=[["Projekte",projects.length],["Aktiv",running],["Fertig",completed]].map(metric=>`<div class="metric"><small>${metric[0]}</small><strong>${metric[1]}</strong></div>`).join("");}
    function renderUpload(){if(!state.upload){uploadArea.innerHTML="";return;}const upload=state.upload;const progress=upload.totalBytes>0?Math.min(100,Math.round(upload.sentBytes/upload.totalBytes*100)):0;uploadArea.innerHTML=`<div class="card"><div class="row"><strong>Upload laeuft</strong><span class="badge running">${upload.phase}</span></div><div class="progress"><span style="width:${progress}%"></span></div><div class="mini">${upload.message}</div><div class="meta"><div><strong>Dateien</strong>${upload.fileIndex}/${upload.fileCount}</div><div><strong>Fortschritt</strong>${progress}%</div></div></div>`;}
    function renderJobs(snapshot){const jobs=snapshot?.jobs||[];if(!jobs.length){jobsEl.innerHTML='<div class="muted">Noch keine Jobs. Zieh oben ein paar Clips hinein.</div>';return;}jobsEl.innerHTML=jobs.map(job=>`<div class="card"><div class="row"><strong>${job.project_name}</strong><span class="badge ${job.status}">${job.status.replace("_"," ")}</span></div><div class="progress"><span style="width:${job.progress_percent||0}%"></span></div><div class="mini">${job.stage_label||"wartend"} · ${job.message||"-"}</div><div class="meta"><div><strong>Aktualisiert</strong>${fmtTime(job.updated_at_utc)}</div><div><strong>Fortschritt</strong>${Math.round(job.progress_percent||0)}%</div><div><strong>Master</strong>${job.details?.master_asset||"-"}</div><div><strong>Kameras</strong>${job.details?.camera_count??"-"}</div></div><div class="actions" style="margin-top:12px"><button class="ghost" ${job.status==="running"||job.status==="pause_requested"?"":"disabled"} onclick="pauseJob('${job.id}')">Pause</button><button class="solid" ${job.status==="paused"?"":"disabled"} onclick="resumeJob('${job.id}')">Weiter</button></div></div>`).join("");}
    function renderProjects(snapshot){const projects=snapshot?.projects||[];if(!projects.length){projectsEl.innerHTML='<div class="muted">Noch keine Projekte im Workspace.</div>';return;}projectsEl.innerHTML=projects.map(project=>{const classification=project.classification||{};const files=(project.files||[]).map(file=>file.original_path).join("<br>");const artifacts=project.artifacts||{};return `<div class="card"><div class="row"><strong>${project.name}</strong><span class="mini">${fmtTime(project.created_at_utc)}</span></div><div class="meta"><div><strong>Master</strong>${classification.master_asset||"nicht erkannt"}</div><div><strong>Kameras</strong>${classification.camera_count??0}</div></div><div class="mini" style="margin-top:8px">${files}</div><div class="mini" style="margin-top:10px">${artifacts.sync_map_path?`sync_map: <code>${artifacts.sync_map_path}</code>`:"Noch kein sync_map geschrieben."}</div></div>`;}).join("");}
    function render(snapshot){state.snapshot=snapshot;workspaceLabel.textContent=snapshot.workspace||"";renderMetrics(snapshot);renderUpload();renderJobs(snapshot);renderProjects(snapshot);}
    async function loadState(){try{render(await fetchJson("/api/state"));}catch(error){console.error(error);}}
    function uploadSingleFile(sessionId,file,index,totalCount,totalBytes,counter){return new Promise((resolve,reject)=>{const path=encodeURIComponent(file.webkitRelativePath||file.name);const xhr=new XMLHttpRequest();xhr.open("POST",`/api/uploads/${sessionId}/files?path=${path}`);xhr.upload.onprogress=(event)=>{if(!state.upload)return;state.upload={...state.upload,phase:"upload",fileIndex:index,fileCount:totalCount,sentBytes:counter.baseBytes+event.loaded,totalBytes,message:`${file.name} wird hochgeladen`};renderUpload();};xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300){counter.baseBytes+=file.size;resolve(JSON.parse(xhr.responseText));return;}reject(new Error(xhr.responseText||`Upload fehlgeschlagen: ${file.name}`));};xhr.onerror=()=>reject(new Error(`Upload fehlgeschlagen: ${file.name}`));xhr.send(file);});}
    async function handleFiles(fileList){const files=Array.from(fileList||[]);if(!files.length)return;const totalBytes=files.reduce((sum,file)=>sum+file.size,0);state.upload={phase:"vorbereitung",fileIndex:0,fileCount:files.length,sentBytes:0,totalBytes,message:"Upload-Session wird erstellt"};renderUpload();try{const session=await fetchJson("/api/uploads/session",{method:"POST"});const counter={baseBytes:0};for(let index=0;index<files.length;index+=1){await uploadSingleFile(session.session_id,files[index],index+1,files.length,totalBytes,counter);}state.upload={...state.upload,phase:"projekt",sentBytes:totalBytes,message:"Projekt und Hintergrundjob werden angelegt"};renderUpload();const name=(files[0]?.name||"VAZ Projekt").replace(/\\.[^.]+$/,"");await fetchJson("/api/projects",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({session_id:session.session_id,name})});}catch(error){alert(error.message);}finally{state.upload=null;renderUpload();await loadState();}}
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
                self._projects[project["id"]] = project

        for job in payload.get("jobs", []) if isinstance(payload.get("jobs"), list) else []:
            if not isinstance(job, dict) or not isinstance(job.get("id"), str):
                continue
            if job.get("status") in {"running", "paused", "pause_requested", "review_required"}:
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

    def create_project(self, session_id: str, name: str | None = None) -> dict[str, Any]:
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
            files=files,
        )

    def create_project_from_paths(self, paths: list[str], name: str | None = None) -> dict[str, Any]:
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

        project_id = f"proj_{uuid.uuid4().hex[:10]}"
        project_name = (name or resolved_paths[0].stem or project_id).strip() or project_id
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
            files=files,
        )

    def _register_project_and_start_job(
        self,
        *,
        project_id: str,
        project_name: str,
        project_root: Path,
        inputs_path: str | None,
        default_output_dir: str | None,
        files: list[dict[str, Any]],
    ) -> dict[str, Any]:
        artifacts_root = project_root / "artifacts"
        artifacts_root.mkdir(parents=True, exist_ok=True)
        fallback_output_dir = project_root / "output"
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
            }
            self._persist_state()

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

        for job_id in active_job_ids:
            try:
                self.cancel_job(job_id)
            except Exception:
                pass

        terminate_registered_processes()

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
        with self._lock:
            job = self._jobs[job_id]
            job.update(changes)
            job["updated_at_utc"] = _utc_timestamp()
            self._persist_state()

    def _write_project_manifest(self, project_id: str) -> None:
        with self._lock:
            project = self._projects[project_id]
            root = Path(project["root_path"])
            manifest_path = root / "project.json"
            manifest_path.write_text(json.dumps(project, indent=2), encoding="utf-8")

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
        try:
            with self._lock:
                project = self._projects[project_id]
                files = list(project["files"])
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
                },
            )

            if camera_path_list:
                self._wait_if_paused(job_id)
                self._raise_if_canceled(job_id)
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

                camera_role_path = artifacts_root / "camera_roles.json"
                role_frame_dir = artifacts_root / "camera_roles"
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
                    message="Camera roles assigned.",
                    progress_percent=45.0,
                )
                self._wait_for_role_review(job_id)
                self._update_job(
                    job_id,
                    status="running",
                    stage="roles",
                    stage_label="Camera roles",
                    message="Role review confirmed. Continuing with audio sync.",
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
            entries: list[dict[str, Any]] = []
            master_summary: dict[str, Any] | None = None
            partial_path = artifacts_root / "sync_map.partial.json"

            for index, camera_path in enumerate(camera_path_list, start=1):
                self._wait_if_paused(job_id)
                self._raise_if_canceled(job_id)
                asset_id = asset_id_by_path[camera_path]
                role = classification["camera_roles"].get(asset_id)
                self._update_project_file(
                    project_id,
                    camera_path,
                    ui_status="syncing",
                    ui_note=_format_camera_note(role, f"Syncing camera {index}/{len(camera_path_list)}"),
                    extra_fields={"asset_id": asset_id},
                )
                self._update_job(
                    job_id,
                    stage="syncing",
                    stage_label="Audio sync",
                    message=f"Syncing {Path(camera_path).name} ({index}/{len(camera_path_list)})",
                    progress_percent=45.0 + 45.0 * ((index - 1) / len(camera_path_list)),
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
                    self._update_project_file(
                        project_id,
                        camera_path,
                        ui_status="failed",
                        ui_note=_format_camera_note(role, f"Sync failed: {error}"),
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
                        extra_fields={"camera_role": role},
                    )

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

            project_root = Path(self._projects[project_id]["root_path"])
            role_overrides = dict(classification.get("camera_roles") or {})

            self._wait_if_paused(job_id)
            self._raise_if_canceled(job_id)
            self._update_job(
                job_id,
                stage="transcribing",
                stage_label="Transcript",
                message="Transcribing master audio with Whisper.",
                progress_percent=58.0,
            )
            transcript_artifact = build_master_transcript(
                master_path,
                source_sync_map_path=str(sync_map_path),
                options=TranscriptionOptions(model="whisper-1"),
            )
            transcript_path = artifacts_root / "transcript.json"
            write_transcript_artifact(transcript_artifact, str(transcript_path))
            self._update_project(
                project_id,
                artifacts={
                    **self._projects[project_id]["artifacts"],
                    "transcript_path": str(transcript_path),
                },
            )
            self._write_project_manifest(project_id)

            self._wait_if_paused(job_id)
            self._raise_if_canceled(job_id)
            self._update_job(
                job_id,
                stage="analysis",
                stage_label="Analyse",
                message="Running cheap technical analysis on synced cameras.",
                progress_percent=66.0,
            )
            analysis_map = build_analysis_map(
                final_sync_map,
                source_sync_map_path=str(sync_map_path),
                options=AnalysisOptions(),
            )
            analysis_map_path = artifacts_root / "analysis_map.json"
            write_analysis_map(analysis_map, str(analysis_map_path))
            self._update_project(
                project_id,
                artifacts={
                    **self._projects[project_id]["artifacts"],
                    "analysis_map_path": str(analysis_map_path),
                },
            )
            self._write_project_manifest(project_id)

            self._wait_if_paused(job_id)
            self._raise_if_canceled(job_id)
            self._update_job(
                job_id,
                stage="planning",
                stage_label="AI Schnitt",
                message="Building chunked AI draft for the full theater recording.",
                progress_percent=74.0,
            )
            planning_root = artifacts_root / "planning"
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
            visual_packet_path = planning_root / "visual_packet.json"
            write_visual_packet(draft_bundle["visual_packet"], str(visual_packet_path))
            for chunk_index, chunk_plan in enumerate(draft_bundle["chunk_plans"], start=1):
                chunk_path = planning_root / "chunks" / f"chunk_{chunk_index:04d}.cut_plan.ai.json"
                write_cut_plan(chunk_plan, str(chunk_path))
            ai_cut_plan = draft_bundle["combined_cut_plan"]
            ai_cut_plan_path = artifacts_root / "cut_plan.ai.json"
            write_cut_plan(ai_cut_plan, str(ai_cut_plan_path))
            self._update_project(
                project_id,
                artifacts={
                    **self._projects[project_id]["artifacts"],
                    "visual_packet_path": str(visual_packet_path),
                    "cut_plan_ai_path": str(ai_cut_plan_path),
                },
            )
            self._write_project_manifest(project_id)

            self._wait_if_paused(job_id)
            self._raise_if_canceled(job_id)
            self._update_job(
                job_id,
                stage="validate",
                stage_label="Cuts pruefen",
                message="Validating proposed cut points.",
                progress_percent=82.0,
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
            )
            validation_path = artifacts_root / "cut_validation.json"
            write_cut_validation_report(validation_report, str(validation_path))

            self._wait_if_paused(job_id)
            self._raise_if_canceled(job_id)
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
            repaired_cut_plan_path = artifacts_root / "cut_plan.repaired.json"
            write_cut_plan(repaired_cut_plan, str(repaired_cut_plan_path))
            render_ready_cut_plan = apply_max_render_size(repaired_cut_plan, max_width=1920, max_height=1080)
            render_ready_cut_plan_path = artifacts_root / "cut_plan.repaired.fhd.json"
            write_cut_plan(render_ready_cut_plan, str(render_ready_cut_plan_path))
            self._update_project(
                project_id,
                artifacts={
                    **self._projects[project_id]["artifacts"],
                    "cut_validation_path": str(validation_path),
                    "cut_plan_repaired_path": str(repaired_cut_plan_path),
                    "cut_plan_render_path": str(render_ready_cut_plan_path),
                },
            )
            self._write_project_manifest(project_id)

            self._wait_if_paused(job_id)
            self._raise_if_canceled(job_id)
            self._update_job(
                job_id,
                stage="rendering",
                stage_label="Render",
                message="Rendering final FHD cut.",
                progress_percent=90.0,
            )
            output_root = Path(project.get("default_output_dir") or (project_root / "output"))
            output_root.mkdir(parents=True, exist_ok=True)
            output_media_path = output_root / f"{project['name']}.fhd.mp4"
            render_manifest = build_render_scaffold(
                render_ready_cut_plan,
                cut_plan_path=str(render_ready_cut_plan_path),
                output_media_path=str(output_media_path),
                scaffold_dir=str(artifacts_root / "render"),
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
                    "render_output_path": str(output_media_path),
                },
                details={
                    "file_count": len(files),
                    "camera_count": classification["camera_count"],
                    "master_asset": classification["master_asset"],
                },
            )
        except Exception as error:
            self._update_job(
                job_id,
                status="failed",
                stage="failed",
                stage_label="Error",
                message=str(error),
            )


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
