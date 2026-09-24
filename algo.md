# Beat-of-Interest Detection

How the app decides which beats are worth a technician's attention, and how
the shape-analysis algorithm behind it works.

A "beat of interest" is any beat with `any_arrhythmia` set — flagged either
by the heuristic rules (rate/timing) or by the shape clustering described
below. `GET /beats/of-interest` returns their timestamps; the graph opens
focused on the first one and steps through the rest.

---

## The shape pipeline

Rate-based rules can't catch a beat that arrives on time but looks wrong.
That's what this handles: compare every beat's **waveform shape** against
the recording's own normal beat, and flag the outliers.

```
beats + signal
   → beatepocher_kde_clipped_rr_smooth()   slice one epoch per beat
   → detrend_normalise()                   compare shape, not amplitude
   → beat_clusterer()                      PCA → DBSCAN
   → abn_cluster                           True = shape unlike the rest
```

All of it lives in `src/physiology_analysis_tools/modules/ml_tools.py`,
called from `arrhythmia_detection.call_arrhythmias()` when the method is
`Unsupervised` or `Both`.

---

## Step 1 — How big a window? (the KDE part)

`beatepocher_kde_clipped_rr_smooth(filtered_data_frame, beat_df, ...)`

The window around each beat must be about one beat wide. Too narrow and
you're comparing noise; too wide and every epoch overlaps its neighbours and
they all look alike. But "one beat wide" differs per recording (heart rate)
and per file (sample rate), so it can't be a fixed number of samples.

**It's derived from the recording's own RR intervals**, in four steps:

1. **Take all RR intervals** — `np.diff(beat_df.ts)`.

2. **Keep only the dominant mode.** RR intervals are multi-modal: a dense
   cluster at the normal beat-to-beat interval, plus a long tail wherever a
   beat was missed or the trace was noisy. A plain median gets dragged up by
   that tail. So `extract_first_modality_kde()` fits a Gaussian KDE over the
   RR distribution, finds its **tallest** peak, and cuts at the first local
   minimum to the *right* of it — everything left of that cut is the normal
   population.

   The tallest peak is the anchor rather than the leftmost one because
   double-detected beats create real peaks at very short RR values.

3. **Median, then clip.** `rr_window = clip(median(first_mode), min_rr,
   max_rr)`. The clip is the safety rail: a recording with too few or mostly
   artefactual beats can't produce an absurd window.

4. **Convert to samples.**
   ```
   sample_rate  = 1 / (time[1] - time[0])
   total_window = beat_window × rr_window × sample_rate
   ```
   split around the R-peak by `bias` — `0` centres it, `+1` puts it entirely
   after the peak, `-1` entirely before.

Each epoch is then **resampled to a fixed `beat_length`**, so epochs stay
directly comparable even when the window sizes differ, and finally
detrended and scaled to unit length so the comparison is about shape rather
than amplitude or baseline drift.

Beats whose window would run off the start or end of the recording are
skipped — they have no cluster label and come back as "not evaluated"
rather than being forced into a verdict.

**Epochs are keyed by timestamp**, not row position, so cluster labels map
back onto the beat table by `ts`. Positional joins were a past source of
silent misalignment.

### Parameters

| Parameter | Default | What it does |
|---|---|---|
| `beat_window` | `1` | Window size, in multiples of the estimated RR interval |
| `beat_window_bias` | `0` | Shifts the window around the R-peak (−1 before … +1 after) |
| `beat_length` | `128` | Samples each epoch is resampled to |
| `kde_bandwidth` | `0.05` | KDE smoothing for the RR distribution |
| `min_rr` / `max_rr` | `0.1` / `0.1667` s | Clip range for the RR estimate (mouse rates: 360–600 bpm) |

`min_rr`/`max_rr` are the ones to revisit for a different species or
preparation — the defaults assume mouse.

---

## Step 2 — Which beats are outliers? (PCA + DBSCAN)

`beat_clusterer(epochs_dict, ...)`

Each epoch is a `beat_length`-dimensional vector. PCA reduces them to 2
dimensions (similar shapes land near each other), then DBSCAN groups them by
density. **Cluster `0` is the recording's normal beat shape; every other
label — including DBSCAN's own `-1` outlier label — becomes
`abn_cluster = True`.**

### `eps` is derived from the data, not fixed

`eps` is DBSCAN's neighbourhood radius — an absolute distance in a PCA space
whose scale is set by each recording's own amplitudes and shape variance.
No fixed value carries between recordings, and the previous fixed default
proved it: it labelled **every** beat an outlier on both real test files
(163/163 and 15/15), making the flag useless.

With `eps_auto` (the default), `auto_eps()` uses the standard k-distance
heuristic: take each point's distance to its `min_samples`-th nearest
neighbour and use a high percentile of those distances. The threshold then
means the same thing everywhere — "further from its neighbours than most
beats are" — and brings the long test file to 1/163 flagged.

`min_samples` is also clamped to the beat count, since a `min_samples`
larger than the number of beats would make DBSCAN call everything an outlier
no matter how tightly grouped.

| Parameter | Default | What it does |
|---|---|---|
| `eps_auto` | `True` | Derive `eps` from the data instead of using a fixed value |
| `eps_percentile` | `90` | Which k-distance percentile becomes `eps` when auto |
| `eps` | `0.5` | The fixed radius, used only when `eps_auto` is off |
| `min_samples` | `30` | Minimum points forming a cluster (clamped to the beat count) |

Fewer than 2 usable epochs raises a clear error rather than failing inside
scikit-learn — a short or sparse recording legitimately can't be clustered,
and the app falls back to heuristic-only detection in that case.

---

## Caveat worth knowing

The **heuristic** thresholds have not been retuned against these files. On
`9 long.txt` (median rate ~202 bpm) the default 300 bpm bradycardia
threshold flags 98 beats and the premature-beat rule 96, so 157/163 beats
come back flagged regardless of what the clustering decides. That's a
threshold/recording mismatch, not a detection failure — but it does mean
"beats of interest" will be most of the recording for files like that until
the thresholds match the preparation.
