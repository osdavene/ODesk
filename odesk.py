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
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

CF_UNICODETEXT = 13
CF_HDROP = 15

user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]

shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
shell32.DragQueryFileW.restype = wintypes.UINT

if hasattr(user32, "ChangeWindowMessageFilterEx"):
    user32.ChangeWindowMessageFilterEx.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.DWORD, ctypes.c_void_p]
    user32.ChangeWindowMessageFilterEx.restype = wintypes.BOOL


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
        if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
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
                ctypes.memmove(ctypes.c_void_p(p_mem), encoded, len(encoded))
                kernel32.GlobalUnlock(h_mem)
                user32.SetClipboardData(CF_UNICODETEXT, h_mem)
    except Exception:
        pass
    finally:
        user32.CloseClipboard()
    return True


def get_clipboard_files():
    """Retorna una lista de rutas de archivos si el portapapeles contiene archivos (CF_HDROP)."""
    if not user32.OpenClipboard(0):
        return []
    file_list = []
    try:
        if user32.IsClipboardFormatAvailable(CF_HDROP):
            h_drop = user32.GetClipboardData(CF_HDROP)
            if h_drop:
                num_files = shell32.DragQueryFileW(h_drop, 0xFFFFFFFF, None, 0)
                buf = ctypes.create_unicode_buffer(1024)
                for i in range(num_files):
                    if shell32.DragQueryFileW(h_drop, i, buf, 1024) > 0:
                        file_list.append(buf.value)
    except Exception:
        pass
    finally:
        user32.CloseClipboard()
    return file_list


def set_clipboard_files(file_paths):
    """Establece una lista de archivos en el portapapeles de Windows (formato CF_HDROP)."""
    if not file_paths or not user32.OpenClipboard(0):
        return False
    try:
        user32.EmptyClipboard()
        # Estructura DROPFILES: 20 bytes
        header = struct.pack("IIIII", 20, 0, 0, 0, 1)
        files_str = "\0".join(file_paths) + "\0\0"
        full_data = header + files_str.encode("utf-16le")
        h_mem = kernel32.GlobalAlloc(0x0042, len(full_data))
        if h_mem:
            p_mem = kernel32.GlobalLock(h_mem)
            if p_mem:
                ctypes.memmove(ctypes.c_void_p(p_mem), full_data, len(full_data))
                kernel32.GlobalUnlock(h_mem)
                user32.SetClipboardData(CF_HDROP, h_mem)
                return True
    except Exception:
        pass
    finally:
        user32.CloseClipboard()
    return False


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


def generate_random_password():
    return f"{random.randint(100000, 999999)}"


