"""
Shared source-grounding loader for the Sol AI content pipeline.

WHY THIS EXISTS
---------------
`run-daily.py` carries a tested source-excerpt fetcher: it downloads the
story page, strips the chrome, and returns readable text so the MiniMax
prompts are grounded in what the article actually says instead of in the
model's imagination.

But the file is named `run-daily.py` — with a hyphen — so it cannot be
reached with a normal `import` statement. `weekly-roundup.py` tried

    from run_daily import fetch_source_excerpt

inside a try/except, which raised ModuleNotFoundError and was swallowed.
The consequence was silent: the roundup's "Sol's Take" was generated from
headlines alone, with no source grounding at all, and the log reported
nothing wrong. OPERATIONS.md described the section as "grounded in the
source pages via fetch_source_excerpt" — that was not happening.

This module loads the hyphenated file properly via importlib and is the
single place that knows how to reach it, so the two generators that ground
their commentary cannot drift apart.
"""

import importlib.util
from pathlib import Path

BUNDLE_DIR = Path(__file__).resolve().parent
RUN_DAILY = BUNDLE_DIR / "run-daily.py"

_MODULE = None
_LOAD_ATTEMPTED = False


def _get_module():
    """Import run-daily.py once, tolerating any failure."""
    global _MODULE, _LOAD_ATTEMPTED
    if _LOAD_ATTEMPTED:
        return _MODULE
    _LOAD_ATTEMPTED = True
    try:
        spec = importlib.util.spec_from_file_location("sol_run_daily", RUN_DAILY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULE = module
    except Exception:
        _MODULE = None
    return _MODULE


def grounding_available():
    """True when the real source fetcher could be loaded."""
    return _get_module() is not None


def fetch_source_excerpt(url, max_chars=6000):
    """
    Fetch a source page and return readable text for factual grounding.

    Returns "" on any failure — grounding is an enhancement, never a hard
    dependency. A generator with no grounding still publishes; it just
    writes from the headline alone.
    """
    module = _get_module()
    if module is None:
        return ""
    fetcher = getattr(module, "fetch_source_excerpt", None)
    if fetcher is None:
        return ""
    try:
        return fetcher(url, max_chars=max_chars)
    except Exception:
        return ""
