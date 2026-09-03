# 🚀 Guia Definitivo: Hospedando o Painel Web na sua VM Oracle

Este guia ensina como rodar a interface web diretamente na sua **VM da Oracle** com um **domínio simples e seguro (HTTPS)**, eliminando completamente as limitações do Google Apps Script (sem avisos de permissão, sem banners do Google e sem login obrigatório).

---

## 🌟 Vantagens de Hospedar na VM

| Característica | Google Apps Script | Na sua VM (Nginx + HTTPS) |
| :--- | :--- | :--- |
| **Avisos de Permissão** | ⚠️ Pede autorização Google ("App não verificado") | ✅ **Zero avisos — abre direto** |
| **Banners no Topo** | ❌ Banner de aviso do Google | ✅ **Visual 100% limpo e profissional** |
| **Compatibilidade** | ❌ Exige conta Google ativa | ✅ **Qualquer celular/computador abre** |
| **Instalação como App** | ❌ Não suporta bem PWA | ✅ **Pode "Adicionar à Tela de Início" (PWA)** |
| **Performance** | ⏳ Depende dos servidores do Google | ⚡ **Resposta instantânea direta da VM** |

---

## 🛠️ Como Funciona a Arquitetura

1. O **FastAPI** está configurado para ouvir **apenas em `127.0.0.1:8000`** (localhost). Ele **não aceita** conexões diretas da internet.
2. O **Nginx** roda nas portas `80` (HTTP) e `443` (HTTPS) e faz o repasse transparente para o FastAPI via proxy reverso.
3. O usuário acessa um link limpo como `https://painel-jumppark.duckdns.org` no navegador do celular.
4. **Rate Limiting** no Nginx protege contra ataques de força bruta e sobrecarregamento da VM micro.

```
Internet ──► Nginx (443/HTTPS) ──► FastAPI (127.0.0.1:8000) ──► Jump Park API
                 │
                 └── Rate Limit (5 req/s por IP)
                 └── Security Headers
                 └── SSL/TLS (Let's Encrypt)
```

---

## 📋 Opção A (Recomendada & 100% Grátis): Domínio DuckDNS + Nginx + SSL

