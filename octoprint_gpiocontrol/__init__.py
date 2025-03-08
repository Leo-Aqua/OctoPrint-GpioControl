# coding=utf-8
from __future__ import absolute_import, print_function
from octoprint.server import user_permission

import octoprint.plugin
import flask
from gpiozero import LED, Button
from time import sleep


class GpioControlPlugin(
    octoprint.plugin.StartupPlugin,
    octoprint.plugin.TemplatePlugin,
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.SimpleApiPlugin,
    octoprint.plugin.RestartNeedingPlugin,
):
    def __init__(self):
        self.gpio_outputs = {}  # Dictionary to store LED objects
        self.gpio_buttons = {}  # Dictionary to store Button objects

    def on_startup(self, *args, **kwargs):
        self._logger.info("GPIO Control initializing using GPIOZero")

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
        # Store old configurations for cleanup
        old_configurations = self._settings.get(["gpio_configurations"])

        # Save the new settings
        octoprint.plugin.SettingsPlugin.on_settings_save(self, data)

        # Get new configurations
        new_configurations = self._settings.get(["gpio_configurations"])

        # Clean up old GPIO configurations
        self._cleanup_gpios(old_configurations)

        # Configure new GPIO outputs and inputs
        self._configure_gpios(new_configurations)

    def _cleanup_gpios(self, configurations):
        """Clean up and close GPIO devices"""
        for config in configurations:
            pin = int(config["pin"])
            if pin in self.gpio_outputs:
                self._logger.info(f"Cleaning up GPIO{pin} ({config['name']})")
                self.gpio_outputs[pin].close()
                del self.gpio_outputs[pin]

            # Clean up button if exists
            switch_pin = int(config.get("switch_pin", -1))
            if switch_pin > 0 and switch_pin in self.gpio_buttons:
                self._logger.info(f"Cleaning up Button on GPIO{switch_pin}")
                self.gpio_buttons[switch_pin].close()
                del self.gpio_buttons[switch_pin]

    def _configure_gpios(self, configurations):
        """Configure GPIO outputs and inputs based on settings"""
        for index, config in enumerate(configurations):
            pin = int(config["pin"])
            if pin < 0:
                continue

            # Initialize the output pin
            self._logger.info(
                f"Configuring GPIO{pin}: {config['active_mode']},{config['default_state']} ({config['name']})"
            )

            # Set initial state based on configuration
            initial_value = False  # Default off
            if (
                config["active_mode"] == "active_high"
                and config["default_state"] == "default_on"
            ) or (
                config["active_mode"] == "active_low"
                and config["default_state"] == "default_off"
            ):
                initial_value = True

            # Create LED with appropriate active_high parameter
            active_high = config["active_mode"] == "active_high"
            self.gpio_outputs[pin] = LED(
                pin, active_high=active_high, initial_value=initial_value
            )

            # Handle external switch configuration
            external_switch = config.get("external_switch", "none")
            switch_pin = int(config.get("switch_pin", -1))

            if external_switch != "none" and switch_pin > 0:
                self._logger.info(f"Configuring external switch on GPIO{switch_pin}")

                # Set up pull_up based on switch type
                pull_up = external_switch == "normally_open"

                # Create Button object with appropriate callback
                button = Button(switch_pin, pull_up=pull_up, bounce_time=0.05)

                # Configure button callbacks
                button.when_pressed = (
                    lambda p=pin, i=index, c=config: self._button_pressed(p, i, c)
                )
                button.when_released = (
                    lambda p=pin, i=index, c=config: self._button_released(p, i, c)
                )

                self.gpio_buttons[switch_pin] = button

    def _button_pressed(self, pin, index, config):
        """Handle button press event"""
        self._logger.info(f"External switch pressed for GPIO{pin}")

        # Turn on the output
        if pin in self.gpio_outputs:
            self.gpio_outputs[pin].on()

        # Notify UI
        self._plugin_manager.send_plugin_message(
            __plugin_name__, {"id": index, "state": "on"}
        )

    def _button_released(self, pin, index, config):
        """Handle button release event"""
        self._logger.info(f"External switch released for GPIO{pin}")

        # Turn off the output
        if pin in self.gpio_outputs:
            self.gpio_outputs[pin].off()

        # Notify UI
        self._plugin_manager.send_plugin_message(
            __plugin_name__, {"id": index, "state": "off"}
        )

    def on_after_startup(self):
        # Configure GPIOs from settings
        self._configure_gpios(self._settings.get(["gpio_configurations"]))

    def get_api_commands(self):
        return dict(turnGpioOn=["id"], turnGpioOff=["id"], getGpioState=["id"])

    def on_api_command(self, command, data):
        if not user_permission.can():
            return flask.make_response("Insufficient rights", 403)

        self._logger.info(f"on_api_command -> data: {data}")

        config_id = int(data["id"])
        configurations = self._settings.get(["gpio_configurations"])

        if config_id >= len(configurations):
            return flask.make_response("Invalid configuration ID", 400)

        config = configurations[config_id]
        pin = int(config["pin"])

        if pin < 0 or pin not in self.gpio_outputs:
            return flask.make_response("Invalid GPIO pin", 400)

        if command == "getGpioState":
            # is_lit returns True if the LED is on (considering active_high setting)
            return flask.jsonify("on" if self.gpio_outputs[pin].is_lit else "off")

        elif command == "turnGpioOn":
            self._logger.info(f"Turned on GPIO{pin}")
            self.gpio_outputs[pin].on()

        elif command == "turnGpioOff":
            self._logger.info(f"Turned off GPIO{pin}")
            self.gpio_outputs[pin].off()

        return flask.jsonify(success=True)

    def on_api_get(self, request):
        states = []
        for config in self._settings.get(["gpio_configurations"]):
            pin = int(config["pin"])
            if pin < 0 or pin not in self.gpio_outputs:
                states.append("")
            else:
                states.append("on" if self.gpio_outputs[pin].is_lit else "off")
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


__plugin_name__ = "GPIO Control mod"
__plugin_pythoncompat__ = ">=2.7,<4"


def __plugin_load__():
    global __plugin_implementation__
    __plugin_implementation__ = GpioControlPlugin()
    global __plugin_hooks__
    __plugin_hooks__ = {
        "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information
    }
