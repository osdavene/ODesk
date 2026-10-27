# ODesk - Escritorio Remoto Híbrido P2P (Windows)

Herramienta de escritorio remoto punto a punto, moderna, ligera y de alto rendimiento.
Desarrollada con una arquitectura híbrida de **Rust** (captura y compresión SIMD) y **Python** (interfaz gráfica moderna con CustomTkinter y orquestación de red).

---

## 🎨 Aspecto Visual y Funcionamiento

ODesk cuenta con una interfaz gráfica moderna con modo oscuro inspirada en **AnyDesk** y **RustDesk**:

- **Este Puesto de Trabajo (Tu ID):** Genera automáticamente un código estilizado de 10 dígitos (ej. `168 329 5266`) asociado a tu equipo, con botón de **Copiar Código**.
- **Servidor en Segundo Plano:** El equipo queda listo para recibir conexiones inmediatamente al abrir la aplicación (indicador verde `● Listo para recibir conexiones`).
- **Control Remoto:** Introduce el código ID del otro equipo y presiona **Conectar**.

---

## 💡 ¿Por qué no genera costos en AWS?

En herramientas comerciales, los servidores en la nube son necesarios principalmente para:
1. **Señalización (Intercambiar IDs):** Consume solo ~500 bytes por conexión.
2. **Relay (Retransmitir el video por la nube):** Consume ~1.5 GB por hora por usuario. Si pasa por AWS, el costo de transferencia de datos (*Data Transfer Out*) sería elevado.

### La Solución de ODesk: "ID Cero Servidor"
ODesk mapea matemáticamente las direcciones IPv4 (locales o de red segura **Tailscale**) a un código numérico amigable de 10 dígitos:
```
Tu IP de Tailscale: 100.85.12.34  <--->  Tu ID en ODesk: 168 329 5266
```
- **El video viaja 100% directo (P2P)** entre los dos equipos.
- **Cero servidores en AWS.** No requieres bases de datos, instancias EC2 ni pagar ancho de banda en la nube.

---

## 🚀 Cómo Usarlo

### 1. Iniciar la aplicación
Simplemente ejecuta:
```powershell
python odesk.py
```
*(O haz doble clic en `odesk.py`)*.

### 2. Conectarte a otro equipo
1. Pídele al usuario remoto su **Código ID** de 10 dígitos que aparece en su pantalla.
2. Escríbelo en la casilla **Controlar Otro Dispositivo**.
3. Haz clic en **Conectar**. Se abrirá la ventana con el escritorio remoto en tiempo real.

---

## 🌐 Conexión por Internet (Fuera de casa) con Tailscale
Para conectar dos ordenadores situados en distintas casas o ciudades sin abrir puertos en el router:
1. Instala [Tailscale](https://tailscale.com/) en ambos ordenadores (es gratuito para uso personal).
2. ODesk detectará automáticamente la interfaz de Tailscale y tu código ID cambiará al código seguro de tu red privada.
3. Todo el tráfico viajará cifrado de extremo a extremo mediante **WireGuard**.
