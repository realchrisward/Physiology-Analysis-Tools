# -*- coding: utf-8 -*-

"""
ECG_ANALYSIS_TOOL
written by Christopher S Ward (C) 2024

Audit mode additions: batch file pairing, per-beat audit labels,
audit notes, audit xlsx save/load with overwrite protection.
"""

__version__ = "0.0.19"

from PySide6 import QtWidgets
from PySide6.QtWidgets import QFileDialog, QMessageBox
from PySide6.QtCore import Qt, QFile
from PySide6.QtUiTools import QUiLoader

import sys
import os
import importlib

# include regular and relative import -
# !!! temporary solution - needed for pip distribution
try:
    heartbeat_detection = importlib.import_module(
        "modules.heartbeat_detection", "modules"
    )
    arrhythmia_detection = importlib.import_module(
        "modules.arrhythmia_detection", "modules"
    )
    ml_tools = importlib.import_module("modules.ml_tools", "modules")
except:
    print("use of relative import")
    heartbeat_detection = importlib.import_module(
        "physiology_analysis_tools.modules.heartbeat_detection",
        "physiology_analysis_tools.modules",
    )
    arrhythmia_detection = importlib.import_module(
        "physiology_analysis_tools.modules.arrhythmia_detection",
        "physiology_analysis_tools.modules",
    )
    ml_tools = importlib.import_module(
        "physiology_analysis_tools.modules.ml_tools",
        "physiology_analysis_tools.modules",
    )


try:
    from modules import comparison_window, pipeline, comparison
except ImportError:
    from .modules import comparison_window, pipeline, comparison


import traceback
from pyqtgraph import PlotWidget, plot
import pyqtgraph
import pandas
import numpy

# include regular and relative import -
# !!! temporary solution - needed for pip distribution
try:
    from modules.signal_converters import (
        dsi_fp_matlab_extract,
        adi_extract,
        labchart_text_extract,
        pklgzip_extract,
        pcc_extract,
    )
except:
    from .modules.signal_converters import (
        dsi_fp_matlab_extract,
        adi_extract,
        labchart_text_extract,
        pklgzip_extract,
        pcc_extract,
    )

extractors = {
    "adi": {"module": adi_extract, "ext": ".adicht"},
    "labchart_text": {"module": labchart_text_extract, "ext": ".txt"},
    "dsi_fp_matlab": {"module": dsi_fp_matlab_extract, "ext": ".mat"},
    "pklgzip": {"module": pklgzip_extract, "ext": ".gzip"},
    "pcc": {"module": pcc_extract, "ext": ".txt"},
}

# Signal file extensions the loader will recognise
SIGNAL_EXTENSIONS = {".adicht", ".txt", ".mat", ".gzip"}

# Audit label constants – stored verbatim in the audit_label column
AUDIT_LABEL_BAD_SIGNAL = "bad_signal"
AUDIT_LABEL_BAD_BEAT_CALLS = "good_signal_bad_beat_calls"
AUDIT_LABEL_NO_ARRHYTHMIA = "good_beat_calls_no_arrhythmia"
AUDIT_LABEL_ARRHYTHMIA_CONFIRMED = "good_beat_calls_arrhythmia_confirmed"

