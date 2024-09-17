# -*- coding: utf-8 -*-

"""
arrhythmia_detection for ECG Analysis Tool
written by Christopher S Ward (C) 2024
"""

__verison__ = "0.0.2"

# %% import libraries
import scipy
import pandas
import numpy


# %% define functions

class Settings():
    def __init__(self):
        self.bradycardia_absolute_hr = 300
        self.tachycardia_absolute_hr = 850 # need different thresholds for anesth vs awake
        self.skipped_beat_multiple_rr = 1.5
        self.premature_beat_multiple_rr = 0.25


        
def call_bradycardia_absolute(df,rr_column_name,threshold):
    return df[rr_column_name] >= threshold
    
def call_tachycardia_absolute(df,rr_column_name,threshold):
    return df[rr_column_name] <= threshold
    
def call_skipped_beat_multiple(df,rr_column_name,ts_column_name,threshold): #!!! this needs tuning
    return df[ts_column_name].diff()/df[rr_column_name] >= threshold

def call_premature_beat_multiple(df,rr_column_name,ts_column_name,threshold):
    return df[ts_column_name].diff()/df[rr_column_name] <= threshold

    
def call_arrhythmias(df,settings):
    
    df['bradycardia_absolute'] = call_bradycardia_absolute(
        df, 'RR', 60/settings.bradycardia_absolute_hr
    )
    df['tachycardia_absolute'] = call_tachycardia_absolute(
        df, 'RR', 60/settings.tachycardia_absolute_hr    
    )
    df['skipped_beat'] = call_skipped_beat_multiple(
        df, 'RR','ts', settings.skipped_beat_multiple_rr    
    )
    df['prem_beat'] = call_premature_beat_multiple(
        df, 'RR','ts', settings.premature_beat_multiple_rr
    )
    df['any_arrhythmia'] = df.any(axis=1,bool_only=True)
    
    arrhythmia_categories = [
        'bradycardia_absolute',
        'tachycardia_absolute',
        'skipped_beat',
        'prem_beat',
        'any_arrhythmia'
    ]
    
    for a in arrhythmia_categories:
        df[f'annot_{a}'] = df[a].astype(int)
    
    return df