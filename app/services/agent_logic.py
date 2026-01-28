import re
from typing import Optional, Dict, Any

from app.schemas.agent import AgentRequest, AgentResponse
from app.core.session import SESSION_CTX
from app.services.periodo_parser import parse_periodo_es

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.queries import (
    _get_vencimientos,
    _count_vencidos_recientes,
    _get_iva_periodo,
    ensure_cliente,
    _get_ventas_periodo,
    _get_compras_periodo,
    _get_resultado_periodo,
    _get_situacion_fiscal,
    _get_documentos,
)

# Si luego querés usar LLM, lo dejamos importable:
# from app.services.llm_ollama import ollama_cloud_json


def ars(value):
    if value is None:
        return "$0,00"
    try:
        return f"${value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(value)


ALLOWED_INTENTS = {
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


def _extract_cliente_ref(msg_l: str):
    # 1) con etiqueta: "cuit 20-..." o "email x@x.com"
    m = re.search(r"\b(cuit|email)\s*[:=]?\s*([^\s]+)", msg_l)
    if m:
        return m.group(2).strip()

    # 2) CUIT suelto: 11 dígitos con guiones (20-12345678-9) o sin guiones (20123456789)
    m2 = re.search(r"\b(\d{2}-\d{8}-\d{1})\b", msg_l)
    if m2:
        return m2.group(1)

    m3 = re.search(r"\b(\d{11})\b", msg_l)
    if m3:
        raw = m3.group(1)
        return f"{raw[:2]}-{raw[2:10]}-{raw[10:]}"  # normaliza con guiones

    return None



def _detect_intent_rules(msg_l: str) -> str:
    if "situacion fiscal" in msg_l or "monotrib" in msg_l or "responsable" in msg_l:
        return "situacion_fiscal"
    if "vence" in msg_l or "vencim" in msg_l or "pendiente" in msg_l:
        return "vencimientos_proximos"
    if any(k in msg_l for k in ["ventas", "venta", "facturacion", "facturación", "ingresos"]):
        return "ventas_resumen_periodo"
    if any(k in msg_l for k in ["compras", "compra", "gastos"]):
        return "compras_resumen_periodo"
    if any(k in msg_l for k in ["resultado", "gané", "gane", "perdí", "perdi", "balance"]):
        return "resultado_periodo"
    if "iva" in msg_l:
        return "iva_resumen_periodo"
    if "document" in msg_l or "constancia" in msg_l or "ddjj" in msg_l:
        return "documentos"
    return "unknown"


def handle_agent(db, req: AgentRequest) -> AgentResponse:
    msg = (req.message or "").strip()
    msg_l = msg.lower().strip()

    # 1) Extraer cliente_ref rápido (regex)
    cliente_ref = _extract_cliente_ref(msg_l)

    # 2) Resolver periodo
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

    # 3) Intent por reglas
    intent = _detect_intent_rules(msg_l)

    # 4) Asegurar contexto por sesión (siempre dict)
    ctx = SESSION_CTX.get(req.session_id)
    if not isinstance(ctx, dict):
        ctx = {}
        SESSION_CTX[req.session_id] = ctx

    # 5) Manejo "solo año" cuando hay pending_month en contexto
    m_year_only = re.fullmatch(r"\s*(20\d{2})\s*", msg)
    if (not periodo) and m_year_only and ctx.get("pending_month"):
        year = int(m_year_only.group(1))
        month = int(ctx["pending_month"])
        periodo = f"{year:04d}-{month:02d}"

        if intent == "unknown" and ctx.get("pending_intent"):
            intent = ctx["pending_intent"]

        # limpiar contexto pendiente
        ctx.pop("pending_month", None)
        ctx.pop("pending_intent", None)

    # 6) Identificación simple: si solo mandó CUIT/email y no hay keywords ni período
    if cliente_ref and intent == "unknown" and not periodo:
        intent = "identify"

    # 7) Guardar cliente_ref en contexto si viene en el mensaje
    if cliente_ref:
        ctx["cliente_ref"] = cliente_ref

    # 8) Resolver id_cliente (ACÁ estaba el bug: id_cliente no existía)
    id_cliente = ctx.get("id_cliente")

    # Si todavía no hay id_cliente pero hay cliente_ref en el mensaje o en el contexto: asegurar cliente
    ref = cliente_ref or ctx.get("cliente_ref")
    if (not id_cliente) and ref:
        id_cliente = ensure_cliente(db, ref)
        ctx["id_cliente"] = id_cliente

    # 9) Si intent es identify: confirmar y esperar consulta
    if intent == "identify":
        data = {"id_cliente": id_cliente} if id_cliente else None
        return AgentResponse(
            intent="identify",
            reply="Perfecto ✅ Ya te identifiqué. Ahora decime qué necesitás: IVA (con período), ventas, compras, resultado, vencimientos, situación fiscal o documentos.",
            missing=[],
            data=data,
        )

    # 10) Si aún no hay identificación (ni id_cliente ni cliente_ref), pedirla
    if not (id_cliente or ctx.get("cliente_ref")):
        return AgentResponse(
            intent=intent,
            reply="Necesito tu CUIT o email para identificarte. Ej: 'cuit 30-12345678-9' o 'email nombre@dominio.com'.",
            missing=["cliente_ref"],
            data=None,
        )

    # 11) Validación periodo requerido
    needs_periodo = intent in {
        "iva_resumen_periodo",
        "ventas_resumen_periodo",
        "compras_resumen_periodo",
        "resultado_periodo",
    }

    if needs_periodo and not periodo:
        if pending_month:
            ctx["pending_month"] = pending_month
            ctx["pending_intent"] = intent

        if periodo_error:
            return AgentResponse(intent=intent, reply=periodo_error, missing=["periodo"], data=None)

        pregunta = "¿De qué período? (YYYY-MM, ej: 2025-12)"
        if intent == "iva_resumen_periodo":
            pregunta = "¿De qué período querés el IVA? (YYYY-MM, ej: 2025-12)"
        elif intent == "ventas_resumen_periodo":
            pregunta = "¿De qué período querés las ventas? (YYYY-MM, ej: 2025-12)"
        elif intent == "compras_resumen_periodo":
            pregunta = "¿De qué período querés las compras? (YYYY-MM, ej: 2025-12)"
        elif intent == "resultado_periodo":
            pregunta = "¿De qué período querés el resultado? (YYYY-MM, ej: 2025-12)"

        return AgentResponse(intent=intent, reply=pregunta, missing=["periodo"], data=None)

    # 12) Ejecutar intent

    if intent == "vencimientos_proximos":
        modo = "proximos"
        if "este mes" in msg_l or "en el mes" in msg_l:
            modo = "mes_actual"

        v = _get_vencimientos(db, id_cliente, limit=10, mode=modo)
        vencidos_30 = _count_vencidos_recientes(db, id_cliente, days=30)

        if not v:
            reply = "No tenés vencimientos pendientes próximos 🎉" if modo == "proximos" else "No tenés vencimientos pendientes para este mes 🎉"
            if vencidos_30 > 0:
                reply += f"\n⚠️ Además tenés {vencidos_30} vencido(s) en los últimos 30 días."
            return AgentResponse(intent=intent, reply=reply, missing=[], data={"vencimientos": []})

        titulo = "Estos son tus próximos vencimientos:\n" if modo == "proximos" else "Estos son tus vencimientos de este mes:\n"
        lines = [f"- {x['impuesto']} {x['periodo']} → {x['fecha_vto']}" for x in v]
        reply = titulo + "\n".join(lines)

        if vencidos_30 > 0:
            reply += f"\n\n⚠️ Tenés {vencidos_30} vencido(s) en los últimos 30 días."

        return AgentResponse(intent=intent, reply=reply, missing=[], data={"vencimientos": v})

    if intent == "iva_resumen_periodo":
        iva = _get_iva_periodo(db, id_cliente, periodo)
        if not iva:
            return AgentResponse(intent=intent, reply=f"No encuentro resumen de IVA para el período {periodo}.", missing=[], data=None)

        reply = (
            f"IVA {periodo} ({iva.get('razon_social')}): {iva.get('resultado')}.\n"
            f"• Débito: {iva.get('iva_debito')}\n"
            f"• Crédito: {iva.get('iva_credito')}\n"
            f"• Percepciones: {iva.get('perc_iva')}\n"
            f"• Retenciones: {iva.get('ret_iva')}\n"
            f"• Saldo: {iva.get('saldo_iva_calculado')}"
        )
        return AgentResponse(intent=intent, reply=reply, missing=[], data={"iva": iva})

    if intent == "ventas_resumen_periodo":
        ventas = _get_ventas_periodo(db, id_cliente, periodo)

        if not ventas:
            return AgentResponse(
                intent=intent,
                reply=f"No encuentro ventas para el período {periodo}.",
                missing=[],
                data=None,
            )

        reply = (
            f"Ventas {periodo} ({ventas.get('razon_social')}):\n"
            f"• Neto: {ars(ventas.get('ventas_neto'))}\n"
            f"• IVA: {ars(ventas.get('ventas_iva'))}\n"
            f"• Total: {ars(ventas.get('ventas_total'))}"
        )

        return AgentResponse(
            intent=intent,
            reply=reply,
            missing=[],
            data={"ventas": ventas},
        )



    if intent == "compras_resumen_periodo":
        compras = _get_compras_periodo(db, id_cliente, periodo)

        if not compras:
            return AgentResponse(
                intent=intent,
                reply=f"No encuentro resumen de compras para el período {periodo}.",
                missing=[],
                data=None,
            )

        reply = (
            f"Compras {periodo} ({compras.get('razon_social')}):\n"
            f"• Neto: {ars(compras.get('compras_neto'))}\n"
            f"• IVA: {ars(compras.get('compras_iva'))}\n"
            f"• Total: {ars(compras.get('compras_total'))}"
        )

        return AgentResponse(
            intent=intent,
            reply=reply,
            missing=[],
            data={"compras": compras},
        )


    if intent == "resultado_periodo":
        res = _get_resultado_periodo(db, id_cliente, periodo)

        if not res:
            return AgentResponse(
                intent=intent,
                reply=f"No encuentro resultado para el período {periodo}.",
                missing=[],
                data=None,
            )

        # Campos típicos (ajustá si tu vista usa otros nombres)
        ventas_total = res.get("ventas_total") or res.get("total_ventas") or res.get("ventas") or 0
        compras_total = res.get("compras_total") or res.get("total_compras") or res.get("compras") or 0

        # Resultado final (si la vista ya lo trae lo usamos, si no lo calculamos)
        resultado = res.get("resultado") or res.get("resultado_total")
        if resultado is None:
            try:
                resultado = float(ventas_total or 0) - float(compras_total or 0)
            except Exception:
                resultado = 0

        estado = "GANANCIA" if (resultado or 0) >= 0 else "PÉRDIDA"

        reply = (
            f"Resultado {periodo} ({res.get('razon_social')}): {estado}\n"
            f"• Ventas: {ars(ventas_total)}\n"
            f"• Compras: {ars(compras_total)}\n"
            f"• Resultado: {ars(resultado)}"
        )

        return AgentResponse(
            intent=intent,
            reply=reply,
            missing=[],
            data={"resultado": res},
        )


    if intent == "situacion_fiscal":
        impuestos = _get_situacion_fiscal(db, id_cliente)

        if not impuestos:
            return AgentResponse(
                intent=intent,
                reply="No tengo impuestos asignados a tu cliente todavía. Decime cuáles corresponde cargar (ej: Monotributo + IIBB) y lo registramos.",
                missing=[],
                data={"impuestos": []},
            )

        # Armamos reply legible
        lines = []
        for x in impuestos:
            if "periodicidad" in x and x.get("periodicidad"):
                lines.append(f"- {x['impuesto']} ({x['periodicidad']})")
            else:
                lines.append(f"- {x['impuesto']}")

        reply = "Tu situación fiscal (impuestos asociados):\n" + "\n".join(lines)

        return AgentResponse(
            intent=intent,
            reply=reply,
            missing=[],
            data={"impuestos": impuestos},
        )


    if intent == "documentos":
        docs = _get_documentos(db, id_cliente, limit=10)

        if not docs:
            return AgentResponse(
                intent=intent,
                reply="No tengo documentos cargados para tu cliente todavía.",
                missing=[],
                data={"documentos": []},
            )

        lines = []
        for d in docs:
            tipo = d.get("tipo") or "documento"
            titulo = d.get("titulo") or "Documento"
            url_archivo = d.get("url_archivo")
            fecha = d.get("fecha_documento") or d.get("created_at")

            extra = []
            if fecha:
                extra.append(str(fecha))
            extra_txt = f" ({' • '.join(extra)})" if extra else ""

            if url_archivo:
                # 📎 nombre embebido con la URL
                lines.append(f"- [{tipo}] [{titulo}]({url_archivo}){extra_txt}")
            else:
                lines.append(f"- [{tipo}] {titulo}{extra_txt}")

        reply = "Documentos disponibles:\n" + "\n".join(lines)

        return AgentResponse(
            intent=intent,
            reply=reply,
            missing=[],
            data={"documentos": docs},
        )




    return AgentResponse(
        intent="unknown",
        reply="No entendí la consulta. Podés pedir: vencimientos, IVA (con período), ventas, compras, resultado, situación fiscal o documentos.",
        missing=[],
        data=None,
    )

