#!/usr/bin/env python3
"""Backend del panel: login humano + proxy de MQTT.

El navegador ya no habla MQTT directo con Mosquitto ni conoce su
contrasena real. Este proceso es el unico que se conecta a Mosquitto
(con paho-mqtt, igual que comms_agent) y reenvia lo que llega por MQTT
a los navegadores conectados via un WebSocket propio -- y viceversa
para los comandos.
"""

import argparse
import asyncio
import hashlib
import json
import secrets
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from aiohttp import web, WSMsgType

PANEL_DIR = Path(__file__).resolve().parent
USERS_FILE = PANEL_DIR / 'users.json'

SESSION_COOKIE = 'panel_session'
SESSION_TTL_SEC = 12 * 3600  # un turno de guardia

INSTALLATION_ID = 'default'
ROBOT_ID = 'andino'
BASE_TOPIC = f'patrol/{INSTALLATION_ID}/{ROBOT_ID}'
STATE_TOPIC = f'{BASE_TOPIC}/state'
POSITION_TOPIC = f'{BASE_TOPIC}/position'
HEARTBEAT_TOPIC = f'{BASE_TOPIC}/heartbeat'
EVENTS_TOPIC = f'{BASE_TOPIC}/events'
CMD_TOPIC = f'{BASE_TOPIC}/cmd'

# mismo subconjunto de comandos que ya ofrecia el panel -- sin
# manual_start/manual_stop, a proposito (ver README).
VALID_COMMANDS = {'start', 'pause', 'resume', 'return_to_base', 'clear_failure'}

PBKDF2_ITERATIONS = 310_000


# -- contrasenas de usuarios humanos (independiente de las credenciales de Mosquitto) --

def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex()


def verify_password(password, salt_hex, hash_hex):
    _, digest_hex = hash_password(password, bytes.fromhex(salt_hex))
    return secrets.compare_digest(digest_hex, hash_hex)


def load_users():
    if not USERS_FILE.exists():
        return {}
    return json.loads(USERS_FILE.read_text())


# -- sesiones en memoria: se pierden si el server se reinicia (aceptable para v0,
# equivale a "todos vuelven a loguearse"; ver README) --

SESSIONS = {}  # session_id -> {'username':, 'expires':}


def create_session(username):
    session_id = secrets.token_urlsafe(32)
    SESSIONS[session_id] = {'username': username, 'expires': time.time() + SESSION_TTL_SEC}
    return session_id


def get_session(request):
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        return None
    session = SESSIONS.get(session_id)
    if not session:
        return None
    if session['expires'] < time.time():
        del SESSIONS[session_id]
        return None
    return session


# -- rutas HTTP --

async def handle_login_page(request):
    return web.FileResponse(PANEL_DIR / 'login.html')


async def handle_login_post(request):
    data = await request.post()
    username = data.get('username', '')
    password = data.get('password', '')
    entry = load_users().get(username)
    if not entry or not verify_password(password, entry['salt'], entry['hash']):
        raise web.HTTPFound('/login?error=1')
    session_id = create_session(username)
    response = web.HTTPFound('/')
    response.set_cookie(SESSION_COOKIE, session_id, httponly=True, samesite='Lax',
                         max_age=SESSION_TTL_SEC)
    return response


async def handle_logout(request):
    SESSIONS.pop(request.cookies.get(SESSION_COOKIE), None)
    response = web.HTTPFound('/login')
    response.del_cookie(SESSION_COOKIE)
    return response


async def handle_index(request):
    if not get_session(request):
        raise web.HTTPFound('/login')
    return web.FileResponse(PANEL_DIR / 'index.html')


async def handle_map_image(request):
    if not get_session(request):
        raise web.HTTPFound('/login')
    return web.FileResponse(PANEL_DIR / 'depot.png')


async def handle_ws(request):
    if not get_session(request):
        return web.Response(status=401, text='no autenticado -- iniciar sesion en /login')

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    request.app['websockets'].add(ws)
    mqtt_client = request.app['mqtt_client']

    # al conectar, mandar lo ultimo conocido -- si no, un navegador que se
    # conecta despues de que el estado ya se publico se queda sin saber
    # nada hasta el proximo cambio (que puede tardar mucho en llegar).
    for cached in request.app['last_known'].values():
        if cached is not None:
            await ws.send_str(cached)

    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                payload = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            if payload.get('type') == 'cmd' and payload.get('data') in VALID_COMMANDS:
                mqtt_client.publish(CMD_TOPIC, payload['data'], qos=1)
    finally:
        request.app['websockets'].discard(ws)
    return ws


