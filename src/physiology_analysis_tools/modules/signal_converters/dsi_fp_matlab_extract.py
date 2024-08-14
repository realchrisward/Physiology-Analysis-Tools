# -*- coding: utf-8 -*-
"""

module for conversion of WA lab .mat files to SASSI ready format

@author: Christopher Scott Ward, 2022

For use with SASSI

Breathing Analysis Selection and Segmentation 
for Plethysmography and Respiratory Observations
***
built as part of the Russell Ray Lab Breathing And Physiology Analysis Pipeline
***
Breathe Easy - an automated waveform analysis pipeline
Copyright (C) 2022  
Savannah Lusk, Andersen Chang, 
Avery Twitchell-Heyne, Shaun Fattig, 
Christopher Scott Ward, Russell Ray.

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

***
"""


__version__ = '0.0.3'
__working__ = True

#%% import libraries


import scipy.io
import pandas
import re
import os
import tkinter
import tkinter.filedialog

#%% define functions

def gui_open_filename(kwargs={}):
    """
    This function creates a temporary Tkinter instance that provides a GUI 
    dialog for selecting a filename.

    Parameters
    ----------
    kwargs : Dict, optional
        The default is {}.
        *Function calls on tkFileDialog and uses those arguments
      ......
      (declare as a dictionary)
      {"defaultextension":'',"filetypes":'',"initialdir":'',...
      "initialfile":'',"multiple":'',"message":'',"parent":'',"title":''}
      ......

    Returns
    -------
    output_text : String
        String describing the path to the file selected by the GUI.
    
    """

    root = tkinter.Tk()
    output_text = tkinter.filedialog.askopenfilename(
        **kwargs)
    root.destroy()
    return output_text


def gui_open_filenames(kwargs={}):
    """
    This function creates a temporary Tkinter instance that provides a GUI
    dialog for selecting [multiple] filenames.

    Parameters
    ----------
    kwargs : Dict, optional
        The default is {}.
        *Function calls on tkFileDialog and uses those arguments
      ......
      (declare as a dictionary)
      {"defaultextension":'',"filetypes":'',"initialdir":'',...
      "initialfile":'',"multiple":'',"message":'',"parent":'',"title":''}
      ......

    Returns
    -------
    output_text : List of Strings
        List of Strings describing the paths to the files selected by the GUI.
    
    """

    root = tkinter.Tk()
    output_text_raw = tkinter.filedialog.askopenfilenames(
        **kwargs)
    output_text = root.tk.splitlist(output_text_raw)
    root.destroy()
    return output_text


def gui_directory(kwargs={}):
    """
    This function creates a temporary Tkinter instance that provides a GUI
    dialog for selecting a directory.
    
    Parameters
    ----------
    kwargs : Dict, optional
        The default is {}.
        *Function calls on tkFileDialog and uses those arguments
          ......
          (declare as a dictionary)
          {"defaultextension":'',"filetypes":'',"initialdir":'',...
          "initialfile":'',"multiple":'',"message":'',"parent":'',"title":''}
          ......

    Returns
    -------
    output_text : String
        Returns the directory path selected by the GUI.

    """

    root = tkinter.Tk()
    output_text = tkinter.filedialog.askdirectory(
        **kwargs)
    root.destroy()
    return output_text

# regex for timestamp text and marker
def extract_timestamp_text_and_interval(event_text):
    
    ts_regex = re.compile(
        "^Create measurement '(?P<ts_text>.+)'. Duration: (?P<ts_hr>\d+):(?P<ts_min>\d+):(?P<ts_sec>\d+)$"
        )
    ts_search = re.search(ts_regex,event_text)
    if ts_search is not None:
        ts_text = '#* '+ts_search['ts_text']+' '
        ts_interval = \
            int(ts_search['ts_hr'])*60*60 + \
            int(ts_search['ts_min'])*60 + \
            int(ts_search['ts_sec'])
        
        return ts_text, ts_interval
    else:
        return None, None
    

