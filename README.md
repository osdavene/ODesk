# ODesk - Escritorio Remoto Híbrido P2P (Windows)

Herramienta de escritorio remoto punto a punto (P2P), moderna, ultraligera y de alto rendimiento.
Desarrollada con una arquitectura híbrida de **Rust** (captura GDI y compresión JPEG SIMD) y **Python** (interfaz gráfica moderna con CustomTkinter y orquestación de red).

---

## 🚀 Novedades y Características

- **Sin consola negra (Windowed):** Empaquetado en un único ejecutable [`ODesk.exe`](file:///c:/Proyectos/ODesk/ODesk.exe) sin ventana de terminal de fondo.
- **Modo Administrador Automático (UAC):** Solicita permisos de Administrador al inicio mediante su manifiesto para eludir la restricción **UIPI (User Interface Privilege Isolation)** de Windows. Permite interactuar con:
  - Ventanas elevadas (CMD como Admin, PowerShell, etc.).
  - Administrador de Tareas (*Task Manager*).
  - Instaladores y diálogos de configuración del sistema.
- **Acceso Desatendido con Contraseña:** Puedes configurar tu propia contraseña o usar la generada automáticamente para permitir el acceso remoto seguro sin intervención física.
- **Señalizador WAN en AWS de Costo Cero:** Conexión a través de tu instancia EC2 (`18.191.199.104:7000`) consumiendo menos de 2 MB de RAM y cero costo de ancho de banda (el video es 100% P2P directo).

---

## 🎨 Aspecto de la Interfaz

```
+----------------------------------------------------------------------------------+
|  ODesk [ADMINISTRADOR] • Control Total P2P       ● Conectado a Servidor WAN (AWS) |
+----------------------------------------------------------------------------------+
|                                        |                                         |
|  [ Este Puesto de Trabajo ]            |  [ Controlar Otro Dispositivo ]         |
|  Comparte tu código y contraseña.      |  Introduce el ID remoto y contraseña.   |
|                                        |                                         |
|  +----------------------------------+  |  +-----------------------------------+  |
|  |           Tu Código ID:          |  |  |                                   |  |
|  |          554 839 109             |  |  |       ID Remoto: 482 919 021      |  |
|  |   IP Pública: 179.12.128.160     |  |  |                                   |  |
|  +----------------------------------+  |  |     Contraseña: [ * * * * * * ]   |  |
|                                        |  +-----------------------------------+  |
|  [ Contraseña: ******* (Ver/Guardar)]  |                                         |
|  [      COPIAR CÓDIGO (Azul)      ]   |  [         CONECTAR (Verde)         ]   |
+----------------------------------------------------------------------------------+
```

---

## 🛠️ Compilar el Ejecutable `.exe`

Para generar o regenerar el ejecutable portátil sin consola:
```powershell
.\build.bat
```
Generará el archivo binario único: [`ODesk.exe`](file:///c:/Proyectos/ODesk/ODesk.exe).
