"""
Data freshness checks (Phase 4 §10).

Distinct from LiveProvider's own connection-level health check (which
reports NETWORK_FAILURE/AUTH_FAILURE/etc. for the provider itself): this
checks whether the historical match dataset a prediction is about to be
built from is stale RELATIVE TO the moment the prediction claims to
represent. This matters most for LIVE predictions — "as of now" is a
meaningless claim if the underlying dataset's most recent real match is
six months old. It's not meaningful for REPLAY (a replay's dataset is
deliberately a fixed historical snapshot) or MANUAL (freshness is
whatever the operator just entered) — see check_freshness()'s data_mode
parameter.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.prediction_service.data_contract import DataMode

DEFAULT_MAX_STALENESS_DAYS = 3


@dataclass
class FreshnessResult:
    is_stale: bool
    dataset_latest_date: str
    reference_timestamp: str
    age_days: float
    max_staleness_days: float
    checked: bool   # False when the check doesn't apply (REPLAY/MANUAL)


def check_freshness(
    historical_matches: pd.DataFrame,
    reference_timestamp,
    data_mode: DataMode,
    max_staleness_days: float = DEFAULT_MAX_STALENESS_DAYS,
) -> FreshnessResult:
    ref_ts = pd.Timestamp(reference_timestamp)
    ref_naive = ref_ts.tz_localize(None) if ref_ts.tzinfo is not None else ref_ts

    latest = pd.to_datetime(historical_matches["date"]).max()
    age_days = (ref_naive - latest).total_seconds() / 86400.0

    if data_mode != DataMode.LIVE:
        # Only a LIVE prediction claims "this reflects the real world right
        # now" — REPLAY/MANUAL datasets are deliberately fixed snapshots,
        # so "staleness" isn't the right question for them.
        return FreshnessResult(
            is_stale=False, dataset_latest_date=str(latest.date()), reference_timestamp=ref_ts.isoformat(),
            age_days=round(age_days, 2), max_staleness_days=max_staleness_days, checked=False,
        )

    return FreshnessResult(
        is_stale=age_days > max_staleness_days,
        dataset_latest_date=str(latest.date()), reference_timestamp=ref_ts.isoformat(),
        age_days=round(age_days, 2), max_staleness_days=max_staleness_days, checked=True,
    )
