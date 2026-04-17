"""Entry point: aiohttp server bridging iPhone mic → VB-Cable over a single HTTPS port.

HTML + WebSocket share the same port/cert, so iOS Safari only needs to accept the
self-signed certificate once.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import ssl
from pathlib import Path

from aiohttp import WSMsgType, web

from audio import AudioOutput, VBCableNotFound
from config import HTTP_PORT
from tls import ensure_cert

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('server')

CLIENT_DIR = Path(__file__).parent / 'client'


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


def make_app(audio: AudioOutput) -> web.Application:
    app = web.Application()
    app['active_client'] = None

    async def index(_request):
        return web.FileResponse(CLIENT_DIR / 'index.html')

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=2 ** 22, heartbeat=20)
        await ws.prepare(request)

        peer = request.remote or 'unknown'

        if app['active_client'] is not None:
            log.warning("Second client %s rejected (one at a time)", peer)
            await ws.close(code=1008, message=b'another client is active')
            return ws

        app['active_client'] = ws
        log.info("Client connected: %s", peer)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    audio.push(msg.data)
                elif msg.type == WSMsgType.TEXT:
                    if msg.data == 'ping':
                        await ws.send_str('pong')
                elif msg.type == WSMsgType.ERROR:
                    log.warning("WS error from %s: %s", peer, ws.exception())
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Unhandled error in ws handler")
        finally:
            log.info("Client disconnected: %s", peer)
            if app['active_client'] is ws:
                app['active_client'] = None

        return ws

    app.router.add_get('/', index)
    app.router.add_get('/index.html', index)
    app.router.add_get('/ws', ws_handler)
    app.router.add_static('/static', path=str(CLIENT_DIR), show_index=False)
    return app


async def main() -> None:
    try:
        audio = AudioOutput()
    except VBCableNotFound as e:
        log.error("%s", e)
        return

    audio.start()
    log.info("Audio stream started")

    lan_ip = _get_lan_ip()

    cert_path, key_path = ensure_cert(lan_ip)
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(cert_path, key_path)
    log.info("TLS enabled — cert=%s key=%s", cert_path.name, key_path.name)

    log.info("Open on iPhone: https://%s:%d", lan_ip, HTTP_PORT)
    log.info("Safari will warn about the cert once — tap 'Show details' → 'visit this website'")

    app = make_app(audio)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', HTTP_PORT, ssl_context=ssl_ctx)
    await site.start()
    log.info("HTTPS + WebSocket listening on :%d (ws path: /ws)", HTTP_PORT)

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
        audio.stop()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
