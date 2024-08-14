# -*- coding: utf-8 -*-
"""
module for conversion of PCC files to SASSI ready format

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


__version__ = "0.1.1"



#%% import libraries

import os
import sys
import pandas
from tkinter import Tk, filedialog
import logging

__working__ = True


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


def create_logger(filepath: str = None):
    logger = logging.getLogger(f'PCC Output Extractor - v{__version__}')
    logger.setLevel(logging.DEBUG)

    # create format for log and apply to handlers
    log_format = logging.Formatter(
            '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
            )


    # create file and console handlers to receive logging
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(log_format)
    logger.addHandler(console_handler)    
    
    if filepath:
        file_handler = logging.FileHandler(filepath)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(log_format)
        logger.addHandler(file_handler)
    
    return logger




# def extract_header_locations_from_dataframe(
#         df,
#         header_text_firstline_fragment = '$$$$$ DATA SESSION',
#         header_text_lastline_fragment = 'statuscodes',
#         local_logger = None,
#         status_queue = None
#         ):

#     """
#     gathers information regarding the locations of header information
#     throughout a signal file - needed if files may contain multiple recording
#     blocks

#     Parameters
#     ----------
#     df : pandas.DataFrame
#         path to file containing signal data
#     header_text_firstline_fragment : string, optional
#         string that is present at beginning of header lines. The default is '$$$'.
#     header_text_lastline_fragment : string, optional
#         string that is present at end of header lines. The default is 'status_codes'.
#     local_logger : instance of logging.logger, optional
#         The default is None (i.e. no logging)

#     Returns
#     -------
#     headers_firstlines : list
#         list of rows in the datafile that indicate header content present (first line)
#     headers_lastlines : list
#         list of rows in the datafile that indicate header content present (last line)
        
#     """
    
#     headers_firstlines = df[
#         df['raw'].str.contains(
#             header_text_firstline_fragment
#             )
#         ].index
            
#     headers_lastlines = df[
#         df['raw'].str.contains(
#             header_text_lastline_fragment
#             )
#         ].index
    
#     if len(headers_firstlines) == len(headers_lastlines):
#         if local_logger: local_logger.info('headers found')
#     else:
#         if local_logger: local_logger.info('headers not balanced')
        
#     headers = [
#         (headers_firstlines[i],headers_lastlines[i]) for i in 
#         range(min(len(headers_firstlines),len(headers_lastlines)))
#         ]
    
#     return headers


def extract_header_locations_from_dataframe(
    df,
    header_text_firstline_fragment='\$\$\$\$\$ DATA SESSION',
    header_text_lastline_fragment='statuscodes',
    local_logger=None
):
    """
    Gathers information regarding the locations of header information
    throughout a signal file - needed if files may contain multiple recording
    blocks.
    """
    # Find start of headers
    headers_firstlines = df[
        df['raw'].str.contains(header_text_firstline_fragment, na=False)
    ].index

    # Find end of headers
    headers_lastlines = df[
        df['raw'].str.contains(header_text_lastline_fragment, na=False)
    ].index

    # Log the status of header finding
    if local_logger:
        if len(headers_firstlines) == len(headers_lastlines):
            local_logger.info('Equal number of header start and end lines found.')
        else:
            local_logger.info('Mismatch in the number of header start and end lines found.')

    # Pair up start and end lines, ensuring no out-of-bounds errors
    headers = [
        (headers_firstlines[i], headers_lastlines[i]) for i in 
        range(min(len(headers_firstlines), len(headers_lastlines)))
    ]

    return headers


def fix_filename(filename):
    if filename.endswith('.txt'):
        new_filename = filename[:-4].replace(' ','-').replace('.','-')+'.txt'
    else:
        new_filename = filename.replace(' ','-').replace('.','-')+'.txt'
    return new_filename


