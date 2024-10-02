# -*- coding: utf-8 -*-

"""
ECG_ANALYSIS_TOOL
written by Christopher S Ward (C) 2024
"""

__version__ = "0.0.10"
# try:
from PyQt6 import QtWidgets, uic, QtCore
from PyQt6.QtWidgets import QFileDialog
from PyQt6.QtCore import Qt
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
    heartbeat_detection = importlib.import_module('modules.heartbeat_detection','modules')
    arrhythmia_detection = importlib.import_module('modules.arrhythmia_detection','modules')
    ml_tools = importlib.import_module('modules.ml_tools','modules')
except:
    print('use of relative import')
    heartbeat_detection = importlib.import_module('modules.heartbeat_detection','.modules')
    arrhythmia_detection = importlib.import_module('modules.arrhythmia_detection','.modules')
    ml_tools = importlib.import_module('modules.ml_tools','.modules')

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
        pcc_extract
    )
except:
    from .modules.signal_converters import (
        dsi_fp_matlab_extract, 
        adi_extract, 
        labchart_text_extract,
        pklgzip_extract,
        pcc_extract
    )
    
extractors = {
    'adi':{'module': adi_extract, 'ext':'.adicht'},
    'labchart_text':{'module': labchart_text_extract, 'ext':'.txt'},
    'dsi_fp_matlab':{'module': dsi_fp_matlab_extract, 'ext':'.mat'},
    'pklgzip':{'module': pklgzip_extract, 'ext':'.gzip'},
    'pcc':{'module':pcc_extract, 'ext':'.txt'}
}


# %% define functions
def gather_data(
    source,
    time_column,
    signal_column,
    filt_column,
    x_min,
    x_max,
    graph_width
    
):
    # print(f'time col: {time_column}, signal_col: {signal_column}')
    # print(source[signal_column])
    if filt_column:
        source = source[source[filt_column]]
    data_filter = (source[time_column]>=x_min) & \
        (source[time_column]<=x_max)
    x_val = source[time_column][data_filter]
    y_val = source[
        signal_column
        ][data_filter]
    
    
    if len(x_val)>graph_width * 4:
        downsample_factor = int(len(x_val)/graph_width/4)
        x_val=x_val[::downsample_factor]
        y_val=y_val[::downsample_factor]
    
    return list(x_val), list(y_val)

