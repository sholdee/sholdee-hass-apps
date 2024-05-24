import appdaemon.plugins.hass.hassapi as hass

#
# App to Turn on fan when difference between upstairs and downstairs temps are beyond a threshold
#
# Args:
#
# app_switch: on/off switch for this app. eg: input_boolean.stairway_fan
# upper_temp_sensor: upper floor temp sensor to monitor. eg: sensor.upper_temperature
# lower_temp_sensor: lower floor temp sensor to monitor. eg: sensor.lower_temperature
# threshold_entity: entity which holds the temp threshold which must be reached.
#     eg: input_number.floor_temp_difference_threshold
# lower_threshold_entity: entity which holds the temp threshold at which actor power off is scheduled.
#     eg: input_number.floor_temp_difference_lower_threshold
# actor: actor to turn on
# delay: seconds to wait before turning off. example: 300

class StairwayFan(hass.Hass):
    def initialize(self):
        self.listen_state_handle_list = []
        self.timer_handle_list = []
        self.turn_off_timer_handle = None

        self.app_switch = self.args["app_switch"]
        self.upper_temp_sensor = self.args["upper_temp_sensor"]
        self.lower_temp_sensor = self.args["lower_temp_sensor"]
        self.threshold_entity = self.args["threshold_entity"]
        self.lower_threshold_entity = self.args["lower_threshold_entity"]
        self.actor = self.args["actor"]
        self.delay = int(self.args["delay"])

        self.turned_on_by_me = False

        self.watched_entity_list = [
            self.app_switch,
            self.upper_temp_sensor,
            self.lower_temp_sensor,
            self.threshold_entity,
            self.lower_threshold_entity,
        ]

        for entity in self.watched_entity_list:
            self.listen_state_handle_list.append(
                self.listen_state(self.state_change, entity)
            )

        self.log_initial_state()

    def log_initial_state(self):
        self.log(
            f"Stairway fan app initialized. App switch: {self.get_state(self.app_switch)} "
            f"Upper temp: {self.get_state(self.upper_temp_sensor)} "
            f"Lower temp: {self.get_state(self.lower_temp_sensor)} "
            f"Threshold: {self.get_state(self.threshold_entity)} "
            f"Cutoff: {self.get_state(self.lower_threshold_entity)}"
        )

    def state_change(self, entity, attribute, old, new, kwargs):
        self.log(f"State change detected for {entity}: {old} -> {new}")
        
        if self.get_state(self.app_switch) != "on":
            return

        upper_temp = self.get_valid_state(self.upper_temp_sensor)
        lower_temp = self.get_valid_state(self.lower_temp_sensor)
        threshold = self.get_valid_state(self.threshold_entity)
        lower_threshold = self.get_valid_state(self.lower_threshold_entity)

        if None in (upper_temp, lower_temp, threshold, lower_threshold):
            self.log("One or more sensor states are invalid. Skipping processing.")
            return

        temp_difference = upper_temp - lower_temp

        self.log(f"Temperature difference: {temp_difference}")

        if temp_difference > threshold:
            self.handle_fan_turn_on(temp_difference, threshold)
        elif temp_difference <= lower_threshold:
            self.handle_fan_turn_off(temp_difference, lower_threshold)

    def get_valid_state(self, entity):
        state = self.get_state(entity)
        if state not in [None, "unknown", "unavailable"]:
            try:
                return float(state)
            except ValueError:
                self.log(f"Invalid state value for {entity}: {state}")
        return None

    def handle_fan_turn_on(self, temp_difference, threshold):
        if self.get_state(self.actor) != "on":
            self.log(
                f"{self.friendly_name(self.upper_temp_sensor)} is {temp_difference} higher than "
                f"{self.friendly_name(self.lower_temp_sensor)}. This is above threshold of {threshold}."
            )
            self.log(f"Turning on {self.friendly_name(self.actor)}")
            self.turn_on(self.actor)
            self.turned_on_by_me = True

        if self.turn_off_timer_handle:
            self.log("Cancelling scheduled power off")
            self.cancel_timer(self.turn_off_timer_handle)
            self.timer_handle_list.remove(self.turn_off_timer_handle)
            self.turn_off_timer_handle = None

    def handle_fan_turn_off(self, temp_difference, lower_threshold):
        if self.turned_on_by_me and self.get_state(self.actor) != "off":
            if not self.turn_off_timer_handle:
                self.log(
                    f"{self.friendly_name(self.upper_temp_sensor)} is {temp_difference} higher than "
                    f"{self.friendly_name(self.lower_temp_sensor)}. This is within lower threshold of {lower_threshold}."
                )
                self.log(f"Turning off {self.friendly_name(self.actor)} in {self.delay} seconds")
                self.turn_off_timer_handle = self.run_in(self.turn_off_callback, self.delay)
                self.timer_handle_list.append(self.turn_off_timer_handle)

    def turn_off_callback(self, kwargs):
        if self.turned_on_by_me:
            self.log(f"Turning off {self.friendly_name(self.actor)}")
            self.turn_off(self.actor)
            self.turned_on_by_me = False
        self.turn_off_timer_handle = None

    def terminate(self):
        for handle in self.listen_state_handle_list:
            self.cancel_listen_state(handle)
        for handle in self.timer_handle_list:
            self.cancel_timer(handle)
