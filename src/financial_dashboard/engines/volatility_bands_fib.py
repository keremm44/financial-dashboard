from __future__ import annotations

from typing import Any

from .volatility_bands_fib_engine import BandState, VolatilityState, _clamp, _safe_div
from .volatility_bands_fib_final import VolatilityBandsFibEngine as _FinalVolatilityBandsFibEngine


class VolatilityBandsFibEngine(_FinalVolatilityBandsFibEngine):
    """Canonical final engine with exact Pine band evidence quality for all states."""

    def _band_quality_exact(self, band_state: BandState, vol_state: VolatilityState) -> float:
        if band_state not in {
            BandState.UPPER_WEAKENING,
            BandState.LOWER_WEAKENING,
            BandState.UPPER_MEAN_REVERSION,
            BandState.LOWER_MEAN_REVERSION,
        }:
            return super()._band_quality_exact(band_state, vol_state)

        n = len(self._rows)
        i = n - 1
        obs = int(self._p["band_obs"])
        if i < 42 or obs <= 0:
            return super()._band_quality_exact(band_state, vol_state)

        closes = [float(r["close"]) for r in self._rows]
        highs = [float(r["high"]) for r in self._rows]
        lows = [float(r["low"]) for r in self._rows]
        atrs = self._atr_series()

        def basic(k: int) -> tuple[float, float, float, float, float] | None:
            if k < 19:
                return None
            window = closes[k - 19 : k + 1]
            basis = sum(window) / 20.0
            variance = sum((x - basis) ** 2 for x in window) / 20.0
            stdev = variance ** 0.5
            upper = basis + 2.0 * stdev
            lower = basis - 2.0 * stdev
            width = upper - lower
            normalized = _safe_div(width, max(abs(basis), self.config.minimum_tick), 0.0)
            return basis, upper, lower, width, normalized

        def values(k: int) -> dict[str, Any] | None:
            current = basic(k)
            if current is None:
                return None
            basis, upper, lower, width, normalized = current
            normalized_window: list[float] = []
            for j in range(k - 19, k + 1):
                item = basic(j)
                if item is None:
                    return None
                normalized_window.append(item[4])
            average_width = sum(normalized_window) / 20.0
            old = basic(k - 3) if k >= 3 else None
            width_slope = _safe_div(
                normalized - old[4] if old is not None else None,
                average_width,
                0.0,
            )
            position = _safe_div(closes[k] - lower, width, 0.5)
            atr = atrs[k] if k < len(atrs) else None
            net = closes[k] - closes[k - 3] if k >= 3 else 0.0
            net_atr = _safe_div(net, atr, 0.0)
            path = sum(abs(closes[j] - closes[j - 1]) for j in range(k - 2, k + 1)) if k >= 3 else 0.0
            efficiency = _safe_div(abs(net), path, 0.0)
            distance_basis_atr = _safe_div(abs(closes[k] - basis), atr, 0.0)
            return {
                "basis": basis,
                "upper": upper,
                "lower": lower,
                "position": position,
                "width_slope": width_slope,
                "net_atr": net_atr,
                "efficiency": efficiency,
                "distance_basis_atr": distance_basis_atr,
            }

        def shares(k: int) -> dict[str, float] | None:
            if k - obs + 1 < 0:
                return None
            rows: list[dict[str, Any]] = []
            for j in range(k - obs + 1, k + 1):
                v = values(j)
                if v is None:
                    return None
                rows.append(
                    {
                        "uz": v["position"] >= self._p["upper_accept"],
                        "lz": v["position"] <= self._p["lower_accept"],
                        "higher": j > 0 and closes[j] > closes[j - 1],
                        "lower": j > 0 and closes[j] < closes[j - 1],
                    }
                )
            return {
                key: sum(1 for row in rows if row[key]) / float(obs)
                for key in ("uz", "lz", "higher", "lower")
            }

        current = values(i)
        previous = values(i - 1)
        two_back = values(i - 2)
        current_shares = shares(i)
        previous_shares = shares(i - 1)
        if None in (current, previous, two_back, current_shares, previous_shares):
            return super()._band_quality_exact(band_state, vol_state)

        assert current is not None and previous is not None and two_back is not None
        assert current_shares is not None and previous_shares is not None

        position_change = current["position"] - previous["position"]
        upper_zone_falling = current_shares["uz"] < previous_shares["uz"]
        lower_zone_falling = current_shares["lz"] < previous_shares["lz"]
        upper_retreat = current["position"] < two_back["position"] and position_change < 0.0
        lower_retreat = current["position"] > two_back["position"] and position_change > 0.0
        upper_net_fade = (
            current["net_atr"] < previous["net_atr"]
            and current["net_atr"] < self._p["trend_progress"]
        )
        lower_net_fade = (
            current["net_atr"] > previous["net_atr"]
            and current["net_atr"] > -self._p["trend_progress"]
        )
        efficiency_falling = current["efficiency"] < previous["efficiency"]
        upper_basis_approach = (
            current["distance_basis_atr"] < previous["distance_basis_atr"]
            or current["position"] < self._p["basis_upper"]
        )
        lower_basis_approach = (
            current["distance_basis_atr"] < previous["distance_basis_atr"]
            or current["position"] > self._p["basis_lower"]
        )

        upper_weakening_count = sum(
            map(
                int,
                (
                    upper_zone_falling,
                    upper_retreat,
                    upper_net_fade,
                    efficiency_falling,
                    current["width_slope"] <= 0.0,
                    upper_basis_approach,
                    current_shares["lower"] > previous_shares["lower"],
                ),
            )
        )
        lower_weakening_count = sum(
            map(
                int,
                (
                    lower_zone_falling,
                    lower_retreat,
                    lower_net_fade,
                    efficiency_falling,
                    current["width_slope"] <= 0.0,
                    lower_basis_approach,
                    current_shares["higher"] > previous_shares["higher"],
                ),
            )
        )

        upper_mr_position = current["position"] < self._p["basis_upper"] and upper_retreat
        lower_mr_position = current["position"] > self._p["basis_lower"] and lower_retreat
        upper_mr_progress = current["net_atr"] <= 0.0 or (
            upper_net_fade and current["efficiency"] < self._p["trend_eff"]
        )
        lower_mr_progress = current["net_atr"] >= 0.0 or (
            lower_net_fade and current["efficiency"] < self._p["trend_eff"]
        )
        upper_mr_basis = closes[i] <= current["basis"] or current["distance_basis_atr"] <= 0.35
        lower_mr_basis = closes[i] >= current["basis"] or current["distance_basis_atr"] <= 0.35
        upper_mean_reversion_count = sum(
            map(
                int,
                (
                    upper_mr_position,
                    upper_mr_progress,
                    upper_mr_basis,
                    current_shares["lower"] >= self._p["higher_lower_share"],
                    current["width_slope"] <= 0.0,
                ),
            )
        )
        lower_mean_reversion_count = sum(
            map(
                int,
                (
                    lower_mr_position,
                    lower_mr_progress,
                    lower_mr_basis,
                    current_shares["higher"] >= self._p["higher_lower_share"],
                    current["width_slope"] <= 0.0,
                ),
            )
        )

        if band_state == BandState.UPPER_WEAKENING:
            return _clamp(upper_weakening_count / 7.0 * 80.0 + 20.0)
        if band_state == BandState.LOWER_WEAKENING:
            return _clamp(lower_weakening_count / 7.0 * 80.0 + 20.0)
        if band_state == BandState.UPPER_MEAN_REVERSION:
            return _clamp(upper_mean_reversion_count / 5.0 * 80.0 + 20.0)
        return _clamp(lower_mean_reversion_count / 5.0 * 80.0 + 20.0)
