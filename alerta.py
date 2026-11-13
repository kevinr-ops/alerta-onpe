"""
Alerta de convocatorias -> Telegram
Revisa las ofertas publicadas en Talento Perú (SERVIR), el portal donde las
entidades públicas publican sus convocatorias, y avisa por Telegram cuando
aparece una nueva que coincida con el filtro.

Solo usa la librería estándar de Python: no hace falta instalar nada.
"""
import html
import http.cookiejar
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")  # para que los emojis no fallen en la consola de Windows

URL = "https://app.servir.gob.pe/DifusionOfertasExterno/faces/consultas/ofertas_laborales.xhtml"
ESTADO = "vistos.json"

# Con DRY_RUN=1 solo imprime en pantalla, sin enviar a Telegram (para probar en tu PC)
DRY_RUN = os.getenv("DRY_RUN") == "1"
TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_IDS = [c.strip() for c in os.getenv("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
# Palabras del puesto, separadas por coma. Ej: "COORDINADOR,ASISTENTE"
PALABRAS = [p.strip() for p in os.getenv("PALABRAS", "COORDINADOR").split(",") if p.strip()]
# Departamento, ej: "LORETO". Vacío = todo el Perú
DEPARTAMENTO = os.getenv("DEPARTAMENTO", "").strip()
# Parte del nombre de la entidad, ej: "PROCESOS ELECTORALES" para solo ONPE. Vacío = cualquiera
ENTIDAD = os.getenv("ENTIDAD", "").strip()

if not DRY_RUN and (not TOKEN or not CHAT_IDS):
    sys.exit("Faltan los secrets TELEGRAM_TOKEN o TELEGRAM_CHAT_ID.")

# Códigos de departamento que usa el portal de SERVIR
DEPARTAMENTOS = {
    "AMAZONAS": "01", "ANCASH": "02", "APURIMAC": "03", "AREQUIPA": "04", "AYACUCHO": "05",
    "CAJAMARCA": "06", "CALLAO": "07", "CUSCO": "08", "HUANCAVELICA": "09", "HUANUCO": "10",
    "ICA": "11", "JUNIN": "12", "LA LIBERTAD": "13", "LAMBAYEQUE": "14", "LIMA": "15",
    "LORETO": "16", "MADRE DE DIOS": "17", "MOQUEGUA": "18", "PASCO": "19", "PIURA": "20",
    "PUNO": "21", "SAN MARTIN": "22", "TACNA": "23", "TUMBES": "24", "UCAYALI": "25",
}

F = "frmLstOfertsLabo"


def sin_tildes(t):
    return "".join(c for c in unicodedata.normalize("NFD", t or "")
                   if unicodedata.category(c) != "Mn").upper()


def limpio(t):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))).strip()


class Portal:
    def __init__(self):
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.op.addheaders = [("User-Agent", "alerta-convocatorias/1.0 (uso personal)")]
        self.html = ""

    def _pedir(self, datos=None):
        cuerpo = urllib.parse.urlencode(datos).encode() if datos else None
        with self.op.open(URL, cuerpo, timeout=30) as r:
            if r.status != 200:
                sys.exit(f"SERVIR respondió {r.status}")
            self.html = r.read().decode("utf-8", errors="replace")

    def _viewstate(self):
        m = re.search(r'name="javax.faces.ViewState"[^>]*value="([^"]+)"', self.html)
        if not m:
            sys.exit("La página de SERVIR cambió: no encontré el formulario.")
        return m.group(1)

    def _boton(self, texto):
        """Devuelve el id del botón con ese texto (Buscar, Sig.), o None si está desactivado."""
        for m in re.finditer(r'<button id="([^"]+)"[^>]*>(.*?)</button>', self.html, re.S):
            etiqueta = limpio(m.group(2))
            titulo = re.search(r'title="([^"]*)"', m.group(0))
            if texto in (etiqueta, titulo.group(1) if titulo else ""):
                return None if "disabled" in m.group(0)[:m.group(0).find(">")] else m.group(1)
        return None

    def _ofertas_de_pagina(self):
        res = []
        for tarjeta in self.html.split("<label>")[1:]:
            if "idPnlRepeatPuestos" not in tarjeta:
                continue
            o = {"puesto": limpio(tarjeta.split("</label>")[0])}
            m = re.search(r"<b>(.*?)</b>", tarjeta, re.S)
            o["entidad"] = limpio(m.group(1)) if m else ""
            for clave, etiqueta in [("ubicacion", "Ubicaci"), ("numero", "mero de Convocatoria"),
                                    ("vacantes", "Cantidad de Vacantes"), ("sueldo", "Remuneraci"),
                                    ("inicio", "Fecha Inicio"), ("fin", "Fecha Fin")]:
                m = re.search(etiqueta + r".*?</span>.*?<span[^>]*>(.*?)</span>", tarjeta, re.S)
                o[clave] = limpio(m.group(1)) if m else ""
            res.append(o)
        return res

    def buscar(self, puesto, cod_dep):
        self._pedir()  # abre la página para obtener el formulario
        form = {
            F: F, f"{F}:modalidadAcceso": "03", f"{F}:txtPerfil": "",
            f"{F}:cboDep_input": cod_dep, f"{F}:cboDep_focus": "",
            f"{F}:txtPuesto": puesto, f"{F}:autocompletar_input": "",
            f"{F}:autocompletar_hinput": "", f"{F}:txtNroConv": "",
        }
        boton = self._boton("Buscar")
        if not boton:
            sys.exit("La página de SERVIR cambió: no encontré el botón Buscar.")
        self._pedir({**form, boton: "", "javax.faces.ViewState": self._viewstate()})
        ofertas = self._ofertas_de_pagina()
        for _ in range(30):  # como máximo 30 páginas (300 ofertas) por palabra
            sig = self._boton("Sig.")
            if not sig:
                break
            self._pedir({**form, sig: "", "javax.faces.ViewState": self._viewstate()})
            nuevas = self._ofertas_de_pagina()
            if not nuevas:
                break
            ofertas += nuevas
        return ofertas


