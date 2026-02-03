from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.agent import router as agent_router
from app.api.health import router as health_router

from app.api.wa import router as wa_router

from app.api.ycloud import router as ycloud_router



app = FastAPI(title="Bot Contable")

# CORS (si hoy lo tenías en main.py, lo dejamos acá)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # luego lo ajustamos
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(health_router)
app.include_router(agent_router)
app.include_router(wa_router)
app.include_router(ycloud_router)