"""Entry point: asyncio WebSocket + HTTP server bridging iPhone mic → VB-Cable."""
from __future__ import annotations

import asyncio
import logging
import socket
from pathlib import Path

import websockets
from aiohttp import web
from websockets.exceptions import ConnectionClosed

from audio import AudioOutput, VBCableNotFound
from config import HTTP_PORT, WS_HOST, WS_PORT

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('server')

CLIENT_DIR = Path(__file__).parent / 'client'

_active_client: websockets.WebSocketServerProtocol | None = None


def _get_lan_ip() -> str:
    """Best-effort LAN IP lookup (doesn't send packets)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        s.close()


def make_ws_handler(audio: AudioOutput):
    async def handler(websocket):
        global _active_client
        peer = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"

        if _active_client is not None:
            log.warning("Second client %s rejected (one at a time)", peer)
            await websocket.close(code=1008, reason='another client is active')
            return

        _active_client = websocket
        log.info("Client connected: %s", peer)

        try:
            async for frame in websocket:
                if isinstance(frame, bytes):
                    audio.push(frame)
                elif isinstance(frame, str):
                    # Text control messages, e.g. 'ping'
                    if frame == 'ping':
                        try:
                            await websocket.send('pong')
                        except ConnectionClosed:
                            break
        except ConnectionClosed:
            pass
        except Exception:
            log.exception("Unhandled error in ws handler")
        finally:
            log.info("Client disconnected: %s", peer)
            if _active_client is websocket:
                _active_client = None

    return handler


def make_http_app() -> web.Application:
    app = web.Application()

    async def index(_request):
        return web.FileResponse(CLIENT_DIR / 'index.html')

    app.router.add_get('/', index)
    app.router.add_get('/index.html', index)
    app.router.add_static('/', path=str(CLIENT_DIR), show_index=False)
    return app


async def run_http(app: web.Application) -> None:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', HTTP_PORT)
    await site.start()
    log.info("HTTP server listening on :%d", HTTP_PORT)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()


async def main() -> None:
    try:
        audio = AudioOutput()
    except VBCableNotFound as e:
        log.error("%s", e)
        return

    audio.start()
    log.info("Audio stream started")

    lan_ip = _get_lan_ip()
    log.info("Open on iPhone: http://%s:%d", lan_ip, HTTP_PORT)

    handler = make_ws_handler(audio)
    app = make_http_app()

    async with websockets.serve(
        handler,
        WS_HOST,
        WS_PORT,
        max_size=2 ** 22,  # 4 MiB — plenty for a 2048-sample Float32 chunk
        ping_interval=20,
        ping_timeout=20,
    ):
        log.info("WebSocket server listening on :%d", WS_PORT)
        try:
            await run_http(app)
        finally:
            audio.stop()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
