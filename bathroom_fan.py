"""
AppDaemon module to turn on bathroom exhaust fan when bathroom humidity is beyond threshold and turn it back off automatically
Calculates and compares absolute humidity between bathroom and living space
Separate power off delays for either case of automatic or manual fan activation

Args:
    app_switch: optional on/off switch for this app. eg: input_boolean.auto_bathroom_fan
                if undefined, this app will always be on
    bathroom_humidity_sensor: bathroom humidity sensor to monitor. eg: sensor.4_in_1_sensor_humidity
    bathroom_temperature_sensor: bathroom temperature sensor. eg: sensor.4_in_1_sensor_air_temperature
    living_humidity_sensor: living space humidity sensor to monitor. eg: sensor.temp_sensor_upper_humidity
    living_temperature_sensor: living space temperature sensor. eg: sensor.temp_sensor_upper_air_temperature
    temperature_unit: the temperature unit (F/C) of sensor data. eg: F
    threshold: the absolute humidity threshold at which fan is activated. (g/m³) eg: 3.54
    lower_threshold: the absolute humidity threshold at which fan power off is scheduled. (g/m³) eg: 1.377
    actor: actor to turn on eg: switch.bathroom_fan
    delay: seconds to wait before turning off actor when turned on automatically. eg: 60
    manual_delay: seconds to wait before turning off actor when turned on manually. eg: 600
"""

import appdaemon.plugins.hass.hassapi as hass
import math

