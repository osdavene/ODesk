"""
ODesk - Herramienta de escritorio remoto moderna, ligera y P2P (Python + Rust).
Diseño visual AnyDesk / RustDesk con CustomTkinter.
Soporte para:
- Conexión Inteligente Multi-Ruta:
  1. Conexión Directa LAN (Ultra rápida para equipos en la misma red/casa).
  2. Conexión Directa WAN P2P.
  3. Puente de Retransmisión (Relay) automático por AWS si el router bloquea puertos.
- Detección y bloqueo de conexión al propio equipo.
- Botón de 'Cancelar' interactivo mientras se conecta.
- Transferencia de archivos bidireccional (Arrastrar y Soltar / Botón F2).
- Sincronización automática de Portapapeles (Copiar y Pegar texto Ctrl+C / Ctrl+V).
- Autenticación dual (Contraseña o Autorización interactiva).
- Modo Administrador automático (UIPI).
- Interfaz limpia (marca ODesk, sin datos de infraestructura ni IPs expuestas).
"""

import sys
import os
import socket
import struct
import threading
import time
import io
import json
import random
import ctypes
import winsound


# =====================================================================
# WIN32 CLIPBOARD Y ADMINISTRADOR (UAC)
# =====================================================================
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
CF_UNICODETEXT = 13


