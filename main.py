from quart import Quart, render_template, websocket, request
import asyncio
import time
import aiohttp
import base64
import functools


app = Quart(__name__)


in_game = False
start_time = 0
messages = asyncio.Queue()
previous_log = ""

current_header = "Not In Game"

control_connections = set()
view_connections = set()

terminating = False


ip_dict = {"zone1": "192.168.4.1",
           "zone2": "0.0.0.0",
           "zone3": "0.0.0.0",
           "zone4": "0.0.0.0"}

conn_dict = {"zone1": set(),
             "zone2": set(),
             "zone3": set(),
             "zone4": set()}


async def game_loop():
    global in_game

    while not terminating:
        if in_game:
            if 300 - (time.time() - start_time) < 0:
                in_game = False
                await send_log("Game ended due to time.")
                await send_header("Not In Game")
                continue

            await send_log(f"In game. {round(300 - (time.time() - start_time))} seconds remaining.")
            await send_header(f"{round(300 - (time.time() - start_time))}")

        await asyncio.sleep(0.01)


async def source_images(zone):
    url = f"http://{ip_dict[zone]}/livestream"
    async with aiohttp.ClientSession() as session:
        while not terminating:
            try:
                async with session.get(url, timeout=1) as resp:
                    msg = await resp.text()
                img_bytes = base64.a85decode(msg)
                for conn in conn_dict[zone]:
                    await conn.put(img_bytes)
            except (asyncio.TimeoutError, aiohttp.client_exceptions.ClientConnectionError):
                pass
            await asyncio.sleep(0.01)


async def subscribe_to_stream(zone):
    conn = asyncio.Queue()
    conn_dict[zone].add(conn)
    try:
        while True:
            yield await conn.get()
    finally:
        conn_dict[zone].remove(conn)


async def send_log(message):
    global previous_log

    previous_log = message
    for connection in control_connections:
        await connection.put(message)


async def subscribe_to_log():
    connection = asyncio.Queue()
    await connection.put(previous_log)
    control_connections.add(connection)
    try:
        while True:
            yield await connection.get()
    finally:
        control_connections.remove(connection)


@app.route("/")
async def index():
    return await render_template("index.html")


async def receive():
    global in_game
    global start_time

    while True:
        message = await websocket.receive()
        match message:
            case "start":
                in_game = True
                start_time = time.time()
            case "end":
                if in_game:
                    in_game = False
                    await send_log("Game ended manually.")
                    await send_header("Not In Game")


@app.websocket("/")
async def control_panel():
    await websocket.accept()
    try:
        task = asyncio.ensure_future(receive())
        async for message in subscribe_to_log():
            await websocket.send(message)
    finally:
        task.cancel()
        await task


async def subscribe_to_header():
    connection = asyncio.Queue()
    await connection.put(current_header)
    view_connections.add(connection)
    try:
        while True:
            yield await connection.get()
    finally:
        view_connections.remove(connection)


async def send_header(message):
    global current_header

    current_header = message
    for connection in view_connections:
        await connection.put(message)


@app.route("/view")
async def view():
    return await render_template("view.html")


@app.websocket("/banner")
async def view_banner():
    await websocket.accept()
    async for message in subscribe_to_header():
        await websocket.send(message)


@app.route("/log")
async def log():
    if "zone" not in request.args:
        return await render_template("log_navigator.html")
    return await render_template("log.html", ip=ip_dict[request.args["zone"]])


@app.websocket("/stream/<zone>")
async def stream(zone):
    await websocket.accept()
    async for img_bytes in subscribe_to_stream(zone):
        await websocket.send(img_bytes)


@app.before_serving
async def startup():
    app.add_background_task(game_loop)
    app.add_background_task(functools.partial(source_images, "zone1"))
    # app.add_background_task(functools.partial(source_images, "zone2"))
    # app.add_background_task(functools.partial(source_images, "zone3"))
    # app.add_background_task(functools.partial(source_images, "zone4"))


@app.after_serving
async def shutdown():
    global terminating
    terminating = True


if __name__ == "__main__":
    app.run(debug=True, port=8000)