class BathroomFan(hass.Hass):
    """Class to control bathroom exhaust fan based on humidity levels."""

    def initialize(self):
        """Initializes the BathroomFan app."""
        self.listen_state_handle_list = []
        self.timer_handle_list = []
        self.manual_turn_off_timer_handle = None
        self.humidity_turn_off_timer_handle = None
        self.auto_activated = False  # Flag to track automatic activation
        self.timer_turn_off = False  # Flag to track if the fan is turned off by a timer

        self.app_switch = self.args.get("app_switch", None)
        self.bathroom_humidity_sensor = self.args["bathroom_humidity_sensor"]
        self.living_humidity_sensor = self.args["living_humidity_sensor"]
        self.bathroom_temperature_sensor = self.args["bathroom_temperature_sensor"]
        self.living_temperature_sensor = self.args["living_temperature_sensor"]
        self.temperature_unit = self.args.get("temperature_unit", "F").upper()
        self.threshold = float(self.args["threshold"])
        self.lower_threshold = float(self.args["lower_threshold"])
        self.actor = self.args["actor"]
        self.delay = int(self.args["delay"])
        self.manual_delay = int(self.args["manual_delay"])

        self.watched_entity_list = [
            self.bathroom_humidity_sensor,
            self.living_humidity_sensor,
            self.bathroom_temperature_sensor,
            self.living_temperature_sensor,
            self.actor
        ]

        # Add app_switch to the watched entity list only if it is defined
        if self.app_switch:
            self.watched_entity_list.append(self.app_switch)

        for entity in self.watched_entity_list:
            self.listen_state_handle_list.append(
                self.listen_state(self.state_change, entity)
            )

        self.log_initial_state()

    def log_initial_state(self):
        """Logs the initial state of the app and sensors."""
        app_switch_state = self.get_state(self.app_switch) if self.app_switch else "on"
        self.log(
            f"Bathroom fan app initialized. App switch: {app_switch_state} "
            f"Bathroom humidity: {self.get_state(self.bathroom_humidity_sensor)} "
            f"Living space humidity: {self.get_state(self.living_humidity_sensor)} "
            f"Bathroom temperature: {self.get_state(self.bathroom_temperature_sensor)} "
            f"Living space temperature: {self.get_state(self.living_temperature_sensor)} "
            f"Threshold: {self.threshold} "
            f"Lower threshold: {self.lower_threshold} "
            f"Delay: {self.delay} "
            f"Manual Delay: {self.manual_delay} "
            f"Temperature unit: {self.temperature_unit}"
        )

    def state_change(self, entity, attribute, old, new, kwargs):
        """
        Handles state changes for monitored entities.

        Args:
            entity (str): The entity that changed state.
            attribute (str): The attribute that changed.
            old (str): The old state value.
            new (str): The new state value.
            kwargs (dict): Additional keyword arguments.
        """
        self.log(f"State change detected for {entity}: {old} -> {new}")

        if entity == self.app_switch and old == "off" and new == "on":
            self.log("App switch turned on, checking fan state.")
            if self.get_state(self.actor) == "on" and not self.auto_activated:
                self.log("Fan is on but not auto-activated, scheduling manual turn off.")
                self.schedule_manual_turn_off(0)  # 0 used as a placeholder for humidity difference
        elif entity == self.app_switch and old == "on" and new == "off":
            # Cancel all timers and stop further processing
            self.log("App switch turned off, cancelling all timers and stopping further processing.")
            self.cancel_timer_handle("manual_turn_off_timer_handle")
            self.cancel_timer_handle("humidity_turn_off_timer_handle")

        if self.app_switch and self.get_state(self.app_switch) == "off":
            return

        humidity_difference, bathroom_absolute_humidity, living_absolute_humidity = self.calculate_humidity_difference()
        if humidity_difference is None:
            return

        self.log(f"Absolute humidity difference: {humidity_difference}, Bathroom: {bathroom_absolute_humidity}, Living: {living_absolute_humidity}")

        if entity == self.actor and old == "on" and new == "off":
            if self.timer_turn_off:
                # Reset the timer turn off flag
                self.timer_turn_off = False
                self.log("Fan turned off by timer.")
            elif self.auto_activated:
                # Fan turned off manually after being auto-activated
                self.log("Fan turned off manually after auto activation.")
                self.auto_activated = False  # Reset the auto-activated flag
                # Re-evaluate humidity to check if the fan should be turned on again
                if humidity_difference > self.threshold:
                    self.handle_fan_turn_on(humidity_difference, self.threshold)
            else:
                # Fan turned off manually, cancel the manual turn off timer
                self.log("Fan turned off manually, cancelling manual turn off timer.")
                self.cancel_timer_handle("manual_turn_off_timer_handle")
            return

        if entity == self.actor and new == "on" and old == "off" and not self.auto_activated:
            # Fan turned on manually
            self.log("Fan turned on manually, scheduling turn off if absolute humidity does not rise above threshold.")
            self.schedule_manual_turn_off(humidity_difference)
        else:
            # Normal humidity-based control
            if humidity_difference > self.threshold:
                self.handle_fan_turn_on(humidity_difference, self.threshold)
            elif humidity_difference <= self.lower_threshold:
                if not self.manual_turn_off_timer_handle:
                    self.handle_fan_turn_off(humidity_difference, self.lower_threshold)

    def get_valid_sensor_states(self):
        """
        Retrieves and validates the states of all relevant sensors.

        Returns:
            tuple: Valid states of bathroom humidity, living humidity, bathroom temperature, living temperature.
        """
        bathroom_humidity = self.get_valid_state(self.bathroom_humidity_sensor)
        living_humidity = self.get_valid_state(self.living_humidity_sensor)
        bathroom_temperature = self.get_valid_state(self.bathroom_temperature_sensor)
        living_temperature = self.get_valid_state(self.living_temperature_sensor)

        return bathroom_humidity, living_humidity, bathroom_temperature, living_temperature

    def get_valid_state(self, entity):
        """
        Retrieves and validates the state of a given entity.

        Args:
            entity (str): The entity to retrieve the state from.

        Returns:
            float: The valid state value or None if invalid.
        """
        state = self.get_state(entity)
        if state not in [None, "unknown", "unavailable"]:
            try:
                return float(state)
            except ValueError:
                self.log(f"Invalid state value for {entity}: {state}")
        return None

    def calculate_humidity_difference(self):
        """
        Calculates the humidity difference between the bathroom and living space.

        Returns:
            tuple: The difference in absolute humidity between the bathroom and the living space,
                   the bathroom absolute humidity, and the living space absolute humidity,
                   or (None, None, None) if any sensor state is invalid.
        """
        bathroom_humidity, living_humidity, bathroom_temperature, living_temperature = self.get_valid_sensor_states()

        if None in (bathroom_humidity, living_humidity, bathroom_temperature, living_temperature):
            self.log("One or more sensor states are invalid. Skipping processing.")
            return None, None, None

        bathroom_absolute_humidity = self.calculate_absolute_humidity(bathroom_humidity, bathroom_temperature)
        living_absolute_humidity = self.calculate_absolute_humidity(living_humidity, living_temperature)

        humidity_difference = bathroom_absolute_humidity - living_absolute_humidity
        return humidity_difference, bathroom_absolute_humidity, living_absolute_humidity

    def calculate_absolute_humidity(self, relative_humidity, temperature):
        """
        Calculates the absolute humidity from relative humidity and temperature.

        Args:
            relative_humidity (float): The relative humidity percentage.
            temperature (float): The temperature.

        Returns:
            float: The calculated absolute humidity in g/m³.
        """
        if self.temperature_unit == "F":
            # Convert temperature from Fahrenheit to Kelvin
            temperature_kelvin = (temperature - 32) * 5/9 + 273.15
            # Convert temperature from Fahrenheit to Celsius
            temperature_celsius = (temperature - 32) / 1.8
        else:
            # Convert temperature from Celsius to Kelvin
            temperature_kelvin = temperature + 273.15
            temperature_celsius = temperature

        # Calculate saturation vapor pressure in pascals (Pa)
        saturation_vapor_pressure = 6.112 * math.exp((17.67 * temperature_celsius) / (temperature_celsius + 243.5)) * 100  # Convert hPa to Pa
        # Calculate actual vapor pressure
        actual_vapor_pressure = (relative_humidity / 100) * saturation_vapor_pressure
        # Specific gas constant for water vapor
        R_w = 461.5  # J/(kg·K)
        # Calculate absolute humidity in g/m³
        absolute_humidity = (actual_vapor_pressure / (R_w * temperature_kelvin)) * 1000  # Convert kg/m³ to g/m³
        return absolute_humidity

    def handle_fan_turn_on(self, humidity_difference, threshold):
        """
        Handles turning on the fan based on humidity difference.

        Args:
            humidity_difference (float): The current humidity difference.
            threshold (float): The threshold humidity difference for turning on the fan.
        """
        self.auto_activated = True  # Mark as auto-activated

        if self.get_state(self.actor) == "off":
            self.log(
                f"{self.friendly_name(self.bathroom_humidity_sensor)} absolute humidity is {humidity_difference} higher than "
                f"{self.friendly_name(self.living_humidity_sensor)}. This is above threshold of {threshold}."
            )
            self.log(f"Turning on {self.friendly_name(self.actor)}")

            self.turn_on(self.actor)

        self.cancel_timer_handle("humidity_turn_off_timer_handle")
        self.cancel_timer_handle("manual_turn_off_timer_handle")

    def handle_fan_turn_off(self, humidity_difference, lower_threshold):
        """
        Handles turning off the fan based on humidity difference.

        Args:
            humidity_difference (float): The current humidity difference.
            lower_threshold (float): The lower threshold humidity difference for turning off the fan.
        """
        if not self.humidity_turn_off_timer_handle and self.get_state(self.actor) == "on":
            self.log(
                f"{self.friendly_name(self.bathroom_humidity_sensor)} absolute humidity is {humidity_difference} higher than "
                f"{self.friendly_name(self.living_humidity_sensor)}. This is within lower threshold of {lower_threshold}."
            )
            self.log(f"Turning off {self.friendly_name(self.actor)} in {self.delay} seconds")
            self.humidity_turn_off_timer_handle = self.run_in(self.turn_off_callback, self.delay)
            self.timer_handle_list.append(self.humidity_turn_off_timer_handle)

    def turn_off_callback(self, kwargs):
        """
        Callback for turning off the fan.

        Args:
            kwargs (dict): Additional keyword arguments.
        """
        self.log(f"Turning off {self.friendly_name(self.actor)}")
        self.timer_turn_off = True  # Set the timer turn off flag
        self.turn_off(self.actor)
        if self.humidity_turn_off_timer_handle in self.timer_handle_list:
            self.timer_handle_list.remove(self.humidity_turn_off_timer_handle)
        self.humidity_turn_off_timer_handle = None
        self.auto_activated = False  # Reset the auto-activated flag

    def schedule_manual_turn_off(self, humidity_difference):
        """
        Schedules the manual turn off of the fan.

        Args:
            humidity_difference (float): The current humidity difference.
        """
        if not self.manual_turn_off_timer_handle:
            self.log(f"Scheduling manual turn off in {self.manual_delay} seconds.")
            self.manual_turn_off_timer_handle = self.run_in(self.manual_turn_off_callback, self.manual_delay, humidity_difference=humidity_difference)
            self.timer_handle_list.append(self.manual_turn_off_timer_handle)

    def manual_turn_off_callback(self, kwargs):
        """
        Callback for manually turning off the fan.

        Args:
            kwargs (dict): Additional keyword arguments containing humidity difference.
        """
        self.log(f"Manual turn off triggered. Current absolute humidity difference ({kwargs['humidity_difference']}) <= threshold ({self.threshold}).")
        self.timer_turn_off = True  # Set the timer turn off flag
        self.turn_off(self.actor)
        if self.manual_turn_off_timer_handle in self.timer_handle_list:
            self.timer_handle_list.remove(self.manual_turn_off_timer_handle)
        self.manual_turn_off_timer_handle = None

    def cancel_timer_handle(self, timer_handle_name):
        """
        Cancels a timer and removes it from the timer handle list.

        Args:
            timer_handle_name (str): The name of the timer handle attribute to be cancelled and removed.
        """
        timer_handle = getattr(self, timer_handle_name)
        if timer_handle:
            self.cancel_timer(timer_handle)
            self.timer_handle_list.remove(timer_handle)
            setattr(self, timer_handle_name, None)

    def terminate(self):
        """Terminates the app and cancels all listeners and timers."""
        for handle in self.listen_state_handle_list:
            self.cancel_listen_state(handle)
        for handle in self.timer_handle_list:
            self.cancel_timer(handle)