# %% setup the main window
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        uic.loadUi(
            os.path.join(
                os.path.dirname(__file__),"ecg_analysis_tool.ui"
            ), self
        )
        self.label_Title_and_Version.setText(f'ECG Analysis - {__version__}')
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
        self.known_time_columns = ['ts','time']
        self.attach_buttons()
        
        self.reset_gui()
        self.add_graph()
        

    def attach_buttons(self):
        # menu items
        self.actionOpen_Files.triggered.connect(self.action_Add_Files)
        self.actionExit.triggered.connect(QtWidgets.QApplication.instance().quit)
        self.actionBeat_Detection.triggered.connect(self.action_BeatDetection)
        self.actionArrhythmia_Analysis.triggered.connect(self.action_Arrhythmia_Analysis)
        self.actionQuality_Scoring.triggered.connect(self.action_Quality_Scoring)

        # gui buttons
        self.pushButton_Add_Files.clicked.connect(self.action_Add_Files)
        self.pushButton_Clear_Files.clicked.connect(self.action_Clear_Files)
        self.listWidget_Files.clicked.connect(self.action_update_selected_file)
        self.listWidget_Signals.clicked.connect(self.add_signal)
        self.pushButton_BeatDetection.clicked.connect(
            self.action_BeatDetection
        )
        self.pushButton_Arrhythmia_Analysis.clicked.connect(
            self.action_Arrhythmia_Analysis    
        )
        self.pushButton_Bad_Data.clicked.connect(
            self.action_bad_data
        )
        self.pushButton_assign_category.clicked.connect(
            self.assign_arrhyth_category
        )
        self.pushButton_start_of_file.clicked.connect(
            self.action_start_of_file    
        )
        self.pushButton_end_of_file.clicked.connect(
            self.action_end_of_file    
        )
        self.pushButton_Next_Arrhythmia.clicked.connect(
            self.action_next_arrhythmia
        )
        self.pushButton_Prev_Arrhythmia.clicked.connect(
            self.action_prev_arrhythmia
        )
        self.pushButton_First_Arrhythmia.clicked.connect(
            self.action_first_arrhythmia
        )
        self.pushButton_Last_Arrhythmia.clicked.connect(
            self.action_last_arrhythmia    
        )
        self.pushButton_next_window.clicked.connect(
            self.action_next_window    
        )
        self.pushButton_prev_window.clicked.connect(
            self.action_prev_window
        )
        self.pushButton_generate_report.clicked.connect(
            self.action_generate_report
        )
        self.pushButton_set_output_dir.clicked.connect(
            self.action_set_output_dir    
        )
        self.pushButton_zoom_in.clicked.connect(
            self.action_zoom_in
        )
        self.pushButton_zoom_out.clicked.connect(
            self.action_zoom_out
        )
        self.pushButton_Confirm_Arrhythmia.clicked.connect(
            self.action_confirm_arrhythmia    
        )
        self.pushButton_Reject_Arrhythmia.clicked.connect(
            self.action_reject_arrhythmia    
        )
        
        self.comboBox_time_column.currentTextChanged.connect(
            self.action_get_start_and_end_time
        )


        self.checkBox_auto_y.stateChanged.connect(self.update_graph)
        self.checkBox_plot_filtered.stateChanged.connect(self.update_graph)
        

        self.doubleSpinBox_filt_freq.valueChanged.connect(self.action_update_filtered_signals)
        self.doubleSpinBox_filt_order.valueChanged.connect(self.action_update_filtered_signals)

        self.action_set_arr_method()
        
    
        self.comboBox_arrhyth_assign.addItems(arrhythmia_detection.annot_arrhythmia_categories)

    
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
        self.graph = pyqtgraph.PlotWidget()
        self.legend = self.graph.addLegend()
        self.legend.setColumnCount(3)
        self.legend.setOffset([0.1,-0.1])
        self.verticalLayout_graph.addWidget(self.graph)
        self.graph.setXRange(
            self.doubleSpinBox_x_min.value(),
            self.doubleSpinBox_x_min.value()+self.doubleSpinBox_x_window.value()
        )
        # !!! todo - limit of x (time) to 999999999 maximum
        self.graph.setBackground('w')

        # add signal for mouse clicks
        self.view_box = self.graph.plotItem.vb
        self.graph.scene().sigMouseClicked.connect(self.action_graph_clicked)
        
        
        
    def reset_plot(self):
        print('resetting plot')
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

        if self.current_beat is not None:
            # print('current beat_marker already exists')
            self.graph.removeItem(self.current_beat)
        self.current_beat = None

        if self.bad_data_markers is not None:
            # print('bad_data_markers already exist')
            self.graph.removeItem(self.bad_data_markers)
        self.bad_data_markers = None


        
    def assign_arrhyth_category(self):
        self.beat_df.at[self.current_beat_index,self.comboBox_arrhyth_assign.currentText()] = 2
        self.beat_df.at[self.current_beat_index,'any_arrhythmia'] = True

        # print(self.beat_df)

        self.arrhythmia_only_df = self.beat_df[
            self.beat_df.any_arrhythmia
        ].reset_index()
        
        if self.arrhythmia_markers is not None:
            # print('arrhythmia_markers already exist')
            self.graph.removeItem(self.arrhythmia_markers)
        self.arrhythmia_markers = None

        self.arrhythmia_markers = self.add_plot(
            source = self.arrhythmia_only_df,
            filt_source = self.arrhythmia_only_df,
            time_column = 'ts',
            signal_column = 'annot_any_arrhythmia',
            symbol = 't1',
            symbol_pen = (0,0,0),
            symbol_brush = (255,0,0),
            symbol_size = 12
        )
        
        self.current_arrhythmia_index = self.arrhythmia_only_df[
                self.arrhythmia_only_df['ts']==self.beat_df.iloc[self.current_beat_index]['ts']
            ].index[0]
        
        
        self.update_graph()

    
    def action_graph_clicked(self,mouseClickEvent):
        self.clicked_coordinates = self.view_box.mapSceneToView(mouseClickEvent.scenePos())
        # print(f'clicked plot @ x:{self.clicked_coordinates.x()}, y:{self.clicked_coordinates.y()}')

        if not self.bad_data_mode and self.beat_df is not None:
            if self.clicked_coordinates.x()<self.beat_df.iloc[0]['ts']: return
            # get index of selected beat
            self.current_beat_index = self.beat_df[self.beat_df['ts']>=self.clicked_coordinates.x()].index[0]
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
                self.pushButton_Bad_Data.setStyleSheet('background-color: None')
                self.bad_data_mode = False
                self.bad_data_list.append([self.bad_start,self.bad_stop])
                # print(self.bad_data_list)
                self.action_update_bad_data_marks()


    
    def action_update_bad_data_marks(self):
        if self.beat_df is not None: # !!! need to also run if data marks placed before bead_df made
            # print(self.beat_df)
            self.beat_df.loc[:,'bad_data'] = False
            # print(self.beat_df)
            for block in self.bad_data_list:
                self.beat_df.loc[(self.beat_df['ts']>=block[0])&(self.beat_df['ts']<=block[1]),'bad_data'] = True
            self.bad_beat_only_df = self.beat_df[self.beat_df['bad_data']==True]
            # print(self.beat_df)
            # print(self.bad_beat_only_df)

            self.bad_data_markers = self.add_plot(
                source = self.bad_beat_only_df,
                filt_source = self.bad_beat_only_df,
                time_column = 'ts',
                signal_column = 'bad_data',
                symbol = 'x',
                symbol_pen = (200,100,0),
                symbol_brush = (200,100,0),
                symbol_size=14
            )



    def action_bad_data(self):
        self.bad_start = None
        self.bad_stop = None
        self.bad_data_mode = True
        self.pushButton_Bad_Data.setStyleSheet('background-color: orange')


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

        if self.beat_df.iloc[self.current_beat_index]['annot_any_arrhythmia'] > 0:
            self.current_arrhythmia_index = self.arrhythmia_only_df[
                self.arrhythmia_only_df['ts']==self.beat_df.iloc[self.current_beat_index]['ts']
            ].index[0]
            self.current_arrhythmia = self.graph.plot(
                x=[self.beat_df.iloc[self.current_beat_index]['ts']],
                y=[1.2],
                name='current arrhythmia',
                symbol='star',
                symbolBrush=(0,0,0),
                symbolPen=(0,0,0),
                symbolSize=14
            )

        else:

            self.current_beat = self.graph.plot(
                x=[self.beat_df.iloc[self.current_beat_index]['ts']],
                y=[1.2],
                name='current beat',
                symbol='star',
                symbolBrush=(0,0,0),
                symbolPen=(0,0,0),
                symbolSize=14
            )
        
        # # update timestamp range of graph
        # self.doubleSpinBox_x_min.setValue(
            # self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]['ts']  
            # - self.doubleSpinBox_x_window.value() / 3
        # )
        
        self.categorize_beat_arrhythmias()

        self.update_graph()


    def add_signal(self):
        # remove prior signal if present
        # print(f'line if {self.line}')
        if self.line is not None:
            # print('a line already exists')
            self.graph.removeItem(self.line)
        self.line = None
        
        self.reset_plot()
        
        pen = pyqtgraph.mkPen(
            "Blue",
            width=1,
            style=Qt.PenStyle.SolidLine
            )
    
        self.line = self.add_plot(
            pen = pen,
            source = self.data,
            filt_source = self.filtered_data,
            time_column = self.comboBox_time_column.currentText(),
            signal_column = self.listWidget_Signals.currentItem().text()
        )
        # print(f'line is now: {self.line}')



    def add_plot(
        self,
        pen = None,
        pen_width = 1,
        pen_color = None,
        symbol = None,
        symbol_brush = None,
        symbol_pen = None,
        symbol_size = 14,
        source = None,
        filt_source = None,
        filt_column = None,
        time_column = None,
        signal_column = None
    ):
        # if not pen:
        #     pen = {'pen':None}

        # if not symbol:
        #     symbol = {'symbol':None}
        
    
        x,y = gather_data(
            source,
            time_column,
            signal_column,
            filt_column,
            self.doubleSpinBox_x_min.value(),
            self.doubleSpinBox_x_min.value()+self.doubleSpinBox_x_window.value(),
            self.graph.width()
            )
        
        line = self.graph.plot(
            x=x,
            y=y,
            name=signal_column,
            pen=pen,
            symbol=symbol,
            symbolBrush=symbol_brush,
            symbolPen=symbol_pen,
            symbolSize=symbol_size
            )
        
        self.plotted[self.plotted_counter]={
            'line': line,
            'pen':pen,
            'pen_width':pen_width,
            'pen_color':pen_color,
            'symbol':symbol,
            'symbol_brush':symbol_brush,
            'symbol_pen':symbol_pen,
            'symbol_size':symbol_size,
            'name':signal_column,
            'source':source,
            'filt_source':filt_source,
            'filt_column':filt_column,
            'time':time_column
            }
        
        self.plotted_counter += 1
        self.update_graph()
        
        return line
        
    
    
    def update_graph(self):
        # redraw data

        for k,v in self.plotted.items():
            if self.checkBox_plot_filtered.isChecked():
                source = v['filt_source']
            else:
                source = v['source']
                
            x_val,y_val = gather_data(
                source,
                v['time'],
                v['name'],
                v['filt_column'],
                self.doubleSpinBox_x_min.value(),
                self.doubleSpinBox_x_min.value()+self.doubleSpinBox_x_window.value(),
                self.graph.width()
            )
                
            
            v['line'].setData(
                x=x_val,
                y=y_val,
                name=v['name'],
                pen=v['pen'],
                symbol=v['symbol']
                )

        # y axis scale
        if not self.checkBox_auto_y.isChecked():
            self.graph.setYRange(
                self.doubleSpinBox_y_min.value(),
                self.doubleSpinBox_y_max.value(),
                padding=0
                )
        else:
            self.graph.autoRange(padding=None)
        # x axis scale
        self.graph.setXRange(
            self.doubleSpinBox_x_min.value(),
            self.doubleSpinBox_x_min.value()+self.doubleSpinBox_x_window.value(),padding=0
            )
        
    
    def categorize_beat_arrhythmias(self):
        category_list = [
            i for i in arrhythmia_detection.annot_arrhythmia_categories
            if self.beat_df.iloc[self.current_beat_index][i] > 0
        ]
        self.listWidget_assign_arrhyth.clear()
        self.listWidget_assign_arrhyth.addItems(category_list)



    
    
    def action_zoom_in(self):
        self.doubleSpinBox_x_window.setValue(self.doubleSpinBox_x_window.value()/2)
        self.update_graph()
        
        
    def action_zoom_out(self):
        self.doubleSpinBox_x_window.setValue(self.doubleSpinBox_x_window.value()*2)
        self.update_graph()
    
    
    # actions for interface
    def action_Clear_Files(self):
        self.filepath_dict = {}
        self.listWidget_Files.clear()
          
    def action_Add_Files(self):
        for p in QFileDialog.getOpenFileNames(
            self,
            "Select ECG signal files"
        )[0]:
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
            i for i in extractors.values() 
            if i['ext'] == os.path.splitext(self.current_filepath)[1]
        ]
        
        self.data = None
        for i in extract_tools:
            if self.data is None:
                try:
                    self.data = i['module'].SASSI_extract(
                        self.current_filepath
                    )
                except:
                    print('unable to open - trying another extractor')
            
        if self.data is not None:
            # print('data opened')
            self.action_update_available_signals()
            
        self.reset_plot()

        # reset dataframes and containers to defaults as well
        # !!! might be better to make this a method and reuse for init
        self.beat_df = None
        self.bad_beat_only_df = None
        self.arrhythmia_only_df = None
        self.bad_data_list = []
        
        
        
    def action_update_filtered_signals(self):

        # !!! need to update logic flow for this
        self.filtered_data = pandas.DataFrame()
        
        sampling_frequency = 1/(
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
                    output='sos'
                    )
        

    def action_update_available_signals(self):
        self.listWidget_Signals.clear()
        self.listWidget_Signals.addItems(self.data.columns)
        
        
        self.comboBox_time_column.clear()
        self.comboBox_time_column.addItems(self.data.columns)
        
        self.action_update_time_column()
        
        
    def action_update_time_column(self):
        
        
        
        if any([c in self.known_time_columns for c in self.data.columns]) and self.comboBox_time_column.currentText() not in self.known_time_columns:    
            # print('time column available')
            for c in self.data.columns:
                if c in self.known_time_columns:
                    # print(f'time set as {c}')
                    self.comboBox_time_column.setCurrentText(c)
                    break
        elif self.comboBox_time_column.currentText() in self.known_time_columns:
            # print('time column found')
            pass
        else:
            # print('unknown time column')
            return
        self.action_get_start_and_end_time()
        
        
    def action_get_start_and_end_time(self):
        # print(f' time: {self.comboBox_time_column.currentText()}')
        self.end_of_file = max(
            self.data[self.comboBox_time_column.currentText()]
        )
        self.start_of_file = min(
            self.data[self.comboBox_time_column.currentText()]
        )
        self.action_update_filtered_signals()
    
    def action_set_arr_method(self):
        self.comboBox_arr_method.clear()
        self.comboBox_arr_method.addItems(["Heuristics", "Unsupervised"])

    def action_start_of_file(self):
        self.doubleSpinBox_x_min.setValue(
            self.start_of_file
        )
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
                self.doubleSpinBox_x_min.value() + self.doubleSpinBox_x_window.value()
            )
        )
        self.update_graph()
    
    def action_prev_window(self):
        self.doubleSpinBox_x_min.setValue(
            max(
                self.start_of_file,
                self.doubleSpinBox_x_min.value() - self.doubleSpinBox_x_window.value()
            )
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
            try:
                importlib.reload(heartbeat_detection)
            except:
                importlib.reload(heartbeat_detection)
        # self.voltage_column = self.listWidget_Signals.currentItem().text()
        
        # self.time_column = self.comboBox_time_column.currentText()
        # print(f'beat if {self.beat_markers}')
        if self.beat_markers is not None:
            # print('beat_markers already exist')
            self.graph.removeItem(self.beat_markers)
        self.beat_markers = None
        
        
        
        print(f'searching for beats in {self.voltage_column} by self.time_column')
        anesth_config = {
            'min_RR' : 60,
            'ecg_invert' : False,
            'ecg_filter' : True,
            'ecg_filt_order' : 2,
            'ecg_filt_cutoff' : 5,
            'abs_thresh' : None,
            'perc_thresh' : 97,
            
        }
        ecgenie_config = {
            'min_RR' : 60,
            'ecg_invert' : False,
            'ecg_filter' : True,
            'ecg_abs_value' : True,
            'ecg_filt_order' : 2,
            'ecg_filt_cutoff' : 5,
            'abs_thresh' : None,
            'perc_thresh' : 97,
        }
        if self.current_filepath[-6:] == 'adicht':
            config_to_use = anesth_config
        elif self.current_filepath[-3:] == 'txt':
            config_to_use = ecgenie_config
        




        self.beat_df = heartbeat_detection.beatcaller(
            self.data,
            time_column = self.comboBox_time_column.currentText(),
            voltage_column = self.listWidget_Signals.currentItem().text(),
            **config_to_use
        ).reset_index(drop=True)
        
        
        
        
        self.beat_markers = self.add_plot(
            source = self.beat_df,
            filt_source = self.beat_df,
            time_column = 'ts',
            signal_column = 'beats',
            symbol = 'o',
            symbol_pen = (0,0,0),
            symbol_brush = (0,255,0),
            symbol_size = 8
            )
        
        if self.bad_data_list !=[]:
            self.action_update_bad_data_marks()
        
        
        # print(self.beat_df)
        
        # !!! need to add integration for center/filetype configs
        
    def action_Quality_Scoring(self):
        print('quality scoring not yet implemented')
    
    def action_Arrhythmia_Analysis(self):
        if self.DEVMODE:
            try:
                importlib.reload(arrhythmia_detection)
            except:
                importlib.reload(arrhythmia_detection)

        # print(f'arrhyth if {self.arrhythmia_markers}')
        if self.arrhythmia_markers is not None:
            # print('arrhythmia_markers already exist')
            self.graph.removeItem(self.arrhythmia_markers)
        self.arrhythmia_markers = None
        
        if self.current_arrhythmia is not None:
            # print('arrhythmia_markers already exist')
            self.graph.removeItem(self.current_arrhythmia)
        self.current_arrhythmia = None
        
        self.beat_df = arrhythmia_detection.call_arrhythmias(
            self.beat_df,
            arrhythmia_detection.Settings(),
            arrhythmia_detection.arrhythmia_categories
            signals = self.data,
            selected_signal = self.listWidget_Signals.currentItem().text()
        )

        
        self.arrhythmia_only_df = self.beat_df[
            self.beat_df.any_arrhythmia
            ].reset_index()
        
        self.arrhythmia_markers = self.add_plot(
            source = self.arrhythmia_only_df,
            filt_source = self.arrhythmia_only_df,
            time_column = 'ts',
            signal_column = 'annot_any_arrhythmia',
            symbol = 't1',
            symbol_pen = (0,0,0),
            symbol_brush = (255,0,0),
            symbol_size = 12
        )
        
        self.current_arrhythmia_index = 0
        
        # print(self.arrhythmia_only_df)
        
        if self.arrhythmia_only_df.shape[0] != 0:
            
            self.current_arrhythmia = self.graph.plot(
                x=[self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]['ts']],
                y=[1.2],
                name='current arrhythmia',
                symbol='star',
                symbolBrush=(0,0,0),
                symbolPen=(0,0,0),
                symbolSize=14
            )
            
            
            
    def action_next_arrhythmia(self):
        self.current_arrhythmia_index = min(
            self.current_arrhythmia_index + 1,
            self.arrhythmia_only_df.shape[0] - 1
        )
        
        self.action_update_current_arrhythmia()
        
    
    
    def action_prev_arrhythmia(self):
        self.current_arrhythmia_index = max(
            self.current_arrhythmia_index - 1,
            0
        )
        
        self.action_update_current_arrhythmia()
        
    def action_confirm_arrhythmia(self):
        self.beat_df.loc[
            (
                self.beat_df['ts'] 
                == self.arrhythmia_only_df.iloc[
                    self.current_arrhythmia_index
                    ]['ts']
            ),
            'annot_any_arrhythmia'] = 2
        self.arrhythmia_only_df.loc[
            (
                self.arrhythmia_only_df['ts'] 
                == self.arrhythmia_only_df.iloc[
                    self.current_arrhythmia_index
                    ]['ts']
            ),
            'annot_any_arrhythmia'] = 2
        
        self.action_next_arrhythmia()
        
    def action_reject_arrhythmia(self):
        self.beat_df.loc[
            (
                self.beat_df['ts'] 
                == self.arrhythmia_only_df.iloc[
                    self.current_arrhythmia_index
                    ]['ts']
            ),
            'annot_any_arrhythmia'] = -1
        self.arrhythmia_only_df.loc[
            (
                self.arrhythmia_only_df['ts'] 
                == self.arrhythmia_only_df.iloc[
                    self.current_arrhythmia_index
                    ]['ts']
            ),
            'annot_any_arrhythmia'] = -1
        
        self.action_next_arrhythmia()
        
        
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
            x=[self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]['ts']],
            y=[1.2],
            name='current arrhythmia',
            symbol='star',
            symbolBrush=(0,0,0),
            symbolPen=(0,0,0),
            symbolSize=14
        )
        
        # update timestamp range of graph
        self.doubleSpinBox_x_min.setValue(
            self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]['ts']  
            - self.doubleSpinBox_x_window.value() / 3
        )
        
        self.current_beat_index = self.beat_df[
                self.beat_df['ts']==self.arrhythmia_only_df.iloc[self.current_arrhythmia_index]['ts']
            ].index[0]
        
        self.categorize_beat_arrhythmias()

        self.update_graph()
        
        
        
    def action_set_output_dir(self):
        # print('setting output dir')
        self.output_dir = QFileDialog.getExistingDirectory(
            caption='select path to save reports'
        )
        self.label_output_dir.setText(self.output_dir)
        
        
    
    def action_generate_report(self):
        print('generate_report')
        if self.beat_df is None:
            print('no beat info - did you perform beat and arrhtyhmia detection')
            return
        
        if self.output_dir is None:
            print('no output dir set')
            return
        
        output_path = os.path.join(
            self.output_dir,
            os.path.splitext(
                os.path.basename(self.current_filepath)
            )[0] + '.xlsx'
        )

        bad_data_df = pandas.DataFrame(self.bad_data_list,columns=['start','stop'])

        print(output_path)
        writer=pandas.ExcelWriter(
            output_path,
            engine='xlsxwriter'
        )
        self.beat_df.to_excel(writer, 'beats', index=False)
        bad_data_df.to_excel(writer, 'bad_data_marks', index=False)
        writer.close()
        print('finished')
        

def main():
    app = QtWidgets.QApplication(sys.argv)

    window = MainWindow()
    window.show()

    app.exec()

if __name__ == "__main__":
    main()