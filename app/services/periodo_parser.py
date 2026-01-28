import re
from datetime import datetime
from typing import Optional, Tuple

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


def _add_months(y: int, m: int, delta: int) -> Tuple[int, int]:
    total = (y * 12 + (m - 1)) + delta
    new_y = total // 12
    new_m = (total % 12) + 1
    return new_y, new_m


def parse_periodo_es(texto: str):
    """
    return (periodo, error, pending_month)
    - periodo: "YYYY-MM" si pudo resolver
    - error: mensaje si detectó mes pero faltó año
    - pending_month: int (1-12) si detectó mes sin año
    """
    t = (texto or "").lower().strip()

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

    if "este mes" in t or "mes actual" in t:
        return _to_periodo(now.year, now.month), None, None

    if "mes pasado" in t or "mes anterior" in t:
        y, m = _add_months(now.year, now.month, -1)
        return _to_periodo(y, m), None, None

    if "mes que viene" in t or "proximo mes" in t or "próximo mes" in t or "mes siguiente" in t:
        y, m = _add_months(now.year, now.month, 1)
        return _to_periodo(y, m), None, None

    return None, None, None
