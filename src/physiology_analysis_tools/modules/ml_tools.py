import numpy
import scipy
import pandas as pd
import sklearn.decomposition 
import sklearn.cluster

__version__ = "0.0.1"

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


def beatepocher(filtered_data_frame,
                beat_index,
                voltage_column = 'ecg', 
                window = 250,
                **kwargs):
    """
    Create a list numpy arrays, of the voltage over heartbeats detected in ECG, detrended and normalised. Takes ECG signal and the timestamps of the heartbeats as input.

    Parameters:
        df - dataframe - Dataframe of filtered data (Usually filtered using `basic_filter` function)
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


        if (start < min(filtered_data_frame.index)) or (end > max(filtered_data_frame.index)):
            continue


        data_epoch = filtered_data_frame.loc[start:end]

        beat_epoch = data_epoch[voltage_column].to_numpy()

        epochs_dict[index] = detrend_normalise(beat_epoch)

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

def beat_clusterer(epochs_dict, eps = 0.5, min_samples = 20):
    """
    Cluster the beats based on shape in PCA space using DBSCAN (density based clustering)

    Parameters:
        epochs_dict - Dictionary of Numpy arrays, with kets as index where heartbeat is detected and values being numpy arr of voltages. Output of beatepocher 
    """
    df = pd.DataFrame.from_dict(epochs_dict, orient= 'index')
    PCAobj = sklearn.decomposition.PCA(n_components=2)
    fit = PCAobj.fit_transform(df)
    fitDF= pd.DataFrame(data = fit, columns = ['PC1', 'PC2'])

    cluster = sklearn.cluster.DBSCAN(eps = eps, min_samples = min_samples).fit(fitDF)
    cluster_dict = dict(zip(epochs_dict.keys(), cluster.labels_))
    return cluster_dict


def call_arrhythmias_PCA(filtered_data_df,
                        beats_df, voltage_column_name,settings):
    beat_epochs_dict = beatepocher(filtered_data_df, beats_df.index, voltage_column= voltage_column_name,
                              window=settings.window_size)
    
    cluster_dict = beat_clusterer(beat_epochs_dict, eps = settings.eps, min_samples=settings.min_samples)

    #cluster_df =  pd.DataFrame.from_dict(cluster_dict, orient="index").rename(columns={0:"any_arrhythmia"}).astype("bool")
    #cluster_df['annot_any_arrhythmia'] = cluster_df["any_arrhythmia"].astype(int)
    cluster_df = pd.DataFrame.from_dict(cluster_dict,orient="index").rename(columns={0:"abn_cluster"}).astype("bool")
    cluster_df['annot_abn_cluster'] = cluster_df['abn_cluster'].astype(int)
    # Clear previously assigned arrythmias. e.g if Heuristic model was used first

    # for col in beats_df.columns:
    #     if col not in ["ts", "RR", "HR", "beats"]:
    #         beats_df = beats_df.drop(columns = col)

    # beats_df = beats_df.join(cluster_df, how = "inner")

    # return beats_df

    return cluster_df

class Settings():
    def __init__(self):
        self.window_size = 100
        self.eps = 0.5
        self.min_samples = 20
