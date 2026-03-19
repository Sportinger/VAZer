from __future__ import annotations

from hashlib import sha1
import json
import os
from pathlib import Path
import re
from typing import Any


def _slugify(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = lowered.strip("-")
    return lowered or "asset"


def launch_desktop_app(*, workspace: str, auto_quit_ms: int | None = None) -> int:
    try:
        import cv2
        from PySide6.QtCore import QSize, Qt, QTimer
        from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage, QPainter, QPainterPath, QPen, QPixmap
        from PySide6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QFileDialog,
            QFrame,
            QHBoxLayout,
            QLabel,
            QListWidget,
            QListWidgetItem,
            QMainWindow,
            QMessageBox,
            QProgressBar,
            QPushButton,
            QSplitter,
            QStackedWidget,
            QVBoxLayout,
            QWidget,
            QSizePolicy,
        )
    except ModuleNotFoundError as error:  # pragma: no cover - runtime dependency guard
        raise ValueError("PySide6 is not installed. Install project dependencies first.") from error

    from .fftools import probe_media
    from .ui_server import (
        OUTPUT_MODE_PREMIERE_ONLY,
        OUTPUT_MODE_RENDER_AND_PREMIERE,
        UIState,
        should_ignore_import_file,
    )

    PREVIEW_MAX_WIDTH = 1280
    PREVIEW_MAX_HEIGHT = 720

    PIPELINE_PHASES = [
        {"id": "probe", "symbol": "ING", "label": "Import", "detail": "Probe"},
        {"id": "classify", "symbol": "SET", "label": "Setup", "detail": "Master + Cams"},
        {"id": "roles", "symbol": "AI", "label": "Rollen", "detail": "Totale / HT / Close"},
        {"id": "sync", "symbol": "A/V", "label": "Sync", "detail": "Audio"},
        {"id": "transcript", "symbol": "TXT", "label": "Text", "detail": "Whisper"},
        {"id": "analysis", "symbol": "CV", "label": "Bild", "detail": "Schaerfe + Motion"},
        {"id": "cut", "symbol": "CUT", "label": "Schnitt", "detail": "Draft + Repair"},
        {"id": "render", "symbol": "OUT", "label": "Export", "detail": "MP4 / XML"},
    ]
    STAGE_TO_PHASE_ID = {
        "queued": "probe",
        "probing": "probe",
        "classified": "classify",
        "roles": "roles",
        "role_review": "roles",
        "syncing": "sync",
        "transcribing": "transcript",
        "analysis": "analysis",
        "planning": "cut",
        "validate": "cut",
        "repair": "cut",
        "exporting_premiere": "render",
        "exporting_premiere_xml": "render",
        "rendering": "render",
        "completed": "render",
    }
    PHASE_INDEX = {phase["id"]: index for index, phase in enumerate(PIPELINE_PHASES)}
    PHASE_PROGRESS_RANGES = {
        "probe": (2.0, 35.0),
        "classify": (35.0, 40.0),
        "roles": (40.0, 46.0),
        "sync": (46.0, 74.0),
        "transcript": (46.0, 74.0),
        "analysis": (46.0, 74.0),
        "cut": (74.0, 89.0),
        "render": (89.0, 100.0),
    }

    class FileRowWidget(QWidget):
        def __init__(self, file_info: dict[str, Any]) -> None:
            super().__init__()
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(6, 6, 6, 6)
            layout.setSpacing(5)

            name_label = QLabel(str(file_info.get("display_name") or Path(file_info["stored_path"]).name))
            name_label.setObjectName("fileRowName")
            name_label.setWordWrap(False)
            layout.addWidget(name_label)

            note_parts = [str(file_info.get("ui_status") or "queued")]
            note = str(file_info.get("ui_note") or "").strip()
            if note:
                note_parts.append(note)
            detail_label = QLabel(" | ".join(part for part in note_parts if part))
            detail_label.setObjectName("fileRowDetail")
            detail_label.setWordWrap(True)
            layout.addWidget(detail_label)

            progress_percent = file_info.get("ui_progress_percent")
            progress_label = str(file_info.get("ui_progress_label") or "").strip()
            if progress_percent is not None:
                progress_bar = QProgressBar()
                progress_bar.setRange(0, 100)
                progress_bar.setValue(int(round(float(progress_percent))))
                progress_bar.setTextVisible(True)
                progress_bar.setFormat(
                    f"{progress_label} %p%" if progress_label else "%p%"
                )
                progress_bar.setObjectName("fileRowProgress")
                progress_color = str(file_info.get("ui_progress_color") or "#ef9d4d")
                progress_bar.setStyleSheet(
                    f"""
                    QProgressBar {{
                      min-height: 10px;
                      max-height: 10px;
                      border: 1px solid rgba(255,255,255,0.08);
                      border-radius: 999px;
                      background: rgba(255,255,255,0.06);
                      color: transparent;
                    }}
                    QProgressBar::chunk {{
                      border-radius: 999px;
                      background: {progress_color};
                    }}
                    """
                )
                layout.addWidget(progress_bar)

            sub_progress = file_info.get("ui_sub_progress") or []
            if isinstance(sub_progress, list):
                for sub_entry in sub_progress:
                    if not isinstance(sub_entry, dict):
                        continue
                    sub_percent = sub_entry.get("percent")
                    if sub_percent is None:
                        continue
                    sub_bar = QProgressBar()
                    sub_bar.setRange(0, 100)
                    sub_bar.setValue(int(round(float(sub_percent))))
                    sub_bar.setTextVisible(True)
                    sub_label = str(sub_entry.get("label") or "").strip()
                    sub_bar.setFormat(f"{sub_label} %p%" if sub_label else "%p%")
                    sub_bar.setObjectName("fileRowSubProgress")
                    sub_color = str(sub_entry.get("color") or "#b9b2a3")
                    sub_bar.setStyleSheet(
                        f"""
                        QProgressBar {{
                          min-height: 7px;
                          max-height: 7px;
                          border: 1px solid rgba(255,255,255,0.06);
                          border-radius: 999px;
                          background: rgba(255,255,255,0.04);
                          color: transparent;
                        }}
                        QProgressBar::chunk {{
                          border-radius: 999px;
                          background: {sub_color};
                        }}
                        """
                    )
                    layout.addWidget(sub_bar)

    class ScaledImageLabel(QLabel):
        def __init__(self, text: str = "", *, fallback_size: QSize | None = None) -> None:
            super().__init__(text)
            self._source_pixmap: QPixmap | None = None
            self._scaled_cache: QPixmap | None = None
            self._scaled_cache_size = QSize()
            self._fallback_size = fallback_size or QSize(640, 360)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        def set_source_pixmap(self, pixmap: QPixmap | None) -> None:
            self._source_pixmap = None if pixmap is None or pixmap.isNull() else QPixmap(pixmap)
            self._scaled_cache = None
            self._scaled_cache_size = QSize()
            if self._source_pixmap is None:
                super().setPixmap(QPixmap())
            self.updateGeometry()
            self.update()

        def clear(self) -> None:  # type: ignore[override]
            super().clear()
            self.set_source_pixmap(None)

        def sizeHint(self) -> QSize:  # type: ignore[override]
            return self._fallback_size

        def minimumSizeHint(self) -> QSize:  # type: ignore[override]
            return self._fallback_size

        def resizeEvent(self, event) -> None:  # type: ignore[override]
            self._scaled_cache = None
            self._scaled_cache_size = QSize()
            super().resizeEvent(event)

        def _scaled_pixmap(self) -> QPixmap | None:
            if self._source_pixmap is None:
                return None
            target_size = self.contentsRect().size()
            if target_size.width() <= 0 or target_size.height() <= 0:
                return None
            if self._scaled_cache is not None and self._scaled_cache_size == target_size:
                return self._scaled_cache
            self._scaled_cache = self._source_pixmap.scaled(
                target_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scaled_cache_size = QSize(target_size)
            return self._scaled_cache

        def paintEvent(self, event) -> None:  # type: ignore[override]
            super().paintEvent(event)
            scaled = self._scaled_pixmap()
            if scaled is None:
                return
            target = self.contentsRect()
            x = target.x() + max(0, (target.width() - scaled.width()) // 2)
            y = target.y() + max(0, (target.height() - scaled.height()) // 2)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(x, y, scaled)

    class PhaseBadge(QWidget):
        def __init__(self, symbol: str) -> None:
            super().__init__()
            self.symbol = symbol
            self.phase_state = "pending"
            self.fill_percent = 0.0
            self.setMinimumSize(60, 60)
            self.setMaximumHeight(60)

        def set_visual_state(self, phase_state: str, fill_percent: float) -> None:
            bounded_fill = max(0.0, min(100.0, float(fill_percent)))
            if self.phase_state == phase_state and abs(self.fill_percent - bounded_fill) < 0.01:
                return
            self.phase_state = phase_state
            self.fill_percent = bounded_fill
            self.update()

        def paintEvent(self, _event) -> None:  # type: ignore[override]
            palettes = {
                "pending": {
                    "background": QColor("#1f2630"),
                    "border": QColor(255, 255, 255, 24),
                    "fill": QColor(255, 255, 255, 10),
                    "text": QColor("#f5efe4"),
                },
                "active": {
                    "background": QColor("#221b16"),
                    "border": QColor("#ef9d4d"),
                    "fill": QColor("#ef9d4d"),
                    "text": QColor("#17110d"),
                },
                "review": {
                    "background": QColor("#162031"),
                    "border": QColor("#70abff"),
                    "fill": QColor("#70abff"),
                    "text": QColor("#101319"),
                },
                "done": {
                    "background": QColor("#152119"),
                    "border": QColor("#63c178"),
                    "fill": QColor("#63c178"),
                    "text": QColor("#102013"),
                },
                "error": {
                    "background": QColor("#261718"),
                    "border": QColor("#e26060"),
                    "fill": QColor("#e26060"),
                    "text": QColor("#210d0d"),
                },
            }
            palette = palettes.get(self.phase_state, palettes["pending"])

            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = self.rect().adjusted(1, 1, -1, -1)
            path = QPainterPath()
            path.addRoundedRect(rect, 16, 16)

            painter.fillPath(path, palette["background"])
            if self.fill_percent > 0.0:
                fill_height = rect.height() * (self.fill_percent / 100.0)
                fill_rect = rect.adjusted(0, int(rect.height() - fill_height), 0, 0)
                painter.save()
                painter.setClipPath(path)
                painter.fillRect(fill_rect, palette["fill"])
                painter.restore()

            painter.setPen(QPen(palette["border"], 1.4))
            painter.drawPath(path)

            font = painter.font()
            font.setFamily("Bahnschrift")
            font.setBold(True)
            font.setPointSize(10)
            painter.setFont(font)
            painter.setPen(palette["text"])
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.symbol)

    class MainWindow(QMainWindow):
        def __init__(self, app_state: UIState) -> None:
            super().__init__()
            self.app_state = app_state
            self.snapshot: dict[str, Any] = {}
            self.staged_files: list[dict[str, Any]] = []
            self.active_project_id: str | None = None
            self.active_job_id: str | None = None
            self.preview_cache_dir = Path(workspace).resolve() / "preview_cache"
            self.preview_cache_dir.mkdir(parents=True, exist_ok=True)
            self.media_cache: dict[str, Any] = {}
            self.current_preview_pixmap: QPixmap | None = None
            self.current_preview_key: str | None = None
            self.current_role_review_signature: str | None = None
            self.staged_existing_run: dict[str, Any] | None = None
            self.staged_existing_signature: tuple[str, ...] = ()
            self._shutdown_started = False

            self.setWindowTitle("VAZer")
            self.resize(1280, 820)
            self.setMinimumSize(1000, 680)
            self.setAcceptDrops(True)

            root = QWidget()
            root.setObjectName("rootWidget")
            self.setCentralWidget(root)
            layout = QVBoxLayout(root)
            layout.setContentsMargins(18, 18, 18, 18)
            layout.setSpacing(14)

            header = QFrame()
            header.setObjectName("header")
            header_layout = QVBoxLayout(header)
            header_layout.setContentsMargins(20, 18, 20, 18)
            header_layout.setSpacing(6)
            eyebrow = QLabel("THEATER VAZ")
            eyebrow.setObjectName("eyebrow")
            title = QLabel("Dateien reinziehen. Datei ansehen. Dann VAZ.")
            title.setObjectName("title")
            subtitle = QLabel(
                "Drag-and-drop funktioniert ueber das ganze Fenster. "
                "Jeder Import sammelt Dateien fuer genau einen Lauf."
            )
            subtitle.setObjectName("subtitle")
            subtitle.setWordWrap(True)
            header_layout.addWidget(eyebrow)
            header_layout.addWidget(title)
            header_layout.addWidget(subtitle)
            layout.addWidget(header)

            phase_strip = QFrame()
            phase_strip.setObjectName("phaseStrip")
            phase_layout = QHBoxLayout(phase_strip)
            phase_layout.setContentsMargins(0, 0, 0, 0)
            phase_layout.setSpacing(8)
            self.phase_widgets: list[dict[str, Any]] = []
            self.phase_connectors: list[QLabel] = []
            for index, phase in enumerate(PIPELINE_PHASES):
                if index > 0:
                    connector = QLabel("->")
                    connector.setObjectName("phaseConnector")
                    connector.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    phase_layout.addWidget(connector)
                    self.phase_connectors.append(connector)

                node = QFrame()
                node.setObjectName("phaseNode")
                node_layout = QHBoxLayout(node)
                node_layout.setContentsMargins(12, 10, 12, 10)
                node_layout.setSpacing(10)
                badge = PhaseBadge(phase["symbol"])
                badge.setObjectName("phaseBadge")
                text_column = QVBoxLayout()
                text_column.setContentsMargins(0, 0, 0, 0)
                text_column.setSpacing(2)
                name = QLabel(phase["label"])
                name.setObjectName("phaseName")
                detail = QLabel(phase["detail"])
                detail.setObjectName("phaseDetail")
                text_column.addWidget(name)
                text_column.addWidget(detail)
                node_layout.addWidget(badge, 0)
                node_layout.addLayout(text_column, 1)
                phase_layout.addWidget(node, 1)
                self.phase_widgets.append(
                    {
                        "id": phase["id"],
                        "node": node,
                        "badge": badge,
                        "name": name,
                        "detail": detail,
                        "default_detail": phase["detail"],
                    }
                )
            layout.addWidget(phase_strip)

            hint = QLabel("Droppe Dateien oder einen ganzen Ordner irgendwo in dieses Fenster.")
            hint.setObjectName("hint")
            layout.addWidget(hint)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            layout.addWidget(splitter, 1)

            list_panel = QFrame()
            list_panel.setObjectName("panel")
            list_layout = QVBoxLayout(list_panel)
            list_layout.setContentsMargins(16, 16, 16, 16)
            list_layout.setSpacing(10)
            list_title = QLabel("Dateien")
            list_title.setObjectName("panelTitle")
            list_layout.addWidget(list_title)
            self.file_list = QListWidget()
            self.file_list.currentItemChanged.connect(self.on_file_selection_changed)
            list_layout.addWidget(self.file_list, 1)
            self.file_meta = QLabel("Noch keine Dateien geladen.")
            self.file_meta.setObjectName("meta")
            self.file_meta.setWordWrap(True)
            list_layout.addWidget(self.file_meta)
            splitter.addWidget(list_panel)

            preview_panel = QFrame()
            preview_panel.setObjectName("panel")
            preview_layout = QVBoxLayout(preview_panel)
            preview_layout.setContentsMargins(16, 16, 16, 16)
            preview_layout.setSpacing(10)
            preview_title = QLabel("Preview")
            preview_title.setObjectName("panelTitle")
            preview_layout.addWidget(preview_title)
            self.review_widget = QFrame()
            self.review_widget.setObjectName("reviewStrip")
            review_layout = QVBoxLayout(self.review_widget)
            review_layout.setContentsMargins(0, 0, 0, 0)
            review_layout.setSpacing(10)
            self.review_cards: list[tuple[ScaledImageLabel, QLabel]] = []
            for _ in range(3):
                card = QFrame()
                card.setObjectName("reviewCard")
                card.setMinimumHeight(210)
                card.setMaximumHeight(210)
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(10, 10, 10, 10)
                card_layout.setSpacing(8)
                image_label = ScaledImageLabel("Warte auf Mittelframe ...", fallback_size=QSize(280, 150))
                image_label.setObjectName("reviewImage")
                image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                image_label.setMinimumHeight(150)
                image_label.setMaximumHeight(150)
                image_label.setWordWrap(True)
                caption_label = QLabel("")
                caption_label.setObjectName("reviewCaption")
                caption_label.setWordWrap(True)
                caption_label.setMinimumHeight(28)
                caption_label.setMaximumHeight(36)
                card_layout.addWidget(image_label, 1)
                card_layout.addWidget(caption_label)
                review_layout.addWidget(card)
                self.review_cards.append((image_label, caption_label))
            review_layout.addStretch(1)
            self.preview_frame = ScaledImageLabel("Kein File ausgewaehlt.", fallback_size=QSize(960, 540))
            self.preview_frame.setObjectName("preview")
            self.preview_frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.preview_frame.setMinimumHeight(320)
            self.preview_stack = QStackedWidget()
            self.preview_stack.setObjectName("previewStack")
            self.preview_stack.addWidget(self.preview_frame)
            self.preview_stack.addWidget(self.review_widget)
            preview_layout.addWidget(self.preview_stack, 1)
            self.preview_meta = QLabel("Waehle links eine Datei aus. Bei Video wird ein Frame aus der Mitte gezeigt.")
            self.preview_meta.setObjectName("meta")
            self.preview_meta.setWordWrap(True)
            preview_layout.addWidget(self.preview_meta)
            splitter.addWidget(preview_panel)
            splitter.setSizes([600, 600])

            footer = QFrame()
            footer.setObjectName("footer")
            footer_layout = QHBoxLayout(footer)
            footer_layout.setContentsMargins(16, 14, 16, 14)
            footer_layout.setSpacing(14)
            status_column = QVBoxLayout()
            status_column.setSpacing(8)
            self.status_label = QLabel("Bereit. Droppe Master und Kameras oder einen ganzen Ordner.")
            self.status_label.setObjectName("status")
            self.status_label.setWordWrap(True)
            self.progress_bar = QProgressBar()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.progress_bar.hide()
            status_column.addWidget(self.status_label)
            status_column.addWidget(self.progress_bar)
            footer_layout.addLayout(status_column, 1)
            output_mode_column = QVBoxLayout()
            output_mode_column.setSpacing(4)
            output_mode_label = QLabel("Output")
            output_mode_label.setObjectName("modeLabel")
            self.output_mode_combo = QComboBox()
            self.output_mode_combo.setObjectName("modeCombo")
            self.output_mode_combo.addItem("MP4 + Premiere XML", OUTPUT_MODE_RENDER_AND_PREMIERE)
            self.output_mode_combo.addItem("Nur Premiere XML", OUTPUT_MODE_PREMIERE_ONLY)
            output_mode_column.addWidget(output_mode_label)
            output_mode_column.addWidget(self.output_mode_combo)
            self.redo_all_checkbox = QCheckBox("Redo all")
            self.redo_all_checkbox.setObjectName("redoAllCheckbox")
            self.redo_all_checkbox.toggled.connect(lambda _checked: self.refresh_state())
            output_mode_column.addWidget(self.redo_all_checkbox)
            footer_layout.addLayout(output_mode_column)
            self.abort_button = QPushButton("Abbrechen")
            self.abort_button.setObjectName("secondaryButton")
            self.abort_button.clicked.connect(self.cancel_active_job)
            self.abort_button.hide()
            footer_layout.addWidget(self.abort_button)
            self.pause_button = QPushButton("Pause")
            self.pause_button.setObjectName("secondaryButton")
            self.pause_button.clicked.connect(self.pause_or_resume_active_job)
            self.pause_button.hide()
            footer_layout.addWidget(self.pause_button)
            self.continue_button = QPushButton("Weiter")
            self.continue_button.setObjectName("secondaryButton")
            self.continue_button.clicked.connect(self.confirm_role_review)
            self.continue_button.hide()
            footer_layout.addWidget(self.continue_button)
            self.start_button = QPushButton("VAZ")
            self.start_button.setObjectName("vazButton")
            self.start_button.clicked.connect(self.start_vaz)
            footer_layout.addWidget(self.start_button)
            layout.addWidget(footer)

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
                  color: #f5efe4;
                  font-family: "Aptos", "Segoe UI Variable", "Segoe UI", sans-serif;
                  font-size: 14px;
                }
                QMainWindow, QWidget#rootWidget {
                  background: #101319;
                }
                QLabel {
                  background: transparent;
                }
                QFrame#header, QFrame#panel, QFrame#footer {
                  background: #171d25;
                  border: 1px solid rgba(255,255,255,0.08);
                  border-radius: 18px;
                }
                QFrame#phaseStrip {
                  background: transparent;
                }
                QFrame#phaseNode {
                  background: rgba(255,255,255,0.03);
                  border: 1px solid rgba(255,255,255,0.07);
                  border-radius: 18px;
                }
                QFrame#phaseNode[phaseState="pending"] {
                  background: rgba(255,255,255,0.025);
                  border-color: rgba(255,255,255,0.06);
                }
                QFrame#phaseNode[phaseState="active"] {
                  background: rgba(239,157,77,0.10);
                  border-color: rgba(239,157,77,0.50);
                }
                QFrame#phaseNode[phaseState="review"] {
                  background: rgba(112,171,255,0.10);
                  border-color: rgba(112,171,255,0.50);
                }
                QFrame#phaseNode[phaseState="done"] {
                  background: rgba(99,193,120,0.10);
                  border-color: rgba(99,193,120,0.45);
                }
                QFrame#phaseNode[phaseState="error"] {
                  background: rgba(226,96,96,0.10);
                  border-color: rgba(226,96,96,0.45);
                }
                QLabel#eyebrow {
                  color: #ef9d4d;
                  font-size: 11px;
                  font-weight: 800;
                  letter-spacing: 2px;
                }
                QLabel#title {
                  font-family: "Bahnschrift", "Trebuchet MS", sans-serif;
                  font-size: 32px;
                  font-weight: 700;
                }
                QLabel#subtitle, QLabel#hint, QLabel#meta, QLabel#status {
                  color: #b9b2a3;
                  line-height: 1.45;
                }
                QLabel#modeLabel {
                  color: #b9b2a3;
                  font-size: 11px;
                  font-weight: 700;
                  letter-spacing: 1px;
                }
                QCheckBox#redoAllCheckbox {
                  color: #b9b2a3;
                  font-size: 12px;
                  font-weight: 700;
                  spacing: 8px;
                  padding-top: 2px;
                }
                QCheckBox#redoAllCheckbox::indicator {
                  width: 14px;
                  height: 14px;
                  border-radius: 4px;
                  border: 1px solid rgba(255,255,255,0.18);
                  background: rgba(255,255,255,0.04);
                }
                QCheckBox#redoAllCheckbox::indicator:checked {
                  border-color: rgba(239,157,77,0.8);
                  background: #ef9d4d;
                }
                QLabel#panelTitle {
                  font-family: "Bahnschrift", "Trebuchet MS", sans-serif;
                  font-size: 22px;
                  font-weight: 700;
                }
                QLabel#phaseName {
                  font-family: "Bahnschrift", "Trebuchet MS", sans-serif;
                  font-size: 15px;
                  font-weight: 700;
                }
                QLabel#phaseDetail {
                  color: #b9b2a3;
                  font-size: 11px;
                }
                QLabel#phaseConnector {
                  color: rgba(255,255,255,0.18);
                  font-family: "Bahnschrift", "Trebuchet MS", sans-serif;
                  font-size: 19px;
                  font-weight: 700;
                  min-width: 18px;
                }
                QLabel#phaseConnector[phaseState="active"] {
                  color: #ef9d4d;
                }
                QLabel#phaseConnector[phaseState="review"] {
                  color: #70abff;
                }
                QLabel#phaseConnector[phaseState="done"] {
                  color: #63c178;
                }
                QLabel#phaseConnector[phaseState="error"] {
                  color: #e26060;
                }
                QListWidget, QLabel#preview, QLabel#reviewImage {
                  background: #11161d;
                  border: 1px solid rgba(255,255,255,0.08);
                  border-radius: 16px;
                }
                QListWidget {
                  padding: 8px;
                }
                QListWidget::item {
                  padding: 12px 10px;
                  border-bottom: 1px solid rgba(255,255,255,0.04);
                }
                QListWidget::item:selected {
                  background: #202734;
                  border-radius: 10px;
                }
                QLabel#fileRowName {
                  font-weight: 700;
                  color: #f5efe4;
                }
                QLabel#fileRowDetail {
                  color: #b9b2a3;
                  line-height: 1.35;
                }
                QLabel#preview {
                  padding: 12px;
                }
                QFrame#reviewCard {
                  background: rgba(255,255,255,0.03);
                  border: 1px solid rgba(255,255,255,0.08);
                  border-radius: 16px;
                }
                QLabel#reviewImage {
                  padding: 10px;
                }
                QLabel#reviewCaption {
                  color: #b9b2a3;
                  line-height: 1.4;
                }
                QProgressBar {
                  border: 1px solid rgba(255,255,255,0.08);
                  background: #11161d;
                  border-radius: 999px;
                  min-height: 18px;
                  text-align: center;
                }
                QProgressBar::chunk {
                  border-radius: 999px;
                  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ef9d4d, stop:1 #ef6b3c);
                }
                QComboBox#modeCombo {
                  min-width: 228px;
                  min-height: 42px;
                  padding: 8px 14px;
                  border-radius: 21px;
                  border: 1px solid rgba(255,255,255,0.10);
                  background: rgba(255,255,255,0.05);
                  color: #f5efe4;
                  font-weight: 700;
                }
                QComboBox#modeCombo:disabled {
                  color: rgba(245,239,228,0.45);
                  background: rgba(255,255,255,0.03);
                }
                QComboBox#modeCombo::drop-down {
                  border: 0;
                  width: 28px;
                }
                QComboBox#modeCombo QAbstractItemView {
                  background: #171d25;
                  color: #f5efe4;
                  border: 1px solid rgba(255,255,255,0.10);
                  selection-background-color: #202734;
                }
                QPushButton#vazButton {
                  min-width: 152px;
                  min-height: 52px;
                  border: 0;
                  border-radius: 26px;
                  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ef9d4d, stop:1 #ef6b3c);
                  color: #17110d;
                  font-family: "Bahnschrift", "Trebuchet MS", sans-serif;
                  font-size: 22px;
                  font-weight: 800;
                  letter-spacing: 1px;
                }
                QPushButton#vazButton:disabled {
                  background: rgba(255,255,255,0.08);
                  color: rgba(245,239,228,0.45);
                }
                QPushButton#secondaryButton {
                  min-width: 132px;
                  min-height: 46px;
                  border-radius: 23px;
                  border: 1px solid rgba(255,255,255,0.10);
                  background: rgba(255,255,255,0.06);
                  color: #f5efe4;
                  font-weight: 700;
                }
                QPushButton#secondaryButton:disabled {
                  color: rgba(245,239,228,0.45);
                }
                """
            )

        def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
            if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
                event.acceptProposedAction()
            else:
                event.ignore()

        def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
            paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            if paths:
                self.import_paths(paths)
                event.acceptProposedAction()
            else:
                event.ignore()

        def resizeEvent(self, event) -> None:  # type: ignore[override]
            super().resizeEvent(event)

        def closeEvent(self, event) -> None:  # noqa: N802
            active_job = self._find_active_job()
            active_status = None if active_job is None else str(active_job.get("status") or "")
            is_busy = active_status in {
                "queued",
                "running",
                "pause_requested",
                "review_required",
            }
            if is_busy:
                response = QMessageBox.question(
                    self,
                    "VAZer schliessen",
                    "Ein VAZ-Lauf ist noch aktiv. Wirklich schliessen und laufende Prozesse abbrechen?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if response != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return

            force_process_exit = is_busy
            self._shutdown_app_state(force_process_exit=force_process_exit)
            event.accept()
            if force_process_exit:
                os._exit(0)

        def import_paths(self, paths: list[str]) -> None:
            if self._active_job_is_busy():
                QMessageBox.information(
                    self,
                    "VAZer",
                    "Ein Lauf ist bereits aktiv. Warte kurz, bis er fertig ist, bevor du neue Dateien hinzufügst.",
                )
                return

            if self.active_job_id:
                self.active_job_id = None
                self.active_project_id = None

            expanded = self._expand_paths(paths)
            if not expanded:
                self.status_label.setText("Keine gueltigen Dateien gefunden.")
                return

            known = {file_info["stored_path"] for file_info in self.staged_files}
            added = 0
            for candidate in expanded:
                if candidate in known:
                    continue
                known.add(candidate)
                self.staged_files.append(
                    {
                        "display_name": Path(candidate).name,
                        "original_path": candidate,
                        "stored_path": candidate,
                        "ui_status": "queued",
                        "ui_note": "Ready to start.",
                    }
                )
                added += 1

            if not added:
                self.status_label.setText("Diese Dateien sind bereits in der Liste.")
            else:
                existing_run = self._refresh_staged_existing_run(force=True)
                if existing_run is not None:
                    summary = str(existing_run.get("summary") or "Vorhandene VAZer-Daten erkannt.")
                    suffix = " Redo all ignoriert vorhandene Daten." if self.redo_all_checkbox.isChecked() else ""
                    self.status_label.setText(
                        f"{added} Datei(en) hinzugefuegt. {summary}.{suffix} Wenn alles passt, drueck VAZ."
                    )
                else:
                    self.status_label.setText(f"{added} Datei(en) hinzugefuegt. Wenn alles passt, drueck VAZ.")
            self.refresh_file_list(preserve_selection=True)

        def start_vaz(self) -> None:
            if self._active_job_is_busy():
                return

            if not self.staged_files:
                QMessageBox.information(self, "VAZer", "Es sind noch keine Dateien in der Liste.")
                return

            paths = [file_info["stored_path"] for file_info in self.staged_files]
            project_name = self._suggest_project_name(paths)
            output_mode = str(self.output_mode_combo.currentData() or OUTPUT_MODE_RENDER_AND_PREMIERE)
            reset_existing = self.redo_all_checkbox.isChecked()
            try:
                existing_run = self._refresh_staged_existing_run(force=True)
            except Exception as error:
                QMessageBox.critical(self, "VAZer", str(error))
                return
            if existing_run is not None and not reset_existing:
                decision = self._ask_existing_run_decision(existing_run)
                if decision == "cancel":
                    self.status_label.setText("VAZ-Start abgebrochen.")
                    return
                reset_existing = decision == "reset"
            try:
                result = self.app_state.create_project_from_paths(
                    paths,
                    name=project_name,
                    output_mode=output_mode,
                    reset_existing=reset_existing,
                )
            except Exception as error:
                QMessageBox.critical(self, "VAZer", str(error))
                return

            self.active_project_id = result["project_id"]
            self.active_job_id = result["job_id"]
            self.staged_files = []
            self.staged_existing_run = None
            self.staged_existing_signature = ()
            self.output_mode_combo.setEnabled(False)
            self.status_label.setText(f"VAZ gestartet: {project_name} | {self._output_mode_label(output_mode)}")
            self.refresh_state()

        def _ask_existing_run_decision(self, existing_run: dict[str, Any]) -> str:
            artifact_flags = existing_run.get("artifact_flags") or {}
            lines = [
                "Im Medienordner wurden bereits VAZer-Daten gefunden.",
                str(existing_run.get("project_dir") or "-"),
                "",
                f"Zustand: {existing_run.get('summary') or '-'}",
            ]
            found = []
            if artifact_flags.get("camera_roles"):
                found.append("Rollen")
            if artifact_flags.get("sync_partial") or artifact_flags.get("sync_complete"):
                found.append("Sync")
            if artifact_flags.get("transcript"):
                found.append("Transcript")
            if artifact_flags.get("analysis"):
                found.append("Analyse")
            if artifact_flags.get("planning") or artifact_flags.get("repair"):
                found.append("Schnittdaten")
            if artifact_flags.get("premiere_xml") or artifact_flags.get("premiere_project") or artifact_flags.get("premiere"):
                found.append("Premiere XML")
            if artifact_flags.get("render"):
                found.append("MP4")
            if found:
                lines.append(f"Gefunden: {', '.join(found)}")
            if existing_run.get("legacy_count"):
                lines.append(f"Zusätzlich alte Root-Artefakte: {existing_run['legacy_count']}")
            lines.append("")
            lines.append("Fortsetzen verwendet vorhandene Daten. Neu beginnen leert den VAZer-Ordner und startet frisch.")

            message_box = QMessageBox(self)
            message_box.setIcon(QMessageBox.Icon.Question)
            message_box.setWindowTitle("VAZer")
            message_box.setText("\n".join(lines))
            continue_button = message_box.addButton("Fortsetzen", QMessageBox.ButtonRole.AcceptRole)
            reset_button = message_box.addButton("Neu beginnen", QMessageBox.ButtonRole.DestructiveRole)
            cancel_button = message_box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
            message_box.setDefaultButton(continue_button)
            message_box.exec()
            clicked = message_box.clickedButton()
            if clicked == continue_button:
                return "continue"
            if clicked == reset_button:
                return "reset"
            return "cancel"

        def confirm_role_review(self) -> None:
            if not self.active_job_id:
                return
            try:
                self.app_state.confirm_job(self.active_job_id)
            except Exception as error:
                QMessageBox.critical(self, "VAZer", str(error))
                return
            self.refresh_state()

        def cancel_active_job(self) -> None:
            if not self.active_job_id:
                return
            try:
                self.app_state.cancel_job(self.active_job_id)
            except Exception as error:
                QMessageBox.critical(self, "VAZer", str(error))
                return
            self.refresh_state()

        def pause_or_resume_active_job(self) -> None:
            if not self.active_job_id:
                return
            active_job = self._find_active_job()
            if active_job is None:
                return
            status = str(active_job.get("status") or "")
            try:
                if status in {"queued", "running", "pause_requested"}:
                    self.app_state.pause_job(self.active_job_id)
                elif status == "paused":
                    result = self.app_state.resume_job(self.active_job_id)
                    new_job_id = str(result.get("job_id") or self.active_job_id)
                    new_project_id = str(result.get("project_id") or self.active_project_id or "")
                    self.active_job_id = new_job_id
                    if new_project_id:
                        self.active_project_id = new_project_id
            except Exception as error:
                QMessageBox.critical(self, "VAZer", str(error))
                return
            self.refresh_state()

        def refresh_state(self) -> None:
            self.snapshot = self.app_state.snapshot()
            self._restore_active_context()
            active_project = self._find_active_project()
            active_job = self._find_active_job()
            role_review_payload = self._build_role_review_payload(active_project, active_job)
            self.output_mode_combo.setEnabled(not self._active_job_is_busy())
            self.redo_all_checkbox.setEnabled(not self._active_job_is_busy())
            staged_existing_run = None if active_job is not None else self._refresh_staged_existing_run()
            self._update_phase_strip(
                active_job,
                existing_run=staged_existing_run,
                has_staged_files=bool(self.staged_files),
            )

            if active_project is None and active_job is None and not self.staged_files:
                self.staged_existing_run = None
                self.staged_existing_signature = ()
                self.file_meta.setText("Droppe Dateien oder einen Ordner. Die Liste sammelt alles fuer genau einen Lauf.")
                self.progress_bar.setValue(0)
                self.start_button.setEnabled(False)
                self.start_button.show()
                self.continue_button.hide()
                self.abort_button.hide()
                self.pause_button.hide()
                self.refresh_file_list()
                self._show_selected_file_preview()
                return

            if active_project is not None:
                self.refresh_file_list(files=active_project.get("files") or [], preserve_selection=True)
                classification = active_project.get("classification") or {}
                file_count = len(active_project.get("files") or [])
                output_mode_label = self._output_mode_label(active_project.get("output_mode"))
                if isinstance(classification, dict) and classification.get("warnings"):
                    warning_text = " | ".join(str(item) for item in classification["warnings"])
                    self.file_meta.setText(f"{file_count} Datei(en) im Lauf. Output: {output_mode_label}. {warning_text}")
                else:
                    self.file_meta.setText(f"{file_count} Datei(en) im aktuellen Lauf. Output: {output_mode_label}.")
            else:
                self.refresh_file_list(preserve_selection=True)
                meta_text = (
                    f"{len(self.staged_files)} Datei(en) vorgemerkt fuer den naechsten Lauf. "
                    f"Output: {self._output_mode_label(self.output_mode_combo.currentData())}."
                )
                staged_summary = self._staged_existing_summary(staged_existing_run)
                if staged_summary:
                    meta_text = f"{meta_text} {staged_summary}"
                self.file_meta.setText(meta_text)

            if active_job is None:
                self.progress_bar.setValue(0)
                self.start_button.setEnabled(bool(self.staged_files))
                self.start_button.show()
                self.continue_button.hide()
                self.abort_button.hide()
                self.pause_button.hide()
                staged_summary = self._staged_existing_summary(staged_existing_run)
                if staged_summary:
                    self.status_label.setText(staged_summary)
                self._show_selected_file_preview()
                return

            self.progress_bar.setValue(int(round(float(active_job.get("progress_percent") or 0.0))))
            stage_label = str(active_job.get("stage_label") or "-")
            analysis_suffix = self._analysis_status_suffix(active_job)
            if analysis_suffix:
                stage_label = f"{stage_label} | {analysis_suffix}"
            self.status_label.setText(
                f"{stage_label} | {active_job.get('message') or '-'}"
            )
            review_required = active_job.get("status") == "review_required"
            paused = active_job.get("status") == "paused"
            pause_capable = active_job.get("status") in {"queued", "running", "pause_requested", "paused"}
            self.start_button.setEnabled(False)
            self.start_button.setVisible(not review_required)
            self.continue_button.setVisible(review_required)
            self.abort_button.setVisible(review_required)
            self.continue_button.setEnabled(review_required)
            self.abort_button.setEnabled(review_required)
            self.pause_button.setVisible(pause_capable and not review_required)
            self.pause_button.setEnabled(pause_capable)
            self.pause_button.setText("Weiter" if paused else "Pause")
            if active_job.get("status") not in {"queued", "running", "pause_requested", "paused", "review_required"}:
                self.start_button.setEnabled(bool(self.staged_files))
                self.pause_button.hide()
            if role_review_payload is not None:
                self._show_role_review(role_review_payload)
            else:
                self._show_selected_file_preview()

        def refresh_file_list(self, *, files: list[dict[str, Any]] | None = None, preserve_selection: bool = False) -> None:
            current_path = self._current_list_path() if preserve_selection else None
            scroll_value = self.file_list.verticalScrollBar().value() if preserve_selection else None
            source_files = files if files is not None else self.staged_files
            self.file_list.blockSignals(True)
            self.file_list.clear()
            selected_row = -1
            for index, file_info in enumerate(source_files):
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, file_info["stored_path"])
                item.setToolTip(str(file_info.get("ui_note") or ""))
                self.file_list.addItem(item)
                row_widget = FileRowWidget(file_info)
                item.setSizeHint(row_widget.sizeHint())
                self.file_list.setItemWidget(item, row_widget)
                if current_path and file_info["stored_path"] == current_path:
                    selected_row = index
            self.file_list.blockSignals(False)
            if self.file_list.count():
                self.file_list.setCurrentRow(selected_row if selected_row >= 0 else 0)
                if scroll_value is not None:
                    self.file_list.verticalScrollBar().setValue(scroll_value)
            else:
                self.on_file_selection_changed()

        def _file_item_text(self, file_info: dict[str, Any]) -> str:
            status = str(file_info.get("ui_status") or "queued")
            note = str(file_info.get("ui_note") or "")
            display_name = str(file_info.get("display_name") or Path(file_info["stored_path"]).name)
            return f"{display_name}\n{status}{' | ' + note if note else ''}"

        def on_file_selection_changed(self) -> None:
            if self._build_role_review_payload(self._find_active_project(), self._find_active_job()) is not None:
                return
            self._show_selected_file_preview()

        def _show_selected_file_preview(self) -> None:
            self.current_role_review_signature = None
            self.preview_stack.setCurrentWidget(self.preview_frame)
            file_info = self._selected_file_info()
            if file_info is None:
                self.preview_frame.setText("Kein File ausgewaehlt.")
                self.preview_frame.set_source_pixmap(None)
                self.preview_meta.setText("Waehle links eine Datei aus.")
                self.current_preview_pixmap = None
                self.current_preview_key = None
                return

            self.preview_meta.setText(self._build_preview_meta(file_info))
            preview_path = self._build_preview_image(file_info["stored_path"])
            if preview_path is None:
                self.preview_frame.set_source_pixmap(None)
                self.preview_frame.setText("Keine Video-Vorschau fuer diese Datei.")
                self.current_preview_pixmap = None
                self.current_preview_key = None
                return

            preview_key = str(preview_path)
            if self.current_preview_key == preview_key and self.current_preview_pixmap is not None:
                self.preview_frame.setText("")
                self.preview_frame.set_source_pixmap(self.current_preview_pixmap)
                return

            pixmap = QPixmap(preview_key)
            if pixmap.isNull():
                self.preview_frame.set_source_pixmap(None)
                self.preview_frame.setText("Preview konnte nicht geladen werden.")
                self.current_preview_pixmap = None
                self.current_preview_key = None
                return

            self.preview_frame.setText("")
            self.current_preview_pixmap = pixmap
            self.current_preview_key = preview_key
            self.preview_frame.set_source_pixmap(pixmap)

        def _show_role_review(self, payload: dict[str, Any]) -> None:
            signature = json.dumps(
                {
                    "summary_text": payload.get("summary_text"),
                    "source_text": payload.get("source_text"),
                    "cards": [
                        {
                            "frame_path": card.get("frame_path"),
                            "display_name": card.get("display_name"),
                            "role": card.get("role"),
                        }
                        for card in payload.get("cards") or []
                    ],
                },
                sort_keys=True,
            )
            if signature == self.current_role_review_signature:
                return

            self.current_role_review_signature = signature
            self.preview_stack.setCurrentWidget(self.review_widget)
            summary_text = payload.get("summary_text") or "Mittelframes fuer die Rollenpruefung."
            source_text = payload.get("source_text") or ""
            self.preview_meta.setText(f"{summary_text}\n{source_text}".strip())
            cards = payload.get("cards") or []
            for index, (image_label, caption_label) in enumerate(self.review_cards):
                if index >= len(cards):
                    image_label.set_source_pixmap(None)
                    image_label.setText("")
                    caption_label.setText("")
                    continue
                card = cards[index]
                image_path = card.get("frame_path")
                if image_path and Path(image_path).exists():
                    pixmap = QPixmap(str(image_path))
                    if pixmap.isNull():
                        image_label.set_source_pixmap(None)
                        image_label.setText("Frame konnte nicht geladen werden.")
                    else:
                        image_label.setText("")
                        image_label.set_source_pixmap(pixmap)
                else:
                    image_label.set_source_pixmap(None)
                    image_label.setText("Warte auf Mittelframe ...")

                role = card.get("role")
                display_name = str(card.get("display_name") or card.get("asset_id") or "Kamera")
                role_color = {
                    "totale": "#63c178",
                    "halbtotale": "#70abff",
                    "close": "#ef9d4d",
                }.get(str(role or "").lower(), "#f5efe4")
                if role:
                    caption_label.setText(
                        f"{display_name}<br>"
                        f"<span style=\"color:{role_color}; font-weight:700;\">{role}</span>"
                    )
                else:
                    caption_label.setText(display_name)

        def _build_preview_meta(self, file_info: dict[str, Any]) -> str:
            media_info = self._probe_cached(file_info["stored_path"])
            lines = [
                f"Datei: {file_info.get('display_name') or Path(file_info['stored_path']).name}",
                f"Status: {file_info.get('ui_status') or 'queued'}",
            ]
            if file_info.get("ui_note"):
                lines.append(f"Info: {file_info['ui_note']}")
            if media_info.duration_seconds:
                lines.append(f"Dauer: {media_info.duration_seconds:.2f}s")
            lines.append(f"Audio streams: {len(media_info.audio_streams)}")
            lines.append(f"Video streams: {len(media_info.video_streams)}")
            if media_info.video_streams:
                stream = media_info.video_streams[0]
                lines.append(f"Bild: {stream.width}x{stream.height}")
            return "\n".join(lines)

        def _probe_cached(self, path: str):
            cached = self.media_cache.get(path)
            if cached is not None:
                return cached
            media_info = probe_media(path)
            self.media_cache[path] = media_info
            return media_info

        def _build_preview_image(self, path: str) -> Path | None:
            media_info = self._probe_cached(path)
            if not media_info.video_streams:
                return None
            duration_seconds = float(media_info.duration_seconds or media_info.video_streams[0].duration_seconds or 0.0)
            if duration_seconds <= 0:
                return None

            file_path = Path(path)
            cache_key = sha1(f"{file_path.resolve()}::{file_path.stat().st_mtime_ns}".encode("utf-8")).hexdigest()
            preview_path = self.preview_cache_dir / f"{cache_key}.jpg"
            if preview_path.exists():
                return preview_path

            capture = cv2.VideoCapture(str(file_path))
            if not capture.isOpened():
                return None
            try:
                capture.set(cv2.CAP_PROP_POS_MSEC, (duration_seconds / 2.0) * 1000.0)
                ok, frame = capture.read()
                if not ok or frame is None:
                    return None
                frame_height, frame_width = frame.shape[:2]
                scale = min(
                    1.0,
                    PREVIEW_MAX_WIDTH / max(1, frame_width),
                    PREVIEW_MAX_HEIGHT / max(1, frame_height),
                )
                if scale < 1.0:
                    target_width = max(2, int(round(frame_width * scale)))
                    target_height = max(2, int(round(frame_height * scale)))
                    frame = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
                preview_path.parent.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(str(preview_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 88]):
                    return None
                return preview_path
            finally:
                capture.release()

        def _selected_file_info(self) -> dict[str, Any] | None:
            current_path = self._current_list_path()
            if not current_path:
                return None
            for file_info in self._current_files():
                if file_info["stored_path"] == current_path:
                    return file_info
            return None

        def _current_files(self) -> list[dict[str, Any]]:
            active_project = self._find_active_project()
            if active_project is not None:
                return list(active_project.get("files") or [])
            return self.staged_files

        def _current_list_path(self) -> str | None:
            item = self.file_list.currentItem()
            if item is None:
                return None
            value = item.data(Qt.ItemDataRole.UserRole)
            return None if value is None else str(value)

        def _active_job_is_busy(self) -> bool:
            active_job = self._find_active_job()
            if active_job is None:
                return False
            return active_job.get("status") in {"queued", "running", "pause_requested", "paused", "review_required"}

        def _analysis_stage_detail(self, active_job: dict[str, Any] | None) -> str | None:
            if active_job is None:
                return None

            analysis_hint = active_job.get("analysis_pass")
            if not isinstance(analysis_hint, str) or not analysis_hint.strip():
                analysis_progress = active_job.get("analysis_progress")
                if isinstance(analysis_progress, dict):
                    analysis_hint = (
                        analysis_progress.get("pass")
                        or analysis_progress.get("phase")
                        or analysis_progress.get("scope")
                    )

            if not isinstance(analysis_hint, str):
                return None

            normalized = analysis_hint.strip().lower()
            if normalized in {"global", "global_pass", "coarse", "scan"}:
                return "Global pass"
            if normalized in {"local", "local_pass", "dense", "refine", "refinement"}:
                return "Local pass"
            if normalized in {"global_local", "global+local", "both", "all"}:
                return "Global + local"
            return None

        def _analysis_status_suffix(self, active_job: dict[str, Any] | None) -> str | None:
            return self._analysis_stage_detail(active_job)

        def _output_mode_label(self, value: Any) -> str:
            if str(value or "") == OUTPUT_MODE_PREMIERE_ONLY:
                return "Nur Premiere XML"
            return "MP4 + Premiere XML"

        def _staged_paths_signature(self) -> tuple[str, ...]:
            return tuple(sorted(str(file_info.get("stored_path") or "") for file_info in self.staged_files))

        def _refresh_staged_existing_run(self, *, force: bool = False) -> dict[str, Any] | None:
            if not self.staged_files:
                self.staged_existing_run = None
                self.staged_existing_signature = ()
                return None

            signature = self._staged_paths_signature()
            if not force and signature == self.staged_existing_signature:
                return self.staged_existing_run

            self.staged_existing_signature = signature
            self.staged_existing_run = self.app_state.inspect_existing_source_run(list(signature))
            return self.staged_existing_run

        def _staged_existing_summary(self, existing_run: dict[str, Any] | None) -> str | None:
            if existing_run is None:
                return None
            artifact_flags = existing_run.get("artifact_flags") or {}
            found: list[str] = []
            if artifact_flags.get("camera_roles"):
                found.append("Rollen")
            if artifact_flags.get("sync_complete"):
                found.append("Sync")
            elif artifact_flags.get("sync_partial"):
                found.append("Sync teilweise")
            if artifact_flags.get("transcript"):
                found.append("Transcript")
            if artifact_flags.get("analysis"):
                found.append("Analyse")
            if artifact_flags.get("repair") or artifact_flags.get("render_cut"):
                found.append("Schnitt")
            elif artifact_flags.get("planning") or artifact_flags.get("validation"):
                found.append("Schnitt teilweise")
            if artifact_flags.get("render"):
                found.append("MP4")
            elif artifact_flags.get("premiere"):
                found.append("Premiere XML")

            parts = [str(existing_run.get("summary") or "").strip()]
            if found:
                parts.append("Gefunden: " + ", ".join(found))
            if self.redo_all_checkbox.isChecked():
                parts.append("Redo all aktiv.")
            return " | ".join(part for part in parts if part)

        def _update_phase_strip(
            self,
            active_job: dict[str, Any] | None,
            *,
            existing_run: dict[str, Any] | None = None,
            has_staged_files: bool = False,
        ) -> None:
            phase_states = self._build_phase_states(active_job, existing_run=existing_run, has_staged_files=has_staged_files)
            phase_fill_percents = self._build_phase_fill_percents(
                active_job,
                existing_run=existing_run,
                has_staged_files=has_staged_files,
            )
            for widget_info in self.phase_widgets:
                state = phase_states.get(widget_info["id"], "pending")
                self._set_phase_state(widget_info["node"], state)
                self._set_phase_state(widget_info["name"], state)
                self._set_phase_state(widget_info["detail"], state)
                widget_info["badge"].set_visual_state(state, phase_fill_percents.get(widget_info["id"], 0.0))
                if widget_info["id"] == "analysis":
                    widget_info["detail"].setText(
                        self._analysis_stage_detail(active_job) or widget_info["default_detail"]
                    )
                elif widget_info["detail"].text() != widget_info["default_detail"]:
                    widget_info["detail"].setText(widget_info["default_detail"])

            for index, connector in enumerate(self.phase_connectors, start=1):
                left_phase = PIPELINE_PHASES[index - 1]["id"]
                right_phase = PIPELINE_PHASES[index]["id"]
                left_state = phase_states.get(left_phase, "pending")
                right_state = phase_states.get(right_phase, "pending")
                connector_state = "pending"
                if "error" in {left_state, right_state}:
                    connector_state = "error"
                elif right_state == "done":
                    connector_state = "done"
                elif right_state == "review":
                    connector_state = "review"
                elif right_state == "active" or (left_state == "done" and right_state in {"pending", "active"}):
                    connector_state = "active" if right_state == "active" else "done"
                self._set_phase_state(connector, connector_state)

        def _build_phase_states(
            self,
            active_job: dict[str, Any] | None,
            *,
            existing_run: dict[str, Any] | None = None,
            has_staged_files: bool = False,
        ) -> dict[str, str]:
            states = {phase["id"]: "pending" for phase in PIPELINE_PHASES}
            if active_job is None:
                if has_staged_files:
                    states["probe"] = "done"
                if existing_run is not None:
                    artifact_flags = existing_run.get("artifact_flags") or {}
                    if has_staged_files or artifact_flags.get("project_state") or any(bool(value) for value in artifact_flags.values()):
                        states["probe"] = "done"
                        states["classify"] = "done"
                    if artifact_flags.get("camera_roles"):
                        states["roles"] = "done"
                    elif states["classify"] == "done":
                        states["roles"] = "pending"
                    if artifact_flags.get("sync_complete"):
                        states["sync"] = "done"
                    elif artifact_flags.get("sync_partial"):
                        states["sync"] = "active"
                    if artifact_flags.get("transcript"):
                        states["transcript"] = "done"
                    if artifact_flags.get("analysis"):
                        states["analysis"] = "done"
                    if artifact_flags.get("repair") or artifact_flags.get("render_cut"):
                        states["cut"] = "done"
                    elif artifact_flags.get("planning") or artifact_flags.get("validation"):
                        states["cut"] = "active"
                    output_mode = str(existing_run.get("output_mode") or "")
                    export_finished = artifact_flags.get("render") or (
                        output_mode == OUTPUT_MODE_PREMIERE_ONLY and artifact_flags.get("premiere")
                    )
                    if export_finished:
                        states["render"] = "done"
                    elif artifact_flags.get("premiere") or artifact_flags.get("render"):
                        states["render"] = "active"
                return states

            status = str(active_job.get("status") or "")
            stage = str(active_job.get("stage") or "")
            if status == "completed":
                return {phase["id"]: "done" for phase in PIPELINE_PHASES}

            if stage == "media_parallel":
                parallel_progress = active_job.get("parallel_progress") or {}
                for phase_id in ("probe", "classify", "roles"):
                    states[phase_id] = "done"
                sync_state = (parallel_progress.get("sync") or {}).get("status")
                transcript_state = (parallel_progress.get("transcript") or {}).get("status")
                analysis_state = (parallel_progress.get("analysis") or {}).get("status")
                states["sync"] = "done" if sync_state == "done" else "active"
                states["transcript"] = "done" if transcript_state == "done" else "active"
                if analysis_state == "pending":
                    states["analysis"] = "pending"
                else:
                    states["analysis"] = "done" if analysis_state == "done" else "active"
                return states

            phase_id = STAGE_TO_PHASE_ID.get(stage)
            if phase_id is None and status in {"failed", "canceled"}:
                phase_id = self._phase_from_progress(active_job)
            if phase_id is None:
                return states

            active_index = PHASE_INDEX.get(phase_id, 0)
            for index, phase in enumerate(PIPELINE_PHASES):
                if index < active_index:
                    states[phase["id"]] = "done"
                elif index == active_index:
                    if status in {"failed", "canceled"}:
                        states[phase["id"]] = "error"
                    elif status == "review_required":
                        states[phase["id"]] = "review"
                    else:
                        states[phase["id"]] = "active"
            return states

        def _build_phase_fill_percents(
            self,
            active_job: dict[str, Any] | None,
            *,
            existing_run: dict[str, Any] | None = None,
            has_staged_files: bool = False,
        ) -> dict[str, float]:
            fills = {phase["id"]: 0.0 for phase in PIPELINE_PHASES}
            if active_job is None:
                if has_staged_files:
                    fills["probe"] = 100.0
                if existing_run is not None:
                    artifact_flags = existing_run.get("artifact_flags") or {}
                    if has_staged_files or artifact_flags.get("project_state") or any(bool(value) for value in artifact_flags.values()):
                        fills["probe"] = 100.0
                        fills["classify"] = 100.0
                    if artifact_flags.get("camera_roles"):
                        fills["roles"] = 100.0
                    if artifact_flags.get("sync_complete"):
                        fills["sync"] = 100.0
                    elif artifact_flags.get("sync_partial"):
                        fills["sync"] = 55.0
                    if artifact_flags.get("transcript"):
                        fills["transcript"] = 100.0
                    if artifact_flags.get("analysis"):
                        fills["analysis"] = 100.0
                    if artifact_flags.get("render_cut") or artifact_flags.get("repair"):
                        fills["cut"] = 100.0
                    elif artifact_flags.get("validation"):
                        fills["cut"] = 80.0
                    elif artifact_flags.get("planning"):
                        fills["cut"] = 55.0
                    output_mode = str(existing_run.get("output_mode") or "")
                    if artifact_flags.get("render"):
                        fills["render"] = 100.0
                    elif artifact_flags.get("premiere"):
                        fills["render"] = 100.0 if output_mode == OUTPUT_MODE_PREMIERE_ONLY else 60.0
                return fills

            status = str(active_job.get("status") or "")
            if status == "completed":
                return {phase["id"]: 100.0 for phase in PIPELINE_PHASES}

            if str(active_job.get("stage") or "") == "media_parallel":
                parallel_progress = active_job.get("parallel_progress") or {}
                fills["probe"] = 100.0
                fills["classify"] = 100.0
                fills["roles"] = 100.0
                fills["sync"] = float((parallel_progress.get("sync") or {}).get("progress_percent") or 0.0)
                fills["transcript"] = float((parallel_progress.get("transcript") or {}).get("progress_percent") or 0.0)
                fills["analysis"] = float((parallel_progress.get("analysis") or {}).get("progress_percent") or 0.0)
                return fills

            progress_percent = max(0.0, min(100.0, float(active_job.get("progress_percent") or 0.0)))
            for phase_id, (start_percent, end_percent) in PHASE_PROGRESS_RANGES.items():
                if progress_percent <= start_percent:
                    fills[phase_id] = 0.0
                elif progress_percent >= end_percent:
                    fills[phase_id] = 100.0
                else:
                    fills[phase_id] = ((progress_percent - start_percent) / (end_percent - start_percent)) * 100.0

            if status == "review_required":
                phase_id = STAGE_TO_PHASE_ID.get(str(active_job.get("stage") or ""))
                if phase_id is not None:
                    fills[phase_id] = 100.0
            return fills

        def _restore_active_context(self) -> None:
            if self.active_job_id and self._find_active_job() is not None:
                return
            self.active_job_id = None
            self.active_project_id = None
            jobs = list(self.snapshot.get("jobs") or [])
            for job in jobs:
                if str(job.get("status") or "") != "paused":
                    continue
                project_id = str(job.get("project_id") or "")
                job_id = str(job.get("id") or "")
                if project_id and job_id:
                    self.active_project_id = project_id
                    self.active_job_id = job_id
                    return
            for job in jobs:
                status = str(job.get("status") or "")
                if status not in {"queued", "running", "pause_requested", "review_required"}:
                    continue
                project_id = str(job.get("project_id") or "")
                job_id = str(job.get("id") or "")
                if project_id and job_id:
                    self.active_project_id = project_id
                    self.active_job_id = job_id
                    return

        def _phase_from_progress(self, active_job: dict[str, Any]) -> str:
            progress_percent = float(active_job.get("progress_percent") or 0.0)
            for phase_id, (_start_percent, end_percent) in PHASE_PROGRESS_RANGES.items():
                if progress_percent <= end_percent:
                    return phase_id
            return "render"

        def _set_phase_state(self, widget: QWidget, state: str) -> None:
            if widget.property("phaseState") == state:
                return
            widget.setProperty("phaseState", state)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

        def _build_role_review_payload(
            self,
            active_project: dict[str, Any] | None,
            active_job: dict[str, Any] | None,
        ) -> dict[str, Any] | None:
            if active_project is None or active_job is None:
                return None
            if active_job.get("stage") not in {"roles", "role_review"} and active_job.get("status") != "review_required":
                return None

            classification = active_project.get("classification") or {}
            camera_paths = list(classification.get("camera_paths") or [])
            asset_ids = list(classification.get("camera_asset_ids") or [])
            if not camera_paths or not asset_ids:
                return None

            artifacts = active_project.get("artifacts") or {}
            artifacts_root = Path(active_project["artifacts_path"])
            frame_root = artifacts_root / "vazer.camera_roles" / "frames"
            assignments_by_asset: dict[str, dict[str, Any]] = {}
            summary_text = None
            source_text = None
            camera_roles_path = artifacts.get("camera_roles_path")
            if camera_roles_path and Path(camera_roles_path).exists():
                try:
                    artifact = json.loads(Path(camera_roles_path).read_text(encoding="utf-8-sig"))
                except Exception:
                    artifact = None
                if isinstance(artifact, dict):
                    summary = artifact.get("summary") or {}
                    provider = artifact.get("provider") or {}
                    summary_text = summary.get("summary_text")
                    source_name = provider.get("name")
                    source_model = provider.get("model")
                    if source_name == "openai":
                        source_text = f"AI-Rollen von {source_model or 'OpenAI'}"
                    elif source_name:
                        source_text = f"Rollenquelle: {source_name}"
                    for assignment in artifact.get("assignments", []):
                        if isinstance(assignment, dict) and isinstance(assignment.get("asset_id"), str):
                            assignments_by_asset[assignment["asset_id"]] = assignment

            cards: list[dict[str, Any]] = []
            for index, (asset_id, camera_path) in enumerate(zip(asset_ids, camera_paths, strict=True), start=1):
                assignment = assignments_by_asset.get(asset_id, {})
                frame_path = frame_root / f"{index:02d}-{_slugify(asset_id)}.jpg"
                cards.append(
                    {
                        "asset_id": asset_id,
                        "display_name": Path(camera_path).name,
                        "frame_path": str(frame_path) if frame_path.exists() else None,
                        "role": assignment.get("role"),
                        "confidence": assignment.get("confidence"),
                        "reason": assignment.get("reason"),
                    }
                )
            if not any(card.get("frame_path") for card in cards):
                return None
            return {
                "summary_text": summary_text,
                "source_text": source_text,
                "cards": cards[: len(self.review_cards)],
            }

        def _find_active_project(self) -> dict[str, Any] | None:
            if not self.active_project_id:
                return None
            for project in self.snapshot.get("projects", []):
                if project.get("id") == self.active_project_id:
                    return project
            return None

        def _find_active_job(self) -> dict[str, Any] | None:
            if not self.active_job_id:
                return None
            for job in self.snapshot.get("jobs", []):
                if job.get("id") == self.active_job_id:
                    return job
            return None

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
                    if should_ignore_import_file(candidate):
                        continue
                    resolved = str(candidate.resolve())
                    if resolved not in seen:
                        seen.add(resolved)
                        collected.append(resolved)
            return collected

        def _suggest_project_name(self, expanded_paths: list[str]) -> str:
            resolved = [Path(path) for path in expanded_paths]
            if len(resolved) == 1:
                return resolved[0].stem or "VAZ Projekt"
            parents = {path.parent for path in resolved}
            if len(parents) == 1:
                parent = next(iter(parents))
                return parent.name or "VAZ Projekt"
            return f"VAZ Import ({len(resolved)} Dateien)"

        def _shutdown_app_state(self, *, force_process_exit: bool = False) -> None:
            if self._shutdown_started:
                return
            self._shutdown_started = True
            try:
                self.app_state.shutdown(preserve_paused=not force_process_exit)
            except Exception:
                pass
            if force_process_exit:
                try:
                    self.timer.stop()
                except Exception:
                    pass

    app = QApplication.instance() or QApplication([])
    window = MainWindow(UIState(Path(workspace)))
    app.aboutToQuit.connect(window._shutdown_app_state)
    window.show()
    window.raise_()
    window.activateWindow()
    if auto_quit_ms is not None:
        QTimer.singleShot(auto_quit_ms, app.quit)
    return app.exec()