# Colours used to highlight audit markers on the plot
AUDIT_COLORS = {
    AUDIT_LABEL_BAD_SIGNAL: (180, 180, 180),  # grey
    AUDIT_LABEL_BAD_BEAT_CALLS: (255, 165, 0),  # orange
    AUDIT_LABEL_NO_ARRHYTHMIA: (0, 180, 0),  # green
    AUDIT_LABEL_ARRHYTHMIA_CONFIRMED: (200, 0, 200),  # purple
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def gather_data(
    source, time_column, signal_column, filt_column, x_min, x_max, graph_width
):
    if filt_column:
        source = source[source[filt_column]]
    data_filter = (source[time_column] >= x_min) & (source[time_column] <= x_max)
    x_val = source[time_column][data_filter]
    y_val = source[signal_column][data_filter]

    if len(x_val) > graph_width * 4:
        downsample_factor = int(len(x_val) / graph_width / 4)
        x_val = x_val[::downsample_factor]
        y_val = y_val[::downsample_factor]

    return list(x_val), list(y_val)


def pair_signal_and_output_files(directory):
    """
    Scan *directory* and return two structures:

    paired   : dict  { base_name: {"signal": path, "output": path} }
    unpaired : dict  { base_name: {"signal": path} }   (no matching xlsx)

    A signal file and an xlsx share the same base name (without extension).
    The xlsx produced by this tool has no special suffix, so
    ``recording_001.adicht``  pairs with  ``recording_001.xlsx``.
    """
    signal_files = {}  # base_name -> full path
    output_files = {}  # base_name -> full path

    for fname in os.listdir(directory):
        fpath = os.path.join(directory, fname)
        if not os.path.isfile(fpath):
            continue
        base, ext = os.path.splitext(fname)
        ext = ext.lower()
        if ext in SIGNAL_EXTENSIONS:
            # Prefer the first signal file found for a given base name
            if base not in signal_files:
                signal_files[base] = fpath
        elif ext == ".xlsx" and not base.endswith("_audit"):
            output_files[base] = fpath

    paired = {}
    unpaired = {}
    for base, sig_path in signal_files.items():
        if base in output_files:
            paired[base] = {"signal": sig_path, "output": output_files[base]}
        else:
            unpaired[base] = {"signal": sig_path}

    return paired, unpaired


def audit_xlsx_path(original_xlsx_path):
    """Return the expected audit xlsx path alongside the original."""
    base, _ = os.path.splitext(original_xlsx_path)
    return base + "_audit.xlsx"


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, ui, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.ui = ui

        # Migrate ui children to parent level for convenience
        for att, val in ui.__dict__.items():
            setattr(self, att, val)

        self.ui.setWindowTitle("Physiology Analysis Tools")
        self.label_Title_and_Version.setText(f"ECG Analysis - {__version__}")

        # ── core data ──────────────────────────────────────────────────────
        self.data = None
        self.filtered_data = None
        self.plotted_counter = 0
        self.beat_df = None
        self.bad_beat_only_df = None
        self.arrhythmia_only_df = None
        self.output_dir = None

        self.time_column = None
        self.voltage_column = None
        self.line = None
        self.beat_markers = None
        self.bad_start = None
        self.bad_stop = None
        self.bad_data_list = []
        self.arrhythmia_markers = None
        self.current_arrhythmia = None
        self.current_arrhythmia_index = 0
        self.current_beat = None
        self.current_beat_index = 0
        self.quality_score_markers = None
        self.bad_data_markers = None
        self.bad_data_mode = False
        self.plotted = {}

        self.start_of_file = 0
        self.end_of_file = 0

        self.known_time_columns = ["ts", "time"]

        self.beat_settings = heartbeat_detection.Settings()
        self.arrhythmia_settings = arrhythmia_detection.Settings()

        # ── batch / audit state ────────────────────────────────────────────
        # batch_entries is an ordered list of dicts:
        #   { "base": str, "signal": str, "output": str|None,
        #     "audit": str|None }
        self.batch_entries = []
        self.batch_index = 0  # which file in the batch is active

        self.audit_beat_index = 0  # which arrhythmia beat we are auditing
        # audit_df mirrors beat_df but lives alongside it;
        # populated when an output xlsx is loaded
        self.audit_df = None
        self.audit_file_path = None  # path of the loaded/saved audit xlsx
        self.audit_modified = False  # unsaved changes guard
        self.audit_markers = None  # pyqtgraph scatter for audit labels

        # ── ui init ────────────────────────────────────────────────────────
        self.horizontalScrollBar_Time.setMinimum(0)
        self.horizontalScrollBar_Time.setMaximum(1000000)
        self.horizontalScrollBar_Time.setFocusPolicy(Qt.StrongFocus)

        self.attach_buttons()
        self.add_graph()
        self.reset_gui()

    # -----------------------------------------------------------------------
    # Button / signal wiring
    # -----------------------------------------------------------------------

    def attach_buttons(self):
        # ── menu ──
        self.actionOpen_Files.triggered.connect(self.action_Add_Files)
        self.actionSettings.triggered.connect(self.action_Edit_Settings)
        self.actionExit.triggered.connect(QtWidgets.QApplication.instance().quit)
        self.actionBeat_Detection.triggered.connect(self.action_BeatDetection)
        self.actionArrhythmia_Analysis.triggered.connect(
            self.action_Arrhythmia_Analysis
        )
        self.actionQuality_Scoring.triggered.connect(self.action_Quality_Scoring)
        self.actionAbout.triggered.connect(self.action_about)
        self.actionLoad_Batch.triggered.connect(self.action_load_batch)
        self.actionSave_Audit.triggered.connect(self.action_save_audit)

        # ── standard controls ──
        self.pushButton_Add_Files.clicked.connect(self.action_Add_Files)
        self.pushButton_Clear_Files.clicked.connect(self.action_Clear_Files)
        self.listWidget_Files.clicked.connect(self.action_update_selected_file)
        self.listWidget_Signals.clicked.connect(self.add_signal)
        self.pushButton_Edit_Settings.clicked.connect(self.action_Edit_Settings)
        self.pushButton_BeatDetection.clicked.connect(self.action_BeatDetection)
        self.pushButton_Arrhythmia_Analysis.clicked.connect(
            self.action_Arrhythmia_Analysis
        )
        self.pushButton_Bad_Data.clicked.connect(self.action_bad_data)
        self.pushButton_assign_category.clicked.connect(self.assign_arrhyth_category)
        self.pushButton_start_of_file.clicked.connect(self.action_start_of_file)
        self.pushButton_end_of_file.clicked.connect(self.action_end_of_file)
        self.pushButton_Next_Arrhythmia.clicked.connect(self.action_next_arrhythmia)
        self.pushButton_Prev_Arrhythmia.clicked.connect(self.action_prev_arrhythmia)
        self.pushButton_First_Arrhythmia.clicked.connect(self.action_first_arrhythmia)
        self.pushButton_Last_Arrhythmia.clicked.connect(self.action_last_arrhythmia)
        self.pushButton_next_window.clicked.connect(self.action_next_window)
        self.pushButton_prev_window.clicked.connect(self.action_prev_window)
        self.pushButton_generate_report.clicked.connect(self.action_generate_report)
        self.pushButton_set_output_dir.clicked.connect(self.action_set_output_dir)
        self.pushButton_zoom_in.clicked.connect(self.action_zoom_in)
        self.pushButton_zoom_out.clicked.connect(self.action_zoom_out)
        self.pushButton_Confirm_Arrhythmia.clicked.connect(
            self.action_confirm_arrhythmia
        )
        self.pushButton_Reject_Arrhythmia.clicked.connect(self.action_reject_arrhythmia)

        # ── audit controls ──
        self.pushButton_load_batch.clicked.connect(self.action_load_batch)
        self.pushButton_audit_prev_file.clicked.connect(self.action_audit_prev_file)
        self.pushButton_audit_next_file.clicked.connect(self.action_audit_next_file)
        self.pushButton_audit_prev_beat.clicked.connect(self.action_audit_prev_beat)
        self.pushButton_audit_next_beat.clicked.connect(self.action_audit_next_beat)
        self.pushButton_save_audit.clicked.connect(self.action_save_audit)
        self.pushButton_audit_bad_signal.clicked.connect(
            lambda: self.action_apply_audit_label(AUDIT_LABEL_BAD_SIGNAL)
        )
        self.pushButton_audit_bad_beat_calls.clicked.connect(
            lambda: self.action_apply_audit_label(AUDIT_LABEL_BAD_BEAT_CALLS)
        )
        self.pushButton_audit_no_arrhythmia.clicked.connect(
            lambda: self.action_apply_audit_label(AUDIT_LABEL_NO_ARRHYTHMIA)
        )
        self.pushButton_audit_arrhythmia_confirmed.clicked.connect(
            lambda: self.action_apply_audit_label(AUDIT_LABEL_ARRHYTHMIA_CONFIRMED)
        )

        # ── spinboxes / checkboxes ──
        self.doubleSpinBox_x_window.valueChanged.connect(self.update_graph)
        self.doubleSpinBox_x_min.valueChanged.connect(self.update_graph)
        self.checkBox_auto_y.stateChanged.connect(self.update_graph)
        self.checkBox_plot_filtered.stateChanged.connect(self.update_graph)
        self.doubleSpinBox_filt_freq.valueChanged.connect(
            self.action_update_filtered_signals
        )
        self.doubleSpinBox_filt_order.valueChanged.connect(
            self.action_update_filtered_signals
        )

        self.horizontalScrollBar_Time.valueChanged.connect(self.action_scroll_time)

        self.action_set_arr_method()
        self.comboBox_arrhyth_assign.addItems(
            arrhythmia_detection.annot_arrhythmia_categories
        )

        self.actionCompare_Pipelines = self.menuRun.addAction(
            "Filter / Beat-caller Comparison..."
        )
        self.actionCompare_Pipelines.triggered.connect(self.action_compare_pipelines)

    # ----------------------------
    # Comparison Pipeline
    # ----------------------------
    def action_compare_pipelines(self):
        if self.data is None:
            QMessageBox.warning(None, "Comparison", "Open a signal file first.")
            return
        if self.listWidget_Signals.currentItem() is None:
            QMessageBox.warning(None, "Comparison", "Select a signal channel first.")
            return

        if self.DEVMODE:
            importlib.reload(comparison_window)

        window = comparison_window.ComparisonWindow(
            data=self.data,
            time_column=self.comboBox_time_column.currentText(),
            voltage_column=self.listWidget_Signals.currentItem().text(),
            parent=self,
        )
        window.exec()

    # -----------------------------------------------------------------------
    # GUI reset / initialisation helpers
    # -----------------------------------------------------------------------

    def reset_gui(self):
        self.filepath_dict = {}
        self.current_filepath = None
        self.current_data = None
        self.doubleSpinBox_y_min.setValue(-3)
        self.doubleSpinBox_y_max.setValue(3)
        self.doubleSpinBox_x_min.setValue(0)
        self.doubleSpinBox_x_window.setValue(15)
        self.reset_plot()
        self._refresh_progress_labels()

    def add_graph(self):
        self.graph = pyqtgraph.PlotWidget()
        self.legend = self.graph.addLegend()
        self.legend.setColumnCount(3)
        self.legend.setOffset([0.1, -0.1])
        self.verticalLayout_graph.addWidget(self.graph)
        self.graph.setXRange(
            self.doubleSpinBox_x_min.value(),
            self.doubleSpinBox_x_min.value() + self.doubleSpinBox_x_window.value(),
        )
        self.graph.setBackground("w")
        self.view_box = self.graph.plotItem.vb
        self.graph.scene().sigMouseClicked.connect(self.action_graph_clicked)

    def reset_plot(self):
        for attr in [
            "line",
            "beat_markers",
            "arrhythmia_markers",
            "current_arrhythmia",
            "current_beat",
            "bad_data_markers",
            "audit_markers",
        ]:
            item = getattr(self, attr, None)
            if item is not None:
                self.graph.removeItem(item)
            setattr(self, attr, None)

    # -----------------------------------------------------------------------
    # Progress label helpers
    # -----------------------------------------------------------------------

    def _refresh_progress_labels(self):
        """Update all progress / status labels from current state."""
        # Title-bar progress
        if self.batch_entries:
            total = len(self.batch_entries)
            idx = self.batch_index + 1
            base = self.batch_entries[self.batch_index].get("base", "")
            self.label_progress.setText(f"File {idx} of {total}  |  {base}")
        else:
            self.label_progress.setText("No files loaded")

        # Batch progress label in audit panel
        if self.batch_entries:
            self.label_batch_progress.setText(
                f"File: {self.batch_index + 1} / {len(self.batch_entries)}"
            )
        else:
            self.label_batch_progress.setText("File: - / -")

        # Beat progress label in audit panel
        if self.audit_df is not None and len(self.audit_df) > 0:
            self.label_beat_progress.setText(
                f"Beat: {self.audit_beat_index + 1} / {len(self.audit_df)}"
            )
        else:
            self.label_beat_progress.setText("Beat: - / -")

        # Audit file status
        if self.audit_file_path:
            fname = os.path.basename(self.audit_file_path)
            dirty = " *" if self.audit_modified else ""
            self.label_audit_file_status.setText(f"Audit file: {fname}{dirty}")
        else:
            self.label_audit_file_status.setText("Audit file: not loaded")

    def _refresh_audit_label_display(self):
        """Update the 'Current audit label' label and highlight buttons."""
        label_colors = {
            AUDIT_LABEL_BAD_SIGNAL: "background-color: #b0b0b0",
            AUDIT_LABEL_BAD_BEAT_CALLS: "background-color: #ffa500",
            AUDIT_LABEL_NO_ARRHYTHMIA: "background-color: #00c000; color: white",
            AUDIT_LABEL_ARRHYTHMIA_CONFIRMED: "background-color: #c800c8; color: white",
        }
        btn_map = {
            AUDIT_LABEL_BAD_SIGNAL: self.pushButton_audit_bad_signal,
            AUDIT_LABEL_BAD_BEAT_CALLS: self.pushButton_audit_bad_beat_calls,
            AUDIT_LABEL_NO_ARRHYTHMIA: self.pushButton_audit_no_arrhythmia,
            AUDIT_LABEL_ARRHYTHMIA_CONFIRMED: self.pushButton_audit_arrhythmia_confirmed,
        }

        current_label = None
        current_note = ""
        if (
            self.audit_df is not None
            and len(self.audit_df) > 0
            and 0 <= self.audit_beat_index < len(self.audit_df)
        ):
            row = self.audit_df.iloc[self.audit_beat_index]
            current_label = row.get("audit_label", None)
            if pandas.isna(current_label):
                current_label = None
            current_note = row.get("audit_notes", "")
            if pandas.isna(current_note):
                current_note = ""

        self.label_audit_current.setText(
            f"Current audit label: {current_label or 'None'}"
        )

        # Populate the notes field without triggering unsaved-change logic
        self.lineEdit_audit_notes.blockSignals(True)
        self.lineEdit_audit_notes.setText(str(current_note))
        self.lineEdit_audit_notes.blockSignals(False)

        # Highlight the active button
        for lbl, btn in btn_map.items():
            if lbl == current_label:
                btn.setStyleSheet(label_colors[lbl])
            else:
                btn.setStyleSheet("")

    # -----------------------------------------------------------------------
    # Batch loading and file pairing
    # -----------------------------------------------------------------------

    def action_load_batch(self):
        """
        Ask the user for a directory, scan it for signal + output file pairs,
        warn about unpaired files, then load the first entry.
        """
        directory = QFileDialog.getExistingDirectory(
            self, "Select batch directory containing signal and output files"
        )
        if not directory:
            return

        paired, unpaired = pair_signal_and_output_files(directory)

        if not paired and not unpaired:
            QMessageBox.warning(
                self,
                "No files found",
                "No recognisable signal files were found in the selected directory.",
            )
            return

        if unpaired:
            names = "\n".join(sorted(unpaired.keys()))
            reply = QMessageBox.question(
                self,
                "Unpaired signal files found",
                f"The following signal files have no matching output xlsx and will "
                f"be loaded for first-pass analysis only:\n\n{names}\n\n"
                f"Continue?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                return

        # Build batch entry list: paired entries first, then unpaired
        entries = []
        for base in sorted(paired.keys()):
            info = paired[base]
            audit_path = audit_xlsx_path(info["output"])
            entries.append(
                {
                    "base": base,
                    "signal": info["signal"],
                    "output": info["output"],
                    "audit": audit_path if os.path.isfile(audit_path) else None,
                }
            )
        for base in sorted(unpaired.keys()):
            entries.append(
                {
                    "base": base,
                    "signal": unpaired[base]["signal"],
                    "output": None,
                    "audit": None,
                }
            )

        self.batch_entries = entries
        self.batch_index = 0
        self._load_batch_entry(self.batch_index)

    def _load_batch_entry(self, index):
        """Load the signal file (and output/audit xlsx if available) for batch entry *index*."""
        if not self.batch_entries:
            return
        if index < 0 or index >= len(self.batch_entries):
            return

        # Guard unsaved audit changes
        if self.audit_modified:
            reply = QMessageBox.question(
                self,
                "Unsaved audit changes",
                "You have unsaved audit changes. Save before switching files?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if reply == QMessageBox.Cancel:
                return
            if reply == QMessageBox.Yes:
                self.action_save_audit()

        self.batch_index = index
        entry = self.batch_entries[index]

        # ── load signal file ──
        self._open_signal_file(entry["signal"])

        # ── load output xlsx if available ──
        if entry["output"] and os.path.isfile(entry["output"]):
            self._load_output_xlsx(entry["output"])
        else:
            self.beat_df = None
            self.arrhythmia_only_df = None

        # ── load audit xlsx if available ──
        if entry["audit"] and os.path.isfile(entry["audit"]):
            self._load_audit_xlsx(entry["audit"])
        else:
            self.audit_df = None
            self.audit_file_path = None
            self.audit_modified = False
            self.audit_beat_index = 0

        self._refresh_progress_labels()
        self._refresh_audit_label_display()
        self.reset_plot()
        self.update_graph()

    def _open_signal_file(self, filepath):
        """Extract a signal dataframe from *filepath* using the appropriate extractor."""
        extract_tools = [
            i
            for i in extractors.values()
            if i["ext"] == os.path.splitext(filepath)[1].lower()
        ]
        self.data = None
        for tool in extract_tools:
            if self.data is None:
                try:
                    self.data = tool["module"].SASSI_extract(filepath)
                except Exception:
                    pass

        if self.data is not None:
            self.current_filepath = filepath
            self.action_update_available_signals()
            self.bad_data_list = []
            self.bad_beat_only_df = None
        else:
            self.textBrowser_Status.append(
                f"WARNING: Could not open signal file: {os.path.basename(filepath)}"
            )

    def _load_output_xlsx(self, xlsx_path):
        """
        Read a previously generated report xlsx and reconstruct beat_df
        and arrhythmia_only_df so the viewer can display prior results.
        """
        try:
            xf = pandas.ExcelFile(xlsx_path)
            if "beats" in xf.sheet_names:
                self.beat_df = pandas.read_excel(xlsx_path, sheet_name="beats")
                self.arrhythmia_only_df = (
                    self.beat_df[
                        self.beat_df.get("any_arrhythmia", False) == True
                    ].reset_index()
                    if "any_arrhythmia" in self.beat_df.columns
                    else pandas.DataFrame()
                )
                self.textBrowser_Status.append(
                    f"Loaded output: {os.path.basename(xlsx_path)}"
                )
            else:
                self.textBrowser_Status.append(
                    f"WARNING: {os.path.basename(xlsx_path)} has no 'beats' sheet."
                )
        except Exception as e:
            self.textBrowser_Status.append(f"ERROR loading output xlsx: {e}")

    def _load_audit_xlsx(self, audit_path):
        """
        Load an existing audit xlsx.  The audit sheet contains the beats
        data augmented with audit_label and audit_notes columns.
        """
        try:
            xf = pandas.ExcelFile(audit_path)
            if "beats_audit" in xf.sheet_names:
                self.audit_df = pandas.read_excel(audit_path, sheet_name="beats_audit")
                self.audit_file_path = audit_path
                self.audit_modified = False
                self.audit_beat_index = 0

                # Update the batch entry to record the audit path
                if self.batch_entries:
                    self.batch_entries[self.batch_index]["audit"] = audit_path

                self.textBrowser_Status.append(
                    f"Loaded audit: {os.path.basename(audit_path)}"
                )
            else:
                self.textBrowser_Status.append(
                    f"WARNING: {os.path.basename(audit_path)} has no 'beats_audit' sheet."
                )
        except Exception as e:
            self.textBrowser_Status.append(f"ERROR loading audit xlsx: {e}")

    # -----------------------------------------------------------------------
    # Batch navigation
    # -----------------------------------------------------------------------

    def action_audit_prev_file(self):
        if self.batch_index > 0:
            self._load_batch_entry(self.batch_index - 1)

    def action_audit_next_file(self):
        if self.batch_index < len(self.batch_entries) - 1:
            self._load_batch_entry(self.batch_index + 1)

    # -----------------------------------------------------------------------
    # Audit beat navigation
    # -----------------------------------------------------------------------

    def _audit_beats_available(self):
        """Return True if there are auditable arrhythmia beats for the current file."""
        return (self.audit_df is not None and len(self.audit_df) > 0) or (
            self.arrhythmia_only_df is not None and len(self.arrhythmia_only_df) > 0
        )

    def _ensure_audit_df(self):
        """
        Create audit_df from arrhythmia_only_df if it does not yet exist,
        adding empty audit_label and audit_notes columns.
        """
        if self.audit_df is not None:
            return

        if self.arrhythmia_only_df is not None and len(self.arrhythmia_only_df) > 0:
            self.audit_df = self.arrhythmia_only_df.copy()
        elif self.beat_df is not None:
            self.audit_df = self.beat_df.copy()
        else:
            return

        if "audit_label" not in self.audit_df.columns:
            self.audit_df["audit_label"] = numpy.nan
        if "audit_notes" not in self.audit_df.columns:
            self.audit_df["audit_notes"] = ""

    def action_audit_prev_beat(self):
        self._ensure_audit_df()
        if self.audit_df is None or len(self.audit_df) == 0:
            return
        self.audit_beat_index = max(0, self.audit_beat_index - 1)
        self._jump_to_audit_beat()

    def action_audit_next_beat(self):
        self._ensure_audit_df()
        if self.audit_df is None or len(self.audit_df) == 0:
            return
        self.audit_beat_index = min(len(self.audit_df) - 1, self.audit_beat_index + 1)
        self._jump_to_audit_beat()

    def _jump_to_audit_beat(self):
        """Centre the view on the current audit beat and refresh labels."""
        if self.audit_df is None or len(self.audit_df) == 0:
            return
        row = self.audit_df.iloc[self.audit_beat_index]
        ts = row.get("ts", None)
        if ts is not None:
            self.doubleSpinBox_x_min.setValue(
                ts - self.doubleSpinBox_x_window.value() / 3
            )
        self._refresh_progress_labels()
        self._refresh_audit_label_display()
        self._redraw_audit_markers()
        self.update_graph()

    # -----------------------------------------------------------------------
    # Audit label application
    # -----------------------------------------------------------------------

    def action_apply_audit_label(self, label):
        """Apply *label* to the currently selected audit beat."""
        self._ensure_audit_df()
        if self.audit_df is None or len(self.audit_df) == 0:
            QMessageBox.information(
                self,
                "No beats",
                "No arrhythmia beats are available to audit. "
                "Run beat detection and arrhythmia analysis first, "
                "or load an output xlsx.",
            )
            return

        note = self.lineEdit_audit_notes.text().strip()

        self.audit_df.at[self.audit_beat_index, "audit_label"] = label
        self.audit_df.at[self.audit_beat_index, "audit_notes"] = note

        self.audit_modified = True
        self._refresh_progress_labels()
        self._refresh_audit_label_display()
        self._redraw_audit_markers()

        # Auto-advance to next beat
        if self.audit_beat_index < len(self.audit_df) - 1:
            self.audit_beat_index += 1
            self._jump_to_audit_beat()

    # -----------------------------------------------------------------------
    # Audit markers on plot
    # -----------------------------------------------------------------------

    def _redraw_audit_markers(self):
        """Remove and redraw all audit label markers on the plot."""
        if self.audit_markers is not None:
            self.graph.removeItem(self.audit_markers)
            self.audit_markers = None

        if self.audit_df is None or len(self.audit_df) == 0:
            return

        labelled = self.audit_df.dropna(subset=["audit_label"])
        if labelled.empty:
            return

        # Build per-label scatter items and overlay them
        for lbl, color in AUDIT_COLORS.items():
            subset = labelled[labelled["audit_label"] == lbl]
            if subset.empty:
                continue
            scatter = self.graph.plot(
                x=list(subset["ts"]),
                y=[1.5] * len(subset),
                name=f"audit:{lbl}",
                symbol="d",
                symbolBrush=color,
                symbolPen=(0, 0, 0),
                symbolSize=10,
                pen=None,
            )
            # Keep reference only to last one; removal happens via reset_plot
            self.audit_markers = scatter

        # Highlight current beat with a star
        if 0 <= self.audit_beat_index < len(self.audit_df):
            row = self.audit_df.iloc[self.audit_beat_index]
            ts = row.get("ts", None)
            if ts is not None:
                self.graph.plot(
                    x=[ts],
                    y=[1.5],
                    name="audit_current",
                    symbol="star",
                    symbolBrush=(255, 255, 0),
                    symbolPen=(0, 0, 0),
                    symbolSize=14,
                    pen=None,
                )

    # -----------------------------------------------------------------------
    # Saving audit output
    # -----------------------------------------------------------------------

    def action_save_audit(self):
        """Save the audit dataframe to an audit xlsx file."""
        if self.audit_df is None:
            QMessageBox.information(
                self,
                "Nothing to save",
                "No audit data exists. Apply at least one audit label first.",
            )
            return

        # Determine default save path
        if self.audit_file_path:
            default_path = self.audit_file_path
        elif self.current_filepath:
            base = os.path.splitext(self.current_filepath)[0]
            default_path = base + "_audit.xlsx"
        elif self.output_dir:
            entry = self.batch_entries[self.batch_index] if self.batch_entries else {}
            base = entry.get("base", "audit")
            default_path = os.path.join(self.output_dir, base + "_audit.xlsx")
        else:
            default_path = "audit.xlsx"

        # Warn if file already exists and has changed
        if os.path.isfile(default_path) and self.audit_modified:
            reply = QMessageBox.question(
                self,
                "Overwrite existing audit file?",
                f"An audit file already exists:\n{default_path}\n\nOverwrite it?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                # Ask user to pick a new path
                default_path, _ = QFileDialog.getSaveFileName(
                    self,
                    "Save audit file as",
                    default_path,
                    "Excel files (*.xlsx)",
                )
                if not default_path:
                    return

        try:
            writer = pandas.ExcelWriter(default_path, engine="xlsxwriter")

            # Write all original sheets if an output xlsx was loaded
            entry = self.batch_entries[self.batch_index] if self.batch_entries else {}
            output_path = entry.get("output", None)
            if output_path and os.path.isfile(output_path):
                orig_xf = pandas.ExcelFile(output_path)
                for sheet in orig_xf.sheet_names:
                    orig_xf.parse(sheet).to_excel(writer, sheet_name=sheet, index=False)

            # Write the augmented audit sheet
            self.audit_df.to_excel(writer, sheet_name="beats_audit", index=False)
            writer.close()

            self.audit_file_path = default_path
            self.audit_modified = False

            # Update batch entry
            if self.batch_entries:
                self.batch_entries[self.batch_index]["audit"] = default_path

            self._refresh_progress_labels()
            self.textBrowser_Status.append(
                f"Audit saved: {os.path.basename(default_path)}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    # -----------------------------------------------------------------------
    # Standard file controls (unchanged from original)
    # -----------------------------------------------------------------------

    def action_about(self):
        QMessageBox.information(
            None,
            "About Physiology Analysis Tools",
            "\n".join([f"{k} : {v}" for k, v in self.version_info.items()]),
        )

    def action_Clear_Files(self):
        self.filepath_dict = {}
        self.listWidget_Files.clear()

    def action_Add_Files(self):
        for p in QFileDialog.getOpenFileNames(self, "Select ECG signal files")[0]:
            self.filepath_dict[os.path.basename(p)] = p
        self.listWidget_Files.clear()
        self.listWidget_Files.addItems(self.filepath_dict)

    def action_update_selected_file(self):
        self.current_filepath = self.filepath_dict[
            self.listWidget_Files.currentItem().text()
        ]
        extract_tools = [
            i
            for i in extractors.values()
            if i["ext"] == os.path.splitext(self.current_filepath)[1]
        ]
        self.data = None
        for i in extract_tools:
            if self.data is None:
                try:
                    self.data = i["module"].SASSI_extract(self.current_filepath)
                except Exception:
                    print("unable to open - trying another extractor")

        if self.data is not None:
            self.action_update_available_signals()

        self.reset_plot()
        self.beat_df = None
        self.bad_beat_only_df = None
        self.arrhythmia_only_df = None
        self.bad_data_list = []

    def action_update_filtered_signals(self):
        if self.data is not None and self.comboBox_time_column.currentText() != "":
            self.filtered_data = pandas.DataFrame()
            sampling_frequency = 1 / (
                self.data[self.comboBox_time_column.currentText()][1]
                - self.data[self.comboBox_time_column.currentText()][0]
            )
            for c in self.data.columns:
                if c in self.known_time_columns:
                    self.filtered_data[c] = self.data[c]
                else:
                    self.filtered_data[c] = heartbeat_detection.basic_filter(
                        self.doubleSpinBox_filt_order.value(),
                        self.data[c],
                        fs=sampling_frequency,
                        cutoff=self.doubleSpinBox_filt_freq.value(),
                        output="sos",
                    )

    def action_update_available_signals(self):
        self.listWidget_Signals.clear()
        self.listWidget_Signals.addItems(self.data.columns)
        self.comboBox_time_column.clear()
        self.comboBox_time_column.addItems(self.data.columns)
        self.action_update_time_column()

    def action_update_time_column(self):
        if (
            any([c in self.known_time_columns for c in self.data.columns])
            and self.comboBox_time_column.currentText() not in self.known_time_columns
        ):
            for c in self.data.columns:
                if c in self.known_time_columns:
                    self.comboBox_time_column.setCurrentText(c)
                    break
        elif self.comboBox_time_column.currentText() in self.known_time_columns:
            pass
        else:
            return

    def action_get_start_and_end_time(self):
        self.end_of_file = max(self.data[self.comboBox_time_column.currentText()])
        self.start_of_file = min(self.data[self.comboBox_time_column.currentText()])
        self.horizontalScrollBar_Time.setPageStep(
            int(
                (
                    self.doubleSpinBox_x_window.value()
                    / (self.end_of_file - self.start_of_file)
                )
                * 1000000
                * 0.9
            )
        )
        self.horizontalScrollBar_Time.setSingleStep(
            int(
                (
                    self.doubleSpinBox_x_window.value()
                    / (self.end_of_file - self.start_of_file)
                )
                * 1000000
                * 0.05
            )
        )
        self.action_update_filtered_signals()

    def action_set_arr_method(self):
        self.comboBox_arr_method.clear()
        self.comboBox_arr_method.addItems(["Heuristics", "Unsupervised", "Both"])

    # -----------------------------------------------------------------------
    # Plot helpers
    # -----------------------------------------------------------------------

    def add_signal(self):
        self.action_get_start_and_end_time()
        if self.line is not None:
            self.graph.removeItem(self.line)
        self.line = None
        self.reset_plot()
        pen = pyqtgraph.mkPen("Blue", width=1, style=Qt.PenStyle.SolidLine)
        self.line = self.add_plot(
            pen=pen,
            source=self.data,
            filt_source=self.filtered_data,
            time_column=self.comboBox_time_column.currentText(),
            signal_column=self.listWidget_Signals.currentItem().text(),
        )

    def add_plot(
        self,
        pen=None,
        pen_width=1,
        pen_color=None,
        symbol=None,
        symbol_brush=None,
        symbol_pen=None,
        symbol_size=14,
        source=None,
        filt_source=None,
        filt_column=None,
        time_column=None,
        signal_column=None,
    ):
        x, y = gather_data(
            source,
            time_column,
            signal_column,
            filt_column,
            self.doubleSpinBox_x_min.value(),
            self.doubleSpinBox_x_min.value() + self.doubleSpinBox_x_window.value(),
            self.graph.width(),
        )
        line = self.graph.plot(
            x=x,
            y=y,
            name=signal_column,
            pen=pen,
            symbol=symbol,
            symbolBrush=symbol_brush,
            symbolPen=symbol_pen,
            symbolSize=symbol_size,
        )
        self.plotted[self.plotted_counter] = {
            "line": line,
            "pen": pen,
            "pen_width": pen_width,
            "pen_color": pen_color,
            "symbol": symbol,
            "symbol_brush": symbol_brush,
            "symbol_pen": symbol_pen,
            "symbol_size": symbol_size,
            "name": signal_column,
            "source": source,
            "filt_source": filt_source,
            "filt_column": filt_column,
            "time": time_column,
        }
        self.plotted_counter += 1
        self.update_graph()
        return line

    def update_graph(self):
        for k, v in self.plotted.items():
            source = (
                v["filt_source"]
                if self.checkBox_plot_filtered.isChecked()
                else v["source"]
            )
            x_val, y_val = gather_data(
                source,
                v["time"],
                v["name"],
                v["filt_column"],
                self.doubleSpinBox_x_min.value(),
                self.doubleSpinBox_x_min.value() + self.doubleSpinBox_x_window.value(),
                self.graph.width(),
            )
            v["line"].setData(
                x=x_val, y=y_val, name=v["name"], pen=v["pen"], symbol=v["symbol"]
            )

        if not self.checkBox_auto_y.isChecked():
            self.graph.setYRange(
                self.doubleSpinBox_y_min.value(),
                self.doubleSpinBox_y_max.value(),
                padding=0,
            )
        else:
            self.graph.autoRange(padding=None)

        self.graph.setXRange(
            self.doubleSpinBox_x_min.value(),
            self.doubleSpinBox_x_min.value() + self.doubleSpinBox_x_window.value(),
            padding=0,
        )

        if (
            self.end_of_file - self.start_of_file - self.doubleSpinBox_x_window.value()
        ) > 0:
            self.horizontalScrollBar_Time.setValue(
                int(
                    (self.doubleSpinBox_x_min.value() - self.start_of_file)
                    / (
                        self.end_of_file
                        - self.start_of_file
                        - self.doubleSpinBox_x_window.value()
                    )
                    * 1000000
                )
            )

    # -----------------------------------------------------------------------
    # Navigation actions (unchanged from original)
    # -----------------------------------------------------------------------

    def action_zoom_in(self):
        self.doubleSpinBox_x_window.setValue(self.doubleSpinBox_x_window.value() / 2)
        self.update_graph()

    def action_zoom_out(self):
        self.doubleSpinBox_x_window.setValue(self.doubleSpinBox_x_window.value() * 2)
        self.update_graph()

    def action_start_of_file(self):
        self.doubleSpinBox_x_min.setValue(self.start_of_file)
        self.update_graph()

    def action_end_of_file(self):
        self.doubleSpinBox_x_min.setValue(
            self.end_of_file - self.doubleSpinBox_x_window.value()
        )
        self.update_graph()

    def action_next_window(self):
        self.doubleSpinBox_x_min.setValue(
            min(
                self.end_of_file - self.doubleSpinBox_x_window.value(),
                self.doubleSpinBox_x_min.value() + self.doubleSpinBox_x_window.value(),
            )
        )
        self.update_graph()

    def action_prev_window(self):
        self.doubleSpinBox_x_min.setValue(
            max(
                self.start_of_file,
                self.doubleSpinBox_x_min.value() - self.doubleSpinBox_x_window.value(),
            )
        )
        self.update_graph()

    def action_scroll_time(self):
        if (
            self.end_of_file - self.start_of_file - self.doubleSpinBox_x_window.value()
        ) > 0:
            self.doubleSpinBox_x_min.setValue(
                self.horizontalScrollBar_Time.value()
                / 1000000
                * (
                    self.end_of_file
                    - self.start_of_file
                    - self.doubleSpinBox_x_window.value()
                )
                + self.start_of_file
            )
            self.update_graph()

    # -----------------------------------------------------------------------
    # Beat detection / arrhythmia (unchanged from original, minus DEVMODE reload)
    # -----------------------------------------------------------------------

    def action_BeatDetection(self):
        if self.beat_markers is not None:
            self.graph.removeItem(self.beat_markers)
        self.beat_markers = None

        self.beat_df = heartbeat_detection.beatcaller(
            self.data,
            time_column=self.comboBox_time_column.currentText(),
            voltage_column=self.listWidget_Signals.currentItem().text(),
            **self.beat_settings.__dict__,
        ).reset_index(drop=True)

        self.beat_markers = self.add_plot(
            source=self.beat_df,
            filt_source=self.beat_df,
            time_column="ts",
            signal_column="beats",
            symbol="o",
            symbol_pen=(0, 0, 0),
            symbol_brush=(0, 255, 0),
            symbol_size=8,
        )

        if self.bad_data_list:
            self.action_update_bad_data_marks()

    def action_Quality_Scoring(self):
        print("quality scoring not yet implemented")

    def action_Arrhythmia_Analysis(self):
        if self.arrhythmia_markers is not None:
            self.graph.removeItem(self.arrhythmia_markers)
        self.arrhythmia_markers = None
        if self.current_arrhythmia is not None:
            self.graph.removeItem(self.current_arrhythmia)
        self.current_arrhythmia = None

        self.beat_df = arrhythmia_detection.call_arrhythmias(
            self.beat_df,
            self.arrhythmia_settings,
            signals=self.filtered_data,
            selected_signal=self.listWidget_Signals.currentItem().text(),
            selected_time=self.comboBox_time_column.currentText(),
            arr_methods=self.comboBox_arr_method.currentText(),
        )

        self.arrhythmia_only_df = self.beat_df[
            self.beat_df.any_arrhythmia
        ].reset_index()

        self.arrhythmia_markers = self.add_plot(
            source=self.arrhythmia_only_df,
            filt_source=self.arrhythmia_only_df,
            time_column="ts",
            signal_column="annot_any_arrhythmia",
            symbol="t1",
            symbol_pen=(0, 0, 0),
            symbol_brush=(255, 0, 0),
            symbol_size=12,
        )

        self.current_arrhythmia_index = 0

        if self.arrhythmia_only_df.shape[0] != 0:
            self.current_arrhythmia = self.graph.plot(
                x=[self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]["ts"]],
                y=[1.2],
                name="current arrhythmia",
                symbol="star",
                symbolBrush=(0, 0, 0),
                symbolPen=(0, 0, 0),
                symbolSize=14,
            )

        # Initialise audit_df from fresh arrhythmia results
        self.audit_df = None
        self._ensure_audit_df()
        self._refresh_progress_labels()

    # -----------------------------------------------------------------------
    # Mouse click on graph
    # -----------------------------------------------------------------------

    def action_graph_clicked(self, mouseClickEvent):
        self.clicked_coordinates = self.view_box.mapSceneToView(
            mouseClickEvent.scenePos()
        )
        if not self.bad_data_mode and self.beat_df is not None:
            if self.clicked_coordinates.x() < self.beat_df.iloc[0]["ts"]:
                return
            self.current_beat_index = self.beat_df[
                self.beat_df["ts"] >= self.clicked_coordinates.x()
            ].index[0]
            self.action_update_current_beat()
        else:
            if self.bad_data_markers is not None:
                self.graph.removeItem(self.bad_data_markers)
            self.bad_data_markers = None

            if not self.bad_start:
                self.bad_start = self.clicked_coordinates.x()
            else:
                self.bad_stop = self.clicked_coordinates.x()
                self.pushButton_Bad_Data.setStyleSheet("background-color: None")
                self.bad_data_mode = False
                self.bad_data_list.append([self.bad_start, self.bad_stop])
                self.action_update_bad_data_marks()

    def action_bad_data(self):
        self.bad_start = None
        self.bad_stop = None
        self.bad_data_mode = True
        self.pushButton_Bad_Data.setStyleSheet("background-color: orange")

    def action_update_bad_data_marks(self):
        if self.beat_df is not None:
            self.beat_df.loc[:, "bad_data"] = False
            for block in self.bad_data_list:
                self.beat_df.loc[
                    (self.beat_df["ts"] >= block[0]) & (self.beat_df["ts"] <= block[1]),
                    "bad_data",
                ] = True
            self.bad_beat_only_df = self.beat_df[self.beat_df["bad_data"] == True]
            self.bad_data_markers = self.add_plot(
                source=self.bad_beat_only_df,
                filt_source=self.bad_beat_only_df,
                time_column="ts",
                signal_column="bad_data",
                symbol="x",
                symbol_pen=(200, 100, 0),
                symbol_brush=(200, 100, 0),
                symbol_size=14,
            )

    # -----------------------------------------------------------------------
    # Beat / arrhythmia annotation (unchanged from original)
    # -----------------------------------------------------------------------

    def action_update_current_beat(self):
        if self.current_beat is not None:
            self.graph.removeItem(self.current_beat)
        self.current_beat = None
        if self.current_arrhythmia is not None:
            self.graph.removeItem(self.current_arrhythmia)
        self.current_arrhythmia = None

        if self.beat_df.iloc[self.current_beat_index]["annot_any_arrhythmia"] > 0:
            self.current_arrhythmia_index = self.arrhythmia_only_df[
                self.arrhythmia_only_df["ts"]
                == self.beat_df.iloc[self.current_beat_index]["ts"]
            ].index[0]
            self.current_arrhythmia = self.graph.plot(
                x=[self.beat_df.iloc[self.current_beat_index]["ts"]],
                y=[1.2],
                name="current arrhythmia",
                symbol="star",
                symbolBrush=(0, 0, 0),
                symbolPen=(0, 0, 0),
                symbolSize=14,
            )
        else:
            self.current_beat = self.graph.plot(
                x=[self.beat_df.iloc[self.current_beat_index]["ts"]],
                y=[1.2],
                name="current beat",
                symbol="star",
                symbolBrush=(0, 0, 0),
                symbolPen=(0, 0, 0),
                symbolSize=14,
            )

        self.categorize_beat_arrhythmias()
        self.update_graph()

    def categorize_beat_arrhythmias(self):
        annotation_columns = [i for i in self.beat_df.columns if i.startswith("annot")]
        category_list = [
            i
            for i in annotation_columns
            if self.beat_df.iloc[self.current_beat_index][i] > 0
        ]
        self.listWidget_assign_arrhyth.clear()
        self.listWidget_assign_arrhyth.addItems(category_list)

    def assign_arrhyth_category(self):
        self.beat_df.at[
            self.current_beat_index, self.comboBox_arrhyth_assign.currentText()
        ] = 2
        self.beat_df.at[self.current_beat_index, "any_arrhythmia"] = True
        self.arrhythmia_only_df = self.beat_df[
            self.beat_df.any_arrhythmia
        ].reset_index()

        if self.arrhythmia_markers is not None:
            self.graph.removeItem(self.arrhythmia_markers)
        self.arrhythmia_markers = None

        self.arrhythmia_markers = self.add_plot(
            source=self.arrhythmia_only_df,
            filt_source=self.arrhythmia_only_df,
            time_column="ts",
            signal_column="annot_any_arrhythmia",
            symbol="t1",
            symbol_pen=(0, 0, 0),
            symbol_brush=(255, 0, 0),
            symbol_size=12,
        )

        self.current_arrhythmia_index = self.arrhythmia_only_df[
            self.arrhythmia_only_df["ts"]
            == self.beat_df.iloc[self.current_beat_index]["ts"]
        ].index[0]

        self.update_graph()

    def action_next_arrhythmia(self):
        self.current_arrhythmia_index = min(
            self.current_arrhythmia_index + 1,
            self.arrhythmia_only_df.shape[0] - 1,
        )
        self.action_update_current_arrhythmia()

    def action_prev_arrhythmia(self):
        self.current_arrhythmia_index = max(self.current_arrhythmia_index - 1, 0)
        self.action_update_current_arrhythmia()

    def action_first_arrhythmia(self):
        self.current_arrhythmia_index = 0
        self.action_update_current_arrhythmia()

    def action_last_arrhythmia(self):
        self.current_arrhythmia_index = self.arrhythmia_only_df.shape[0] - 1
        self.action_update_current_arrhythmia()

    def action_confirm_arrhythmia(self):
        self.beat_df.loc[
            self.beat_df["ts"]
            == self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]["ts"],
            "annot_any_arrhythmia",
        ] = 2
        self.arrhythmia_only_df.loc[
            self.arrhythmia_only_df["ts"]
            == self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]["ts"],
            "annot_any_arrhythmia",
        ] = 2
        self.action_next_arrhythmia()

    def action_reject_arrhythmia(self):
        self.beat_df.loc[
            self.beat_df["ts"]
            == self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]["ts"],
            "annot_any_arrhythmia",
        ] = -1
        self.arrhythmia_only_df.loc[
            self.arrhythmia_only_df["ts"]
            == self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]["ts"],
            "annot_any_arrhythmia",
        ] = -1
        self.action_next_arrhythmia()

    def action_update_current_arrhythmia(self):
        if self.current_beat is not None:
            self.graph.removeItem(self.current_beat)
        self.current_beat = None
        if self.current_arrhythmia is not None:
            self.graph.removeItem(self.current_arrhythmia)
        self.current_arrhythmia = None

        self.current_arrhythmia = self.graph.plot(
            x=[self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]["ts"]],
            y=[1.2],
            name="current arrhythmia",
            symbol="star",
            symbolBrush=(0, 0, 0),
            symbolPen=(0, 0, 0),
            symbolSize=14,
        )

        self.doubleSpinBox_x_min.setValue(
            self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]["ts"]
            - self.doubleSpinBox_x_window.value() / 3
        )

        self.current_beat_index = self.beat_df[
            self.beat_df["ts"]
            == self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]["ts"]
        ].index[0]

        self.categorize_beat_arrhythmias()
        self.update_graph()

    # -----------------------------------------------------------------------
    # Report generation (original behaviour preserved)
    # -----------------------------------------------------------------------

    def action_set_output_dir(self):
        self.output_dir = QFileDialog.getExistingDirectory(
            caption="select path to save reports"
        )
        self.label_output_dir.setText(self.output_dir)

    def action_generate_report(self):
        if self.beat_df is None:
            print("no beat info - did you perform beat and arrhythmia detection?")
            return
        if self.output_dir is None:
            print("no output dir set")
            return

        output_path = os.path.join(
            self.output_dir,
            os.path.splitext(os.path.basename(self.current_filepath))[0] + ".xlsx",
        )

        bad_data_df = pandas.DataFrame(self.bad_data_list, columns=["start", "stop"])
        settings_df = pandas.DataFrame(
            {
                **self.beat_settings.__dict__,
                **self.arrhythmia_settings.__dict__,
                "main_version": __version__,
                "heartbeat_version": heartbeat_detection.__version__,
                "arrhythmia_version": arrhythmia_detection.__version__,
                "ml_version": ml_tools.__version__,
            },
            index=[0],
        )

        writer = pandas.ExcelWriter(output_path, engine="xlsxwriter")
        self.beat_df.to_excel(writer, sheet_name="beats", index=False)
        bad_data_df.to_excel(writer, sheet_name="bad_data_marks", index=False)
        settings_df.to_excel(writer, sheet_name="settings", index=False)
        writer.close()
        print("finished")
        self.textBrowser_Status.append(f"Report saved: {os.path.basename(output_path)}")

    def action_Edit_Settings(self):
        window = SettingsWindow(parent=self)
        window.exec()