def read_pcc_file(
        input_file,
        header_tuples = [
                ('time',float),
                ('FLOW',float),
                ('ECG',float),
                ('BT',float),
                ('RH',float),
                ('O2',float),
                ('CO2',float),
                ('labjack_temp',float),
                ('mode_block',str),
                ('parameter',str),
                ('arduino_comments',str)
                ], 
        signal_blocks = None,
        logger = None, 

        status_queue = None,

        delim = '\t',
        samplingHz = 1000):
    
    if logger: logger.info('loading data')

    if status_queue: status_queue.put_nowait((20,f'{input_file}:loading_data'))

    with open(input_file,'r') as opfi:
        data = pandas.DataFrame(
            {'raw':[
                line for line in opfi.readlines()
                ]}
            )
        
    
    
    # test for line repair - line not ending with \n 
    # (is that still an issue? was it really the prior issue?)
    # only raise exception if not the last line
    if data[data['raw'].str[-1] != '\n'].shape[0] != 0:
        if data[data['raw'].str[-1] != '\n'].index[0] == data.index[-1]:
            # do nothing since the problem is on the last line
            pass
        else:
            raise Exception('Line Repair Needed')
    
    
    # purge '\n' characters
    if logger: logger.info('purging newline characters')
    data.loc[:,'raw'] = data['raw'].str.replace('\n','')
    
    
    # break into columns 
    if logger: logger.info('splitting into columns')

    if status_queue: status_queue.put_nowait((20,f'{input_file}: splitting into columns'))


    data['delim_split'] = data['raw'].str.split(delim)
    for i,v in enumerate(header_tuples):
        data[v[0]] = data['delim_split'].str[i]
        
    # purge known header rows
    if not signal_blocks:
        signal_blocks = extract_header_locations_from_dataframe(
            data,
            local_logger = logger
            )
    
    

    if logger: logger.info('extracting header rows')
    if status_queue: status_queue.put_nowait((20,f'{input_file}: extracting header rows'))

    skippable_rows = [i for i in range(signal_blocks[0][0])]
    for b in signal_blocks:
        skippable_rows += [i for i in range(b[0],b[1]+1)]
    
    filtered_data = data.drop(
        labels=skippable_rows, axis='index'
        )[[k[0] for k in header_tuples]].reset_index(drop=True)
    
    # set types for columns
    for v in header_tuples:
        
        if v[1]==float:
            if logger: logger.info(f'setting {v[0]} as numeric data')

            if status_queue: 
                status_queue.put_nowait(
                    (20,f'{input_file}: setting {v[0]} as numeric data')
                )
            filtered_data[v[0]] = pandas.to_numeric(

                filtered_data[v[0]],errors='coerce'
                )
            if sum(filtered_data[v[0]].isnull()) > 0:
                if logger: logger.warning('corrupted rows found')

                if status_queue: 
                    status_queue.put_nowait(
                        (30,f'{os.path.basename(input_file)}: corrupted rows found')
                    )
                    

            # drop rows where time value is missing
            if v[0] == 'time':
                filtered_data = filtered_data[~filtered_data['time'].isnull()]
        elif v[1]==str:
            #filtered_data[v[0]].fillna('',inplace=True)
            filtered_data.fillna({v[0]:''}, inplace=True)
    
    # fix timestamps
    filtered_data['time_rate'] = filtered_data['time'].diff().round(3)
    filtered_data.loc[filtered_data['time_rate']<0,'time_rate'] = \
        round(1/samplingHz,3)
    filtered_data.loc[0,'time_rate'] = 0
    filtered_data['time'] = filtered_data['time_rate'].cumsum().round(3)
    filtered_data.drop(labels='time_rate',axis=1,inplace=True)
    
    # flag corrupt data boudaries
    filtered_data['time_rate'] = filtered_data['time'].diff().round(3)
    
    filtered_data.loc[
        filtered_data['time_rate']>round(1/samplingHz,3),'arduino_comments'
        ] += '< data corruption boundary'
    filtered_data.loc[
        filtered_data.shift(-1)['time_rate']>round(1/samplingHz,3),
        'arduino_comments'
        ] += 'data corruption boundary >'
    
    
    # reset gaps in timestamps
    updated_time = [
        round(i/samplingHz,3) for i in range(
            0,round(filtered_data['time'].max()*samplingHz)+1
            )
        ]
    filtered_data = filtered_data.set_index('time').reindex(updated_time).reset_index()
    
    # fill nan
    for value in header_tuples:
        if logger: logger.info(f'Filling nan values - {value}')
        if value[1]==float:
            filtered_data.loc[filtered_data[v[0]].isnull(),v[0]] = 999
        elif v[0]=='mode_block':
        #     filtered_data[v[0]].ffill(method='ffill',inplace=True)
            filtered_data.ffill({v[0]:v},inplace=True)    
        elif v[1]==str:
            # filtered_data[v[0]].fillna('',inplace=True)
            filtered_data.fillna({v[0]:''},inplace=True)
    
    # create comments column and populate with arduino comments and changes in mode
    filtered_data['comment'] = ''
    filtered_data.loc[
        filtered_data.mode_block.ne(filtered_data.mode_block.shift(1)),
        'comment'
    ] = filtered_data.mode_block[
        filtered_data.mode_block.ne(filtered_data.mode_block.shift(1))
    ]
    
    filtered_data['comment'] += filtered_data['arduino_comments']
    
        
    return filtered_data




