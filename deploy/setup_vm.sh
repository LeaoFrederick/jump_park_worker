#!/bin/bash

# ============================================================
#  JumpPark Worker — Setup na VM Oracle
# ============================================================

set -e  # Para se qualquer comando falhar

REPO_URL="https://github.com/LeaoFrederick/jump_park_worker.git"
PROJECT_DIR="$HOME/jump_park_worker"

echo ""
echo "=================================================="
echo "  JumpPark Worker — Setup na VM Oracle"
echo "=================================================="
echo ""

# 1. Clonar o repositório
if [ -d "$PROJECT_DIR" ]; then
    echo "[INFO] Pasta já existe. Atualizando com git pull..."
    cd "$PROJECT_DIR"
    git pull origin main
else
    echo "[INFO] Clonando repositório..."
    git clone "$REPO_URL" "$PROJECT_DIR"
    cd "$PROJECT_DIR"
fi

# 2. Criar ambiente virtual
echo ""
echo "[INFO] Criando ambiente virtual Python..."
python3 -m venv .venv

# 3. Ativar e instalar dependências
echo "[INFO] Instalando dependências..."
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt

# 4. Criar o .env se não existir
if [ ! -f ".env" ]; then
    echo ""
    echo "=================================================="
    echo "  ATENÇÃO: Arquivo .env não encontrado!"
    echo "  Cole o conteúdo do seu .env local abaixo."
    echo "  Pressione ENTER duas vezes ao terminar."
    echo "=================================================="
    echo ""
    content=""
    while IFS= read -r line; do
        [[ -z "$line" ]] && break
        content+="$line"$'\n'
    done
    echo "$content" > .env
    echo "[INFO] Arquivo .env criado com sucesso."
else
    echo "[INFO] Arquivo .env já existe. Pulando..."
fi

# 5. Testar execução
echo ""
echo "[INFO] Testando o worker por 5 segundos..."
timeout 5 python main.py || true

echo ""
echo "=================================================="
echo "  Setup concluído!"
echo ""
echo "  Para rodar manualmente:"
echo "    cd ~/jump_park_worker"
echo "    source .venv/bin/activate"
echo "    python main.py"
echo ""
echo "  Para configurar como serviço (24/7):"
echo "    sudo cp deploy/jump_worker.service /etc/systemd/system/"
echo "    sudo systemctl daemon-reload"
echo "    sudo systemctl enable --now jump_worker"
echo "=================================================="
