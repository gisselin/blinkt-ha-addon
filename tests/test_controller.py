from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


ADDON_DIR = Path(__file__).resolve().parents[1] / "blinkt"
sys.path.insert(0, str(ADDON_DIR))

from controller import (  # noqa: E402
    BlinktController,
    PixelState,
    Settings,
    detect_rpi_revision,
    parse_command,
)


class FakeHardware:
    def __init__(self) -> None:
        self.frames: list[list[PixelState]] = []
        self.cleared = False

    def render(self, pixels: list[PixelState]) -> None:
        self.frames.append(deepcopy(pixels))

    def clear(self) -> None:
        self.cleared = True


class FakePublishResult:
    rc = 0


class FakeClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, int, bool]] = []

    def publish(
        self, topic: str, payload: str, qos: int, retain: bool
    ) -> FakePublishResult:
        self.messages.append((topic, payload, qos, retain))
        return FakePublishResult()


def make_settings(data_dir: Path, restore_state: bool = True) -> Settings:
    return Settings(
        mqtt_host="broker",
        mqtt_port=1883,
        mqtt_username="",
        mqtt_password="",
        mqtt_tls=False,
        mqtt_tls_insecure=False,
        mqtt_ca_file="",
        discovery_prefix="homeassistant",
        topic_prefix="blinkt",
        device_id="blinkt_gpio",
        device_name="Pimoroni Blinkt",
        default_brightness=64,
        restore_state=restore_state,
        clear_on_stop=True,
        orientation="normal",
        gpio_chip="auto",
        data_dir=data_dir,
    )


