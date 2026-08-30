# Pimoroni Blinkt MQTT documentation

## What this app does

This Home Assistant OS app (formerly called an add-on) drives a Pimoroni
Blinkt! plugged into the GPIO header of the same Raspberry Pi. It publishes
Home Assistant MQTT Discovery for:

- **Pimoroni Blinkt** — master control for all eight pixels
- **Pixel 1** through **Pixel 8** — individual RGB controls

All nine lights support on/off, 0–255 Home Assistant brightness, and RGB color.
The Blinkt hardware has 32 physical brightness steps, so brightness is
necessarily quantized by Pimoroni's library.

## Requirements

- Home Assistant OS on a supported 64-bit Raspberry Pi 3, 4, or 5
- A Pimoroni Blinkt! seated correctly on the 40-pin GPIO header
- An MQTT broker and the Home Assistant MQTT integration with Discovery enabled

The official Mosquitto broker app is the simplest broker choice. With
`mqtt_host: auto`, this app requests the broker address and its generated
app credentials from Home Assistant Supervisor; you do not need to copy
credentials into the configuration.

## Local installation

1. Extract the downloaded project.
2. Copy the project’s `blinkt` directory to `/addons/blinkt` on Home Assistant
   OS. Use the Samba share or Terminal & SSH app if necessary.
3. Open **Settings > Apps > App store**. From the menu, select **Check for
   updates**. On older Home Assistant releases the wording is **Add-ons** and
   **Reload**.
4. Find **Pimoroni Blinkt MQTT** under **Local apps** and select **Install**.
   The first local build can take several minutes because the Pi-compatible
   GPIO package contains a small native component.
5. Review the configuration, then start the app. Enable **Start on boot** and
   **Watchdog**.
6. Open **Settings > Devices & services > MQTT**. Confirm that MQTT Discovery
   is enabled. The device and its nine entities should appear shortly.

No change to Home Assistant `configuration.yaml` is needed.

## Configuration

### MQTT

| Option | Default | Meaning |
|---|---:|---|
| `mqtt_host` | `auto` | Use Supervisor’s MQTT service, or enter a broker DNS name/IP. |
| `mqtt_port` | `1883` | Port for a manually configured broker. Ignored in auto mode. |
| `mqtt_username` | empty | Username for a manual broker. Ignored in auto mode. |
| `mqtt_password` | empty | Password for a manual broker. Ignored in auto mode. |
| `mqtt_tls` | `false` | Use TLS with a manual broker. Auto mode follows the advertised service. |
| `mqtt_tls_insecure` | `false` | Disable certificate verification. This is unsafe and intended only for temporary diagnosis. |
| `mqtt_ca_file` | empty | Optional CA filename in Home Assistant’s `/ssl` directory. |
| `discovery_prefix` | `homeassistant` | Must match the prefix configured in the Home Assistant MQTT integration. |
| `topic_prefix` | `blinkt` | Root for command, state, and availability topics. |

When `mqtt_host` is not `auto`, all manual MQTT fields are used. A CA file may
be entered as a filename such as `my-ca.pem` (resolved as `/ssl/my-ca.pem`) or
as an absolute path inside the app.

### Device and GPIO

| Option | Default | Meaning |
|---|---:|---|
| `device_id` | `blinkt_gpio` | Stable MQTT unique ID. Do not change it after entities are created unless you intend to create a new device. |
| `device_name` | `Pimoroni Blinkt` | Device name shown in Home Assistant. |
| `default_brightness` | `64` | Initial brightness, 1–255, when no saved state exists. |
| `restore_state` | `true` | Restore `/data/state.json` and render it during startup. |
| `clear_on_stop` | `true` | Turn the physical pixels off on a clean app stop. The saved logical state is retained for the next start. |
| `orientation` | `normal` | Set `reversed` to swap logical Pixel 1 and Pixel 8. |
| `gpio_chip` | `auto` | Detect the GPIO character device. Set a numeric suffix only when troubleshooting, for example `0` for `/dev/gpiochip0`. |
| `log_level` | `info` | `debug`, `info`, `warning`, or `error`. |

## Entity behavior

- A master color or brightness command applies to every pixel.
- Master **On** turns all pixels on using their stored colors and brightnesses;
  master **Off** turns all pixels off without forgetting those values.
- Pixel commands affect only that pixel.
- A brightness of zero is treated as **Off**, while the last nonzero brightness
  is preserved for the next **On** command.
