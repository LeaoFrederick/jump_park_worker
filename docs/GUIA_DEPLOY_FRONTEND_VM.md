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

1. O **FastAPI** já está configurado para servir a interface visual no arquivo [`src/static/index.html`](file:///e:/drive/Clientes/FRANCISCO%202025/RESTRIÇÕES%20CENTRO/jump_park_worker/src/static/index.html) quando qualquer usuário acessa a raiz `/`.
2. O **Nginx** roda na porta padrão `80` (HTTP) e `443` (HTTPS) e faz o repasse transparente para o FastAPI na porta `8000`.
3. O usuário acessa um link limpo como `https://painel-jump.duckdns.org` ou `https://painel.suaempresa.com.br` no navegador do celular.

---

## 📋 Opção A (Recomendada & 100% Grátis): Domínio DuckDNS + Nginx + SSL

### 1. Criar um subdomínio gratuito no DuckDNS (Leva 1 minuto)
1. Acesse [duckdns.org](https://www.duckdns.org) e faça login com qualquer conta (Google, GitHub, etc.).
2. No campo **sub domain**, escolha um nome (exemplo: `painel-jumppark` ou `estacionamento-restricoes`).
3. No campo **IP**, coloque o IP público da sua VM Oracle: `168.138.131.8`.
4. Clique em **add domain**.
   > Pronto! Seu domínio gratuito será: `https://painel-jumppark.duckdns.org`.

---

### 2. Liberar as portas 80 e 443 na Oracle Cloud & Linux
Na VM Oracle (Ubuntu/Debian), execute no terminal:

```bash
# 1. Liberar portas no firewall local da VM (iptables / ufw)
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save

# 2. Instalar Nginx e Certbot (para o certificado SSL gratuito)
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
```

> **Atenção (Oracle Cloud Security List):**  
> No painel web da Oracle Cloud, na sua **VCN (Virtual Cloud Network) ➔ Security Lists ➔ Ingress Rules**, certifique-se de que as portas `80` (HTTP) e `443` (HTTPS) estão abertas com Source CIDR `0.0.0.0/0`.

---

### 3. Configurar o Nginx como Proxy Reverso

Crie o arquivo de configuração do Nginx:

```bash
sudo nano /etc/nginx/sites-available/jumppark
```

Cole o conteúdo abaixo (substitua `painel-jumppark.duckdns.org` pelo seu subdomínio escolhido):

```nginx
server {
    listen 80;
    server_name painel-jumppark.duckdns.org;

    location / {
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
cd /caminho/do/projeto/jump_park_worker
git pull origin main
sudo systemctl restart jump_park_worker.service
```
*(O FastAPI recarregará o novo `src/static/index.html` instantaneamente).*
