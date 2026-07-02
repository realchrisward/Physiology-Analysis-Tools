# -*- coding: utf-8 -*-

"""
ECG_ANALYSIS_TOOL
written by Christopher S Ward (C) 2024
"""

__version__ = "0.0.18"

# try:
from PySide6 import QtWidgets
from PySide6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog
from PySide6.QtCore import Qt, QFile, QThread, QObject, Signal
from PySide6.QtUiTools import QUiLoader

# except:
#     from PyQt5 import QtWidgets, uic, QtCore
#     from PyQt5.QtWidgets import QFileDialog
#     from PyQt5.QtCore import Qt
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
except ImportError:
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


import traceback
from pyqtgraph import PlotWidget, plot
import pyqtgraph
import pandas

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
except ImportError:
    from .modules.signal_converters import (
        dsi_fp_matlab_extract,
        adi_extract,
        labchart_text_extract,
        pklgzip_extract,
        pcc_extract,
    )

# edf_extract depends on the optional pyedflib package - import it separately
# so that a missing/broken pyedflib install disables EDF support instead of
# crashing the whole application on startup
try:
    try:
        from modules.signal_converters import edf_extract
    except ImportError:
        from .modules.signal_converters import edf_extract
except ImportError as e:
    edf_extract = None
    print(f"EDF support disabled - unable to import edf_extract ({e})")

extractors = {
    "adi": {"module": adi_extract, "ext": ".adicht"},
    "labchart_text": {"module": labchart_text_extract, "ext": ".txt"},
    "dsi_fp_matlab": {"module": dsi_fp_matlab_extract, "ext": ".mat"},
    "pklgzip": {"module": pklgzip_extract, "ext": ".gzip"},
    "pcc": {"module": pcc_extract, "ext": ".txt"},
}

if edf_extract is not None:
    extractors["edf"] = {"module": edf_extract, "ext": ".edf"}


# %% define functions
def gather_data(
    source, time_column, signal_column, filt_column, x_min, x_max, graph_width
):
    # print(f'time col: {time_column}, signal_col: {signal_column}')
    # print(source[signal_column])
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