def clave(o):
    return f"{o['entidad']}|{o['numero']}|{o['puesto']}"


def telegram(texto):
    if DRY_RUN:
        print("---- (no enviado, DRY_RUN) ----\n" + texto)
        return
    for chat in CHAT_IDS:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data=json.dumps({"chat_id": chat, "text": texto, "parse_mode": "HTML",
                             "disable_web_page_preview": True}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30)


def esc(t):
    return html.escape(t or "?", quote=False)


def main():
    cod_dep = "0"
    if DEPARTAMENTO:
        cod_dep = DEPARTAMENTOS.get(sin_tildes(DEPARTAMENTO))
        if not cod_dep:
            sys.exit(f"No conozco el departamento '{DEPARTAMENTO}'. Opciones: {', '.join(DEPARTAMENTOS)}")

    portal = Portal()
    encontradas = {}
    for palabra in PALABRAS:
        for o in portal.buscar(palabra, cod_dep):
            if ENTIDAD and sin_tildes(ENTIDAD) not in sin_tildes(o["entidad"]):
                continue
            encontradas[clave(o)] = o

    primera_vez = not os.path.exists(ESTADO)
    vistos = set() if primera_vez else set(json.load(open(ESTADO, encoding="utf-8")))
    nuevas = [o for k, o in encontradas.items() if k not in vistos]

    zona = DEPARTAMENTO.upper() or "todo el Perú"
    filtro = f"{', '.join(PALABRAS)}" + (f" en {ENTIDAD}" if ENTIDAD else "")
    print(f"{len(encontradas)} coinciden, {len(nuevas)} nuevas ({filtro} / {zona})")

    if primera_vez:
        lista = "\n".join(f"• {esc(o['puesto'])} — {esc(o['entidad'])} — {esc(o['sueldo'])}"
                          for o in encontradas.values()) or "Por ahora no hay ninguna vigente."
        telegram(f"✅ <b>Alerta de convocatorias activada</b>\nZona: {esc(zona)}\nBusco: {esc(filtro)}\n\n"
                 f"Vigentes ahora:\n{lista[:3500]}\n\nTe aviso apenas salga una nueva.")
    else:
        for o in nuevas:
            telegram(f"🚨 <b>NUEVA CONVOCATORIA</b>\n\n<b>{esc(o['puesto'])}</b>\n"
                     f"🏛 {esc(o['entidad'])}\n💰 {esc(o['sueldo'])}\n👥 Vacantes: {esc(o['vacantes'])}\n"
                     f"📍 {esc(o['ubicacion'])}\n🗓 {esc(o['inicio'])} al {esc(o['fin'])}\n"
                     f"🔖 {esc(o['numero'])}\n\n"
                     f"Búscala en Talento Perú (revisa las bases y el plazo):\n{URL}")

    json.dump(sorted(vistos | set(encontradas)), open(ESTADO, "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()
