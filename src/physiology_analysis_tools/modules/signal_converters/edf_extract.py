# -*- coding: utf-8 -*-
"""
Created on Wed May 17 11:12:13 2023

EDF extract

@author: wardc
"""


__version__ = "0.0.1"


#%%
import pandas
from pyedflib import highlevel
import os
import numpy
from tkinter import Tk, filedialog


# # %% demo
# signal_files = [
#     "D:/BCM/OneDrive - Baylor College of Medicine/MMPC/projects/Seebeck/EDF sample data ECG BP EEG EMG/2-22-042.edf",
#     "D:/BCM/OneDrive - Baylor College of Medicine/MMPC/projects/Seebeck/EDF sample data ECG BP EEG EMG/2-22-044.edf",
#     "D:/BCM/OneDrive - Baylor College of Medicine/MMPC/projects/Seebeck/EDF sample data ECG BP EEG EMG/2-22-062.edf",
#     "D:/BCM/OneDrive - Baylor College of Medicine/MMPC/projects/Seebeck/EDF sample data ECG BP EEG EMG/2-22-068.edf",
# ]

# # %
# signals, signal_headers, header = highlevel.read_edf(signal_files[2])

# # %
# column_names = [i["label"] for i in signal_headers]
# row_counts = [len(i) for i in signals]
# sampling_rate = [i["sample_rate"] for i in signal_headers]
# sampling_frequency = [i["sample_frequency"] for i in signal_headers]
# prefilters = [i["prefilter"] for i in signal_headers]
# transducers = [i["transducer"] for i in signal_headers]
# dimensions = [i["dimension"] for i in signal_headers]

# # %
# signal_dict = {
#     f"{i} - {v}": {
#         "rows": row_counts[i],
#         "sampling_rate": sampling_rate[i],
#         "sampling_freq": sampling_frequency[i],
#         "prefilter": prefilters[i],
#         "transducers": transducers[i],
#         "dimensions": dimensions[i],
#     }
#     for i, v in enumerate(column_names)
# }
# #%%
# highest_sampling_freq = max(sampling_frequency)

# longest_signal = max(
#     [row_counts[i] / sampling_frequency[i] for i, v in enumerate(column_names)]
# )

# df = pandas.DataFrame(
#     {"ts": numpy.arange(0, longest_signal, 1 / highest_sampling_freq)}
# )

# precision = len(f"{1/highest_sampling_freq}".split(".")[1])

# df.loc[:, "ts"] = df.ts.round(precision)


# #%
# for i, v in enumerate(column_names):
#     channel_name = f"{i} - {v}"
#     ts = numpy.arange(
#         0, row_counts[i] / sampling_frequency[i], 1 / sampling_frequency[i]
#     )
#     temp_df = pandas.DataFrame({"ts": ts, channel_name: signals[i]})
#     temp_df.loc[:, "ts"] = temp_df.ts.round(precision)

#     df = pandas.merge(df, temp_df, on="ts", how="left")

# merged_data = df.ffill().set_index("ts")
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


def edf_extract(filepath, logger=None):
    """
    Extracts data from an '.edf' file into a pandas DataFrame

    Parameters
    ----------
    filepath : str
        Path to '.edf' file

    Returns
    -------
    merged_data : pandas.DataFrame
        DataFrame containing contents of the '.edf' file in a basspro ready
        state

    """

    signals, signal_headers, header = highlevel.read_edf(filepath)

    # %
    column_names = [i["label"] for i in signal_headers]
    row_counts = [len(i) for i in signals]
    # sampling_rate = [i['sample_rate'] for i in signal_headers]
    sampling_frequency = [i["sample_frequency"] for i in signal_headers]
    # prefilters = [i['prefilter'] for i in signal_headers]
    # transducers = [i['transducer'] for i in signal_headers]
    # dimensions =  [i['dimension'] for i in signal_headers]

    # %
    # signal_dict = {
    #     f"{i} - {v}": {
    #         "rows": row_counts[i],
    #         "sampling_rate": sampling_rate[i],
    #         "sampling_freq": sampling_frequency[i],
    #         "prefilter": prefilters[i],
    #         "transducers": transducers[i],
    #         "dimensions": dimensions[i],
    #     }
    #     for i, v in enumerate(column_names)
    # }
    #
    highest_sampling_freq = max(sampling_frequency)

    longest_signal = max(
        [
            row_counts[i] / sampling_frequency[i]
            for i, v in enumerate(column_names)
        ]
    )

    df = pandas.DataFrame(
        {"ts": numpy.arange(0, longest_signal, 1 / highest_sampling_freq)}
    )

    precision = len(f"{1/highest_sampling_freq}".split(".")[1])

    df.loc[:, "ts"] = df.ts.round(precision)

    #
    for i, v in enumerate(column_names):
        channel_name = f"{i} - {v}"
        ts = numpy.arange(
            0, row_counts[i] / sampling_frequency[i], 1 / sampling_frequency[i]
        )
        temp_df = pandas.DataFrame({"ts": ts, channel_name: signals[i]})
        temp_df.loc[:, "ts"] = temp_df.ts.round(precision)

        df = pandas.merge(df, temp_df, on="ts", how="left")

    merged_data = df.ffill()
    merged_data['comment'] = ''

    return merged_data


def convert_to_pickle(filepath, output_dir):
    """
    Convert '.adicht' file to '.pkl.gzip' file in basspro ready state

    Parameters
    ----------
    filepath : str
        Path to '.edf file'

    output_dir : str
        Path to directory to place newly converted '.pkl.gzip' file

    Returns
    -------
    None.

    """

    df = edf_extract(filepath)
    df.to_pickle(
        os.path.join(
            output_dir,
            os.path.splitext(os.path.basename(filepath))[0] + ".pkl.gzip",
        ),
        compression={"method": "gzip", "compresslevel": 1, "mtime": 1},
    )


def get_channel_names(filepath):
    """
    Return list of 'channel names' present in an '.edf' file

    Parameters
    ----------
    filepath : str
        Path to '.edf file

    Returns
    -------
    channel_names : list of str
        list containing channel names (one entry for each channel) - note that
        leading and trailing whitespace characters are removed.

    """

    signals, signal_headers, header = highlevel.read_edf(filepath)

    column_names = [i["label"] for i in signal_headers]

    channel_names = [f"{i} - {v}" for i, v in enumerate(column_names)]

    return channel_names


def basspro_extract(filepath, logger=None):
    """
    simple wrapper for calling adi_extract()

    Parameters
    ----------
    filepath : str
        Path to '.edf' file

    Returns
    -------
    pandas DataFrame
        DataFrame containing contents of '.edf' file

    """
    return edf_extract(filepath)


def main():
    input_files = gui_open_filenames({"title": "select files to convert"})
    output_dir = gui_directory({"title": "select output directory"})

    for f in input_files:
        print(f"working on file - {os.path.basename(f)}")
        try:
            convert_to_pickle(f, output_dir)
        except:
            print(f"unable to process file: {f}")


# %% run main

if __name__ == "__main__":
    main()
