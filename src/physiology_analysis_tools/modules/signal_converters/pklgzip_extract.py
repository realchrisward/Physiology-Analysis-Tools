# -*- coding: utf-8 -*-
"""
module for opening of SASSI ready .pkl.gzip files

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
__version__ = "0.0.4"

__working__ = True

#%% import libraries

import pandas
import numpy

#%% define functions


def get_channel_names(filepath):
    """
    Return list of 'channel names' present in an '.pkl.gzip' file

    Parameters
    ----------
    filepath : str
        Path to '.pkl.gzip' file

    Returns
    -------
    channel_names : list of str
        list containing channel names (one entry for each channel)

    """


    df = SASSI_extract(filepath)

    channel_names = [i.strip().lower() for i in df.columns]

    return channel_names



def SASSI_extract(filepath, logger = None):

    """
    loads dataframe from '.pkl.gzip' file

    Parameters
    ----------
    filepath : str
        Path to '.pkl.gzip' file

    Returns
    -------
    pandas DataFrame
        DataFrame containing contents of '.pkl.gzip' file

    """

    df = pandas.read_pickle(filepath, compression="gzip")
    df.columns = [i.strip().lower() for i in df.columns]
 
    # check if all_comments exits in PCC derived files

    # if not create all_comments column and populate with arduino comments and 
    # changes in mode
    if (
            'arduino_comments' in df.columns 
            and 'mode_block' in df.columns 
            and 'all_comments' not in df.columns
            and 'comment' not in df.columns
    ):
        df['all_comments'] = ''
        df.loc[
            df.mode_block.ne(df.mode_block.shift(1)),
            'all_comments'
        ] = df.mode_block[
            df.mode_block.ne(df.mode_block.shift(1))
        ]
        
        df['all_comments'] += df['arduino_comments']
    
    df = df.rename(columns = {'comments':'comment','all_comments':'comment'})
    df.loc[df['comment']=='','comment'] = numpy.nan

    return df