# ---------------------------------------------------------------------------
# Settings window (unchanged from original)
# ---------------------------------------------------------------------------


class SettingsWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settingsOptions = {}
        self.parentFrame = parent
        self.setWindowTitle("ECG Analysis - Settings")

        outer_layout = QtWidgets.QVBoxLayout()
        inner_layout = QtWidgets.QHBoxLayout()

        beat_layout = QtWidgets.QFormLayout()
        beat_settings = parent.beat_settings
        beat_options = {}
        for k, v in beat_settings.__dict__.items():
            EntryWidget = FlexibleEntryWidget(value=v)
            beat_options[k] = EntryWidget
            beat_layout.addRow(k, EntryWidget.entry)

        arr_layout = QtWidgets.QFormLayout()
        arr_settings = parent.arrhythmia_settings
        arr_options = {}
        for k, v in arr_settings.__dict__.items():
            EntryWidget = FlexibleEntryWidget(value=v)
            arr_options[k] = EntryWidget
            arr_layout.addRow(k, EntryWidget.entry)

        self.beatSettingsOptions = beat_options
        self.arrSettingsOptions = arr_options

        inner_layout.addLayout(beat_layout)
        inner_layout.addLayout(arr_layout)

        self.button = QtWidgets.QPushButton("Update Settings")
        self.button.clicked.connect(self.updateSettings)

        outer_layout.addLayout(inner_layout)
        outer_layout.addWidget(self.button)
        self.setLayout(outer_layout)

    def updateSettings(self):
        for k, v in self.beatSettingsOptions.items():
            self.parentFrame.beat_settings.__dict__[k] = v.getValues()
        for k, v in self.arrSettingsOptions.items():
            self.parentFrame.arrhythmia_settings.__dict__[k] = v.getValues()
        self.close()


