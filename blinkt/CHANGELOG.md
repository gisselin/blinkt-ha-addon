# Changelog

## 1.0.1 - 2026-08-30

- Map the Raspberry Pi device tree into the app.
- Detect and explicitly pass the board revision to rpi-lgpio before importing
  Pimoroni's Blinkt library.
- Add a guarded modern-Pi compatibility revision for older Supervisor versions
  that do not expose the requested device-tree property.

## 1.0.0 - 2026-08-30

- Initial production release.
- Add one master and eight pixel RGB lights through MQTT Discovery.
- Add retained state, persistent restore, reconnect handling, and availability.
- Use Pimoroni's Blinkt library with the Pi 5-compatible rpi-lgpio backend.
- Add automatic Supervisor MQTT credentials and optional external MQTT/TLS.
- Add protected GPIO character-device access and a custom AppArmor profile.