class ArrhythmiaAnalysisWorker(QObject):
    """
    Runs arrhythmia_detection.call_arrhythmias() (which includes the
    PCA/DBSCAN unsupervised clustering) on a background thread so the UI
    doesn't freeze for the duration of the analysis.
    """

    finished = Signal(object)
    error = Signal(str)

    def __init__(self, beat_df, settings, signals, selected_signal, selected_time, arr_methods):
        super().__init__()
        self.beat_df = beat_df
        self.settings = settings
        self.signals = signals
        self.selected_signal = selected_signal
        self.selected_time = selected_time
        self.arr_methods = arr_methods

    def run(self):
        try:
            result = arrhythmia_detection.call_arrhythmias(
                self.beat_df,
                self.settings,
                signals=self.signals,
                selected_signal=self.selected_signal,
                selected_time=self.selected_time,
                arr_methods=self.arr_methods,
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# %% setup the main window
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, ui, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # uic.loadUi(
        #     os.path.join(os.path.dirname(__file__), "ecg_analysis_tool.ui"), self
        # )

        self.ui = ui

        # migrate ui children to parent level of class
        for att, val in ui.__dict__.items():
            setattr(self, att, val)

        self.ui.setWindowTitle("Physiology Analysis Tools")

        self.label_Title_and_Version.setText(f"ECG Analysis - {__version__}")
        self.data = None
        self.plotted_counter = 0
        self.beat_df = None
        self.bad_beat_only_df = None
        self.arrhythmia_only_df = None
        self.output_dir = None

        self.DEVMODE = True

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

        self.known_time_columns = ["ts", "time"]

        self.horizontalScrollBar_Time.setMinimum(0)
        self.horizontalScrollBar_Time.setMaximum(1000000)
        self.horizontalScrollBar_Time.setFocusPolicy(Qt.StrongFocus)

        self.attach_buttons()

        self.add_graph()
        self.reset_gui()

    def log_status(self, message):
        """Print message to console and append it to the status browser in the UI."""
        print(message)
        self.textBrowser_Status.append(message)

    def attach_buttons(self):
        # menu items
        self.actionOpen_Files.triggered.connect(self.action_Add_Files)
        self.actionSettings.triggered.connect(self.action_Edit_Settings)
        self.actionExit.triggered.connect(QtWidgets.QApplication.instance().quit)
        self.actionBeat_Detection.triggered.connect(self.action_BeatDetection)
        self.actionArrhythmia_Analysis.triggered.connect(
            self.action_Arrhythmia_Analysis
        )
        self.actionQuality_Scoring.triggered.connect(self.action_Quality_Scoring)
        self.actionAbout.triggered.connect(self.action_about)

        # gui buttons
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
        self.pushButton_Clear_Rejected.clicked.connect(
            self.action_clear_rejected_arrhythmias
        )

        # self.comboBox_time_column.currentTextChanged.connect(
        #     self.action_get_start_and_end_time
        # )

        self.doubleSpinBox_x_window.valueChanged.connect(self.update_graph)
        # self.doubleSpinBox_x_window.valueChanged.connect(self.action_get_start_and_end_time)

        self.doubleSpinBox_x_min.valueChanged.connect(self.update_graph)

        self.checkBox_auto_y.stateChanged.connect(self.update_graph)
        self.checkBox_plot_filtered.stateChanged.connect(self.update_graph)

        self.doubleSpinBox_filt_freq.valueChanged.connect(
            self.action_update_filtered_signals
        )
        self.doubleSpinBox_filt_order.valueChanged.connect(
            self.action_update_filtered_signals
        )

        self.action_set_arr_method()

        self.comboBox_arrhyth_assign.addItems(
            arrhythmia_detection.annot_arrhythmia_categories
        )

        self.horizontalScrollBar_Time.valueChanged.connect(self.action_scroll_time)

    def action_about(self):
        QMessageBox.information(
            None,
            "About Physiology Analysis Tools",
            "\n".join([f"{k} : {v}" for k, v in self.version_info.items()]),
        )

    def reset_gui(self):
        self.filepath_dict = {}
        self.current_filepath = None
        self.current_data = None
        self.doubleSpinBox_y_min.setValue(-3)
        self.doubleSpinBox_y_max.setValue(3)

        self.doubleSpinBox_x_min.setValue(0)
        self.doubleSpinBox_x_window.setValue(15)

        self.reset_plot()
        pass

    def add_graph(self):
        legend_hint = QtWidgets.QLabel(
            "Legend: click an item's line/marker swatch below to show/hide it on the graph"
        )
        legend_hint.setStyleSheet(
            "color: #6b7280; font-size: 8pt; font-style: italic; background: transparent;"
        )
        self.verticalLayout_graph.addWidget(legend_hint)

        self.graph = pyqtgraph.PlotWidget()
        self.legend = self.graph.addLegend(
            offset=(-10, 10),
            pen=pyqtgraph.mkPen(color=(207, 212, 220), width=1),
            brush=pyqtgraph.mkBrush(255, 255, 255, 235),
            labelTextColor=(31, 41, 55),
            labelTextSize="8pt",
        )
        self.legend.setColumnCount(3)
        self.verticalLayout_graph.addWidget(self.graph)
        self.graph.setXRange(
            self.doubleSpinBox_x_min.value(),
            self.doubleSpinBox_x_min.value() + self.doubleSpinBox_x_window.value(),
        )
        # !!! todo - limit of x (time) to 999999999 maximum
        self.graph.setBackground("w")

        # add signal for mouse clicks
        self.view_box = self.graph.plotItem.vb
        self.graph.scene().sigMouseClicked.connect(self.action_graph_clicked)

    def reset_plot(self):
        print("resetting plot")
        if self.line is not None:
            # print('a line already exists')
            self.graph.removeItem(self.line)
        self.line = None

        if self.beat_markers is not None:
            # print('beat_markers already exist')
            self.graph.removeItem(self.beat_markers)
        self.beat_markers = None

        if self.arrhythmia_markers is not None:
            # print('arrhythmia_markers already exist')
            self.graph.removeItem(self.arrhythmia_markers)
        self.arrhythmia_markers = None

        if self.current_arrhythmia is not None:
            # print('current arrhythmia_marker already exists')
            self.graph.removeItem(self.current_arrhythmia)
        self.current_arrhythmia = None

        self.pushButton_Clear_Rejected.setVisible(False)

        if self.current_beat is not None:
            # print('current beat_marker already exists')
            self.graph.removeItem(self.current_beat)
        self.current_beat = None

        if self.bad_data_markers is not None:
            # print('bad_data_markers already exist')
            self.graph.removeItem(self.bad_data_markers)
        self.bad_data_markers = None

    def assign_arrhyth_category(self):
        self.beat_df.at[
            self.current_beat_index, self.comboBox_arrhyth_assign.currentText()
        ] = 2
        self.beat_df.at[self.current_beat_index, "any_arrhythmia"] = True

        # print(self.beat_df)

        self.arrhythmia_only_df = self.beat_df[
            self.beat_df.any_arrhythmia
        ].reset_index()

        if self.arrhythmia_markers is not None:
            # print('arrhythmia_markers already exist')
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

    def action_graph_clicked(self, mouseClickEvent):
        self.clicked_coordinates = self.view_box.mapSceneToView(
            mouseClickEvent.scenePos()
        )
        # print(f'clicked plot @ x:{self.clicked_coordinates.x()}, y:{self.clicked_coordinates.y()}')

        if not self.bad_data_mode and self.beat_df is not None:
            if self.clicked_coordinates.x() < self.beat_df.iloc[0]["ts"]:
                return
            # get index of selected beat
            self.current_beat_index = self.beat_df[
                self.beat_df["ts"] >= self.clicked_coordinates.x()
            ].index[0]
            # print(self.beat_df[self.beat_df['ts']>=self.clicked_coordinates.x()]['ts'])
            # print(self.current_beat_index)
            # print(self.beat_df.iloc[self.current_beat_index])
            # update image
            self.action_update_current_beat()
        else:
            # clear old bad data plot
            if self.bad_data_markers is not None:
                # print('bad_data_markers already exist')
                self.graph.removeItem(self.bad_data_markers)
            self.bad_data_markers = None

            if not self.bad_start:
                self.bad_start = self.clicked_coordinates.x()
            else:
                self.bad_stop = self.clicked_coordinates.x()
                self.pushButton_Bad_Data.setStyleSheet("background-color: None")
                self.bad_data_mode = False
                self.bad_data_list.append([self.bad_start, self.bad_stop])
                # print(self.bad_data_list)
                self.action_update_bad_data_marks()

    def action_update_bad_data_marks(self):
        if (
            self.beat_df is not None
        ):  # !!! need to also run if data marks placed before bead_df made
            # print(self.beat_df)
            self.beat_df.loc[:, "bad_data"] = False
            # print(self.beat_df)
            for block in self.bad_data_list:
                self.beat_df.loc[
                    (self.beat_df["ts"] >= block[0]) & (self.beat_df["ts"] <= block[1]),
                    "bad_data",
                ] = True
            self.bad_beat_only_df = self.beat_df[self.beat_df["bad_data"] == True]
            # print(self.beat_df)
            # print(self.bad_beat_only_df)

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

    def action_bad_data(self):
        self.bad_start = None
        self.bad_stop = None
        self.bad_data_mode = True
        self.pushButton_Bad_Data.setStyleSheet("background-color: orange")

    def action_update_current_beat(self):
        # update timestamp of marker
        if self.current_beat is not None:
            # print('beat_markers already exist')
            self.graph.removeItem(self.current_beat)
        self.current_beat = None
        if self.current_arrhythmia is not None:
            # print('arrythmia markers already exist')
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

        # # update timestamp range of graph
        # self.doubleSpinBox_x_min.setValue(
        # self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]['ts']
        # - self.doubleSpinBox_x_window.value() / 3
        # )

        self.categorize_beat_arrhythmias()

        self.update_graph()

    def add_signal(self):
        # update timing info
        self.action_get_start_and_end_time()

        # remove prior signal if present
        # print(f'line if {self.line}')
        if self.line is not None:
            # print('a line already exists')
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
        # print(f'line is now: {self.line}')

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
        # if not pen:
        #     pen = {'pen':None}

        # if not symbol:
        #     symbol = {'symbol':None}

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
        # redraw data

        for k, v in self.plotted.items():
            if self.checkBox_plot_filtered.isChecked():
                source = v["filt_source"]
            else:
                source = v["source"]

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

        # y axis scale
        if not self.checkBox_auto_y.isChecked():
            self.graph.setYRange(
                self.doubleSpinBox_y_min.value(),
                self.doubleSpinBox_y_max.value(),
                padding=0,
            )
        else:
            self.graph.autoRange(padding=None)
        # x axis scale
        self.graph.setXRange(
            self.doubleSpinBox_x_min.value(),
            self.doubleSpinBox_x_min.value() + self.doubleSpinBox_x_window.value(),
            padding=0,
        )

        # adjust slider if needed
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

    def categorize_beat_arrhythmias(self):
        annotation_columns = [i for i in self.beat_df.columns if i.startswith("annot")]
        category_list = [
            i
            for i in annotation_columns
            if self.beat_df.iloc[self.current_beat_index][i] > 0
        ]
        self.listWidget_assign_arrhyth.clear()
        self.listWidget_assign_arrhyth.addItems(category_list)

    def action_zoom_in(self):
        self.doubleSpinBox_x_window.setValue(self.doubleSpinBox_x_window.value() / 2)
        self.update_graph()

    def action_zoom_out(self):
        self.doubleSpinBox_x_window.setValue(self.doubleSpinBox_x_window.value() * 2)
        self.update_graph()

    # actions for interface
    def action_Clear_Files(self):
        self.filepath_dict = {}
        self.listWidget_Files.clear()

    def action_Add_Files(self):
        for p in QFileDialog.getOpenFileNames(self, "Select ECG signal files")[0]:
            self.filepath_dict[os.path.basename(p)] = p

        # print(self.filepath_dict)
        self.listWidget_Files.clear()
        self.listWidget_Files.addItems(self.filepath_dict)

    def action_update_selected_file(self):
        self.current_filepath = self.filepath_dict[
            self.listWidget_Files.currentItem().text()
        ]
        # print(f'now working on file: {self.current_filepath}')

        extract_tools = [
            i
            for i in extractors.values()
            if i["ext"] == os.path.splitext(self.current_filepath)[1]
        ]

        if not extract_tools:
            self.log_status(
                f"No extractor available for file type "
                f"'{os.path.splitext(self.current_filepath)[1]}': "
                f"{os.path.basename(self.current_filepath)}"
            )

        self.data = None
        for i in extract_tools:
            if self.data is None:
                try:
                    self.data = i["module"].SASSI_extract(self.current_filepath)
                except Exception as e:
                    self.log_status(
                        f"unable to open with {i['module'].__name__} - "
                        f"trying another extractor ({e})"
                    )

        if self.data is not None:
            self.log_status(f"Loaded file: {os.path.basename(self.current_filepath)}")
            self.action_update_available_signals()
        else:
            self.log_status(
                f"Failed to load file: {os.path.basename(self.current_filepath)}"
            )

        self.reset_plot()

        # reset dataframes and containers to defaults as well
        # !!! might be better to make this a method and reuse for init
        self.beat_df = None
        self.bad_beat_only_df = None
        self.arrhythmia_only_df = None
        self.bad_data_list = []

    def action_update_filtered_signals(self):
        if self.data is not None and self.comboBox_time_column.currentText() != '':
            # !!! need to update logic flow for this
            # for some reason - comboBox_time_column is an empty string until the first 
            # signal channel is selected - changing that behavior would be ideal, until then
            # the above logic check ensures that a time column is set before running the code
            # of this method... which doesn't cause a crash, but does raise an error.
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
            print('time column available')
            for c in self.data.columns:
                if c in self.known_time_columns:
                    print(f'time set as {c}')
                    self.comboBox_time_column.setCurrentText(c)
                    break
        elif self.comboBox_time_column.currentText() in self.known_time_columns:
            print('time column found')
            pass
        else:
            print('unknown time column')
            return
        # self.action_get_start_and_end_time()

    def action_get_start_and_end_time(self):
        print(f' time: {self.comboBox_time_column.currentText()}')
        self.end_of_file = max(self.data[self.comboBox_time_column.currentText()])
        self.start_of_file = min(self.data[self.comboBox_time_column.currentText()])

        self.horizontalScrollBar_Time.setPageStep(
            int(
                (
                self.doubleSpinBox_x_window.value()/(self.end_of_file-self.start_of_file)
                )*1000000*0.9
            )
        )
        
        self.horizontalScrollBar_Time.setSingleStep(
            int(
                (
                self.doubleSpinBox_x_window.value()/(self.end_of_file-self.start_of_file)
                )*1000000*0.05
            )
        )

        print(f'step size:{self.horizontalScrollBar_Time.singleStep()}')



        self.action_update_filtered_signals()

    def action_set_arr_method(self):
        self.comboBox_arr_method.clear()
        self.comboBox_arr_method.addItems(["Heuristics", "Unsupervised", "Both"])

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

    # def action_Reset_Beats(self):
    #     if self.beat_markers is not None:
    #         print('beat_markers already exist')
    #         self.graph.removeItem(self.beat_markers)
    #     self.beat_markers = None
    #     if self.arrhythmia_markers is not None:
    #         print('arrhythmia_markers already exist')
    #         self.graph.removeItem(self.arrhythmia_markers)
    #     self.arrhythmia_markers = None

    def action_BeatDetection(self):
        if self.DEVMODE:
            importlib.reload(heartbeat_detection)
        # self.voltage_column = self.listWidget_Signals.currentItem().text()

        # self.time_column = self.comboBox_time_column.currentText()
        # print(f'beat if {self.beat_markers}')
        if self.beat_markers is not None:
            # print('beat_markers already exist')
            self.graph.removeItem(self.beat_markers)
        self.beat_markers = None

        time_column = self.comboBox_time_column.currentText()
        voltage_column = self.listWidget_Signals.currentItem().text()
        self.log_status(f"Searching for beats in {voltage_column} by {time_column}")

        self.beat_df = heartbeat_detection.beatcaller(
            self.data,
            time_column=time_column,
            voltage_column=voltage_column,
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

        if self.bad_data_list != []:
            self.action_update_bad_data_marks()

        beat_count = self.beat_df.shape[0]
        if beat_count > 0:
            mean_hr = self.beat_df["HR"].mean()
            duration = self.beat_df["ts"].iloc[-1] - self.beat_df["ts"].iloc[0]
            self.log_status(
                f"Beat detection complete: {beat_count} beats found | "
                f"Mean HR: {mean_hr:.0f} bpm | Duration: {duration:.1f}s"
            )
        else:
            self.log_status("Beat detection complete: 0 beats found")

        # !!! need to add integration for center/filetype configs

    def action_Quality_Scoring(self):
        self.log_status("Quality scoring is not yet implemented")

    def action_Arrhythmia_Analysis(self):
        if self.DEVMODE:
            importlib.reload(arrhythmia_detection)

        if self.beat_df is None:
            self.log_status("Run beat detection before arrhythmia analysis")
            return

        # print(f'arrhyth if {self.arrhythmia_markers}')
        if self.arrhythmia_markers is not None:
            # print('arrhythmia_markers already exist')
            self.graph.removeItem(self.arrhythmia_markers)
        self.arrhythmia_markers = None

        if self.current_arrhythmia is not None:
            # print('arrhythmia_markers already exist')
            self.graph.removeItem(self.current_arrhythmia)
        self.current_arrhythmia = None

        self.pushButton_Clear_Rejected.setVisible(False)

        self.log_status("Running arrhythmia analysis...")

        # progress indicator - unsupervised (PCA/DBSCAN) analysis can take
        # 10+ seconds and would otherwise freeze the UI, so run it on a
        # background thread and show an indeterminate progress dialog
        self.arrhythmia_progress = QProgressDialog(
            "Running arrhythmia analysis "
            "(this may take a moment for unsupervised clustering)...",
            None,
            0,
            0,
            self,
        )
        self.arrhythmia_progress.setWindowTitle("Arrhythmia Analysis")
        self.arrhythmia_progress.setWindowModality(Qt.WindowModal)
        self.arrhythmia_progress.setMinimumDuration(0)
        self.arrhythmia_progress.setCancelButton(None)
        self.arrhythmia_progress.show()

        self.pushButton_Arrhythmia_Analysis.setEnabled(False)
        self.pushButton_BeatDetection.setEnabled(False)

        self._arrhythmia_thread = QThread(self)
        self._arrhythmia_worker = ArrhythmiaAnalysisWorker(
            self.beat_df.copy(),
            self.arrhythmia_settings,
            self.filtered_data,
            self.listWidget_Signals.currentItem().text(),
            self.comboBox_time_column.currentText(),
            self.comboBox_arr_method.currentText(),
        )
        self._arrhythmia_worker.moveToThread(self._arrhythmia_thread)
        self._arrhythmia_thread.started.connect(self._arrhythmia_worker.run)
        self._arrhythmia_worker.finished.connect(self._on_arrhythmia_analysis_finished)
        self._arrhythmia_worker.error.connect(self._on_arrhythmia_analysis_error)
        self._arrhythmia_worker.finished.connect(self._arrhythmia_thread.quit)
        self._arrhythmia_worker.error.connect(self._arrhythmia_thread.quit)
        self._arrhythmia_thread.finished.connect(self._arrhythmia_worker.deleteLater)
        self._arrhythmia_thread.finished.connect(self._arrhythmia_thread.deleteLater)
        self._arrhythmia_thread.start()

    def _cleanup_arrhythmia_progress(self):
        self.arrhythmia_progress.close()
        self.pushButton_Arrhythmia_Analysis.setEnabled(True)
        self.pushButton_BeatDetection.setEnabled(True)

    def _on_arrhythmia_analysis_finished(self, result_df):
        self._cleanup_arrhythmia_progress()

        self.beat_df = result_df

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

        # print(self.arrhythmia_only_df)

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

        self.log_status(
            f"Arrhythmia analysis complete: {self.arrhythmia_only_df.shape[0]} "
            f"arrhythmias flagged out of {self.beat_df.shape[0]} beats"
        )

    def _on_arrhythmia_analysis_error(self, message):
        self._cleanup_arrhythmia_progress()
        self.log_status(f"Arrhythmia analysis failed: {message}")
        QMessageBox.critical(self, "Arrhythmia Analysis Error", message)

    def action_next_arrhythmia(self):
        self.current_arrhythmia_index = min(
            self.current_arrhythmia_index + 1, self.arrhythmia_only_df.shape[0] - 1
        )

        self.action_update_current_arrhythmia()

    def action_prev_arrhythmia(self):
        self.current_arrhythmia_index = max(self.current_arrhythmia_index - 1, 0)

        self.action_update_current_arrhythmia()

    def action_confirm_arrhythmia(self):
        self.beat_df.loc[
            (
                self.beat_df["ts"]
                == self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]["ts"]
            ),
            "annot_any_arrhythmia",
        ] = 2
        self.arrhythmia_only_df.loc[
            (
                self.arrhythmia_only_df["ts"]
                == self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]["ts"]
            ),
            "annot_any_arrhythmia",
        ] = 2

        self.action_next_arrhythmia()

    def action_reject_arrhythmia(self):
        self.beat_df.loc[
            (
                self.beat_df["ts"]
                == self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]["ts"]
            ),
            "annot_any_arrhythmia",
        ] = -1
        self.arrhythmia_only_df.loc[
            (
                self.arrhythmia_only_df["ts"]
                == self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]["ts"]
            ),
            "annot_any_arrhythmia",
        ] = -1

        # a rejected marker now exists on the plot - let the user clear it/redraw
        self.pushButton_Clear_Rejected.setVisible(True)

        self.action_next_arrhythmia()

    def action_clear_rejected_arrhythmias(self):
        """
        Remove rejected (annot_any_arrhythmia == -1) markers from the displayed
        arrhythmia set and redraw. beat_df itself keeps the rejection so it is
        still recorded in the exported report.
        """
        if self.arrhythmia_only_df is None or self.arrhythmia_only_df.shape[0] == 0:
            self.pushButton_Clear_Rejected.setVisible(False)
            return

        current_ts = None
        if self.current_arrhythmia_index < self.arrhythmia_only_df.shape[0]:
            current_ts = self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]["ts"]

        rejected_count = (self.arrhythmia_only_df["annot_any_arrhythmia"] == -1).sum()

        self.arrhythmia_only_df = self.arrhythmia_only_df[
            self.arrhythmia_only_df["annot_any_arrhythmia"] != -1
        ].reset_index(drop=True)

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

        if self.current_arrhythmia is not None:
            self.graph.removeItem(self.current_arrhythmia)
        self.current_arrhythmia = None

        if self.arrhythmia_only_df.shape[0] == 0:
            self.current_arrhythmia_index = 0
        else:
            matches = (
                self.arrhythmia_only_df[self.arrhythmia_only_df["ts"] >= current_ts].index
                if current_ts is not None
                else []
            )
            if len(matches) > 0:
                self.current_arrhythmia_index = matches[0]
            else:
                self.current_arrhythmia_index = self.arrhythmia_only_df.shape[0] - 1
            self.action_update_current_arrhythmia()

        self.pushButton_Clear_Rejected.setVisible(False)
        self.log_status(
            f"Removed {rejected_count} rejected arrhythmia marker(s) - "
            f"{self.arrhythmia_only_df.shape[0]} remaining"
        )

    def action_first_arrhythmia(self):
        self.current_arrhythmia_index = 0

        self.action_update_current_arrhythmia()

    def action_last_arrhythmia(self):
        self.current_arrhythmia_index = self.arrhythmia_only_df.shape[0] - 1

        self.action_update_current_arrhythmia()

    def action_update_current_arrhythmia(self):
        # update timestamp of marker
        if self.current_beat is not None:
            # print('beat_markers already exist')
            self.graph.removeItem(self.current_beat)
        self.current_beat = None
        if self.current_arrhythmia is not None:
            # print('arrythmia markers already exist')
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

        # update timestamp range of graph
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

    def action_set_output_dir(self):
        # print('setting output dir')
        self.output_dir = QFileDialog.getExistingDirectory(
            caption="select path to save reports"
        )
        self.label_output_dir.setText(self.output_dir)

    def action_generate_report(self):
        self.log_status("Generating report...")
        if self.beat_df is None:
            self.log_status(
                "No beat info - did you perform beat and arrhythmia detection?"
            )
            return

        if self.output_dir is None:
            self.log_status("No output directory set")
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
                'main_version': __version__,
                'heartbeat_version': heartbeat_detection.__version__,
                'arrhythmia_version': arrhythmia_detection.__version__,
                'ml_version':ml_tools.__version__
            },
            index=[0]
        )

        writer = pandas.ExcelWriter(output_path, engine="xlsxwriter")
        self.beat_df.to_excel(writer, sheet_name="beats", index=False)
        bad_data_df.to_excel(writer, sheet_name="bad_data_marks", index=False)
        settings_df.to_excel(writer, sheet_name="settings", index=False)
        writer.close()
        self.log_status(f"Report saved: {output_path}")

    def action_Edit_Settings(self):
        window = SettingsWindow(parent=self)
        window.exec()


