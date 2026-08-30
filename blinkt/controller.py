#!/usr/bin/env python3
"""Pimoroni Blinkt to Home Assistant MQTT bridge."""

from __future__ import annotations

import json
import logging
import os
import signal
import ssl
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import paho.mqtt.client as mqtt


APP_VERSION = "1.0.1"
PIXEL_COUNT = 8
STATE_VERSION = 1
LOGGER = logging.getLogger("blinkt_mqtt")
RPI_REVISION_FALLBACK = "c03114"


def env_bool(name: str, default: bool = False) -> bool:
    """Read a conventional boolean environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def normalize_topic(value: str, field: str) -> str:
    """Return a safe MQTT topic prefix without leading/trailing separators."""
    normalized = value.strip().strip("/")
    if not normalized or any(token in normalized for token in ("#", "+", "\0")):
        raise ValueError(f"{field} is not a valid MQTT topic prefix")
    return normalized


@dataclass(frozen=True)
class Settings:
    """Runtime configuration populated by run.sh."""

    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_tls: bool
    mqtt_tls_insecure: bool
    mqtt_ca_file: str
    discovery_prefix: str
    topic_prefix: str
    device_id: str
    device_name: str
    default_brightness: int
    restore_state: bool
    clear_on_stop: bool
    orientation: str
    gpio_chip: str
    data_dir: Path

    @classmethod
    def from_environment(cls) -> "Settings":
        mqtt_host = os.getenv("BLINKT_MQTT_HOST", "").strip()
        if not mqtt_host:
            raise ValueError("BLINKT_MQTT_HOST is empty")

        mqtt_port = int(os.getenv("BLINKT_MQTT_PORT", "1883"))
        if not 1 <= mqtt_port <= 65535:
            raise ValueError("BLINKT_MQTT_PORT must be between 1 and 65535")

        default_brightness = int(os.getenv("BLINKT_DEFAULT_BRIGHTNESS", "64"))
        if not 1 <= default_brightness <= 255:
            raise ValueError("BLINKT_DEFAULT_BRIGHTNESS must be between 1 and 255")

        orientation = os.getenv("BLINKT_ORIENTATION", "normal").strip().lower()
        if orientation not in {"normal", "reversed"}:
            raise ValueError("BLINKT_ORIENTATION must be normal or reversed")

        gpio_chip = os.getenv("BLINKT_GPIO_CHIP", "auto").strip().lower()
        if gpio_chip != "auto" and not gpio_chip.isdigit():
            raise ValueError("BLINKT_GPIO_CHIP must be auto or a non-negative integer")

        device_id = os.getenv("BLINKT_DEVICE_ID", "blinkt_gpio").strip()
        if not device_id or not all(c.isalnum() or c in "_-" for c in device_id):
            raise ValueError("BLINKT_DEVICE_ID contains unsupported characters")

        device_name = os.getenv("BLINKT_DEVICE_NAME", "Pimoroni Blinkt").strip()
        if not device_name:
            raise ValueError("BLINKT_DEVICE_NAME is empty")

        return cls(
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_username=os.getenv("BLINKT_MQTT_USERNAME", ""),
            mqtt_password=os.getenv("BLINKT_MQTT_PASSWORD", ""),
            mqtt_tls=env_bool("BLINKT_MQTT_TLS"),
            mqtt_tls_insecure=env_bool("BLINKT_MQTT_TLS_INSECURE"),
            mqtt_ca_file=os.getenv("BLINKT_MQTT_CA_FILE", "").strip(),
            discovery_prefix=normalize_topic(
                os.getenv("BLINKT_DISCOVERY_PREFIX", "homeassistant"),
                "BLINKT_DISCOVERY_PREFIX",
            ),
            topic_prefix=normalize_topic(
                os.getenv("BLINKT_TOPIC_PREFIX", "blinkt"),
                "BLINKT_TOPIC_PREFIX",
            ),
            device_id=device_id,
            device_name=device_name,
            default_brightness=default_brightness,
            restore_state=env_bool("BLINKT_RESTORE_STATE", True),
            clear_on_stop=env_bool("BLINKT_CLEAR_ON_STOP", True),
            orientation=orientation,
            gpio_chip=gpio_chip,
            data_dir=Path(os.getenv("BLINKT_DATA_DIR", "/data")),
        )


@dataclass
class PixelState:
    """Logical state of one pixel."""

    on: bool
    brightness: int
    color: list[int]

    @classmethod
    def default(cls, brightness: int) -> "PixelState":
        return cls(on=False, brightness=brightness, color=[255, 255, 255])

    @classmethod
    def from_dict(cls, value: Any) -> "PixelState":
        if not isinstance(value, dict):
            raise ValueError("pixel state is not an object")
        on = value.get("on")
        brightness = value.get("brightness")
        color = value.get("color")
        if not isinstance(on, bool):
            raise ValueError("pixel on state is not boolean")
        if not _is_integer(brightness) or not 1 <= brightness <= 255:
            raise ValueError("pixel brightness is outside 1..255")
        if (
            not isinstance(color, list)
            or len(color) != 3
            or any(
                not _is_integer(channel) or not 0 <= channel <= 255 for channel in color
            )
        ):
            raise ValueError("pixel color is invalid")
        return cls(on=on, brightness=brightness, color=list(color))


class Hardware(Protocol):
    def render(self, pixels: list[PixelState]) -> None:
        """Render all logical pixels."""

    def clear(self) -> None:
        """Turn every physical pixel off."""


def detect_rpi_revision(
    device_tree_roots: tuple[Path, ...] | None = None,
    cpuinfo_path: Path = Path("/proc/cpuinfo"),
) -> str:
    """Find the Raspberry Pi revision and format it for rpi-lgpio."""
    configured = os.getenv("RPI_LGPIO_REVISION", "").strip().lower()
    if configured:
        try:
            int(configured, 16)
        except ValueError as exc:
            raise RuntimeError("RPI_LGPIO_REVISION is not hexadecimal") from exc
        return configured.removeprefix("0x")

    roots = device_tree_roots or (
        Path("/device-tree"),
        Path("/proc/device-tree"),
        Path("/sys/firmware/devicetree/base"),
    )
    for root in roots:
        revision_path = root / "system/linux,revision"
        try:
            raw_revision = revision_path.read_bytes()
        except OSError:
            continue
        if len(raw_revision) >= 4:
            revision = int.from_bytes(raw_revision[-4:], byteorder="big")
            if revision:
                return f"{revision:06x}"

    try:
        for line in cpuinfo_path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip().lower() == "revision":
                revision = value.strip().lower().removeprefix("0x")
                int(revision, 16)
                return revision
    except (OSError, ValueError):
        pass

    # The add-on is machine-restricted to 64-bit Raspberry Pi 3/4/5 systems.
    # Blinkt uses BCM numbering and RPI_LGPIO_CHIP is selected separately, so a
    # modern 40-pin Pi revision is a safe compatibility fallback if an older
    # Supervisor does not provide the requested device-tree mount.
    LOGGER.warning(
        "Could not read the Raspberry Pi revision from the mapped device tree; "
        "using compatibility revision %s",
        RPI_REVISION_FALLBACK,
    )
    return RPI_REVISION_FALLBACK


class BlinktHardware:
    """Pimoroni Blinkt hardware adapter using the rpi-lgpio RPi.GPIO shim."""

    def __init__(self, orientation: str, requested_gpio_chip: str) -> None:
        chip = self._select_gpio_chip(requested_gpio_chip)
        os.environ["RPI_LGPIO_CHIP"] = str(chip)
        revision = detect_rpi_revision()
        os.environ["RPI_LGPIO_REVISION"] = revision
        LOGGER.info("Using /dev/gpiochip%s for BCM GPIO 23/24", chip)
        LOGGER.info("Using Raspberry Pi revision %s for rpi-lgpio", revision)

        try:
            # Both rpi-lgpio environment values must be set before this import.
            import blinkt
        except Exception as exc:
            raise RuntimeError(
                f"Unable to import the Pimoroni Blinkt library: {exc}"
            ) from exc

        self._blinkt = blinkt
        self._reversed = orientation == "reversed"
        self._blinkt.set_clear_on_exit(False)

    @staticmethod
    def _select_gpio_chip(requested: str) -> int:
        if requested != "auto":
            chip = int(requested)
            if not Path(f"/dev/gpiochip{chip}").exists():
                raise RuntimeError(f"Configured /dev/gpiochip{chip} does not exist")
            return chip

        preferred_labels = {
            "pinctrl-rp1",
            "pinctrl-bcm2835",
            "pinctrl-bcm2711",
            "raspberrypi,rp1-gpio",
        }
        for sysfs_path in sorted(Path("/sys/class/gpio").glob("gpiochip*")):
            try:
                label = (sysfs_path / "label").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if label in preferred_labels:
                suffix = sysfs_path.name.removeprefix("gpiochip")
                device = Path(f"/dev/gpiochip{suffix}")
                if suffix.isdigit() and device.exists():
                    LOGGER.debug("Matched GPIO label %s at %s", label, device)
                    return int(suffix)

        # Current Pi 3/4 and current Pi 5 kernels expose header GPIO as chip 0;
        # older Pi 5 kernels used chip 4.
        for chip in (0, 4):
            if Path(f"/dev/gpiochip{chip}").exists():
                LOGGER.warning(
                    "Could not identify the header GPIO label; falling back to /dev/gpiochip%s",
                    chip,
                )
                return chip
        raise RuntimeError(
            "No supported GPIO character device was found. Check the add-on GPIO/device mappings."
        )

    def render(self, pixels: list[PixelState]) -> None:
        for logical_index, pixel in enumerate(pixels):
            physical_index = (
                PIXEL_COUNT - 1 - logical_index if self._reversed else logical_index
            )
            if pixel.on:
                r, g, b = pixel.color
                self._blinkt.set_pixel(
                    physical_index,
                    r,
                    g,
                    b,
                    brightness=pixel.brightness / 255.0,
                )
            else:
                self._blinkt.set_pixel(physical_index, 0, 0, 0, brightness=0.0)
        self._blinkt.show()

    def clear(self) -> None:
        self._blinkt.clear()
        self._blinkt.show()


@dataclass(frozen=True)
class LightUpdate:
    state: str | None
    brightness: int | None
    color: list[int] | None


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _bounded_integer(value: Any, field: str) -> int:
    if not _is_integer(value) or not 0 <= value <= 255:
        raise ValueError(f"{field} must be an integer from 0 to 255")
    return value


def parse_command(payload: bytes) -> LightUpdate:
    """Parse and validate a Home Assistant MQTT JSON light command."""
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"command is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("command must be a JSON object")

    state: str | None = None
    if "state" in value:
        if not isinstance(value["state"], str):
            raise ValueError("state must be ON or OFF")
        state = value["state"].upper()
        if state not in {"ON", "OFF"}:
            raise ValueError("state must be ON or OFF")

    brightness = None
    if "brightness" in value:
        brightness = _bounded_integer(value["brightness"], "brightness")

    color = None
    if "color" in value:
        raw_color = value["color"]
        if not isinstance(raw_color, dict):
            raise ValueError("color must be an object with r, g, and b")
        if not all(channel in raw_color for channel in ("r", "g", "b")):
            raise ValueError("color must contain r, g, and b")
        color = [
            _bounded_integer(raw_color[channel], f"color.{channel}")
            for channel in ("r", "g", "b")
        ]

    if state is None and brightness is None and color is None:
        raise ValueError("command does not contain state, brightness, or color")
    return LightUpdate(state=state, brightness=brightness, color=color)


class BlinktController:
    """Own state, render hardware, and implement MQTT entity behavior."""

    def __init__(self, settings: Settings, hardware: Hardware) -> None:
        self.settings = settings
        self.hardware = hardware
        self.state_path = settings.data_dir / "state.json"
        self.pixels = [
            PixelState.default(settings.default_brightness) for _ in range(PIXEL_COUNT)
        ]
        self.client: mqtt.Client | None = None
        self._lock = threading.RLock()
        self._load_state()
        self.hardware.render(self.pixels)

    @property
    def availability_topic(self) -> str:
        return f"{self.settings.topic_prefix}/availability"

    @property
    def master_command_topic(self) -> str:
        return f"{self.settings.topic_prefix}/master/set"

    @property
    def master_state_topic(self) -> str:
        return f"{self.settings.topic_prefix}/master/state"

    def pixel_command_topic(self, index: int) -> str:
        return f"{self.settings.topic_prefix}/pixel/{index + 1}/set"

    def pixel_state_topic(self, index: int) -> str:
        return f"{self.settings.topic_prefix}/pixel/{index + 1}/state"

    def _load_state(self) -> None:
        if not self.settings.restore_state or not self.state_path.exists():
            return
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("version") != STATE_VERSION:
                raise ValueError("unsupported state version")
            raw_pixels = value.get("pixels")
            if not isinstance(raw_pixels, list) or len(raw_pixels) != PIXEL_COUNT:
                raise ValueError("state does not contain exactly eight pixels")
            self.pixels = [PixelState.from_dict(pixel) for pixel in raw_pixels]
            LOGGER.info("Restored Blinkt state from %s", self.state_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.warning(
                "Ignoring invalid saved state in %s: %s", self.state_path, exc
            )

    def _save_state(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = self.state_path.with_suffix(".json.tmp")
        value = {
            "version": STATE_VERSION,
            "pixels": [asdict(pixel) for pixel in self.pixels],
        }
        try:
            with temporary_path.open("w", encoding="utf-8") as state_file:
                os.chmod(temporary_path, 0o600)
                json.dump(value, state_file, separators=(",", ":"), sort_keys=True)
                state_file.write("\n")
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary_path, self.state_path)
        except OSError:
            LOGGER.exception("Unable to persist state to %s", self.state_path)
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _apply_update(pixel: PixelState, update: LightUpdate) -> None:
        if update.color is not None:
            pixel.color = list(update.color)
        if update.brightness is not None and update.brightness > 0:
            pixel.brightness = update.brightness

        if update.state == "OFF" or update.brightness == 0:
            pixel.on = False
        elif update.state == "ON":
            pixel.on = True

    def handle_command(
        self, topic: str, payload: bytes, retained: bool = False
    ) -> bool:
        """Handle one MQTT command; return True only when accepted."""
        if retained:
            LOGGER.warning(
                "Ignored retained command on %s to prevent stale actions", topic
            )
            return False
        try:
            update = parse_command(payload)
        except ValueError as exc:
            LOGGER.warning("Ignored invalid command on %s: %s", topic, exc)
            return False

        with self._lock:
            if topic == self.master_command_topic:
                affected = list(range(PIXEL_COUNT))
            else:
                affected = [
                    index
                    for index in range(PIXEL_COUNT)
                    if topic == self.pixel_command_topic(index)
                ]
                if not affected:
                    LOGGER.debug("Ignored message on unrecognized topic %s", topic)
                    return False

            for index in affected:
                self._apply_update(self.pixels[index], update)

            try:
                self.hardware.render(self.pixels)
            except Exception:
                LOGGER.exception(
                    "GPIO render failed; stopping so Supervisor can restart the app"
                )
                raise

            self._save_state()
            for index in affected:
                self.publish_pixel_state(index)
            self.publish_master_state()
            return True

    @staticmethod
    def _pixel_payload(pixel: PixelState) -> dict[str, Any]:
        return {
            "state": "ON" if pixel.on else "OFF",
            "brightness": pixel.brightness,
            "color_mode": "rgb",
            "color": {
                "r": pixel.color[0],
                "g": pixel.color[1],
                "b": pixel.color[2],
            },
        }

    def _master_payload(self) -> dict[str, Any]:
        active = [pixel for pixel in self.pixels if pixel.on]
        source = active if active else self.pixels
        count = len(source)
        brightness = round(sum(pixel.brightness for pixel in source) / count)
        color = [
            round(sum(pixel.color[channel] for pixel in source) / count)
            for channel in range(3)
        ]
        return {
            "state": "ON" if active else "OFF",
            "brightness": brightness,
            "color_mode": "rgb",
            "color": {"r": color[0], "g": color[1], "b": color[2]},
        }

    def _publish_json(
        self, topic: str, value: dict[str, Any], retain: bool = True
    ) -> None:
        if self.client is None:
            return
        info = self.client.publish(
            topic,
            json.dumps(value, separators=(",", ":"), sort_keys=True),
            qos=1,
            retain=retain,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.warning(
                "MQTT publish to %s was not queued: %s",
                topic,
                mqtt.error_string(info.rc),
            )

    def publish_pixel_state(self, index: int) -> None:
        self._publish_json(
            self.pixel_state_topic(index), self._pixel_payload(self.pixels[index])
        )

    def publish_master_state(self) -> None:
        self._publish_json(self.master_state_topic, self._master_payload())

    def publish_all_states(self) -> None:
        with self._lock:
            for index in range(PIXEL_COUNT):
                self.publish_pixel_state(index)
            self.publish_master_state()

    def _device_discovery(self) -> dict[str, Any]:
        return {
            "identifiers": [self.settings.device_id],
            "name": self.settings.device_name,
            "manufacturer": "Pimoroni",
            "model": "Blinkt!",
            "sw_version": APP_VERSION,
        }

    def _light_discovery(
        self,
        *,
        unique_suffix: str,
        name: str | None,
        command_topic: str,
        state_topic: str,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "unique_id": f"{self.settings.device_id}_{unique_suffix}",
            "object_id": f"{self.settings.device_id}_{unique_suffix}",
            "schema": "json",
            "command_topic": command_topic,
            "state_topic": state_topic,
            "availability_topic": self.availability_topic,
            "payload_available": "online",
            "payload_not_available": "offline",
            "brightness": True,
            "supported_color_modes": ["rgb"],
            "qos": 1,
            "icon": "mdi:led-strip-variant",
            "device": self._device_discovery(),
            "origin": {
                "name": "Pimoroni Blinkt MQTT add-on",
                "sw_version": APP_VERSION,
            },
        }

    def discovery_messages(self) -> list[tuple[str, dict[str, Any]]]:
        messages = [
            (
                f"{self.settings.discovery_prefix}/light/{self.settings.device_id}/master/config",
                self._light_discovery(
                    unique_suffix="master",
                    name=None,
                    command_topic=self.master_command_topic,
                    state_topic=self.master_state_topic,
                ),
            )
        ]
        for index in range(PIXEL_COUNT):
            messages.append(
                (
                    f"{self.settings.discovery_prefix}/light/{self.settings.device_id}/pixel_{index + 1}/config",
                    self._light_discovery(
                        unique_suffix=f"pixel_{index + 1}",
                        name=f"Pixel {index + 1}",
                        command_topic=self.pixel_command_topic(index),
                        state_topic=self.pixel_state_topic(index),
                    ),
                )
            )
        return messages

    def publish_discovery(self) -> None:
        for topic, payload in self.discovery_messages():
            self._publish_json(topic, payload)

    def command_subscriptions(self) -> list[tuple[str, int]]:
        return [(self.master_command_topic, 1)] + [
            (self.pixel_command_topic(index), 1) for index in range(PIXEL_COUNT)
        ]


class Application:
    """Configure MQTT callbacks and manage clean shutdown."""

    def __init__(self, settings: Settings, controller: BlinktController) -> None:
        self.settings = settings
        self.controller = controller
        self.stopping = False
        self.connected = False

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"{settings.device_id}_controller",
            protocol=mqtt.MQTTv311,
        )
        client.enable_logger(LOGGER)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.will_set(controller.availability_topic, "offline", qos=1, retain=True)
        if settings.mqtt_username:
            client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        if settings.mqtt_tls:
            client.tls_set(
                ca_certs=settings.mqtt_ca_file or None,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
            client.tls_insecure_set(settings.mqtt_tls_insecure)

        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message
        self.client = client
        self.controller.client = client

    def on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        del userdata, flags, properties
        if reason_code.is_failure:
            LOGGER.error("MQTT connection rejected: %s", reason_code)
            return
        self.connected = True
        LOGGER.info(
            "Connected to MQTT broker at %s:%s",
            self.settings.mqtt_host,
            self.settings.mqtt_port,
        )
        subscriptions = self.controller.command_subscriptions()
        subscriptions.append(("homeassistant/status", 1))
        result, _mid = client.subscribe(subscriptions)
        if result != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.error(
                "Unable to subscribe to MQTT command topics: %s",
                mqtt.error_string(result),
            )
            client.disconnect()
            return
        self.controller.publish_discovery()
        self.controller.publish_all_states()
        client.publish(self.controller.availability_topic, "online", qos=1, retain=True)

    def on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        disconnect_flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        del client, userdata, disconnect_flags, properties
        self.connected = False
        if not self.stopping:
            LOGGER.warning(
                "Disconnected from MQTT (%s); reconnecting automatically", reason_code
            )

    def on_message(
        self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage
    ) -> None:
        del client, userdata
        if message.topic == "homeassistant/status":
            if (
                message.payload.decode("utf-8", errors="ignore").strip().lower()
                == "online"
            ):
                LOGGER.info(
                    "Home Assistant is online; re-publishing discovery and state"
                )
                self.controller.publish_discovery()
                self.controller.publish_all_states()
            return
        try:
            self.controller.handle_command(
                message.topic, message.payload, message.retain
            )
        except Exception:
            self.shutdown(clear=False)
            raise

    def shutdown(self, clear: bool | None = None) -> None:
        if self.stopping:
            return
        self.stopping = True
        LOGGER.info("Stopping Pimoroni Blinkt MQTT controller")
        if self.connected:
            try:
                info = self.client.publish(
                    self.controller.availability_topic,
                    "offline",
                    qos=1,
                    retain=True,
                )
                info.wait_for_publish(timeout=2.0)
            except (RuntimeError, ValueError):
                LOGGER.warning("Could not confirm the final MQTT offline message")
        self.client.disconnect()
        should_clear = self.settings.clear_on_stop if clear is None else clear
        if should_clear:
            try:
                self.controller.hardware.clear()
            except Exception:
                LOGGER.exception("Could not clear Blinkt during shutdown")

    def run(self) -> None:
        def signal_handler(signum: int, frame: Any) -> None:
            del frame
            LOGGER.debug("Received signal %s", signum)
            self.shutdown()

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        LOGGER.info(
            "Connecting to MQTT at %s:%s (TLS: %s)",
            self.settings.mqtt_host,
            self.settings.mqtt_port,
            self.settings.mqtt_tls,
        )
        self.client.connect_async(
            self.settings.mqtt_host,
            self.settings.mqtt_port,
            keepalive=60,
        )
        try:
            self.client.loop_forever(retry_first_connection=True)
        finally:
            self.shutdown()


def configure_logging() -> None:
    level_name = os.getenv("BLINKT_LOG_LEVEL", "info").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> int:
    configure_logging()
    try:
        settings = Settings.from_environment()
        hardware = BlinktHardware(settings.orientation, settings.gpio_chip)
        controller = BlinktController(settings, hardware)
        Application(settings, controller).run()
    except (OSError, RuntimeError, ValueError):
        LOGGER.exception("Fatal startup/runtime error")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
