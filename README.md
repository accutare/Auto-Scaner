# docflow — leitura automática de documentos contábeis (Dropbox → IA → organização)

## O que ele faz

1. O scanner salva os arquivos na pasta **/Entrada** do Dropbox.
2. O programa (rodando num servidor) vê o arquivo novo, espera ele terminar de sincronizar e baixa.
3. A IA lê o documento e extrai: tipo, cliente/CNPJ, data, competência, valor, fornecedor, nº e chave da NF-e.
4. Se estiver tudo claro e o cliente existir na lista, o arquivo é movido para
   `/Processados/<Cliente>/<Ano>/<Mês>/<tipo>/` com nome padronizado.
5. Se houver dúvida (ilegível, confiança baixa, cliente não encontrado, duplicado), vai para **/Revisar**.
   Se der erro técnico 3 vezes, vai para **/Erros**.
6. Cada documento vira uma linha no banco; o arquivo **/Controle/Controle.xlsx** é regerado no Dropbox
   (é uma cópia para consulta: não edite nele, pois ele é sobrescrito).

O envio ao aplicativo de destino é a **próxima etapa** (ver "Próximos passos").

## Instalação

### 1. Contas necessárias
- **Dropbox** (a que já recebe o scanner).
- **Anthropic API**: crie a conta em console.anthropic.com, adicione crédito e gere uma *API key*.
- **Um servidor pequeno** que rode Docker (ex.: uma VPS básica de 1 GB de RAM, Railway, Render ou Fly.io).
  O programa é leve; o custo principal é o uso da IA, por documento.

### 2. Criar o app no Dropbox (uma vez)
1. Acesse dropbox.com/developers/apps → **Create app** → *Scoped access* → *Full Dropbox*.
2. Na aba **Permissions**, marque: `files.metadata.read`, `files.metadata.write`,
   `files.content.read`, `files.content.write`. Clique em **Submit**.
3. Na aba **Settings**, copie **App key** e **App secret**.
4. Gere o *refresh token* (isso não expira). Abra no navegador, trocando SUA_APP_KEY:

   `https://www.dropbox.com/oauth2/authorize?client_id=SUA_APP_KEY&response_type=code&token_access_type=offline`

   Autorize e copie o **código** exibido. Depois, num terminal:

   ```
   curl https://api.dropboxapi.com/oauth2/token \
     -d code=O_CODIGO -d grant_type=authorization_code \
     -u SUA_APP_KEY:SUA_APP_SECRET
   ```
   Na resposta, copie o valor de `refresh_token`.

### 3. Configurar
```
cp .env.example .env        # preencha as chaves
mkdir -p dados
cp clientes.exemplo.csv dados/clientes.csv   # edite com seus clientes reais (cnpj_cpf;nome)
```
O CNPJ/CPF pode ter pontos e traços; o programa usa só os dígitos.

### 4. Subir
```
docker compose up -d --build
```
O `docker-compose.yml` já cuida do reinício automático e do limite de tamanho dos logs.

**Para colocar numa VPS (DigitalOcean etc.) com firewall, backup e alerta de queda, siga o `GUIA-VPS.md`.**

Ver o que está acontecendo: `docker logs -f docflow`

### 5. Teste com poucos arquivos
Coloque 5 a 10 documentos reais em **/Entrada**, espere uns 2 minutos e confira
`/Processados`, `/Revisar` e `/Controle/Controle.xlsx`. Ajuste `CONFIANCA_MINIMA` conforme os resultados
e comece com poucos clientes antes de liberar para todos.

## Gerenciar sem mexer no servidor
- **Adicionar/alterar clientes:** edite `clientes.csv` na pasta de dados do servidor (é relido a cada ciclo).
- **Corrigir um documento em /Revisar:** mova o arquivo para o lugar certo manualmente (ou ajuste a lista de
  clientes e devolva o arquivo para /Entrada que ele é reprocessado).
  Observação: um arquivo idêntico a um já registrado é tratado como duplicado; para reprocessar um item que
  ficou pendente, renomeie ou escaneie de novo.

## Segurança e LGPD
- Os documentos têm dados pessoais e financeiros. Use senha forte e verificação em duas etapas no Dropbox.
- Guarde o `.env` só no servidor. Quem tem a chave da API ou o refresh token tem acesso aos dados.
- Verifique os termos de retenção de dados da API da Anthropic para o seu plano e informe isso na política
  de privacidade do escritório.
- O programa **nunca apaga** originais: só move.

## Próximos passos
1. **Envio ao aplicativo de destino** (Domínio, Omie, Acessórias ou outro): o registro já guarda os dados
   com status `OK`; falta o módulo que envia por API ou arquivo de importação e muda o status para `ENVIADO`.
2. Alerta por e-mail/mensagem quando houver erro ou fila de revisão grande.
3. Painel simples de acompanhamento.
