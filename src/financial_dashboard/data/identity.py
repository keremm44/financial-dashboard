from __future__ import annotations


def normalize_symbol(symbol: str) -> str:
    """Return the canonical engine/cache symbol identity.

    Provider-specific aliases such as ``ASELS.IS`` or ``BIST:ASELS`` belong at the
    provider boundary and must not leak into engine identity unless they are the
    actual canonical cache symbol chosen by the caller.
    """

    normalized = str(symbol).strip().upper()
    if not normalized:
        raise ValueError("symbol must not be empty")
    return normalized


__all__ = ["normalize_symbol"]
