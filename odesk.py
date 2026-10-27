"""
ODesk - Herramienta de escritorio remoto moderna, ligera y P2P (Python + Rust).
Diseño visual estilo AnyDesk / RustDesk con CustomTkinter.
Soporte para:
- Autenticación con contraseña (acceso desatendido).
- Ejecución obligatoria con privilegios de Administrador (Total Control).
- Integración en binario único .exe sin consola negra de fondo.
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


# =====================================================================
# ELEVACIÓN A ADMINISTRADOR (UAC) & SOPORTE DPI
# =====================================================================
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def ensure_admin():
    """Fuerza la elevación de privilegios a Administrador para control total (UIPI)."""
    if not is_admin():
        try:
            if getattr(sys, "frozen", False):
                # Ejecutando como .exe
                ret = ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", sys.executable, " ".join(sys.argv[1:]), None, 1
                )
            else:
                # Ejecutando como script .py
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


def load_or_create_config():
    """Carga o genera una configuración persistente en APPDATA."""
    default_cfg = {
        "device_id": f"{random.randint(100, 999)} {random.randint(100, 999)} {random.randint(100, 999)}",
        "password": f"{random.randint(100000, 999999)}",  # Contraseña inicial de 6 dígitos
        "signal_server": "18.191.199.104:7000",
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


# =====================================================================
# MATEMÁTICA DEL ID DIRECTO (Fallback P2P sin servidor)
# =====================================================================
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
# CLIENTE DE SEÑALIZACIÓN WAN (Rendezvous Client)
# =====================================================================
class SignalingClient:
    def __init__(self, server_addr: str, my_id: str, local_port: int = 5555):
        self.server_addr = server_addr
        self.my_id = "".join(c for c in my_id if c.isdigit())
        self.local_port = local_port
        self.sock = None
        self.connected = False
        self.public_ip = None
        self.stop_event = threading.Event()

    def connect_and_register(self, on_status_change=None):
        if ":" in self.server_addr:
            host, port_s = self.server_addr.split(":", 1)
            port = int(port_s)
        else:
            host, port = self.server_addr, 7000

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
                        }
                    )
                    + "\n"
                )
                s.sendall(reg_payload.encode("utf-8"))

                resp_line = s.makefile().readline()
                if resp_line:
                    resp = json.loads(resp_line)
                    self.public_ip = resp.get("public_ip")
                    self.connected = True
                    if on_status_change:
                        on_status_change(True, self.public_ip)

                while not self.stop_event.is_set():
                    time.sleep(25)
                    s.sendall(b'{"action":"ping"}\n')

            except Exception:
                self.connected = False
                if on_status_change:
                    on_status_change(False, None)
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
        if ":" in self.server_addr:
            host, port_s = self.server_addr.split(":", 1)
            port = int(port_s)
        else:
            host, port = self.server_addr, 7000

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
                    return data.get("ip"), data.get("port")
        except Exception:
            pass
        return None, None

    def stop(self):
        self.stop_event.set()
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass


# =====================================================================
# UTILIDADES DE RED Y MOTOR EN RUST
# =====================================================================
def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def load_rust_engine():
    # Incluir sys._MEIPASS para cuando esté empaquetado en PyInstaller
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
# SERVIDOR HOST CON AUTENTICACIÓN
# =====================================================================
class HostServer:
    def __init__(self, port=5555, target_fps=45, quality=70, password=""):
        self.port = port
        self.target_fps = target_fps
        self.quality = quality
        self.password = password
        self.engine = load_rust_engine()
        self.running = False
        self.user32 = ctypes.windll.user32

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
        while self.running:
            try:
                evt_type_raw = sock.recv(1)
                if not evt_type_raw:
                    break
                evt_type = evt_type_raw[0]

                if evt_type == 1:  # Move
                    data = recv_exact(sock, 4)
                    if not data:
                        break
                    x, y = struct.unpack(">HH", data)
                    self.user32.SetCursorPos(x, y)

                elif evt_type in (2, 3):  # Click
                    btn_data = recv_exact(sock, 1)
                    if not btn_data:
                        break
                    btn = btn_data[0]
                    is_down = evt_type == 2
                    flags = 0
                    if btn == 1:
                        flags = 0x0002 if is_down else 0x0004
                    elif btn == 3:
                        flags = 0x0008 if is_down else 0x0010
                    elif btn == 2:
                        flags = 0x0020 if is_down else 0x0040
                    if flags:
                        self.user32.mouse_event(flags, 0, 0, 0, 0)

                elif evt_type == 4:  # Wheel
                    data = recv_exact(sock, 2)
                    if not data:
                        break
                    delta, = struct.unpack(">h", data)
                    self.user32.mouse_event(0x0800, 0, 0, delta, 0)

                elif evt_type in (5, 6):  # Key (Hardware Scancode)
                    data = recv_exact(sock, 2)
                    if not data:
                        break
                    scancode, = struct.unpack(">H", data)
                    flags = 0x0008 | (0x0002 if evt_type == 6 else 0)
                    self.user32.keybd_event(0, scancode, flags, 0)

            except Exception:
                break

    def authenticate_client(self, client_sock):
        """Protocolo de autenticación: Verifica la contraseña antes de transmitir video."""
        # Enviar Magic y flag de contraseña requerida: b"ODES" + [1 if password else 0]
        req_flag = 1 if self.password else 0
        client_sock.sendall(b"ODES" + bytes([req_flag]))

        if req_flag == 1:
            len_byte = recv_exact(client_sock, 1)
            if not len_byte:
                return False
            pwd_len = len_byte[0]
            pwd_bytes = recv_exact(client_sock, pwd_len)
            if not pwd_bytes:
                return False
            received_pwd = pwd_bytes.decode("utf-8", errors="ignore")

            if received_pwd != self.password:
                # Fallo de autenticación
                client_sock.sendall(b"\x00")
                return False

        # Autenticación exitosa
        client_sock.sendall(b"\x01")
        return True

    def start(self, on_client_connect=None, on_client_disconnect=None):
        self.running = True
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
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                # Validar contraseña del cliente
                if not self.authenticate_client(client_sock):
                    client_sock.close()
                    continue

                if on_client_connect:
                    on_client_connect(addr)

                _, w, h = self.capture_frame()
                if w == 0:
                    w = self.user32.GetSystemMetrics(0)
                    h = self.user32.GetSystemMetrics(1)
                client_sock.sendall(struct.pack(">II", w, h))

                t_input = threading.Thread(
                    target=self.handle_input, args=(client_sock,), daemon=True
                )
                t_input.start()

                frame_interval = 1.0 / self.target_fps
                while t_input.is_alive() and self.running:
                    t0 = time.perf_counter()
                    frame_data, _, _ = self.capture_frame()
                    if frame_data:
                        client_sock.sendall(
                            struct.pack(">I", len(frame_data)) + frame_data
                        )

                    delay = frame_interval - (time.perf_counter() - t0)
                    if delay > 0:
                        time.sleep(delay)

                if on_client_disconnect:
                    on_client_disconnect()
                client_sock.close()

            except Exception:
                pass


# =====================================================================
# CLIENTE VISOR (Lanzador Pygame con Autenticación)
# =====================================================================
def launch_viewer(host_ip, port=5555, password="", on_close=None):
    import pygame

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        sock.connect((host_ip, port))
    except Exception as e:
        if on_close:
            on_close(f"No se pudo conectar a {host_ip}:{port}\n({e})")
        return

    # 1. Leer saludo y requerimiento de autenticación
    greeting = recv_exact(sock, 5)
    if not greeting or not greeting.startswith(b"ODES"):
        if on_close:
            on_close("Protocolo de servidor no reconocido.")
        sock.close()
        return

    req_flag = greeting[4]
    if req_flag == 1:
        # Enviar contraseña: [len (1B)][pwd_bytes]
        pwd_bytes = password.encode("utf-8")
        sock.sendall(bytes([len(pwd_bytes)]) + pwd_bytes)

    # 2. Leer estado de autenticación (1 byte)
    auth_status = recv_exact(sock, 1)
    if not auth_status or auth_status != b"\x01":
        sock.close()
        if on_close:
            on_close("Contraseña incorrecta. Acceso denegado por el Host.")
        return

    # 3. Leer resolución
    dim_data = recv_exact(sock, 8)
    if not dim_data:
        if on_close:
            on_close("El servidor cerró la conexión prematuramente.")
        sock.close()
        return
    host_w, host_h = struct.unpack(">II", dim_data)

    pygame.init()
    win_w, win_h = min(1280, host_w), min(720, host_h)
    screen = pygame.display.set_mode((win_w, win_h), pygame.RESIZABLE)
    pygame.display.set_caption(f"ODesk - Controlando [{host_ip}] (Modo Administrador)")
    clock = pygame.time.Clock()

    current_frame = None
    lock = threading.Lock()
    running = True

    def frame_receiver():
        nonlocal current_frame, running
        while running:
            len_bytes = recv_exact(sock, 4)
            if not len_bytes:
                break
            frame_len, = struct.unpack(">I", len_bytes)
            jpeg_bytes = recv_exact(sock, frame_len)
            if not jpeg_bytes:
                break
            try:
                surf = pygame.image.load(io.BytesIO(jpeg_bytes), ".jpg")
                with lock:
                    current_frame = surf
            except Exception:
                pass
        running = False

    t_recv = threading.Thread(target=frame_receiver, daemon=True)
    t_recv.start()

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                break

            cur_w, cur_h = screen.get_size()
            scale_x = host_w / cur_w
            scale_y = host_h / cur_h

            if event.type == pygame.MOUSEMOTION:
                hx = int(event.pos[0] * scale_x)
                hy = int(event.pos[1] * scale_y)
                try:
                    sock.sendall(struct.pack(">BHH", 1, hx, hy))
                except OSError:
                    running = False

            elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                is_down = event.type == pygame.MOUSEBUTTONDOWN
                btn = event.button
                try:
                    if btn in (1, 2, 3):
                        sock.sendall(struct.pack(">BB", 2 if is_down else 3, btn))
                    elif btn in (4, 5) and is_down:
                        delta = 120 if btn == 4 else -120
                        sock.sendall(struct.pack(">Bh", 4, delta))
                except OSError:
                    running = False

            elif event.type in (pygame.KEYDOWN, pygame.KEYUP):
                is_down = event.type == pygame.KEYDOWN
                try:
                    sock.sendall(
                        struct.pack(">BH", 5 if is_down else 6, event.scancode)
                    )
                except OSError:
                    running = False

        with lock:
            if current_frame:
                cur_w, cur_h = screen.get_size()
                if (cur_w, cur_h) != (host_w, host_h):
                    scaled = pygame.transform.smoothscale(
                        current_frame, (cur_w, cur_h)
                    )
                    screen.blit(scaled, (0, 0))
                else:
                    screen.blit(current_frame, (0, 0))

        pygame.display.flip()
        clock.tick(60)

    sock.close()
    pygame.quit()
    if on_close:
        on_close(None)


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
    signal_server_addr = config.get("signal_server", "18.191.199.104:7000")

    root = ctk.CTk()
    root.title("ODesk - Control Remoto P2P (Administrador)")
    root.geometry("780x560")
    root.resizable(False, False)

    # 1. Iniciar servidor local
    host_server = HostServer(
        port=config["listen_port"],
        target_fps=config["fps"],
        quality=config["quality"],
        password=config.get("password", "123456"),
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

    # 3. Cliente de señalización
    sig_client = SignalingClient(signal_server_addr, my_id, config["listen_port"])

    def on_signal_status(is_connected, pub_ip):
        if is_connected:
            status_badge.configure(
                text="● Conectado a Servidor WAN (AWS)", text_color="#10b981"
            )
            net_info.configure(text=f"IP Pública WAN detectada: {pub_ip}")
        else:
            status_badge.configure(
                text="● Modo P2P Local (Sin Servidor WAN)", text_color="#f59e0b"
            )
            net_info.configure(text="Servidor WAN no alcanzado. Usando modo directo.")

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
        text="● Conectando a Señalización...",
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
        text="Comparte tu código permanente y contraseña para acceso remoto.",
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
        text="Detectando estado de red...",
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
        text="Contraseña de este equipo:",
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
        text="Introduce el código ID del equipo remoto y su contraseña de acceso.",
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
        placeholder_text="Contraseña Remota",
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

    def on_connect_clicked():
        target_code = entry_id.get().strip()
        remote_pwd = entry_remote_pwd.get().strip()
        if not target_code:
            lbl_error.configure(text="Introduce un código ID.")
            return

        lbl_error.configure(text="Localizando equipo en AWS...")
        btn_connect.configure(state="disabled", text="Buscando...")

        def resolve_and_connect():
            target_ip, target_port = sig_client.query_peer(target_code)

            if not target_ip:
                target_ip = id_to_ip_fallback(target_code)
                target_port = 5555

            if not target_ip:
                root.after(
                    0,
                    lambda: (
                        lbl_error.configure(
                            text="Equipo no encontrado o fuera de línea."
                        ),
                        btn_connect.configure(state="normal", text="Conectar"),
                    ),
                )
                return

            root.after(
                0,
                lambda: (
                    lbl_error.configure(text="Conectando..."),
                    btn_connect.configure(
                        state="disabled", text="Transmitiendo..."
                    ),
                ),
            )

            def on_viewer_close(err):
                btn_connect.configure(state="normal", text="Conectar")
                if err:
                    lbl_error.configure(text=str(err))
                    messagebox.showerror("Aviso ODesk", err)
                else:
                    lbl_error.configure(text="")

            launch_viewer(target_ip, target_port, remote_pwd, on_viewer_close)

        threading.Thread(target=resolve_and_connect, daemon=True).start()

    btn_connect = ctk.CTkButton(
        card_right,
        text="Conectar",
        font=ctk.CTkFont(size=15, weight="bold"),
        fg_color="#10b981",
        hover_color="#059669",
        height=40,
        command=on_connect_clicked,
    )
    btn_connect.pack(fill="x", padx=20, pady=(5, 15))

    # ------------------ BARRA INFERIOR / CONFIGURACIÓN ------------------
    footer_frame = ctk.CTkFrame(root, fg_color="transparent")
    footer_frame.pack(fill="x", padx=25, pady=(0, 10))

    lbl_server_title = ctk.CTkLabel(
        footer_frame,
        text="Servidor WAN:",
        font=ctk.CTkFont(size=11),
        text_color="#64748b",
    )
    lbl_server_title.pack(side="left")

    entry_server = ctk.CTkEntry(
        footer_frame, width=175, height=26, font=ctk.CTkFont(size=11)
    )
    entry_server.insert(0, signal_server_addr)
    entry_server.pack(side="left", padx=6)

    def update_server():
        new_val = entry_server.get().strip()
        if new_val:
            config["signal_server"] = new_val
            save_config(config)
            sig_client.server_addr = new_val
            btn_save_server.configure(text="OK!", fg_color="#059669")
            root.after(
                1500,
                lambda: btn_save_server.configure(
                    text="Guardar", fg_color="#334155"
                ),
            )

    btn_save_server = ctk.CTkButton(
        footer_frame,
        text="Guardar",
        width=55,
        height=26,
        font=ctk.CTkFont(size=11),
        fg_color="#334155",
        command=update_server,
    )
    btn_save_server.pack(side="left")

    lbl_cost_note = ctk.CTkLabel(
        footer_frame,
        text="Control Total • Admin UIPI Habilitado",
        font=ctk.CTkFont(size=11, weight="bold"),
        text_color="#10b981",
    )
    lbl_cost_note.pack(side="right")

    root.mainloop()


def main():
    # Comprobar privilegios de administrador al iniciar
    ensure_admin()
    run_gui()


if __name__ == "__main__":
    main()
