import asyncio
import json

import pytest

from dwinui.moonraker import Moonraker, RPCError
from dwinui.printer import Printer


class StubWS:
    def __init__(self):
        self.sent = []
        self.incoming: asyncio.Queue = asyncio.Queue()

    async def send(self, data):
        self.sent.append(json.loads(data))

    def __aiter__(self):
        return self

    async def __anext__(self):
        msg = await self.incoming.get()
        if msg is None:
            raise StopAsyncIteration
        return json.dumps(msg)


def test_call_result_error_and_notifications():
    async def run():
        printer = Printer()
        mr = Moonraker(printer)
        ws = StubWS()
        mr._ws = ws
        reader = asyncio.create_task(mr._read_loop(ws))

        call = asyncio.create_task(mr.call("server.info"))
        await asyncio.sleep(0)
        req = ws.sent[-1]
        assert req["method"] == "server.info" and "params" not in req
        await ws.incoming.put({"jsonrpc": "2.0", "id": req["id"], "result": {"ok": 1}})
        assert await call == {"ok": 1}

        call = asyncio.create_task(mr.call("printer.gcode.script", script="G28"))
        await asyncio.sleep(0)
        req = ws.sent[-1]
        assert req["params"] == {"script": "G28"}
        await ws.incoming.put({"id": req["id"], "error": {"code": 400, "message": "Must home"}})
        with pytest.raises(RPCError, match="Must home"):
            await call

        responses = []
        mr.on_gcode_response = responses.append
        await ws.incoming.put({"method": "notify_status_update",
                               "params": [{"extruder": {"temperature": 201.5}}, 1.0]})
        await ws.incoming.put({"method": "notify_gcode_response", "params": ["!! oops"]})
        await ws.incoming.put({"method": "notify_klippy_shutdown"})
        await ws.incoming.put(None)
        await reader
        assert printer.temp("extruder")[0] == 201.5
        assert responses == ["!! oops"]
        assert printer.klippy_state == "shutdown"
    asyncio.run(run())


def test_webhooks_state_drives_klippy_state():
    printer = Printer()
    printer.connected = True
    printer.replace({"webhooks": {"state": "ready", "state_message": "ok"}})
    assert printer.ready
    printer.update({"webhooks": {"state": "shutdown", "state_message": "MCU lost"}})
    assert not printer.ready and printer.state_message == "MCU lost"


def test_full_session_against_websocket_server():
    from websockets.asyncio.server import serve

    async def run():
        seen = []
        state = {"klippy": "startup"}

        async def handler(ws):
            async for raw in ws:
                msg = json.loads(raw)
                seen.append(msg["method"])
                method = msg["method"]
                if method == "server.connection.identify":
                    result = {"connection_id": 1}
                elif method == "printer.info":
                    result = {"state": state["klippy"], "state_message": "Starting up"}
                    state["klippy"] = "ready"        # ready on the next poll
                elif method == "printer.objects.query":
                    result = {"status": {"heaters": {"available_heaters": ["extruder"],
                                                     "available_sensors": ["extruder", "temperature_sensor mcu"]}}}
                elif method == "printer.objects.subscribe":
                    assert "print_stats" in msg["params"]["objects"]
                    assert "temperature_sensor mcu" in msg["params"]["objects"]
                    result = {"eventtime": 1.0, "status": {
                        "webhooks": {"state": "ready", "state_message": "ok"},
                        "extruder": {"temperature": 25.0, "target": 0.0}}}
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}))
                    await ws.send(json.dumps({"jsonrpc": "2.0", "method": "notify_status_update",
                                              "params": [{"extruder": {"target": 200.0}}, 2.0]}))
                    await ws.close()                 # force a reconnect
                    return
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}))

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            printer = Printer()
            states = []
            printer.listen(lambda: states.append(printer.klippy_state))
            mr = Moonraker(printer, f"ws://127.0.0.1:{port}/websocket")
            task = asyncio.create_task(mr.run())
            for _ in range(60):
                await asyncio.sleep(0.1)
                if "disconnected" in states[states.index("ready"):] if "ready" in states else False:
                    break
            task.cancel()
        assert seen[:2] == ["server.connection.identify", "printer.info"]
        assert "startup" in states and "ready" in states
        assert printer.temp("extruder") == (25.0, 200.0)
        assert not printer.connected and printer.klippy_state == "disconnected"
    asyncio.run(run())


def test_sensor_order_and_labels():
    from dwinui.printer import sensor_label
    printer = Printer()
    printer.replace({"heaters": {
        "available_heaters": ["heater_bed", "extruder"],
        "available_sensors": ["temperature_sensor cartographer_coil", "heater_bed",
                              "temperature_sensor Chamber", "temperature_fan Host_MCU",
                              "temperature_sensor Cartographer_MCU", "extruder"]}})
    assert [sensor_label(n) for n in printer.sensor_names] == [
        "Hotend", "Bed", "Cartographer Coil", "Cartographer MCU", "Chamber", "Host MCU"]
