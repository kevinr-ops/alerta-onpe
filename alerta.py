"""
Alerta ONPE -> Telegram
Revisa las ofertas del portal SIGLOC de ONPE (reclutamiento.onpe.gob.pe)
y avisa por Telegram cuando aparece una nueva que coincida con el filtro.
"""
import json
import os
import sys
import unicodedata
import urllib.request

API = "https://reclutamiento.onpe.gob.pe/sigloc-backend/v1/api"
PORTAL = "https://reclutamiento.onpe.gob.pe/convocatorias"
ESTADO = "vistos.json"

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_IDS = [c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",") if c.strip()]
# Palabras clave separadas por coma (sin tildes da igual). Ej: "COORDINADOR"
PALABRAS = [p.strip() for p in os.getenv("PALABRAS", "COORDINADOR").split(",") if p.strip()]
# Departamento, ej: "SAN MARTIN". Vacío = todo el Perú
DEPARTAMENTO = os.getenv("DEPARTAMENTO", "").strip()

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Origin": "https://reclutamiento.onpe.gob.pe",
    "Referer": PORTAL,
}


def sin_tildes(t):
    return "".join(c for c in unicodedata.normalize("NFD", t or "") if unicodedata.category(c) != "Mn").upper()


from curl_cffi import requests as cr  # se hace pasar por Chrome real

SESION = cr.Session(impersonate="chrome")
SESION.get(PORTAL, timeout=30)  # visita la página primero, como una persona


def http(url, data=None):
    h = {k: v for k, v in HEADERS.items() if k != "User-Agent"}
    if data is not None:
        r = SESION.post(url, json=data, headers=h, timeout=30)
    else:
        r = SESION.get(url, headers=h, timeout=30)
    if r.status_code != 200:
        sys.exit(f"ONPE respondió {r.status_code} en {url}")
    return r.json()


def id_departamento():
    if not DEPARTAMENTO:
        return 0
    for d in http(f"{API}/ubigeo/padre/1").get("data") or []:
        if sin_tildes(d["nombre"]) == sin_tildes(DEPARTAMENTO):
            return d["id"]
    sys.exit(f"No encontré el departamento '{DEPARTAMENTO}'. Revisa cómo lo escribiste.")


def ofertas(id_dep):
    resultado = []
    for tipo in (1, 2):  # 1 = Locación de servicios, 2 = Concursos públicos
        pagina = 0
        while True:
            r = http(f"{API}/convocatoria/lista", {
                "tipoConvocatoria": tipo, "idRubro": 0, "idDepartamento": id_dep,
                "verConcluidas": False, "palabraClave": None, "idOdp": None,
                "idLugarEjecucion": 0, "fechaPublicacion": {"start": None, "end": None},
                "page": pagina, "pageSize": 50,
            })
            p = (r.get("data") or {}).get("convocatoriasPage") or {}
            resultado += p.get("rows") or []
            pagina += 1
            if pagina >= (p.get("totalPages") or 0):
                break
    return resultado


def telegram(texto):
    for chat in CHAT_IDS:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data=json.dumps({"chat_id": chat, "text": texto, "parse_mode": "HTML",
                             "disable_web_page_preview": True}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30)


def main():
    vistos = set()
    primera_vez = not os.path.exists(ESTADO)
    if not primera_vez:
        vistos = set(json.load(open(ESTADO)))

    id_dep = id_departamento()
    todas = ofertas(id_dep)
    claves = [sin_tildes(p) for p in PALABRAS]
    filtradas = [o for o in todas if any(k in sin_tildes(o.get("denominacion")) for k in claves)]
    nuevas = [o for o in filtradas if str(o["idPerfil"]) not in vistos]

    zona = DEPARTAMENTO.upper() or "todo el Perú"
    print(f"{len(todas)} ofertas vigentes, {len(filtradas)} coinciden, {len(nuevas)} nuevas ({zona})")

    if primera_vez:
        lista = "\n".join(f"• {o['denominacion']} — S/ {o.get('montoSoles') or '?'}" for o in filtradas) \
            or "Por ahora no hay ninguna vigente."
        telegram(f"✅ <b>Alerta ONPE activada</b>\nZona: {zona}\nBusco: {', '.join(PALABRAS)}\n\n"
                 f"Vigentes ahora:\n{lista}\n\nTe aviso apenas salga una nueva.\n{PORTAL}")
    else:
        for o in nuevas:
            fecha = (o.get("fechaPublicacion") or "")[:16].replace("T", " ")
            rubro = ", ".join(r["descripcion"] for r in o.get("lsRubros") or [])
            telegram(f"🚨 <b>NUEVA CONVOCATORIA ONPE</b>\n\n<b>{o['denominacion']}</b>\n"
                     f"💰 S/ {o.get('montoSoles') or '?'}\n📂 {rubro}\n🗓 Publicada: {fecha}\n📍 {zona}\n\n"
                     f"Postula aquí (revisa las bases y el plazo):\n{PORTAL}")

    json.dump(sorted(vistos | {str(o["idPerfil"]) for o in filtradas}), open(ESTADO, "w"), indent=0)


if __name__ == "__main__":
    main()
