# -*- coding: utf-8 -*-

"""
arrhythmia_detection for ECG Analysis Tool
written by Christopher S Ward (C) 2024
"""

__verison__ = "0.0.5"

# %% import libraries
import scipy
import pandas
# import numpy


# %% define functions

arrhythmia_categories = [
        'bradycardia_absolute',
        'tachycardia_absolute',
        'skipped_beat',
        'prem_beat',
        'any_arrhythmia',
        'other_arrhythmia'
    ]

annot_arrhythmia_categories = [
    f'annot_{i}' for i in arrhythmia_categories
]



def calculate_moving_average(input_series, window, include_current=True):
    """
    Calculates a moving average of a series, with an option to exclude the current value
    from the average calculation.

    Parameters
    ----------
    input_series : pd.Series
        Data to use for moving average calculation.
    window : int
        Number of samples to use for the moving average.
    include_current : bool, optional
        Whether to include the 'middle' value in the moving average calculation.
        The default is True.

    Returns
    -------
    moving_average : pd.Series
        Moving average smoothed series paired to the input_series.
    """

    if include_current:
        moving_average = input_series.rolling(window, center=True, min_periods=1).mean()
    else:
        # Adjust for excluding the current value:
        # Calculate the sum over the window, subtract the current value, and divide by the adjusted count.
        total_sum = input_series.rolling(window, center=True, min_periods=1).sum()
        count = input_series.rolling(window, center=True, min_periods=1).count() - 1
        # Ensure we do not divide by zero
        count = count.apply(lambda x: max(x, 1))
        adjusted_sum = total_sum - input_series
        moving_average = adjusted_sum / count

    return moving_average



class Settings():
    def __init__(self):
        self.bradycardia_absolute_hr = 300
        self.tachycardia_absolute_hr = 850 # need different thresholds for anesth vs awake
        self.skipped_beat_multiple_rr = 1.5
        self.premature_beat_multiple_rr = 0.25
        self.skipped_beat_multiple_rr = 1.5
        self.premature_beat_multiple_rr = 0.7


        
def call_bradycardia_absolute(df,rr_column_name,threshold):
    output = df[rr_column_name] >= threshold
    print(f'brady {output.shape}')
    return output
    
def call_tachycardia_absolute(df,rr_column_name,threshold):
    output = df[rr_column_name] <= threshold
    print(f'tachy {output.shape}')
    return output
    
def call_skipped_beat_multiple(df,rr_column_name,ts_column_name,threshold): #!!! this needs tuning
    # output = df[ts_column_name].diff()/df[rr_column_name] >= threshold
    output =  df[rr_column_name]/calculate_moving_average(df[rr_column_name],window=7,include_current=False) >= threshold
    print(f'skip bt {output.shape}')
    return output
    print('test')
    # 

def call_premature_beat_multiple(df,rr_column_name,ts_column_name,threshold):
    # output = df[ts_column_name].diff()/df[rr_column_name] <= threshold
    output = df[ts_column_name].diff()/calculate_moving_average(df[rr_column_name],window=7,include_current=False) <= threshold
    print(f'prem bt {output.shape}')
    return output
    

    
def call_arrhythmias(df,settings,arrhythmia_categories):
    
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
    df['any_arrhythmia'] = df[['bradycardia_absolute','tachycardia_absolute','skipped_beat','prem_beat']].any(axis=1,bool_only=True)

    df['other_arrhythmia'] = False
        
    
    for a in arrhythmia_categories:
        df[f'annot_{a}'] = df[a].astype(int)
    
    return df