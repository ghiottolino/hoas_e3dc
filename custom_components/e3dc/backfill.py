"""Backfill Home Assistant long-term statistics from the E3DC's own archive.

The E3DC system keeps its own day/month/year history independent of whether
Home Assistant was successfully polling it (see E3DC.get_db_data). This module
uses that archive to fill in long-term statistics for any past days our
sensors have no data for, so history graphs, the Energy dashboard and cards
aren't left with gaps after an outage such as
https://github.com/ghiottolino/hoas_e3dc.

get_db_data's field names suggest average power ("solarProduction", etc) but
for a DAY-timespan request they are actually the day's energy total in Wh -
confirmed empirically (a value of ~32000 for a day matches a plausible ~32
kWh/day production, not a plausible ~32 kW average). Two things are
backfilled from that Wh figure:

  - This integration's own power (W) sensors, as `mean` long-term statistics,
    by dividing the day's Wh total by 24 to get an average watts figure.
  - The separate cumulative kWh sensors used by the Energy dashboard (the
    `integration:` platform sensors from the README, e.g.
    sensor.solar_production) as `sum` long-term statistics, seeded from
    whichever cumulative total already exists so newly backfilled days
    connect cleanly with real data before/after the gap.

Only day-level resolution is reconstructed: one statistics row per missing
day. This is enough for daily/weekly/monthly rollups and the Energy dashboard
to be correct, but an hour-zoomed history graph will show a single populated
hour per backfilled day rather than a smooth 24-hour curve.
"""
from __future__ import annotations

import datetime
import logging

import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower
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
# in sensor.py, and whether that field is a Wh/day energy total (needs
# dividing by 24 to become an average watts figure) or already a percentage.
# These entities have no unique_id, so their entity_id is whatever HA
# slugifies their fixed _attr_name to - that's the only stable handle we
# have, and it's what's used here and in the README.
SENSOR_MAP = [
    ("solarProduction", "sensor.e3dc_solar_production", "E3DC Solar Production", UnitOfPower.WATT, True),
    ("grid_power_in", "sensor.e3dc_grid_production", "E3DC Grid Production", UnitOfPower.WATT, True),
    ("grid_power_out", "sensor.e3dc_grid_consumption", "E3DC Grid Consumption", UnitOfPower.WATT, True),
    ("consumption", "sensor.e3dc_house_consumption", "E3DC House Consumption", UnitOfPower.WATT, True),
    ("bat_power_in", "sensor.e3dc_battery_incoming", "E3DC Battery Incoming", UnitOfPower.WATT, True),
    ("bat_power_out", "sensor.e3dc_battery_outgoing", "E3DC Battery Outgoing", UnitOfPower.WATT, True),
    ("stateOfCharge", "sensor.e3dc_battery_charge", "E3DC Battery Charge", PERCENTAGE, False),
    ("autarky", "sensor.e3dc_autarky", "E3DC Autarky", PERCENTAGE, False),
    ("consumed_production", "sensor.e3dc_domestic_consumption", "E3DC Domestic Consumption", PERCENTAGE, False),
]

# Used to check whether a day already has data - any one sensor is
# representative since all are created and polled together.
_REFERENCE_STATISTIC_ID = SENSOR_MAP[0][1]

# get_db_data() field -> the Energy dashboard sensor it feeds, per the
# `integration:` (Riemann sum) sensors documented in the README. These
# entities belong to a different integration and have no fixed entity_id, so
# their statistic_id is configurable (see DEFAULT_ENERGY_ENTITY_IDS).
ENERGY_FIELD_MAP = {
    "solarProduction": "solar_production",
    "grid_power_in": "grid_return",  # exported to the grid
    "grid_power_out": "grid_consumption",  # imported from the grid
    "bat_power_in": "battery_incoming",
    "bat_power_out": "battery_outgoing",
}

DEFAULT_ENERGY_ENTITY_IDS = {
    "solar_production": "sensor.solar_production",
    "grid_return": "sensor.grid_return",
    "grid_consumption": "sensor.grid_consumption",
    "battery_incoming": "sensor.battery_incoming",
    "battery_outgoing": "sensor.battery_outgoing",
}


def _unit_class_for(unit: str) -> str | None:
    try:
        from homeassistant.components.recorder.statistics import (
            STATISTIC_UNIT_TO_UNIT_CONVERTER,
        )
    except ImportError:
        return None
    converter = STATISTIC_UNIT_TO_UNIT_CONVERTER.get(unit)
    return converter.UNIT_CLASS if converter else None


def _build_metadata(statistic_id: str, name: str, unit: str, has_mean: bool, has_sum: bool) -> dict:
    metadata = {
        "has_mean": has_mean,
        "has_sum": has_sum,
        "name": name,
        "source": "recorder",
        "statistic_id": statistic_id,
        "unit_of_measurement": unit,
        "unit_class": _unit_class_for(unit),
    }
    try:
        # HA >= 2024.9 additionally wants mean_type; older versions ignore
        # unknown metadata keys, so it's safe to always set this.
        from homeassistant.components.recorder.models import StatisticMeanType

        metadata["mean_type"] = StatisticMeanType.ARITHMETIC if has_mean else StatisticMeanType.NONE
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


async def _day_has_data(hass: HomeAssistant, day: datetime.date) -> bool:
    start = _day_start_utc(day)
    end = start + datetime.timedelta(days=1)

    def _query() -> bool:
        result = statistics_during_period(
            hass, start, end, {_REFERENCE_STATISTIC_ID}, "hour", None, {"mean"}
        )
        return bool(result.get(_REFERENCE_STATISTIC_ID))

    return await get_instance(hass).async_add_executor_job(_query)