### 1. Criar um subdomínio gratuito no DuckDNS (Leva 1 minuto)
1. Acesse [duckdns.org](https://www.duckdns.org) e faça login com qualquer conta (Google, GitHub, etc.).
2. No campo **sub domain**, escolha um nome (exemplo: `painel-jumppark` ou `estacionamento-restricoes`).
3. No campo **IP**, coloque o IP público da sua VM Oracle: `168.138.131.8`.
4. Clique em **add domain**.
   > Pronto! Seu domínio gratuito será: `https://painel-jumppark.duckdns.org`.

---

### 2. Liberar APENAS as portas 80 e 443 na Oracle Cloud & Linux

> ⚠️ **IMPORTANTE:** NÃO abra as portas 8000 ou 3306 na Security List da Oracle Cloud!
> O FastAPI roda somente em localhost e o MySQL deve ser acessível apenas localmente.

Na VM Oracle (Ubuntu), execute no terminal:

```bash
# 1. Liberar portas no firewall local da VM (iptables)
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save

# 2. Instalar Nginx e Certbot (para o certificado SSL gratuito)
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
```

> **Oracle Cloud Security List (Ingress Rules):**
> Certifique-se de que APENAS estas portas estão abertas:
> | Porta | Protocolo | Source CIDR | Descrição |
> |-------|-----------|-------------|-----------|
> | 22 | TCP | 0.0.0.0/0 | SSH |
> | 80 | TCP | 0.0.0.0/0 | HTTP (redirect → HTTPS) |
> | 443 | TCP | 0.0.0.0/0 | HTTPS (Nginx) |
> | — | ICMP | — | Padrão Oracle (manter) |
>
> **REMOVA** qualquer regra para as portas `8000`, `3306` e `3506`.

---

### 3. Configurar o Nginx como Proxy Reverso (com Rate Limiting e Security Headers)

Crie o arquivo de configuração do Nginx:

```bash
sudo nano /etc/nginx/sites-available/jumppark
```

Cole o conteúdo abaixo (substitua `painel-jumppark.duckdns.org` pelo seu subdomínio escolhido):

```nginx
# ── Rate Limiting ────────────────────────────────────────────────────────────
# Limita cada IP a 5 requisições por segundo (burst de 10 com delay).
# Protege a VM micro (1GB RAM) contra brute-force e varreduras de bots.
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=5r/s;

# ── Bloquear acesso direto por IP (sem domínio) ─────────────────────────────
server {
    listen 80 default_server;
    listen 443 default_server;
    server_name _;
    return 444;  # Fecha a conexão silenciosamente
}

# ── Servidor Principal ──────────────────────────────────────────────────────
server {
    listen 80;
    server_name painel-jumppark.duckdns.org;

    # Redirecionar HTTP → HTTPS
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name painel-jumppark.duckdns.org;

    # ── SSL será configurado automaticamente pelo Certbot ──
    # ssl_certificate     /etc/letsencrypt/live/painel-jumppark.duckdns.org/fullchain.pem;
    # ssl_certificate_key /etc/letsencrypt/live/painel-jumppark.duckdns.org/privkey.pem;

    # ── Security Headers ───────────────────────────────────────────────────
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # ── Proxy para FastAPI (somente localhost) ─────────────────────────────
    location / {
        limit_req zone=api_limit burst=10 delay=5;

        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ── Rate Limit mais restritivo para endpoints de autenticação ──────────
    location /api/auth/ {
        limit_req zone=api_limit burst=3 nodelay;

        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Ative o site e reinicie o Nginx:

```bash
sudo ln -sf /etc/nginx/sites-available/jumppark /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx
```

---

### 4. Gerar o Certificado SSL HTTPS Gratuito (Let's Encrypt)

Execute o comando mágico do Certbot:

```bash
sudo certbot --nginx -d painel-jumppark.duckdns.org
```

- Informe seu e-mail e aceite os termos (`Y`).
- O Certbot configurará o HTTPS automaticamente com renovação automática.

---

## 📋 Opção B: Usando seu Próprio Domínio (ex: `painel.suaempresa.com.br`)

Se você já tem um domínio próprio (no Registro.br, Hostinger, GoDaddy, Cloudflare, etc.):

1. Acesse o painel de DNS do seu domínio.
2. Crie uma nova entrada:
   - **Tipo:** `A`
   - **Nome / Host:** `painel` (ou `bloqueios`)
   - **Valor / Aponta para:** `168.138.131.8`
   - **TTL:** 1 hora (ou Automático)
3. Siga os mesmos passos do **Nginx** acima, usando o seu domínio no lugar do DuckDNS.

---

## 📱 Como os Funcionários usam no Celular

1. O funcionário clica no link: `https://painel-jumppark.duckdns.org`.
2. A tela abre **imediatamente**, sem pedir nenhuma permissão ou login do Google.
3. **Dica de Ouro:** No Chrome ou Safari do celular, o usuário clica nos 3 pontinhos e seleciona **"Adicionar à tela de início"**.
4. Um ícone do aplicativo aparecerá na tela do celular, abrindo o painel em tela cheia como se fosse um app nativo instalado da Play Store!

---

## 🔄 Atualizando o Código na VM

Para atualizar o frontend na VM com as novas alterações feitas no projeto:

```bash
cd ~/jump_park_worker
git pull origin main
sudo systemctl restart jump_worker
```
*(O FastAPI recarregará o novo `src/static/index.html` instantaneamente).*

---

## 🔒 Checklist de Segurança Pós-Deploy

Após o deploy, confirme que tudo está trancado:

- [ ] **Oracle Cloud Security Lists:** Apenas portas `22`, `80` e `443` abertas. Portas `8000`, `3306`, `3506` **REMOVIDAS**.
- [ ] **FastAPI:** Rodando em `127.0.0.1:8000` (apenas localhost).
- [ ] **Nginx:** Rodando com Rate Limit, Security Headers e SSL.
- [ ] **MySQL:** Acessível apenas localmente (sem porta 3306 exposta).
- [ ] **Teste:** Acessar `http://168.138.131.8:8000` no navegador deve retornar **Connection Refused** (porta fechada).
- [ ] **Teste:** Acessar `https://painel-jumppark.duckdns.org` deve abrir o painel normalmente.
