# -*- coding: utf-8 -*-

"""
ECG_ANALYSIS_TOOL
written by Christopher S Ward (C) 2024
"""

__version__ = "0.0.1"
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
from modules import heartbeat_detection
import traceback
from pyqtgraph import PlotWidget, plot
import pyqtgraph

from modules.signal_converters import (
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
    x_min,
    x_max,
    graph_width
    
):
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
        uic.loadUi("ecg_analysis_tool.ui",self)
        self.label_Title_and_Version.setText(f'ECG Analysis - {__version__}')
        self.attach_buttons()
        
        self.reset_gui()
        self.add_graph()
        self.plotted_counter = 0
        self.time_column = None
        self.voltage_column = None
        self.line = None
        self.beat_markers = None
        self.arrhythmia_markers = None
        self.bad_data_markers = None
        self.plotted = {}

    def attach_buttons(self):
        self.pushButton_Add_Files.clicked.connect(self.action_Add_Files)
        self.listWidget_Files.clicked.connect(self.action_update_selected_file)
        self.listWidget_Signals.clicked.connect(self.add_signal)
        self.pushButton_BeatDetection.clicked.connect(
            self.action_BeatDetection
        )
        self.pushButton_start_of_file.clicked.connect(
            self.action_start_of_file    
        )
        self.pushButton_end_of_file.clicked.connect(
            self.action_end_of_file    
        )
        self.pushButton_next_window.clicked.connect(
            self.action_next_window    
        )
        self.pushButton_prev_window.clicked.connect(
            self.action_prev_window
        )
    
    
    def reset_gui(self):
        self.filepath_dict = {}
        self.current_filepath = None
        self.current_data = None
        self.doubleSpinBox_y_min.setValue(-3)
        self.doubleSpinBox_y_max.setValue(3)

        self.doubleSpinBox_x_min.setValue(0)
        self.doubleSpinBox_x_window.setValue(15)
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
        
        
    
        
    def add_signal(self):
        # remove prior signal if present
        print(f'line if {self.line}')
        if self.line is not None:
            print('a line already exists')
            self.graph.removeItem(self.line)
        self.line = None
        
        self.line = self.add_plot(
            source = self.data,
            time_column = self.comboBox_time_column.currentText(),
            signal_column = self.listWidget_Signals.currentItem().text()
        )
        print(f'line is now: {self.line}')

    def add_plot(
        self,
        pen = None, 
        symbol = None, 
        source = None,
        time_column = None,
        signal_column = None
    ):
        if not pen:
            pen = pyqtgraph.mkPen(
                "Blue",
                width=1,
                style=Qt.PenStyle.SolidLine
                )
        if not symbol:
            symbol = {'symbol':None}
    
        x,y = gather_data(
            source,
            time_column,
            signal_column,
            self.doubleSpinBox_x_min.value(),
            self.doubleSpinBox_x_min.value()+self.doubleSpinBox_x_window.value(),
            self.graph.width()
            )
        
        line = self.graph.plot(
            x=x,
            y=y,
            name=signal_column,
            pen=pen,
            **symbol
            )
        
        self.plotted[self.plotted_counter]={
            'line': line,
            'pen':pen,
            'symbol':symbol,
            'name':signal_column,
            'source':source,
            'time':time_column
            }
        
        self.plotted_counter += 1
        self.update_graph()
        
        return line
        
    def update_graph(self):
        # redraw data
        
        for k,v in self.plotted.items():
            x_val,y_val = gather_data(
                v['source'],
                v['time'],
                v['name'],
                self.doubleSpinBox_x_min.value(),
                self.doubleSpinBox_x_min.value()+self.doubleSpinBox_x_window.value(),
                self.graph.width()
            )
                
            v['line'].setData(
                x=x_val,
                y=y_val,
                name=v['name'],
                pen=v['pen'],
                **v['symbol']
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
    
    # actions for interface
    def action_Clear_Files(self):
        self.filepath_dict = {}
        self.listWidget_Files.clear()
        
        
    def action_Add_Files(self):
        self.filepath_dict = {
            os.path.basename(p):p for p in 
            QFileDialog.getOpenFileNames(
                self,
                "Select ECG signal files"
                )[0]
            }
            
        print(self.filepath_dict)
        self.listWidget_Files.addItems(self.filepath_dict)
        
        
    def action_update_selected_file(self):
        self.current_filepath = self.filepath_dict[
            self.listWidget_Files.currentItem().text()
        ]
        print(f'now working on file: {self.current_filepath}')
        
        extract_tools = [
            i for i in extractors.values() 
            if i['ext'] == os.path.splitext(self.current_filepath)[1]
        ]
        
        self.data = None
        for i in extract_tools:
            if not self.data:
                try:
                    self.data = i['module'].SASSI_extract(
                        self.current_filepath
                    )
                except:
                    print('unable to open - trying another extractor')
            
            if self.data is not None:
                self.action_update_available_signals()
            
        
    def action_update_available_signals(self):
        self.listWidget_Signals.clear()
        self.listWidget_Signals.addItems(self.data.columns)
        
        self.comboBox_time_column.clear()
        self.comboBox_time_column.addItems(self.data.columns)
        self.end_of_file = max(
            self.data[self.comboBox_time_column.currentText()]
        )
        self.start_of_file = min(
            self.data[self.comboBox_time_column.currentText()]
        )
    
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
        
    def action_BeatDetection(self):
        self.voltage_column = self.listWidget_Signals.currentItem().text()
        
        self.time_column = self.comboBox_time_column.currentText()
        
        print(f'searching for beats in {self.voltage_column}')
        pklgzip_config = {
            'min_RR' : 100,
            'ecg_invert' : False,
            'ecg_filter' : True,
            'abs_thresh' : None,
            'perc_thresh' : 90,
            'time_column' : 'time'
        }
        
        self.beat_df = heartbeat_detection.beatcaller(
            self.data,
            time_column = self.time_column,
            voltage_column = self.voltage_column,
            **pklgzip_config
            )
        
        print(self.beat_df)
        
        # !!! need to add integration for center/filetype configs
        

def main():
    app = QtWidgets.QApplication(sys.argv)

    window = MainWindow()
    window.show()

    app.exec()

if __name__ == "__main__":
    main()