# -- MQTT: conexion propia del backend, la contrasena real solo vive aca --

async def _safe_send(ws, text):
    try:
        await ws.send_str(text)
    except ConnectionResetError:
        pass


def make_mqtt_client(app, loop, host, port, username, password):
    client = mqtt.Client()
    if username:
        client.username_pw_set(username, password)

    def on_connect(c, userdata, flags, rc):
        if rc != 0:
            print(f'[server] fallo la conexion a Mosquitto (rc={rc})')
            return
        c.subscribe(STATE_TOPIC, qos=1)
        c.subscribe(POSITION_TOPIC, qos=1)
        c.subscribe(HEARTBEAT_TOPIC, qos=1)
        c.subscribe(EVENTS_TOPIC, qos=1)
        print('[server] conectado a Mosquitto')

    def on_message(c, userdata, msg):
        if msg.topic == STATE_TOPIC:
            key = 'state'
            out = {'type': 'state', 'data': msg.payload.decode('utf-8')}
        elif msg.topic == POSITION_TOPIC:
            key = 'position'
            out = {'type': 'position', 'data': json.loads(msg.payload.decode('utf-8'))}
        elif msg.topic == HEARTBEAT_TOPIC:
            key = 'heartbeat'
            out = {'type': 'heartbeat', 'data': None}
        elif msg.topic == EVENTS_TOPIC:
            # a diferencia de state/position/heartbeat, un evento no se
            # cachea en last_known -- es un hecho puntual, no algo que un
            # navegador que se conecta despues deba recibir como si
            # acabara de pasar.
            key = None
            out = {'type': 'event', 'data': json.loads(msg.payload.decode('utf-8'))}
        else:
            return
        text = json.dumps(out)
        if key is not None:
            app['last_known'][key] = text
        for ws in list(app['websockets']):
            asyncio.run_coroutine_threadsafe(_safe_send(ws, text), loop)

    client.on_connect = on_connect
    client.on_message = on_message
    # connect_async (en vez de connect) no hace el handshake TCP aca mismo --
    # lo delega al loop de background, que reintenta solo. Con connect(),
    # Mosquitto no estando listo justo cuando arranca server.py tira una
    # excepcion sin capturar que mata on_startup y con el todo aiohttp.
    client.connect_async(host, port)
    client.loop_start()
    return client


def create_app(mqtt_host, mqtt_port, mqtt_username, mqtt_password):
    app = web.Application()
    app['websockets'] = set()
    app['last_known'] = {'state': None, 'position': None, 'heartbeat': None}

    app.router.add_get('/login', handle_login_page)
    app.router.add_post('/login', handle_login_post)
    app.router.add_post('/logout', handle_logout)
    app.router.add_get('/', handle_index)
    app.router.add_get('/depot.png', handle_map_image)
    app.router.add_get('/ws', handle_ws)

    async def on_startup(app):
        loop = asyncio.get_event_loop()
        app['mqtt_client'] = make_mqtt_client(app, loop, mqtt_host, mqtt_port,
                                               mqtt_username, mqtt_password)

    async def on_cleanup(app):
        app['mqtt_client'].loop_stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main():
    parser = argparse.ArgumentParser(description='Backend del panel: login + proxy de MQTT.')
    parser.add_argument('--http-host', default='0.0.0.0')
    parser.add_argument('--http-port', type=int, default=8080)
    parser.add_argument('--mqtt-host', default='localhost')
    parser.add_argument('--mqtt-port', type=int, default=1883)
    parser.add_argument('--mqtt-username', default='',
                         help='Usuario de Mosquitto. Vacio = sin autenticacion.')
    parser.add_argument('--mqtt-password', default='',
                         help='Contrasena de Mosquitto. Sin default a proposito.')
    args = parser.parse_args()

    if not USERS_FILE.exists():
        print(f'[server] AVISO: no existe {USERS_FILE} -- nadie va a poder loguearse. '
              f'Correr "python3 manage_users.py <usuario>" primero.')

    app = create_app(args.mqtt_host, args.mqtt_port, args.mqtt_username, args.mqtt_password)
    web.run_app(app, host=args.http_host, port=args.http_port)


if __name__ == '__main__':
    main()
