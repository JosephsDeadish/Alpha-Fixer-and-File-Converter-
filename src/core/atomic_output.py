"""Atomic sibling staging shared by core exporters and UI save actions."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile


@contextmanager
def staged_output_path(destination, *, preserve_existing_mode=False):
    """Replace a destination only after successfully writing a sibling file."""
    path = Path(destination)
    handle = tempfile.NamedTemporaryFile(
        prefix=".alpha_fixer_save_", suffix=path.suffix,
        dir=str(path.absolute().parent), delete=False,
    )
    staged = Path(handle.name)
    try:
        handle.close()
        yield str(staged)
        if preserve_existing_mode and os.name == "posix":
            try:
                mode = stat.S_IMODE(path.stat().st_mode)
            except FileNotFoundError:
                pass
            else:
                staged.chmod(mode)
        os.replace(staged, path)
    except BaseException:
        # Cleanup may also fail on a disconnected/full filesystem. Preserve the
        # save error (or cancellation), not the secondary cleanup error.
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass
        raise
