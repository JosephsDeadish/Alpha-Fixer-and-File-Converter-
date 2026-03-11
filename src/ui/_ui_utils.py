"""
Shared UI helper utilities used by multiple tool widgets.

Kept small and import-free (only stdlib) so it can be imported early
without pulling in heavy Qt or Pillow dependencies.
"""


def format_eta(current: int, total: int, elapsed: float, threshold: int = 500) -> str:
    """Return an ETA string for a batch progress update, or '' if not shown.

    The ETA is only shown when the batch is large enough (*total* ≥ *threshold*)
    and enough time has passed to produce a meaningful rate estimate (elapsed > 1 s).

    Parameters
    ----------
    current:   number of items completed so far (0-based index).
    total:     total number of items in the batch.
    elapsed:   wall-clock seconds since the batch started.
    threshold: minimum batch size before an ETA is shown (default: 500).

    Returns
    -------
    str: "  ETA ~Xm YYs" or "  ETA ~Xs" when applicable, otherwise "".
    """
    if total < threshold or current <= 0 or elapsed <= 1.0:
        return ""
    rate = current / elapsed          # items per second
    if rate <= 0:
        return ""
    eta_secs = int(max(0, total - current) / rate)
    if eta_secs >= 60:
        return f"  ETA ~{eta_secs // 60}m {eta_secs % 60:02d}s"
    return f"  ETA ~{eta_secs}s"