def dsi_fp_matlab_extract(filepath, logger = None):
    # extract data components
    mat_data = scipy.io.loadmat(filepath)
    
    flow = mat_data['Data'][0][0][0][0][0][0]
    flow_interval = mat_data['Data'][0][0][0][0][0][1][0][0]
    flow_chan_name = mat_data['Data'][0][0][0][0][0][2][0]
    
    chamber_temp = mat_data['Data'][0][0][1][0][0][0]
    chamber_temp_interval = mat_data['Data'][0][0][1][0][0][1][0][0]
    chamber_temp_chan_name = mat_data['Data'][0][0][1][0][0][2][0]
    
    chamber_hum = mat_data['Data'][0][0][2][0][0][0]
    chamber_hum_interval = mat_data['Data'][0][0][2][0][0][1][0][0]
    chamber_hum_chan_name = mat_data['Data'][0][0][2][0][0][2][0]
    
    event_timestamps = mat_data['Data'][0][0][3][0][0][0][0]
    
    event_dict = {
        extract_timestamp_text_and_interval(i[0])[0]:\
            extract_timestamp_text_and_interval(i[0])[1] for 
        i in event_timestamps if extract_timestamp_text_and_interval(i[0])[0]
        }
    
                 
    df = pandas.DataFrame()
    df['flow'] = [float(i) for i in flow]
    df['interval'] = flow_interval
    df.loc[0,'interval'] = 0
    df['time'] = df['interval'].cumsum()
    df.loc[:,'time'] = df['time'].round(3)
    
    df_chamber_temp = pandas.DataFrame()
    df_chamber_temp['chamber_temp'] = [float(i) for i in chamber_temp]
    df_chamber_temp['interval'] = chamber_temp_interval
    df_chamber_temp.loc[0,'interval'] = 0
    df_chamber_temp['time'] = df_chamber_temp['interval'].cumsum()
    df_chamber_temp.loc[:,'time'] = df_chamber_temp['time'].round(3)
    
    df_chamber_hum = pandas.DataFrame()
    df_chamber_hum['chamber_hum'] = [float(i) for i in chamber_hum]
    df_chamber_hum['interval'] = chamber_hum_interval
    df_chamber_hum.loc[0,'interval'] = 0
    df_chamber_hum['time'] = df_chamber_hum['interval'].cumsum()
    df_chamber_hum.loc[:,'time'] = df_chamber_hum['time'].round(3)
    
    df = df.merge(
        df_chamber_temp[['time','chamber_temp']],on='time',how='left'
        ).merge(df_chamber_hum[['time','chamber_hum']],on='time',how='left')
    
    df.loc[:,'chamber_temp'] = df['chamber_temp'].fillna(method='ffill')
    df.loc[:,'chamber_hum'] = df['chamber_hum'].fillna(method='ffill')
    
    ts = 0
    for k,v in event_dict.items():
        df.loc[df['time'] == ts,'comment'] = k
        ts+=v
    #df['comment'] = df['comment'].fillna('')
    
    # rename columns for output (time -> ts)
    
    df = df.rename(columns={
        'time':'ts',
        'chamber_temp':'temp',
        'chamber_hum':'hum',
        'all_comments':'comment'})
    
    return df[['ts','flow','temp','hum','comment']]
    
def convert_to_pickle(filepath,output_dir):
    df = dsi_fp_matlab_extract(filepath)
    df.to_pickle(
        os.path.join(
            output_dir,
            os.path.splitext(os.path.basename(filepath))[0]+".pkl.gzip"
            ),compression={'method': 'gzip', 'compresslevel': 1, 'mtime': 1}
        )



def get_channel_names(filepath = None):
    """
    Return list of 'channel names' present in an '.mat' file prepared by WA lab.

    Parameters
    ----------
    filepath : str (default is None)
        Path to '.mat' file

    Returns
    -------
    list of str
        list containing channel names (one entry for each channel)
        currently this is constant based on the extraction code

    """
    
    return ['ts','flow','temp','hum','comment']




def SASSI_extract(filepath, logger = None):
    """
    simple wrapper for calling dsi_fp_matlab_extract()

    Parameters
    ----------
    filepath : str
        Path to '.mat' file

    Returns
    -------
    pandas DataFrame
        DataFrame containing data from .mat file

    """
    
    return dsi_fp_matlab_extract(filepath)
    


def main():
    input_files = gui_open_filenames({'title':'select files to convert'})
    output_dir = gui_directory({'title':'select output directory'})
    
    for f in input_files:
        try:
            convert_to_pickle(f, output_dir)
        except:
            print(f'unable to process file: {f}')
            
#%% run main

if __name__ == '__main__':
    main()
            