# HOAS E3DC
Home Assistant integration to provide E3DC data to hoas retrieving data from the e3dc web portal.

# dependencies and thanks

* [python-e3dc](https://raw.githubusercontent.com/fsantini/python-e3dc): code base integrated in the project for solving issues with HOAS dependencies import.
#

# Installation

1. Make sure the [HACS](https://github.com/custom-components/hacs) custom component is installed and working.
2. Search for `E3DC Home Assistant` and add it through HACS
3. Refresh home-assistant.

# Configuration

```
sensor:
  - platform: e3dc
    scan_interval: 600
    username: YOUR_E3DC_USERNAME
    md5_pass: YOUR_E3DC_MD5_PASSWORD
    serial_number: YOUR_SERIAL_NUMBER
    power_meters_index: 6
    backfill: true
    backfill_max_days: 31
    # Only needed if you named the `integration:` sensors below differently
    # from the example - see "Usage with Home Assistant Energy".
    energy_solar_production_entity_id: sensor.solar_production
    energy_grid_return_entity_id: sensor.grid_return
    energy_grid_consumption_entity_id: sensor.grid_consumption
    energy_battery_incoming_entity_id: sensor.battery_incoming
    energy_battery_outgoing_entity_id: sensor.battery_outgoing
```

see [python-e3dc web connection configuration](https://github.com/fsantini/python-e3dc/blob/master/README.md#web-connection).

# Backfilling missing days

The E3DC system keeps its own daily archive independently of whether Home
Assistant was actually polling it. On every HA startup (unless `backfill:
false` is set), the integration checks every day up to `backfill_max_days`
(default 31) days back and backfills whichever ones have no statistics yet
from that archive. Only day-level resolution is reconstructed (one value per
missing day) - enough to keep daily/weekly/monthly history and the Energy
dashboard gap-free, but an hour-zoomed history graph will show a single
populated hour per backfilled day rather than a smooth curve.

Two things get backfilled for each missing day:

- This integration's own power sensors (solar/grid/house/battery power,
  battery charge, autarky, domestic consumption), as `mean` statistics.
- The Energy dashboard's cumulative kWh sensors - i.e. the `integration:`
  sensors from the section below (`sensor.solar_production`,
  `sensor.grid_return`, `sensor.grid_consumption`, `sensor.battery_incoming`,
  `sensor.battery_outgoing` by default) - as `sum` statistics, seeded from
  whatever cumulative total already exists so backfilled days connect
  cleanly with real data. If you named those sensors differently, set the
  `energy_*_entity_id` options above; sensors that can't be found are skipped
  with a warning in the log.

To force a specific range (e.g. a known outage window older than
`backfill_max_days`, or to correct days backfilled by an older, buggy version
of this feature), call the `e3dc.backfill_days` service from Developer Tools
> Actions with `start_date`/`end_date`. Unlike the automatic run, a forced
range always overwrites, regardless of whether those days already have data.

# Usage with Home Assistant Energy 

You will need to add a few sensors to integrate the measurement coming from E3DC and get proper statistics., this is what I've added:

```
sensor:

  - platform: integration
    source: sensor.e3dc_grid_production
    name: grid_return
    unit_prefix: k
    round: 2    
    
  - platform: integration
    source: sensor.e3dc_grid_consumption
    name: grid_consumption
    unit_prefix: k
    round: 2
    
  - platform: integration
    source: sensor.e3dc_house_consumption
    name: house_consumption
    unit_prefix: k
    round: 2
    
  - platform: integration
    source: sensor.e3dc_solar_production
    name: solar_production
    unit_prefix: k
    round: 2    

  - platform: integration
    source: sensor.e3dc_battery_incoming
    name: battery_incoming
    unit_prefix: k
    round: 2    
    
  - platform: integration
    source: sensor.e3dc_battery_outgoing
    name: battery_outgoing
    unit_prefix: k
    round: 2
```

then you can go to your energy dashboard (http://HOME_ASSISTANT_URL/config/energy/dashboard). You may need to wait a few hours before seeing the new statistics.

<p align="center">
<img src="https://raw.githubusercontent.com/ghiottolino/hoas_e3dc/main/hoas_power.png"/>
</p>

# Usage with Power Distribution Card

* [Power distribution Card](https://github.com/JonahKr/power-distribution-card) is a lovelace card for visualizing power distributions.



From a lovelace dashboard, this is how I've configured the power dinstribution card using the raw configuration editor. 

```
      - type: custom:power-distribution-card
        title: E3DC
        animation: flash
        entities:
          - entity: sensor.e3dc_solar_production
            preset: solar
          - entity: sensor.e3dc_grid_consumption_production
            preset: grid
          - entity: sensor.e3dc_battery_incoming_outgoing
            preset: battery
          - entity: sensor.e3dc_house_comsumption_negative
            preset: home
        center:
          type: bars
          content:
            - preset: ratio
              name: battery
              entity: sensor.e3dc_battery_charge
```
<p align="center">
<img src="https://raw.githubusercontent.com/ghiottolino/hoas_e3dc/main/power_distibution.png"/>
</p>