# tooltip text shown for each setting field in the Settings dialog
SETTINGS_TOOLTIPS = {
    # heartbeat_detection.Settings
    "min_RR": "Minimum inter-beat interval, in milliseconds. Limits the "
              "maximum detectable heart rate (e.g. 60ms allows up to ~1000 bpm).",
    "ecg_invert": "Flip (invert) the ECG signal polarity before peak detection.",
    "ecg_filter": "Apply a Butterworth high-pass filter to remove baseline "
                  "wander before peak detection.",
    "ecg_filt_order": "Order of the Butterworth high-pass filter.",
    "ecg_filt_cutoff": "High-pass filter cutoff frequency, in Hz.",
    "abs_thresh": "Absolute voltage threshold for R-peak detection. Overrides "
                  "perc_thresh when set; leave blank to use perc_thresh instead.",
    "perc_thresh": "Percentile of the signal amplitude used as the R-peak "
                   "detection threshold (e.g. 97 = top 3% of the signal).",
    # arrhythmia_detection.Settings
    "bradycardia_absolute_hr": "Heart rate (bpm) below which a beat is "
                               "flagged as bradycardia.",
    "tachycardia_absolute_hr": "Heart rate (bpm) above which a beat is "
                               "flagged as tachycardia.",
    "skipped_beat_multiple_rr": "A beat is flagged as a skipped beat when its "
                                "RR interval exceeds this multiple of the "
                                "local average RR interval.",
    "premature_beat_multiple_rr": "A beat is flagged as premature when its RR "
                                  "interval is below this multiple of the "
                                  "local average RR interval.",
    "window_size": "Number of samples per beat epoch used for PCA-based "
                   "shape clustering (unsupervised method).",
    "eps": "DBSCAN neighborhood radius (in PCA space) used to group similar "
           "beat shapes (unsupervised method).",
    "min_samples": "Minimum number of beats required to form a DBSCAN "
                   "cluster of 'normal' beat shapes (unsupervised method).",
}


class SettingsWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settingsOptions = {}
        self.parentFrame = parent
        self.setWindowTitle("ECG Analysis - Settings")

        outer_layout = QtWidgets.QVBoxLayout()
        inner_layout = QtWidgets.QHBoxLayout()

        # Create grouped layout for heartbeat detection

        beat_group = QtWidgets.QGroupBox("Beat Detection Settings")
        beat_layout = QtWidgets.QFormLayout()
        beat_settings = parent.beat_settings
        beat_options = {}

        for k, v in beat_settings.__dict__.items():
            EntryWidget = FlexibleEntryWidget(value=v)
            beat_options[k] = EntryWidget
            tooltip = SETTINGS_TOOLTIPS.get(k, "")
            label = QtWidgets.QLabel(k)
            label.setToolTip(tooltip)
            EntryWidget.entry.setToolTip(tooltip)
            beat_layout.addRow(label, EntryWidget.entry)

        beat_group.setLayout(beat_layout)

        # Create grouped layout for arrhythmia detection

        arr_group = QtWidgets.QGroupBox("Arrhythmia Detection Settings")
        arr_layout = QtWidgets.QFormLayout()

        arr_settings = parent.arrhythmia_settings

        arr_options = {}

        for k, v in arr_settings.__dict__.items():

            EntryWidget = FlexibleEntryWidget(value=v)
            arr_options[k] = EntryWidget
            tooltip = SETTINGS_TOOLTIPS.get(k, "")
            label = QtWidgets.QLabel(k)
            label.setToolTip(tooltip)
            EntryWidget.entry.setToolTip(tooltip)
            arr_layout.addRow(label, EntryWidget.entry)

        arr_group.setLayout(arr_layout)

        self.beatSettingsOptions = beat_options
        self.arrSettingsOptions = arr_options

        inner_layout.addWidget(beat_group)
        inner_layout.addWidget(arr_group)

        self.button = QtWidgets.QPushButton("Update Settings")
        self.button.clicked.connect(self.updateSettings)

        self.restoreDefaultsButton = QtWidgets.QPushButton("Restore Defaults")
        self.restoreDefaultsButton.clicked.connect(self.restoreDefaults)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addWidget(self.restoreDefaultsButton)
        button_layout.addWidget(self.button)

        outer_layout.addLayout(inner_layout)
        outer_layout.addLayout(button_layout)
        self.setLayout(outer_layout)

    def updateSettings(self):

        for k, v in self.beatSettingsOptions.items():
            self.parentFrame.beat_settings.__dict__[k] = v.getValues()

        for k, v in self.arrSettingsOptions.items():
            self.parentFrame.arrhythmia_settings.__dict__[k] = v.getValues()

        self.close()

    def restoreDefaults(self):
        """Reset the dialog's fields (not yet applied) to factory defaults."""
        default_beat_settings = heartbeat_detection.Settings()
        default_arr_settings = arrhythmia_detection.Settings()

        for k, widget in self.beatSettingsOptions.items():
            widget.setValue(getattr(default_beat_settings, k))

        for k, widget in self.arrSettingsOptions.items():
            widget.setValue(getattr(default_arr_settings, k))


