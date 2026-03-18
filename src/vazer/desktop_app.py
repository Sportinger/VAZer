from __future__ import annotations

from pathlib import Path


def launch_desktop_app(*, workspace: str, auto_quit_ms: int | None = None) -> int:
    try:
        from PySide6.QtCore import QMimeData, Qt, QTimer, Signal
        from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent
        from PySide6.QtWidgets import (
            QApplication,
            QFileDialog,
            QFrame,
            QGridLayout,
            QHBoxLayout,
            QLabel,
            QListWidget,
            QListWidgetItem,
            QMainWindow,
            QMessageBox,
            QPlainTextEdit,
            QProgressBar,
            QPushButton,
            QSplitter,
            QStatusBar,
            QVBoxLayout,
            QWidget,
        )
    except ModuleNotFoundError as error:  # pragma: no cover - runtime dependency guard
        raise ValueError("PySide6 is not installed. Install project dependencies first.") from error

    from .ui_server import UIState

    class DropPanel(QFrame):
        files_dropped = Signal(list)
        clicked = Signal()

        def __init__(self) -> None:
            super().__init__()
            self.setAcceptDrops(True)
            self.setObjectName("dropPanel")
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(28, 28, 28, 28)
            layout.setSpacing(8)

            title = QLabel("Clips direkt hier hineinziehen")
            title.setObjectName("dropTitle")
            subtitle = QLabel(
                "Die Desktop-App nimmt lokale Dateien direkt vom Dateisystem. "
                "Kein Browser-Upload, kein Umweg."
            )
            subtitle.setObjectName("dropSubtitle")
            subtitle.setWordWrap(True)
            layout.addWidget(title)
            layout.addWidget(subtitle)

        def mousePressEvent(self, event) -> None:  # type: ignore[override]
            if event.button() == Qt.MouseButton.LeftButton:
                self.clicked.emit()
            super().mousePressEvent(event)

        def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
            if _mime_has_local_urls(event.mimeData()):
                event.acceptProposedAction()
                self.setProperty("dragover", True)
                self.style().polish(self)
            else:
                event.ignore()

        def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
            self.setProperty("dragover", False)
            self.style().polish(self)
            super().dragLeaveEvent(event)

        def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
            self.setProperty("dragover", False)
            self.style().polish(self)
            paths = _paths_from_mime(event.mimeData())
            if paths:
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
            else:
                event.ignore()

    class MainWindow(QMainWindow):
        def __init__(self, app_state: UIState) -> None:
            super().__init__()
            self.app_state = app_state
            self.snapshot: dict[str, object] = {}
            self.setWindowTitle("VAZer")
            self.resize(1360, 860)
            self.setMinimumSize(1120, 720)

            central = QWidget()
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setContentsMargins(18, 18, 18, 18)
            root.setSpacing(16)

            hero = QFrame()
            hero.setObjectName("hero")
            hero_layout = QHBoxLayout(hero)
            hero_layout.setContentsMargins(22, 22, 22, 22)
            hero_layout.setSpacing(18)
            hero_text = QVBoxLayout()
            hero_text.setSpacing(6)
            eyebrow = QLabel("Theater VAZ")
            eyebrow.setObjectName("eyebrow")
            title = QLabel("Multicam-Mitschnitt lokal laden und Jobs kontrollieren")
            title.setObjectName("heroTitle")
            title.setWordWrap(True)
            subtitle = QLabel(
                "Minimaler Desktop-Start fuer den lokalen VAZer-Workflow: "
                "Dateien referenzieren, Job sehen, Pause und Resume ohne Browser."
            )
            subtitle.setObjectName("heroSubtitle")
            subtitle.setWordWrap(True)
            hero_text.addWidget(eyebrow)
            hero_text.addWidget(title)
            hero_text.addWidget(subtitle)
            hero_text.addStretch(1)
            hero_layout.addLayout(hero_text, 3)

            self.metrics_frame = QFrame()
            metrics_layout = QGridLayout(self.metrics_frame)
            metrics_layout.setContentsMargins(0, 0, 0, 0)
            metrics_layout.setHorizontalSpacing(10)
            metrics_layout.setVerticalSpacing(10)
            self.metric_labels: dict[str, QLabel] = {}
            for index, metric_name in enumerate(("Projekte", "Aktiv", "Fertig")):
                card = QFrame()
                card.setObjectName("metricCard")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(14, 12, 14, 12)
                card_layout.setSpacing(6)
                label = QLabel(metric_name)
                label.setObjectName("metricLabel")
                value = QLabel("0")
                value.setObjectName("metricValue")
                card_layout.addWidget(label)
                card_layout.addWidget(value)
                metrics_layout.addWidget(card, index, 0)
                self.metric_labels[metric_name] = value
            hero_layout.addWidget(self.metrics_frame, 1)
            root.addWidget(hero)

            toolbar = QHBoxLayout()
            toolbar.setSpacing(10)
            self.workspace_label = QLabel(str(app_state.workspace))
            self.workspace_label.setObjectName("workspaceLabel")
            self.add_button = QPushButton("Dateien waehlen")
            self.add_button.clicked.connect(self.pick_files)
            self.refresh_button = QPushButton("Aktualisieren")
            self.refresh_button.setProperty("secondary", True)
            self.refresh_button.clicked.connect(self.refresh_state)
            toolbar.addWidget(self.workspace_label, 1)
            toolbar.addWidget(self.refresh_button)
            toolbar.addWidget(self.add_button)
            root.addLayout(toolbar)

            self.drop_panel = DropPanel()
            self.drop_panel.files_dropped.connect(self.import_paths)
            self.drop_panel.clicked.connect(self.pick_files)
            root.addWidget(self.drop_panel)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            root.addWidget(splitter, 1)

            jobs_panel = QFrame()
            jobs_panel.setObjectName("panel")
            jobs_layout = QVBoxLayout(jobs_panel)
            jobs_layout.setContentsMargins(18, 18, 18, 18)
            jobs_layout.setSpacing(14)
            jobs_header = QLabel("Jobs")
            jobs_header.setObjectName("panelTitle")
            jobs_layout.addWidget(jobs_header)
            self.jobs_list = QListWidget()
            self.jobs_list.currentItemChanged.connect(self.on_job_selection_changed)
            jobs_layout.addWidget(self.jobs_list, 1)
            self.job_progress = QProgressBar()
            self.job_progress.setRange(0, 100)
            jobs_layout.addWidget(self.job_progress)
            self.job_stage = QLabel("Kein Job gewaehlt.")
            self.job_stage.setObjectName("detailTitle")
            jobs_layout.addWidget(self.job_stage)
            self.job_details = QPlainTextEdit()
            self.job_details.setReadOnly(True)
            self.job_details.setMinimumHeight(180)
            jobs_layout.addWidget(self.job_details)
            job_actions = QHBoxLayout()
            self.pause_button = QPushButton("Pause")
            self.pause_button.setProperty("secondary", True)
            self.pause_button.clicked.connect(self.pause_selected_job)
            self.resume_button = QPushButton("Weiter")
            self.resume_button.clicked.connect(self.resume_selected_job)
            job_actions.addWidget(self.pause_button)
            job_actions.addWidget(self.resume_button)
            job_actions.addStretch(1)
            jobs_layout.addLayout(job_actions)
            splitter.addWidget(jobs_panel)

            projects_panel = QFrame()
            projects_panel.setObjectName("panel")
            projects_layout = QVBoxLayout(projects_panel)
            projects_layout.setContentsMargins(18, 18, 18, 18)
            projects_layout.setSpacing(14)
            projects_header = QLabel("Projekte")
            projects_header.setObjectName("panelTitle")
            projects_layout.addWidget(projects_header)
            self.projects_list = QListWidget()
            self.projects_list.currentItemChanged.connect(self.on_project_selection_changed)
            projects_layout.addWidget(self.projects_list, 1)
            self.project_title = QLabel("Kein Projekt gewaehlt.")
            self.project_title.setObjectName("detailTitle")
            projects_layout.addWidget(self.project_title)
            self.project_details = QPlainTextEdit()
            self.project_details.setReadOnly(True)
            self.project_details.setMinimumHeight(220)
            projects_layout.addWidget(self.project_details)
            splitter.addWidget(projects_panel)
            splitter.setSizes([650, 520])

            status_bar = QStatusBar()
            self.setStatusBar(status_bar)
            self.statusBar().showMessage("Bereit.")

            file_action = QAction("Dateien waehlen", self)
            file_action.triggered.connect(self.pick_files)
            self.addAction(file_action)

            self.timer = QTimer(self)
            self.timer.setInterval(1200)
            self.timer.timeout.connect(self.refresh_state)
            self.timer.start()

            self.apply_styles()
            self.refresh_state()

        def apply_styles(self) -> None:
            self.setStyleSheet(
                """
                QWidget {
                  background: #101319;
                  color: #f4eee2;
                  font-family: "Aptos", "Segoe UI Variable", "Segoe UI", sans-serif;
                  font-size: 14px;
                }
                QMainWindow { background: #101319; }
                QFrame#hero, QFrame#panel, QFrame#metricCard, QFrame#dropPanel {
                  background: #171d25;
                  border: 1px solid rgba(255,255,255,0.08);
                  border-radius: 18px;
                }
                QFrame#dropPanel[dragover="true"] {
                  border: 1px solid #ef6b3c;
                  background: #1e242e;
                }
                QLabel#eyebrow {
                  color: #ef9d4d;
                  font-size: 12px;
                  font-weight: 800;
                  letter-spacing: 2px;
                  text-transform: uppercase;
                }
                QLabel#heroTitle {
                  font-family: "Bahnschrift", "Trebuchet MS", sans-serif;
                  font-size: 34px;
                  font-weight: 700;
                }
                QLabel#heroSubtitle, QLabel#workspaceLabel, QLabel#dropSubtitle {
                  color: #b9b2a3;
                  line-height: 1.5;
                }
                QLabel#dropTitle, QLabel#panelTitle, QLabel#detailTitle {
                  font-family: "Bahnschrift", "Trebuchet MS", sans-serif;
                }
                QLabel#dropTitle { font-size: 24px; }
                QLabel#panelTitle { font-size: 22px; }
                QLabel#detailTitle { font-size: 18px; }
                QLabel#metricLabel {
                  color: #b9b2a3;
                  font-size: 11px;
                  font-weight: 700;
                  letter-spacing: 1.8px;
                  text-transform: uppercase;
                }
                QLabel#metricValue {
                  font-size: 24px;
                  font-weight: 700;
                }
                QPushButton {
                  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ef9d4d, stop:1 #ef6b3c);
                  color: #15110d;
                  border: 0;
                  border-radius: 18px;
                  padding: 10px 16px;
                  font-weight: 700;
                }
                QPushButton[secondary="true"] {
                  background: rgba(255,255,255,0.06);
                  color: #f4eee2;
                  border: 1px solid rgba(255,255,255,0.08);
                }
                QPushButton:disabled {
                  color: rgba(244,238,226,0.45);
                  background: rgba(255,255,255,0.05);
                }
                QListWidget, QPlainTextEdit {
                  background: #11161d;
                  border: 1px solid rgba(255,255,255,0.08);
                  border-radius: 14px;
                  padding: 8px;
                }
                QListWidget::item {
                  padding: 10px;
                  border-bottom: 1px solid rgba(255,255,255,0.04);
                }
                QListWidget::item:selected {
                  background: #202734;
                  border-radius: 10px;
                }
                QProgressBar {
                  border: 1px solid rgba(255,255,255,0.08);
                  background: #11161d;
                  border-radius: 999px;
                  text-align: center;
                  min-height: 18px;
                }
                QProgressBar::chunk {
                  border-radius: 999px;
                  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ef9d4d, stop:1 #ef6b3c);
                }
                QStatusBar {
                  color: #b9b2a3;
                  border-top: 1px solid rgba(255,255,255,0.06);
                }
                """
            )

        def pick_files(self) -> None:
            paths, _ = QFileDialog.getOpenFileNames(
                self,
                "Clips und Master waehlen",
                str(Path.home()),
                "Media Files (*.wav *.mp3 *.m4a *.aac *.mov *.mp4 *.mxf *.mkv *.avi);;Alle Dateien (*.*)",
            )
            if paths:
                self.import_paths(paths)

        def import_paths(self, paths: list[str]) -> None:
            expanded = self._expand_paths(paths)
            if not expanded:
                self.statusBar().showMessage("Keine gueltigen Dateien gefunden.")
                return
            project_name = Path(expanded[0]).stem or "VAZ Projekt"
            try:
                result = self.app_state.create_project_from_paths(expanded, name=project_name)
            except Exception as error:
                QMessageBox.critical(self, "VAZer", str(error))
                return

            self.statusBar().showMessage(f"Projekt angelegt: {project_name}")
            self.refresh_state(select_job_id=result["job_id"], select_project_id=result["project_id"])

        def _expand_paths(self, paths: list[str]) -> list[str]:
            collected: list[str] = []
            seen: set[str] = set()
            for raw_path in paths:
                path = Path(raw_path).expanduser()
                if path.is_dir():
                    candidates = sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
                elif path.is_file():
                    candidates = [path]
                else:
                    candidates = []
                for candidate in candidates:
                    resolved = str(candidate.resolve())
                    if resolved not in seen:
                        seen.add(resolved)
                        collected.append(resolved)
            return collected

        def refresh_state(
            self,
            *,
            select_job_id: str | None = None,
            select_project_id: str | None = None,
        ) -> None:
            previous_job_id = select_job_id or self._current_item_id(self.jobs_list)
            previous_project_id = select_project_id or self._current_item_id(self.projects_list)
            snapshot = self.app_state.snapshot()
            self.snapshot = snapshot
            jobs = snapshot["jobs"]
            projects = snapshot["projects"]
            self.workspace_label.setText(snapshot["workspace"])
            self.metric_labels["Projekte"].setText(str(len(projects)))
            self.metric_labels["Aktiv"].setText(str(sum(1 for job in jobs if job["status"] in {"queued", "running", "paused", "pause_requested"})))
            self.metric_labels["Fertig"].setText(str(sum(1 for job in jobs if job["status"] == "completed")))

            self.jobs_list.blockSignals(True)
            self.jobs_list.clear()
            selected_job_row = -1
            for row, job in enumerate(jobs):
                item = QListWidgetItem(self._job_summary(job))
                item.setData(Qt.ItemDataRole.UserRole, job["id"])
                self.jobs_list.addItem(item)
                if job["id"] == previous_job_id:
                    selected_job_row = row
            self.jobs_list.blockSignals(False)
            if self.jobs_list.count():
                self.jobs_list.setCurrentRow(selected_job_row if selected_job_row >= 0 else 0)
            else:
                self.on_job_selection_changed()

            self.projects_list.blockSignals(True)
            self.projects_list.clear()
            selected_project_row = -1
            for row, project in enumerate(projects):
                item = QListWidgetItem(self._project_summary(project))
                item.setData(Qt.ItemDataRole.UserRole, project["id"])
                self.projects_list.addItem(item)
                if project["id"] == previous_project_id:
                    selected_project_row = row
            self.projects_list.blockSignals(False)
            if self.projects_list.count():
                self.projects_list.setCurrentRow(selected_project_row if selected_project_row >= 0 else 0)
            else:
                self.on_project_selection_changed()

        def _job_summary(self, job: dict[str, object]) -> str:
            progress = int(round(float(job.get("progress_percent") or 0.0)))
            return f"[{progress:>3}%] {job['project_name']}  |  {job['status']}  |  {job.get('stage_label') or '-'}"

        def _project_summary(self, project: dict[str, object]) -> str:
            classification = project.get("classification") or {}
            camera_count = classification.get("camera_count") if isinstance(classification, dict) else 0
            return f"{project['name']}  |  {camera_count} Kamera(s)"

        def _current_item_id(self, widget: QListWidget) -> str | None:
            item = widget.currentItem()
            if item is None:
                return None
            value = item.data(Qt.ItemDataRole.UserRole)
            return None if value is None else str(value)

        def _selected_payload(self, key: str, widget: QListWidget) -> dict[str, object] | None:
            item_id = self._current_item_id(widget)
            if not item_id:
                return None
            for payload in self.snapshot.get(key, []):
                if payload["id"] == item_id:
                    return payload
            return None

        def on_job_selection_changed(self) -> None:
            job = self._selected_payload("jobs", self.jobs_list)
            if not job:
                self.job_stage.setText("Kein Job gewaehlt.")
                self.job_progress.setValue(0)
                self.job_details.setPlainText("")
                self.pause_button.setEnabled(False)
                self.resume_button.setEnabled(False)
                return

            self.job_stage.setText(f"{job['project_name']} - {job.get('stage_label') or '-'}")
            self.job_progress.setValue(int(round(float(job.get("progress_percent") or 0.0))))
            details = job.get("details") or {}
            artifacts = job.get("artifacts") or {}
            lines = [
                f"Status: {job['status']}",
                f"Message: {job.get('message') or '-'}",
                f"Updated: {job.get('updated_at_utc') or '-'}",
                "",
                f"Master: {details.get('master_asset') or '-'}",
                f"Kameras: {details.get('camera_count') or '-'}",
                f"Dateien: {details.get('file_count') or '-'}",
            ]
            if artifacts.get("sync_map_path"):
                lines.extend(["", f"sync_map: {artifacts['sync_map_path']}"])
            self.job_details.setPlainText("\n".join(lines))
            self.pause_button.setEnabled(job["status"] in {"running", "pause_requested"})
            self.resume_button.setEnabled(job["status"] == "paused")

        def on_project_selection_changed(self) -> None:
            project = self._selected_payload("projects", self.projects_list)
            if not project:
                self.project_title.setText("Kein Projekt gewaehlt.")
                self.project_details.setPlainText("")
                return

            self.project_title.setText(str(project["name"]))
            classification = project.get("classification") or {}
            artifacts = project.get("artifacts") or {}
            files = project.get("files") or []
            lines = [
                f"Root: {project.get('root_path') or '-'}",
                f"Master: {classification.get('master_asset') if isinstance(classification, dict) else '-'}",
                f"Kameras: {classification.get('camera_count') if isinstance(classification, dict) else '-'}",
            ]
            if isinstance(artifacts, dict) and artifacts.get("sync_map_path"):
                lines.append(f"sync_map: {artifacts['sync_map_path']}")
            lines.extend(["", "Dateien:"])
            lines.extend(f"- {file_info.get('original_path')}" for file_info in files if isinstance(file_info, dict))
            self.project_details.setPlainText("\n".join(lines))

        def pause_selected_job(self) -> None:
            job_id = self._current_item_id(self.jobs_list)
            if not job_id:
                return
            try:
                self.app_state.pause_job(job_id)
            except Exception as error:
                QMessageBox.critical(self, "VAZer", str(error))
                return
            self.refresh_state(select_job_id=job_id)

        def resume_selected_job(self) -> None:
            job_id = self._current_item_id(self.jobs_list)
            if not job_id:
                return
            try:
                self.app_state.resume_job(job_id)
            except Exception as error:
                QMessageBox.critical(self, "VAZer", str(error))
                return
            self.refresh_state(select_job_id=job_id)

    def _mime_has_local_urls(mime_data: QMimeData) -> bool:
        return mime_data.hasUrls() and any(url.isLocalFile() for url in mime_data.urls())

    def _paths_from_mime(mime_data: QMimeData) -> list[str]:
        return [url.toLocalFile() for url in mime_data.urls() if url.isLocalFile()]

    app = QApplication.instance() or QApplication([])
    window = MainWindow(UIState(Path(workspace)))
    window.show()
    window.raise_()
    window.activateWindow()
    if auto_quit_ms is not None:
        QTimer.singleShot(auto_quit_ms, app.quit)
    return app.exec()
