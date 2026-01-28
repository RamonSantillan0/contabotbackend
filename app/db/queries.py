from typing import Optional, Dict, Any, List
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
import re


def _fetch_one_dict(db, sql: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    row = db.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def _fetch_all_dicts(db, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def _get_vencimientos(db, id_cliente: int, limit: int = 10, mode: str = "proximos") -> List[Dict[str, Any]]:
    """
    mode:
      - "mes_actual": vencimientos pendientes del mes calendario actual
      - "proximos": vencimientos pendientes desde hoy en adelante
    """
    if mode == "mes_actual":
        fecha_filter = """
            v.fecha_vto >= DATE_FORMAT(CURDATE(), '%Y-%m-01')
            AND v.fecha_vto <  DATE_ADD(DATE_FORMAT(CURDATE(), '%Y-%m-01'), INTERVAL 1 MONTH)
        """
    else:
        fecha_filter = "v.fecha_vto >= CURDATE()"

    sql = f"""
        SELECT v.id_vencimiento, v.periodo, v.fecha_vto, v.estado,
               i.nombre AS impuesto
        FROM vencimientos v
        JOIN impuestos i ON i.id_impuesto = v.id_impuesto
        WHERE v.id_cliente = :id_cliente
          AND v.estado = 'PENDIENTE'
          AND ({fecha_filter})
        ORDER BY v.fecha_vto ASC
        LIMIT :limit
    """

    return _fetch_all_dicts(db, sql, {"id_cliente": id_cliente, "limit": limit})


def _count_vencidos_recientes(db, id_cliente: int, days: int = 30) -> int:
    row = _fetch_one_dict(
        db,
        """
        SELECT COUNT(*) AS c
        FROM vencimientos
        WHERE id_cliente = :id_cliente
          AND estado = 'VENCIDO'
          AND fecha_vto >= DATE_SUB(CURDATE(), INTERVAL :days DAY)
        """,
        {"id_cliente": id_cliente, "days": days},
    )
    return int(row["c"]) if row and row.get("c") is not None else 0


def _get_iva_periodo(db, id_cliente: int, periodo: str) -> Optional[Dict[str, Any]]:
    # IVA: vista vw_iva_totales
    return _fetch_one_dict(
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


def _is_email(s: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", (s or "").strip(), flags=re.IGNORECASE))


def _norm_cuit(s: str) -> str:
    # deja solo dígitos y vuelve a formato XX-XXXXXXXX-X si tiene 11 dígitos
    digits = re.sub(r"\D", "", (s or ""))
    if len(digits) == 11:
        return f"{digits[:2]}-{digits[2:10]}-{digits[10]}"
    return s.strip()


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")

def ensure_cliente(db, cliente_ref: str) -> int:
    """
    Busca o crea un cliente según CUIT o email y devuelve id_cliente.
    """
    ref = (cliente_ref or "").strip().lower()
    if not ref:
        raise ValueError("cliente_ref vacío")

    # =========================
    # EMAIL
    # =========================
    if _is_email(ref):
        row = _fetch_one_dict(
            db,
            "SELECT id_cliente FROM clientes WHERE LOWER(email)=:email LIMIT 1",
            {"email": ref},
        )
        if row:
            return int(row["id_cliente"])

        db.execute(
            text("""
                INSERT INTO clientes (razon_social, cuit, email, telefono, provincia, domicilio, activo)
                VALUES ('Cliente sin nombre', NULL, :email, NULL, 'SIN DEFINIR', NULL, 1)
            """),
            {"email": ref},
        )
        db.commit()

        row2 = _fetch_one_dict(
            db,
            "SELECT id_cliente FROM clientes WHERE LOWER(email)=:email ORDER BY id_cliente DESC LIMIT 1",
            {"email": ref},
        )
        return int(row2["id_cliente"])

    # =========================
    # CUIT
    # =========================
    cuit_digits = _only_digits(ref)
    if len(cuit_digits) != 11:
        raise ValueError("CUIT inválido")

    # 🔴 CLAVE: buscar sin guiones
    row = _fetch_one_dict(
        db,
        """
        SELECT id_cliente
        FROM clientes
        WHERE REPLACE(cuit, '-', '') = :cuit
        LIMIT 1
        """,
        {"cuit": cuit_digits},
    )
    if row:
        return int(row["id_cliente"])

    # Formato estándar para guardar
    cuit_formatted = f"{cuit_digits[:2]}-{cuit_digits[2:10]}-{cuit_digits[10:]}"

    db.execute(
        text("""
            INSERT INTO clientes (razon_social, cuit, email, telefono, provincia, domicilio, activo)
            VALUES ('Cliente sin nombre', :cuit, NULL, NULL, 'SIN DEFINIR', NULL, 1)
        """),
        {"cuit": cuit_formatted},
    )
    db.commit()

    row2 = _fetch_one_dict(
        db,
        """
        SELECT id_cliente
        FROM clientes
        WHERE REPLACE(cuit, '-', '') = :cuit
        ORDER BY id_cliente DESC
        LIMIT 1
        """,
        {"cuit": cuit_digits},
    )
    return int(row2["id_cliente"])

def _get_ventas_periodo(db, id_cliente: int, periodo: str):
    return _fetch_one_dict(
        db,
        """
        SELECT *
        FROM vw_ventas_resumen_periodo
        WHERE id_cliente=:id_cliente AND periodo=:periodo
        LIMIT 1
        """,
        {"id_cliente": id_cliente, "periodo": periodo},
    )


def _get_compras_periodo(db, id_cliente: int, periodo: str):
    return _fetch_one_dict(
        db,
        """
        SELECT *
        FROM vw_compras_resumen_periodo
        WHERE id_cliente=:id_cliente AND periodo=:periodo
        LIMIT 1
        """,
        {"id_cliente": id_cliente, "periodo": periodo},
    )


def _get_resultado_periodo(db, id_cliente: int, periodo: str):
    return _fetch_one_dict(
        db,
        """
        SELECT *
        FROM vw_resultado_periodo
        WHERE id_cliente=:id_cliente AND periodo=:periodo
        LIMIT 1
        """,
        {"id_cliente": id_cliente, "periodo": periodo},
    )

def _get_situacion_fiscal(db, id_cliente: int):
    rows = db.execute(
        text("""
            SELECT
                i.id_impuesto,
                i.nombre AS impuesto,
                i.periodicidad
            FROM cliente_impuesto ci
            JOIN impuestos i ON i.id_impuesto = ci.id_impuesto
            WHERE ci.id_cliente = :id_cliente
            ORDER BY i.nombre ASC
        """),
        {"id_cliente": id_cliente},
    ).mappings().all()

    return [dict(r) for r in rows]


def _get_documentos(db, id_cliente: int, limit: int = 10):
    rows = db.execute(
        text("""
            SELECT
                d.id_documento,
                d.id_cliente,
                COALESCE(d.tipo, 'documento')   AS tipo,
                COALESCE(d.titulo, 'Documento') AS titulo,
                d.url_archivo,
                d.fecha_documento,
                d.created_at
            FROM documentos d
            WHERE d.id_cliente = :id_cliente
            ORDER BY COALESCE(d.fecha_documento, d.created_at) DESC
            LIMIT :limit
        """),
        {"id_cliente": id_cliente, "limit": limit},
    ).mappings().all()

    return [dict(r) for r in rows]

