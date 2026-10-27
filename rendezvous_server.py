"""
ODesk Rendezvous & Relay Server (Para AWS EC2 / VPS)
---------------------------------------------------
Funcionalidades:
1. Directorio de Señalización de IDs.
2. Detección Inteligente de LAN vs WAN (Intercambio de IP Pública y Local).
3. Puente de Retransmisión P2P / Relay Fallback:
   Si los routers de ambos equipos tienen NAT estricto o no soportan loopback,
   el servidor establece un puente de datos transparente entre ambos sockets.
"""

import asyncio
import json
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

REGISTRY = {}
RELAY_SESSIONS = {}
TOTAL_BYTES_SERVED = 0


async def pipe_streams(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    global TOTAL_BYTES_SERVED
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            TOTAL_BYTES_SERVED += len(data)
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    global TOTAL_BYTES_SERVED
    client_ip, client_port = writer.get_extra_info("peername")
    registered_id = None

    try:
        while True:
            line = await reader.readline()
            if not line:
                break

            TOTAL_BYTES_SERVED += len(line)
            try:
                msg = json.loads(line.decode("utf-8").strip())
            except Exception:
                continue

            msg_type = msg.get("action")

            # 1. Registro de Host
            if msg_type == "register":
                device_id = "".join(c for c in str(msg.get("id", "")) if c.isdigit())
                listen_port = int(msg.get("port", 5555))
                local_ip = msg.get("local_ip", "")

                REGISTRY[device_id] = {
                    "public_ip": client_ip,
                    "local_ip": local_ip,
                    "port": listen_port,
                    "writer": writer,
                    "reader": reader,
                    "ts": time.time(),
                }
                registered_id = device_id
                logging.info(f"[REGISTRO] ID: {device_id} -> WAN: {client_ip}:{listen_port} | LAN: {local_ip}")

                resp = json.dumps({
                    "status": "ok",
                    "action": "registered",
                    "public_ip": client_ip
                }) + "\n"
                writer.write(resp.encode("utf-8"))
                await writer.drain()

            # 2. Consulta de Cliente (Lookup)
            elif msg_type == "lookup":
                target_id = "".join(c for c in str(msg.get("id", "")) if c.isdigit())
                peer = REGISTRY.get(target_id)

                if peer:
                    logging.info(f"[LOOKUP] ID {target_id} consultado por {client_ip} -> Encontrado")
                    resp = json.dumps({
                        "status": "ok",
                        "action": "lookup_result",
                        "id": target_id,
                        "public_ip": peer["public_ip"],
                        "local_ip": peer.get("local_ip", ""),
                        "port": peer["port"],
                        "same_network": (client_ip == peer["public_ip"])
                    }) + "\n"
                else:
                    logging.info(f"[LOOKUP] ID {target_id} consultado por {client_ip} -> Fuera de línea")
                    resp = json.dumps({
                        "status": "not_found",
                        "action": "lookup_result",
                        "id": target_id
                    }) + "\n"

                writer.write(resp.encode("utf-8"))
                await writer.drain()

            # 3. Notificación de conexión entrante para activar Relay
            elif msg_type == "request_relay":
                target_id = "".join(c for c in str(msg.get("target_id", "")) if c.isdigit())
                session_id = f"relay_{int(time.time()*1000)}_{random_hex(6)}"
                peer = REGISTRY.get(target_id)

                if peer and peer.get("writer"):
                    # Notificar al Host por su socket abierto que se conecte al relay
                    notify_msg = json.dumps({
                        "action": "incoming_relay",
                        "session_id": session_id
                    }) + "\n"
                    try:
                        peer["writer"].write(notify_msg.encode("utf-8"))
                        await peer["writer"].drain()
                    except Exception:
                        pass

                    # Guardar sesion de relay
                    RELAY_SESSIONS[session_id] = {"client_r": reader, "client_w": writer, "host_r": None, "host_w": None}
                    logging.info(f"[RELAY] Sesión {session_id} creada entre {client_ip} y {target_id}")

                    # Este socket ahora es un túnel de datos (esperar emparejamiento)
                    await wait_and_bridge_relay(session_id, is_host=False, r=reader, w=writer)
                    return
                else:
                    writer.write(b'{"status":"target_offline"}\n')
                    await writer.drain()

            # 4. Host uniéndose al Relay
            elif msg_type == "join_relay":
                session_id = msg.get("session_id")
                if session_id in RELAY_SESSIONS:
                    RELAY_SESSIONS[session_id]["host_r"] = reader
                    RELAY_SESSIONS[session_id]["host_w"] = writer
                    logging.info(f"[RELAY] Host emparejado en sesión {session_id}. Iniciando túnel...")
                    await wait_and_bridge_relay(session_id, is_host=True, r=reader, w=writer)
                    return

            # 5. Ping
            elif msg_type == "ping":
                if registered_id and registered_id in REGISTRY:
                    REGISTRY[registered_id]["ts"] = time.time()
                writer.write(b'{"status":"pong"}\n')
                await writer.drain()

    except (ConnectionResetError, asyncio.IncompleteReadError):
        pass
    finally:
        if registered_id and registered_id in REGISTRY:
            if REGISTRY[registered_id].get("writer") == writer:
                del REGISTRY[registered_id]
                logging.info(f"[DESCONECTADO] ID: {registered_id} desconectado.")
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


def random_hex(n):
    import secrets
    return secrets.token_hex(n)


async def wait_and_bridge_relay(session_id, is_host, r, w):
    """Espera a que ambas partes estén conectadas y establece un puente bidireccional transparente."""
    for _ in range(60):  # Esperar hasta 6 segundos
        sess = RELAY_SESSIONS.get(session_id)
        if sess and sess["client_r"] and sess["host_r"]:
            break
        await asyncio.sleep(0.1)

    sess = RELAY_SESSIONS.get(session_id)
    if not sess or not sess["client_r"] or not sess["host_r"]:
        return

    if is_host:
        # El hilo del cliente se encargará del puente
        return

    client_r, client_w = sess["client_r"], sess["client_w"]
    host_r, host_w = sess["host_r"], sess["host_w"]

    try:
        # Puente bidireccional
        await asyncio.gather(
            pipe_streams(client_r, host_w),
            pipe_streams(host_r, client_w)
        )
    finally:
        RELAY_SESSIONS.pop(session_id, None)
        logging.info(f"[RELAY] Sesión {session_id} finalizada.")


async def stats_reporter():
    while True:
        await asyncio.sleep(300)
        logging.info(f"[ESTADÍSTICAS] Hosts activos: {len(REGISTRY)} | Tráfico total: {TOTAL_BYTES_SERVED / 1024:.2f} KB")


async def main():
    host = "0.0.0.0"
    port = 7000
    server = await asyncio.start_server(handle_client, host, port)
    logging.info(f"===========================================================")
    logging.info(f" ODesk Rendezvous & Relay Server iniciado en {host}:{port}")
    logging.info(f" Soporte: LAN directa, WAN directa y Relay Fallback")
    logging.info(f"===========================================================")
    asyncio.create_task(stats_reporter())
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
