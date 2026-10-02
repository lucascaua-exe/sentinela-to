import os
import tempfile
from pathlib import Path

# Precisa estar definido antes de importar backend.main, que abre o banco na importação.
os.environ["SENTINELA_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="sentinela-test-")) / "test.db")
os.environ["SENTINELA_ADMIN_USER"] = "admin"
os.environ["SENTINELA_ADMIN_PASSWORD"] = "senha-de-teste-123"
os.environ["SENTINELA_DEVICE_TOKEN"] = "token-de-teste"
# Os testes usam o compositor local; o caminho com LLM é exercitado com transporte simulado.
os.environ.pop("OPENAI_API_KEY", None)