# ---------------------------------------------------------------------------
# Flexible entry widget (unchanged from original)
# ---------------------------------------------------------------------------


class FlexibleEntryWidget:
    def __init__(self, value):
        self.type = type(value)
        if self.type == type(None):
            self.entry = QtWidgets.QLineEdit()
        elif self.type == bool:
            self.entry = QtWidgets.QCheckBox()
            self.entry.setChecked(value)
        else:
            self.entry = QtWidgets.QLineEdit()
            self.entry.setText(str(value))

    def getValues(self):
        if self.type in [float, int]:
            value = self.entry.text()
            if value == "":
                return None
            if self.type == float:
                value = float(value)
            if self.type == int:
                value = int(value)
            return value
        elif self.type == bool:
            return self.entry.isChecked()
        else:
            value = self.entry.text()
            if value == "":
                return None
            return float(value)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    loader = QUiLoader()
    app = QtWidgets.QApplication(sys.argv)
    ui_file = QFile(os.path.join(os.path.dirname(__file__), "ecg_analysis_tool.ui"))
    ui = loader.load(ui_file)
    window = MainWindow(ui)
    window.version_info = {
        "main": __version__,
        "heartbeat_detection": heartbeat_detection.__version__,
        "arrhythmia_detection": arrhythmia_detection.__version__,
        "ml_tools": ml_tools.__version__,
        "pipeline": pipeline.__version__,
        "comparison": comparison.__version__,
    }
    }
    ui.show()
    app.exec()


if __name__ == "__main__":
    main()
