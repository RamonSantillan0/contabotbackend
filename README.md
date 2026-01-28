# Bot Contable Backend (FastAPI)

Backend del Bot Contable construido con **FastAPI**.  
Expone endpoints HTTP para el chat y se conecta a una base de datos **PostgreSQL** (Render) vía `DATABASE_URL`.

---

## 🚀 Stack
- Python 3.11+ (recomendado)
- FastAPI + Uvicorn
- SQLAlchemy
- PostgreSQL (Render)
- (Opcional) Ollama / LLM provider vía variables de entorno

---

## 📁 Estructura (resumen)
```
app/
  main.py               # FastAPI app
  db/
    db.py               # Engine + Session
    queries.py          # Consultas SQL/SQLAlchemy
requirements.txt
```

---

## ✅ Requisitos
- Python 3.11+
- pip
- (Opcional) Docker para migración MySQL → Postgres

---

## ⚙️ Configuración (variables de entorno)

Crear un archivo `.env` **solo en local** (NO subirlo a GitHub):

```env
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
CORS_ORIGINS=http://localhost:3000
```

### Variables opcionales (si aplican)
```env
OLLAMA_API_BASE=http://localhost:11434
OLLAMA_API_KEY=
OLLAMA_MODEL=llama3
```

> Importante: si tu `DATABASE_URL` viene como `postgres://...`, el backend debe normalizarlo a `postgresql://...` antes de crear el engine.

---

## 🧪 Correr en local

### 1) Crear entorno virtual e instalar dependencias
```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate

pip install -r requirements.txt
```

### 2) Ejecutar
```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Abrir:
- API: `http://127.0.0.1:8000`
- Docs Swagger: `http://127.0.0.1:8000/docs`

---

## 🌐 CORS
En producción, **no uses `*`**.  
Configurar `CORS_ORIGINS` con el dominio real del frontend, por ejemplo:

```env
CORS_ORIGINS=https://tu-frontend.onrender.com
```

---

## 🛢️ Base de datos (PostgreSQL)

### Conexión por `DATABASE_URL`
El backend usa `DATABASE_URL` para conectarse.

Ejemplo:
```env
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

---

## 🔁 Migración MySQL → PostgreSQL (recomendado con pgloader)

### Opción recomendada (si tenés dump .sql)
1) Levantar MySQL temporal (Docker)
2) Importar el dump
3) Migrar a Postgres (Render) con pgloader
4) Recrear VIEWS y ajustar queries MySQL-específicas a Postgres

#### 1) MySQL temporal + import
```bash
docker run --name tmp-mysql \
  -e MYSQL_ALLOW_EMPTY_PASSWORD=yes \
  -e MYSQL_DATABASE=ramon_agenteia \
  -p 3307:3306 -d mariadb:10.4

docker exec -i tmp-mysql mysql -uroot ramon_agenteia < ramon_agenteia.sql
```

#### 2) Migrar con pgloader
```bash
pgloader mysql://root@localhost:3307/ramon_agenteia \
  "postgresql://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require"
```

> En Render, suele requerirse `sslmode=require` al migrar desde tu PC.

#### 3) Views
Si tu backend consume **VIEWS** (ej: resúmenes de IVA/ventas/compras), recrearlas en Postgres luego de la migración.

---

## 🚀 Deploy en Render (Web Service)

### Configuración en Render
- **Build Command**
```bash
pip install -r requirements.txt
```

- **Start Command**
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

### Environment Variables (Render)
- `DATABASE_URL` = **Internal Database URL** de Render Postgres
- `CORS_ORIGINS` = URL del frontend (Render)
- (opcionales) `OLLAMA_*`

---

## ✅ Healthcheck
Si el proyecto incluye endpoint de salud, sugerido:
- `GET /health`

Ejemplo esperado:
```json
{ "status": "ok" }
```

---

## 🧾 Notas
- No subir al repo:
  - `.env`
  - `.venv/`
  - `__pycache__/`
- En producción, usar Postgres y reemplazar funciones MySQL como:
  - `CURDATE()`, `DATE_FORMAT`, `DATE_ADD`, `DATE_SUB`  
  por equivalentes Postgres (`current_date`, `date_trunc`, `interval`, etc.)

---

## 📄 Licencia
Uso interno / privado (ajustar si se publica).
