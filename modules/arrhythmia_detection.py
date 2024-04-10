# -*- coding: utf-8 -*-

"""
arrhythmia_detection for ECG Analysis Tool
written by Christopher S Ward (C) 2024
"""

__verison__ = "0.0.1"

# %% import libraries
import scipy
import pandas
import numpy


# %% define functions

class Settings():
    def __init__(self):
        self.bradycardia_absolute_hr = 300
        self.tachycardia_absolute_hr = 750
        self.skipped_beat_multiple_rr = 2


        
def call_bradycardia_absolute(df,rr_column_name,threshold):
    return df[rr_column_name] >= threshold
    
def call_tachycardia_absolute(df,rr_column_name,threshold):
    return df[rr_column_name] <= threshold
    
def call_skipped_beat_multiple(df,rr_column_name,threshold):
    df[rr_column_name].diff()/df[rr_column_name] >= threshold

    
def call_arrhythmias(df,settings):
    df['arrhyth'] = 0
    
    df['bradycardia_absolute'] = call_bradycardia_absolute(
        df, 'RR', 60/settings.bradycardia_absolute_hr
    )
    df['tachycardia_absolute'] = call_tachycardia_absolute(
        df, 'RR', 60/settings.tachycardia_absolute_hr    
    )
    df['skipped_beat'] = call_skipped_beat_multiple(
        df, 'RR', settings.skipped_beat_multiple_rr    
    )
    df['any_arrhythmia'] = df.any(axis=1,bool_only=True)
    
    df.loc[df['any_arrhythmia'],'arrhyth'] = 1
    
    return df