def convert_to_pickle(
        filepath, 
        output_dir, 
        logger = None, 
        status_queue = None,
        convert_to_lctf = False
        ):
    """
    Convert PCC file to '.pkl.gzip' file in SASSI ready state


    Parameters
    ----------
    filepath : str
        Path to file

    output_dir : str
        Path to directory to place newly converted '.pkl.gzip' file

    Returns
    -------
    None.

    """

    df = read_pcc_file(
        filepath,
        [
            ('time',float),
            ('FLOW',float),
            ('ECG',float),
            ('BT',float),
            ('RH',float),
            ('O2',float),
            ('CO2',float),
            ('labjack_temp',float),
            ('mode_block',str),
            ('parameter',str),
            ('arduino_comments',str)
        ],
        logger = logger,

        status_queue = status_queue,

        delim='\t'
    )
    df.to_pickle(
        os.path.join(
            output_dir,
            os.path.splitext(
                fix_filename(os.path.basename(filepath))
                )[0] + ".pkl.gzip",
        ),
        compression={"method": "gzip", "compresslevel": 1, "mtime": 1},
    )
    
    if convert_to_lctf:
        convert_to_lctf(df,filepath, output_dir, logger = None)


def convert_to_lctf(df, filepath,output_dir, logger = None, status_queue = None):
    
    if logger: logger.info('exporting labchart format file')
    if status_queue: 
        status_queue.put_nowait(
            (20,f'{os.path.basename(filepath)}: exporting labchart format file')
        )

    
    sampling_interval = \
                df['time'].iloc[1] - \
                    df['time'].iloc[0]
    
    columns_for_export = [
                'time',
                'FLOW',
                'ECG',
                'all_comments'
                ]
    
    with open(os.path.join(
            output_dir,
            os.path.splitext(
                fix_filename(os.path.basename(filepath))
                )[0] + ".txt"
            ),'w') as lctf:
        lctf.write('\n'.join([
            'Interval= {:.3F} s'.format(sampling_interval),
            'TimeFormat= StartofBlock',
            'ChannelTitle= \tFLOW\tECG\t',
            'Range= \t10.000V\t10.000V\t\n'                                 
            ]
            )
            )


    df[columns_for_export].to_csv(
        os.path.join(
            output_dir,
            os.path.splitext(
                fix_filename(os.path.basename(filepath))
                )[0] + ".txt"
            ),
        index=False,
        header=False,
        sep='\t',
        mode='a'
        )
    
    
def get_channel_names(filepath):
    """
    Return list of 'channel names' present in an PCC file

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


    channel_names = [
        'time',
        'FLOW',
        'ECG',
        'BT',
        'RH',
        'O2',
        'CO2',
        'labjack_temp',
        'mode_block',
        'parameter',
        'arduino_comments'
    ]

    return channel_names



def SASSI_extract(filepath, logger = None, status_queue = None):
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

    return read_pcc_file(filepath, logger = logger, status_queue = status_queue)



def main():
    input_files = gui_open_filenames({"title": "select files to convert"})
    output_dir = gui_directory({"title": "select output directory"})
    logger = create_logger(os.path.join(output_dir,'log.log'))
    for f in input_files:
        logger.info(f"working on file - {os.path.basename(f)}")
        try:
            convert_to_pickle(f, output_dir,logger = logger)
        except:
            logger.info(f"unable to process file: {f}")


#%% run main

if __name__ == "__main__":
    main()