class FlexibleEntryWidget:
    """
    Class to allow for a flexible 'data entry' widget that will adjust the type depending on the input data type
    This allows for easier building of layouts where the form and format of the input is not known/will change
    """

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
            else:
                if self.type == float:
                    value = float(value)
                if self.type == int:
                    value = int(value)

            return value

        elif self.type == bool:
            value = self.entry.isChecked()
            return value

        elif self.type == str:
            value = self.entry.text()
            return value if value != "" else None

        else:
            # value's original type was None (a nullable numeric setting, e.g.
            # abs_thresh) - try to parse what the user typed as a float, but
            # fall back to the raw string instead of crashing on non-numeric
            # input
            value = self.entry.text()

            if value == "":
                return None

            try:
                value = float(value)
            except ValueError:
                pass

            return value

    def setValue(self, value):
        """Set the displayed value, used by 'Restore Defaults'."""
        if self.type == bool:
            self.entry.setChecked(bool(value))
        else:
            self.entry.setText("" if value is None else str(value))


# light, flat, "modern web-UI" theme - applied at the QApplication level so it
# covers the main window, the Settings dialog, and any QMessageBox/QProgressDialog.
# Deliberately leaves the pyqtgraph plot area alone (it manages its own
# background via setBackground("w") and draws directly with QPainter, not QSS).
APP_STYLESHEET = """
QWidget {
    background-color: #f5f6f8;
    color: #1f2937;
    font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    font-size: 9.5pt;
}

QPushButton {
    background-color: #ffffff;
    color: #1f2937;
    border: 1px solid #cfd4dc;
    border-radius: 6px;
    padding: 3px 5px;
}

QPushButton:hover {
    background-color: #eef2ff;
    border: 1px solid #6366f1;
}

QPushButton:pressed {
    background-color: #e0e7ff;
}

QPushButton:disabled {
    background-color: #f1f2f4;
    color: #9ca3af;
    border: 1px solid #e5e7eb;
}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #ffffff;
    border: 1px solid #cfd4dc;
    border-radius: 4px;
    padding: 2px 4px;
    selection-background-color: #6366f1;
    selection-color: #ffffff;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #6366f1;
}

QListWidget, QTextBrowser {
    background-color: #ffffff;
    border: 1px solid #cfd4dc;
    border-radius: 6px;
}

QListWidget::item:selected {
    background-color: #6366f1;
    color: #ffffff;
}

QCheckBox {
    spacing: 6px;
}

QGroupBox {
    border: 1px solid #d7dbe0;
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 8px;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #374151;
}

QMenuBar {
    background-color: #ffffff;
    border-bottom: 1px solid #e5e7eb;
}

QMenuBar::item:selected {
    background-color: #eef2ff;
}

QMenu {
    background-color: #ffffff;
    border: 1px solid #cfd4dc;
}

QMenu::item:selected {
    background-color: #eef2ff;
}

QStatusBar {
    background-color: #ffffff;
    border-top: 1px solid #e5e7eb;
}

QLabel {
    color: #374151;
    background-color: transparent;
}

QLabel#label_Title_and_Version {
    color: #4338ca;
    font-size: 13pt;
    font-weight: 700;
}

/* primary workflow actions get an accent color so they stand out from
   secondary/navigation buttons */
QPushButton#pushButton_BeatDetection,
QPushButton#pushButton_Arrhythmia_Analysis,
QPushButton#pushButton_generate_report {
    background-color: #4f46e5;
    color: #ffffff;
    border: 1px solid #4338ca;
    font-weight: 600;
}

QPushButton#pushButton_BeatDetection:hover,
QPushButton#pushButton_Arrhythmia_Analysis:hover,
QPushButton#pushButton_generate_report:hover {
    background-color: #4338ca;
}

QPushButton#pushButton_BeatDetection:pressed,
QPushButton#pushButton_Arrhythmia_Analysis:pressed,
QPushButton#pushButton_generate_report:pressed {
    background-color: #3730a3;
}

QPushButton#pushButton_BeatDetection:disabled,
QPushButton#pushButton_Arrhythmia_Analysis:disabled,
QPushButton#pushButton_generate_report:disabled {
    background-color: #c7d2fe;
    color: #eef2ff;
    border: 1px solid #c7d2fe;
}
"""


def main():

    loader = QUiLoader()
    print("1")
    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)
    print("2")
    ui_file = QFile(os.path.join(os.path.dirname(__file__), "ecg_analysis_tool.ui"))
    print("3")
    ui = loader.load(ui_file)
    print("4")

    window = MainWindow(ui)

    window.version_info = {
        "main": __version__,
        "heartbeat_detection": heartbeat_detection.__version__,
        "arrhythmia_detection": arrhythmia_detection.__version__,
        "ml_tools": ml_tools.__version__,
    }

    ui.show()

    app.exec()


if __name__ == "__main__":
    main()
