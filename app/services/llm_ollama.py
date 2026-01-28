import json
import requests

from app.core.config import OLLAMA_API_BASE, OLLAMA_API_KEY, OLLAMA_MODEL


def ollama_cloud_json(user_message: str) -> dict:
    """
    Pide al modelo que devuelva SOLO JSON estricto (sin texto extra).
    """
    if not OLLAMA_API_KEY:
        raise RuntimeError("Falta OLLAMA_API_KEY en variables de entorno.")

    system = """
Sos un asistente virtual de un ESTUDIO CONTABLE. Tu tarea es interpretar el mensaje del cliente
y devolver SIEMPRE un JSON estricto (sin texto extra, sin markdown) con esta forma exacta:

{
  "intent": "situacion_fiscal" | "vencimientos_proximos" | "iva_resumen_periodo" | "ventas_resumen_periodo" | "compras_resumen_periodo" | "resultado_periodo" | "documentos" | "identify" | "unknown",
  "cliente_ref": string | null,
  "periodo": "YYYY-MM" | null,
  "missing": string[],
  "reply": string
}

REGLAS CRITICAS (no romper):
- NO inventes datos de la base. No inventes montos, fechas, estados ni documentos.
- Tu salida debe ser SOLO JSON válido.
- cliente_ref:
  - Si el usuario incluye "cuit ..." o "email ...", extraelo y colocalo en cliente_ref.
  - Si NO lo incluye, cliente_ref = null.
- periodo:
  - SOLO si el usuario da un período exacto "YYYY-MM" (ej: 2025-12).
  - Si el usuario dice "este mes", "diciembre", etc., periodo=null.
- intent:
  - "iva_resumen_periodo" si pregunta por IVA / saldo / a pagar / a favor.
  - "ventas_resumen_periodo" si pregunta por ventas / facturación / ingresos.
  - "compras_resumen_periodo" si pregunta por compras / gastos.
  - "resultado_periodo" si pregunta por resultado / gané o perdí / balance del mes.
  - "vencimientos_proximos" si pregunta por vencimientos / pendientes / vencido / qué vence.
  - "situacion_fiscal" si pregunta por régimen / monotributo / responsable inscripto / categoría / IIBB.
  - "documentos" si pide constancia / ddjj / comprobantes / documentación.
  - "identify" si el mensaje SOLO aporta identificación (ej: "cuit 30-..." o "email x@x.com") sin otra consulta.
  - "unknown" si no se puede clasificar.
- missing:
  - SOLO puede incluir: "cliente_ref" y/o "periodo".
  - Si intent es uno de:
      iva_resumen_periodo, ventas_resumen_periodo, compras_resumen_periodo, resultado_periodo
    y falta periodo -> incluir "periodo".
  - Si intent NO es unknown y no hay cliente_ref (y el cliente no está identificado) -> incluir "cliente_ref".
  - Nunca incluyas otros campos en missing.
- reply:
  - Si falta cliente_ref: pedir CUIT o email con ejemplo.
  - Si falta periodo: pedir "YYYY-MM" con ejemplo.
  - Si intent es identify: confirmar que quedó identificado y preguntar qué desea consultar.
  - Si está todo: responder corto (sin datos numéricos), por ejemplo "Perfecto, consulto tu info y te respondo."
""".strip()

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
    }

    r = requests.post(
        f"{OLLAMA_API_BASE}/chat",
        headers={
            "Authorization": f"Bearer {OLLAMA_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )

    if not r.ok:
        raise RuntimeError(f"Ollama Cloud HTTP {r.status_code}: {r.text}")

    data = r.json()
    content = data.get("message", {}).get("content", "")
    return json.loads(content)