def get_local_lan_ip():
    """Detecta la IP local de este equipo dentro de la red LAN/WiFi."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_clipboard_text():
    if not user32.OpenClipboard(0):
        return ""
    text = ""
    try:
        h_clip = user32.GetClipboardData(CF_UNICODETEXT)
        if h_clip:
            p_clip = kernel32.GlobalLock(h_clip)
            if p_clip:
                text = ctypes.c_wchar_p(p_clip).value or ""
                kernel32.GlobalUnlock(h_clip)
    except Exception:
        pass
    finally:
        user32.CloseClipboard()
    return text


def set_clipboard_text(text):
    if not text or not user32.OpenClipboard(0):
        return False
    try:
        user32.EmptyClipboard()
        encoded = text.encode("utf-16le") + b"\x00\x00"
        h_mem = kernel32.GlobalAlloc(0x0042, len(encoded))
        if h_mem:
            p_mem = kernel32.GlobalLock(h_mem)
            if p_mem:
                ctypes.memmove(p_mem, encoded, len(encoded))
                kernel32.GlobalUnlock(h_mem)
                user32.SetClipboardData(CF_UNICODETEXT, h_mem)
    except Exception:
        pass
    finally:
        user32.CloseClipboard()
    return True


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def ensure_admin():
    if not is_admin():
        try:
            if getattr(sys, "frozen", False):
                ret = ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", sys.executable, " ".join(sys.argv[1:]), None, 1
                )
            else:
                params = f'"{os.path.abspath(sys.argv[0])}" ' + " ".join(
                    f'"{arg}"' for arg in sys.argv[1:]
                )
                ret = ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", sys.executable, params, None, 1
                )
            if ret > 32:
                sys.exit(0)
        except Exception:
            pass


try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


CONFIG_FILE = os.path.join(
    os.environ.get("APPDATA", os.path.dirname(__file__)), "odesk_config.json"
)

DEFAULT_SIGNAL_SERVER = "18.191.199.104:7000"


def load_or_create_config():
    default_cfg = {
        "device_id": f"{random.randint(100, 999)} {random.randint(100, 999)} {random.randint(100, 999)}",
        "password": f"{random.randint(100000, 999999)}",
        "signal_server": DEFAULT_SIGNAL_SERVER,
        "listen_port": 5555,
        "fps": 45,
        "quality": 70,
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                default_cfg.update(cfg)
                return default_cfg
        except Exception:
            pass
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_cfg, f, indent=4)
    except Exception:
        pass
    return default_cfg


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception:
        pass


def id_to_ip_fallback(id_str: str) -> str:
    clean = "".join(c for c in id_str if c.isdigit())
    if len(clean) == 10:
        num = int(clean)
        return socket.inet_ntoa(struct.pack("!I", num))
    try:
        socket.inet_aton(id_str.strip())
        return id_str.strip()
    except socket.error:
        return None


# =====================================================================
# CLIENTE DE SEÑALIZACIÓN Y RELAY (AWS)
# =====================================================================
class SignalingClient:
    def __init__(self, server_addr: str, my_id: str, local_port: int = 5555, on_relay_request=None):
        self.server_addr = server_addr
        self.my_id = "".join(c for c in my_id if c.isdigit())
        self.local_port = local_port
        self.on_relay_request = on_relay_request
        self.sock = None
        self.connected = False
        self.detected_public_ip = None
        self.stop_event = threading.Event()

    def get_server_host_port(self):
        if ":" in self.server_addr:
            h, p = self.server_addr.split(":", 1)
            return h, int(p)
        return self.server_addr, 7000

    def connect_and_register(self, on_status_change=None):
        host, port = self.get_server_host_port()
        local_ip = get_local_lan_ip()

        while not self.stop_event.is_set():
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(6.0)
                s.connect((host, port))
                s.settimeout(None)
                self.sock = s

                reg_payload = (
                    json.dumps(
                        {
                            "action": "register",
                            "id": self.my_id,
                            "port": self.local_port,
                            "local_ip": local_ip,
                        }
                    )
                    + "\n"
                )
                s.sendall(reg_payload.encode("utf-8"))

                resp_line = s.makefile().readline()
                if resp_line:
                    resp = json.loads(resp_line)
                    if resp.get("status") == "ok":
                        self.detected_public_ip = resp.get("public_ip")
                        self.connected = True
                        if on_status_change:
                            on_status_change(True)

                # Bucle de escucha permanente (Pings periódicos y Notificaciones de Relay)
                while not self.stop_event.is_set():
                    s.settimeout(30.0)
                    try:
                        line = s.makefile().readline()
                        if not line:
                            break
                        msg = json.loads(line.strip())
                        if msg.get("action") == "incoming_relay":
                            session_id = msg.get("session_id")
                            if self.on_relay_request:
                                threading.Thread(
                                    target=self.on_relay_request, args=(session_id,), daemon=True
                                ).start()
                    except socket.timeout:
                        # Enviar ping periódico
                        s.sendall(b'{"action":"ping"}\n')

            except Exception:
                self.connected = False
                if on_status_change:
                    on_status_change(False)
                time.sleep(8)
            finally:
                if self.sock:
                    try:
                        self.sock.close()
                    except Exception:
                        pass
                    self.sock = None

    def query_peer(self, target_id: str):
        clean_target = "".join(c for c in target_id if c.isdigit())
        host, port = self.get_server_host_port()

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(4.0)
            s.connect((host, port))
            query = json.dumps({"action": "lookup", "id": clean_target}) + "\n"
            s.sendall(query.encode("utf-8"))
            resp_line = s.makefile().readline()
            s.close()
            if resp_line:
                data = json.loads(resp_line)
                if data.get("status") == "ok":
                    return data
        except Exception:
            pass
        return None

    def request_relay_connection(self, target_id: str):
        """Solicita al servidor de AWS crear un túnel Relay cuando la conexión directa P2P falla."""
        host, port = self.get_server_host_port()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(10.0)
            s.connect((host, port))
            req = json.dumps({"action": "request_relay", "target_id": target_id}) + "\n"
            s.sendall(req.encode("utf-8"))
            s.settimeout(None)
            return s
        except Exception:
            return None

    def join_relay_session(self, session_id: str):
        """Conecta un nuevo socket al servidor de AWS para unirse a la sesión de Relay del Host."""
        host, port = self.get_server_host_port()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(10.0)
            s.connect((host, port))
            msg = json.dumps({"action": "join_relay", "session_id": session_id}) + "\n"
            s.sendall(msg.encode("utf-8"))
            s.settimeout(None)
            return s
        except Exception:
            return None

    def stop(self):
        self.stop_event.set()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass


# =====================================================================
# MOTOR RUST Y PROTOCOLO
# =====================================================================
def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        except Exception:
            return None
    return bytes(buf)


def load_rust_engine():
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    possible_paths = [
        os.path.join(base_dir, "screen_engine.dll"),
        os.path.join(os.path.dirname(__file__), "screen_engine.dll"),
        os.path.join(
            os.path.dirname(__file__),
            "screen_engine",
            "target",
            "release",
            "screen_engine.dll",
        ),
        "./screen_engine.dll",
    ]
    for path in possible_paths:
        if os.path.exists(path):
            try:
                engine = ctypes.CDLL(os.path.abspath(path))
                engine.capture_screen.argtypes = [
                    ctypes.c_uint8,
                    ctypes.POINTER(ctypes.c_size_t),
                    ctypes.POINTER(ctypes.c_uint32),
                    ctypes.POINTER(ctypes.c_uint32),
                ]
                engine.capture_screen.restype = ctypes.POINTER(ctypes.c_uint8)
                engine.free_screen_buffer.argtypes = [
                    ctypes.POINTER(ctypes.c_uint8),
                    ctypes.c_size_t,
                ]
                engine.free_screen_buffer.restype = None
                return engine
            except Exception:
                pass
    return None


# =====================================================================
# SERVIDOR HOST
# =====================================================================
class HostServer:
    def __init__(self, port=5555, target_fps=45, quality=70, password="", on_permission_request=None):
        self.port = port
        self.target_fps = target_fps
        self.quality = quality
        self.password = password
        self.on_permission_request = on_permission_request
        self.engine = load_rust_engine()
        self.running = False
        self.current_client_sock = None
        self.last_sent_clip = ""
        self.on_client_connect_cb = None
        self.on_client_disconnect_cb = None

    def capture_frame(self):
        if self.engine:
            out_len = ctypes.c_size_t()
            out_w = ctypes.c_uint32()
            out_h = ctypes.c_uint32()
            ptr = self.engine.capture_screen(
                self.quality,
                ctypes.byref(out_len),
                ctypes.byref(out_w),
                ctypes.byref(out_h),
            )
            if not ptr:
                return None, 0, 0
            length = out_len.value
            data = ctypes.string_at(ptr, length)
            self.engine.free_screen_buffer(ptr, length)
            return data, out_w.value, out_h.value
        return None, 0, 0

    def handle_input(self, sock):
        incoming_file = None
        incoming_file_name = ""

        while self.running:
            try:
                evt_type_raw = sock.recv(1)
                if not evt_type_raw:
                    break
                evt_type = evt_type_raw[0]

                if evt_type == 1:
                    data = recv_exact(sock, 4)
                    if not data: break
                    x, y = struct.unpack(">HH", data)
                    user32.SetCursorPos(x, y)

                elif evt_type in (2, 3):
                    btn_data = recv_exact(sock, 1)
                    if not btn_data: break
                    btn = btn_data[0]
                    is_down = evt_type == 2
                    flags = 0
                    if btn == 1: flags = 0x0002 if is_down else 0x0004
                    elif btn == 3: flags = 0x0008 if is_down else 0x0010
                    elif btn == 2: flags = 0x0020 if is_down else 0x0040
                    if flags: user32.mouse_event(flags, 0, 0, 0, 0)

                elif evt_type == 4:
                    data = recv_exact(sock, 2)
                    if not data: break
                    delta, = struct.unpack(">h", data)
                    user32.mouse_event(0x0800, 0, 0, delta, 0)

                elif evt_type in (5, 6):
                    data = recv_exact(sock, 2)
                    if not data: break
                    scancode, = struct.unpack(">H", data)
                    flags = 0x0008 | (0x0002 if evt_type == 6 else 0)
                    user32.keybd_event(0, scancode, flags, 0)

                elif evt_type == 7:
                    len_bytes = recv_exact(sock, 4)
                    if not len_bytes: break
                    txt_len, = struct.unpack(">I", len_bytes)
                    txt_bytes = recv_exact(sock, txt_len)
                    if txt_bytes:
                        remote_text = txt_bytes.decode("utf-8", errors="ignore")
                        self.last_sent_clip = remote_text
                        set_clipboard_text(remote_text)

                elif evt_type == 8:
                    sub_action = recv_exact(sock, 1)[0]
                    if sub_action == 1:
                        nl = struct.unpack(">H", recv_exact(sock, 2))[0]
                        incoming_file_name = recv_exact(sock, nl).decode("utf-8", errors="ignore")
                        _ = struct.unpack(">Q", recv_exact(sock, 8))[0]
                        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
                        dest_path = os.path.join(downloads_dir, incoming_file_name)
                        base, ext = os.path.splitext(dest_path)
                        c = 1
                        while os.path.exists(dest_path):
                            dest_path = f"{base}_{c}{ext}"
                            c += 1
                        incoming_file = open(dest_path, "wb")

                    elif sub_action == 2:
                        chunk_len, = struct.unpack(">I", recv_exact(sock, 4))
                        chunk_bytes = recv_exact(sock, chunk_len)
                        if incoming_file and chunk_bytes:
                            incoming_file.write(chunk_bytes)

                    elif sub_action == 3:
                        if incoming_file:
                            incoming_file.close()
                            incoming_file = None
                            try: winsound.MessageBeep(winsound.MB_ICONASTERISK)
                            except Exception: pass

            except Exception:
                break
        if incoming_file:
            incoming_file.close()

    def authenticate_client(self, client_sock, client_addr):
        client_sock.sendall(b"ODES\x02")
        auth_type_b = recv_exact(client_sock, 1)
        if not auth_type_b:
            return False
        auth_type = auth_type_b[0]

        if auth_type == 1:
            len_byte = recv_exact(client_sock, 1)
            if not len_byte: return False
            pwd_bytes = recv_exact(client_sock, len_byte[0])
            if not pwd_bytes: return False
            received_pwd = pwd_bytes.decode("utf-8", errors="ignore")
            if received_pwd and received_pwd == self.password:
                client_sock.sendall(b"\x01")
                return True
            else:
                client_sock.sendall(b"\x00")
                return False

        elif auth_type == 2:
            if self.on_permission_request:
                accepted = self.on_permission_request(client_addr[0])
                if accepted:
                    client_sock.sendall(b"\x01")
                    return True
                else:
                    client_sock.sendall(b"\x02")
                    return False
            else:
                client_sock.sendall(b"\x00")
                return False

        return False

    def handle_client_session(self, client_sock, addr):
        """Maneja la sesión de transmisión de un cliente (venga por LAN directa, WAN directa o Relay)."""
        try:
            client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            if not self.authenticate_client(client_sock, addr):
                client_sock.close()
                return

            self.current_client_sock = client_sock
            if self.on_client_connect_cb:
                self.on_client_connect_cb(addr)

            _, w, h = self.capture_frame()
            if w == 0:
                w = user32.GetSystemMetrics(0)
                h = user32.GetSystemMetrics(1)
            client_sock.sendall(struct.pack(">II", w, h))

            t_input = threading.Thread(
                target=self.handle_input, args=(client_sock,), daemon=True
            )
            t_input.start()

            def host_clip_sync():
                while self.running and self.current_client_sock == client_sock:
                    try:
                        cur_txt = get_clipboard_text()
                        if cur_txt and cur_txt != self.last_sent_clip and len(cur_txt) < 500000:
                            self.last_sent_clip = cur_txt
                            tb = cur_txt.encode("utf-8")
                            client_sock.sendall(b"\x01" + struct.pack(">I", len(tb)) + tb)
                    except Exception:
                        break
                    time.sleep(0.5)

            threading.Thread(target=host_clip_sync, daemon=True).start()

            frame_interval = 1.0 / self.target_fps
            while t_input.is_alive() and self.running:
                t0 = time.perf_counter()
                frame_data, _, _ = self.capture_frame()
                if frame_data:
                    client_sock.sendall(
                        b"\x00" + struct.pack(">I", len(frame_data)) + frame_data
                    )

                delay = frame_interval - (time.perf_counter() - t0)
                if delay > 0:
                    time.sleep(delay)

            self.current_client_sock = None
            if self.on_client_disconnect_cb:
                self.on_client_disconnect_cb()
            client_sock.close()

        except Exception:
            pass

    def start(self, on_client_connect=None, on_client_disconnect=None):
        self.running = True
        self.on_client_connect_cb = on_client_connect
        self.on_client_disconnect_cb = on_client_disconnect
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            server.bind(("0.0.0.0", self.port))
            server.listen(2)
        except Exception:
            return

        while self.running:
            try:
                client_sock, addr = server.accept()
                threading.Thread(target=self.handle_client_session, args=(client_sock, addr), daemon=True).start()
            except Exception:
                pass


# =====================================================================
# CLIENTE VISOR
# =====================================================================
def establish_connection_cascade(sig_client, target_code, cancel_token, update_status_cb):
    """
    Algoritmo de Conexión Inteligente Multi-Ruta:
    1. Si están en la misma red (LAN / WiFi), conecta directo por IP local (ultra veloz).
    2. Si están en redes diferentes, intenta P2P directo a la IP pública.
    3. Si el router tiene firewall y no responde, salta automáticamente al túnel Relay de AWS.
    """
    clean_target = "".join(c for c in target_code if c.isdigit())
    peer_info = sig_client.query_peer(clean_target)

    if cancel_token and cancel_token.get("cancelled"):
        return None

    candidates = []
    if peer_info:
        same_net = peer_info.get("same_network", False)
        local_ip = peer_info.get("local_ip")
        pub_ip = peer_info.get("public_ip")
        port = peer_info.get("port", 5555)

        # Si están en la misma red o casa, priorizar IP local
        if same_net and local_ip:
            candidates.append((local_ip, port, "LAN Directa"))
        if pub_ip:
            candidates.append((pub_ip, port, "WAN Directa"))
        if local_ip and (local_ip, port, "LAN Directa") not in candidates:
            candidates.append((local_ip, port, "LAN Directa"))
    else:
        direct_ip = id_to_ip_fallback(target_code)
        if direct_ip:
            candidates.append((direct_ip, 5555, "P2P Directo"))

    # Intentar candidatos directos
    for ip, port, desc in candidates:
        if cancel_token and cancel_token.get("cancelled"):
            return None
        try:
            update_status_cb(f"Conectando vía {desc}...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(2.0)  # Timeout rápido
            cancel_token["sock"] = sock
            sock.connect((ip, port))
            sock.settimeout(None)
            return sock
        except Exception:
            if cancel_token and cancel_token.get("cancelled"):
                return None
            continue

    # Si la conexión directa falló (ambos routers bloquean puertos), activar Relay de AWS
    if peer_info:
        update_status_cb("Conectando a través de Red ODesk Relay...")
        relay_sock = sig_client.request_relay_connection(clean_target)
        if relay_sock:
            cancel_token["sock"] = relay_sock
            return relay_sock

    return None


def launch_viewer(sock, password="", on_close=None, on_status_msg=None, cancel_token=None):
    import pygame
    from tkinter import filedialog
    import tkinter as tk

    close_notified = False
    def notify_close(err=None):
        nonlocal close_notified
        if not close_notified and on_close:
            close_notified = True
            on_close(err)

    try:
        sock.settimeout(15.0)
        greeting = recv_exact(sock, 5)
        if not greeting or not greeting.startswith(b"ODES"):
            if not (cancel_token and cancel_token.get("cancelled")):
                notify_close("No se pudo establecer el túnel con el equipo remoto.\n\nVerifica que ODesk esté activo en el otro equipo.")
            return

        if cancel_token and cancel_token.get("cancelled"):
            return

        if password:
            pwd_bytes = password.encode("utf-8")
            sock.sendall(bytes([1, len(pwd_bytes)]) + pwd_bytes)
        else:
            if on_status_msg:
                on_status_msg("Esperando autorización del usuario remoto...")
            sock.sendall(b"\x02")

        sock.settimeout(40.0)
        auth_status = recv_exact(sock, 1)
        sock.settimeout(None)

        if not auth_status:
            if not (cancel_token and cancel_token.get("cancelled")):
                notify_close("Conexión finalizada o rechazada por el equipo remoto.")
            return

        if auth_status == b"\x00":
            notify_close("Contraseña incorrecta.\n\nPuedes dejarla vacía para solicitar autorización manual al usuario.")
            return
        elif auth_status == b"\x02":
            notify_close("El usuario remoto ha RECHAZADO la solicitud de conexión.")
            return
        elif auth_status != b"\x01":
            notify_close("Respuesta de autorización no válida.")
            return

        dim_data = recv_exact(sock, 8)
        if not dim_data:
            return
        host_w, host_h = struct.unpack(">II", dim_data)

        pygame.init()
        win_w, win_h = min(1280, host_w), min(720, host_h)
        screen = pygame.display.set_mode((win_w, win_h), pygame.RESIZABLE)
        pygame.display.set_caption("ODesk - Control Remoto P2P (Arrastra archivos o presiona F2)")
        clock = pygame.time.Clock()

        current_frame = None
        transfer_status_msg = ""
        transfer_msg_timer = 0
        lock = threading.Lock()
        running = True
        last_local_clip = ""

        def send_file_p2p(file_path):
            nonlocal transfer_status_msg, transfer_msg_timer
            if not os.path.exists(file_path): return
            try:
                fn = os.path.basename(file_path)
                size = os.path.getsize(file_path)
                fn_b = fn.encode("utf-8")
                with lock:
                    transfer_status_msg = f"Enviando archivo: {fn} ({size // 1024} KB)..."
                    transfer_msg_timer = time.time() + 5.0

                sock.sendall(b"\x08\x01" + struct.pack(">H", len(fn_b)) + fn_b + struct.pack(">Q", size))

                with open(file_path, "rb") as f:
                    while running:
                        chunk = f.read(65536)
                        if not chunk: break
                        sock.sendall(b"\x08\x02" + struct.pack(">I", len(chunk)) + chunk)

                sock.sendall(b"\x08\x03")
                with lock:
                    transfer_status_msg = f"¡Archivo enviado con éxito a Descargas!: {fn}"
                    transfer_msg_timer = time.time() + 4.0
            except Exception as e:
                with lock:
                    transfer_status_msg = f"Error al enviar archivo: {e}"
                    transfer_msg_timer = time.time() + 4.0

        def frame_and_clip_receiver():
            nonlocal current_frame, running
            while running:
                pkt_type = recv_exact(sock, 1)
                if not pkt_type: break

                if pkt_type == b"\x00":  # Video frame
                    len_bytes = recv_exact(sock, 4)
                    if not len_bytes: break
                    frame_len, = struct.unpack(">I", len_bytes)
                    jpeg_bytes = recv_exact(sock, frame_len)
                    if not jpeg_bytes: break
                    try:
                        surf = pygame.image.load(io.BytesIO(jpeg_bytes), ".jpg")
                        with lock:
                            current_frame = surf
                    except Exception:
                        pass

                elif pkt_type == b"\x01":  # Portapapeles remoto
                    len_bytes = recv_exact(sock, 4)
                    if not len_bytes: break
                    txt_len, = struct.unpack(">I", len_bytes)
                    txt_bytes = recv_exact(sock, txt_len)
                    if txt_bytes:
                        t_str = txt_bytes.decode("utf-8", errors="ignore")
                        set_clipboard_text(t_str)

            running = False

        t_recv = threading.Thread(target=frame_and_clip_receiver, daemon=True)
        t_recv.start()

        def client_clip_sync():
            nonlocal last_local_clip
            while running:
                try:
                    cur_txt = get_clipboard_text()
                    if cur_txt and cur_txt != last_local_clip and len(cur_txt) < 500000:
                        last_local_clip = cur_txt
                        tb = cur_txt.encode("utf-8")
                        sock.sendall(b"\x07" + struct.pack(">I", len(tb)) + tb)
                except Exception:
                    break
                time.sleep(0.5)

        threading.Thread(target=client_clip_sync, daemon=True).start()

        font = pygame.font.SysFont("Arial", 16, bold=True)

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                    break

                elif event.type == pygame.DROPFILE:
                    dropped_file = event.file
                    threading.Thread(target=send_file_p2p, args=(dropped_file,), daemon=True).start()

                elif event.type == pygame.KEYDOWN and event.key == pygame.K_F2:
                    def pick_and_send():
                        tk_root = tk.Tk()
                        tk_root.withdraw()
                        tk_root.attributes("-topmost", True)
                        selected_file = filedialog.askopenfilename(title="Seleccionar archivo para enviar al equipo remoto")
                        tk_root.destroy()
                        if selected_file:
                            send_file_p2p(selected_file)
                    threading.Thread(target=pick_and_send, daemon=True).start()

                cur_w, cur_h = screen.get_size()
                scale_x = host_w / cur_w
                scale_y = host_h / cur_h

                if event.type == pygame.MOUSEMOTION:
                    hx = int(event.pos[0] * scale_x)
                    hy = int(event.pos[1] * scale_y)
                    try: sock.sendall(struct.pack(">BHH", 1, hx, hy))
                    except OSError: running = False

                elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                    is_down = event.type == pygame.MOUSEBUTTONDOWN
                    btn = event.button
                    try:
                        if btn in (1, 2, 3):
                            sock.sendall(struct.pack(">BB", 2 if is_down else 3, btn))
                        elif btn in (4, 5) and is_down:
                            delta = 120 if btn == 4 else -120
                            sock.sendall(struct.pack(">Bh", 4, delta))
                    except OSError: running = False

                elif event.type in (pygame.KEYDOWN, pygame.KEYUP):
                    is_down = event.type == pygame.KEYDOWN
                    try:
                        sock.sendall(struct.pack(">BH", 5 if is_down else 6, event.scancode))
                    except OSError: running = False

            with lock:
                if current_frame:
                    cur_w, cur_h = screen.get_size()
                    if (cur_w, cur_h) != (host_w, host_h):
                        scaled = pygame.transform.smoothscale(current_frame, (cur_w, cur_h))
                        screen.blit(scaled, (0, 0))
                    else:
                        screen.blit(current_frame, (0, 0))

                if transfer_status_msg and time.time() < transfer_msg_timer:
                    badge_surf = font.render(transfer_status_msg, True, (255, 255, 255))
                    bg_rect = pygame.Rect(15, 15, badge_surf.get_width() + 20, badge_surf.get_height() + 10)
                    pygame.draw.rect(screen, (16, 185, 129), bg_rect, border_radius=6)
                    screen.blit(badge_surf, (25, 20))

            pygame.display.flip()
            clock.tick(60)

        notify_close(None)

    except Exception as e:
        if not (cancel_token and cancel_token.get("cancelled")):
            notify_close(f"Error en la sesión remota: {e}")
    finally:
        try:
            sock.close()
        except Exception:
            pass
        try:
            pygame.quit()
        except Exception:
            pass
        notify_close(None)


# =====================================================================
# INTERFAZ GRÁFICA MODERNA (CustomTkinter)
# =====================================================================
def run_gui():
    import customtkinter as ctk
    from tkinter import messagebox

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    config = load_or_create_config()
    my_id = config["device_id"]
    signal_server_addr = config.get("signal_server", DEFAULT_SIGNAL_SERVER)

    root = ctk.CTk()
    root.title("ODesk - Control Remoto P2P (Administrador)")
    root.geometry("780x520")
    root.resizable(False, False)

    active_cancel_token = {"sock": None, "cancelled": False}
    connecting_state = [False]

    def ask_user_permission(client_ip):
        decision_event = threading.Event()
        decision_res = [False]

        def show_dialog():
            try: winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception: pass

            top = ctk.CTkToplevel(root)
            top.title("Solicitud de Control Remoto")
            top.geometry("420x240")
            top.resizable(False, False)
            top.lift()
            top.attributes("-topmost", True)
            top.grab_set()

            rx, ry = root.winfo_x(), root.winfo_y()
            top.geometry(f"+{rx + 180}+{ry + 140}")

            header_dlg = ctk.CTkFrame(top, fg_color="#f59e0b", corner_radius=0, height=36)
            header_dlg.pack(fill="x")
            ctk.CTkLabel(
                header_dlg,
                text="⚠️ SOLICITUD DE ACCESO ENTRANTE",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#000000",
            ).pack(pady=8)

            msg_frame = ctk.CTkFrame(top, fg_color="transparent")
            msg_frame.pack(fill="both", expand=True, padx=20, pady=15)

            ctk.CTkLabel(
                msg_frame,
                text="Un usuario remoto solicita conectarse\na tu pantalla.",
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color="#f8fafc",
                justify="center",
            ).pack(pady=(5, 10))

            ctk.CTkLabel(
                msg_frame,
                text="No proporcionó contraseña. ¿Deseas autorizar el control de este equipo?",
                font=ctk.CTkFont(size=11),
                text_color="#94a3b8",
                justify="center",
            ).pack()

            btn_frame = ctk.CTkFrame(top, fg_color="transparent")
            btn_frame.pack(fill="x", padx=20, pady=(0, 20))

            def on_accept():
                decision_res[0] = True
                decision_event.set()
                top.destroy()

            def on_reject():
                decision_res[0] = False
                decision_event.set()
                top.destroy()

            top.protocol("WM_DELETE_WINDOW", on_reject)

            btn_acc = ctk.CTkButton(
                btn_frame,
                text="ACEPTAR",
                fg_color="#10b981",
                hover_color="#059669",
                font=ctk.CTkFont(size=13, weight="bold"),
                height=38,
                command=on_accept,
            )
            btn_acc.pack(side="left", fill="x", expand=True, padx=(0, 8))

            btn_rej = ctk.CTkButton(
                btn_frame,
                text="RECHAZAR",
                fg_color="#ef4444",
                hover_color="#dc2626",
                font=ctk.CTkFont(size=13, weight="bold"),
                height=38,
                command=on_reject,
            )
            btn_rej.pack(side="right", fill="x", expand=True, padx=(8, 0))

        root.after(0, show_dialog)
        if decision_event.wait(timeout=35.0):
            return decision_res[0]
        return False

    # 1. Iniciar servidor local
    host_server = HostServer(
        port=config["listen_port"],
        target_fps=config["fps"],
        quality=config["quality"],
        password=config.get("password", "123456"),
        on_permission_request=ask_user_permission,
    )
    t_host = threading.Thread(target=host_server.start, daemon=True)
    t_host.start()

    # 2. UPnP en segundo plano
    def background_upnp():
        try:
            import upnp_helper
            upnp_helper.open_port(config["listen_port"])
        except Exception:
            pass

    threading.Thread(target=background_upnp, daemon=True).start()

    # 3. Callback cuando AWS solicita que este Host atienda una sesión de Relay
    def on_relay_incoming(session_id):
        relay_sock = sig_client.join_relay_session(session_id)
        if relay_sock:
            # Procesar el socket de relay como si fuera una conexión entrante directa
            host_server.handle_client_session(relay_sock, ("ODesk-Relay", 7000))

    # 4. Cliente de señalización
    sig_client = SignalingClient(signal_server_addr, my_id, config["listen_port"], on_relay_request=on_relay_incoming)

    def on_signal_status(is_connected):
        if is_connected:
            status_badge.configure(
                text="● Conectado a Servidor ODesk", text_color="#10b981"
            )
            net_info.configure(text="● Red ODesk Lista (Conexión Directa P2P)")
        else:
            status_badge.configure(
                text="● Modo Directo P2P", text_color="#f59e0b"
            )
            net_info.configure(text="Conexión Directa P2P")

    t_signal = threading.Thread(
        target=sig_client.connect_and_register, args=(on_signal_status,), daemon=True
    )
    t_signal.start()

    # ------------------ ENCABEZADO SUPERIOR ------------------
    header_frame = ctk.CTkFrame(root, corner_radius=0, fg_color="#181825")
    header_frame.pack(fill="x", padx=0, pady=0)

    title_label = ctk.CTkLabel(
        header_frame,
        text="ODesk",
        font=ctk.CTkFont(size=24, weight="bold"),
        text_color="#38bdf8",
    )
    title_label.pack(side="left", padx=25, pady=15)

    badge_admin = ctk.CTkLabel(
        header_frame,
        text="[ADMINISTRADOR]",
        font=ctk.CTkFont(size=11, weight="bold"),
        text_color="#f59e0b",
    )
    badge_admin.pack(side="left", padx=0, pady=15)

    subtitle_label = ctk.CTkLabel(
        header_frame,
        text="• Control Total P2P",
        font=ctk.CTkFont(size=13),
        text_color="#94a3b8",
    )
    subtitle_label.pack(side="left", padx=8, pady=15)

    status_badge = ctk.CTkLabel(
        header_frame,
        text="● Conectando a Servidor ODesk...",
        font=ctk.CTkFont(size=12, weight="bold"),
        text_color="#94a3b8",
    )
    status_badge.pack(side="right", padx=25, pady=15)

    # ------------------ CONTENEDOR PRINCIPAL ------------------
    main_frame = ctk.CTkFrame(root, fg_color="transparent")
    main_frame.pack(fill="both", expand=True, padx=25, pady=(15, 10))

    # Tarjeta Izquierda: Este Equipo
    card_left = ctk.CTkFrame(main_frame, corner_radius=12, fg_color="#1e1e2e")
    card_left.pack(side="left", fill="both", expand=True, padx=(0, 12))

    lbl_card1 = ctk.CTkLabel(
        card_left,
        text="Este Puesto de Trabajo",
        font=ctk.CTkFont(size=16, weight="bold"),
        text_color="#f8fafc",
    )
    lbl_card1.pack(anchor="w", padx=20, pady=(15, 2))

    lbl_desc1 = ctk.CTkLabel(
        card_left,
        text="Comparte tu código permanente. Puedes dar tu contraseña o aceptar la solicitud en pantalla.",
        font=ctk.CTkFont(size=11),
        text_color="#94a3b8",
        wraplength=280,
        justify="left",
    )
    lbl_desc1.pack(anchor="w", padx=20, pady=(0, 10))

    id_box = ctk.CTkFrame(card_left, corner_radius=8, fg_color="#11111b")
    id_box.pack(fill="x", padx=20, pady=5)

    id_label = ctk.CTkLabel(
        id_box,
        text=my_id,
        font=ctk.CTkFont(family="Consolas", size=24, weight="bold"),
        text_color="#38bdf8",
    )
    id_label.pack(pady=(8, 2))

    net_info = ctk.CTkLabel(
        id_box,
        text="Iniciando conexión segura...",
        font=ctk.CTkFont(size=11),
        text_color="#64748b",
    )
    net_info.pack(pady=(0, 8))

    def copy_id():
        root.clipboard_clear()
        root.clipboard_append(my_id)
        btn_copy.configure(text="¡Copiado!", fg_color="#059669")
        root.after(
            2000, lambda: btn_copy.configure(text="Copiar Código", fg_color="#2563eb")
        )

    btn_copy = ctk.CTkButton(
        card_left,
        text="Copiar Código",
        font=ctk.CTkFont(size=13, weight="bold"),
        fg_color="#2563eb",
        hover_color="#1d4ed8",
        height=34,
        command=copy_id,
    )
    btn_copy.pack(fill="x", padx=20, pady=(5, 12))

    # Caja de Contraseña del Host
    pwd_frame = ctk.CTkFrame(card_left, corner_radius=8, fg_color="#181825")
    pwd_frame.pack(fill="x", padx=20, pady=(0, 15))

    lbl_pwd_t = ctk.CTkLabel(
        pwd_frame,
        text="Contraseña (para acceso desatendido):",
        font=ctk.CTkFont(size=11),
        text_color="#94a3b8",
    )
    lbl_pwd_t.pack(anchor="w", padx=12, pady=(8, 2))

    pwd_subframe = ctk.CTkFrame(pwd_frame, fg_color="transparent")
    pwd_subframe.pack(fill="x", padx=12, pady=(0, 8))

    entry_my_pwd = ctk.CTkEntry(
        pwd_subframe,
        font=ctk.CTkFont(family="Consolas", size=13),
        height=30,
        show="*",
    )
    entry_my_pwd.insert(0, config.get("password", "123456"))
    entry_my_pwd.pack(side="left", fill="x", expand=True, padx=(0, 6))

    def toggle_show_pwd():
        if entry_my_pwd.cget("show") == "*":
            entry_my_pwd.configure(show="")
            btn_show_pwd.configure(text="Ocultar")
        else:
            entry_my_pwd.configure(show="*")
            btn_show_pwd.configure(text="Ver")

    btn_show_pwd = ctk.CTkButton(
        pwd_subframe,
        text="Ver",
        width=55,
        height=30,
        fg_color="#334155",
        command=toggle_show_pwd,
    )
    btn_show_pwd.pack(side="left", padx=(0, 4))

    def save_new_password():
        new_p = entry_my_pwd.get().strip()
        config["password"] = new_p
        host_server.password = new_p
        save_config(config)
        btn_save_pwd.configure(text="OK!", fg_color="#059669")
        root.after(
            1500,
            lambda: btn_save_pwd.configure(text="Guardar", fg_color="#334155"),
        )

    btn_save_pwd = ctk.CTkButton(
        pwd_subframe,
        text="Guardar",
        width=60,
        height=30,
        fg_color="#334155",
        command=save_new_password,
    )
    btn_save_pwd.pack(side="left")

    # Tarjeta Derecha: Control Remoto
    card_right = ctk.CTkFrame(main_frame, corner_radius=12, fg_color="#1e1e2e")
    card_right.pack(side="right", fill="both", expand=True, padx=(12, 0))

    lbl_card2 = ctk.CTkLabel(
        card_right,
        text="Controlar Otro Dispositivo",
        font=ctk.CTkFont(size=16, weight="bold"),
        text_color="#f8fafc",
    )
    lbl_card2.pack(anchor="w", padx=20, pady=(15, 2))

    lbl_desc2 = ctk.CTkLabel(
        card_right,
        text="Introduce el ID remoto. Si no tienes la contraseña, déjala vacía para solicitar acceso.",
        font=ctk.CTkFont(size=11),
        text_color="#94a3b8",
        wraplength=280,
        justify="left",
    )
    lbl_desc2.pack(anchor="w", padx=20, pady=(0, 10))

    entry_id = ctk.CTkEntry(
        card_right,
        placeholder_text="ID Remoto (Ej: 482 919 021)",
        font=ctk.CTkFont(family="Consolas", size=16),
        height=40,
        justify="center",
    )
    entry_id.pack(fill="x", padx=20, pady=(5, 8))

    entry_remote_pwd = ctk.CTkEntry(
        card_right,
        placeholder_text="Contraseña (Opcional)",
        font=ctk.CTkFont(size=14),
        height=36,
        show="*",
        justify="center",
    )
    entry_remote_pwd.pack(fill="x", padx=20, pady=(0, 10))

    lbl_error = ctk.CTkLabel(
        card_right, text="", font=ctk.CTkFont(size=11), text_color="#f87171"
    )
    lbl_error.pack(pady=0)

    def restore_connect_btn():
        connecting_state[0] = False
        btn_action.configure(
            text="Conectar",
            fg_color="#10b981",
            hover_color="#059669",
            state="normal"
        )

    def cancel_active_connection():
        active_cancel_token["cancelled"] = True
        if active_cancel_token["sock"]:
            try:
                active_cancel_token["sock"].close()
            except Exception:
                pass
        restore_connect_btn()
        lbl_error.configure(text="Conexión cancelada.")

    def on_btn_action_clicked():
        if connecting_state[0]:
            cancel_active_connection()
            return

        target_code = entry_id.get().strip()
        remote_pwd = entry_remote_pwd.get().strip()
        if not target_code:
            lbl_error.configure(text="Introduce un código ID.")
            return

        clean_target = "".join(c for c in target_code if c.isdigit())
        clean_my_id = "".join(c for c in my_id if c.isdigit())

        if clean_target == clean_my_id:
            lbl_error.configure(text="No es posible conectarte a tu propio equipo.")
            messagebox.showinfo(
                "Aviso ODesk",
                "⚠️ Conexión no permitida:\n\n"
                "Estás intentando conectarte a tu propio puesto de trabajo.\n"
                "Para controlar otro PC, introduce el código ID del equipo remoto."
            )
            return

        connecting_state[0] = True
        active_cancel_token["cancelled"] = False
        active_cancel_token["sock"] = None

        btn_action.configure(
            text="Cancelar",
            fg_color="#ef4444",
            hover_color="#dc2626",
            state="normal"
        )
        lbl_error.configure(text="Buscando equipo en Servidor ODesk...")

        def resolve_and_connect():
            def update_status_txt(msg):
                root.after(0, lambda: lbl_error.configure(text=msg))

            # Conexión Inteligente en Cascada (LAN -> WAN P2P -> Relay)
            sock = establish_connection_cascade(
                sig_client, target_code, active_cancel_token, update_status_txt
            )

            if active_cancel_token["cancelled"]:
                return

            if not sock:
                root.after(
                    0,
                    lambda: (
                        lbl_error.configure(
                            text="No fue posible conectar con el equipo remoto."
                        ),
                        restore_connect_btn(),
                        messagebox.showerror(
                            "Aviso ODesk",
                            "No se pudo conectar con el equipo remoto.\n\n"
                            "Verifica que el equipo remoto tenga ODesk abierto y activo."
                        )
                    ),
                )
                return

            root.after(0, lambda: lbl_error.configure(text="Estableciendo sesión remota..."))

            def on_viewer_close(err):
                restore_connect_btn()
                if err and not active_cancel_token["cancelled"]:
                    lbl_error.configure(text=str(err).splitlines()[0])
                    messagebox.showerror("Aviso ODesk", err)
                elif not active_cancel_token["cancelled"]:
                    lbl_error.configure(text="")

            launch_viewer(
                sock,
                remote_pwd,
                on_close=on_viewer_close,
                on_status_msg=update_status_txt,
                cancel_token=active_cancel_token,
            )

        threading.Thread(target=resolve_and_connect, daemon=True).start()

    btn_action = ctk.CTkButton(
        card_right,
        text="Conectar",
        font=ctk.CTkFont(size=15, weight="bold"),
        fg_color="#10b981",
        hover_color="#059669",
        height=40,
        command=on_btn_action_clicked,
    )
    btn_action.pack(fill="x", padx=20, pady=(5, 15))

    # ------------------ BARRA INFERIOR LIMPIA ------------------
    footer_frame = ctk.CTkFrame(root, fg_color="transparent")
    footer_frame.pack(fill="x", padx=25, pady=(5, 15))

    lbl_footer_info = ctk.CTkLabel(
        footer_frame,
        text="ODesk P2P • Conexión Inteligente LAN / WAN / Relay",
        font=ctk.CTkFont(size=11),
        text_color="#64748b",
    )
    lbl_footer_info.pack(side="left")

    lbl_admin_badge = ctk.CTkLabel(
        footer_frame,
        text="Control Total • Admin UIPI Activo",
        font=ctk.CTkFont(size=11, weight="bold"),
        text_color="#10b981",
    )
    lbl_admin_badge.pack(side="right")

    root.mainloop()


def main():
    ensure_admin()
    run_gui()


if __name__ == "__main__":
    main()