def load_or_create_config():
    default_cfg = {
        "device_id": f"{random.randint(100, 999)} {random.randint(100, 999)} {random.randint(100, 999)}",
        "fixed_password": "",
        "use_fixed_password": False,
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
        except Exception:
            pass

    # Generar SIEMPRE una nueva contraseña temporal aleatoria para esta ejecución
    default_cfg["dynamic_password"] = generate_random_password()

    # Si el usuario tiene seleccionada contraseña fija, se usa esa; si no, la dinámica aleatoria
    if default_cfg.get("use_fixed_password") and default_cfg.get("fixed_password"):
        default_cfg["active_password"] = default_cfg["fixed_password"]
    else:
        default_cfg["active_password"] = default_cfg["dynamic_password"]
        default_cfg["use_fixed_password"] = False

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
    def __init__(self, port=5555, target_fps=45, quality=70, password="", fixed_password="", on_permission_request=None):
        self.port = port
        self.target_fps = target_fps
        self.quality = quality
        self.password = password
        self.fixed_password = fixed_password
        self.on_permission_request = on_permission_request
        self.engine = load_rust_engine()
        self.running = False
        self.current_client_sock = None
        self.sock_send_lock = threading.Lock()
        self.last_sent_clip = ""
        self.last_sent_clip_files = []
        self.on_client_connect_cb = None
        self.on_client_disconnect_cb = None

    def safe_send_client(self, data):
        """Envío thread-safe al cliente evitando entrelazado de paquetes TCP."""
        with self.sock_send_lock:
            if self.current_client_sock:
                try:
                    self.current_client_sock.sendall(data)
                    return True
                except Exception:
                    return False
            return False

    def disconnect_current_client(self):
        """Finaliza la sesión del cliente actualmente conectado."""
        if self.current_client_sock:
            try:
                self.current_client_sock.close()
            except Exception:
                pass
            self.current_client_sock = None

    def send_file_to_client(self, file_path):
        """Transfiere un archivo desde el Host hacia el Cliente conectado."""
        if not self.current_client_sock or not os.path.exists(file_path):
            return False
        try:
            fn = os.path.basename(file_path)
            size = os.path.getsize(file_path)
            fn_b = fn.encode("utf-8")
            self.safe_send_client(
                b"\x08\x01" + struct.pack(">H", len(fn_b)) + fn_b + struct.pack(">Q", size)
            )
            with open(file_path, "rb") as f:
                while self.running and self.current_client_sock:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.safe_send_client(
                        b"\x08\x02" + struct.pack(">I", len(chunk)) + chunk
                    )
            self.safe_send_client(b"\x08\x03")
            return True
        except Exception:
            return False

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
                    sub_b = recv_exact(sock, 1)
                    if not sub_b: break
                    sub_action = sub_b[0]

                    if sub_action == 1:
                        len_b = recv_exact(sock, 2)
                        if not len_b: break
                        nl = struct.unpack(">H", len_b)[0]
                        name_b = recv_exact(sock, nl)
                        if not name_b: break
                        incoming_file_name = name_b.decode("utf-8", errors="ignore")

                        size_b = recv_exact(sock, 8)
                        if not size_b: break
                        _ = struct.unpack(">Q", size_b)[0]

                        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
                        try:
                            os.makedirs(downloads_dir, exist_ok=True)
                        except Exception:
                            pass
                        dest_path = os.path.join(downloads_dir, incoming_file_name)
                        base, ext = os.path.splitext(dest_path)
                        c = 1
                        while os.path.exists(dest_path):
                            dest_path = f"{base}_{c}{ext}"
                            c += 1
                        try:
                            incoming_file = open(dest_path, "wb")
                        except Exception:
                            incoming_file = None

                    elif sub_action == 2:
                        len_b = recv_exact(sock, 4)
                        if not len_b: break
                        chunk_len, = struct.unpack(">I", len_b)
                        chunk_bytes = recv_exact(sock, chunk_len)
                        if not chunk_bytes: break
                        if incoming_file:
                            try:
                                incoming_file.write(chunk_bytes)
                            except Exception:
                                pass

                    elif sub_action == 3:
                        if incoming_file:
                            try:
                                incoming_file.close()
                            except Exception:
                                pass
                            incoming_file = None
                            try:
                                set_clipboard_files([dest_path])
                                self.last_sent_clip_files = [dest_path]
                            except Exception:
                                pass
                            try:
                                winsound.MessageBeep(winsound.MB_ICONASTERISK)
                            except Exception:
                                pass
                            # Simular Ctrl+V en el host para pegar en la ventana activa de Windows Explorer
                            try:
                                user32.keybd_event(0x11, 0, 0, 0)
                                user32.keybd_event(0x56, 0, 0, 0)
                                user32.keybd_event(0x56, 0, 2, 0)
                                user32.keybd_event(0x11, 0, 2, 0)
                            except Exception:
                                pass

            except Exception:
                break
        if incoming_file:
            try:
                incoming_file.close()
            except Exception:
                pass

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
            # Aceptar contraseña activa (temporal) o fija predeterminada si existe
            allowed_pwds = [self.password]
            if getattr(self, "fixed_password", None):
                allowed_pwds.append(self.fixed_password)

            if received_pwd and received_pwd in allowed_pwds:
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
            self.safe_send_client(struct.pack(">II", w, h))

            t_input = threading.Thread(
                target=self.handle_input, args=(client_sock,), daemon=True
            )
            t_input.start()

            def host_clip_sync():
                while self.running and self.current_client_sock == client_sock:
                    try:
                        # 1. Sincronización de Texto
                        cur_txt = get_clipboard_text()
                        if cur_txt and cur_txt != self.last_sent_clip and len(cur_txt) < 500000:
                            self.last_sent_clip = cur_txt
                            tb = cur_txt.encode("utf-8")
                            self.safe_send_client(b"\x01" + struct.pack(">I", len(tb)) + tb)

                        # 2. Sincronización de Archivos (Servidor -> Cliente al hacer Ctrl+C en el Servidor)
                        cur_files = get_clipboard_files()
                        if cur_files and cur_files != self.last_sent_clip_files:
                            self.last_sent_clip_files = list(cur_files)
                            for fp in cur_files:
                                if os.path.exists(fp) and os.path.isfile(fp):
                                    if os.path.getsize(fp) <= 500 * 1024 * 1024:
                                        self.send_file_to_client(fp)
                    except Exception:
                        break
                    time.sleep(0.5)

            threading.Thread(target=host_clip_sync, daemon=True).start()

            frame_interval = 1.0 / self.target_fps
            while t_input.is_alive() and self.running:
                t0 = time.perf_counter()
                frame_data, _, _ = self.capture_frame()
                if frame_data:
                    self.safe_send_client(
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


def launch_viewer(sock, password="", target_code="", on_connected=None, on_close=None, on_status_msg=None, cancel_token=None):
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

        # La conexión está 100% autorizada y establecida: notificar para ocultar lanzador
        if on_connected:
            on_connected()

        pygame.init()
        win_w, win_h = min(1280, host_w), min(720, host_h)
        screen = pygame.display.set_mode((win_w, win_h), pygame.RESIZABLE)
        pygame.display.set_caption(f"ODesk - Control Remoto P2P [{target_code}]")
        clock = pygame.time.Clock()

        # Permitir Drag & Drop desde Windows Explorer aunque ODesk se ejecute como Administrador (UIPI)
        try:
            hwnd = pygame.display.get_wm_info().get("window")
            if hwnd:
                MSGFLT_ALLOW = 1
                for msg_id in (0x0233, 0x004A, 0x0049):
                    user32.ChangeWindowMessageFilterEx(hwnd, msg_id, MSGFLT_ALLOW, None)
                shell32.DragAcceptFiles(hwnd, True)
        except Exception:
            pass

        current_frame = None
        transfer_status_msg = ""
        transfer_msg_timer = 0
        lock = threading.Lock()
        running = True
        last_local_clip = ""

        # Candado atómico para envíos por socket (evita entrelazado con movimientos de ratón)
        sock_send_lock = threading.Lock()

        def safe_send(data):
            with sock_send_lock:
                try:
                    sock.sendall(data)
                    return True
                except Exception:
                    return False

        # Cola de transferencia secuencial de archivos (Cliente -> Servidor)
        file_send_queue = []
        file_send_queue_lock = threading.Lock()
        is_sending_file = False

        def process_file_send_queue():
            nonlocal is_sending_file, transfer_status_msg, transfer_msg_timer
            while running:
                target_f = None
                with file_send_queue_lock:
                    if file_send_queue:
                        target_f = file_send_queue.pop(0)
                    else:
                        is_sending_file = False
                        break

                if not target_f or not os.path.exists(target_f):
                    continue

                try:
                    fn = os.path.basename(target_f)
                    size = os.path.getsize(target_f)
                    fn_b = fn.encode("utf-8")
                    with lock:
                        transfer_status_msg = f"Enviando archivo: {fn} ({size // 1024} KB)..."
                        transfer_msg_timer = time.time() + 8.0

                    # 1. Cabecera (Acción 8, sub-acción 1)
                    safe_send(b"\x08\x01" + struct.pack(">H", len(fn_b)) + fn_b + struct.pack(">Q", size))

                    # 2. Envío de fragmentos (Acción 8, sub-acción 2)
                    with open(target_f, "rb") as f:
                        while running:
                            chunk = f.read(65536)
                            if not chunk:
                                break
                            safe_send(b"\x08\x02" + struct.pack(">I", len(chunk)) + chunk)

                    # 3. Fin de archivo (Acción 8, sub-acción 3)
                    safe_send(b"\x08\x03")

                    with lock:
                        transfer_status_msg = f"¡Archivo enviado con éxito al servidor!: {fn}"
                        transfer_msg_timer = time.time() + 5.0
                except Exception as e:
                    with lock:
                        transfer_status_msg = f"Error al enviar archivo: {e}"
                        transfer_msg_timer = time.time() + 4.0

        def queue_file_for_send(file_path):
            nonlocal is_sending_file
            if not file_path or not os.path.exists(file_path):
                return
            with file_send_queue_lock:
                if file_path not in file_send_queue:
                    file_send_queue.append(file_path)
                if not is_sending_file:
                    is_sending_file = True
                    threading.Thread(target=process_file_send_queue, daemon=True).start()

        def pick_and_send():
            tk_root = tk.Tk()
            tk_root.withdraw()
            tk_root.attributes("-topmost", True)
            selected_files = filedialog.askopenfilenames(title="Seleccionar archivos para transferir al equipo remoto")
            tk_root.destroy()
            for sf in selected_files:
                queue_file_for_send(sf)

        def frame_and_clip_receiver():
            nonlocal current_frame, running, transfer_status_msg, transfer_msg_timer
            c_inc_file = None
            c_dest = ""
            inc_fn = ""

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

                elif pkt_type == b"\x08":  # Archivo entrante transferido desde el Host
                    sub_b = recv_exact(sock, 1)
                    if not sub_b: break
                    sub_action = sub_b[0]

                    if sub_action == 1:
                        len_b = recv_exact(sock, 2)
                        if not len_b: break
                        nl = struct.unpack(">H", len_b)[0]
                        name_b = recv_exact(sock, nl)
                        if not name_b: break
                        inc_fn = name_b.decode("utf-8", errors="ignore")

                        size_b = recv_exact(sock, 8)
                        if not size_b: break
                        _ = struct.unpack(">Q", size_b)[0]

                        d_dir = os.path.join(os.path.expanduser("~"), "Downloads")
                        try: os.makedirs(d_dir, exist_ok=True)
                        except Exception: pass
                        c_dest = os.path.join(d_dir, inc_fn)
                        base, ext = os.path.splitext(c_dest)
                        c = 1
                        while os.path.exists(c_dest):
                            c_dest = f"{base}_{c}{ext}"
                            c += 1
                        try:
                            c_inc_file = open(c_dest, "wb")
                        except Exception:
                            c_inc_file = None
                        with lock:
                            transfer_status_msg = f"Copiando archivo del servidor: {inc_fn}..."
                            transfer_msg_timer = time.time() + 6.0

                    elif sub_action == 2:
                        len_b = recv_exact(sock, 4)
                        if not len_b: break
                        c_len, = struct.unpack(">I", len_b)
                        c_chunk = recv_exact(sock, c_len)
                        if not c_chunk: break
                        if c_inc_file:
                            try:
                                c_inc_file.write(c_chunk)
                            except Exception:
                                pass

                    elif sub_action == 3:
                        if c_inc_file:
                            try:
                                c_inc_file.close()
                            except Exception:
                                pass
                            c_inc_file = None
                            try:
                                set_clipboard_files([c_dest])
                            except Exception:
                                pass
                            try:
                                winsound.MessageBeep(winsound.MB_ICONASTERISK)
                            except Exception:
                                pass
                            with lock:
                                transfer_status_msg = f"📋 ¡Archivo copiado del servidor!: {inc_fn} (Listo para Pegar en tu PC)"
                                transfer_msg_timer = time.time() + 6.0

            if c_inc_file:
                try: c_inc_file.close()
                except Exception: pass
            running = False

        t_recv = threading.Thread(target=frame_and_clip_receiver, daemon=True)
        t_recv.start()

        def client_clip_sync():
            nonlocal last_local_clip
            last_client_clip_files = []
            while running:
                try:
                    # 1. Sincronización de Texto
                    cur_txt = get_clipboard_text()
                    if cur_txt and cur_txt != last_local_clip and len(cur_txt) < 500000:
                        last_local_clip = cur_txt
                        tb = cur_txt.encode("utf-8")
                        safe_send(b"\x07" + struct.pack(">I", len(tb)) + tb)

                    # 2. Sincronización de Archivos al copiar en el Cliente
                    cur_files = get_clipboard_files()
                    if cur_files and cur_files != last_client_clip_files:
                        last_client_clip_files = list(cur_files)
                        for fp in cur_files:
                            if os.path.exists(fp) and os.path.isfile(fp):
                                if os.path.getsize(fp) <= 500 * 1024 * 1024:
                                    queue_file_for_send(fp)
                except Exception:
                    break
                time.sleep(0.5)

        threading.Thread(target=client_clip_sync, daemon=True).start()

        font_bar = pygame.font.SysFont("Segoe UI", 12, bold=True)
        font_badge = pygame.font.SysFont("Segoe UI", 13, bold=True)

        tb_w, tb_h = 470, 32
        tb_y = 6

        while running:
            cur_w, cur_h = screen.get_size()
            scale_x = host_w / cur_w
            scale_y = host_h / cur_h

            tb_x = (cur_w - tb_w) // 2
            tb_rect = pygame.Rect(tb_x, tb_y, tb_w, tb_h)
            btn_send_rect = pygame.Rect(tb_x + 195, tb_y + 3, 155, 26)
            btn_exit_rect = pygame.Rect(tb_x + 358, tb_y + 3, 104, 26)

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                    break

                elif event.type == pygame.DROPFILE:
                    dropped_file = event.file
                    queue_file_for_send(dropped_file)

                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_F2:
                        threading.Thread(target=pick_and_send, daemon=True).start()
                        continue
                    elif event.key == pygame.K_v and (pygame.key.get_mods() & pygame.KMOD_CTRL):
                        # Detectar si hay archivos en el portapapeles local de Windows
                        clip_files = get_clipboard_files()
                        if clip_files:
                            for cf in clip_files:
                                queue_file_for_send(cf)
                            continue

                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if tb_rect.collidepoint(event.pos):
                        if btn_send_rect.collidepoint(event.pos):
                            threading.Thread(target=pick_and_send, daemon=True).start()
                        elif btn_exit_rect.collidepoint(event.pos):
                            running = False
                            break
                        continue

                    btn = event.button
                    try:
                        if btn in (1, 2, 3):
                            safe_send(struct.pack(">BB", 2, btn))
                        elif btn in (4, 5):
                            delta = 120 if btn == 4 else -120
                            safe_send(struct.pack(">Bh", 4, delta))
                    except OSError:
                        running = False

                elif event.type == pygame.MOUSEBUTTONUP:
                    if tb_rect.collidepoint(event.pos):
                        continue
                    btn = event.button
                    try:
                        if btn in (1, 2, 3):
                            safe_send(struct.pack(">BB", 3, btn))
                    except OSError:
                        running = False

                elif event.type == pygame.MOUSEMOTION:
                    if tb_rect.collidepoint(event.pos):
                        continue
                    hx = int(event.pos[0] * scale_x)
                    hy = int(event.pos[1] * scale_y)
                    try:
                        safe_send(struct.pack(">BHH", 1, hx, hy))
                    except OSError:
                        running = False

                elif event.type in (pygame.KEYDOWN, pygame.KEYUP):
                    is_down = event.type == pygame.KEYDOWN
                    try:
                        safe_send(struct.pack(">BH", 5 if is_down else 6, event.scancode))
                    except OSError:
                        running = False

            with lock:
                if current_frame:
                    if (cur_w, cur_h) != (host_w, host_h):
                        scaled = pygame.transform.smoothscale(current_frame, (cur_w, cur_h))
                        screen.blit(scaled, (0, 0))
                    else:
                        screen.blit(current_frame, (0, 0))

                # Barra superior elegante flotante
                m_pos = pygame.mouse.get_pos()
                pygame.draw.rect(screen, (24, 24, 37), tb_rect, border_radius=6)
                pygame.draw.rect(screen, (37, 99, 235), tb_rect, width=1, border_radius=6)

                lbl_conn = font_bar.render(f"● ODesk: {target_code}", True, (56, 189, 248))
                screen.blit(lbl_conn, (tb_x + 12, tb_y + 8))

                s_hover = btn_send_rect.collidepoint(m_pos)
                pygame.draw.rect(screen, (37, 99, 235) if not s_hover else (29, 78, 216), btn_send_rect, border_radius=5)
                lbl_btn_s = font_bar.render("📁 Enviar Archivos (F2)", True, (255, 255, 255))
                screen.blit(lbl_btn_s, (btn_send_rect.x + (btn_send_rect.w - lbl_btn_s.get_width()) // 2, btn_send_rect.y + 5))

                e_hover = btn_exit_rect.collidepoint(m_pos)
                pygame.draw.rect(screen, (239, 68, 68) if not e_hover else (220, 38, 38), btn_exit_rect, border_radius=5)
                lbl_btn_e = font_bar.render("❌ Desconectar", True, (255, 255, 255))
                screen.blit(lbl_btn_e, (btn_exit_rect.x + (btn_exit_rect.w - lbl_btn_e.get_width()) // 2, btn_exit_rect.y + 5))

                # Toast de notificación de transferencia
                if transfer_status_msg and time.time() < transfer_msg_timer:
                    badge_surf = font_badge.render(transfer_status_msg, True, (255, 255, 255))
                    bg_rect = pygame.Rect(20, cur_h - 45, badge_surf.get_width() + 24, badge_surf.get_height() + 10)
                    pygame.draw.rect(screen, (16, 185, 129), bg_rect, border_radius=6)
                    screen.blit(badge_surf, (32, cur_h - 40))

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

    # 1. Iniciar servidor local y Widget Flotante de Sesión Activa
    import tkinter as tk
    from tkinter import filedialog

    host_session_top = [None]

    def show_host_session_widget(addr):
        # Minimizar ventana principal del anfitrión para no obstruir la pantalla capturada
        root.iconify()

        if host_session_top[0]:
            try:
                host_session_top[0].destroy()
            except Exception:
                pass

        top = ctk.CTkToplevel(root)
        host_session_top[0] = top
        top.title("Sesión ODesk")
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(fg_color="#181825")

        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        w, h = 380, 88
        x = screen_w - w - 24
        y = screen_h - h - 70
        top.geometry(f"{w}x{h}+{x}+{y}")

        box = ctk.CTkFrame(
            top, corner_radius=10, fg_color="#181825", border_width=2, border_color="#10b981"
        )
        box.pack(fill="both", expand=True)

        info_frame = ctk.CTkFrame(box, fg_color="transparent")
        info_frame.pack(side="left", padx=(16, 8), pady=12, fill="y")

        ctk.CTkLabel(
            info_frame,
            text="● Conexión ODesk Activa",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#10b981",
            anchor="w",
        ).pack(anchor="w", pady=(2, 2))

        ctk.CTkLabel(
            info_frame,
            text="Control remoto en curso...",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#f1f5f9",
            anchor="w",
        ).pack(anchor="w")

        btn_box = ctk.CTkFrame(box, fg_color="transparent")
        btn_box.pack(side="right", padx=(8, 16), pady=12)

        def on_send_file():
            def pick():
                tk_temp = tk.Tk()
                tk_temp.withdraw()
                tk_temp.attributes("-topmost", True)
                files = filedialog.askopenfilenames(
                    title="Seleccionar archivos para enviar al cliente conectado"
                )
                tk_temp.destroy()
                for fp in files:
                    threading.Thread(
                        target=host_server.send_file_to_client, args=(fp,), daemon=True
                    ).start()

            threading.Thread(target=pick, daemon=True).start()

        def on_end_session():
            host_server.disconnect_current_client()
            hide_host_session_widget()

        btn_send = ctk.CTkButton(
            btn_box,
            text="📁 Enviar",
            width=80,
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=on_send_file,
        )
        btn_send.pack(pady=(0, 4))

        btn_end = ctk.CTkButton(
            btn_box,
            text="Finalizar",
            width=80,
            height=28,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#ef4444",
            hover_color="#dc2626",
            command=on_end_session,
        )
        btn_end.pack()

    def hide_host_session_widget():
        if host_session_top[0]:
            try:
                host_session_top[0].destroy()
            except Exception:
                pass
            host_session_top[0] = None
        root.deiconify()

    host_server = HostServer(
        port=config["listen_port"],
        target_fps=config["fps"],
        quality=config["quality"],
        password=config["active_password"],
        fixed_password=config.get("fixed_password", ""),
        on_permission_request=ask_user_permission,
    )
    t_host = threading.Thread(
        target=host_server.start,
        args=(
            lambda addr: root.after(0, lambda: show_host_session_widget(addr)),
            lambda: root.after(0, hide_host_session_widget),
        ),
        daemon=True,
    )
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
        text="Contraseña de este puesto:",
        font=ctk.CTkFont(size=11, weight="bold"),
        text_color="#94a3b8",
    )
    lbl_pwd_t.pack(anchor="w", padx=12, pady=(6, 2))

    pwd_subframe = ctk.CTkFrame(pwd_frame, fg_color="transparent")
    pwd_subframe.pack(fill="x", padx=12, pady=(0, 4))

    entry_my_pwd = ctk.CTkEntry(
        pwd_subframe,
        font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
        height=32,
        show="",
    )
    entry_my_pwd.insert(0, config["active_password"])
    entry_my_pwd.pack(side="left", fill="x", expand=True, padx=(0, 4))

    def on_refresh_dynamic_pwd():
        new_dyn = generate_random_password()
        config["dynamic_password"] = new_dyn
        if not chk_fixed_var.get():
            config["active_password"] = new_dyn
            host_server.password = new_dyn
            entry_my_pwd.delete(0, "end")
            entry_my_pwd.insert(0, new_dyn)
            lbl_pwd_status.configure(
                text="● Nueva contraseña temporal generada",
                text_color="#38bdf8",
            )

    btn_refresh = ctk.CTkButton(
        pwd_subframe,
        text="🔄",
        width=34,
        height=32,
        font=ctk.CTkFont(size=13),
        fg_color="#334155",
        hover_color="#475569",
        command=on_refresh_dynamic_pwd,
    )
    btn_refresh.pack(side="left", padx=(0, 4))

    def toggle_show_pwd():
        if entry_my_pwd.cget("show") == "*":
            entry_my_pwd.configure(show="")
            btn_show_pwd.configure(text="Ocultar")
        else:
            entry_my_pwd.configure(show="*")
            btn_show_pwd.configure(text="Ver")

    btn_show_pwd = ctk.CTkButton(
        pwd_subframe,
        text="Ocultar",
        width=58,
        height=32,
        fg_color="#334155",
        hover_color="#475569",
        command=toggle_show_pwd,
    )
    btn_show_pwd.pack(side="left", padx=(0, 4))

    def on_save_fixed():
        new_val = entry_my_pwd.get().strip()
        if not new_val:
            return
        config["fixed_password"] = new_val
        config["use_fixed_password"] = True
        config["active_password"] = new_val
        host_server.password = new_val
        host_server.fixed_password = new_val
        chk_fixed_var.set(True)
        save_config({
            "device_id": config["device_id"],
            "fixed_password": new_val,
            "use_fixed_password": True,
            "signal_server": config.get("signal_server", DEFAULT_SIGNAL_SERVER),
            "listen_port": config.get("listen_port", 5555),
            "fps": config.get("fps", 45),
            "quality": config.get("quality", 70),
        })
        lbl_pwd_status.configure(
            text="✓ Contraseña fija guardada en odesk_config.json",
            text_color="#10b981",
        )
        btn_save_pwd.configure(text="¡OK!", fg_color="#059669")
        root.after(1500, lambda: btn_save_pwd.configure(text="Guardar", fg_color="#2563eb"))

    btn_save_pwd = ctk.CTkButton(
        pwd_subframe,
        text="Guardar",
        width=62,
        height=32,
        fg_color="#2563eb",
        hover_color="#1d4ed8",
        command=on_save_fixed,
    )
    btn_save_pwd.pack(side="left")

    def on_toggle_fixed():
        is_fixed = chk_fixed_var.get()
        config["use_fixed_password"] = is_fixed
        if is_fixed:
            fixed_val = config.get("fixed_password", "") or entry_my_pwd.get().strip()
            config["fixed_password"] = fixed_val
            config["active_password"] = fixed_val
            host_server.password = fixed_val
            host_server.fixed_password = fixed_val
            entry_my_pwd.delete(0, "end")
            entry_my_pwd.insert(0, fixed_val)
            lbl_pwd_status.configure(
                text="✓ Contraseña fija activa (guardada en config)",
                text_color="#10b981",
            )
        else:
            dyn_val = generate_random_password()
            config["dynamic_password"] = dyn_val
            config["active_password"] = dyn_val
            host_server.password = dyn_val
            entry_my_pwd.delete(0, "end")
            entry_my_pwd.insert(0, dyn_val)
            lbl_pwd_status.configure(
                text="● Contraseña temporal (cambia en cada inicio)",
                text_color="#38bdf8",
            )
        save_config({
            "device_id": config["device_id"],
            "fixed_password": config.get("fixed_password", ""),
            "use_fixed_password": is_fixed,
            "signal_server": config.get("signal_server", DEFAULT_SIGNAL_SERVER),
            "listen_port": config.get("listen_port", 5555),
            "fps": config.get("fps", 45),
            "quality": config.get("quality", 70),
        })

    chk_fixed_var = ctk.BooleanVar(value=config.get("use_fixed_password", False))
    chk_fixed = ctk.CTkCheckBox(
        pwd_frame,
        text="Predeterminada fija (guardar en config)",
        variable=chk_fixed_var,
        font=ctk.CTkFont(size=11),
        text_color="#cbd5e1",
        command=on_toggle_fixed,
    )
    chk_fixed.pack(anchor="w", padx=12, pady=(2, 2))

    lbl_pwd_status = ctk.CTkLabel(
        pwd_frame,
        text="✓ Contraseña fija guardada en odesk_config.json" if config.get("use_fixed_password") else "● Contraseña temporal (cambia en cada inicio)",
        font=ctk.CTkFont(size=10, weight="bold"),
        text_color="#10b981" if config.get("use_fixed_password") else "#38bdf8",
    )
    lbl_pwd_status.pack(anchor="w", padx=12, pady=(0, 6))

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

            def on_viewer_connected():
                # Ocultar la ventana del lanzador SOLO cuando la conexión remota fue autorizada
                root.after(0, root.withdraw)

            def on_viewer_close(err):
                # Restaurar ventana del lanzador al finalizar la sesión
                root.after(0, root.deiconify)
                root.after(0, restore_connect_btn)
                if err and not active_cancel_token["cancelled"]:
                    root.after(0, lambda: (
                        lbl_error.configure(text=str(err).splitlines()[0]),
                        messagebox.showerror("Aviso ODesk", err)
                    ))
                elif not active_cancel_token["cancelled"]:
                    root.after(0, lambda: lbl_error.configure(text=""))

            launch_viewer(
                sock,
                remote_pwd,
                target_code=target_code,
                on_connected=on_viewer_connected,
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
