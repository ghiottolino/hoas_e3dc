"""Backfill Home Assistant long-term statistics from the E3DC's own archive.

The E3DC system keeps its own day/month/year history independent of whether
Home Assistant was successfully polling it (see E3DC.get_db_data). This module
uses that archive to fill in long-term statistics for any past days our
sensors have no data for, so history graphs and cards aren't left with gaps
after an outage such as https://github.com/ghiottolino/hoas_e3dc.

Only day-level resolution is reconstructed: one statistics row per missing
day, holding that day's mean/min/max as reported by the E3DC archive. This is
enough for daily/weekly/monthly rollups to be correct, but an hour-zoomed
history graph will show a single populated hour per backfilled day rather
than a smooth 24-hour curve.
"""
from __future__ import annotations

import datetime
import logging

import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
)
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
import homeassistant.util.dt as dt_util

_LOGGER = logging.getLogger(__name__)

SERVICE_BACKFILL_DAYS = "backfill_days"

BACKFILL_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional("start_date"): cv.date,
        vol.Optional("end_date"): cv.date,
    }
)

# Maps a get_db_data() field to the statistic_id of the corresponding entity
# in sensor.py. These entities have no unique_id, so their entity_id is
# whatever HA slugifies their fixed _attr_name to - that's the only stable
# handle we have, and it's what's used in this file and in the README.
#
# NOTE: python-e3dc doesn't document field semantics precisely; "consumption"
# in particular is assumed to be house consumption. Check the first backfilled
# day's INFO log against the E3DC portal's own daily chart before trusting a
# long backfill.
SENSOR_MAP = [
    ("solarProduction", "sensor.e3dc_solar_production", "E3DC Solar Production", UnitOfPower.WATT),
    ("grid_power_in", "sensor.e3dc_grid_production", "E3DC Grid Production", UnitOfPower.WATT),
    ("grid_power_out", "sensor.e3dc_grid_consumption", "E3DC Grid Consumption", UnitOfPower.WATT),
    ("consumption", "sensor.e3dc_house_consumption", "E3DC House Consumption", UnitOfPower.WATT),
    ("bat_power_in", "sensor.e3dc_battery_incoming", "E3DC Battery Incoming", UnitOfPower.WATT),
    ("bat_power_out", "sensor.e3dc_battery_outgoing", "E3DC Battery Outgoing", UnitOfPower.WATT),
    ("stateOfCharge", "sensor.e3dc_battery_charge", "E3DC Battery Charge", PERCENTAGE),
    ("autarky", "sensor.e3dc_autarky", "E3DC Autarky", PERCENTAGE),
    ("consumed_production", "sensor.e3dc_domestic_consumption", "E3DC Domestic Consumption", PERCENTAGE),
]


def _build_metadata(statistic_id: str, name: str, unit: str) -> dict:
    metadata = {
        "has_mean": True,
        "has_sum": False,
        "name": name,
        "source": "recorder",
        "statistic_id": statistic_id,
        "unit_of_measurement": unit,
    }
    try:
        # HA >= 2024.9 additionally wants mean_type; older versions ignore
        # unknown metadata keys, so it's safe to always set this.
        from homeassistant.components.recorder.models import StatisticMeanType

        metadata["mean_type"] = StatisticMeanType.ARITHMETIC
    except ImportError:
        pass
    return metadata


def _day_start_utc(day: datetime.date) -> datetime.datetime:
    """Local midnight of `day`, floored to the hour, as a UTC datetime.

    Long-term statistics rows must start on the hour in UTC. Local midnight
    only lands off-hour in a handful of fractional-UTC-offset timezones,
    which we tolerate by flooring rather than rejecting the day.
    """
    local_midnight = dt_util.start_of_local_day(day)
    return dt_util.as_utc(local_midnight).replace(minute=0, second=0, microsecond=0)


async def _last_backfilled_day(hass: HomeAssistant, statistic_id: str) -> datetime.date | None:
    def _query() -> datetime.date | None:
        result = get_last_statistics(hass, 1, statistic_id, True, {"mean"})
        rows = result.get(statistic_id)
        if not rows:
            return None
        start = rows[0]["start"]
        if isinstance(start, (int, float)):
            start = dt_util.utc_from_timestamp(start)
        return dt_util.as_local(start).date()

    return await get_instance(hass).async_add_executor_job(_query)


async def _fetch_day(hass: HomeAssistant, e3dc_api, day: datetime.date) -> dict | None:
    def _poll():
        try:
            return e3dc_api.get_db_data(startDate=day, timespan="DAY")
        except Exception:  # noqa: BLE001 - one bad day shouldn't abort the whole run
            _LOGGER.warning("E3DC backfill: failed to fetch archive data for %s", day, exc_info=True)
            return None

    return await hass.async_add_executor_job(_poll)


async def async_run_backfill(
    hass: HomeAssistant,
    e3dc_api,
    max_days_back: int = 45,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
) -> None:
    """Backfill missing past days of E3DC data as long-term statistics.

    With no explicit start_date/end_date, this fills the gap between the
    last known statistic and yesterday, bounded by max_days_back - this is
    what runs automatically on every HA startup and is a no-op once caught
    up. Pass explicit dates (e.g. via the backfill_days service) to force a
    specific range, overwriting any existing statistics in it.
    """
    today = dt_util.now().date()
    yesterday = today - datetime.timedelta(days=1)
    oldest_allowed = today - datetime.timedelta(days=max_days_back)

    if start_date is None:
        reference_statistic_id = SENSOR_MAP[0][1]
        last_day = await _last_backfilled_day(hass, reference_statistic_id)
        start_date = oldest_allowed if last_day is None else last_day + datetime.timedelta(days=1)
        start_date = max(start_date, oldest_allowed)
    if end_date is None:
        end_date = yesterday

    if start_date > end_date:
        _LOGGER.debug("E3DC backfill: no gap to fill")
        return

    _LOGGER.info("E3DC backfill: fetching %s to %s from the E3DC archive", start_date, end_date)

    per_sensor_stats: dict[str, list[dict]] = {statistic_id: [] for _, statistic_id, _, _ in SENSOR_MAP}
    day = start_date
    first_day = True
    while day <= end_date:
        data = await _fetch_day(hass, e3dc_api, day)
        if data is not None:
            if first_day:
                _LOGGER.info("E3DC backfill: sample archive data for %s: %s", day, data)
                first_day = False
            day_start = _day_start_utc(day)
            for field, statistic_id, _, _ in SENSOR_MAP:
                value = data.get(field)
                if value is not None:
                    value = float(value)
                    per_sensor_stats[statistic_id].append(
                        {"start": day_start, "mean": value, "min": value, "max": value}
                    )
        day += datetime.timedelta(days=1)

    for field, statistic_id, name, unit in SENSOR_MAP:
        stats = per_sensor_stats[statistic_id]
        if stats:
            async_import_statistics(hass, _build_metadata(statistic_id, name, unit), stats)

    _LOGGER.info("E3DC backfill: done")


def async_register_backfill_service(hass: HomeAssistant, domain: str, e3dc_api, max_days_back: int) -> None:
    """Register the backfill_days service, once per HA run."""
    if hass.services.has_service(domain, SERVICE_BACKFILL_DAYS):
        return

    async def _handle_service(call: ServiceCall) -> None:
        await async_run_backfill(
            hass,
            e3dc_api,
            max_days_back=max_days_back,
            start_date=call.data.get("start_date"),
            end_date=call.data.get("end_date"),
        )

    hass.services.async_register(
        domain, SERVICE_BACKFILL_DAYS, _handle_service, schema=BACKFILL_SERVICE_SCHEMA
    )
