"""
ODesk Rendezvous / Signaling Server (Para desplegar en AWS / VPS)
-----------------------------------------------------------------
Consumo de recursos:
 - Memoria RAM: < 20 MB
 - Ancho de banda: ~500 bytes por conexión (CERO VIDEO)
 - CPU: < 0.01%
 
Este servidor actúa únicamente como directorio de teléfonos:
1. El Host se registra con su ID de 9 dígitos.
2. El Cliente consulta la IP y puerto del ID.
3. El Cliente se conecta DIRECTAMENTE al Host (P2P). El servidor no toca el video.
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

# Diccionario en memoria: { "id_limpio": {"ip": str, "port": int, "writer": StreamWriter, "ts": float} }
REGISTRY = {}
TOTAL_BYTES_SERVED = 0


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
                
                # La IP pública es detectada directamente por el socket TCP en el servidor
                public_ip = msg.get("custom_ip") or client_ip
                
                REGISTRY[device_id] = {
                    "ip": public_ip,
                    "port": listen_port,
                    "writer": writer,
                    "ts": time.time(),
                }
                registered_id = device_id
                logging.info(f"[REGISTRO] ID: {device_id} -> {public_ip}:{listen_port}")
                
                resp = json.dumps({
                    "status": "ok",
                    "action": "registered",
                    "public_ip": public_ip
                }) + "\n"
                writer.write(resp.encode("utf-8"))
                await writer.drain()

            # 2. Consulta de Cliente
            elif msg_type == "lookup":
                target_id = "".join(c for c in str(msg.get("id", "")) if c.isdigit())
                peer_info = REGISTRY.get(target_id)

                if peer_info:
                    logging.info(f"[LOOKUP] Cliente {client_ip} consultó ID {target_id} -> Encontrado ({peer_info['ip']}:{peer_info['port']})")
                    resp = json.dumps({
                        "status": "ok",
                        "action": "lookup_result",
                        "id": target_id,
                        "ip": peer_info["ip"],
                        "port": peer_info["port"]
                    }) + "\n"
                else:
                    logging.info(f"[LOOKUP] Cliente {client_ip} consultó ID {target_id} -> No encontrado o fuera de línea")
                    resp = json.dumps({
                        "status": "not_found",
                        "action": "lookup_result",
                        "id": target_id
                    }) + "\n"

                writer.write(resp.encode("utf-8"))
                await writer.drain()

            # 3. Heartbeat / Keepalive (Ping / Pong)
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
                logging.info(f"[DESCONECTADO] ID: {registered_id} eliminado del registro.")
        writer.close()
        await writer.wait_closed()


async def stats_reporter():
    """Muestra estadísticas periódicas del consumo de ancho de banda."""
    while True:
        await asyncio.sleep(300)  # Cada 5 minutos
        logging.info(f"[ESTADÍSTICAS] Hosts activos: {len(REGISTRY)} | Total tráfico consumido: {TOTAL_BYTES_SERVED / 1024:.2f} KB")


async def main():
    host = "0.0.0.0"
    port = 7000
    server = await asyncio.start_server(handle_client, host, port)
    logging.info(f"===========================================================")
    logging.info(f" ODesk Signaling Server iniciado en {host}:{port}")
    logging.info(f" Consumo de red: Ultrabajo (Solo señalización de IDs)")
    logging.info(f"===========================================================")
    
    asyncio.create_task(stats_reporter())

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Servidor detenido manualmente.")
