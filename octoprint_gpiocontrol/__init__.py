# coding=utf-8
from __future__ import absolute_import, print_function
from octoprint.server import user_permission

import octoprint.plugin
import flask
from gpiozero import LED, Button
import threading
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
        self.button_poll_thread = None
        self.button_poll_active = False
        self.button_states = {}  # Track button states for polling

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
        # Stop button polling thread if active
        self.stop_button_polling()

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
        has_buttons = False
        self.button_states = {}  # Reset button states

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
                self._logger.info(
                    f"Configuring external switch on GPIO{switch_pin} as {external_switch}"
                )

                # Set up pull_up based on switch type
                pull_up = external_switch == "normally_open"

                # Create Button object for input detection
                button = Button(switch_pin, pull_up=pull_up, bounce_time=0.01)
                self.gpio_buttons[switch_pin] = button

                # For normally open: Active when button is pressed (is_pressed = True)
                # For normally closed: Active when button is NOT pressed (is_pressed = False)
                active_state = True if external_switch == "normally_open" else False

                # Store button configuration for polling
                self.button_states[switch_pin] = {
                    "pin": pin,
                    "index": index,
                    "config": config,
                    "last_state": button.is_pressed,
                    "active_state": active_state,
                    "switch_type": external_switch,
                    "active": False,  # Track if the switch is currently active (output is on)
                }

                has_buttons = True

                # Log the initial button state
                self._logger.info(
                    f"Button {switch_pin} initial state: {button.is_pressed}, "
                    + f"active when: {active_state}, type: {external_switch}"
                )

        # Start polling thread if we have buttons
        if has_buttons:
            self.start_button_polling()

    def start_button_polling(self):
        """Start a thread to poll button states continuously"""
        if self.button_poll_thread is not None and self.button_poll_thread.is_alive():
            return

        self.button_poll_active = True
        self.button_poll_thread = threading.Thread(target=self._poll_buttons)
        self.button_poll_thread.daemon = True
        self.button_poll_thread.start()
        self._logger.info("Button polling thread started")

    def stop_button_polling(self):
        """Stop the button polling thread"""
        if self.button_poll_thread is not None:
            self.button_poll_active = False
            self.button_poll_thread.join(timeout=1.0)
            self.button_poll_thread = None
            self._logger.info("Button polling thread stopped")

    def _poll_buttons(self):
        """Poll button states at a high frequency to detect changes quickly"""
        self._logger.info("Button polling thread running")

        while self.button_poll_active:
            for switch_pin, button_data in self.button_states.items():
                if switch_pin not in self.gpio_buttons:
                    continue

                button = self.gpio_buttons[switch_pin]
                current_state = button.is_pressed
                last_state = button_data["last_state"]

                # Check if physical state changed
                if current_state != last_state:
                    # Update stored state
                    self.button_states[switch_pin]["last_state"] = current_state

                    # Debug log with detailed info
                    self._logger.debug(
                        f"Button {switch_pin} state changed: {last_state} -> {current_state}, "
                        + f"type: {button_data['switch_type']}, active_state: {button_data['active_state']}"
                    )

                    # Check if the button is now in its active state
                    is_active = current_state == button_data["active_state"]

                    # Only trigger events if active status changed
                    if is_active != button_data["active"]:
                        self.button_states[switch_pin]["active"] = is_active

                        if is_active:
                            self._logger.info(
                                f"Button {switch_pin} ({button_data['switch_type']}) activated"
                            )
                            self._button_pressed(
                                button_data["pin"],
                                button_data["index"],
                                button_data["config"],
                            )
                        else:
                            self._logger.info(
                                f"Button {switch_pin} ({button_data['switch_type']}) deactivated"
                            )
                            self._button_released(
                                button_data["pin"],
                                button_data["index"],
                                button_data["config"],
                            )

            # Poll at 20ms interval (50Hz) for responsiveness
            sleep(0.02)

    def _button_pressed(self, pin, index, config):
        """Handle button press event"""
        self._logger.info(f"External switch activated for GPIO{pin}")

        # Turn on the output
        if pin in self.gpio_outputs:
            self.gpio_outputs[pin].on()

        # Notify UI
        self._plugin_manager.send_plugin_message(
            __plugin_name__, {"id": index, "state": "on"}
        )

    def _button_released(self, pin, index, config):
        """Handle button release event"""
        self._logger.info(f"External switch deactivated for GPIO{pin}")

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
