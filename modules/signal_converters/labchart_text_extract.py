# -*- coding: utf-8 -*-
"""
module for conversion of labchart exported text files to SASSI ready format

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
__working__ = True

#%% import libraries


import os
import pandas
from tkinter import Tk, filedialog

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


def extract_header_type(filename, rows_to_check=20):
    """
    Gathers information regarding the header format/column contents present
    in an exported lab chart signal file (assumes set-up is in line with
    Ray Lab specifications)

    Parameters
    ----------
    filename : string
        path for file containing signal data

    Returns
    -------
    header_tuples : list of tuples
        list of tuples specifying ([column name],[datatype])

    """

    with open(filename, "r") as opfi:
        # check 1st [rows_to_check] rows to see if header is expected to
        # include date column with data as well as data containing columns
        header_columns=[]
        
        ts_columns = ["ts"]
        for i in range(rows_to_check):
            cur_line = opfi.readline()
            if "DateFormat=  M/d/yyyy" in cur_line:
                ts_columns = ["ts", "date"]
            elif "ChannelTitle=" in cur_line:
                header_columns = (
                    cur_line.lower().replace("\n", "").split("\t")[1:]
                )
                while "" in header_columns:
                    header_columns.remove("")

        # Ensure header_columns is defined even if "ChannelTitle=" is not found
        if not header_columns:
            header_columns = ['n/a']  # Replace 'default_column' with a suitable default value
        
        # ts_columns = ["ts"]
        # for i in range(rows_to_check):
        #     cur_line = opfi.readline()
        #     if "DateFormat=	M/d/yyyy" in cur_line:
        #         ts_columns = ["ts", "date"]
        #     else:
        #         pass
            
           
        #     if "ChannelTitle=" in cur_line:
        #         header_columns = (
        #             cur_line.lower().replace("\n", "").split("\t")[1:]
        #         )
        #         while "" in header_columns:
        #             header_columns.remove("")
        #     else:
        #         pass

    # special_columns describe columns that should not be
    # processed as float
    special_columns = {"date": str, "comment": str}

    
    combined_columns = (
        ts_columns
        + [i.strip().lower() for i in header_columns]
        + ["comment"]
    )

    header_tuples = [
        (j, str) if j in special_columns else (j, float)
        for j in combined_columns
    ]

    return header_tuples


def extract_header_locations(
    filename, header_text_fragment="Interval=", local_logger=None
):
    """
    Gathers information regarding the locations of header information
    throughout a signal file - needed if files may contain multiple recording
    blocks

    Parameters
    ----------
    filename : string
        path to file containing signal data
    header_text_fragment : string, optional
        string that is present in header lines. The default is 'Interval='.
    local_logger : instance of logging.logger, optional
        The default is None (i.e. no logging)

    Returns
    -------
    headers : list
        list of rows in the datafile that indicate header content present

    """

    headers = []
    i = 0
    with open(filename, "r") as opfi:
        for line in opfi:
            if header_text_fragment in line:
                if local_logger != None:
                    local_logger.info(
                        "Signal File has HEADER AT LINE: {}".format(i)
                    )
                headers.append(i)
            i += 1
    return headers


def read_exported_labchart_file(
    lc_filepath, header_locations, header_tuples, delim="\t", rows_to_skip=6
):
    """
    Collects data from an exported lab chart file and returns a list of
    dataframes (in order) containing the extracted contents of the signal file.

    Parameters
    ----------
    lc_filepath : string
        path to file containing signal data
    header_locations : list of integers
        list describing the locations of headers throughout a signal file
    header_tuples : list of tuples
        list of tuples specifying ([column name],[datatype])
    delim : string, optional
        delimiter used. The default is '\t'.
    rows_to_skip : integer, optional
        the number of rows present in the header that should be skipped
        to get to the location containing data. The default is 6.

    Returns
    -------
    df_list : list of pandas.DataFrames
        list of dataframes containing signal data

    """
    df_list = []

    for i in range(len(header_locations)):
        # case if only one header
        if len(header_locations) == 1:
            df_list.append(
                pandas.read_csv(
                    lc_filepath,
                    sep=delim,
                    names=[i[0] for i in header_tuples],
                    skiprows=rows_to_skip + header_locations[i],
                    dtype=dict(header_tuples),
                )
            )
        else:
            # case if not last section
            if i + 1 < len(header_locations):
                df_list.append(
                    pandas.read_csv(
                        lc_filepath,
                        sep=delim,
                        names=[i[0] for i in header_tuples],
                        skiprows=rows_to_skip + header_locations[i],
                        nrows=header_locations[i + 1]
                        - header_locations[i]
                        - 6,
                        dtype=dict(header_tuples),
                    )
                )
            # case if last section of multisection file
            else:
                df_list.append(
                    pandas.read_csv(
                        lc_filepath,
                        sep=delim,
                        names=[i[0] for i in header_tuples],
                        skiprows=rows_to_skip + header_locations[i],
                        dtype=dict(header_tuples),
                    )
                )
    return df_list


def merge_signal_data_pieces(df_list):
    """
    Merges multiple blocks contained in a list of dataframes into a single
    dataframe (current behavior will override timestamp information to
    place subsequent blocks of data using the next sequential timestamp).

    Parameters
    ----------
    df_list : list of pandas.DataFrames
        list of dataframes containing signal data

    Returns
    -------
    merged_data : pandas.DataFrame
        dataframe containing signal data

    """
    # merge into single dataframe
    # merged_data = pandas.DataFrame()
    for piece_number in range(len(df_list)):
        if piece_number != 0:
            # revise timestamp so that multiblock data fit into the next
            # consecutive timestamp - labchart file may reset each block to 0
            # or each block may track time relative to experiment start
            ts_minus = df_list[piece_number]["ts"].min()
            ts_add = (
                df_list[piece_number - 1]["ts"].max()
                + df_list[0]["ts"][2]
                - df_list[0]["ts"][1]
            )
        else:
            ts_minus = 0
            ts_add = 0
        df_list[piece_number].loc[:, "ts"] = (
            df_list[piece_number]["ts"] + ts_add - ts_minus
        )
    merged_data = pandas.concat(
        df_list, ignore_index=True,
        axis = 0
    )
    return merged_data


def labchart_text_extract(filepath, logger=None):

    Header_Tuples = extract_header_type(filepath)
    header_locations = extract_header_locations(filepath, local_logger=logger)
    signal_data_pieces = read_exported_labchart_file(
        filepath, header_locations, header_tuples=Header_Tuples
    )
    signal_data_assembled = merge_signal_data_pieces(signal_data_pieces)

    return signal_data_assembled


def convert_to_pickle(filepath, output_dir):
    df = labchart_text_extract(filepath)
    df.to_pickle(
        os.path.join(
            output_dir,
            os.path.splitext(os.path.basename(filepath))[0] + ".pkl.gzip",
        ),
        compression={"method": "gzip", "compresslevel": 1, "mtime": 1},
    )


def get_channel_names(filepath):
    """
    Return list of 'channel names' present in an '.txt' file

    Parameters
    ----------
    filepath : str
        Path to '.txt' file

    Returns
    -------
    channel_names : list of str
        list containing channel names (one entry for each channel)  - note that
        leading and trailing whitespace characters are removed.

    """

    channel_names = [i[0] for i in extract_header_type(filepath)]
    
    return channel_names



def SASSI_extract(filepath, logger = None):
    """
    simple wrapper for calling labchart_text_extract()

    Parameters
    ----------
    filepath : str
        Path to '.txt' file

    Returns
    -------
    pandas DataFrame
        DataFrame containing contents of '.txt' file

    """
    return labchart_text_extract(filepath)


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
