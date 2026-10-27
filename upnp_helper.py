"""
ODesk UPnP Helper - Apertura automática de puertos en el router doméstico.
100% Python estándar (sin librerías externas).
"""

import socket
import re
import urllib.request
import xml.etree.ElementTree as ET

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_MX = 2
SSDP_ST = "urn:schemas-upnp-org:device:InternetGatewayDevice:1"


def discover_upnp_url():
    """Busca el router compatible con UPnP en la red local mediante SSDP."""
    msg = (
        f'M-SEARCH * HTTP/1.1\r\n'
        f'HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n'
        f'ST: {SSDP_ST}\r\n'
        f'MAN: "ssdp:discover"\r\n'
        f'MX: {SSDP_MX}\r\n\r\n'
    )
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(2.0)
    try:
        sock.sendto(msg.encode(), (SSDP_ADDR, SSDP_PORT))
        while True:
            data, _ = sock.recvfrom(2048)
            response = data.decode("utf-8", errors="ignore")
            match = re.search(r"LOCATION:\s*(http://[^\r\n]+)", response, re.IGNORECASE)
            if match:
                return match.group(1).strip()
    except (socket.timeout, OSError):
        return None
    finally:
        sock.close()


def find_control_url(desc_xml_url):
    """Descarga el XML del router y localiza la URL de control del servicio WANIPConnection o WANPPPConnection."""
    try:
        req = urllib.request.Request(desc_xml_url, headers={"User-Agent": "ODesk-UPnP"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        # Namespace general de UPnP
        ns = {"ns": "urn:schemas-upnp-org:device-1-0"}
        
        for service in root.iter():
            if service.tag.endswith("service"):
                st = service.find("{*}serviceType")
                cu = service.find("{*}controlURL")
                if st is not None and cu is not None:
                    if "WANIPConnection" in st.text or "WANPPPConnection" in st.text:
                        control_url = cu.text.strip()
                        service_type = st.text.strip()
                        if not control_url.startswith("http"):
                            # Resolver URL relativa
                            from urllib.parse import urljoin
                            control_url = urljoin(desc_xml_url, control_url)
                        return control_url, service_type
    except Exception:
        pass
    return None, None


def open_port(port=5555, proto="TCP", desc="ODesk Remote"):
    """Intenta abrir automáticamente el puerto en el router usando UPnP."""
    desc_url = discover_upnp_url()
    if not desc_url:
        return False, "Router no respondió a UPnP (puede estar deshabilitado)."

    control_url, service_type = find_control_url(desc_url)
    if not control_url:
        return False, "No se encontró el servicio WAN de UPnP en el router."

    # Obtener IP local del equipo
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "127.0.0.1"
    finally:
        s.close()

    soap_body = f"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:AddPortMapping xmlns:u="{service_type}">
      <NewRemoteHost></NewRemoteHost>
      <NewExternalPort>{port}</NewExternalPort>
      <NewProtocol>{proto}</NewProtocol>
      <NewInternalPort>{port}</NewInternalPort>
      <NewInternalClient>{local_ip}</NewInternalClient>
      <NewEnabled>1</NewEnabled>
      <NewPortMappingDescription>{desc}</NewPortMappingDescription>
      <NewLeaseDuration>0</NewLeaseDuration>
    </u:AddPortMapping>
  </s:Body>
</s:Envelope>"""

    headers = {
        "SOAPAction": f'"{service_type}#AddPortMapping"',
        "Content-Type": 'text/xml; charset="utf-8"',
        "Content-Length": str(len(soap_body)),
    }

    try:
        req = urllib.request.Request(control_url, data=soap_body.encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            if resp.status == 200:
                return True, f"Puerto {port} abierto exitosamente en el router vía UPnP para {local_ip}."
    except Exception as e:
        return False, f"El router rechazó la regla UPnP ({e})."

    return False, "Respuesta no concluyente de UPnP."
