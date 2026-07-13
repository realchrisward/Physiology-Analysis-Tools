# -*- coding: utf-8 -*-
"""
Filter / beat-caller comparison window.

Self-contained QDialog - it does not touch MainWindow state except through the
optional "send to main window" button, so the existing single-pipeline workflow
keeps working exactly as before.

Layout
------
  left   : pipeline table (enable, filter, caller, configure) + controls
  top    : signal trace (raw + the filtered trace of the highlighted pipeline)
  bottom : beat raster - one lane per pipeline, plus a manual annotation lane.
           Disagreements show up as gaps in a column.
  right  : metrics table scored against the chosen reference

written for Physiology Analysis Tools (C) 2024
"""

__version__ = "0.0.2"

import json

import numpy
import pandas
import pyqtgraph
from PySide6 import QtCore, QtWidgets

from . import batch, comparison
from .algorithms import sampling_frequency
from .beatcallers import BEATCALLERS
from .filters import FILTERS
from .normalizers import NORMALIZERS
from .pipeline import DEFAULT_NORMALIZER, Pipeline, next_color, run_pipelines

MANUAL = "__manual__"
CONSENSUS = "__consensus__"

# Analysis segment length used when the window first opens. Filtering, beat
# calling and plotting all operate on the segment, never on the whole file: a
# 3 h recording at 1 kHz is ~10.8 M samples, which takes minutes per pipeline
# and makes pyqtgraph unusable. Widen it deliberately once a grid looks right.
DEFAULT_SEGMENT_S = 60.0

# Above this many samples, confirm before analysing (roughly 10 min at 1 kHz).
LARGE_SEGMENT_SAMPLES = 600_000


# %% settings form (mirrors main.FlexibleEntryWidget, kept local to avoid a
#    circular import between main.py and the modules package)


class FlexibleEntry:
    def __init__(self, value):
        self.type = type(value)
        if self.type is bool:
            self.widget = QtWidgets.QCheckBox()
            self.widget.setChecked(value)
        else:
            self.widget = QtWidgets.QLineEdit()
            self.widget.setText("" if value is None else str(value))

    def value(self):
        if self.type is bool:
            return self.widget.isChecked()
        text = self.widget.text().strip()
        if text == "":
            return None
        if self.type is int:
            return int(float(text))
        try:
            return float(text)
        except ValueError:
            return text