- The master entity reports **On** when any pixel is on. Its displayed color
  and brightness are the averages of the active pixels. When all are off, the
  averages use the remembered pixel values.
- If master and pixel commands are mixed, the most recent command wins for the
  affected pixels.

Commands, states, and discovery use these topics by default:

```text
blinkt/availability
blinkt/master/set
blinkt/master/state
blinkt/pixel/1/set
blinkt/pixel/1/state
...
homeassistant/light/blinkt_gpio/master/config
homeassistant/light/blinkt_gpio/pixel_1/config
...
```

Command payloads follow Home Assistant’s MQTT JSON light schema, for example:

```json
{"state":"ON","brightness":128,"color":{"r":255,"g":80,"b":0}}
```

Discovery and state messages are published at QoS 1 with retain enabled. The
availability topic uses a retained online status plus an MQTT Last Will of
`offline`. Retained command messages are deliberately ignored to prevent an
old command from unexpectedly changing the lights after a restart.

## Persistence and restart behavior

Accepted commands are rendered first and then written atomically to
`/data/state.json`, which is part of the app’s persistent data and Home
Assistant backups. On startup the saved state is validated before use. Invalid
or incompatible data is ignored safely.

After connecting or reconnecting to MQTT, the app re-publishes all discovery
and state messages. It also listens for Home Assistant’s
`homeassistant/status = online` announcement and re-publishes after a Home
Assistant restart.

## GPIO access and security

Pimoroni’s `blinkt` Python package directly controls BCM GPIO 23 (data,
physical pin 16) and BCM GPIO 24 (clock, physical pin 18). This app uses that
package unchanged for the Blinkt protocol. Its `RPi.GPIO` import is supplied by
the `rpi-lgpio` compatibility package, which supports Raspberry Pi 5 and uses
Linux GPIO character devices.

The app requests `gpio: true` and maps the possible Raspberry Pi
`/dev/gpiochip0` through `/dev/gpiochip5` device nodes. It includes a custom
AppArmor profile and remains in Home Assistant protection mode. It does **not**
request `/dev/mem`, `SYS_RAWIO`, host networking, or full hardware access.

GPIO 23 and 24 must not be claimed by another process, app, overlay, or HAT.
The GPIO character-device API intentionally gives one process exclusive
ownership while the app runs.

## Hardware and Pi caveats

- The packaged local app targets the current Home Assistant `aarch64`
  architecture and the `raspberrypi3-64`, `raspberrypi4-64`, and
  `raspberrypi5-64` machine types. Old 32-bit HA OS installations are not in
  scope; migrate them to the supported 64-bit image first.
- Pi 5 requires a GPIO character-device backend; the original `RPi.GPIO`
  package used by the old Blinkt release does not support Pi 5 directly. That
  is why this app installs `rpi-lgpio` as the compatible backend.
- Current Pi kernels normally expose the header GPIO controller as
  `/dev/gpiochip0`; some older Pi 5 kernels used `/dev/gpiochip4`. Automatic
  detection examines the kernel’s GPIO label and supports both layouts.
- The Blinkt keeps its last latched output if a process is killed before it can
  clear the bar. Supervisor’s watchdog restart will render the saved logical
  state again. A clean stop honors `clear_on_stop`.
- Blinkt LEDs are bright. The default brightness is intentionally 64/255.

## Troubleshooting

### “No MQTT service is available”

Start the Mosquitto broker app and wait for it to finish initialization, or set
`mqtt_host`, port, and credentials for another broker. Also add/configure the
Home Assistant MQTT integration; running the broker alone is not enough for
entities to appear.

### The app connects, but no entities appear

Confirm MQTT Discovery is enabled and that `discovery_prefix` matches the MQTT
integration. Restart the app to force a complete re-publication. Do not reuse
the same `device_id` for another physical device.

### “can not open gpiochip” or no LEDs change

1. Confirm the app log reports a real `/dev/gpiochipN` path.
2. Stop other GPIO apps that might own BCM 23 or 24.
3. Try `gpio_chip: "0"`; on an older Pi 5 kernel try `"4"`.
4. Confirm the Blinkt is aligned with pin 1 and is not offset on the header.
5. Keep protection mode enabled; the supplied device mappings and AppArmor
   profile are intended to work without full access.

### Pixel numbering is backwards

Set `orientation: reversed` and restart the app.