async def _sum_before(hass: HomeAssistant, statistic_id: str, before: datetime.datetime) -> float:
    """Return the last known cumulative `sum` for statistic_id strictly before `before`."""

    def _query() -> float:
        result = statistics_during_period(
            hass, dt_util.utc_from_timestamp(0), before, {statistic_id}, "hour", None, {"sum"}
        )
        rows = result.get(statistic_id)
        if not rows:
            return 0.0
        return float(rows[-1]["sum"] or 0.0)

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
    max_days_back: int = 31,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    energy_entity_ids: dict[str, str | None] | None = None,
) -> None:
    """Backfill missing past days of E3DC data as long-term statistics.

    With no explicit start_date/end_date, this checks every day between
    max_days_back and yesterday and backfills whichever ones have no
    statistics yet - this is what runs automatically on every HA startup and
    is a no-op once caught up. Pass explicit dates (e.g. via the
    backfill_days service) to force a specific range, overwriting any
    existing statistics in it without checking first - use this to correct
    previously-backfilled days too, if they were imported by an older,
    buggy version of this module.
    """
    forced_range = start_date is not None
    _LOGGER.info(
        "E3DC backfill: starting%s (max_days_back=%s, start_date=%s, end_date=%s)",
        " (forced range)" if forced_range else "",
        max_days_back,
        start_date,
        end_date,
    )

    energy_entity_ids = {**DEFAULT_ENERGY_ENTITY_IDS, **(energy_entity_ids or {})}
    energy_entity_ids = {
        key: entity_id
        for key, entity_id in energy_entity_ids.items()
        if entity_id and hass.states.get(entity_id) is not None
    }
    missing_energy_entities = set(DEFAULT_ENERGY_ENTITY_IDS) - set(energy_entity_ids)
    if missing_energy_entities:
        _LOGGER.warning(
            "E3DC backfill: Energy dashboard sensors not found, skipping their sum backfill: %s",
            ", ".join(sorted(missing_energy_entities)),
        )

    today = dt_util.now().date()
    yesterday = today - datetime.timedelta(days=1)
    if start_date is None:
        start_date = today - datetime.timedelta(days=max_days_back)
    if end_date is None:
        end_date = yesterday

    if start_date > end_date:
        _LOGGER.debug("E3DC backfill: nothing to check, start date %s is after end date %s", start_date, end_date)
        return

    per_sensor_stats: dict[str, list[dict]] = {statistic_id: [] for _, statistic_id, _, _, _ in SENSOR_MAP}
    energy_stats: dict[str, list[dict]] = {entity_id: [] for entity_id in energy_entity_ids.values()}
    energy_running_sum: dict[str, float] = {}
    first_fetch = True
    days_backfilled = 0

    day = start_date
    while day <= end_date:
        _LOGGER.info("E3DC backfill: testing day %s", day)

        if not forced_range and await _day_has_data(hass, day):
            day += datetime.timedelta(days=1)
            continue

        _LOGGER.info("E3DC backfill: day %s has no data, backfilling from the E3DC archive", day)

        data = await _fetch_day(hass, e3dc_api, day)
        if data is not None:
            if first_fetch:
                _LOGGER.info("E3DC backfill: sample archive data for %s: %s", day, data)
                first_fetch = False
            day_start = _day_start_utc(day)

            for field, statistic_id, _, _, is_energy_wh in SENSOR_MAP:
                value = data.get(field)
                if value is None:
                    continue
                value = float(value)
                if is_energy_wh:
                    value = value / 24.0  # Wh for the day -> average W
                per_sensor_stats[statistic_id].append(
                    {"start": day_start, "mean": value, "min": value, "max": value}
                )

            for field, energy_key in ENERGY_FIELD_MAP.items():
                statistic_id = energy_entity_ids.get(energy_key)
                if statistic_id is None:
                    continue
                value = data.get(field)
                if value is None:
                    continue
                if statistic_id not in energy_running_sum:
                    energy_running_sum[statistic_id] = await _sum_before(hass, statistic_id, day_start)
                energy_running_sum[statistic_id] += float(value) / 1000.0  # Wh -> kWh
                energy_stats[statistic_id].append({"start": day_start, "sum": energy_running_sum[statistic_id]})

            days_backfilled += 1
        day += datetime.timedelta(days=1)

    for field, statistic_id, name, unit, _ in SENSOR_MAP:
        stats = per_sensor_stats[statistic_id]
        if stats:
            async_import_statistics(hass, _build_metadata(statistic_id, name, unit, True, False), stats)

    for energy_key, statistic_id in energy_entity_ids.items():
        stats = energy_stats.get(statistic_id)
        if stats:
            name = energy_key.replace("_", " ").title()
            async_import_statistics(
                hass,
                _build_metadata(statistic_id, name, UnitOfEnergy.KILO_WATT_HOUR, False, True),
                stats,
            )

    _LOGGER.info("E3DC backfill: done, backfilled %s day(s)", days_backfilled)


def async_register_backfill_service(
    hass: HomeAssistant, domain: str, e3dc_api, max_days_back: int, energy_entity_ids: dict[str, str | None]
) -> None:
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
            energy_entity_ids=energy_entity_ids,
        )

    hass.services.async_register(
        domain, SERVICE_BACKFILL_DAYS, _handle_service, schema=BACKFILL_SERVICE_SCHEMA
    )
