"""Platform for sensor integration."""
from __future__ import annotations
from datetime import timedelta
import logging
import async_timeout


from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.exceptions import ConfigEntryAuthFailed

from homeassistant.const import POWER_WATT, PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN
from ._e3dc import E3DC

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistantType,
    config: ConfigType,
    async_add_entities: Callable,
    discovery_info: Optional[DiscoveryInfoType] = None,
) -> None:
    """Config entry example."""
    
    logging.info("initializing E3DC sensors with config:")
    logging.info(config)    
    
    USERNAME = config['username']
    PASS = config['md5_pass']
    SERIALNUMBER = str(config['serial_number'])
    CONFIG = {"powermeters": [{"index": config['power_meters_index']}]}

    e3dc_api = E3DC(E3DC.CONNECT_WEB, username=USERNAME, password=PASS, serialNumber = SERIALNUMBER, isPasswordMd5=True, configuration = CONFIG)
    e3dc_data = E3DCData()
    
    async_add_entities(
        #[HouseConsumpion(coordinator), GridConsumption(e3dc_api)]
        [E3DCSensor(e3dc_api, e3dc_data), GridProduction(e3dc_data), SolarProduction(e3dc_data),GridConsumption(e3dc_data),HouseConsumption(e3dc_data), WallboxConsumption(e3dc_data), BatteryIncoming(e3dc_data),BatteryOutgoing(e3dc_data),BatteryCharge(e3dc_data)]
    )


class E3DCData():
    """My custom coordinator."""

    def __init__(self):
        self.data = []

    def update(self,data):
        logging.info("Updating data with:")
        logging.info(data)
        self.data = data
    
    def get(self):
        logging.info("Reading data:")
        logging.info(self.data)
        return self.data

class E3DCSensor(SensorEntity):       
    """Representation of a Sensor."""

    _attr_name = "E3DC Sensor"
    #_attr_native_unit_of_measurement = POWER_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, api, e3dc_data):
        self.api = api
        self.e3dc_data = e3dc_data
        
    def update(self) -> None:
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """
        logging.info("Polling E3DC Web")
        e3dc_status = self.api.poll()
        self.e3dc_data.update(e3dc_status)
        self._attr_native_value = e3dc_status

class GridProduction(SensorEntity):
    _attr_name = "E3DC Grid Production"
    _attr_native_unit_of_measurement = POWER_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, e3dc_data):
        self.e3dc_data = e3dc_data
        
    def update(self) -> None:
        e3dc_status = self.e3dc_data.get()
        grid_production = e3dc_status['production']['grid']
        if grid_production < 0:
            self._attr_native_value = -grid_production
        else:
            self._attr_native_value = 0
            
class SolarProduction(SensorEntity):
    _attr_name = "E3DC Solar Production"
    _attr_native_unit_of_measurement = POWER_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, e3dc_data):
        self.e3dc_data = e3dc_data
        
    def update(self) -> None:
        e3dc_status = self.e3dc_data.get()
        self._attr_native_value = e3dc_status['production']['solar']        
 
class GridConsumption(SensorEntity):
    _attr_name = "E3DC Grid Consumption"
    _attr_native_unit_of_measurement = POWER_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, e3dc_data):
        self.e3dc_data = e3dc_data
        
    def update(self) -> None:
        e3dc_status = self.e3dc_data.get()
        grid_production = e3dc_status['production']['grid']
        if grid_production > 0:
            self._attr_native_value = grid_production
        else:
            self._attr_native_value = 0
        
class HouseConsumption(SensorEntity):
    _attr_name = "E3DC House Consumtpion"
    _attr_native_unit_of_measurement = POWER_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, e3dc_data):
        self.e3dc_data = e3dc_data
        
    def update(self) -> None:
        e3dc_status = self.e3dc_data.get()
        self._attr_native_value = e3dc_status['consumption']['house']

class WallboxConsumption(SensorEntity):
    _attr_name = "E3DC Wallbox Consumption"
    _attr_native_unit_of_measurement = POWER_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, e3dc_data):
        self.e3dc_data = e3dc_data
        
    def update(self) -> None:
        e3dc_status = self.e3dc_data.get()
        self._attr_native_value = e3dc_status['consumption']['wallbox']  
                
class BatteryIncoming(SensorEntity):
    _attr_name = "E3DC Battery Incoming"
    _attr_native_unit_of_measurement = POWER_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, e3dc_data):
        self.e3dc_data = e3dc_data
        
    def update(self) -> None:
        e3dc_status = self.e3dc_data.get()
        battery_consumption = e3dc_status['consumption']['battery']
        if battery_consumption > 0:
            self._attr_native_value = battery_consumption
        else:
            self._attr_native_value = 0
        
class BatteryOutgoing(SensorEntity):
    _attr_name = "E3DC Battery Outgoing"
    _attr_native_unit_of_measurement = POWER_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, e3dc_data):
        self.e3dc_data = e3dc_data
        
    def update(self) -> None:
        e3dc_status = self.e3dc_data.get()
        battery_consumption = e3dc_status['consumption']['battery']
        if battery_consumption < 0:
            self._attr_native_value = -battery_consumption
        else:
            self._attr_native_value = 0
                
        
class BatteryCharge(SensorEntity):
    _attr_name = "E3DC Battery Charge"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, e3dc_data):
        self.e3dc_data = e3dc_data
        
    def update(self) -> None:
        e3dc_status = self.e3dc_data.get()
        self._attr_native_value = e3dc_status['stateOfCharge']                 