class CommandParsingTests(unittest.TestCase):
    def test_complete_command(self) -> None:
        update = parse_command(
            b'{"state":"on","brightness":123,"color":{"r":1,"g":2,"b":3}}'
        )
        self.assertEqual(update.state, "ON")
        self.assertEqual(update.brightness, 123)
        self.assertEqual(update.color, [1, 2, 3])

    def test_invalid_brightness_is_rejected(self) -> None:
        for payload in (
            b'{"brightness":256}',
            b'{"brightness":true}',
            b'{"brightness":1.5}',
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_command(payload)

    def test_empty_or_incomplete_command_is_rejected(self) -> None:
        for payload in (b"{}", b'{"color":{"r":1,"g":2}}', b"not-json"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_command(payload)


class RaspberryPiRevisionTests(unittest.TestCase):
    def test_reads_big_endian_revision_from_mapped_device_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            revision_path = root / "system/linux,revision"
            revision_path.parent.mkdir(parents=True)
            revision_path.write_bytes(bytes.fromhex("00c04170"))

            with patch.dict("os.environ", {}, clear=True):
                revision = detect_rpi_revision(
                    device_tree_roots=(root,),
                    cpuinfo_path=root / "missing-cpuinfo",
                )

        self.assertEqual(revision, "c04170")

    def test_reads_revision_from_cpuinfo_when_device_tree_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            cpuinfo_path = root / "cpuinfo"
            cpuinfo_path.write_text("Model: Raspberry Pi\nRevision : d04170\n")

            with patch.dict("os.environ", {}, clear=True):
                revision = detect_rpi_revision(
                    device_tree_roots=(root / "missing",),
                    cpuinfo_path=cpuinfo_path,
                )

        self.assertEqual(revision, "d04170")

    def test_uses_compatibility_revision_when_metadata_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with patch.dict("os.environ", {}, clear=True):
                revision = detect_rpi_revision(
                    device_tree_roots=(root / "missing",),
                    cpuinfo_path=root / "missing-cpuinfo",
                )

        self.assertEqual(revision, "c03114")


class ControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.hardware = FakeHardware()
        self.controller = BlinktController(make_settings(self.data_dir), self.hardware)
        self.client = FakeClient()
        self.controller.client = self.client  # type: ignore[assignment]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_master_command_updates_all_pixels_and_publishes_state(self) -> None:
        accepted = self.controller.handle_command(
            "blinkt/master/set",
            b'{"state":"ON","brightness":100,"color":{"r":9,"g":8,"b":7}}',
        )
        self.assertTrue(accepted)
        self.assertTrue(all(pixel.on for pixel in self.controller.pixels))
        self.assertTrue(
            all(pixel.brightness == 100 for pixel in self.controller.pixels)
        )
        self.assertTrue(
            all(pixel.color == [9, 8, 7] for pixel in self.controller.pixels)
        )
        self.assertEqual(len(self.hardware.frames), 2)
        published_topics = [message[0] for message in self.client.messages]
        self.assertEqual(published_topics.count("blinkt/master/state"), 1)
        self.assertEqual(sum(topic.endswith("/state") for topic in published_topics), 9)

    def test_individual_command_updates_only_one_pixel(self) -> None:
        accepted = self.controller.handle_command(
            "blinkt/pixel/3/set",
            b'{"state":"ON","color":{"r":0,"g":10,"b":20}}',
        )
        self.assertTrue(accepted)
        self.assertTrue(self.controller.pixels[2].on)
        self.assertEqual(self.controller.pixels[2].color, [0, 10, 20])
        self.assertFalse(
            any(pixel.on for i, pixel in enumerate(self.controller.pixels) if i != 2)
        )
        self.assertEqual(
            [message[0] for message in self.client.messages],
            ["blinkt/pixel/3/state", "blinkt/master/state"],
        )

    def test_zero_brightness_turns_off_but_preserves_last_nonzero_level(self) -> None:
        self.controller.handle_command(
            "blinkt/pixel/1/set", b'{"state":"ON","brightness":80}'
        )
        self.controller.handle_command(
            "blinkt/pixel/1/set", b'{"state":"ON","brightness":0}'
        )
        pixel = self.controller.pixels[0]
        self.assertFalse(pixel.on)
        self.assertEqual(pixel.brightness, 80)

    def test_retained_command_is_ignored(self) -> None:
        initial_frames = len(self.hardware.frames)
        accepted = self.controller.handle_command(
            "blinkt/pixel/1/set", b'{"state":"ON"}', retained=True
        )
        self.assertFalse(accepted)
        self.assertFalse(self.controller.pixels[0].on)
        self.assertEqual(len(self.hardware.frames), initial_frames)

    def test_state_is_persisted_and_restored(self) -> None:
        self.controller.handle_command(
            "blinkt/pixel/8/set",
            b'{"state":"ON","brightness":77,"color":{"r":4,"g":5,"b":6}}',
        )
        saved = json.loads((self.data_dir / "state.json").read_text())
        self.assertEqual(saved["version"], 1)

        restored_hardware = FakeHardware()
        restored = BlinktController(make_settings(self.data_dir), restored_hardware)
        self.assertEqual(restored.pixels[7], PixelState(True, 77, [4, 5, 6]))
        self.assertEqual(len(restored_hardware.frames), 1)

    def test_corrupt_state_falls_back_to_defaults(self) -> None:
        (self.data_dir / "state.json").write_text("broken", encoding="utf-8")
        controller = BlinktController(make_settings(self.data_dir), FakeHardware())
        self.assertTrue(all(not pixel.on for pixel in controller.pixels))
        self.assertTrue(all(pixel.brightness == 64 for pixel in controller.pixels))

    def test_discovery_exposes_master_and_eight_pixels(self) -> None:
        messages = self.controller.discovery_messages()
        self.assertEqual(len(messages), 9)
        topics = [topic for topic, _payload in messages]
        self.assertEqual(len(set(topics)), 9)
        unique_ids = [payload["unique_id"] for _topic, payload in messages]
        self.assertEqual(len(set(unique_ids)), 9)
        for _topic, payload in messages:
            self.assertEqual(payload["schema"], "json")
            self.assertEqual(payload["supported_color_modes"], ["rgb"])
            self.assertEqual(payload["availability_topic"], "blinkt/availability")
            self.assertEqual(payload["device"]["identifiers"], ["blinkt_gpio"])

    def test_master_state_summarizes_active_pixels(self) -> None:
        self.controller.pixels[0] = PixelState(True, 100, [255, 0, 0])
        self.controller.pixels[1] = PixelState(True, 200, [0, 0, 255])
        payload = self.controller._master_payload()
        self.assertEqual(payload["state"], "ON")
        self.assertEqual(payload["brightness"], 150)
        self.assertEqual(payload["color"], {"r": 128, "g": 0, "b": 128})


if __name__ == "__main__":
    unittest.main()
