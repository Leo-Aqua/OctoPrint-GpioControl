# coding=utf-8
from __future__ import absolute_import, print_function
from octoprint.server import user_permission

import octoprint.plugin
import flask
import RPi.GPIO as GPIO

from time import sleep


class GpioControlPlugin(
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.SimpleApiPlugin,
    octoprint.plugin.RestartNeedingPlugin,
):
    mode = None

    def on_startup(self, *args, **kwargs):
        GPIO.setwarnings(False)
        self.mode = GPIO.getmode()
        if self.mode is None:
            self.mode = GPIO.BCM
            GPIO.setmode(self.mode)
        self._logger.info("Detected GPIO mode: {}".format(self.mode))

    def get_template_configs(self):
        return [
            dict(type="settings", custom_bindings=True),
            dict(
                type="sidebar",
                custom_bindings=True,
                template="gpiocontrol_sidebar.jinja2",
                icon="map-signs",
            ),
        ]

    def get_assets(self):
        return dict(
            js=["js/gpiocontrol.js", "js/fontawesome-iconpicker.min.js"],
            css=["css/gpiocontrol.css", "css/fontawesome-iconpicker.min.css"],
        )

    def get_settings_defaults(self):
        return dict(gpio_configurations=[])

    def on_settings_save(self, data):
        # Clean up and configure external switch pins
        for configuration in self._settings.get(["gpio_configurations"]):
            self._logger.info(
                "Cleaned GPIO{}: {},{} ({})".format(
                    configuration["pin"],
                    configuration["active_mode"],
                    configuration["default_state"],
                    configuration["name"],
                )
            )
            pin = self.get_pin_number(int(configuration["pin"]))
            if pin > 0:
                GPIO.cleanup(pin)

            # Handle external switch configuration
            external_switch = configuration.get("external_switch", "none")
            switch_pin = self.get_pin_number(int(configuration.get("switch_pin", -1)))
            if external_switch != "none" and switch_pin > 0:
                # Remove any previous event detection
                try:
                    GPIO.remove_event_detect(switch_pin)
                except RuntimeError:
                    pass  # Ignore if event detect was not set

                # Set up the external switch as an input BEFORE adding event detection
                if external_switch == "normally_open":
                    GPIO.setup(switch_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                elif external_switch == "normally_closed":
                    GPIO.setup(switch_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

        # Now save settings
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)

        # Now configure GPIO outputs
        for configuration in self._settings.get(["gpio_configurations"]):
            self._logger.info(
                "Reconfigured GPIO{}: {},{} ({})".format(
                    configuration["pin"],
                    configuration["active_mode"],
                    configuration["default_state"],
                    configuration["name"],
                )
            )
            pin = self.get_pin_number(int(configuration["pin"]))
            if pin > 0:
                GPIO.setup(pin, GPIO.OUT)
                if configuration["active_mode"] == "active_low":
                    if configuration["default_state"] == "default_on":
                        GPIO.output(pin, GPIO.LOW)
                    elif configuration["default_state"] == "default_off":
                        GPIO.output(pin, GPIO.HIGH)
                elif configuration["active_mode"] == "active_high":
                    if configuration["default_state"] == "default_on":
                        GPIO.output(pin, GPIO.HIGH)
                    elif configuration["default_state"] == "default_off":
                        GPIO.output(pin, GPIO.LOW)

        # Finally, add event detection for external switches (now that they’re set up as inputs)
        for index, configuration in enumerate(
            self._settings.get(["gpio_configurations"])
        ):
            external_switch = configuration.get("external_switch", "none")
            switch_pin = self.get_pin_number(int(configuration.get("switch_pin", -1)))
            if external_switch != "none" and switch_pin > 0:
                # Make sure the switch pin is properly set up before adding event detection
                if external_switch == "normally_open":
                    GPIO.setup(switch_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                elif external_switch == "normally_closed":
                    GPIO.setup(switch_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

                # Add event detection for both rising and falling edges
                GPIO.add_event_detect(
                    switch_pin,
                    GPIO.BOTH,
                    callback=self._switch_callback,
                    bouncetime=50,  # Debounce time to avoid rapid triggering
                )

    def on_after_startup(self):
        for configuration in self._settings.get(["gpio_configurations"]):
            self._logger.info(
                "Configured GPIO{}: {},{} ({})".format(
                    configuration["pin"],
                    configuration["active_mode"],
                    configuration["default_state"],
                    configuration["name"],
                )
            )
            # Configure output pins
            pin = self.get_pin_number(int(configuration["pin"]))
            if pin != -1:
                GPIO.setup(pin, GPIO.OUT)
                if configuration["active_mode"] == "active_low":
                    if configuration["default_state"] == "default_on":
                        GPIO.output(pin, GPIO.LOW)
                    elif configuration["default_state"] == "default_off":
                        GPIO.output(pin, GPIO.HIGH)
                elif configuration["active_mode"] == "active_high":
                    if configuration["default_state"] == "default_on":
                        GPIO.output(pin, GPIO.HIGH)
                    elif configuration["default_state"] == "default_off":
                        GPIO.output(pin, GPIO.LOW)

            # Set up external switch input and add event detection
            external_switch = configuration.get("external_switch", "none")
            switch_pin = self.get_pin_number(int(configuration.get("switch_pin", -1)))
            if external_switch != "none" and switch_pin > 0:
                if external_switch == "normally_open":
                    GPIO.setup(switch_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                elif external_switch == "normally_closed":
                    GPIO.setup(switch_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
                GPIO.add_event_detect(
                    switch_pin,
                    GPIO.BOTH,
                    callback=self._switch_callback,
                    bouncetime=50,
                )

    def _switch_callback(self, channel):
        """
        This callback is called when an external switch changes state.
        It looks up the configuration that matches the triggered channel,
        determines if the switch is activated, and turns on the corresponding
        GPIO output if needed. It also sends a plugin message so that the UI
        can update the button state.
        """
        self._logger.info("Switch callback triggered on channel {}".format(channel))
        for index, configuration in enumerate(
            self._settings.get(["gpio_configurations"])
        ):
            external_switch = configuration.get("external_switch", "none")
            if external_switch == "none":
                continue

            switch_pin = self.get_pin_number(int(configuration.get("switch_pin", -1)))
            if switch_pin != channel:
                continue

            pin = self.get_pin_number(int(configuration["pin"]))
            if pin <= 0:
                continue

            input_state = GPIO.input(channel)
            activated = False

            # For normally_open: activated when input goes LOW
            if external_switch == "normally_open" and input_state == GPIO.LOW:
                activated = True
            # For normally_closed: activated when input goes HIGH
            elif external_switch == "normally_closed" and input_state == GPIO.HIGH:
                activated = True

            if activated:
                self._logger.info(
                    "External switch (pin {}) activated. Turning on GPIO{}".format(
                        channel, configuration["pin"]
                    )
                )

                # Only change the pin if it's not already in the desired state
                if configuration["active_mode"] == "active_low":
                    if GPIO.input(pin) != GPIO.LOW:
                        GPIO.output(pin, GPIO.LOW)
                elif configuration["active_mode"] == "active_high":
                    if GPIO.input(pin) != GPIO.HIGH:
                        GPIO.output(pin, GPIO.HIGH)

                self._plugin_manager.send_plugin_message(
                    __plugin_name__, {"id": index, "state": "on"}
                )
            else:
                self._logger.info(
                    "External switch (pin {}) deactivated.".format(channel)
                )

                # Only change the pin if it's not already in the desired state
                # Optionally, turn off the GPIO when the switch is released:
                if configuration["active_mode"] == "active_low":
                    if (
                        GPIO.input(pin) != GPIO.HIGH
                    ):  # Prevent overriding if pin is already high
                        GPIO.output(pin, GPIO.HIGH)
                elif configuration["active_mode"] == "active_high":
                    if (
                        GPIO.input(pin) != GPIO.LOW
                    ):  # Prevent overriding if pin is already low
                        GPIO.output(pin, GPIO.LOW)

                self._plugin_manager.send_plugin_message(
                    __plugin_name__, {"id": index, "state": "off"}
                )

    def get_api_commands(self):
        return dict(turnGpioOn=["id"], turnGpioOff=["id"], getGpioState=["id"])

    def on_api_command(self, command, data):
        if not user_permission.can():
            return flask.make_response("Insufficient rights", 403)

        self._logger.info("on_api_command -> data: {}".format(data))
        configuration = self._settings.get(["gpio_configurations"])[int(data["id"])]
        pin = self.get_pin_number(int(configuration["pin"]))

        if command == "getGpioState":
            if pin < 0:
                return flask.jsonify("")
            elif configuration["active_mode"] == "active_low":
                return flask.jsonify("off" if GPIO.input(pin) else "on")
            elif configuration["active_mode"] == "active_high":
                return flask.jsonify("on" if GPIO.input(pin) else "off")
        elif command == "turnGpioOn":
            if pin > 0:
                self._logger.info("Turned on GPIO{}".format(configuration["pin"]))
                if configuration["active_mode"] == "active_low":
                    GPIO.output(pin, GPIO.LOW)
                elif configuration["active_mode"] == "active_high":
                    GPIO.output(pin, GPIO.HIGH)
        elif command == "turnGpioOff":
            if pin > 0:
                self._logger.info("Turned off GPIO{}".format(configuration["pin"]))
                if configuration["active_mode"] == "active_low":
                    GPIO.output(pin, GPIO.HIGH)
                elif configuration["active_mode"] == "active_high":
                    GPIO.output(pin, GPIO.LOW)

    def on_api_get(self, request):
        states = []
        for configuration in self._settings.get(["gpio_configurations"]):
            pin = self.get_pin_number(int(configuration["pin"]))
            if pin < 0:
                states.append("")
            elif configuration["active_mode"] == "active_low":
                states.append("off" if GPIO.input(pin) else "on")
            elif configuration["active_mode"] == "active_high":
                states.append("on" if GPIO.input(pin) else "off")
        return flask.jsonify(states)

    def get_update_information(self):
        return dict(
            gpiocontrol=dict(
                displayName="GPIO Control mod",
                displayVersion=self._plugin_version,
                type="github_release",
                user="Leo-Aqua",
                repo="OctoPrint-GpioControl",
                current=self._plugin_version,
                stable_branch=dict(
                    name="Stable",
                    branch="master",
                    comittish=["master"],
                ),
                prerelease_branches=[
                    dict(
                        name="Prerelease",
                        branch="development",
                        comittish=["development", "master"],
                    )
                ],
                pip="https://github.com/Leo-Aqua/OctoPrint-GpioControl/archive/{target_version}.zip",
            )
        )

    PIN_MAPPINGS = [
        -1,
        -1,
        3,
        5,
        7,
        29,
        31,
        26,
        24,
        21,
        19,
        23,
        32,
        33,
        8,
        10,
        36,
        11,
        12,
        35,
        38,
        40,
        15,
        16,
        18,
        22,
        37,
        13,
    ]

    def get_pin_number(self, pin):
        if 2 <= pin <= 27:
            if self.mode == GPIO.BCM:
                return pin
            if self.mode == GPIO.BOARD:
                return self.PIN_MAPPINGS[pin]
        return -1


__plugin_name__ = "GPIO Control mod"
__plugin_pythoncompat__ = ">=2.7,<4"


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = GpioControlPlugin()
    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
    }
