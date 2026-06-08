from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import welch


def _json_safe(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _load_signal_path(recording_dir: Path) -> Path:
    candidates = [
        recording_dir / "signal.npy",
        recording_dir / "processed" / "signal.npy",
        recording_dir / "raw" / "signal.npy",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(f"No signal.npy found in {recording_dir}")


def _load_channels(recording_dir: Path, n_channels: int) -> tuple[list[str], list[str], dict[str, list[str]]]:
    candidates = [recording_dir / "channels.csv", recording_dir / "processed" / "channels.csv", recording_dir / "raw" / "channels.csv"]
    for p in candidates:
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p)
            name_col = next((c for c in ("name", "channel_name", "label", "channel") if c in df.columns), None)
            if name_col:
                names = [str(x) for x in df[name_col].tolist()]
                if len(names) == n_channels:
                    type_col = next((c for c in ("type", "channel_type", "kind", "modality") if c in df.columns), None)
                    types = [str(x) for x in df[type_col].tolist()] if type_col else ["NEURAL"] * n_channels
                    groups: dict[str, list[str]] = {}
                    for group_col in ("group", "region", "organoid", "well", "row", "hemisphere"):
                        if group_col in df.columns:
                            for val, name in zip(df[group_col].fillna("").astype(str), names):
                                if val and val.lower() != "nan":
                                    groups.setdefault(f"{group_col}:{val}", []).append(name)
                    return names, types, groups
        except Exception:
            pass
    names = [f"ch_{i:03d}" for i in range(n_channels)]
    return names, ["NEURAL"] * n_channels, {}


def _load_metadata(recording_dir: Path) -> dict[str, Any]:
    for p in (recording_dir / "metadata.json", recording_dir / "processed" / "metadata.json", recording_dir / "raw" / "metadata.json"):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
    return {}


def _sampling_rate(metadata: dict[str, Any], override: float | None = None) -> float | None:
    if override:
        return float(override)
    for key in ("sampling_rate", "sampling_rate_hz", "sample_rate_hz", "fs", "sfreq"):
        val = metadata.get(key)
        if val is not None:
            try:
                return float(val)
            except Exception:
                pass
    return None


def _safe_float(x: Any) -> float | None:
    try:
        f = float(x)
        if np.isfinite(f):
            return f
    except Exception:
        pass
    return None


def _downsample_time_axis(x: np.ndarray, max_samples: int) -> tuple[np.ndarray, int]:
    if x.shape[0] <= max_samples:
        return x, 1
    step = int(np.ceil(x.shape[0] / max_samples))
    return x[::step], step


def _sliding_geometry(
    signal: np.ndarray,
    fs: float | None,
    *,
    window_sec: float = 2.0,
    step_sec: float = 0.5,
    max_windows: int = 600,
) -> dict[str, Any]:
    n_samples, n_channels = signal.shape
    if not fs or fs <= 0:
        # fallback to normalized windows over sample index
        fs = 1.0
    win = max(8, int(window_sec * fs))
    step = max(1, int(step_sec * fs))
    if n_samples < win:
        win = max(8, n_samples)
        step = max(1, win)
    starts = list(range(0, max(1, n_samples - win + 1), step))
    if len(starts) > max_windows:
        stride = int(np.ceil(len(starts) / max_windows))
        starts = starts[::stride]

    times = []
    centroid = np.zeros((n_channels, len(starts)), dtype=float)
    spread = np.zeros_like(centroid)
    entropy = np.zeros_like(centroid)
    flatness = np.zeros_like(centroid)
    edge95 = np.zeros_like(centroid)
    alpha_relative_power = np.zeros_like(centroid)

    for j, start in enumerate(starts):
        seg = np.asarray(signal[start:start + win], dtype=float)
        seg = np.nan_to_num(seg - np.nanmean(seg, axis=0, keepdims=True))
        nperseg = min(seg.shape[0], max(8, int(min(fs, seg.shape[0]))))
        freqs, psd = welch(seg, fs=fs, nperseg=nperseg, axis=0)
        # psd shape: freqs x channels
        positive = freqs > 0
        p = psd[positive]
        f = freqs[positive]
        denom = np.sum(p, axis=0) + 1e-20
        c = np.sum(f[:, None] * p, axis=0) / denom
        centroid[:, j] = c
        spread[:, j] = np.sqrt(np.sum(((f[:, None] - c[None, :]) ** 2) * p, axis=0) / denom)
        prob = p / denom[None, :]
        entropy[:, j] = -np.sum(prob * np.log2(prob + 1e-20), axis=0)
        flatness[:, j] = np.exp(np.mean(np.log(p + 1e-20), axis=0)) / (np.mean(p + 1e-20, axis=0) + 1e-20)
        cumulative = np.cumsum(p, axis=0) / denom[None, :]
        for ch in range(n_channels):
            idx = int(np.searchsorted(cumulative[:, ch], 0.95))
            edge95[ch, j] = float(f[min(idx, len(f)-1)])
        alpha_mask = (f >= 8) & (f < 13)
        total_mask = (f >= 1) & (f < min(60, fs/2))
        alpha_power = np.sum(p[alpha_mask], axis=0) if np.any(alpha_mask) else np.zeros(n_channels)
        total_power = np.sum(p[total_mask], axis=0) if np.any(total_mask) else denom
        alpha_relative_power[:, j] = alpha_power / (total_power + 1e-20)
        times.append(float(start / fs))

    return {
        "time": times,
        "centroid": centroid,
        "spread": spread,
        "entropy": entropy,
        "flatness": flatness,
        "edge95": edge95,
        "alpha_relative_power": alpha_relative_power,
    }


def build_speedmouse_dataset(
    recording_dir: str | Path,
    *,
    dataset_id: str | None = None,
    sampling_rate: float | None = None,
    max_analysis_samples: int = 240_000,
    max_windows: int = 600,
) -> dict[str, Any]:
    """Build a SpeedMouse-compatible static data.json object.

    The output is variable-electrode safe. Matrix convention is channel-major:
    - welch_psd.psd: channels x frequencies
    - geometry metrics: channels x time_windows
    """
    recording_dir = Path(recording_dir).expanduser().resolve()
    signal_path = _load_signal_path(recording_dir)
    signal = np.load(signal_path, mmap_mode="r")
    if signal.ndim != 2:
        raise ValueError(f"Expected signal.npy samples x channels, got {signal.shape}")
    n_samples, n_channels = signal.shape
    metadata = _load_metadata(signal_path.parent) or _load_metadata(recording_dir)
    fs = _sampling_rate(metadata, sampling_rate) or 1.0
    channel_names, channel_types, groups = _load_channels(signal_path.parent, n_channels)
    if channel_names == [f"ch_{i:03d}" for i in range(n_channels)]:
        channel_names, channel_types, groups = _load_channels(recording_dir, n_channels)

    analysis_signal, ds_step = _downsample_time_axis(np.asarray(signal), max_analysis_samples)
    fs_analysis = fs / ds_step if fs and fs > 0 else fs
    analysis_signal = np.asarray(analysis_signal, dtype=np.float32)
    analysis_signal = np.nan_to_num(analysis_signal)

    nperseg = min(4096, max(64, analysis_signal.shape[0] // 8))
    nperseg = min(nperseg, analysis_signal.shape[0])
    freqs, psd = welch(analysis_signal, fs=fs_analysis, nperseg=nperseg, axis=0)
    psd_ch_major = psd.T

    geometry = _sliding_geometry(analysis_signal, fs_analysis, max_windows=max_windows)
    channel_summary = []
    for i, name in enumerate(channel_names):
        ch = np.asarray(analysis_signal[:, i], dtype=float)
        psd_i = psd_ch_major[i]
        alpha_mask = (freqs >= 8) & (freqs < 13)
        peak_idx = int(np.nanargmax(psd_i)) if psd_i.size else 0
        alpha_rel = _safe_float(np.nanmean(geometry["alpha_relative_power"][i]))
        centroid_hz = _safe_float(np.nanmean(geometry["centroid"][i]))
        spread_hz = _safe_float(np.nanmean(geometry["spread"][i]))
        entropy_val = _safe_float(np.nanmean(geometry["entropy"][i]))
        flatness_val = _safe_float(np.nanmean(geometry["flatness"][i]))
        edge95_hz = _safe_float(np.nanmean(geometry["edge95"][i]))
        # SpeedMouse's original workbench expects these summary keys. For
        # non-EEG layouts we keep region/hemisphere generic but valid.
        channel_summary.append({
            "channel_index": i,
            "channel": name,
            "name": name,
            "type": channel_types[i] if i < len(channel_types) else "NEURAL",
            "region": "generic",
            "hemisphere": "",
            "mean": _safe_float(np.nanmean(ch)),
            "std": _safe_float(np.nanstd(ch)),
            "rms": _safe_float(np.sqrt(np.nanmean(ch * ch))),
            "variance": _safe_float(np.nanvar(ch)),
            "peak_frequency_hz": _safe_float(freqs[peak_idx]) if psd_i.size else None,
            "alpha_power": _safe_float(np.nanmean(psd_i[alpha_mask])) if np.any(alpha_mask) else None,
            "alpha_relative_power": alpha_rel,
            "spectral_centroid_hz": centroid_hz,
            "spectral_spread_hz": spread_hz,
            "spectral_entropy": entropy_val,
            "spectral_flatness": flatness_val,
            "edge95_hz": edge95_hz,
            "sliding_alpha_relative_mean": alpha_rel,
            "mean_centroid_hz": centroid_hz,
            "mean_alpha_relative_power": alpha_rel,
            "has_clear_alpha_peak": bool(alpha_rel is not None and alpha_rel > 0.05),
            "variability": {
                "alpha_range": _safe_float(np.nanmax(geometry["alpha_relative_power"][i]) - np.nanmin(geometry["alpha_relative_power"][i])),
            },
        })

    manifest = {
        "n_channels": n_channels,
        "channel_names": channel_names,
        "channel_types": channel_types,
        "channel_groups": groups,
        "namespace": metadata.get("channel_namespace") or metadata.get("layout") or "variable_neural",
        "modality": metadata.get("modality") or metadata.get("signal_type") or "continuous_neural",
    }
    data = {
        "meta": {
            "schema": "speedmouse.data.v1.variable",
            "dataset_id": dataset_id or recording_dir.name,
            "source_recording_dir": str(recording_dir),
            "source_signal_path": str(signal_path),
            "channels": channel_names,
            "n_channels": n_channels,
            "channel_types": channel_types,
            "channel_manifest": manifest,
            "sampling_rate_hz": fs,
            "sampling_rate_analysis_hz": fs_analysis,
            "duration_sec": float(n_samples / fs) if fs else None,
            "n_samples": int(n_samples),
            "analysis_samples": int(analysis_signal.shape[0]),
            "analysis_downsample_step": int(ds_step),
            "continuous_signal_only": True,
            "generated_by": "Neuro Signal Importer SpeedMouse adapter",
        },
        "welch_psd": {
            "frequencies": freqs.tolist(),
            "psd": psd_ch_major.tolist(),
        },
        "centroid": {
            "time_relative": geometry["time"],
            "values": geometry["centroid"].tolist(),
        },
        "geometry": {
            "time": geometry["time"],
            "centroid": geometry["centroid"].tolist(),
            "spread": geometry["spread"].tolist(),
            "entropy": geometry["entropy"].tolist(),
            "flatness": geometry["flatness"].tolist(),
            "edge95": geometry["edge95"].tolist(),
            "alpha_relative_power": geometry["alpha_relative_power"].tolist(),
        },
        "channel_summary": channel_summary,
    }
    return _json_safe(data)


def write_speedmouse_dataset(
    recording_dir: str | Path,
    output_dir: str | Path,
    *,
    dataset_id: str | None = None,
    sampling_rate: float | None = None,
    max_analysis_samples: int = 240_000,
    max_windows: int = 600,
) -> dict[str, str]:
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    data = build_speedmouse_dataset(
        recording_dir,
        dataset_id=dataset_id,
        sampling_rate=sampling_rate,
        max_analysis_samples=max_analysis_samples,
        max_windows=max_windows,
    )
    data_path = output_dir / "data.json"
    manifest_path = output_dir / "speedmouse_manifest.json"
    data_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    manifest = {
        "data_json": str(data_path),
        "dataset_id": data["meta"].get("dataset_id"),
        "n_channels": data["meta"].get("n_channels"),
        "sampling_rate_hz": data["meta"].get("sampling_rate_hz"),
        "source_recording_dir": data["meta"].get("source_recording_dir"),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"data_json": str(data_path), "manifest": str(manifest_path)}