class SettingsDialog(QtWidgets.QDialog):
    """Builds a form from a Settings object by introspecting its __dict__."""

    def __init__(self, settings, title, description="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.settings = settings
        self.entries = {}

        layout = QtWidgets.QVBoxLayout()
        if description:
            note = QtWidgets.QLabel(description)
            note.setWordWrap(True)
            note.setMaximumWidth(420)
            layout.addWidget(note)

        form = QtWidgets.QFormLayout()
        for k, v in settings.as_dict().items():
            entry = FlexibleEntry(v)
            self.entries[k] = entry
            form.addRow(k, entry.widget)
        layout.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.apply_and_close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def apply_and_close(self):
        self.settings.update(**{k: e.value() for k, e in self.entries.items()})
        self.accept()


# %% main comparison window


class ComparisonWindow(QtWidgets.QDialog):
    def __init__(self, data, time_column, voltage_column, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Filter / Beat-caller Comparison")
        self.resize(1400, 850)

        self.full_data = data.reset_index(drop=True)
        self.time_column = time_column
        self.voltage_column = voltage_column

        self.full_time = numpy.asarray(self.full_data[time_column], dtype=float)
        self.file_start = float(self.full_time[0])
        self.file_end = float(self.full_time[-1])
        self.fs = sampling_frequency(self.full_time)

        self.pipelines = []
        self.results = {}
        self.manual_ts = []
        self.annotate_mode = False
        self.review_windows = numpy.array([])
        self.review_index = 0
        self.beat_items = {}

        # The analysis segment. self.data / self.time / self.voltage always
        # refer to the segment, so every downstream consumer (run_pipelines,
        # the plots, annotation snapping) is confined to it automatically.
        self.ui_ready = False
        span = self.file_end - self.file_start
        self.segment_start = self.file_start
        self.segment_duration = (
            min(DEFAULT_SEGMENT_S, span) if span > 0 else DEFAULT_SEGMENT_S
        )
        self.apply_segment()

        self.build_ui()
        self.ui_ready = True
        self.sync_segment_widgets()
        self.add_pipeline()

    # -- analysis segment --------------------------------------------------
    def apply_segment(self):
        """Slice the recording down to [segment_start, +segment_duration]."""
        start = float(self.segment_start)
        stop = start + float(self.segment_duration)

        i0 = int(numpy.searchsorted(self.full_time, start, side="left"))
        i1 = int(numpy.searchsorted(self.full_time, stop, side="right"))
        i1 = min(max(i1, i0 + 2), self.full_time.size)  # filters need >1 sample
        i0 = min(i0, max(i1 - 2, 0))

        self.segment_slice = (i0, i1)
        self.data = self.full_data.iloc[i0:i1].reset_index(drop=True)
        self.time = self.full_time[i0:i1]
        self.voltage = numpy.asarray(self.data[self.voltage_column], dtype=float)

        # Results were computed against the previous slice: their signal arrays
        # are a different length than self.time, so they can be neither drawn
        # nor scored. Drop them rather than silently mis-aligning.
        self.results = {}
        self.review_windows = numpy.array([])
        self.review_index = 0

    def segment_bounds(self):
        return float(self.time[0]), float(self.time[-1])

    def segment_changed(self):
        if not self.ui_ready:
            return
        start = self.spin_seg_start.value()
        duration = self.spin_seg_len.value()
        samples = int(duration * self.fs)
        if samples > LARGE_SEGMENT_SAMPLES:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Large segment",
                f"{duration:.0f} s is about {samples:,} samples per pipeline.\n"
                "Running a grid over this will be slow and the plots will be "
                "sluggish.\n\nContinue?",
            )
            if answer != QtWidgets.QMessageBox.Yes:
                self.sync_segment_widgets()
                return

        self.segment_start = start
        self.segment_duration = duration
        self.apply_segment()
        self.sync_segment_widgets()
        self.rebuild_reference_combo()
        self.redraw_all()
        self.status.setText(
            "segment changed - previous results discarded, re-run the pipelines"
        )

    def use_current_view(self):
        """Shrink the analysis segment to whatever is on screen right now."""
        if not self.ui_ready:
            return
        self.spin_seg_start.blockSignals(True)
        self.spin_seg_len.blockSignals(True)
        self.spin_seg_start.setValue(self.spin_x_min.value())
        self.spin_seg_len.setValue(self.spin_x_window.value())
        self.spin_seg_start.blockSignals(False)
        self.spin_seg_len.blockSignals(False)
        self.segment_changed()

    def use_whole_recording(self):
        if not self.ui_ready:
            return
        self.spin_seg_start.blockSignals(True)
        self.spin_seg_len.blockSignals(True)
        self.spin_seg_start.setValue(self.file_start)
        self.spin_seg_len.setValue(self.file_end - self.file_start)
        self.spin_seg_start.blockSignals(False)
        self.spin_seg_len.blockSignals(False)
        self.segment_changed()

    def sync_segment_widgets(self):
        """Push segment state back into the widgets and the view spinboxes."""
        if not self.ui_ready:
            return
        low, high = self.segment_bounds()
        span = self.file_end - self.file_start
        i0, i1 = self.segment_slice

        for spin, lo, hi, value in (
            (self.spin_seg_start, self.file_start, self.file_end, self.segment_start),
            (self.spin_seg_len, 0.05, max(span, 0.05), self.segment_duration),
            (self.spin_x_min, low, high, low),
        ):
            spin.blockSignals(True)
            spin.setRange(lo, hi)
            spin.setValue(value)
            spin.blockSignals(False)

        self.spin_x_window.blockSignals(True)
        self.spin_x_window.setRange(0.05, max(high - low, 0.05))
        self.spin_x_window.setValue(min(self.spin_x_window.value(), high - low))
        self.spin_x_window.blockSignals(False)

        n_manual = len(self.manual_ts) - len(self.manual_in_segment())
        outside = f"  |  {n_manual} annotation(s) outside segment" if n_manual else ""
        self.label_segment.setText(
            f"analysing {high - low:.1f} s of {span:.1f} s "
            f"({i1 - i0:,} of {self.full_time.size:,} samples){outside}"
        )
        self.update_range()

    def manual_in_segment(self):
        """
        Annotations outside the segment must not be scored against: the
        detectors never saw that stretch of signal, so every annotation out
        there would be counted as a false negative and quietly tank recall.
        """
        if not self.manual_ts:
            return []
        low, high = self.segment_bounds()
        return [t for t in self.manual_ts if low <= t <= high]

    # -- construction ------------------------------------------------------
    def build_ui(self):
        outer = QtWidgets.QHBoxLayout(self)

        # --- left: pipelines ------------------------------------------
        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel(
            f"<b>Pipelines</b> &mdash; signal: {self.voltage_column} "
            f"@ {self.fs:.0f} Hz"
        ))

        # --- analysis segment -----------------------------------------
        seg_box = QtWidgets.QGroupBox("Analysis segment")
        seg_box.setToolTip(
            "Filtering, beat calling and plotting are limited to this slice.\n"
            "Tune a grid on a short segment, then widen it once it looks right."
        )
        seg_outer = QtWidgets.QVBoxLayout()
        seg_row = QtWidgets.QHBoxLayout()
        seg_row.addWidget(QtWidgets.QLabel("Start (s):"))
        self.spin_seg_start = QtWidgets.QDoubleSpinBox()
        self.spin_seg_start.setDecimals(3)
        self.spin_seg_start.setRange(self.file_start, self.file_end)
        self.spin_seg_start.setValue(self.segment_start)
        self.spin_seg_start.setKeyboardTracking(False)
        self.spin_seg_start.editingFinished.connect(self.segment_changed)
        seg_row.addWidget(self.spin_seg_start)

        seg_row.addWidget(QtWidgets.QLabel("Length (s):"))
        self.spin_seg_len = QtWidgets.QDoubleSpinBox()
        self.spin_seg_len.setDecimals(3)
        self.spin_seg_len.setRange(0.05, max(self.file_end - self.file_start, 0.05))
        self.spin_seg_len.setValue(self.segment_duration)
        self.spin_seg_len.setKeyboardTracking(False)
        self.spin_seg_len.editingFinished.connect(self.segment_changed)
        seg_row.addWidget(self.spin_seg_len)

        button_view = QtWidgets.QPushButton("Use current view")
        button_view.clicked.connect(self.use_current_view)
        seg_row.addWidget(button_view)
        button_whole = QtWidgets.QPushButton("Whole recording")
        button_whole.clicked.connect(self.use_whole_recording)
        seg_row.addWidget(button_whole)
        seg_outer.addLayout(seg_row)

        self.label_segment = QtWidgets.QLabel("")
        seg_outer.addWidget(self.label_segment)
        seg_box.setLayout(seg_outer)
        left.addWidget(seg_box)

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["run", "show", "Filter", "Normalise", "Beat caller", ""]
        )
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 36)
        self.table.setColumnWidth(1, 40)
        self.table.setColumnWidth(2, 180)
        self.table.setColumnWidth(3, 180)
        self.table.setColumnWidth(4, 180)
        self.table.setColumnWidth(5, 80)
        self.table.setMinimumWidth(740)
        self.table.setToolTip(
            "run = include in the next run;  show = draw in the plots.\n"
            "Run a large grid, then show only the few you are comparing."
        )
        self.table.itemSelectionChanged.connect(self.redraw_signal)
        left.addWidget(self.table)

        row = QtWidgets.QHBoxLayout()
        for text, slot in [
            ("Add", self.add_pipeline),
            ("Duplicate", self.duplicate_pipeline),
            ("Remove", self.remove_pipeline),
            ("Full grid", self.add_full_grid),
        ]:
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        left.addLayout(row)

        run = QtWidgets.QPushButton("Run all enabled pipelines")
        run.clicked.connect(self.run_all)
        left.addWidget(run)

        show_row = QtWidgets.QHBoxLayout()
        show_all = QtWidgets.QPushButton("Show all")
        show_all.clicked.connect(lambda: self.set_all_visible(True))
        show_row.addWidget(show_all)
        show_none = QtWidgets.QPushButton("Show none")
        show_none.clicked.connect(lambda: self.set_all_visible(False))
        show_row.addWidget(show_none)
        self.checkBox_overlay_traces = QtWidgets.QCheckBox(
            "overlay all shown detector inputs"
        )
        self.checkBox_overlay_traces.setToolTip(
            "Off: only the highlighted row's trace is drawn (beat markers for "
            "every shown pipeline are drawn either way)."
        )
        self.checkBox_overlay_traces.stateChanged.connect(self.redraw_signal)
        show_row.addWidget(self.checkBox_overlay_traces)
        left.addLayout(show_row)

        # reference / tolerance
        ref_row = QtWidgets.QHBoxLayout()
        ref_row.addWidget(QtWidgets.QLabel("Reference:"))
        self.combo_reference = QtWidgets.QComboBox()
        ref_row.addWidget(self.combo_reference, 1)
        ref_row.addWidget(QtWidgets.QLabel("Tolerance (ms):"))
        self.spin_tolerance = QtWidgets.QDoubleSpinBox()
        self.spin_tolerance.setRange(1, 500)
        self.spin_tolerance.setValue(comparison.DEFAULT_TOLERANCE * 1000)
        ref_row.addWidget(self.spin_tolerance)
        left.addLayout(ref_row)

        self.combo_reference.currentIndexChanged.connect(self.refresh_metrics)
        self.spin_tolerance.valueChanged.connect(self.refresh_metrics)

        self.metrics_table = QtWidgets.QTableWidget(0, 0)
        left.addWidget(QtWidgets.QLabel("<b>Scores vs reference</b>"))
        left.addWidget(self.metrics_table)

        export_row = QtWidgets.QHBoxLayout()
        export = QtWidgets.QPushButton("Export comparison report")
        export.clicked.connect(self.export_report)
        export_row.addWidget(export)
        send = QtWidgets.QPushButton("Send selected beats to main window")
        send.clicked.connect(self.send_to_main)
        export_row.addWidget(send)
        left.addLayout(export_row)

        config_row = QtWidgets.QHBoxLayout()
        save_config = QtWidgets.QPushButton("Save pipeline config (for batch)")
        save_config.clicked.connect(self.save_config)
        config_row.addWidget(save_config)
        load_config = QtWidgets.QPushButton("Load pipeline config")
        load_config.clicked.connect(self.load_config)
        config_row.addWidget(load_config)
        left.addLayout(config_row)

        outer.addLayout(left, 0)

        # --- right: plots ---------------------------------------------
        right = QtWidgets.QVBoxLayout()

        self.signal_plot = pyqtgraph.PlotWidget()
        self.signal_plot.setBackground("w")
        # peak-preserving downsampling: keeps R peaks visible while drawing far
        # fewer points than the segment contains
        self.signal_plot.setDownsampling(auto=True, mode="peak")
        self.signal_plot.setClipToView(True)
        self.signal_plot.addLegend(offset=(10, 10))
        self.signal_plot.scene().sigMouseClicked.connect(self.plot_clicked)
        self.view_box = self.signal_plot.plotItem.vb

        self.raster_plot = pyqtgraph.PlotWidget()
        self.raster_plot.setBackground("w")
        self.raster_plot.setMaximumHeight(260)
        self.raster_plot.setXLink(self.signal_plot)
        self.raster_plot.getAxis("left").setWidth(60)

        right.addWidget(self.signal_plot, 3)
        right.addWidget(self.raster_plot, 1)

        nav = QtWidgets.QHBoxLayout()
        self.button_annotate = QtWidgets.QPushButton("Annotate beats (click plot)")
        self.button_annotate.setCheckable(True)
        self.button_annotate.toggled.connect(self.set_annotate_mode)
        nav.addWidget(self.button_annotate)

        clear = QtWidgets.QPushButton("Clear annotations")
        clear.clicked.connect(self.clear_annotations)
        nav.addWidget(clear)

        nav.addStretch(1)
        nav.addWidget(QtWidgets.QLabel("Disagreements:"))
        for text, slot in [("|<", self.first_review), ("<<<", self.prev_review),
                           (">>>", self.next_review), (">|", self.last_review)]:
            button = QtWidgets.QPushButton(text)
            button.setMaximumWidth(50)
            button.clicked.connect(slot)
            nav.addWidget(button)
        self.label_review = QtWidgets.QLabel("0 / 0")
        nav.addWidget(self.label_review)

        nav.addStretch(1)
        nav.addWidget(QtWidgets.QLabel("Time (s):"))
        low, high = self.segment_bounds()
        self.spin_x_min = QtWidgets.QDoubleSpinBox()
        self.spin_x_min.setRange(low, high)
        self.spin_x_min.setValue(low)
        self.spin_x_min.setDecimals(3)
        self.spin_x_min.valueChanged.connect(self.update_range)
        nav.addWidget(self.spin_x_min)
        nav.addWidget(QtWidgets.QLabel("Window (s):"))
        self.spin_x_window = QtWidgets.QDoubleSpinBox()
        self.spin_x_window.setRange(0.05, max(high - low, 0.05))
        self.spin_x_window.setValue(min(5.0, max(high - low, 0.05)))
        self.spin_x_window.valueChanged.connect(self.update_range)
        nav.addWidget(self.spin_x_window)

        right.addLayout(nav)

        self.status = QtWidgets.QLabel("no pipelines run yet")
        right.addWidget(self.status)

        outer.addLayout(right, 1)

        self.update_range()

    # -- pipeline table ----------------------------------------------------
    def add_pipeline(self, pipeline=None):
        if not isinstance(pipeline, Pipeline):
            pipeline = Pipeline(
                filter_name=(
                    "butter_highpass"
                    if "butter_highpass" in FILTERS
                    else FILTERS.names()[0]
                ),
                caller_name=BEATCALLERS.names()[0],
                norm_name=DEFAULT_NORMALIZER,
                color=next_color(len(self.pipelines)),
            )
        self.pipelines.append(pipeline)
        self.rebuild_table()

    def duplicate_pipeline(self):
        row = self.table.currentRow()
        if row < 0:
            return
        clone = self.pipelines[row].clone()
        clone.color = next_color(len(self.pipelines))
        self.pipelines.append(clone)
        self.rebuild_table()

    def remove_pipeline(self):
        row = self.table.currentRow()
        if row < 0:
            return
        self.results.pop(self.pipelines[row].uid, None)
        del self.pipelines[row]
        self.rebuild_table()
        self.redraw_all()

    def add_full_grid(self):
        dialog = GridDialog(self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        for f in dialog.selected_filters():
            for n in dialog.selected_normalizers():
                for c in dialog.selected_callers():
                    self.pipelines.append(
                        Pipeline(
                            f, c, norm_name=n, color=next_color(len(self.pipelines))
                        )
                    )
        self.rebuild_table()

    def rebuild_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.pipelines))
        for row, pipeline in enumerate(self.pipelines):
            run_check = QtWidgets.QTableWidgetItem()
            run_check.setFlags(
                QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled
            )
            run_check.setCheckState(
                QtCore.Qt.Checked if pipeline.enabled else QtCore.Qt.Unchecked
            )
            run_check.setBackground(pyqtgraph.mkColor(pipeline.color))
            self.table.setItem(row, 0, run_check)

            show_check = QtWidgets.QTableWidgetItem()
            show_check.setFlags(
                QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled
            )
            show_check.setCheckState(
                QtCore.Qt.Checked if pipeline.visible else QtCore.Qt.Unchecked
            )
            self.table.setItem(row, 1, show_check)

            filter_combo = QtWidgets.QComboBox()
            for name, label in FILTERS.labels().items():
                filter_combo.addItem(label, name)
            filter_combo.setCurrentIndex(FILTERS.names().index(pipeline.filter_name))
            filter_combo.currentIndexChanged.connect(
                lambda _, r=row: self.filter_changed(r)
            )
            self.table.setCellWidget(row, 2, filter_combo)

            norm_combo = QtWidgets.QComboBox()
            for name, label in NORMALIZERS.labels().items():
                norm_combo.addItem(label, name)
            norm_combo.setCurrentIndex(NORMALIZERS.names().index(pipeline.norm_name))
            norm_combo.currentIndexChanged.connect(
                lambda _, r=row: self.norm_changed(r)
            )
            self.table.setCellWidget(row, 3, norm_combo)

            caller_combo = QtWidgets.QComboBox()
            for name, label in BEATCALLERS.labels().items():
                caller_combo.addItem(label, name)
            caller_combo.setCurrentIndex(
                BEATCALLERS.names().index(pipeline.caller_name)
            )
            caller_combo.currentIndexChanged.connect(
                lambda _, r=row: self.caller_changed(r)
            )
            self.table.setCellWidget(row, 4, caller_combo)

            configure = QtWidgets.QPushButton("Settings")
            configure.clicked.connect(lambda _, r=row: self.configure(r))
            self.table.setCellWidget(row, 5, configure)

        self.table.blockSignals(False)
        self.table.itemChanged.connect(self.enabled_changed)
        self.rebuild_reference_combo()

    def enabled_changed(self, item):
        checked = item.checkState() == QtCore.Qt.Checked
        if item.column() == 0:
            self.pipelines[item.row()].enabled = checked
            self.rebuild_reference_combo()
        elif item.column() == 1:
            self.pipelines[item.row()].visible = checked
            self.redraw_all()

    def filter_changed(self, row):
        combo = self.table.cellWidget(row, 2)
        pipeline = self.pipelines[row]
        pipeline.filter_name = combo.currentData()
        pipeline.filter_settings = FILTERS[pipeline.filter_name].Settings()
        pipeline.label = pipeline.default_label()
        self.rebuild_reference_combo()

    def norm_changed(self, row):
        combo = self.table.cellWidget(row, 3)
        pipeline = self.pipelines[row]
        pipeline.norm_name = combo.currentData()
        pipeline.norm_settings = NORMALIZERS[pipeline.norm_name].Settings()
        pipeline.label = pipeline.default_label()
        self.rebuild_reference_combo()

    def caller_changed(self, row):
        combo = self.table.cellWidget(row, 4)
        pipeline = self.pipelines[row]
        pipeline.caller_name = combo.currentData()
        pipeline.caller_settings = BEATCALLERS[pipeline.caller_name].Settings()
        pipeline.label = pipeline.default_label()
        self.rebuild_reference_combo()

    def configure(self, row):
        pipeline = self.pipelines[row]
        tabs = QtWidgets.QDialog(self)
        tabs.setWindowTitle(pipeline.label)
        layout = QtWidgets.QVBoxLayout(tabs)

        filter_button = QtWidgets.QPushButton(
            f"Filter settings - {FILTERS[pipeline.filter_name].label}"
        )
        filter_button.clicked.connect(
            lambda: SettingsDialog(
                pipeline.filter_settings,
                FILTERS[pipeline.filter_name].label,
                FILTERS[pipeline.filter_name].description,
                self,
            ).exec()
        )
        norm_button = QtWidgets.QPushButton(
            f"Normalise settings - {NORMALIZERS[pipeline.norm_name].label}"
        )
        norm_button.clicked.connect(
            lambda: SettingsDialog(
                pipeline.norm_settings,
                NORMALIZERS[pipeline.norm_name].label,
                NORMALIZERS[pipeline.norm_name].description,
                self,
            ).exec()
        )
        caller_button = QtWidgets.QPushButton(
            f"Beat caller settings - {BEATCALLERS[pipeline.caller_name].label}"
        )
        caller_button.clicked.connect(
            lambda: SettingsDialog(
                pipeline.caller_settings,
                BEATCALLERS[pipeline.caller_name].label,
                BEATCALLERS[pipeline.caller_name].description,
                self,
            ).exec()
        )
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(tabs.accept)
        for widget in (filter_button, norm_button, caller_button, close):
            layout.addWidget(widget)
        tabs.exec()

    # -- execution ---------------------------------------------------------
    def run_all(self):
        enabled = [p for p in self.pipelines if p.enabled]
        if not enabled:
            return

        progress = QtWidgets.QProgressDialog(
            "Running pipelines...", "Cancel", 0, len(enabled), self
        )
        progress.setWindowModality(QtCore.Qt.WindowModal)

        def report(done, total, label):
            progress.setValue(done)
            progress.setLabelText(label)
            QtWidgets.QApplication.processEvents()

        self.results = run_pipelines(
            self.pipelines,
            self.data,
            self.time_column,
            self.voltage_column,
            progress=report,
        )
        progress.close()

        errors = [
            f"{r.pipeline.label}: {r.error}"
            for r in self.results.values()
            if not r.ok
        ]
        counts = ", ".join(
            f"{r.pipeline.label}: {len(r.beats)} beats ({r.runtime_s:.2f}s)"
            for r in self.results.values()
            if r.ok
        )
        low, high = self.segment_bounds()
        self.status.setText(
            f"[{low:.1f}-{high:.1f} s]  "
            + counts
            + ("  |  FAILED: " + "; ".join(errors) if errors else "")
        )

        self.rebuild_reference_combo()
        self.redraw_all()

    # -- reference ---------------------------------------------------------
    def rebuild_reference_combo(self):
        current = self.combo_reference.currentData()
        self.combo_reference.blockSignals(True)
        self.combo_reference.clear()
        self.combo_reference.addItem("Manual annotations", MANUAL)
        self.combo_reference.addItem("Consensus (majority of enabled)", CONSENSUS)
        for pipeline in self.pipelines:
            if pipeline.enabled:
                self.combo_reference.addItem(pipeline.label, pipeline.uid)
        index = self.combo_reference.findData(current)
        self.combo_reference.setCurrentIndex(max(index, 0))
        self.combo_reference.blockSignals(False)

    def reference_ts(self):
        key = self.combo_reference.currentData()
        if key == MANUAL:
            return numpy.sort(numpy.array(self.manual_in_segment(), dtype=float))
        if key == CONSENSUS:
            return comparison.consensus_beats(
                [r.beats for r in self.results.values() if r.ok],
                tolerance=self.tolerance(),
            )
        result = self.results.get(key)
        return result.beats if result else numpy.array([])

    def tolerance(self):
        return self.spin_tolerance.value() / 1000.0

    # -- metrics -----------------------------------------------------------
    def refresh_metrics(self):
        if not self.results:
            return
        labels = {r.pipeline.uid: r.pipeline.label for r in self.results.values()}
        table = comparison.score_table(
            self.results, self.reference_ts(), self.tolerance(), labels
        )
        columns = [
            "pipeline", "n_test", "TP", "FP", "FN",
            "sensitivity", "PPV", "F1", "abs_error_ms", "mean_HR", "runtime_s",
        ]
        columns = [c for c in columns if c in table.columns]
        table = table[columns]

        self.metrics_table.setRowCount(len(table))
        self.metrics_table.setColumnCount(len(columns))
        self.metrics_table.setHorizontalHeaderLabels(columns)
        for i, (_, row) in enumerate(table.iterrows()):
            for j, column in enumerate(columns):
                value = row[column]
                if isinstance(value, float):
                    value = f"{value:.3f}" if not numpy.isnan(value) else "-"
                self.metrics_table.setItem(
                    i, j, QtWidgets.QTableWidgetItem(str(value))
                )
        self.metrics_table.resizeColumnsToContents()

        # review queue
        disagreements = comparison.disagreement_times(
            self.results, self.reference_ts(), self.tolerance()
        )
        self.review_windows = comparison.cluster_disagreements(disagreements)
        self.review_index = 0
        self.update_review_label()

    # -- plotting ----------------------------------------------------------
    def shown_results(self):
        """Results for pipelines whose 'show' box is ticked, in table order."""
        shown = []
        for pipeline in self.pipelines:
            if not pipeline.visible:
                continue
            result = self.results.get(pipeline.uid)
            if result and result.ok:
                shown.append(result)
        return shown

    def set_all_visible(self, visible):
        for pipeline in self.pipelines:
            pipeline.visible = visible
        self.rebuild_table()
        self.redraw_all()

    def agreement(self, result, reference):
        """
        Split one pipeline's beats against the reference into:
          hit    - matched a reference beat within tolerance
          extra  - a beat the reference does not have (false positive)
          missed - a reference beat this pipeline did not find (false negative)

        This is what makes multiple combos comparable at a glance: an 'x' and an
        open circle in the same column tell you instantly which pipeline is
        wrong, without reading the metrics table.
        """
        reference_ts = comparison._as_ts(reference)
        test_ts = comparison._as_ts(result.beats)
        matches, missed, extra = comparison.match_beats(
            reference_ts, test_ts, self.tolerance()
        )
        matched_test = numpy.array([t for _, t in matches], dtype=int)
        return {
            "hit": test_ts[matched_test] if matched_test.size else numpy.array([]),
            "extra": test_ts[extra] if len(extra) else numpy.array([]),
            "missed": reference_ts[missed] if len(missed) else numpy.array([]),
        }

    def redraw_all(self):
        self.redraw_signal()
        self.redraw_raster()
        self.refresh_metrics()

    def redraw_signal(self):
        self.signal_plot.clear()

        # raw trace, always
        self.signal_plot.plot(
            self.time, self.voltage,
            pen=pyqtgraph.mkPen((170, 170, 170), width=1), name="raw",
        )

        shown = self.shown_results()
        current_row = self.table.currentRow()
        highlighted = (
            self.pipelines[current_row].uid
            if 0 <= current_row < len(self.pipelines)
            else None
        )

        # detector-input traces: all shown, or just the highlighted one
        for result in shown:
            if (
                self.checkBox_overlay_traces.isChecked()
                or result.pipeline.uid == highlighted
            ):
                self.signal_plot.plot(
                    self.time, result.signal,
                    pen=pyqtgraph.mkPen(result.pipeline.color, width=1),
                    name=f"detector input: {result.pipeline.label}",
                )

        # --- beat markers, one lane per shown pipeline, stacked above the trace
        traces = [r.signal for r in shown] or [self.voltage]
        ceiling = max(
            float(numpy.nanpercentile(trace, 99.9)) for trace in traces
        )
        ceiling = ceiling if numpy.isfinite(ceiling) and ceiling > 0 else 1.0
        step = ceiling * 0.14
        base = ceiling * 1.15

        reference = self.reference_ts()
        reference_ts = comparison._as_ts(reference)

        # reference lane (manual annotations / consensus / chosen pipeline)
        if reference_ts.size:
            self.signal_plot.plot(
                reference_ts, numpy.full(reference_ts.shape, base),
                pen=None, symbol="d", symbolSize=9,
                symbolBrush=(0, 0, 0), symbolPen=(0, 0, 0),
                name=f"reference: {self.combo_reference.currentText()}",
            )

        for i, result in enumerate(shown):
            lane = base + (i + 1) * step
            color = result.pipeline.color
            split = self.agreement(result, reference_ts)

            # hits: filled circle in the pipeline colour
            if len(split["hit"]):
                self.signal_plot.plot(
                    split["hit"], numpy.full(len(split["hit"]), lane),
                    pen=None, symbol="o", symbolSize=7,
                    symbolBrush=color, symbolPen=color,
                    name=result.pipeline.label,
                )
            # false positives: cross
            if len(split["extra"]):
                self.signal_plot.plot(
                    split["extra"], numpy.full(len(split["extra"]), lane),
                    pen=None, symbol="x", symbolSize=10,
                    symbolBrush=(200, 0, 0), symbolPen=(200, 0, 0),
                )
            # false negatives: hollow marker at the reference time
            if len(split["missed"]):
                self.signal_plot.plot(
                    split["missed"], numpy.full(len(split["missed"]), lane),
                    pen=None, symbol="o", symbolSize=9,
                    symbolBrush=None, symbolPen=pyqtgraph.mkPen((200, 0, 0), width=2),
                )

        # manual annotations also sit on the waveform itself while annotating
        if self.manual_in_segment():
            manual = numpy.array(self.manual_in_segment())
            self.signal_plot.plot(
                manual, numpy.interp(manual, self.time, self.voltage),
                pen=None, symbol="d", symbolBrush=(0, 0, 0), symbolSize=9,
                name="manual",
            )

        self.update_range()

    def redraw_raster(self):
        self.raster_plot.clear()
        shown = self.shown_results()
        reference_ts = comparison._as_ts(self.reference_ts())

        lanes = []
        if reference_ts.size:
            self.raster_plot.plot(
                reference_ts, numpy.zeros(reference_ts.shape),
                pen=None, symbol="d", symbolSize=8,
                symbolBrush=(0, 0, 0), symbolPen=(0, 0, 0),
            )
            lanes.append((0, "reference"))

        for i, result in enumerate(shown, start=1 if reference_ts.size else 0):
            color = result.pipeline.color
            split = self.agreement(result, reference_ts)

            if len(split["hit"]):
                self.raster_plot.plot(
                    split["hit"], numpy.full(len(split["hit"]), i),
                    pen=None, symbol="o", symbolSize=7,
                    symbolBrush=color, symbolPen=color,
                )
            if len(split["extra"]):
                self.raster_plot.plot(
                    split["extra"], numpy.full(len(split["extra"]), i),
                    pen=None, symbol="x", symbolSize=10,
                    symbolBrush=(200, 0, 0), symbolPen=(200, 0, 0),
                )
            if len(split["missed"]):
                self.raster_plot.plot(
                    split["missed"], numpy.full(len(split["missed"]), i),
                    pen=None, symbol="o", symbolSize=9,
                    symbolBrush=None,
                    symbolPen=pyqtgraph.mkPen((200, 0, 0), width=2),
                )
            lanes.append((i, result.pipeline.label[:30]))

        self.raster_plot.getAxis("left").setTicks([lanes])
        self.raster_plot.setYRange(-0.5, max(len(lanes) - 0.5, 0.5))
        self.raster_plot.setMaximumHeight(max(140, 34 * (len(lanes) + 1)))

    def update_range(self):
        low, high = self.segment_bounds()
        x_min = min(max(self.spin_x_min.value(), low), high)
        x_max = min(x_min + self.spin_x_window.value(), high)
        self.signal_plot.setXRange(x_min, max(x_max, x_min + 0.05), padding=0)

    # -- manual annotation -------------------------------------------------
    def set_annotate_mode(self, on):
        self.annotate_mode = on
        self.signal_plot.setBackground("#fff8e1" if on else "w")

    def clear_annotations(self):
        self.manual_ts = []
        self.sync_segment_widgets()
        self.redraw_all()

    def plot_clicked(self, event):
        if not self.annotate_mode:
            return
        point = self.view_box.mapSceneToView(event.scenePos())
        clicked = float(point.x())

        low, high = self.segment_bounds()
        if not low <= clicked <= high:
            self.status.setText(
                "click is outside the analysis segment - annotation ignored"
            )
            return

        # ctrl-click removes the nearest annotation
        if event.modifiers() & QtCore.Qt.ControlModifier:
            if self.manual_ts:
                nearest = min(self.manual_ts, key=lambda t: abs(t - clicked))
                if abs(nearest - clicked) < self.tolerance() * 4:
                    self.manual_ts.remove(nearest)
        else:
            # snap to the local voltage maximum within the tolerance window
            half = max(int(self.tolerance() * self.fs), 1)
            index = int(numpy.searchsorted(self.time, clicked))
            start = max(index - half, 0)
            stop = min(index + half + 1, self.time.size)
            snapped = start + int(numpy.argmax(self.voltage[start:stop]))
            self.manual_ts.append(float(self.time[snapped]))
            self.manual_ts.sort()

        self.sync_segment_widgets()
        self.redraw_all()

    # -- review navigation -------------------------------------------------
    def update_review_label(self):
        total = len(self.review_windows)
        self.label_review.setText(
            f"{min(self.review_index + 1, total)} / {total}"
        )

    def goto_review(self):
        if not len(self.review_windows):
            return
        ts = self.review_windows[self.review_index]
        self.spin_x_min.setValue(ts - self.spin_x_window.value() / 3)
        self.update_review_label()
        self.update_range()

    def first_review(self):
        self.review_index = 0
        self.goto_review()

    def last_review(self):
        self.review_index = max(len(self.review_windows) - 1, 0)
        self.goto_review()

    def next_review(self):
        self.review_index = min(
            self.review_index + 1, max(len(self.review_windows) - 1, 0)
        )
        self.goto_review()

    def prev_review(self):
        self.review_index = max(self.review_index - 1, 0)
        self.goto_review()

    # -- output ------------------------------------------------------------
    def export_report(self):
        if not self.results:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save comparison report", "", "Excel (*.xlsx)"
        )
        if not path:
            return

        labels = {r.pipeline.uid: r.pipeline.label for r in self.results.values()}
        writer = pandas.ExcelWriter(path, engine="xlsxwriter")

        pandas.DataFrame(
            [r.pipeline.as_record() for r in self.results.values()]
        ).to_excel(writer, sheet_name="pipelines", index=False)

        comparison.score_table(
            self.results, self.reference_ts(), self.tolerance(), labels
        ).to_excel(writer, sheet_name="scores", index=False)

        comparison.pairwise_f1(
            self.results, self.tolerance(), labels
        ).to_excel(writer, sheet_name="pairwise_F1")

        comparison.disagreement_times(
            self.results, self.reference_ts(), self.tolerance()
        ).replace({"pipeline_uid": labels}).to_excel(
            writer, sheet_name="disagreements", index=False
        )

        low, high = self.segment_bounds()
        i0, i1 = self.segment_slice
        pandas.DataFrame(
            [{
                "segment_start_s": low,
                "segment_stop_s": high,
                "segment_samples": i1 - i0,
                "file_start_s": self.file_start,
                "file_stop_s": self.file_end,
                "file_samples": int(self.full_time.size),
                "sampling_rate_hz": self.fs,
                "signal_column": self.voltage_column,
            }]
        ).to_excel(writer, sheet_name="segment", index=False)

        pandas.DataFrame(
            {
                "ts": self.manual_ts,
                "in_segment": [low <= t <= high for t in self.manual_ts],
            }
        ).to_excel(writer, sheet_name="manual_annotations", index=False)

        for uid, result in self.results.items():
            if result.ok and len(result.beats):
                sheet = f"beats_{uid}"[:31]
                result.beats.to_excel(writer, sheet_name=sheet, index=False)

        writer.close()
        self.status.setText(f"report written to {path}")

    def save_config(self):
        """Write the current grid as a json config that modules.batch can run."""
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save pipeline config", "pipelines.json", "JSON (*.json)"
        )
        if not path:
            return
        config = {
            "signal_column": self.voltage_column,
            "signal_column_aliases": [self.voltage_column],
            "time_column": self.time_column,
            "tolerance_ms": self.spin_tolerance.value(),
            "reference": "consensus",
            "pipelines": batch.pipelines_to_config(self.pipelines),
        }
        with open(path, "w") as handle:
            json.dump(config, handle, indent=2)
        self.status.setText(f"config written to {path}")

    def load_config(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load pipeline config", "", "JSON (*.json)"
        )
        if not path:
            return
        with open(path) as handle:
            config = json.load(handle)
        self.pipelines = batch.pipelines_from_config(config)
        if config.get("tolerance_ms"):
            self.spin_tolerance.setValue(config["tolerance_ms"])
        self.results = {}
        self.rebuild_table()
        self.redraw_all()
        self.status.setText(
            f"loaded {len(self.pipelines)} pipeline(s) from {path}"
        )

    def send_to_main(self):
        """Push the highlighted pipeline's beats into the main window's beat_df."""
        row = self.table.currentRow()
        if row < 0 or not self.results:
            return
        result = self.results.get(self.pipelines[row].uid)
        parent = self.parent()
        if result and result.ok and parent is not None:
            parent.beat_df = result.beats.copy().reset_index(drop=True)
            parent.beat_markers = parent.add_plot(
                source=parent.beat_df,
                filt_source=parent.beat_df,
                time_column="ts",
                signal_column="beats",
                symbol="o",
                symbol_pen=(0, 0, 0),
                symbol_brush=(0, 255, 0),
                symbol_size=8,
            )
            low, high = self.segment_bounds()
            self.status.setText(
                f"sent {len(result.beats)} beats from '{result.pipeline.label}' "
                f"to the main window (segment {low:.1f}-{high:.1f} s only)"
            )


