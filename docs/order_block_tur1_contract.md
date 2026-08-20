# Order Block Tur-1 source contract

Source: `ORDER BLOCK - Fill Debug v0.4.23 TIME FIX EXPORT`.

Tur-1 ports only:

- consecutive opposite-color A/B pair detection
- A wick preservation vs B source replacement
- source-local imbalance search on the 3rd, 4th, or 5th candle
- full source-candle high/low OB zone
- pre-confirm fill accumulation from `source + 2`
- full gap-through => 100% used
- fill cancellation threshold
- candidate expiry when its own imbalance window ends
- same source + same direction deduplication
- closed/complete-bar state advancement only in Python

Explicitly out of scope because the supplied Pine says they are absent:

- BOS / CHoCH
- trend filter
- pivot / swing confirmation
- liquidity
- buy/sell signals
- MTF
- time-based OB deletion

Visual distance filtering is intentionally not part of engine state because the Pine states that it only controls drawing visibility, not record or fill lifecycle.
