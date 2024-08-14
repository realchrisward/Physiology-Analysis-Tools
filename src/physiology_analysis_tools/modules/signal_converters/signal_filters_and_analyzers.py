# -*- coding: utf-8 -*-
"""
Created on Fri Jun  9 09:56:52 2023
signal filters and analyzers
@author: wardc
"""

__version__ = '0.0.1'

# %% import libraries
import pandas
from scipy import signal
import numpy


# %% define functions
def basicFilt(CT, sampleHz, f0, Q):
    """
    Applies a notch and butter filter to data, useful for reducing artifacts
    from electrical noise or voltage offsets. It is intended for use on an
    ECG signal.

    Parameters
    ----------
    CT : list or pandas.Series
        ecg voltage values
    sampleHz : Float
        the sampling rate of the data
    f0 : Float
        The target frequency to exclude
    Q : Float
        Quality factor. Dimensionless parameter that characterizes
        notch filter -3 dB bandwidth ``bw`` relative to its center
        frequency, ``Q = w0/bw``.

    Returns
    -------
    filtered : list or pandas.Series
        the filtered data

    """

    b, a = signal.iirnotch(f0 / (sampleHz / 2), Q)

    notched = signal.filtfilt(b, a, CT)

    b, a = signal.butter(1, 1 / (sampleHz / 2), btype="highpass")
    filtered = signal.filtfilt(b, a, notched)
    return filtered


def apply_smoothing_filter(
    signal_data,
    column,
    high_pass=0.1,
    high_pass_order=2,
    low_pass=50,
    low_pass_order=10,
):
    """
    Applies a highpass and lowpass filter to data, useful for reducing
    artifacts from electrical noise or bias flow pump vibrations. It is
    intended for use on a plethysmography or pneumotacography 'flow' signal.

    Parameters
    ----------
    signal_data : pandas.DataFrame
        data to be smoothed
    column : 'String'
        The name of the
    high_pass : Float, optional
        Frequency cutoff (Hz) for the highpass filter. The default is 0.1.
    high_pass_order : Integer, optional
        order value for the high pass filter. The default is 2.
    low_pass : Float, optional
        Frequency cutoff (Hz) for the low_pass filter. The default is 50.
    low_pass_order : Integer, optional
        order value for the low pass filter. The default is 10.

    Returns
    -------
    lpf_hpf_signal : List, DataSeries of Floats
        smoothed data

    """
    sampleHz = round(
        1 / (list(signal_data["ts"])[2] - list(signal_data["ts"])[1])
    )

    hpf_b, hpf_a = signal.butter(
        high_pass_order, high_pass / (sampleHz / 2), "high"
    )

    hpf_signal = signal.filtfilt(hpf_b, hpf_a, signal_data[column])

    lpf_b, lpf_a = signal.bessel(
        low_pass_order, low_pass / (sampleHz / 2), "low"
    )

    lpf_hpf_signal = signal.filtfilt(lpf_b, lpf_a, hpf_signal)

    return lpf_hpf_signal


def calculate_irreg_score(input_series):
    """
    takes a numpy compatible series and calculates an irregularity score
    using the formula |x[n]-X[n-1]| / X[n-1]. A series of the irregularity
    scores will be returned.
    First value will be zero as it has no comparison to change from.

    Parameters
    ----------
    input_series : Pandas.DataSeries of Floats
        Data to use for Irreg Score Calculation

    Returns
    -------
    output_series : Pandas.DataSeries of Floats
        Series of Irreg Scores, paired to input_series


    """
    output_series = numpy.insert(
        numpy.multiply(
            numpy.divide(
                numpy.abs(
                    numpy.subtract(
                        list(input_series[1:]), list(input_series[:-1])
                    )
                ),
                list(input_series[:-1]),
            ),
            100,
        ),
        0,
        numpy.nan,
    )
    return output_series


