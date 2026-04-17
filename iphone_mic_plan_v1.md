# iPhone → Windows PC Microphone Bridge
### Build Plan — Wi-Fi, Advanced Python, Auto-reconnect + Volume Control + Phone Call Recovery

---

## 1. Goal

Stream iPhone microphone audio over local Wi-Fi to a virtual audio device on Windows (VB-Cable), making the iPhone mic appear as a standard Windows input device selectable in Discord, OBS, or any app — with no iPhone app install required (Safari only), auto-reconnect on drop, volume control UI, and graceful phone call interruption recovery.

**Note on noise suppression:** Browser-side noise suppression is intentionally omitted. Discord's built-in Voice Isolation already handles this on the receiving end — double-processing degrades audio quality. Raw PCM is sent; let Discord do the work.

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        iPhone (Safari)                       │
│                                                             │
│  getUserMedia ──► AudioWorklet ──► GainNode (volume)        │
│       │               │                  │                  │
│  noiseSuppression   PCM chunks      WebSocket client        │
│  echoCancellation   (Float32)       + auto-reconnect        │
└───────────────────────────────────┬─────────────────────────┘
                                    │ WebSocket (ws://PC_IP:8765)
                                    │ Binary frames (Float32LE PCM)
┌───────────────────────────────────▼─────────────────────────┐
│                     Windows PC (Python)                      │
│                                                             │
│  asyncio WebSocket server (websockets)                      │
│       │                                                     │
│  Audio buffer (numpy ring buffer)                           │
│       │                                                     │
│  sounddevice.OutputStream ──► VB-Cable Input                │
│                                                             │
│  aiohttp HTTP server ──► serves client HTML to iPhone       │
└───────────────────────────────────┬─────────────────────────┘
                                    │
                              VB-Cable Input
                                    │
                              VB-Cable Output ──► Discord / any app
```

**Data flow summary:**
1. Python starts: HTTP server on `:8080`, WebSocket server on `:8765`
2. iPhone opens `http://PC_IP:8080` in Safari
3. Safari captures mic via AudioWorklet → Float32 PCM chunks → WebSocket binary frames
4. Python receives frames → numpy array → sounddevice writes to VB-Cable Input
5. Discord selects "CABLE Output (VB-Audio Virtual Cable)" as mic input

---

## 3. Prerequisites

### Windows
| Dependency | Version | Purpose |
|---|---|---|
| Python | 3.11+ | Server runtime |
| `websockets` | 12+ | WebSocket server |
| `aiohttp` | 3.9+ | HTTP server to serve client HTML |
| `sounddevice` | 0.4.6+ | Write PCM to VB-Cable |
| `numpy` | 1.26+ | Audio buffer handling |
| VB-Cable | Latest | Virtual audio driver |

Install deps:
```bash
pip install websockets aiohttp sounddevice numpy
```

VB-Cable: https://vb-audio.com/Cable/ — free, install and reboot.

### iPhone
- Safari (no install needed)
- Same local Wi-Fi network as PC
- HTTPS **not** required (getUserMedia works on HTTP for LAN IPs in Safari as of iOS 14.5+)
  - ⚠️ If Safari blocks mic on plain HTTP, see Section 8 (Self-signed cert fallback)

---

## 4. File Structure

```
iphone-mic/
├── server.py          # Main entry: asyncio WebSocket + HTTP server
├── audio.py           # sounddevice output stream, ring buffer
├── config.py          # All tuneable constants in one place
├── client/
│   └── index.html     # iPhone UI: volume slider, status, auto-reconnect
└── README.md
```

---

## 5. Audio Pipeline Specification

| Parameter | Value | Rationale |
|---|---|---|
| Sample rate | 48000 Hz | Discord native rate, no resampling needed |
| Channels | 1 (mono) | Mic input; stereo wastes bandwidth |
| Bit depth | Float32 | Native Web Audio format, no conversion loss |
| Chunk size | 2048 samples | ~42ms per chunk — stable over Wi-Fi |
| Wire format | Raw Float32LE binary | Zero serialisation overhead |
| Noise suppression | `noiseSuppression: false` | Discord Voice Isolation handles this — no double processing |
| Echo cancellation | `echoCancellation: true` | Still useful to prevent feedback from PC speakers |

**Latency budget (LAN):**
```
AudioWorklet chunk:     ~42ms  (2048 / 48000)
Wi-Fi RTT (LAN):        ~2ms
sounddevice buffer:     ~21ms  (1024 frames)
──────────────────────────────
Total end-to-end:       ~65ms  (acceptable for voice)
```

To reduce latency: lower chunk size to 1024 (tradeoff: higher CPU, more sensitive to Wi-Fi jitter).

---

## 6. Component Breakdown

### 6.1 `config.py`
```python
SAMPLE_RATE    = 48000
CHANNELS       = 1
CHUNK_SAMPLES  = 2048
DTYPE          = 'float32'
WS_HOST        = '0.0.0.0'
WS_PORT        = 8765
HTTP_PORT      = 8080
RING_BUF_SIZE  = CHUNK_SAMPLES * 8  # 8 chunks headroom
```

---

### 6.2 `audio.py` — Ring Buffer + sounddevice Stream

**Key design decisions:**
- `sounddevice.OutputStream` in callback mode (non-blocking, low jitter)
- Thread-safe `collections.deque` as ring buffer between asyncio and audio thread
- Silence padding when buffer underruns (prevents clicks)
- Device auto-detection: finds VB-Cable Input by name substring match

```python
# Pseudocode structure
class AudioOutput:
    def __init__(self):
        self.buffer = deque(maxlen=RING_BUF_SIZE)
        self.stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=1024,
            device=self._find_vbcable(),
            callback=self._callback
        )

    def _find_vbcable(self):
        # iterate sd.query_devices(), match "CABLE Input" case-insensitive
        # raise RuntimeError with helpful message if not found

    def _callback(self, outdata, frames, time, status):
        # drain self.buffer into outdata
        # pad with zeros on underrun (silence > glitch)

    def push(self, pcm_bytes: bytes):
        # np.frombuffer(pcm_bytes, dtype=DTYPE) → deque
```

---

### 6.3 `server.py` — asyncio WebSocket + HTTP

**Structure:**
```python
# Two coroutines run concurrently under asyncio.gather():
# 1. websockets.serve() — handles audio streaming
# 2. aiohttp web.Application() — serves index.html

async def ws_handler(websocket):
    # On connect: log client IP
    # Loop: receive binary frame → audio.push(frame)
    # On disconnect: log, allow next client to connect
    # ConnectionClosed is expected — handle silently

async def main():
    audio = AudioOutput()
    audio.stream.start()

    ws_server = websockets.serve(ws_handler, WS_HOST, WS_PORT)
    http_server = start_http()  # aiohttp serving client/

    await asyncio.gather(ws_server, http_server)
```

**Important:** Only one WebSocket client at a time (your phone). No auth needed for local use, but log the connecting IP for visibility.

---

### 6.4 `client/index.html` — iPhone UI

**Web Audio chain:**
```
MediaStreamSource → GainNode (volume) → AudioWorkletNode → WebSocket.send()
```

**AudioWorklet processor** (runs on audio thread, avoids main thread jitter):
```javascript
// worklet-processor.js (inlined as Blob URL to avoid CORS issues)
class PCMProcessor extends AudioWorkletProcessor {
    process(inputs) {
        // inputs[0][0] = Float32Array of CHUNK_SAMPLES
        // post to main thread via this.port.postMessage()
    }
}
```

**Auto-reconnect logic:**
```javascript
// Exponential backoff: 1s → 2s → 4s → 8s → cap at 30s
// Resets to 1s on successful connection
// Visual indicator: Connecting / Live / Reconnecting
```

**UI elements:**
- Status badge: `● Connecting` / `● Live` / `● Reconnecting`
- Volume slider: 0–200% (GainNode.gain.value = slider / 100)
- Mute toggle button
- Latency display (ping via WebSocket message roundtrip, updates every 5s)

**iPhone Safari considerations:**
- `getUserMedia` requires a user gesture (tap button to start — don't auto-start)
- AudioContext must be resumed after user gesture (`context.resume()`)
- ~~Wake lock~~ — `navigator.wakeLock` is unreliable on Safari/iOS, **do not use** (see Section 11 for screen sleep handling)

---

## 7. Implementation Phases

### Phase 1 — Core pipeline (get audio flowing) ~2-3h
- [ ] Install VB-Cable, verify it appears in `sd.query_devices()`
- [ ] Write `config.py`
- [ ] Write `audio.py` with ring buffer + sounddevice stream
- [ ] Write minimal WebSocket server (`server.py`)
- [ ] Write minimal HTML client (no UI polish, just `getUserMedia` + WebSocket send)
- [ ] Test: open Safari → verify audio reaches VB-Cable → select in Discord

### Phase 2 — Robustness ~1-2h
- [ ] Add auto-reconnect with exponential backoff to client
- [ ] Add graceful server-side disconnect handling
- [ ] Add silence padding on buffer underrun
- [ ] Add VB-Cable not found error with helpful message
- [ ] Test: kill Wi-Fi for 10s → reconnects cleanly

### Phase 3 — Features ~1h
- [ ] Volume slider (GainNode)
- [ ] Mute button
- [ ] Status badge with reconnect state
- [ ] Latency ping display
- [ ] Screen sleep handling: silent oscillator (primary) + visibilitychange resume (fallback)
- [ ] AudioContext `statechange` listener (phone call interruption)
- [ ] "Tap to Resume" button shown on interruption
- [ ] `● Call in progress` status state

### Phase 4 — Polish ~30min
- [ ] Clean mobile UI (large touch targets, dark theme)
- [ ] `README.md` with setup instructions
- [ ] Startup script (`start.bat`) that launches server and opens browser

---

## 8. HTTPS Fallback (if Safari blocks mic on HTTP)

Safari on iOS may require HTTPS for `getUserMedia` even on LAN. If this happens:

Generate a self-signed cert:
```bash
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj "/CN=localhost"
```

Then serve over HTTPS in aiohttp:
```python
import ssl
ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
ssl_ctx.load_cert_chain('cert.pem', 'key.pem')
# pass ssl_ctx to aiohttp runner
```

iPhone will show a certificate warning once — tap "Advanced → Proceed" and it won't appear again.

---

## 9. Discord Setup (final step)

1. Open Discord → Settings → Voice & Video
2. Input Device → **"CABLE Output (VB-Audio Virtual Cable)"**
3. Input sensitivity → disable "Automatically determine input sensitivity"
4. Test mic → you should hear your iPhone mic

---

## 10. Phone Call Interruption Handling

When an incoming call arrives on iPhone, iOS takes over the audio session and suspends Safari's mic access. The WebSocket connection will drop. This needs explicit handling — auto-reconnect alone isn't enough because iOS requires a **user gesture** to resume AudioContext after interruption.

**What happens step by step:**
1. Call comes in → iOS suspends Safari audio session
2. AudioWorklet stops producing frames → WebSocket goes silent
3. Server detects silence / disconnect → logs it, waits
4. Call ends → Safari page resumes but `AudioContext.state === 'interrupted'`
5. Auto-reconnect fires → WebSocket reconnects ✓
6. But audio still won't flow until user taps to resume AudioContext ✗

**Client-side fix — listen for AudioContext state changes:**
```javascript
audioContext.addEventListener('statechange', () => {
    if (audioContext.state === 'interrupted') {
        // iOS phone call took over
        showResumeButton(); // prominent "Tap to Resume" button
        stopStreaming();
    }
    if (audioContext.state === 'running') {
        // resumed — restart stream if WS is connected
        if (ws?.readyState === WebSocket.OPEN) startStreaming();
    }
});
```

**UI behaviour during a call:**
- Status badge switches to `● Call in progress`
- "Tap to Resume" button appears prominently
- After call ends and user taps: AudioContext resumes, streaming restarts automatically

**Server-side:** No special handling needed. The existing graceful disconnect + silence padding already covers the gap. Python just waits.

**Add to Phase 3 checklist:**
- [ ] AudioContext `statechange` listener
- [ ] "Tap to Resume" button shown on interruption
- [ ] `● Call in progress` status state

---

## 11. Screen Sleep Handling

`navigator.wakeLock` on Safari/iOS is unreliable and partially supported — **do not rely on it**. Instead, use a two-layer approach that keeps the mic running without requiring the screen to stay on.

### Layer 1 — Silent Oscillator (primary prevention)
iOS won't fully suspend Safari's audio session if there is active audio output, even at zero volume. This is the same trick used by podcast and music apps to stay alive in the background.

```javascript
// Run immediately after AudioContext is created and resumed
function keepAudioSessionAlive(ctx) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    gain.gain.value = 0;        // completely silent — no audible output
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();                // runs forever, keeps session active
}
```

This prevents the screen sleep problem entirely in most cases. Battery impact is negligible.

### Layer 2 — visibilitychange Resume (fallback)
If the audio session is suspended despite Layer 1 (e.g. on older iOS versions), auto-resume the moment the screen is unlocked — no tap required:

```javascript
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        audioContext.resume().then(() => {
            if (ws?.readyState === WebSocket.OPEN) {
                startStreaming(); // restart mic → worklet → WS pipeline
            }
        });
    }
});
```

### Combined behaviour
| Scenario | Result |
|---|---|
| Screen sleeps, Layer 1 works | Mic keeps streaming — no interruption at all |
| Screen sleeps, Layer 1 fails | Session suspends; auto-resumes on screen unlock via Layer 2 |
| Screen sleeps, both fail | WS drops; auto-reconnect fires on wake; one tap to restart mic |

The worst case is a single tap after unlocking — no page reload, no reconfiguration.

---

## 12. Known Limitations & Mitigations

| Limitation | Mitigation |
|---|---|
| Wi-Fi jitter causes audio glitches | Ring buffer headroom (8 chunks); silence on underrun beats glitches |
| iPhone screen sleeps, mic stops | Silent oscillator (primary) + visibilitychange resume (fallback) — Section 11 |
| Safari requires user gesture for AudioContext | "Start" button in UI — document clearly |
| Phone call interrupts mic session | AudioContext statechange listener + "Tap to Resume" button — Section 10 |
| Only one client at a time | Sufficient for use case; log warning if second client tries to connect |
| No encryption | LAN-only, no sensitive data beyond voice — acceptable |

---

## 13. Testing Checklist

- [ ] Audio flows: iPhone → Discord voice test
- [ ] Volume slider affects level in Discord input meter
- [ ] Kill Wi-Fi for 10s → reconnects automatically
- [ ] Lock iPhone screen → audio continues uninterrupted (Layer 1 working)
- [ ] Lock iPhone screen on older iOS → unlock → audio resumes automatically (Layer 2 fallback)
- [ ] Simulate phone call → `● Call in progress` appears → call ends → tap Resume → audio flows again
- [ ] Reboot PC → `start.bat` brings everything back up in one click
