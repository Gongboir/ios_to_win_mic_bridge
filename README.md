# iPhone → Windows Mic Bridge

Stream your iPhone's microphone to a Windows PC over Wi-Fi so it shows up as a regular mic in Discord/OBS/etc. No iPhone app — Safari only.

## One-time setup (Windows)

1. **Install Python 3.11+** from https://python.org (tick "Add to PATH").
2. **Install VB-Cable** from https://vb-audio.com/Cable/ — run the installer as administrator and reboot.
3. Double-click `start.bat`. First run creates a `.venv` and installs dependencies; subsequent runs start instantly.

The console prints something like:

```
Open on iPhone: https://192.168.1.42:8080
```

On first run the server auto-generates a self-signed cert (`cert.pem` / `key.pem`) — iOS Safari requires HTTPS for mic access.

## Using it

1. Make sure your iPhone is on the same Wi-Fi as the PC.
2. On the iPhone, open Safari and go to the URL printed above.
3. **Accept the cert warning**: tap *"Show details" → "visit this website" → "Visit Website"*. Only needed the first time.
4. Tap **Start**, grant mic permission.
5. Status badge goes green → **Live**.
6. In Discord: *Settings → Voice & Video → Input Device → "CABLE Output (VB-Audio Virtual Cable)"*.

### Regenerate the cert

Delete `cert.pem` and `key.pem` and restart. A fresh cert will be generated (useful if your PC's LAN IP changes — the cert pins the IP into its SAN extension).

## Features

- Auto-reconnect with exponential backoff (1s → 30s cap)
- Volume control (0–200%) and mute
- Input level meter
- Round-trip latency display (updates every 5s)
- Phone-call interrupt handling — shows **Tap to Resume** button
- Screen-sleep handling — silent oscillator keeps the audio session alive; `visibilitychange` listener resumes on unlock

## Firewall

First run will prompt Windows Firewall to allow Python on **private networks**. Accept it. If you accidentally blocked it, add an inbound rule for TCP ports **8080** and **8765**.

## Files

```
config.py           # tuneable constants
audio.py            # ring buffer + sounddevice output
server.py           # asyncio WebSocket + aiohttp HTTP
client/index.html   # iPhone Safari UI
start.bat           # Windows launcher (auto-installs deps)
requirements.txt
```

## Troubleshooting

**"VB-Cable output device not found"** — install VB-Cable and reboot. The script lists available devices in the error message.

**No audio in Discord** — confirm the Discord input is *"CABLE Output"* (not Input), disable Discord's auto-input-sensitivity.

**Audio glitches** — move closer to the router. If the problem persists, lower `CHUNK_SAMPLES` in `config.py` to 1024 (lower latency, more sensitive to Wi-Fi jitter).

**iPhone disconnects when screen locks** — the silent-oscillator trick covers most cases; if it fails, unlock to auto-resume via `visibilitychange`.
