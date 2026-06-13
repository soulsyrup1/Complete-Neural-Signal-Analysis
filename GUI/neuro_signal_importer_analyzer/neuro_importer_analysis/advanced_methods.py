from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from scipy.signal import butter, find_peaks, sosfiltfilt, welch


BANDS = {
    "delta": (1.0, 4.0),
    "theta": (4.0, 8.0),
    "alpha": (8.0, 13.0),
    "beta": (13.0, 30.0),
    "gamma": (30.0, 80.0),
}


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _safe_float(value: Any) -> float | None:
    try:
        out = float(value)
        if np.isfinite(out):
            return out
    except Exception:
        return None
    return None


def _safe_int(value: Any, default: int) -> int:
    try:
        out = int(value)
        if out > 0:
            return out
    except Exception:
        pass
    return default


def _positive_float(value: Any, default: float) -> float:
    try:
        out = float(value)
        if math.isfinite(out) and out > 0:
            return out
    except Exception:
        pass
    return float(default)


def _load_signal_path(recording_dir: Path) -> Path:
    candidates = [
        recording_dir / "signal.npy",
        recording_dir / "processed" / "signal.npy",
        recording_dir / "raw" / "signal.npy",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No signal.npy found in {recording_dir}")


def _load_metadata(recording_dir: Path, signal_path: Path) -> dict[str, Any]:
    for candidate in (
        signal_path.parent / "metadata.json",
        recording_dir / "metadata.json",
        recording_dir / "processed" / "metadata.json",
        recording_dir / "raw" / "metadata.json",
    ):
        if not candidate.exists():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _sampling_rate(metadata: dict[str, Any], override: Any = None) -> float:
    if override not in (None, ""):
        return _positive_float(override, 1.0)
    for key in ("sampling_rate", "sampling_rate_hz", "sample_rate_hz", "fs", "sfreq"):
        if metadata.get(key) not in (None, ""):
            return _positive_float(metadata[key], 1.0)
    return 1.0


def _load_channels(recording_dir: Path, signal_path: Path, n_channels: int) -> list[str]:
    for candidate in (
        signal_path.parent / "channels.csv",
        recording_dir / "channels.csv",
        recording_dir / "processed" / "channels.csv",
        recording_dir / "raw" / "channels.csv",
    ):
        if not candidate.exists():
            continue
        try:
            df = pd.read_csv(candidate)
            name_col = next((col for col in ("name", "channel_name", "label", "channel") if col in df.columns), None)
            if name_col:
                names = [str(x) for x in df[name_col].tolist()]
                if len(names) == n_channels:
                    return names
        except Exception:
            pass
    return [f"ch_{i:03d}" for i in range(n_channels)]


def _load_recording(recording_dir: str | Path, *, sampling_rate: Any = None, max_samples: Any = None) -> dict[str, Any]:
    root = Path(recording_dir).expanduser().resolve()
    signal_path = _load_signal_path(root)
    signal = np.load(signal_path, mmap_mode="r")
    if signal.ndim != 2:
        raise ValueError(f"Expected signal.npy samples x channels, got shape {signal.shape}")
    n_samples, n_channels = signal.shape
    metadata = _load_metadata(root, signal_path)
    fs = _sampling_rate(metadata, sampling_rate)
    channels = _load_channels(root, signal_path, n_channels)
    max_count = _safe_int(max_samples, 240_000) if max_samples not in (None, "") else 240_000
    if n_samples > max_count:
        step = int(math.ceil(n_samples / max_count))
        data = np.asarray(signal[::step], dtype=np.float64)
        fs_eff = fs / step if fs > 0 else fs
    else:
        step = 1
        data = np.asarray(signal, dtype=np.float64)
        fs_eff = fs
    data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
    return {
        "recording_dir": str(root),
        "signal_path": str(signal_path),
        "metadata": metadata,
        "signal": data,
        "sampling_rate_hz": fs_eff,
        "original_sampling_rate_hz": fs,
        "analysis_downsample_step": step,
        "n_samples": int(n_samples),
        "analysis_samples": int(data.shape[0]),
        "n_channels": int(n_channels),
        "channels": channels,
        "duration_sec": float(n_samples / fs) if fs > 0 else None,
    }


def _nperseg(sample_count: int, fs: float) -> int:
    if sample_count < 8:
        return max(1, sample_count)
    return int(min(sample_count, max(8, min(4096, round(fs * 4.0)))))


def _band_power_summary(recording_dir: str | Path, params: dict[str, Any]) -> dict[str, Any]:
    band_name = str(params.get("band") or "custom").lower()
    if band_name in BANDS and params.get("min_hz") in (None, "") and params.get("max_hz") in (None, ""):
        min_hz, max_hz = BANDS[band_name]
    else:
        min_hz = _positive_float(params.get("min_hz"), 8.0)
        max_hz = _positive_float(params.get("max_hz"), 13.0)
        band_name = next((name for name, pair in BANDS.items() if abs(pair[0] - min_hz) < 1e-9 and abs(pair[1] - max_hz) < 1e-9), "custom")
    if max_hz <= min_hz:
        raise ValueError("max_hz must be greater than min_hz")
    rec = _load_recording(recording_dir, sampling_rate=params.get("sampling_rate"), max_samples=params.get("max_analysis_samples"))
    x = rec["signal"]
    fs = float(rec["sampling_rate_hz"] or 1.0)
    nps = _nperseg(x.shape[0], fs)
    freqs, psd = welch(x - np.mean(x, axis=0, keepdims=True), fs=fs, nperseg=nps, axis=0)
    total_mask = (freqs > 0) & (freqs < min(fs / 2.0, 120.0))
    band_mask = (freqs >= min_hz) & (freqs < min(max_hz, fs / 2.0))
    if not np.any(band_mask):
        raise ValueError(f"No frequency bins available in requested band {min_hz:g}-{max_hz:g} Hz at analysis sampling rate {fs:g} Hz")
    band_power = np.trapezoid(psd[band_mask, :], freqs[band_mask], axis=0)
    total_power = np.trapezoid(psd[total_mask, :], freqs[total_mask], axis=0) if np.any(total_mask) else np.sum(psd, axis=0)
    rel_power = band_power / (total_power + 1e-20)
    peak_idx = np.argmax(psd[band_mask, :], axis=0)
    band_freqs = freqs[band_mask]
    rows: list[dict[str, Any]] = []
    for i, channel in enumerate(rec["channels"]):
        rows.append({
            "channel": channel,
            "channel_index": i,
            "band_power": _safe_float(band_power[i]),
            "relative_power": _safe_float(rel_power[i]),
            "peak_frequency_hz": _safe_float(band_freqs[int(peak_idx[i])]),
        })
    top_i = int(np.nanargmax(band_power)) if band_power.size else 0
    return {
        "band_power_summary": {
            "band": band_name,
            "min_hz": min_hz,
            "max_hz": max_hz,
            "rows": rows,
            "summary": {
                "mean_band_power": _safe_float(np.nanmean(band_power)),
                "median_relative_power": _safe_float(np.nanmedian(rel_power)),
                "top_channel": rows[top_i]["channel"] if rows else None,
                "top_channel_power": _safe_float(band_power[top_i]) if rows else None,
                "n_channels": rec["n_channels"],
                "sampling_rate_analysis_hz": fs,
            },
        }
    }


def _standardize(signal: np.ndarray) -> np.ndarray:
    x = np.asarray(signal, dtype=np.float64)
    x = x - np.median(x, axis=0, keepdims=True)
    scale = np.std(x, axis=0, keepdims=True)
    scale[~np.isfinite(scale) | (scale <= 1e-12)] = 1.0
    return x / scale


def _maybe_filter_for_spikes(x: np.ndarray, fs: float, params: dict[str, Any]) -> tuple[np.ndarray, str]:
    low = params.get("bandpass_low_hz")
    high = params.get("bandpass_high_hz")
    if low in (None, "") and high in (None, ""):
        return _standardize(x), "standardized_broadband"
    low_f = _safe_float(low)
    high_f = _safe_float(high)
    nyq = fs / 2.0
    if nyq <= 0:
        return _standardize(x), "standardized_broadband_no_valid_fs"
    try:
        if low_f and high_f and 0 < low_f < high_f < nyq:
            sos = butter(3, [low_f / nyq, high_f / nyq], btype="bandpass", output="sos")
            return _standardize(sosfiltfilt(sos, x, axis=0)), f"bandpass_{low_f:g}_{high_f:g}_hz"
        if low_f and 0 < low_f < nyq:
            sos = butter(3, low_f / nyq, btype="highpass", output="sos")
            return _standardize(sosfiltfilt(sos, x, axis=0)), f"highpass_{low_f:g}_hz"
        if high_f and 0 < high_f < nyq:
            sos = butter(3, high_f / nyq, btype="lowpass", output="sos")
            return _standardize(sosfiltfilt(sos, x, axis=0)), f"lowpass_{high_f:g}_hz"
    except Exception:
        pass
    return _standardize(x), "standardized_broadband_filter_unavailable"


def _detect_spikes_from_recording(recording_dir: str | Path, params: dict[str, Any]) -> tuple[dict[str, list[float]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    rec = _load_recording(recording_dir, sampling_rate=params.get("sampling_rate"), max_samples=params.get("max_analysis_samples"))
    x, filter_note = _maybe_filter_for_spikes(rec["signal"], float(rec["sampling_rate_hz"] or 1.0), params)
    fs = float(rec["sampling_rate_hz"] or 1.0)
    threshold_multiplier = _positive_float(params.get("threshold_multiplier"), 5.0)
    refractory_ms = _positive_float(params.get("refractory_ms"), 2.0)
    max_spikes_per_channel = _safe_int(params.get("max_spikes_per_channel"), 5000)
    polarity = str(params.get("polarity") or "negative").lower()
    distance = max(1, int(round((refractory_ms / 1000.0) * fs)))
    spikes: dict[str, list[float]] = {}
    rows: list[dict[str, Any]] = []
    duration = float(rec["analysis_samples"] / fs) if fs > 0 else 0.0
    for ch_i, channel in enumerate(rec["channels"]):
        trace = x[:, ch_i]
        mad = np.median(np.abs(trace - np.median(trace)))
        threshold = threshold_multiplier * (1.4826 * mad if mad > 1e-12 else np.std(trace) + 1e-12)
        peak_indices: np.ndarray
        if polarity == "positive":
            peak_indices, _ = find_peaks(trace, height=threshold, distance=distance)
        elif polarity == "both":
            pos, _ = find_peaks(trace, height=threshold, distance=distance)
            neg, _ = find_peaks(-trace, height=threshold, distance=distance)
            peak_indices = np.unique(np.concatenate([pos, neg]))
        else:
            peak_indices, _ = find_peaks(-trace, height=threshold, distance=distance)
        times = (peak_indices.astype(np.float64) / fs).tolist()
        kept_times = times[:max_spikes_per_channel]
        spikes[channel] = [round(float(t), 6) for t in kept_times]
        rows.append({
            "channel": channel,
            "channel_index": ch_i,
            "spike_count": int(len(times)),
            "stored_spike_count": int(len(kept_times)),
            "firing_rate_hz": _safe_float(len(times) / duration) if duration > 0 else 0.0,
            "threshold": _safe_float(threshold),
            "first_spike_sec": _safe_float(times[0]) if times else None,
        })
    summary = {
        "total_spikes": int(sum(row["spike_count"] for row in rows)),
        "mean_firing_rate_hz": _safe_float(np.mean([row["firing_rate_hz"] for row in rows])) if rows else 0.0,
        "active_channels": int(sum(1 for row in rows if row["spike_count"] > 0)),
        "duration_sec": duration,
        "sampling_rate_analysis_hz": fs,
        "analysis_downsample_step": rec["analysis_downsample_step"],
        "filter_note": filter_note,
        "polarity": polarity,
        "threshold_multiplier": threshold_multiplier,
        "refractory_ms": refractory_ms,
        "spikes_truncated_per_channel_at": max_spikes_per_channel,
    }
    return spikes, rows, summary, rec


def _spike_detect(recording_dir: str | Path, params: dict[str, Any]) -> dict[str, Any]:
    spikes, rows, summary, rec = _detect_spikes_from_recording(recording_dir, params)
    return {
        "spike_detect": {
            "spikes": spikes,
            "rows": rows,
            "summary": summary,
            "channels": rec["channels"],
        },
        "mea": {
            "spikes": spikes,
            "duration_sec": summary["duration_sec"],
        },
    }


def _flatten_spikes(spikes: dict[str, list[float]]) -> list[tuple[float, str]]:
    events: list[tuple[float, str]] = []
    for channel, times in spikes.items():
        for t in times:
            try:
                events.append((float(t), channel))
            except Exception:
                pass
    events.sort(key=lambda item: item[0])
    return events


def _network_burst(recording_dir: str | Path, params: dict[str, Any]) -> dict[str, Any]:
    spikes, rows, spike_summary, rec = _detect_spikes_from_recording(recording_dir, params)
    duration = float(spike_summary.get("duration_sec") or 0.0)
    bin_size_ms = _positive_float(params.get("bin_size_ms"), 10.0)
    threshold_count = _safe_int(params.get("threshold_count"), max(3, int(math.ceil(0.1 * max(1, rec["n_channels"])))) )
    merge_gap_ms = _positive_float(params.get("merge_gap_ms"), 50.0)
    min_spikes = _safe_int(params.get("min_spikes"), 5)
    bin_size = bin_size_ms / 1000.0
    merge_gap = merge_gap_ms / 1000.0
    if duration <= 0:
        bins = np.asarray([0.0, bin_size])
    else:
        bins = np.arange(0.0, duration + bin_size, bin_size)
        if bins.size < 2:
            bins = np.asarray([0.0, max(bin_size, duration)])
    all_events = _flatten_spikes(spikes)
    all_times = np.asarray([t for t, _ in all_events], dtype=float)
    counts, edges = np.histogram(all_times, bins=bins) if all_times.size else (np.zeros(len(bins) - 1, dtype=int), bins)
    active = counts >= threshold_count
    bursts: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for idx, is_active in enumerate(active):
        if not is_active:
            continue
        start = float(edges[idx])
        end = float(edges[idx + 1])
        if current and start - float(current["end_sec"]) <= merge_gap:
            current["end_sec"] = end
            current["peak_bin_count"] = max(int(current["peak_bin_count"]), int(counts[idx]))
        else:
            if current:
                bursts.append(current)
            current = {"start_sec": start, "end_sec": end, "peak_bin_count": int(counts[idx])}
    if current:
        bursts.append(current)
    filtered: list[dict[str, Any]] = []
    for burst in bursts:
        start = float(burst["start_sec"])
        end = float(burst["end_sec"])
        burst_events = [(t, ch) for t, ch in all_events if start <= t <= end]
        if len(burst_events) < min_spikes:
            continue
        channels = sorted({ch for _, ch in burst_events})
        filtered.append({
            "start_sec": round(start, 6),
            "end_sec": round(end, 6),
            "duration_ms": round((end - start) * 1000.0, 3),
            "spike_count": int(len(burst_events)),
            "active_channels": int(len(channels)),
            "peak_bin_count": int(burst["peak_bin_count"]),
            "channels": channels[:30],
        })
    return {
        "network_burst": {
            "bursts": filtered,
            "timeline": filtered,
            "summary": {
                "burst_count": len(filtered),
                "bin_size_ms": bin_size_ms,
                "threshold_count": threshold_count,
                "merge_gap_ms": merge_gap_ms,
                "min_spikes": min_spikes,
                "total_detected_spikes": spike_summary["total_spikes"],
                "duration_sec": duration,
            },
            "spike_detection_summary": spike_summary,
        }
    }


def _bin_spikes(spikes: dict[str, list[float]], channels: list[str], duration_sec: float, bin_size_sec: float) -> np.ndarray:
    bin_count = max(1, int(math.ceil(duration_sec / bin_size_sec)))
    binned = np.zeros((len(channels), bin_count), dtype=np.float64)
    last_bin = bin_count - 1
    for row, channel in enumerate(channels):
        times = np.asarray(spikes.get(channel, []), dtype=float)
        if not times.size:
            continue
        valid = times[(times >= 0.0) & (times <= duration_sec)]
        if valid.size == 0:
            continue
        indices = np.minimum((valid / bin_size_sec).astype(int), last_bin)
        np.add.at(binned[row], indices, 1)
    return binned


def _electrode_connectivity(recording_dir: str | Path, params: dict[str, Any]) -> dict[str, Any]:
    spikes, rows, spike_summary, rec = _detect_spikes_from_recording(recording_dir, params)
    channels = rec["channels"]
    duration = float(spike_summary.get("duration_sec") or 0.0)
    bin_size_ms = _positive_float(params.get("bin_size_ms"), 5.0)
    max_lag_ms = float(params.get("max_lag_ms") if params.get("max_lag_ms") not in (None, "") else 20.0)
    if not math.isfinite(max_lag_ms) or max_lag_ms < 0:
        raise ValueError("max_lag_ms must be a non-negative finite number")
    bin_size_sec = bin_size_ms / 1000.0
    lag_bins = int(round((max_lag_ms / 1000.0) / bin_size_sec))
    binned = _bin_spikes(spikes, channels, duration, bin_size_sec)
    c = len(channels)
    matrix = np.eye(c, dtype=float)
    links: list[dict[str, Any]] = []
    strongest: dict[str, Any] | None = None
    for i in range(c):
        for j in range(i + 1, c):
            best_score = 0.0
            best_lag = 0
            best_support = 0.0
            for lag in range(-lag_bins, lag_bins + 1):
                if lag >= 0:
                    left = binned[i, lag:]
                    right = binned[j, : binned.shape[1] - lag]
                else:
                    offset = -lag
                    left = binned[i, : binned.shape[1] - offset]
                    right = binned[j, offset:]
                if left.size == 0 or right.size == 0:
                    score = 0.0
                    support = 0.0
                else:
                    denom = math.sqrt(float(np.dot(left, left) * np.dot(right, right)))
                    support = float(np.dot(left, right))
                    score = support / denom if denom > 0 else 0.0
                if (score, support, -abs(lag)) > (best_score, best_support, -abs(best_lag)):
                    best_score, best_lag, best_support = score, lag, support
            score_r = round(float(max(0.0, best_score)), 6)
            lag_ms = round(float(best_lag * bin_size_ms), 6)
            matrix[i, j] = matrix[j, i] = score_r
            link = {
                "source": channels[i],
                "target": channels[j],
                "score": score_r,
                "lag_ms": lag_ms,
                "support": int(round(best_support)),
            }
            links.append(link)
            if strongest is None or (link["score"], link["support"]) > (strongest["score"], strongest["support"]):
                strongest = link
    links.sort(key=lambda row: (-row["score"], -row["support"], row["source"], row["target"]))
    max_links = _safe_int(params.get("max_links"), 200)
    return {
        "electrode_connectivity": {
            "channels": channels,
            "matrix": np.round(matrix, 6).tolist(),
            "links": links[:max_links],
            "summary": {
                "method_note": "reference binned spike-train cross-correlation over spikes detected from the continuous signal",
                "strongest_pair": strongest or {"source": "", "target": "", "score": 0.0, "lag_ms": 0.0, "support": 0},
                "bin_size_ms": bin_size_ms,
                "max_lag_ms": max_lag_ms,
                "stored_link_count": min(len(links), max_links),
                "total_pair_count": len(links),
                "spike_detection_summary": spike_summary,
            },
        }
    }


HFD_MODE_CONFIGS: dict[str, dict[str, Any]] = {
    "normal": {
        "k_max": 10,
        "decimate": 1,
        "max_samples": None,
        "rolling_window_sec": 2.0,
        "rolling_step_sec": 1.0,
        "rolling_max_windows": 80,
    },
    "fast": {
        "k_max": 8,
        "decimate": 2,
        "max_samples": 60_000,
        "rolling_window_sec": 2.0,
        "rolling_step_sec": 2.0,
        "rolling_max_windows": 40,
    },
    "ultrafast": {
        "k_max": 6,
        "decimate": 5,
        "max_samples": 20_000,
        "rolling_window_sec": 1.0,
        "rolling_step_sec": 3.0,
        "rolling_max_windows": 18,
    },
}

HFD_SCALP_XY: dict[str, tuple[float, float]] = {
    "Fp1": (-0.45, 1.00), "Fpz": (0.00, 1.08), "Fp2": (0.45, 1.00),
    "F7": (-0.95, 0.60), "F3": (-0.45, 0.55), "Fz": (0.00, 0.60),
    "F4": (0.45, 0.55), "F8": (0.95, 0.60),
    "FC5": (-0.75, 0.25), "FC1": (-0.25, 0.25), "FC2": (0.25, 0.25), "FC6": (0.75, 0.25),
    "M1": (-1.15, 0.05), "T7": (-1.00, 0.00), "C3": (-0.50, 0.00),
    "Cz": (0.00, 0.00), "C4": (0.50, 0.00), "T8": (1.00, 0.00), "M2": (1.15, 0.05),
    "CP5": (-0.75, -0.30), "CP1": (-0.25, -0.30), "CP2": (0.25, -0.30), "CP6": (0.75, -0.30),
    "P7": (-0.90, -0.60), "P3": (-0.45, -0.60), "Pz": (0.00, -0.65),
    "P4": (0.45, -0.60), "P8": (0.90, -0.60),
    "POz": (0.00, -0.88), "O1": (-0.35, -1.00), "Oz": (0.00, -1.08), "O2": (0.35, -1.00),
}

HFD_REGIONS: dict[str, list[str]] = {
    "Frontal": ["Fp1", "Fpz", "Fp2", "F7", "F3", "Fz", "F4", "F8"],
    "Frontocentral": ["FC5", "FC1", "FC2", "FC6"],
    "Central": ["T7", "C3", "Cz", "C4", "T8"],
    "Centroparietal": ["CP5", "CP1", "CP2", "CP6"],
    "Parietal": ["P7", "P3", "Pz", "P4", "P8"],
    "Occipital": ["POz", "O1", "Oz", "O2"],
    "Mastoid": ["M1", "M2"],
}

HFD_LR_PAIRS: list[tuple[str, str]] = [
    ("Fp1", "Fp2"), ("F7", "F8"), ("F3", "F4"), ("FC5", "FC6"), ("FC1", "FC2"),
    ("T7", "T8"), ("C3", "C4"), ("CP5", "CP6"), ("CP1", "CP2"),
    ("P7", "P8"), ("P3", "P4"), ("O1", "O2"),
]


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return _safe_float(value)


def _hfd_bool(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _higuchi_fd_curve(x: np.ndarray, k_max: int = 10) -> tuple[float, np.ndarray | None, np.ndarray | None]:
    x = np.asarray(x, dtype=float)
    n = int(x.size)
    if n < k_max + 2:
        return np.nan, None, None
    k_vals = np.arange(1, k_max + 1, dtype=int)
    lk_values: list[float] = []
    for k in k_vals:
        lm_vals: list[float] = []
        for m in range(k):
            n_max = (n - m - 1) // k
            if n_max < 1:
                continue
            idx1 = m + np.arange(1, n_max + 1) * k
            idx0 = m + np.arange(0, n_max) * k
            diffs = np.abs(x[idx1] - x[idx0]).sum()
            lm_vals.append(float(diffs * (n - 1) / (n_max * k * k)))
        lk_values.append(float(np.mean(lm_vals)) if lm_vals else np.nan)
    lk = np.asarray(lk_values, dtype=float)
    mask = np.isfinite(lk) & (lk > 0)
    if np.sum(mask) < 2:
        return np.nan, k_vals, lk
    slope = np.polyfit(np.log(k_vals[mask]), np.log(lk[mask]), 1)[0]
    return float(-slope), k_vals, lk


def _higuchi_fit_diagnostics(k_vals: np.ndarray | None, lk: np.ndarray | None) -> dict[str, float]:
    empty = {"slope": np.nan, "intercept": np.nan, "hfd": np.nan, "r2": np.nan, "rmse": np.nan}
    if k_vals is None or lk is None:
        return empty
    mask = np.isfinite(lk) & (lk > 0)
    if np.sum(mask) < 2:
        return empty
    logk = np.log(k_vals[mask])
    logl = np.log(lk[mask])
    slope, intercept = np.polyfit(logk, logl, 1)
    pred = slope * logk + intercept
    resid = logl - pred
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((logl - np.mean(logl)) ** 2))
    return {
        "slope": float(slope),
        "intercept": float(intercept),
        "hfd": float(-slope),
        "r2": float(1.0 - ss_res / (ss_tot + 1e-12)),
        "rmse": float(np.sqrt(np.mean(resid ** 2))),
    }


def _load_hfd_segment(recording_dir: str | Path, params: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    root = Path(recording_dir).expanduser().resolve()
    signal_path = _load_signal_path(root)
    signal = np.load(signal_path, mmap_mode="r")
    if signal.ndim != 2:
        raise ValueError(f"Expected 2D signal.npy, got shape {signal.shape}")
    metadata = _load_metadata(root, signal_path)
    fs = _sampling_rate(metadata, params.get("sampling_rate"))
    # Canonical exports are samples x channels. If a channels.csv strongly suggests
    # channels x samples, transpose after slicing.
    n0, n1 = int(signal.shape[0]), int(signal.shape[1])
    ch_sample_major = _load_channels(root, signal_path, n1)
    ch_channel_major = _load_channels(root, signal_path, n0)
    assume_channel_major = len(ch_channel_major) == n0 and len(ch_sample_major) != n1 and n0 < n1
    total_samples = n1 if assume_channel_major else n0
    channels = ch_channel_major if assume_channel_major else ch_sample_major
    start_sec = _optional_float(params.get("segment_start_sec"))
    end_sec = _optional_float(params.get("segment_end_sec"))
    start_idx = 0 if start_sec is None else max(0, int(start_sec * fs))
    end_idx = total_samples if end_sec is None else min(total_samples, int(end_sec * fs))
    if end_idx <= start_idx:
        raise ValueError("segment_end_sec must be greater than segment_start_sec")
    decimate = _safe_int(params.get("decimate"), int(cfg.get("decimate") or 1))
    if assume_channel_major:
        data = np.asarray(signal[:, start_idx:end_idx:decimate], dtype=np.float64).T
    else:
        data = np.asarray(signal[start_idx:end_idx:decimate, :], dtype=np.float64)
    max_samples_param = params.get("max_samples")
    if max_samples_param in (None, ""):
        max_samples = cfg.get("max_samples")
    else:
        max_samples = _safe_int(max_samples_param, int(cfg.get("max_samples") or data.shape[0]))
    if max_samples is not None and data.shape[0] > int(max_samples):
        data = data[: int(max_samples), :]
    data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
    return {
        "root": root,
        "signal_path": signal_path,
        "metadata": metadata,
        "signal": data,
        "channels": channels,
        "sampling_rate_hz": fs / decimate if fs > 0 else fs,
        "original_sampling_rate_hz": fs,
        "decimate": decimate,
        "source_total_samples": total_samples,
        "segment_start_sec": float(start_idx / fs) if fs > 0 else 0.0,
        "segment_end_sec": float(end_idx / fs) if fs > 0 else None,
        "segment_original_samples": int(end_idx - start_idx),
    }


def _fallback_scalp_points(channels: list[str], hfd_map: dict[str, float]) -> list[dict[str, Any]]:
    n = max(1, len(channels))
    points: list[dict[str, Any]] = []
    for i, ch in enumerate(channels):
        val = hfd_map.get(ch)
        if val is None or not np.isfinite(val):
            continue
        if ch in HFD_SCALP_XY:
            x, y = HFD_SCALP_XY[ch]
            layout = "10-20"
        else:
            angle = -math.pi / 2 + 2 * math.pi * i / n
            x, y = 0.95 * math.cos(angle), 0.95 * math.sin(angle)
            layout = "circular"
        points.append({"channel": ch, "x": float(x), "y": float(y), "higuchi_fd": float(val), "layout": layout})
    return points


def _hfd_regional_summary(channels: list[str], hfd_map: dict[str, float]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assigned = {ch for group in HFD_REGIONS.values() for ch in group}
    region_rows: list[dict[str, Any]] = []
    region_points: list[dict[str, Any]] = []
    region_map = {**HFD_REGIONS}
    other = [ch for ch in channels if ch not in assigned]
    if other:
        region_map["Other"] = other
    for region_name, chans in region_map.items():
        vals: list[float] = []
        for ch in chans:
            val = hfd_map.get(ch)
            if val is not None and np.isfinite(val):
                vals.append(float(val))
                region_points.append({"region": region_name, "channel": ch, "higuchi_fd": float(val)})
        if not vals:
            continue
        arr = np.asarray(vals, dtype=float)
        sd = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
        region_rows.append({
            "region": region_name,
            "mean_hfd": float(np.mean(arr)),
            "std_hfd": sd,
            "sem_hfd": float(sd / math.sqrt(len(arr))) if len(arr) > 1 else 0.0,
            "n_channels": int(len(arr)),
        })
    return region_rows, region_points


def _hfd_asymmetry(hfd_map: dict[str, float]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for left, right in HFD_LR_PAIRS:
        lv = hfd_map.get(left)
        rv = hfd_map.get(right)
        if lv is None or rv is None or not np.isfinite(lv) or not np.isfinite(rv):
            continue
        rows.append({
            "pair": f"{left}-{right}",
            "left_channel": left,
            "right_channel": right,
            "left_hfd": float(lv),
            "right_hfd": float(rv),
            "asymmetry_left_minus_right": float(lv - rv),
        })
    return rows


def _higuchi_fractal_dimension(recording_dir: str | Path, params: dict[str, Any]) -> dict[str, Any]:
    mode = str(params.get("mode") or "fast").lower()
    if mode not in HFD_MODE_CONFIGS:
        raise ValueError(f"Unknown Higuchi mode {mode!r}; use normal, fast, or ultrafast")
    cfg = dict(HFD_MODE_CONFIGS[mode])
    k_max = _safe_int(params.get("k_max"), int(cfg["k_max"]))
    rec = _load_hfd_segment(recording_dir, params, cfg)
    signal = np.asarray(rec["signal"], dtype=np.float64)
    channels = list(rec["channels"])
    if signal.ndim != 2 or signal.shape[1] != len(channels):
        raise ValueError(f"Loaded segment shape/channel mismatch: {signal.shape}, {len(channels)} channel labels")

    rows: list[dict[str, Any]] = []
    fit_rows: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []
    for ch_idx, ch_name in enumerate(channels):
        x = signal[:, ch_idx].astype(float)
        x = (x - np.mean(x)) / (np.std(x) + 1e-12)
        hfd, k_vals, lk = _higuchi_fd_curve(x, k_max=k_max)
        diag = _higuchi_fit_diagnostics(k_vals, lk)
        row = {"channel": ch_name, "channel_index": ch_idx, "higuchi_fd": _safe_float(hfd)}
        rows.append(row)
        fit_row = {
            "channel": ch_name,
            "channel_index": ch_idx,
            "higuchi_fd": _safe_float(diag["hfd"]),
            "fit_slope": _safe_float(diag["slope"]),
            "fit_intercept": _safe_float(diag["intercept"]),
            "fit_r2": _safe_float(diag["r2"]),
            "fit_rmse": _safe_float(diag["rmse"]),
        }
        fit_rows.append(fit_row)
        if k_vals is not None and lk is not None:
            mask = np.isfinite(lk) & (lk > 0)
            log_k = np.log(k_vals[mask]).tolist()
            log_l = np.log(lk[mask]).tolist()
            fit_log_l = []
            if np.isfinite(diag["slope"]) and np.isfinite(diag["intercept"]):
                fit_log_l = (diag["slope"] * np.asarray(log_k) + diag["intercept"]).tolist()
            curves.append({
                "channel": ch_name,
                "channel_index": ch_idx,
                "k_vals": k_vals.tolist(),
                "Lk": lk.tolist(),
                "log_k": log_k,
                "log_Lk": log_l,
                "fit_log_Lk": fit_log_l,
                "higuchi_fd": _safe_float(hfd),
                "fit_r2": _safe_float(diag["r2"]),
            })

    rows = sorted(rows, key=lambda r: (r["higuchi_fd"] is None, -(r["higuchi_fd"] or -1e9)))
    hfd_map = {row["channel"]: float(row["higuchi_fd"]) for row in rows if row["higuchi_fd"] is not None}
    values = np.asarray(list(hfd_map.values()), dtype=float)
    region_rows, region_points = _hfd_regional_summary(channels, hfd_map)
    asym_rows = _hfd_asymmetry(hfd_map)

    rolling_payload: dict[str, Any] = {"enabled": False, "times_sec": [], "channels": channels, "matrix": []}
    temporal_rows: list[dict[str, Any]] = []
    if _hfd_bool(params.get("rolling"), True):
        fs_eff = float(rec["sampling_rate_hz"] or 1.0)
        window_sec = _positive_float(params.get("rolling_window_sec"), float(cfg["rolling_window_sec"]))
        step_sec = _positive_float(params.get("rolling_step_sec"), float(cfg["rolling_step_sec"]))
        max_windows = _safe_int(params.get("rolling_max_windows"), int(cfg["rolling_max_windows"]))
        window_samples = max(int(window_sec * fs_eff), k_max + 5)
        step_samples = max(int(step_sec * fs_eff), 1)
        possible = np.arange(0, max(1, signal.shape[0] - window_samples + 1), step_samples, dtype=int)
        if possible.size > max_windows:
            possible = possible[np.linspace(0, possible.size - 1, max_windows).astype(int)]
        rolling = np.full((len(channels), len(possible)), np.nan, dtype=float)
        for w_idx, s0 in enumerate(possible):
            s1 = int(s0 + window_samples)
            for ch_idx in range(len(channels)):
                xw = signal[s0:s1, ch_idx].astype(float)
                xw = (xw - np.mean(xw)) / (np.std(xw) + 1e-12)
                hfd_w, _, _ = _higuchi_fd_curve(xw, k_max=k_max)
                rolling[ch_idx, w_idx] = hfd_w
        times = (possible / fs_eff).astype(float).tolist() if fs_eff > 0 else [float(x) for x in possible]
        rolling_payload = {
            "enabled": True,
            "window_sec": window_sec,
            "step_sec": step_sec,
            "times_sec": times,
            "channels": channels,
            "matrix": np.round(rolling, 6).tolist(),
        }
        temporal_std = np.nanstd(rolling, axis=1) if rolling.size else np.full(len(channels), np.nan)
        temporal_rows = sorted([
            {"channel": ch, "channel_index": i, "rolling_hfd_std": _safe_float(temporal_std[i]), "mean_hfd": _safe_float(hfd_map.get(ch))}
            for i, ch in enumerate(channels)
        ], key=lambda r: (r["rolling_hfd_std"] is None, -(r["rolling_hfd_std"] or -1e9)))

    fit_map = {row["channel"]: row for row in fit_rows}
    complexity_rows = []
    for row in temporal_rows:
        ch = row["channel"]
        fit = fit_map.get(ch, {})
        complexity_rows.append({
            "channel": ch,
            "mean_hfd": row.get("mean_hfd"),
            "rolling_hfd_std": row.get("rolling_hfd_std"),
            "fit_r2": fit.get("fit_r2"),
            "fit_rmse": fit.get("fit_rmse"),
        })

    out_dir = Path(rec["root"]) / "advanced_methods" / "higuchi_fractal_dimension"
    out_dir.mkdir(parents=True, exist_ok=True)
    output_paths: dict[str, str] = {}
    try:
        summary_df = pd.DataFrame(rows)
        fit_df = pd.DataFrame(fit_rows)
        region_df = pd.DataFrame(region_rows)
        asym_df = pd.DataFrame(asym_rows)
        temporal_df = pd.DataFrame(temporal_rows)
        summary_path = out_dir / "higuchi_fd_summary.csv"
        fit_path = out_dir / "higuchi_fd_fit_diagnostics.csv"
        summary_df.to_csv(summary_path, index=False)
        fit_df.to_csv(fit_path, index=False)
        output_paths["summary_csv"] = str(summary_path)
        output_paths["fit_diagnostics_csv"] = str(fit_path)
        if region_rows:
            p = out_dir / "regional_higuchi_fd_summary.csv"
            region_df.to_csv(p, index=False)
            output_paths["regional_summary_csv"] = str(p)
        if asym_rows:
            p = out_dir / "left_right_higuchi_fd_asymmetry.csv"
            asym_df.to_csv(p, index=False)
            output_paths["asymmetry_csv"] = str(p)
        if temporal_rows:
            p = out_dir / "rolling_higuchi_fd_temporal_variability.csv"
            temporal_df.to_csv(p, index=False)
            output_paths["temporal_variability_csv"] = str(p)
        if curves:
            npz = out_dir / "higuchi_fd_curves.npz"
            save_dict: dict[str, np.ndarray] = {}
            for c in curves:
                name = str(c["channel"]).replace("/", "_")
                save_dict[f"{name}_k_vals"] = np.asarray(c["k_vals"], dtype=float)
                save_dict[f"{name}_Lk"] = np.asarray(c["Lk"], dtype=float)
            if save_dict:
                np.savez(npz, **save_dict)
                output_paths["curves_npz"] = str(npz)
    except Exception as exc:
        output_paths["save_warning"] = repr(exc)

    summary = {
        "mode": mode,
        "k_max": k_max,
        "n_channels": len(channels),
        "analysis_samples": int(signal.shape[0]),
        "sampling_rate_analysis_hz": _safe_float(rec["sampling_rate_hz"]),
        "duration_sec": _safe_float(signal.shape[0] / float(rec["sampling_rate_hz"] or 1.0)),
        "segment_start_sec": _safe_float(rec["segment_start_sec"]),
        "segment_end_sec": _safe_float(rec["segment_end_sec"]),
        "mean_hfd": _safe_float(np.nanmean(values)) if values.size else None,
        "median_hfd": _safe_float(np.nanmedian(values)) if values.size else None,
        "min_hfd": _safe_float(np.nanmin(values)) if values.size else None,
        "max_hfd": _safe_float(np.nanmax(values)) if values.size else None,
        "top_channel": rows[0]["channel"] if rows else None,
        "top_channel_hfd": rows[0]["higuchi_fd"] if rows else None,
        "decimate": rec["decimate"],
    }

    return {
        "higuchi_fractal_dimension": {
            "summary": summary,
            "rows": rows,
            "fit_diagnostics": fit_rows,
            "curves": curves,
            "scalp_layout": {"points": _fallback_scalp_points(channels, hfd_map)},
            "regional_summary": region_rows,
            "regional_points": region_points,
            "asymmetry": asym_rows,
            "rolling": rolling_payload,
            "temporal_variability": temporal_rows,
            "complexity_instability": complexity_rows,
            "outputs": output_paths,
        }
    }


def _neuromouse_advanced_plots(recording_dir: str | Path, params: dict[str, Any]) -> dict[str, Any]:
    """Build the full NeuroMouse advanced data objects for guaranteed plot rendering.

    This method is intentionally separate from the legacy browser workbench. It
    runs the backend NeuroMouse dataset builder and writes a concrete data.json
    containing polar_chronomap, kuramoto, channel_network, and tda fields, then
    points the user to the server-rendered /advanced-analysis page.
    """
    from neuro_importer_neuromouse.advanced_plot_renderer import availability
    from neuro_importer_neuromouse.static_dataset_builder import write_speedmouse_dataset

    root = Path(recording_dir).expanduser().resolve()
    output_dir = root / "neuromouse" / "advanced_plots"
    paths = write_speedmouse_dataset(
        root,
        output_dir,
        dataset_id=str(params.get("dataset_id") or root.name or "advanced_plots"),
        sampling_rate=params.get("sampling_rate") if params.get("sampling_rate") not in (None, "") else None,
        max_analysis_samples=_safe_int(params.get("max_analysis_samples"), 240000),
        max_windows=_safe_int(params.get("max_windows"), 600),
    )
    data_path = Path(paths["data_json"])
    data = json.loads(data_path.read_text(encoding="utf-8"))
    meta = data.get("meta") or {}
    return {
        "neuromouse_advanced_plots": {
            "summary": {
                "data_json": str(data_path),
                "plot_page_url": "/advanced-analysis",
                "dataset_id": meta.get("dataset_id"),
                "n_channels": meta.get("n_channels"),
                "sampling_rate_hz": meta.get("sampling_rate_hz"),
                "advanced_plots_generated": True,
            },
            "availability": availability(data),
            "data_json": str(data_path),
            "plot_page_url": "/advanced-analysis",
            "open_in_neuromouse_url": "/neuromouse-latest/",
        }
    }



# ---- v0.11.14 Embedded attractor fractal-dimension analysis ----
# Adapted from the user's embedded_fractal_dimension_research_fast script.
# This native method can either use precomputed 2D..10D embedding .npy files
# when they exist, or generate delay embeddings directly from canonical
# signal.npy so the analysis works from a normal converted recording.

EMBEDDED_FD_MODE_CONFIGS: dict[str, dict[str, Any]] = {
    "ultra": {
        "embedding_dims": [2, 3, 4, 6, 8, 10],
        "max_points_per_embedding": 4000,
        "corr_subsample_n": 800,
        "corr_n_pairs": 25000,
        "corr_n_r": 16,
        "corr_cmin": 5e-4,
        "corr_cmax": 2e-1,
        "box_subsample_n": 2500,
        "box_eps_count": 8,
        "diagnostic_top_n": 6,
        "max_analysis_samples": 90000,
    },
    "fast": {
        "embedding_dims": list(range(2, 11)),
        "max_points_per_embedding": 7000,
        "corr_subsample_n": 1200,
        "corr_n_pairs": 60000,
        "corr_n_r": 20,
        "corr_cmin": 3e-4,
        "corr_cmax": 2e-1,
        "box_subsample_n": 4000,
        "box_eps_count": 10,
        "diagnostic_top_n": 9,
        "max_analysis_samples": 120000,
    },
    "balanced": {
        "embedding_dims": list(range(2, 11)),
        "max_points_per_embedding": 15000,
        "corr_subsample_n": 2500,
        "corr_n_pairs": 200000,
        "corr_n_r": 28,
        "corr_cmin": 1e-4,
        "corr_cmax": 1e-1,
        "box_subsample_n": 8000,
        "box_eps_count": 12,
        "diagnostic_top_n": 12,
        "max_analysis_samples": 180000,
    },
    "full": {
        "embedding_dims": list(range(2, 11)),
        "max_points_per_embedding": 25000,
        "corr_subsample_n": 5000,
        "corr_n_pairs": 500000,
        "corr_n_r": 36,
        "corr_cmin": 5e-5,
        "corr_cmax": 2e-1,
        "box_subsample_n": 15000,
        "box_eps_count": 16,
        "diagnostic_top_n": 16,
        "max_analysis_samples": 240000,
    },
}


def _efd_standardize_cols(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    x = np.where(np.isfinite(x), x, np.nan)
    col_mean = np.nanmean(x, axis=0, keepdims=True)
    bad = np.where(~np.isfinite(x))
    if bad[0].size:
        x[bad] = np.take(col_mean, bad[1])
    mu = np.mean(x, axis=0, keepdims=True)
    sd = np.std(x, axis=0, keepdims=True) + 1e-12
    return (x - mu) / sd


def _efd_rescale_to_unit_cube(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    xmin = np.min(x, axis=0, keepdims=True)
    xmax = np.max(x, axis=0, keepdims=True)
    rng = xmax - xmin
    rng[rng < 1e-12] = 1.0
    return (x - xmin) / rng


def _efd_subsample_rows(x: np.ndarray, max_points: int | None, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(x)
    n = len(x)
    if max_points is None or n <= max_points:
        return x, np.arange(n)
    idx = np.sort(rng.choice(n, size=int(max_points), replace=False))
    return x[idx], idx


def _efd_linear_fit_r2(x: np.ndarray, y: np.ndarray) -> tuple[float, np.ndarray, np.ndarray | None]:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    if np.sum(mask) < 3:
        return np.nan, np.array([np.nan, np.nan]), None
    xv = x[mask]
    yv = y[mask]
    coef = np.polyfit(xv, yv, 1)
    yhat = np.polyval(coef, xv)
    ss_res = float(np.sum((yv - yhat) ** 2))
    ss_tot = float(np.sum((yv - np.mean(yv)) ** 2))
    r2 = 1.0 - ss_res / (ss_tot + 1e-12)
    return float(r2), coef, yhat


def _efd_box_counting_dimension(x: np.ndarray, eps_vals: np.ndarray, max_points: int | None, rng: np.random.Generator) -> dict[str, Any]:
    x, _ = _efd_subsample_rows(np.asarray(x, dtype=float), max_points, rng)
    if x.ndim != 2 or len(x) < 10:
        return {"dimension": np.nan, "inv_eps": [], "counts": [], "logx_fit": [], "logy_fit": [], "fit_line": [], "r2": np.nan, "n_fit": 0}
    x = _efd_rescale_to_unit_cube(_efd_standardize_cols(x))
    counts: list[float] = []
    inv_eps: list[float] = []
    for eps in eps_vals:
        if not np.isfinite(eps) or eps <= 0:
            continue
        bins = np.floor(x / eps).astype(np.int64)
        bins = np.clip(bins, 0, int(np.ceil(1 / eps)) + 2)
        n_boxes = int(len(np.unique(bins, axis=0)))
        if n_boxes > 0:
            counts.append(float(n_boxes))
            inv_eps.append(float(1.0 / eps))
    counts_arr = np.asarray(counts, dtype=float)
    inv_arr = np.asarray(inv_eps, dtype=float)
    mask = np.isfinite(counts_arr) & (counts_arr > 0) & np.isfinite(inv_arr) & (inv_arr > 0)
    if np.sum(mask) < 3:
        return {"dimension": np.nan, "inv_eps": inv_arr, "counts": counts_arr, "logx_fit": [], "logy_fit": [], "fit_line": [], "r2": np.nan, "n_fit": int(np.sum(mask))}
    logx = np.log(inv_arr[mask])
    logy = np.log(counts_arr[mask])
    r2, coef, fit_line = _efd_linear_fit_r2(logx, logy)
    return {
        "dimension": float(coef[0]),
        "inv_eps": inv_arr[mask],
        "counts": counts_arr[mask],
        "logx_fit": logx,
        "logy_fit": logy,
        "fit_line": fit_line if fit_line is not None else [],
        "r2": r2,
        "n_fit": int(np.sum(mask)),
    }


def _efd_correlation_dimension_sampled(
    x: np.ndarray,
    *,
    subsample_n: int,
    n_pairs: int,
    n_r: int,
    cmin: float,
    cmax: float,
    rng: np.random.Generator,
) -> dict[str, Any]:
    x = _efd_standardize_cols(np.asarray(x, dtype=float))
    n = len(x)
    empty = {"dimension": np.nan, "rs": [], "Cs": [], "logr_fit": [], "logC_fit": [], "fit_line": [], "n_fit": 0, "r2": np.nan, "n_used": int(n), "n_pairs_used": 0}
    if n < 50:
        return empty
    if n > subsample_n:
        idx = np.sort(rng.choice(n, size=int(subsample_n), replace=False))
        x = x[idx]
        n = len(x)
    n_pairs_use = max(1000, int(n_pairs))
    i = rng.integers(0, n, size=n_pairs_use)
    j = rng.integers(0, n, size=n_pairs_use)
    keep = i != j
    i = i[keep]
    j = j[keep]
    if len(i) < 1000:
        out = dict(empty)
        out.update({"n_used": int(n), "n_pairs_used": int(len(i))})
        return out
    d = np.linalg.norm(x[i] - x[j], axis=1)
    d = d[np.isfinite(d) & (d > 0)]
    if len(d) < 1000:
        out = dict(empty)
        out.update({"n_used": int(n), "n_pairs_used": int(len(d))})
        return out
    qlo = float(np.quantile(d, 0.01))
    qhi = float(np.quantile(d, 0.50))
    if not np.isfinite(qlo) or not np.isfinite(qhi) or qhi <= qlo:
        out = dict(empty)
        out.update({"n_used": int(n), "n_pairs_used": int(len(d))})
        return out
    rs = np.logspace(np.log10(qlo), np.log10(qhi), int(n_r))
    d_sorted = np.sort(d)
    cs = np.searchsorted(d_sorted, rs, side="right") / len(d_sorted)
    mask = np.isfinite(cs) & (cs > cmin) & (cs < cmax) & np.isfinite(rs) & (rs > 0)
    n_fit = int(np.sum(mask))
    if n_fit < 4:
        return {"dimension": np.nan, "rs": rs, "Cs": cs, "logr_fit": [], "logC_fit": [], "fit_line": [], "n_fit": n_fit, "r2": np.nan, "n_used": int(n), "n_pairs_used": int(len(d))}
    log_r = np.log(rs[mask])
    log_c = np.log(cs[mask])
    r2, coef, fit_line = _efd_linear_fit_r2(log_r, log_c)
    return {
        "dimension": float(coef[0]),
        "rs": rs,
        "Cs": cs,
        "logr_fit": log_r,
        "logC_fit": log_c,
        "fit_line": fit_line if fit_line is not None else [],
        "n_fit": n_fit,
        "r2": r2,
        "n_used": int(n),
        "n_pairs_used": int(len(d)),
    }


def _efd_embedding_file(root: Path, channel: str, emb_dim: int) -> Path | None:
    candidates: list[Path] = []
    base = root / "embedding_data"
    if emb_dim == 2:
        candidates.append(base / "2dembedding_data" / f"2dembedded_{channel}.npy")
    elif emb_dim == 3:
        candidates.append(base / "3dembedding_data" / f"3dembedded_{channel}.npy")
    elif 4 <= emb_dim <= 10:
        candidates.append(base / "embeddings_4to10" / f"{emb_dim}dembedded_{channel}.npy")
    candidates.append(root / f"{emb_dim}dembedded_{channel}.npy")
    for c in candidates:
        if c.exists():
            return c
    return None


def _efd_delay_embedding_1d(x: np.ndarray, emb_dim: int, tau: int, max_points: int | None, rng: np.random.Generator) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    x = (x - np.mean(x)) / (np.std(x) + 1e-12)
    tau = max(1, int(tau))
    n_vec = len(x) - (int(emb_dim) - 1) * tau
    if n_vec <= max(50, int(emb_dim) + 5):
        return np.empty((0, int(emb_dim)), dtype=float)
    if max_points is not None and n_vec > int(max_points):
        starts = np.sort(rng.choice(n_vec, size=int(max_points), replace=False))
    else:
        starts = np.arange(n_vec)
    return np.column_stack([x[starts + lag * tau] for lag in range(int(emb_dim))])


def _efd_parse_dims(value: Any, default: list[int]) -> list[int]:
    if value in (None, ""):
        return default
    if isinstance(value, (list, tuple)):
        vals = value
    else:
        vals = str(value).replace(";", ",").split(",")
    out: list[int] = []
    for raw in vals:
        try:
            iv = int(str(raw).strip())
            if 2 <= iv <= 20 and iv not in out:
                out.append(iv)
        except Exception:
            pass
    return out or default


def _efd_add_derived_metrics(rows: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.DataFrame(rows)
    if df.empty:
        return df, pd.DataFrame()
    for col in ("corr_dim_d2", "boxcount_fd", "emb_dim"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["d2_minus_boxfd"] = df["corr_dim_d2"] - df["boxcount_fd"]
    df["d2_over_emb_dim"] = df["corr_dim_d2"] / df["emb_dim"]
    df["boxfd_over_emb_dim"] = df["boxcount_fd"] / df["emb_dim"]
    out_parts = []
    for _, sub in df.groupby("channel", sort=False):
        sub = sub.sort_values("emb_dim").copy()
        sub["corr_dim_d2_increment"] = sub["corr_dim_d2"].diff()
        sub["boxcount_fd_increment"] = sub["boxcount_fd"].diff()
        out_parts.append(sub)
    df = pd.concat(out_parts, ignore_index=True) if out_parts else df
    summary_rows: list[dict[str, Any]] = []
    for channel, sub in df.groupby("channel"):
        sub = sub.sort_values("emb_dim")
        valid_d2 = sub[np.isfinite(sub["corr_dim_d2"])]
        valid_box = sub[np.isfinite(sub["boxcount_fd"])]
        row: dict[str, Any] = {"channel": channel}
        if len(valid_d2):
            last_d2 = valid_d2.iloc[-1]
            row.update({
                "max_available_emb_dim": int(last_d2["emb_dim"]),
                "terminal_corr_dim_d2": float(last_d2["corr_dim_d2"]),
                "terminal_corr_r2": float(last_d2["corr_fit_r2"]),
                "terminal_corr_fit_points": int(last_d2["corr_fit_points"]),
                "terminal_d2_over_m": float(last_d2["d2_over_emb_dim"]),
            })
        else:
            row.update({"max_available_emb_dim": None, "terminal_corr_dim_d2": np.nan, "terminal_corr_r2": np.nan, "terminal_corr_fit_points": None, "terminal_d2_over_m": np.nan})
        if len(valid_box):
            last_box = valid_box.iloc[-1]
            row.update({"terminal_boxcount_fd": float(last_box["boxcount_fd"]), "terminal_box_r2": float(last_box["box_fit_r2"])})
        else:
            row.update({"terminal_boxcount_fd": np.nan, "terminal_box_r2": np.nan})
        if len(valid_d2) >= 3:
            tail = valid_d2.tail(3)
            row["d2_tail_slope"] = float(np.polyfit(tail["emb_dim"], tail["corr_dim_d2"], 1)[0])
            row["d2_tail_std"] = float(np.nanstd(tail["corr_dim_d2"]))
        else:
            row["d2_tail_slope"] = np.nan
            row["d2_tail_std"] = np.nan
        if len(valid_box) >= 3:
            tail = valid_box.tail(3)
            row["box_tail_slope"] = float(np.polyfit(tail["emb_dim"], tail["boxcount_fd"], 1)[0])
            row["box_tail_std"] = float(np.nanstd(tail["boxcount_fd"]))
        else:
            row["box_tail_slope"] = np.nan
            row["box_tail_std"] = np.nan
        terminal_d2 = row.get("terminal_corr_dim_d2", np.nan)
        terminal_box = row.get("terminal_boxcount_fd", np.nan)
        tail_slope = row.get("d2_tail_slope", 0.0)
        terminal_r2 = row.get("terminal_corr_r2", np.nan)
        row["dimension_complexity_score"] = (
            0.55 * (terminal_d2 if np.isfinite(terminal_d2) else 0.0)
            + 0.25 * (terminal_box if np.isfinite(terminal_box) else 0.0)
            - 0.10 * abs(tail_slope if np.isfinite(tail_slope) else 0.0)
            + 0.10 * (terminal_r2 if np.isfinite(terminal_r2) else 0.0)
        )
        summary_rows.append(row)
    return df, pd.DataFrame(summary_rows)


def _embedded_fractal_dimension(recording_dir: str | Path, params: dict[str, Any]) -> dict[str, Any]:
    mode = str(params.get("mode") or "fast").lower()
    if mode not in EMBEDDED_FD_MODE_CONFIGS:
        mode = "fast"
    cfg = dict(EMBEDDED_FD_MODE_CONFIGS[mode])
    seed = _safe_int(params.get("random_seed"), 0)
    rng = np.random.default_rng(seed)
    dims = _efd_parse_dims(params.get("embedding_dims"), cfg["embedding_dims"])
    max_channels_raw = params.get("max_channels")
    max_channels = None if max_channels_raw in (None, "", "all") else _safe_int(max_channels_raw, 0)
    max_analysis_samples = _safe_int(params.get("max_analysis_samples"), int(cfg["max_analysis_samples"]))
    rec = _load_recording(recording_dir, sampling_rate=params.get("sampling_rate"), max_samples=max_analysis_samples)
    root = Path(rec["recording_dir"])
    signal = np.asarray(rec["signal"], dtype=float)
    fs = float(rec["sampling_rate_hz"] or 1.0)
    channels = list(rec["channels"])
    if max_channels and max_channels > 0:
        signal = signal[:, :max_channels]
        channels = channels[:max_channels]
    tau_samples = params.get("tau_samples")
    if tau_samples not in (None, ""):
        tau = _safe_int(tau_samples, 1)
    else:
        tau_ms = _positive_float(params.get("tau_ms"), 10.0)
        tau = max(1, int(round(fs * tau_ms / 1000.0)))
    eps_count = _safe_int(params.get("box_eps_count"), int(cfg["box_eps_count"]))
    box_eps = np.logspace(np.log10(1 / 5), np.log10(1 / 60), eps_count)
    rows: list[dict[str, Any]] = []
    diagnostics: dict[tuple[str, int], dict[str, Any]] = {}
    use_precomputed_count = 0
    generated_count = 0
    for ch_idx, ch_name in enumerate(channels):
        x = signal[:, ch_idx]
        for emb_dim in dims:
            try:
                source = "generated_delay_embedding"
                fpath = _efd_embedding_file(root, ch_name, emb_dim)
                if fpath is not None:
                    X = np.asarray(np.load(fpath), dtype=float)
                    if X.ndim != 2:
                        raise ValueError(f"bad embedding shape {X.shape}")
                    source = "precomputed_embedding"
                    use_precomputed_count += 1
                    X_used, _ = _efd_subsample_rows(X, int(cfg["max_points_per_embedding"]), rng)
                else:
                    X_used = _efd_delay_embedding_1d(x, emb_dim, tau, int(cfg["max_points_per_embedding"]), rng)
                    generated_count += 1
                n_points_original = int(len(X_used))
                if X_used.ndim != 2 or len(X_used) < 50:
                    raise ValueError("insufficient embedding points")
                box = _efd_box_counting_dimension(X_used, box_eps, int(cfg["box_subsample_n"]), rng)
                corr = _efd_correlation_dimension_sampled(
                    X_used,
                    subsample_n=int(cfg["corr_subsample_n"]),
                    n_pairs=int(cfg["corr_n_pairs"]),
                    n_r=int(cfg["corr_n_r"]),
                    cmin=float(cfg["corr_cmin"]),
                    cmax=float(cfg["corr_cmax"]),
                    rng=rng,
                )
                diagnostics[(ch_name, int(emb_dim))] = {"box": box, "corr": corr}
                rows.append({
                    "channel": ch_name,
                    "channel_index": ch_idx,
                    "emb_dim": int(emb_dim),
                    "n_points_original": n_points_original,
                    "n_points_used": int(len(X_used)),
                    "ambient_dim": int(X_used.shape[1]),
                    "boxcount_fd": _safe_float(box["dimension"]),
                    "box_fit_r2": _safe_float(box["r2"]),
                    "box_fit_points": int(box["n_fit"]),
                    "corr_dim_d2": _safe_float(corr["dimension"]),
                    "corr_fit_r2": _safe_float(corr["r2"]),
                    "corr_fit_points": int(corr["n_fit"]),
                    "corr_n_used": int(corr["n_used"]),
                    "corr_pairs_used": int(corr["n_pairs_used"]),
                    "embedding_source": source,
                    "status": "ok",
                    "error": "",
                })
            except Exception as exc:
                rows.append({
                    "channel": ch_name,
                    "channel_index": ch_idx,
                    "emb_dim": int(emb_dim),
                    "n_points_original": None,
                    "n_points_used": None,
                    "ambient_dim": int(emb_dim),
                    "boxcount_fd": None,
                    "box_fit_r2": None,
                    "box_fit_points": 0,
                    "corr_dim_d2": None,
                    "corr_fit_r2": None,
                    "corr_fit_points": 0,
                    "corr_n_used": 0,
                    "corr_pairs_used": 0,
                    "embedding_source": "error",
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                })
    ok_rows = [r for r in rows if r.get("status") == "ok"]
    results_df, channel_summary_df = _efd_add_derived_metrics(ok_rows)
    mean_df = pd.DataFrame()
    if not results_df.empty:
        mean_df = results_df.groupby("emb_dim").agg(
            boxcount_fd=("boxcount_fd", "mean"),
            corr_dim_d2=("corr_dim_d2", "mean"),
            box_fit_r2=("box_fit_r2", "mean"),
            corr_fit_r2=("corr_fit_r2", "mean"),
            n_rows=("channel", "count"),
        ).reset_index()
    out_dir = root / "advanced_methods" / "embedded_fractal_dimension"
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    try:
        results_csv = out_dir / f"embedded_fractal_dimension_summary_{mode}.csv"
        pd.DataFrame(rows).to_csv(results_csv, index=False)
        outputs["summary_csv"] = str(results_csv)
        if not channel_summary_df.empty:
            p = out_dir / f"embedded_fractal_dimension_channel_summary_{mode}.csv"
            channel_summary_df.to_csv(p, index=False)
            outputs["channel_summary_csv"] = str(p)
        if not mean_df.empty:
            p = out_dir / f"embedded_fractal_dimension_mean_by_dim_{mode}.csv"
            mean_df.to_csv(p, index=False)
            outputs["mean_by_dimension_csv"] = str(p)
        txt = out_dir / f"embedded_fractal_dimension_summary_{mode}.txt"
        top = channel_summary_df.sort_values("dimension_complexity_score", ascending=False).head(20) if not channel_summary_df.empty else pd.DataFrame()
        txt.write_text(
            "Embedded attractor fractal-dimension analysis\n"
            "=============================================\n\n"
            f"mode: {mode}\nembedding_dims: {dims}\ntau_samples: {tau}\n"
            f"channels: {len(channels)}\nrows: {len(rows)}\nok_rows: {len(ok_rows)}\n\n"
            + (top.to_string(index=False) if not top.empty else "No successful rows."),
            encoding="utf-8",
        )
        outputs["summary_txt"] = str(txt)
    except Exception:
        pass
    diag_rows = []
    if not results_df.empty:
        top_diag = results_df[np.isfinite(results_df["corr_dim_d2"]) & np.isfinite(results_df["corr_fit_r2"])].sort_values(["corr_fit_r2", "corr_dim_d2"], ascending=False).head(int(cfg["diagnostic_top_n"]))
        for _, row in top_diag.iterrows():
            key = (str(row["channel"]), int(row["emb_dim"]))
            diag = diagnostics.get(key) or {}
            corr = diag.get("corr") or {}
            box = diag.get("box") or {}
            diag_rows.append({
                "channel": str(row["channel"]),
                "emb_dim": int(row["emb_dim"]),
                "corr_dim_d2": _safe_float(row["corr_dim_d2"]),
                "corr_fit_r2": _safe_float(row["corr_fit_r2"]),
                "boxcount_fd": _safe_float(row["boxcount_fd"]),
                "box_fit_r2": _safe_float(row["box_fit_r2"]),
                "corr_log_r": _json_safe(corr.get("logr_fit", [])),
                "corr_log_C": _json_safe(corr.get("logC_fit", [])),
                "corr_fit_line": _json_safe(corr.get("fit_line", [])),
                "box_log_inv_eps": _json_safe(box.get("logx_fit", [])),
                "box_log_counts": _json_safe(box.get("logy_fit", [])),
                "box_fit_line": _json_safe(box.get("fit_line", [])),
            })
    channel_summary = channel_summary_df.replace({np.nan: None}).to_dict(orient="records") if not channel_summary_df.empty else []
    mean_by_dimension = mean_df.replace({np.nan: None}).to_dict(orient="records") if not mean_df.empty else []
    summary = {
        "mode": mode,
        "embedding_dims": dims,
        "tau_samples": tau,
        "tau_ms_effective": _safe_float(1000.0 * tau / fs if fs > 0 else None),
        "n_channels": len(channels),
        "n_rows": len(rows),
        "ok_rows": len(ok_rows),
        "generated_embeddings": generated_count,
        "precomputed_embeddings": use_precomputed_count,
        "sampling_rate_analysis_hz": _safe_float(fs),
        "recording_dir": str(root),
        "top_channel": None,
        "top_complexity_score": None,
    }
    if channel_summary:
        top_channel = sorted(channel_summary, key=lambda r: (r.get("dimension_complexity_score") is None, -(r.get("dimension_complexity_score") or -1e99)))[0]
        summary["top_channel"] = top_channel.get("channel")
        summary["top_complexity_score"] = top_channel.get("dimension_complexity_score")
    return {
        "embedded_fractal_dimension": {
            "summary": summary,
            "rows": pd.DataFrame(rows).replace({np.nan: None}).to_dict(orient="records"),
            "channel_summary": channel_summary,
            "mean_by_dimension": mean_by_dimension,
            "diagnostics": diag_rows,
            "outputs": outputs,
        }
    }

def _method_specs() -> list[dict[str, Any]]:
    return [
        {
            "id": "higuchi_fractal_dimension",
            "name": "Higuchi fractal dimension",
            "description": "Computes per-channel Higuchi fractal dimension with log-log fit diagnostics, scalp/regional/asymmetry summaries, and rolling temporal-stability plots.",
            "panel": {"kind": "custom", "field": "higuchi_fractal_dimension", "title": "Higuchi Fractal Dimension"},
            "parameters": [
                {"name": "mode", "label": "Speed mode", "type": "select", "default": "fast", "options": ["normal", "fast", "ultrafast"]},
                {"name": "segment_start_sec", "label": "Segment start sec", "type": "number", "default": ""},
                {"name": "segment_end_sec", "label": "Segment end sec", "type": "number", "default": ""},
                {"name": "k_max", "label": "k max override", "type": "number", "default": ""},
                {"name": "decimate", "label": "Decimate override", "type": "number", "default": ""},
                {"name": "max_samples", "label": "Max samples override", "type": "number", "default": ""},
                {"name": "sampling_rate", "label": "Sampling rate override Hz", "type": "number", "default": ""},
                {"name": "rolling", "label": "Rolling stability", "type": "select", "default": "true", "options": ["true", "false"]},
                {"name": "rolling_window_sec", "label": "Rolling window sec", "type": "number", "default": ""},
                {"name": "rolling_step_sec", "label": "Rolling step sec", "type": "number", "default": ""},
                {"name": "rolling_max_windows", "label": "Rolling max windows", "type": "number", "default": ""},
            ],
        },
        {
            "id": "embedded_fractal_dimension",
            "name": "Embedded fractal dimension",
            "description": "Computes box-counting fractal dimension and sampled Grassberger-Procaccia correlation dimension D2 across 2D..10D attractor embeddings, using precomputed embedding files when present or generated delay embeddings from signal.npy.",
            "panel": {"kind": "custom", "field": "embedded_fractal_dimension", "title": "Embedded Fractal Dimension"},
            "parameters": [
                {"name": "mode", "label": "Speed mode", "type": "select", "default": "fast", "options": ["ultra", "fast", "balanced", "full"]},
                {"name": "embedding_dims", "label": "Embedding dimensions comma list", "type": "text", "default": "2,3,4,5,6,7,8,9,10"},
                {"name": "tau_ms", "label": "Delay tau ms", "type": "number", "default": 10.0},
                {"name": "tau_samples", "label": "Delay tau samples override", "type": "number", "default": ""},
                {"name": "max_channels", "label": "Max channels", "type": "number", "default": ""},
                {"name": "max_analysis_samples", "label": "Max analysis samples", "type": "number", "default": 120000},
                {"name": "sampling_rate", "label": "Sampling rate override Hz", "type": "number", "default": ""},
                {"name": "random_seed", "label": "Random seed", "type": "number", "default": 0}
            ],
        },
        {
            "id": "neuromouse_advanced_plots",
            "name": "NeuroMouse advanced plots",
            "description": "Builds the original NeuroMouse advanced analysis data objects and opens the guaranteed server-rendered plot page: Polar Alpha Chronomap, Kuramoto Animation, Channel Network, and TDA View.",
            "panel": {"kind": "summary", "field": "neuromouse_advanced_plots.summary", "title": "NeuroMouse Advanced Plots"},
            "parameters": [
                {"name": "dataset_id", "label": "Dataset ID", "type": "text", "default": ""},
                {"name": "sampling_rate", "label": "Sampling rate override Hz", "type": "number", "default": ""},
                {"name": "max_analysis_samples", "label": "Max analysis samples", "type": "number", "default": 240000},
                {"name": "max_windows", "label": "Max sliding windows", "type": "number", "default": 600},
            ],
        },
        {
            "id": "band_power_summary",
            "name": "Band power summary",
            "description": "Welch PSD band-power table for a chosen frequency band.",
            "panel": {"kind": "table", "field": "band_power_summary.rows", "title": "Band Power Summary"},
            "parameters": [
                {"name": "band", "label": "Preset band", "type": "select", "default": "alpha", "options": ["custom", "delta", "theta", "alpha", "beta", "gamma"]},
                {"name": "min_hz", "label": "Min Hz", "type": "number", "default": 8.0},
                {"name": "max_hz", "label": "Max Hz", "type": "number", "default": 13.0},
                {"name": "sampling_rate", "label": "Sampling rate override Hz", "type": "number", "default": ""},
                {"name": "max_analysis_samples", "label": "Max analysis samples", "type": "number", "default": 240000},
            ],
        },
        {
            "id": "spike_detect",
            "name": "Spike detection",
            "description": "Threshold-based spike/event detection from continuous channels.",
            "panel": {"kind": "heatmap_table", "field": "spike_detect.rows", "title": "Spike Detection"},
            "parameters": [
                {"name": "threshold_multiplier", "label": "Threshold multiplier", "type": "number", "default": 5.0},
                {"name": "bandpass_low_hz", "label": "Bandpass low Hz", "type": "number", "default": ""},
                {"name": "bandpass_high_hz", "label": "Bandpass high Hz", "type": "number", "default": ""},
                {"name": "refractory_ms", "label": "Refractory ms", "type": "number", "default": 2.0},
                {"name": "polarity", "label": "Polarity", "type": "select", "default": "negative", "options": ["negative", "positive", "both"]},
                {"name": "sampling_rate", "label": "Sampling rate override Hz", "type": "number", "default": ""},
                {"name": "max_analysis_samples", "label": "Max analysis samples", "type": "number", "default": 240000},
                {"name": "max_spikes_per_channel", "label": "Max stored spikes/channel", "type": "number", "default": 5000},
            ],
        },
        {
            "id": "network_burst",
            "name": "Network burst detection",
            "description": "Detects multi-channel burst epochs after threshold-based spike detection.",
            "panel": {"kind": "timeline", "field": "network_burst.timeline", "title": "Network Bursts"},
            "parameters": [
                {"name": "threshold_multiplier", "label": "Spike threshold multiplier", "type": "number", "default": 5.0},
                {"name": "refractory_ms", "label": "Spike refractory ms", "type": "number", "default": 2.0},
                {"name": "polarity", "label": "Spike polarity", "type": "select", "default": "negative", "options": ["negative", "positive", "both"]},
                {"name": "bin_size_ms", "label": "Burst bin size ms", "type": "number", "default": 10.0},
                {"name": "threshold_count", "label": "Minimum spikes per active bin", "type": "number", "default": 3},
                {"name": "merge_gap_ms", "label": "Merge gap ms", "type": "number", "default": 50.0},
                {"name": "min_spikes", "label": "Minimum spikes per burst", "type": "number", "default": 5},
                {"name": "sampling_rate", "label": "Sampling rate override Hz", "type": "number", "default": ""},
                {"name": "max_analysis_samples", "label": "Max analysis samples", "type": "number", "default": 240000},
            ],
        },
        {
            "id": "electrode_connectivity",
            "name": "Electrode connectivity",
            "description": "Pairwise lagged cross-correlation matrix over detected spike trains.",
            "panel": {"kind": "matrix", "field": "electrode_connectivity.matrix", "title": "Electrode Connectivity Matrix"},
            "parameters": [
                {"name": "threshold_multiplier", "label": "Spike threshold multiplier", "type": "number", "default": 5.0},
                {"name": "refractory_ms", "label": "Spike refractory ms", "type": "number", "default": 2.0},
                {"name": "polarity", "label": "Spike polarity", "type": "select", "default": "negative", "options": ["negative", "positive", "both"]},
                {"name": "bin_size_ms", "label": "Connectivity bin size ms", "type": "number", "default": 5.0},
                {"name": "max_lag_ms", "label": "Max lag ms", "type": "number", "default": 20.0},
                {"name": "sampling_rate", "label": "Sampling rate override Hz", "type": "number", "default": ""},
                {"name": "max_analysis_samples", "label": "Max analysis samples", "type": "number", "default": 240000},
                {"name": "max_links", "label": "Max stored links", "type": "number", "default": 200},
            ],
        },
    ]


METHODS: dict[str, Callable[[str | Path, dict[str, Any]], dict[str, Any]]] = {
    "higuchi_fractal_dimension": _higuchi_fractal_dimension,
    "embedded_fractal_dimension": _embedded_fractal_dimension,
    "neuromouse_advanced_plots": _neuromouse_advanced_plots,
    "band_power_summary": _band_power_summary,
    "spike_detect": _spike_detect,
    "network_burst": _network_burst,
    "electrode_connectivity": _electrode_connectivity,
}


def list_advanced_methods() -> list[dict[str, Any]]:
    return _method_specs()


def get_method_spec(method_id: str) -> dict[str, Any]:
    for spec in _method_specs():
        if spec["id"] == method_id:
            return spec
    raise KeyError(method_id)


def run_advanced_method(method_id: str, recording_dir: str | Path, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if method_id not in METHODS:
        raise ValueError(f"Unknown advanced method: {method_id}")
    spec = get_method_spec(method_id)
    defaults = {param["name"]: param.get("default") for param in spec.get("parameters", [])}
    merged = {**defaults, **(params or {})}
    # When a preset band is chosen, keep blank min/max from overriding it.
    if method_id == "band_power_summary" and merged.get("band") not in (None, "", "custom"):
        if params is None or "min_hz" not in params:
            merged["min_hz"] = ""
        if params is None or "max_hz" not in params:
            merged["max_hz"] = ""
    result = METHODS[method_id](recording_dir, merged)
    payload = {
        "ok": True,
        "method": spec,
        "method_id": method_id,
        "recording_dir": str(Path(recording_dir).expanduser()),
        "params": merged,
        "result": result,
    }
    return _json_safe(payload)