def basicPulse(animal_pressure, ambient_pressure, ts, threshold=100):
    # %%

    pressure = animal_pressure - ambient_pressure

    pressure_df = pandas.DataFrame({"ts": ts, "pressure": pressure})
    pressure_df["filtered_pressure"] = apply_smoothing_filter(
        pandas.DataFrame({"ts": ts, "pressure": pressure}), "pressure"
    )
    pressure_df["difpress"] = pressure_df.filtered_pressure.diff().fillna(0)
    pressure_df["rising"] = pressure_df.difpress > 0
    pressure_df["changing"] = pressure_df.rising.diff().fillna(False)
    pressure_df["plot_changeing"] = pressure_df["changing"].astype(int)

    pulse_list_systole = pressure_df[
        (pressure_df.changing) & (~pressure_df.rising)
    ][['ts', 'pressure']]
    pulse_list_systole.reset_index(inplace=True)

    pulse_list_diastole = pressure_df[
        (pressure_df.changing) & (pressure_df.rising)
    ][['ts', 'pressure']]
    pulse_list_diastole.reset_index(inplace=True)

    pulse_df = pandas.DataFrame()

    if pulse_list_systole['ts'].iloc[0] < pulse_list_diastole['ts'].iloc[0]:

        pulse_list_length = min(
            len(pulse_list_systole), len(pulse_list_diastole)
        )
        
        pulse_df[['index_sys','ts_sys','systolic']] = pulse_list_systole[['index','ts','pressure']].iloc[:pulse_list_length]
        pulse_df[['index_dia','ts_dia','diastolic']] = pulse_list_diastole[['index','ts','pressure']].iloc[:pulse_list_length]

    else:

        pulse_list_length = min(
            len(pulse_list_systole), len(pulse_list_diastole[1:])
        )

        pulse_df[['index_sys','ts_sys','systolic']] = pulse_list_systole[['index','ts','pressure']].iloc[:pulse_list_length]
        pulse_df[['index_dia','ts_dia','diastolic']] = pulse_list_diastole[['index','ts','pressure']].iloc[1:pulse_list_length+1].reset_index(drop = True)
        pulse_df['mean'] = pulse_df['diastolic']+(pulse_df['systolic']-pulse_df['diastolic'])/3
        
    return pressure_df, pulse_df
    
    # %%


def basicRR(
    CT,
    TS,
    noisecutoff=75,
    threshfactor=2,
    absthresh=0.2,
    minRR=0.05,
    ecg_filter="1",
    ecg_invert="0",
    analysis_parameters=None,
):
    """
    A simple RR based heart beat caller based on relative signal to noise
    thresholding.

    Parameters
    ----------
    CT : Pandas.DataSeries or List of Floats
        Series of voltage data
    TS : Pandas.DataSeries or List of Floats
        Series of timestamps paired to voltage data
    noisecutoff : Float, optional
        percentile of signal amplitude to use to set the 'noise level'.
        The default is 75.
    threshfactor : Float, optional
        fold change above noisecutoff to use for peak detection.
        The default is 4.
    absthresh : Float, optional
        absolute minimum voltage to use for peak detection. The default is 0.3.
    minRR : Float, optional
        minimum duration of heartbeat to be considered a valid beat.
        The default is 0.05.
    ecg_filter : Str ('1' or '0')
        1 = on, 0 = off for filtering of ecg signal.
    ecg_invert : Str ('1' or '0')
        1 = on, 0 = off for inversion of ecg signal.
    analysis_parameters : dict, optional
        dictionary which may contain settings to overide defaults

    Returns
    -------
    beat_df : Pandas.DataFrame
        DataFrame containing baseg heart beat parameters
        (ts, 'RR', 'HR', 'IS_RR', 'IS_HR')

    """
    if analysis_parameters is not None:
        noisecutoff = float(
            analysis_parameters.get("ecg_noise_cutoff", noisecutoff)
        )
        threshfactor = float(
            analysis_parameters.get("ecg_threshfactor", threshfactor)
        )
        absthresh = float(analysis_parameters.get("ecg_absthresh", absthresh))
        minRR = float(analysis_parameters.get("ecg_minRR", minRR))
        ecg_filter = str(analysis_parameters.get("ecg_filter", ecg_filter))
        ecg_invert = str(analysis_parameters.get("ecg_invert", ecg_invert))

    if ecg_invert == "1":
        CT = CT * -1

    if ecg_filter == "1":
        CT = basicFilt(CT, 1 / (TS[1] - TS[0]), 60, 30)

    # get above thresh
    noise_level = numpy.percentile(CT, noisecutoff)
    thresh = max(noise_level * threshfactor, absthresh)
    beats = {}
    index_crosses = []
    for i in range(len(CT) - 1):
        if CT[i + 1] >= thresh and CT[i] < thresh:
            index_crosses.append(i + 1)

    if len(index_crosses) == 0:

        return beats  # pass no beats

    prevJ = 0

    for i in index_crosses[:-1]:
        maxR = CT[i]

        TS_R = TS[i]
        for j in range(i, len(CT), 1):
            if CT[j] < thresh:
                break
            if j >= index_crosses[-1]:
                break
            elif CT[j] > maxR:
                maxR = CT[j]

        if j - prevJ >= minRR:
            beats[TS_R] = {"RR": TS[j] - TS[prevJ]}
            if prevJ == 0:
                beats[TS_R]["first"] = True
            else:
                beats[TS_R]["first"] = False
            prevJ = j

    if not beats:
        return pandas.DataFrame({"ts": [], "RR": []})

    beat_df = pandas.DataFrame(beats).transpose()
    beat_df.index.name = "ts"
    beat_df = beat_df.reset_index()
    beat_df["HR"] = 60 / beat_df["RR"]
    beat_df["IS_RR"] = calculate_irreg_score(beat_df["RR"])
    beat_df["IS_HR"] = calculate_irreg_score(beat_df["HR"])
    return beat_df
