# -*- coding: utf-8 -*-
"""
module for conversion of labchart adicht files to SASSI ready format

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


__version__ = "0.0.3"


#%% import libraries

import os
import pandas
import numpy
from tkinter import Tk, filedialog

try:
    import adi

    __working__ = True
except:
    __working__ = False


#%% define functions


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

    root = Tk()
    output_text_raw = filedialog.askopenfilenames(**kwargs)
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

    root = Tk()
    output_text = filedialog.askdirectory(**kwargs)
    root.destroy()
    return output_text



def adi_extract(filepath, logger=None):
    """
    Extracts data from an '.adicht' file into a pandas DataFrame

    Parameters
    ----------
    filepath : str
        Path to '.adicht' file

    Returns
    -------
    merged_data : pandas.DataFrame
        DataFrame containing contents of the '.adicht' file in a SASSI ready state

    """


    file = adi.read_file(filepath)

    number_of_channels = file.n_channels
    list_of_records = [r.id for r in file.records]

    df_dict = {}
    temp_timestamp_dict = {}




    for i, r in enumerate(list_of_records):
        channel_data = {}

        for c in range(number_of_channels):

            channel_data[file.channels[c].name.strip().lower()] = file.channels[
                c
            ].get_data(r)

            temp_timestamp_dict[c] = {
                chan_rec.time: chan_rec.text
                for chan_rec in file.channels[c].records[i].comments
            }

        df_dict[r] = pandas.DataFrame(channel_data)

        df_dict[r].loc[:, "time_increment"] = file.records[i].tick_dt
        df_dict[r].loc[:, "time"] = df_dict[r]["time_increment"].cumsum()
        # df_dict[r].drop(labels='time_increment', axis = 1, inplace = True)
        if "." in f"{file.records[i].tick_dt}":
            time_precision = len(f"{file.records[i].tick_dt}".split(".")[1])
        else:
            time_precision = 0
        df_dict[r].loc[:, "time"] = df_dict[r]["time"].round(time_precision)

        merged_timestamp_dict = {}
        for c, v in temp_timestamp_dict.items():
            for ts, comment in v.items():
                if comment == merged_timestamp_dict.get(ts, None):
                    pass
                elif ts in merged_timestamp_dict:
                    merged_timestamp_dict[ts] += f"|{comment}"
                else:
                    merged_timestamp_dict[ts] = comment

        df_dict[r]["comment"] = numpy.nan
        for ts, comment in merged_timestamp_dict.items():

            df_dict[r].loc[
                df_dict[r]["time"] == round(ts, time_precision), "comment"
            ] = comment


    merged_data = pandas.concat(df_dict, ignore_index=True,axis=0)
    merged_data.loc[0,"time_increment"] = 0
    merged_data.loc[:, "time"] = (
        merged_data["time_increment"].cumsum().round(time_precision)
    )

    merged_data.drop(labels="time_increment", axis=1, inplace=True)

    return merged_data


def convert_to_pickle(filepath, output_dir):

    """
    Convert '.adicht' file to '.pkl.gzip' file in SASSI ready state

    Parameters
    ----------
    filepath : str
        Path to '.adicht file'

    output_dir : str
        Path to directory to place newly converted '.pkl.gzip' file

    Returns
    -------
    None.

    """


    df = adi_extract(filepath)
    df.to_pickle(
        os.path.join(
            output_dir,
            os.path.splitext(os.path.basename(filepath))[0] + ".pkl.gzip",
        ),
        compression={"method": "gzip", "compresslevel": 1, "mtime": 1},
    )



def get_channel_names(filepath):
    """
    Return list of 'channel names' present in an '.adicht' file

    Parameters
    ----------
    filepath : str
        Path to '.adicht file

    Returns
    -------
    channel_names : list of str
        list containing channel names (one entry for each channel) - note that
        leading and trailing whitespace characters are removed.

    """

    file = adi.read_file(filepath)

    number_of_channels = file.n_channels

    channel_names = [
        file.channels[c].name.strip().lower()
        for c in range(number_of_channels)
    ]+['comment']

    return channel_names


def collect_time_stamps(filepath, logger = None):

    data = SASSI_extract(filepath, logger)[['time','comment']]

    data_ts = data.dropna().reset_index()
    time_stamps = {}
    for i, row in data_ts.iterrows():
        time_stamps[i] = {'text':row['comment'],'time':row['time']}
    time_stamps[i+1] = {'text':'end_of_session','time':data['time'].max()}
        
    return time_stamps


# def collect_time_stamps(filepath, logger = None):
#     file = adi.read_file(filepath)
#     time_stamps = {}
#     i_comments = 0
#     i_blocks = 0
#     for b in file.records:
#         for c in b.comments:
#             time_stamps[i_comments]={'text':c.text,'time':c.time, 'block':i_blocks}
#             i_comments += 1
#         i_blocks += 1
#         # add end of session
#         time_stamps[i_comments]={
#             'text':'end_of_session',
#             'time':b.n_ticks*b.tick_dt,
#             'block':i_blocks
#         }
        
#     return time_stamps




def SASSI_extract(filepath, logger = None):
    """
    simple wrapper for calling adi_extract()

    Parameters
    ----------
    filepath : str
        Path to '.adicht' file

    Returns
    -------
    pandas DataFrame
        DataFrame containing contents of '.adicht' file

    """
    return adi_extract(filepath)



def main():
    input_files = gui_open_filenames({"title": "select files to convert"})
    output_dir = gui_directory({"title": "select output directory"})

    for f in input_files:
        print(f"working on file - {os.path.basename(f)}")
        try:
            convert_to_pickle(f, output_dir)
        except:
            print(f"unable to process file: {f}")


#%% run main

if __name__ == "__main__":
    main()
