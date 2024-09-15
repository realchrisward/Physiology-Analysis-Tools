import numpy
import scipy

def basic_filter(
    order,
    signal,
    fs = 1000,
    cutoff = 5,
    output = 'sos'  
):
    """
    Copy of heartbeat_detection.basic_filter, where inverted beats are detected automatically. 
    These inverted beats will be flipped to allow for comparison across datasets.
    """
    sos = scipy.signal.butter(
        order,
        cutoff,
        fs=fs,
        btype='highpass',
        output='sos'    
    )
    filtered_data = scipy.signal.sosfiltfilt(sos,signal)

    peaks,_ = scipy.signal.find_peaks(filtered_data, distance =100)
    neg_peaks,_ = scipy.signal.find_peaks(-filtered_data, distance =100)

    ecg_invert = abs(filtered_data[peaks].mean()) < abs(filtered_data[neg_peaks].mean())

    if ecg_invert:
        return filtered_data * -1
    
    return filtered_data


def beatepocher(df,
                beat_index,
                voltage_column = 'ecg', 
                window = 250,
                **kwargs):
    """
    Create a list numpy arrays, of the voltage over heartbeats detected in ECG. Takes ECG signal and the timestamps of the heartbeats as input.

    Parameters:
        df - dataframe - Dataframe of filtered data. Usually the return of basic_filter
        beat_index - list of ints - List of the indices where the beat was detected. e.g. Index of the dataframe returned by heartbeat_detection.beat_caller
        voltage_column - str - Column name for the voltages in `df`
        window - int - Number of datapoints to include in the window
        **kwargs -
            pre - int - shares of window before R peak
            post - int - shares of window after R peak
            Pre and post arguments allow you to skew the window to either before (pre) or after (post) the R peak/detected beat.
            e.g To skew the window to 2/3rds before the R peak, submit pre = 2, post =1

    Returns:
    - Dictionary of Numpy arrays, containing voltages over course of each window around a heartbeat. Labelled with index where beat was detected
    """
    pre_window = window/2
    post_window = window/2

    if "pre" in kwargs:
        if "post" in kwargs:
            pre = kwargs.get('pre')
            post = kwargs.get('post')

            pre_window = pre/(pre+post) * window
            post_window = post/(pre+post) * window

    epochs_dict = {}

    for index in beat_index:

        start = index - round(pre_window, 0)
        end = index + round(post_window, 0)


        if (start < min(df.index)) or (end > max(df.index)):
            continue


        data_epoch = df.loc[start:end]

        beat_epoch = data_epoch[voltage_column].to_numpy()

        epochs_dict[index] = beat_epoch

    return epochs_dict

def detrend_normalise(signal):
    """
    Detrend and normalise ECG signal for a single Epoch.

    Parameters:
        signal - array_like - voltages of ECG signal (for a single epoch)

    Returns :
    - Array: Array of detrended and normalised signal

    """
    signal = scipy.signal.detrend(signal)
    dn_signal= signal / numpy.linalg.norm(signal, ord=2) 
    return dn_signal
