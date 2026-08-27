"""
main.py
Ponto de entrada (Entrypoint) principal do Jump Park Worker.

Mantém 100% de compatibilidade com os scripts de execução e serviço systemd na VM.
Orquestra o disparo através do pacote modular `src`.
"""

import sys
from pathlib import Path

# Garante que o diretório raiz está no PYTHONPATH
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.main import main

if __name__ == "__main__":
    main()
