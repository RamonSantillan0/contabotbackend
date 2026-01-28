from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from decimal import Decimal, InvalidOperation
from .schemas import AgentRequest, AgentResponse
from datetime import datetime
from .settings import settings
from sqlalchemy import text
from .db import SessionLocal, test_db_connection
import os
import json
import requests
import re


# Global session storage (in-memory)
SESSION_CLIENTE = {}  # { "session_id": id_cliente }
SESSION_CTX = {}      # { "session_id": {"pending_month": 12, "pending_intent": "ventas_resumen_periodo"} }



# FastAPI app
app = FastAPI(title="Contador Agent API", version="0.1.0")

origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# OLLAMA config
# -----------------------------
OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE", "https://ollama.com/api")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:120b")


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


# -----------------------------
# Helpers
# -----------------------------
def ars(value) -> str:
    """
    Formatea números a ARS estilo Argentina: $ 168.982,50
    Acepta str, int, float, Decimal.
    """
    if value is None:
        return "-"
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)

    s = f"{n:,.2f}"          # 168,982.50
    s = s.replace(",", "X")  # 168X982.50
    s = s.replace(".", ",")  # 168X982,50
    s = s.replace("X", ".")  # 168.982,50
    return f"$ {s}"



MESES = {
    "ene": 1, "enero": 1,
    "feb": 2, "febrero": 2,
    "mar": 3, "marzo": 3,
    "abr": 4, "abril": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "junio": 6,
    "jul": 7, "julio": 7,
    "ago": 8, "agosto": 8,
    "sep": 9, "sept": 9, "septiembre": 9,
    "oct": 10, "octubre": 10,
    "nov": 11, "noviembre": 11,
    "dic": 12, "diciembre": 12,
}

