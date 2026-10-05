# esp32-7oled 20261005-v1.0.11

- Parse backend WAV by RIFF chunks instead of fixed 44-byte offset.
- Read fmt and locate data, allowing LIST/INFO chunks between fmt and data.
- Keep HTTPClient TTS POST download and buffered WAV playback path.
- Build verified for esp32-7oled.
