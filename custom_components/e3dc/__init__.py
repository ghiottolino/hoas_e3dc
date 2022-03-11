"""
The "hello world" custom component.
This component implements the bare minimum that a component should implement.
Configuration:
To use the hello_world component you will need to add the following to your
configuration.yaml file.
hello_world_async:
"""
from __future__ import annotations


import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from ._e3dc import E3DC

PLATFORMS: list[Platform] = [Platform.SENSOR]

def setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up a skeleton component."""
    # Return boolean to indicate that initialization was successfully.
    return True