class GridDialog(QtWidgets.QDialog):
    """Pick which filters x which callers to add as a full factorial grid."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Build pipeline grid")
        layout = QtWidgets.QHBoxLayout(self)

        self.filter_list = QtWidgets.QListWidget()
        self.filter_list.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        for name, label in FILTERS.labels().items():
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, name)
            self.filter_list.addItem(item)

        self.norm_list = QtWidgets.QListWidget()
        self.norm_list.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        for name, label in NORMALIZERS.labels().items():
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, name)
            self.norm_list.addItem(item)
            if name == DEFAULT_NORMALIZER:
                item.setSelected(True)

        self.caller_list = QtWidgets.QListWidget()
        self.caller_list.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        for name, label in BEATCALLERS.labels().items():
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, name)
            self.caller_list.addItem(item)

        for title, widget in [("Filters", self.filter_list),
                              ("Normalisers", self.norm_list),
                              ("Beat callers", self.caller_list)]:
            column = QtWidgets.QVBoxLayout()
            column.addWidget(QtWidgets.QLabel(f"<b>{title}</b>"))
            column.addWidget(widget)
            layout.addLayout(column)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_filters(self):
        return [i.data(QtCore.Qt.UserRole) for i in self.filter_list.selectedItems()]

    def selected_normalizers(self):
        selected = [
            i.data(QtCore.Qt.UserRole) for i in self.norm_list.selectedItems()
        ]
        return selected or [DEFAULT_NORMALIZER]

    def selected_callers(self):
        return [i.data(QtCore.Qt.UserRole) for i in self.caller_list.selectedItems()]
