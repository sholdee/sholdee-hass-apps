import appdaemon.plugins.hass.hassapi as hass
import datetime

#
# App to turn on outside lights when someone comes home
#
# Args:
#
# homesensors: sensor(s) to test for home state
# lights: lights(s) to turn on. example: switch.rear_floodlight

class LightsOnWhenHome(hass.Hass):
    def initialize(self):
        self.listen_state_handle_list = []

        self.homesensors = self.args["homesensors"]
        self.lights = self.args["lights"]

        for sensor in self.homesensors:
            self.listen_state_handle_list.append(
                self.listen_state(self.state_change, sensor)
            )

    def state_change(self, entity, attribute, old, new, kwargs):
        if new == "home" and old == "not_home":
            if self.get_state("sun.sun") == "below_horizon":
                self.log("{} changed to {} and sun is below horizon. Turning on lights.".format(self.friendly_name(entity), new))
                for light in self.lights:
                    if self.get_state(light) == "off":
                        self.turn_on(light)
                        self.log("Turned on {}.".format(self.friendly_name(light)))

    def terminate(self):
        for listen_state_handle in self.listen_state_handle_list:
            self.cancel_listen_state(listen_state_handle)