def _to_periodo(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"

def parse_periodo_es(texto: str):
    """
    return (periodo, error, pending_month)
    - periodo: "YYYY-MM" si pudo resolver
    - error: mensaje si detectó mes pero faltó año, o si pidió confirmación
    - pending_month: int (1-12) si detectó mes sin año
    """
    t = texto.lower()

    # 1) Si ya viene YYYY-MM
    m = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", texto)
    if m:
        return m.group(0), None, None

    # 2) "noviembre 2025" / "nov 2025" / "noviembre de 2025"
    m2 = re.search(
        r"\b(ene(?:ro)?|feb(?:rero)?|mar(?:zo)?|abr(?:il)?|may(?:o)?|jun(?:io)?|jul(?:io)?|ago(?:sto)?|sep(?:t)?(?:iembre)?|oct(?:ubre)?|nov(?:iembre)?|dic(?:iembre)?)\b"
        r"(?:\s+de)?\s+(20\d{2})\b",
        t
    )
    if m2:
        mes_txt = m2.group(1)
        anio = int(m2.group(2))
        mes_key = mes_txt[:3]  # ene/feb/mar...
        mes = MESES.get(mes_key) or MESES.get(mes_txt)
        if mes:
            return _to_periodo(anio, mes), None, None

    # 3) Solo mes ("diciembre")
    m3 = re.search(
        r"\b(ene(?:ro)?|feb(?:rero)?|mar(?:zo)?|abr(?:il)?|may(?:o)?|jun(?:io)?|jul(?:io)?|ago(?:sto)?|sep(?:t)?(?:iembre)?|oct(?:ubre)?|nov(?:iembre)?|dic(?:iembre)?)\b",
        t
    )
    if m3:
        mes_txt = m3.group(1)
        mes_key = mes_txt[:3]
        mes = MESES.get(mes_key) or MESES.get(mes_txt)
        return None, f"¿De qué año es {mes_txt}? Ej: 2025-{mes:02d}", mes

    # 4) "este mes" / "mes actual" / "mes pasado" / "mes que viene"
    now = datetime.now()

    def add_months(y: int, m: int, delta: int):
        total = (y * 12 + (m - 1)) + delta
        new_y = total // 12
        new_m = (total % 12) + 1
        return new_y, new_m

    if "este mes" in t or "mes actual" in t:
        return _to_periodo(now.year, now.month), None, None

    if "mes pasado" in t or "mes anterior" in t:
        y, m = add_months(now.year, now.month, -1)
        return _to_periodo(y, m), None, None

    if "mes que viene" in t or "proximo mes" in t or "próximo mes" in t or "mes siguiente" in t:
        y, m = add_months(now.year, now.month, 1)
        return _to_periodo(y, m), None, None



    return None, None, None




def _fetch_one_dict(db, sql: str, params: dict):
    row = db.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def _get_cliente_id(db, cliente_ref: str | None) -> int | None:
    if not cliente_ref:
        return None

    ref = cliente_ref.strip()

    # CUIT con o sin guiones
    cuit_digits = re.sub(r"\D", "", ref)
    if len(cuit_digits) in (10, 11):
        row = db.execute(
            text("SELECT id_cliente FROM clientes WHERE REPLACE(REPLACE(cuit,'-',''),' ','') = :cuit LIMIT 1"),
            {"cuit": cuit_digits},
        ).mappings().first()
        return int(row["id_cliente"]) if row else None

    # email
    if "@" in ref:
        row = db.execute(
            text("SELECT id_cliente FROM clientes WHERE email = :email LIMIT 1"),
            {"email": ref},
        ).mappings().first()
        return int(row["id_cliente"]) if row else None

    # razon social
    row = db.execute(
        text("SELECT id_cliente FROM clientes WHERE razon_social LIKE :q LIMIT 1"),
        {"q": f"%{ref}%"},
    ).mappings().first()
    return int(row["id_cliente"]) if row else None


def _get_situacion_fiscal(db, id_cliente: int):
    row = db.execute(
        text("""
            SELECT *
            FROM situacion_fiscal
            WHERE id_cliente = :id_cliente
            ORDER BY COALESCE(fecha_fin, '9999-12-31') DESC, fecha_inicio DESC
            LIMIT 1
        """),
        {"id_cliente": id_cliente},
    ).mappings().first()
    return dict(row) if row else None


def _get_vencimientos(db, id_cliente: int, limit: int = 10, mode: str = "proximos"):
    if mode == "mes_actual":
        fecha_filter = """
            v.fecha_vto >= DATE_FORMAT(CURDATE(), '%Y-%m-01')
            AND v.fecha_vto <  DATE_ADD(DATE_FORMAT(CURDATE(), '%Y-%m-01'), INTERVAL 1 MONTH)
        """
    else:  # "proximos"
        fecha_filter = "v.fecha_vto >= CURDATE()"

    rows = db.execute(
        text(f"""
            SELECT v.id_vencimiento, v.periodo, v.fecha_vto, v.estado,
                   i.nombre AS impuesto
            FROM vencimientos v
            JOIN impuestos i ON i.id_impuesto = v.id_impuesto
            WHERE v.id_cliente = :id_cliente
              AND v.estado = 'PENDIENTE'
              AND ({fecha_filter})
            ORDER BY v.fecha_vto ASC
            LIMIT :limit
        """),
        {"id_cliente": id_cliente, "limit": limit},
    ).mappings().all()

    return [dict(r) for r in rows]

def _count_vencidos_recientes(db, id_cliente: int, days: int = 30) -> int:
    row = db.execute(
        text("""
            SELECT COUNT(*) AS c
            FROM vencimientos
            WHERE id_cliente = :id_cliente
              AND estado = 'VENCIDO'
              AND fecha_vto >= DATE_SUB(CURDATE(), INTERVAL :days DAY)
        """),
        {"id_cliente": id_cliente, "days": days},
    ).mappings().first()
    return int(row["c"]) if row else 0



def _norm_periodo(p: str) -> str:
    """
    Normaliza a YYYY-MM si viene como YYYY/MM o YYYYMM.
    Si ya viene YYYY-MM, lo deja.
    """
    p = (p or "").strip()

    # YYYY-MM
    if re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", p):
        return p

    # YYYY/MM
    m = re.fullmatch(r"(20\d{2})/(0[1-9]|1[0-2])", p)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # YYYYMM
    m = re.fullmatch(r"(20\d{2})(0[1-9]|1[0-2])", p)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # si no matchea, lo devolvemos tal cual (para debug)
    return p


def _get_iva_periodo(db, id_cliente: int, periodo: str):
    periodo = _norm_periodo(periodo)

    # 1) intento exacto (lo ideal)
    row = _fetch_one_dict(
        db,
        """
        SELECT *
        FROM vw_iva_totales
        WHERE id_cliente = :id_cliente
          AND periodo = :periodo
        LIMIT 1
        """,
        {"id_cliente": id_cliente, "periodo": periodo},
    )
    if row:
        return row

    # 2) fallback tolerante: compara ignorando separadores (YYYY-MM vs YYYYMM vs YYYY/MM)
    row = _fetch_one_dict(
        db,
        """
        SELECT *
        FROM vw_iva_totales
        WHERE id_cliente = :id_cliente
          AND REPLACE(REPLACE(periodo,'-',''),'/','') = REPLACE(:periodo,'-','')
        LIMIT 1
        """,
        {"id_cliente": id_cliente, "periodo": periodo},
    )
    return row

def _get_ventas_periodo(db, id_cliente: int, periodo: str):
    return _fetch_one_dict(
        db,
        "SELECT * FROM vw_ventas_resumen_periodo WHERE id_cliente=:id_cliente AND periodo=:periodo LIMIT 1",
        {"id_cliente": id_cliente, "periodo": periodo},
    )


def _get_compras_periodo(db, id_cliente: int, periodo: str):
    return _fetch_one_dict(
        db,
        "SELECT * FROM vw_compras_resumen_periodo WHERE id_cliente=:id_cliente AND periodo=:periodo LIMIT 1",
        {"id_cliente": id_cliente, "periodo": periodo},
    )


def _get_resultado_periodo(db, id_cliente: int, periodo: str):
    return _fetch_one_dict(
        db,
        "SELECT * FROM vw_resultado_periodo WHERE id_cliente=:id_cliente AND periodo=:periodo LIMIT 1",
        {"id_cliente": id_cliente, "periodo": periodo},
    )


def _get_documentos(db, id_cliente: int, limit: int = 10):
    rows = db.execute(
        text("""
            SELECT id_documento, tipo, titulo, url_archivo, fecha_documento
            FROM documentos
            WHERE id_cliente = :id_cliente
            ORDER BY fecha_documento DESC
            LIMIT :limit
        """),
        {"id_cliente": id_cliente, "limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


# -----------------------------
# Health / db-test
# -----------------------------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/db-test")
def db_test():
    try:
        return {"db": test_db_connection()}
    except Exception as e:
        return {"error": str(e)}


# -----------------------------
# Agent
# -----------------------------
@app.post("/agent", response_model=AgentResponse)
def agent(req: AgentRequest):
    msg = req.message.strip()
    msg_l = msg.lower()

    # 1) Regex siempre (captura rápida)
    cliente_ref = None
    m = re.search(r"(cuit|email)\s*[:=]?\s*([^\s]+)", msg_l)
    if m:
        cliente_ref = m.group(2)

    periodo = None
    periodo_error = None
    pending_month = None

    mper = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", msg)
    if mper:
        periodo = mper.group(0)
    else:
        periodo_parsed, periodo_err, pending_month = parse_periodo_es(msg)
        if periodo_parsed:
            periodo = periodo_parsed
        elif periodo_err:
            periodo_error = periodo_err




    # 2) Intent por reglas
    intent_rules = "unknown"
    if "situacion fiscal" in msg_l or "monotrib" in msg_l or "responsable" in msg_l:
        intent_rules = "situacion_fiscal"
    elif "vence" in msg_l or "vencim" in msg_l or "pendiente" in msg_l:
        intent_rules = "vencimientos_proximos"
    elif any(k in msg_l for k in ["ventas", "venta", "facturacion", "facturación", "ingresos"]):
        intent_rules = "ventas_resumen_periodo"
    elif any(k in msg_l for k in ["compras", "compra", "gastos"]):
        intent_rules = "compras_resumen_periodo"
    elif any(k in msg_l for k in ["resultado", "gané", "gane", "perdí", "perdi", "balance"]):
        intent_rules = "resultado_periodo"
    elif "iva" in msg_l:
        intent_rules = "iva_resumen_periodo"
    elif "document" in msg_l or "constancia" in msg_l or "ddjj" in msg_l:
        intent_rules = "documentos"

    # 🔹 3) Fijamos intent base
    intent = intent_rules

    # 🔹 3A) ---- 2A.2: solo año + mes pendiente ----
    m_year_only = re.fullmatch(r"\s*(20\d{2})\s*", msg)
    ctx = SESSION_CTX.get(req.session_id)

    if (not periodo) and m_year_only and ctx and ctx.get("pending_month"):
        year = int(m_year_only.group(1))
        month = int(ctx["pending_month"])
        periodo = f"{year:04d}-{month:02d}"

        # si el usuario no dijo qué quiere, usamos el intent pendiente
        if intent == "unknown" and ctx.get("pending_intent"):
            intent = ctx["pending_intent"]

        # limpiamos contexto pendiente
        SESSION_CTX[req.session_id] = {}

    allowed_intents = {
        "situacion_fiscal",
        "vencimientos_proximos",
        "iva_resumen_periodo",
        "ventas_resumen_periodo",
        "compras_resumen_periodo",
        "resultado_periodo",
        "documentos",
        "identify",
        "unknown",
    }

    llm = None

    # Condición: llamo al LLM si el intent es unknown o si falta cliente_ref o periodo cuando es requerido
    needs_periodo = intent in {
        "iva_resumen_periodo",
        "ventas_resumen_periodo",
        "compras_resumen_periodo",
        "resultado_periodo",
    }

    should_call_llm = (
        intent == "unknown"
        or (not cliente_ref)
        or (needs_periodo and not periodo)
    )

    if should_call_llm:
        try:
            llm = ollama_cloud_json(msg)   # <-- ACÁ recién existe llm
        except Exception as e:
            print("Error llamando LLM:", e)
            llm = None

    # 🔹 4) LLM completa lo que falte

    if isinstance(llm, dict):
        llm_intent = llm.get("intent")
        llm_cliente_ref = llm.get("cliente_ref")
        llm_periodo = llm.get("periodo")

        if llm_intent in allowed_intents:
            intent = llm_intent

        if not cliente_ref and llm_cliente_ref:
            cliente_ref = str(llm_cliente_ref)

        if not periodo and llm_periodo:
            periodo = str(llm_periodo)

    # 🔹 5) si ya hay periodo, limpiamos error
    if periodo:
        periodo_error = None


    db = SessionLocal()
    try:
        # 4) Identify: solo CUIT/email y nada más
        only_id = (
            cliente_ref is not None
            and intent in ("unknown", "identify")
            and not any(k in msg_l for k in [
                "iva","vence","vencim","situacion","monotrib","responsable","document","constancia","ddjj",
                "ventas","venta","facturacion","facturación","ingresos","compras","compra","gastos","resultado","gané","gane","perdí","perdi","balance"
            ])
        )

        if only_id:
            id_cliente_tmp = _get_cliente_id(db, cliente_ref)
            if not id_cliente_tmp:
                return AgentResponse(
                    intent="identify",
                    reply="No pude encontrar ese CUIT/email. Verificá el dato y probá de nuevo.",
                    missing=["cliente_ref"],
                )

            SESSION_CLIENTE[req.session_id] = id_cliente_tmp
            return AgentResponse(
                intent="identify",
                reply="Perfecto ✅ Ya te identifiqué. Ahora decime qué necesitás: IVA (con período), ventas, compras, resultado, vencimientos, situación fiscal o documentos.",
                missing=[],
                data={"id_cliente": id_cliente_tmp},
            )

        # 5) Resolver id_cliente (ref o sesión)
        id_cliente = None

        if cliente_ref:
            id_cliente = _get_cliente_id(db, cliente_ref)
            if not id_cliente:
                missing = ["cliente_ref"]
                if intent in {"iva_resumen_periodo","ventas_resumen_periodo","compras_resumen_periodo","resultado_periodo"} and not periodo:
                    missing.append("periodo")
                return AgentResponse(
                    intent=intent,
                    reply="No pude encontrar ese CUIT/email. Verificá el dato y probá de nuevo.",
                    missing=missing,
                )
            SESSION_CLIENTE[req.session_id] = id_cliente

        if not id_cliente:
            id_cliente = SESSION_CLIENTE.get(req.session_id)

        # 6) Missing si falta cliente_ref / periodo
        if intent != "unknown" and not id_cliente:
            missing = ["cliente_ref"]
            if intent in {"iva_resumen_periodo","ventas_resumen_periodo","compras_resumen_periodo","resultado_periodo"} and not periodo:
                missing.append("periodo")
            return AgentResponse(
                intent=intent,
                reply=(
                    "Para ayudarte necesito tu CUIT o email"
                    + (" y el período (YYYY-MM)." if "periodo" in missing else ".")
                    + " Ej: 'cuit 20-xxxxxxxx-x iva 2025-12'."
                ),
                missing=missing,
            )

        # 7) Responder por intent
        if intent == "situacion_fiscal":
            sf = _get_situacion_fiscal(db, id_cliente)
            if not sf:
                return AgentResponse(intent=intent, reply="No encuentro tu situación fiscal cargada todavía.", missing=[])
            reply = (
                f"Tu situación fiscal actual es: {sf.get('regimen','(sin régimen)')}. "
                f"Categoría: {sf.get('categoria','-')}. "
                f"IIBB: {sf.get('iibb_regimen','-')} {sf.get('iibb_jurisdiccion','')}"
            ).strip()
            return AgentResponse(intent=intent, reply=reply, data={"situacion_fiscal": sf})

        if intent == "vencimientos_proximos":
            # 🔹 Detectamos si el usuario pidió "este mes"
            modo = "proximos"
            if "este mes" in msg_l or "en el mes" in msg_l:
                modo = "mes_actual"

            # 🔹 Traemos vencimientos pendientes
            v = _get_vencimientos(db, id_cliente, limit=10, mode=modo)

            # 🔹 (Opcional) Contamos vencidos recientes
            vencidos_30 = _count_vencidos_recientes(db, id_cliente, days=30)

            # 🔹 Si no hay pendientes
            if not v:
                reply = (
                    "No tenés vencimientos pendientes para este mes 🎉"
                    if modo == "mes_actual"
                    else "No tenés vencimientos pendientes próximos 🎉"
                )

                if vencidos_30 > 0:
                    reply += f"\n⚠️ Además tenés {vencidos_30} vencido(s) en los últimos 30 días."

                return AgentResponse(
                    intent=intent,
                    reply=reply,
                    missing=[],
                    data={"vencimientos": []}
                )

            # 🔹 Armamos respuesta
            titulo = (
                "Estos son tus vencimientos de este mes:\n"
                if modo == "mes_actual"
                else "Estos son tus próximos vencimientos:\n"
            )

            lines = [
                f"- {x['impuesto']} {x['periodo']} → {x['fecha_vto']}"
                for x in v
            ]

            reply = titulo + "\n".join(lines)

            if vencidos_30 > 0:
                reply += f"\n\n⚠️ Tenés {vencidos_30} vencido(s) en los últimos 30 días."

            return AgentResponse(
                intent=intent,
                reply=reply,
                missing=[],
                data={"vencimientos": v}
            )

        if intent in {"iva_resumen_periodo","ventas_resumen_periodo","compras_resumen_periodo","resultado_periodo"} and not periodo:
        # Guardamos el mes/intención pendiente si detectamos un mes sin año
            if pending_month:
                SESSION_CTX[req.session_id] = {
                    "pending_month": int(pending_month),
                    "pending_intent": intent,
                }

            # Si parse_periodo_es nos dio un mensaje específico (ej: "¿De qué año es diciembre?"), lo usamos
            if periodo_error:
                return AgentResponse(intent=intent, reply=periodo_error, missing=["periodo"])

            # Si no hubo pista, pregunta genérica según intent
            pregunta = "¿De qué período? (YYYY-MM, ej: 2025-12)"
            if intent == "iva_resumen_periodo":
                pregunta = "¿De qué período querés el IVA? (YYYY-MM, ej: 2025-12)"
            elif intent == "ventas_resumen_periodo":
                pregunta = "¿De qué período querés las ventas? (YYYY-MM, ej: 2025-12)"
            elif intent == "compras_resumen_periodo":
                pregunta = "¿De qué período querés las compras? (YYYY-MM, ej: 2025-12)"
            elif intent == "resultado_periodo":
                pregunta = "¿De qué período querés el resultado? (YYYY-MM, ej: 2025-12)"

            return AgentResponse(intent=intent, reply=pregunta, missing=["periodo"])



        if intent == "iva_resumen_periodo":
            iva = _get_iva_periodo(db, id_cliente, periodo)
            if not iva:
                return AgentResponse(intent=intent, reply=f"No encuentro resumen de IVA para el período {periodo}.", missing=[])
            reply = (
                f"IVA {periodo} ({iva.get('razon_social')}): {iva.get('resultado')}.\n"
                f"• Débito: {ars(iva.get('iva_debito'))}\n"
                f"• Crédito: {ars(iva.get('iva_credito'))}\n"
                f"• Percepciones: {ars(iva.get('perc_iva'))}\n"
                f"• Retenciones: {ars(iva.get('ret_iva'))}\n"
                f"• Saldo: {ars(iva.get('saldo_iva_calculado'))}"
            )
            return AgentResponse(intent=intent, reply=reply, data={"iva": iva})

        if intent == "ventas_resumen_periodo":
            v = _get_ventas_periodo(db, id_cliente, periodo)
            if not v:
                return AgentResponse(intent=intent, reply=f"No encuentro ventas para el período {periodo}.", missing=[])
            reply = (
                f"Ventas {periodo} ({v.get('razon_social')}):\n"
                f"• Neto: {ars(v.get('ventas_neto'))}\n"
                f"• IVA: {ars(v.get('ventas_iva'))}\n"
                f"• Total: {ars(v.get('ventas_total'))}"
            )
            return AgentResponse(intent=intent, reply=reply, data={"ventas": v})

        if intent == "compras_resumen_periodo":
            c = _get_compras_periodo(db, id_cliente, periodo)
            if not c:
                return AgentResponse(intent=intent, reply=f"No encuentro compras para el período {periodo}.", missing=[])
            reply = (
                f"Compras {periodo} ({c.get('razon_social')}):\n"
                f"• Neto: {ars(c.get('compras_neto'))}\n"
                f"• IVA: {ars(c.get('compras_iva'))}\n"
                f"• Total: {ars(c.get('compras_total'))}"
            )
            return AgentResponse(intent=intent, reply=reply, data={"compras": c})

        if intent == "resultado_periodo":
            r = _get_resultado_periodo(db, id_cliente, periodo)
            if not r:
                return AgentResponse(intent=intent, reply=f"No encuentro resultado para el período {periodo}.", missing=[])
            reply = (
                f"Resultado {periodo} ({r.get('razon_social')}):\n"
                f"• Ventas total: {ars(r.get('ventas_total'))}\n"
                f"• Compras total: {ars(r.get('compras_total'))}\n"
                f"• Resultado total: {ars(r.get('resultado_total'))}\n"
                f"• Resultado neto: {ars(r.get('resultado_neto'))}"
            )
            return AgentResponse(intent=intent, reply=reply, data={"resultado": r})

        if intent == "documentos":
            docs = _get_documentos(db, id_cliente)
            if not docs:
                return AgentResponse(intent=intent, reply="No hay documentos cargados todavía.", missing=[])
            lines = [f"- {d['tipo']}: {d['titulo']} ({d['fecha_documento']})" for d in docs]
            reply = "Documentos disponibles:\n" + "\n".join(lines)
            return AgentResponse(intent=intent, reply=reply, data={"documentos": docs})

        return AgentResponse(
            intent="unknown",
            reply="Decime qué necesitás: IVA (con período YYYY-MM), ventas, compras, resultado, vencimientos, situación fiscal o documentos.",
            missing=[],
        )

    finally:
        db.close()


# -----------------------------
# Extras
# -----------------------------
@app.get("/clientes")
def list_clientes(limit: int = 20):
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
                SELECT id_cliente, razon_social, cuit, email, activo
                FROM clientes
                ORDER BY id_cliente DESC
                LIMIT :limit
            """),
            {"limit": limit},
        ).mappings().all()
        return {"count": len(rows), "items": [dict(r) for r in rows]}
    finally:
        db.close()


@app.delete("/session/{session_id}")
def reset_session(session_id: str):
    removed = SESSION_CLIENTE.pop(session_id, None)
    return {"ok": True, "session_id": session_id, "removed": removed is not None}


@app.get("/session-ctx/{session_id}")
def get_session_ctx(session_id: str):
    return {
        "session_id": session_id,
        "ctx": SESSION_CTX.get(session_id),
        "id_cliente": SESSION_CLIENTE.get(session_id),
    }

