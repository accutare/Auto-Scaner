# Guia de instalação na VPS (DigitalOcean, Ubuntu 24.04)

Tempo estimado: 1 a 2 horas na primeira vez. Se ninguém aí usa terminal, entregue este guia a um técnico:
tudo abaixo é copiar e colar.

Antes de começar, tenha em mãos: as chaves do Dropbox (README, passo 2) e a chave da API da Anthropic.

## 1. Criar a chave de acesso (no seu PC Windows)

Abra o **PowerShell** e rode:

```
ssh-keygen -t ed25519 -C "docflow"
```
Aperte Enter nas perguntas (ou defina uma senha para a chave). Depois mostre a chave **pública**:

```
type $env:USERPROFILE\.ssh\id_ed25519.pub
```
Copie a linha inteira que aparecer (começa com `ssh-ed25519`). Ela vai para a DigitalOcean no passo 2.
A chave **sem** `.pub` é privada: nunca envie para ninguém.

## 2. Criar o servidor ("Droplet")

1. Crie a conta em digitalocean.com e adicione a forma de pagamento.
2. **Create → Droplets.**
3. Região: a mais próxima (ou a que você preferir; veja a observação sobre LGPD no README).
4. Imagem: **Ubuntu 24.04 (LTS)**. (A imagem "Docker" do Marketplace também serve e já traz o Docker.)
5. Plano: **Basic / Regular, 2 GB de RAM**.
6. Em **Backups**, marque a opção de backups automáticos.
7. Autenticação: **SSH Key** → *New SSH Key* → cole a chave pública do passo 1.
8. Nome: `docflow` → **Create Droplet**. Anote o **endereço IP** que aparecer.

## 3. Preparar o servidor

No PowerShell do seu PC (troque `IP` pelo endereço do Droplet):

```
scp -r docflow root@IP:/opt/
ssh root@IP
```
(`docflow` é a pasta do projeto descompactada.) Já dentro do servidor:

```
cd /opt/docflow
bash deploy/setup-servidor.sh
```
O script atualiza o sistema, liga as atualizações de segurança automáticas, ativa o firewall (só o SSH
fica aberto), instala o fail2ban e o Docker e, se houver chave SSH cadastrada, desativa o login por senha.

## 4. Configurar e subir o programa

```
cd /opt/docflow
cp .env.example .env
nano .env                     # preencha as chaves e salve (Ctrl+O, Enter, Ctrl+X)
mkdir -p dados
cp clientes.exemplo.csv dados/clientes.csv
nano dados/clientes.csv       # coloque os clientes reais
chmod 600 .env
docker compose up -d --build
```

Conferir se está rodando e o que está acontecendo:

```
docker compose ps
docker compose logs -f --tail=100
```
Você deve ver "Iniciado. Vigiando /Entrada...". Saia dos logs com Ctrl+C (o programa continua rodando).

## 5. Alerta de queda (muito recomendado)

Um programa parado não consegue avisar que parou. Por isso o aviso vem de fora:

1. Crie uma conta gratuita em **healthchecks.io**.
2. Crie um *check* com **Period = 5 minutes** e **Grace Time = 10 minutes**, e confirme que o seu e-mail
   está nas integrações.
3. Copie a **Ping URL** do check.
4. No servidor: `nano /opt/docflow/.env`, cole a URL em `HEALTHCHECK_URL=` e salve.
5. Aplique: `cd /opt/docflow && docker compose up -d`

O programa avisa a cada ciclo (cerca de 1 minuto). Se parar de avisar por mais de uns 15 minutos, o
healthchecks.io manda um e-mail. Em caso de erro no ciclo, ele também sinaliza falha na hora.

## 6. Teste real com poucos arquivos

1. Coloque 5 a 10 documentos reais na pasta **/Entrada** do Dropbox.
2. Espere uns 2 minutos e confira `/Processados`, `/Revisar` e `/Controle/Controle.xlsx`.
3. Veja os logs se algo não se comportar como esperado.
4. Ajuste `CONFIANCA_MINIMA` no `.env` conforme os resultados e rode `docker compose up -d`.

## 7. Rotina de manutenção

| Quando | O quê |
|---|---|
| Sempre que pedir ajuste | Substituir os arquivos em `/opt/docflow` e rodar `docker compose up -d --build` |
| De vez em quando | `docker compose logs --tail=200` para olhar erros |
| A cada 2 ou 3 meses | `ssh root@IP` e `apt update && apt upgrade -y`; reiniciar se pedir (`reboot`) |
| Ao adicionar cliente | Editar `/opt/docflow/dados/clientes.csv` (é relido a cada ciclo, não precisa reiniciar) |

## 8. Backups e recuperação

- **Banco de controle:** 1 vez por dia o programa envia uma cópia para `/Controle/backup/` no Dropbox, com
  7 cópias rotativas (`controle-seg.db`, `controle-ter.db`...). Para restaurar:
  `docker compose down`, copie a cópia desejada para `/opt/docflow/dados/controle.db` e rode
  `docker compose up -d`.
- **Máquina inteira:** o backup automático do Droplet que você ligou no passo 2.
- **Documentos:** continuam no Dropbox, que mantém o histórico de versões.
- **Guarde o `.env` num cofre de senhas** (ele não está em nenhum backup do programa).

## 9. Segurança: checklist

- [ ] Verificação em duas etapas no Dropbox, na DigitalOcean, no healthchecks.io e no console da Anthropic.
- [ ] `.env` só no servidor, com `chmod 600`, e nunca no GitHub (o `.gitignore` já impede por acidente).
- [ ] Repositório do GitHub **privado**, se usar GitHub.
- [ ] Se uma chave vazar, gere outra na hora (Dropbox App Console / console da Anthropic) e atualize o `.env`.
- [ ] Limite de gasto mensal configurado no console da Anthropic.

## Problemas comuns

| Sintoma | Provável causa |
|---|---|
| Nada acontece com arquivos novos | Veja `docker compose logs`. Confira permissões do app Dropbox e as chaves do `.env` |
| Tudo vai para /Revisar | O CNPJ do cliente não bate com `dados/clientes.csv`, ou a confiança mínima está alta demais |
| Erro de "refresh token" | O token foi gerado sem `token_access_type=offline`. Gere de novo (README, passo 2) |
| Container reiniciando em loop | Falta variável obrigatória no `.env`. Os logs dizem qual |
