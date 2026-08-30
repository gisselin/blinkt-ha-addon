# Pimoroni Blinkt MQTT add-on for Home Assistant OS

This repository contains a local Home Assistant OS add-on that controls a
Pimoroni Blinkt! attached to the same Raspberry Pi. It creates one master RGB
light and eight pixel RGB lights through MQTT Discovery.

## Install in Home Assistant

[![Open your Home Assistant instance and add this app repository.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fgisselin%2Fblinkt-ha-addon)

Select the button, choose your Home Assistant instance, and confirm the
repository. Then open the app/add-on store and install **Pimoroni Blinkt MQTT**.

This is a Home Assistant OS app/add-on rather than a HACS integration, so the
button adds the repository directly to Home Assistant's app store; HACS is not
required.

## Manual local installation

1. Extract this archive on a computer.
2. Copy the `blinkt` directory into `/addons/blinkt` on Home Assistant OS. The
   Samba share or the Terminal & SSH add-on can be used to reach `/addons`.
3. In Home Assistant, open **Settings > Apps > App store**, open the menu, and
   select **Check for updates** (older Home Assistant versions call these
   “Add-ons” and use **Reload**).
4. Install **Pimoroni Blinkt MQTT** from the **Local apps** section.
5. Leave `mqtt_host` set to `auto` to use the MQTT service advertised by the
   Mosquitto broker add-on. Start the add-on and enable **Start on boot** and
   **Watchdog**.
6. Ensure the Home Assistant MQTT integration is installed and MQTT Discovery
   is enabled. One device with nine light entities will appear automatically.

See `blinkt/DOCS.md` for configuration, behavior, security, and troubleshooting
details.

## Repository layout

```text
blinkt-ha-addon/
├── repository.yaml
├── README.md
├── LICENSE
├── blinkt/
│   ├── config.yaml
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── run.sh
│   ├── controller.py
│   ├── apparmor.txt
│   ├── README.md
│   ├── DOCS.md
│   ├── CHANGELOG.md
│   └── translations/en.yaml
└── tests/
    └── test_controller.py
```

## License

The add-on code is MIT licensed. Pimoroni Blinkt!, rpi-lgpio, lgpio,
paho-mqtt, and Home Assistant base images retain their own licenses.
