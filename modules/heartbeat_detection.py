# -*- coding: utf-8 -*-

"""
heartbeat_detection for ECG Analysis Tool
written by Christopher S Ward (C) 2024
"""

__verison__ = "0.0.1"

# %% import libraries
import scipy
import pandas
import numpy


# %% define functions

def basic_filter(
    signal,
    fs,
    cutoff,
    output   
):
    sos = scipy.signal.butter(
        1,
        cutoff,
        btype='highpass',
        output='sos'    
    )
    filtered_data = scipy.signal.sosfilt(sos,signal)
    
    return filtered_data
    
def beatcaller(
    df,
    voltage_column = 'ecg', 
    time_column = 'time',
    min_RR = 100,
    ecg_invert = False,
    ecg_filter = True,
    abs_thresh = None,
    perc_thresh = None,
    
):
    """
    Create a Dataframe of ECG outcome measures using an ecg signal as input
    
    adapted from code in Breathe Easy (develpoed by Ray Lab, used under GPLv3)

    Parameters:
        
    *Note, if both abs_thresh and perc_thresh are provided, abs_thresh will be 
    used
    
    Returns:
    - DataFrame: DataFrame containing timestamps, RR intervals, and heart rates.
    """
    
    time = df[time_column]
    voltage = df[voltage_column]
    
    # Invert ECG signal if required
    if ecg_invert:
        voltage = voltage * -1
    
    
    sampling_frequency = 1/(time[1]-time[0])
    
    if ecg_filter:
        voltage = basic_filter(
            voltage,
            fs = sampling_frequency,
            cutoff = 0.1,
            output='sos'
        )
        
        
    # Calculate isoelectric line
    isoelectric_line = pandas.Series(voltage).median()
    
    # Set threshold
    if perc_thresh:
        threshold = pandas.Series(voltage).quantile(perc_thresh/100)
    if abs_thresh:
        threshold = abs_thresh
        
    # Identify peaks in the ECG signal; adjust parameters as necessary for your data
    peaks,_ = scipy.signal.find_peaks(
        voltage, 
        height=threshold, 
        distance=int(min_RR/1000*sampling_frequency)
    )  
    
    # Extract timestamps for the detected peaks
    timestamps_peaks = numpy.take(time, peaks, axis=0)
    
    # Calculate RR intervals in seconds
    rr_intervals = numpy.diff(timestamps_peaks)
    
    # Calculate heart rate from rr intervals
    heart_rates = [60/ri for ri in rr_intervals]
    
    # Prepare dataframe to return
    beat_df = pandas.DataFrame({
        'ts': timestamps_peaks[:-1], # Exclude the last timestamp
        'RR': rr_intervals,
        'HR': heart_rates
        })
   
    return beat_df 