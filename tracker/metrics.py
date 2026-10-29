"""A small set of peak measurements for the checked regions only."""
import numpy as np


def summarize_checked(regions: list[dict]) -> dict:
    checked = [r for r in regions if r["included"]]
    counts = [r["peak_count"] for r in checked]
    intervals = [r["median_interval_seconds"] for r in checked
                 if r["median_interval_seconds"] is not None]
    return dict(
        region_count=len(checked),
        mean_peak_count=float(np.mean(counts)) if counts else None,
        no_peaks_count=sum(count == 0 for count in counts),
        single_peak_count=sum(count == 1 for count in counts),
        multiple_peaks_count=sum(count > 1 for count in counts),
        median_interval_seconds=float(np.median(intervals)) if intervals else None,
        interval_region_count=len(intervals),
    )
