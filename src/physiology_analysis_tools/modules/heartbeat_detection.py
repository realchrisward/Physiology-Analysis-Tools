# -*- coding: utf-8 -*-

"""
heartbeat_detection for ECG Analysis Tool
written by Christopher S Ward (C) 2024
"""

__version__ = "0.0.4"

# %% import libraries
import scipy
import pandas
import numpy



class Settings:
    def __init__(self):
        self.min_RR = 60
        self.ecg_invert = False
        self.ecg_filter = True
        self.ecg_filt_order = 2
        self.ecg_filt_cutoff = 5
        self.abs_thresh = None
        self.perc_thresh = 97

    def use_anesthetized_default(self):
        self.min_RR = 60
        self.ecg_invert = False
        self.ecg_filter = True
        self.ecg_filt_order = 2
        self.ecg_filt_cutoff = 5
        self.abs_thresh = None
        self.perc_thresh = 97

    def use_awake_default(self):
        # need to update this !!!
        self.min_RR = 60
        self.ecg_invert = False
        self.ecg_filter = True
        self.ecg_filt_order = 2
        self.ecg_filt_cutoff = 5
        self.abs_thresh = None
        self.perc_thresh = 97



# %% define functions
def basic_filter(order, signal, fs=1000, cutoff=5, output="sos", use_pandas=True):
    sos = scipy.signal.butter(order, cutoff, fs=fs, btype="highpass", output="sos")
    filtered_data = scipy.signal.sosfiltfilt(sos, signal)

    if use_pandas:
        return pandas.Series(filtered_data)
    else:
        return filtered_data


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



def beatcaller(
    df,
    voltage_column="ecg",
    time_column="time",
    min_RR=100,
    ecg_invert=False,
    ecg_abs_value=False,
    ecg_filter=True,
    ecg_filt_order=2,
    ecg_filt_cutoff=5,
    abs_thresh=None,
    perc_thresh=None,
    breath_filter=True,
    breath_filter_cutoff=None,
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
    df = df.reset_index(drop=True)
    time = df[time_column]
    voltage = df[voltage_column]

    # Invert ECG signal if required
    if ecg_invert:
        voltage = voltage * -1

    if ecg_abs_value:
        voltage = voltage.abs()

    sampling_frequency = 1 / (time[1] - time[0])

    if ecg_filter:
        voltage = basic_filter(
            ecg_filt_order,
            voltage,
            fs=sampling_frequency,
            cutoff=ecg_filt_cutoff,
            output="sos",
        )

    # Calculate isoelectric line
    isoelectric_line = pandas.Series(voltage).median()

    # Set threshold
    if perc_thresh:
        threshold = pandas.Series(voltage).quantile(perc_thresh / 100)
    if abs_thresh:
        threshold = abs_thresh

    print(f"beat detection threshold: {threshold}")

    # Identify peaks in the ECG signal; adjust parameters as necessary for your data
    peaks, _ = scipy.signal.find_peaks(
        voltage, height=threshold, distance=int(min_RR / 1000 * sampling_frequency)
    )

    # Extract timestamps for the detected peaks
    timestamps_peaks = time.take(peaks)

    # Calculate R peak heights
    r_amp = voltage.take(peaks)

    if breath_filter or breath_filter_cutoff is not None:
        if breath_filter_cutoff is None:
            breath_filter_cutoff = 0.4

        R_amplitude_neighbors = calculate_moving_average(
            voltage, window=3, include_current=False
        )

        R_amplitude_filter = R_amplitude_neighbors >= breath_filter_cutoff

        timestamps_peaks = timestamps_peaks[R_amplitude_filter]
        r_amp = r_amp[R_amplitude_filter]

    # Calculate RR intervals in seconds
    rr_intervals = numpy.diff(timestamps_peaks)

    # Calculate heart rate from rr intervals
    heart_rates = [60 / ri for ri in rr_intervals]

    ones = [1 for ri in rr_intervals]

    # Prepare dataframe to return
    beat_df = pandas.DataFrame(
        {
            "ts": timestamps_peaks[1:-1],  # Exclude the last timestamp
            "RR": rr_intervals[:-1],
            "R_amplitude": r_amp[1:-1],
            "HR": heart_rates[:-1],
            "beats": ones[:-1],
        }
    )

    return beat_df
