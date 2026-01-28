from typing import Dict, Any

# Contexto en memoria por session_id.
# Ejemplo:
# SESSION_CTX["s1"] = {"id_cliente": 15, "pending_month": 7, "pending_intent": "iva_resumen_periodo"}
SESSION_CTX: Dict[str, Dict[str, Any]] = {}
