import os

# -----------------------------
# Ollama Cloud config
# -----------------------------
OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE", "https://ollama.com/api")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")  # obligatorio si usás Ollama Cloud
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:120b")
