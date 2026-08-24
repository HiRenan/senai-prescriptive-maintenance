# Painel de demonstração

Este diretório contém os dois fluxos da demonstração: informar as 18 features,
executar `POST /analysis` e ler o resultado de forma auditável; e operar o ciclo
documental mínimo publicado pela API v1.

O painel não tem framework, bundler nem dependência de execução. São módulos ESM
servidos diretamente ao navegador por um processo Node mínimo.

## Estrutura

```text
apps/web/
├── server.mjs        # liveness, arquivos estáticos e proxy de mesma origem
├── src/
│   ├── index.html    # documento e marcos de acessibilidade
│   ├── styles.css    # tokens, tons dos desfechos e layout
│   ├── main.js       # ponto de entrada e ligação do fluxo
│   ├── api/          # clientes da análise e do ciclo documental
│   ├── core/         # regras puras: entrada, decodificação e apresentação
│   ├── generated/    # contrato derivado do OpenAPI v1, não editar à mão
│   └── ui/           # construção de DOM do console e do laudo
└── tests/            # testes essenciais do fluxo com o runner do Node
```

## Contrato

`src/generated/analysis-contract.js`, `src/generated/document-contract.js` e os
arquivos `.d.ts` irmãos são gerados a partir de
[`apps/api/openapi/v1.json`](../api/openapi/v1.json). Nenhum tipo de request ou
response é escrito à mão. Os módulos publicam as 18 features, os cinco
desfechos, os sete estados documentais, as seis operações documentais, limites,
esquemas e os pares sintéticos de request/response na forma declarada pelo
contrato.

O gerador recusa um padrão de texto que não esteja ancorado ou que use
construção fora do subconjunto lido igual por Python e pelo navegador. Os
atalhos `\d`, `\w` e `\s` ficam de fora por isso: são Unicode de um lado e
ASCII do outro. Só classes explícitas e escapes literais são publicados.

Regenere e verifique a partir da raiz:

```powershell
uv run --frozen poe web-contract
uv run --frozen poe web-contract-check
```

O `.d.ts` é material de desenvolvimento: fica fora do contexto Docker e da
imagem.

## Fluxo

1. O console monta os 18 campos a partir do contrato, agrupados por métrica com
   os eixos X e Z lado a lado.
2. A entrada pode ser digitada, carregada de um exemplo sintético do contrato ou
   importada de um JSON colado ou de arquivo. A importação recusa chaves fora do
   contrato em vez de descartá-las em silêncio, e recusa um arquivo acima de
   64 KiB pelo tamanho declarado, antes de lê-lo.
3. O envio valida campo a campo antes de qualquer requisição e mostra o motivo
   junto do campo.
4. Durante a primeira execução o laudo mostra um esqueleto. Nas seguintes, o
   último resultado válido permanece visível e rotulado como anterior enquanto
   a região viva anuncia o estado.
5. O resultado é apresentado como um laudo: desfecho, próximo passo, prescrição,
   diagnóstico, suporte, abstenção, citações, vizinhos opacos, avisos e a
   comparação das features enviadas.

## Modo offline

O seletor no cabeçalho alterna por URL entre a API local e o modo offline
sintético. Em `?mode=offline`, a análise usa exclusivamente os cinco pares de
request/response gerados dos exemplos do OpenAPI v1 e a área documental não
consulta nem altera a API. Uma entrada modificada é recusada em vez de receber
um outcome inferido pelo painel.

O laudo identifica a fixture como origem e os cinco outcomes preservam as
mesmas regras de diagnóstico, abstenção, prescrição, citações e avisos do
contrato. Voltar a `./` reativa a API local.

## Decodificação da resposta

O contrato gerado publica a tabela de esquemas das cinco variantes de resposta.
O cliente decodifica o corpo contra a variante do desfecho recebido: membros
obrigatórios, ausência de qualquer propriedade que o contrato não declare,
constantes, enums, padrões e limites de texto, limites numéricos e limites de
lista, até cada elemento. Só o status de sucesso publicado vira resultado; um `201` ou um
`204` é resposta inesperada. O prazo do cliente cobre a leitura do corpo, então
uma resposta que começa e nunca termina vira tempo limite. Qualquer divergência
é apresentada como falha de contrato, com o próximo passo, em vez de virar um
laudo com lacunas ou rótulos inventados.

## Disponibilidade da prescrição

A prescrição tem quatro estados de apresentação e só o primeiro exibe conteúdo:

| Estado | Quando | O que aparece |
| --- | --- | --- |
| Emitida | `documented_fault` com prescrição válida | resumo, prioridade e ações |
| Não se aplica | `normal` | explicação de que condição normal não prescreve |
| Retida | abstenção do contrato | motivo da API e próximo passo |
| Indisponível | corpo que contradiz o contrato | nota de integridade |

A decisão vem da tabela de desfechos gerada do contrato, não de condições
escritas à mão. Um corpo que traga prescrição num desfecho que não prescreve, ou
que omita a prescrição em `documented_fault`, cai em indisponível e nada é
exibido como prescrição.

Essa checagem é independente da validação do cliente. O corpo contraditório é
recusado antes de chegar ao laudo, e mesmo assim a apresentação continua
incapaz de exibir uma prescrição que o contrato não autoriza.

## Ciclo documental

A área de documentos lista e consulta o estado atual, mostra última atualização,
vigência, decisão e falha sanitizada e oferece somente as transições aplicáveis:
aprovar ou rejeitar com motivo em `pending_approval`, e reprocessar em `rejected`
ou `failed`. Cada comando exige confirmação; durante uma requisição todos os
controles ficam inertes para impedir duplo envio. Se o comando terminar e a
atualização posterior da lista falhar, a tela preserva as duas informações sem
afirmar que a lista está atualizada.

`POST /documents` não é upload. A tela envia estritamente `filename`,
`media_type`, `size_bytes` e `sha256`; nenhum PDF, caminho local ou conteúdo é
lido ou transmitido. O recibo também não significa aprovação automática. Como o
contrato não publica versão nem instante de processamento, a tela não inventa
esses valores.

## Comparação de features

A comparação mostra os oito pares de eixo X e Z e as duas leituras de processo.
Cada par é escalado pela própria magnitude máxima, porque as 18 features não
compartilham unidade. Nada é comparado com o modelo, com os vizinhos ou com
qualquer limiar: o painel declara que a comparação é descritiva e não indica
causa, gravidade nem relação com o desfecho.

## Servidor

`server.mjs` responde:

| Rota | Comportamento |
| --- | --- |
| `GET /health/live` | `{"status":"ok"}` para o healthcheck do contêiner |
| `GET` de `src/` | `index.html`, CSS e módulos, com allowlist de extensão |
| `POST /api/analysis` | encaminha para `POST /analysis` da API |
| `GET`, `POST /api/documents` | lista ou registra metadados em `/documents` |
| `GET /api/documents/{document_id}` | consulta exatamente um documento |
| `POST /api/documents/{document_id}/approve` | registra aprovação |
| `POST /api/documents/{document_id}/reject` | registra rejeição com motivo |
| `POST /api/documents/{document_id}/reprocess` | solicita reprocessamento |

O proxy existe para que o navegador use a mesma origem da página e a API não
precise de exceção de CORS. A allowlist documental é gerada das seis operações
do OpenAPI; só substitui um `document_id` que corresponda integralmente ao padrão
publicado. Path arbitrário, query, método ou corpo fora da operação são recusados
sem chegar à API. Caminhos fora da raiz estática também são recusados.

Os dois sentidos são limitados e temporizados:

| Limite | Valor | Comportamento ao exceder |
| --- | --- | --- |
| Corpo recebido | 64 KiB | `413`, sem chamar a API |
| Leitura do corpo recebido | 15 s | `408`, sem chamar a API |
| Corpo devolvido pela API | 256 KiB | `502`, sem repassar |
| Resposta da API | 20 s, incluindo a leitura do corpo | `504` |

O proxy repassa apenas os status que o contrato publica e apenas com o tipo de
mídia do contrato, verificado por token exato; não segue redirecionamento. Fora
disso responde `502` com envelope próprio. O tipo de mídia entregue ao navegador
é sempre `application/json`.

Configure a API por `API_BASE_URL`; o padrão local é `http://127.0.0.1:8000` e o
Compose usa `http://api:8000`. `WEB_REQUEST_TIMEOUT_MS` e
`WEB_UPSTREAM_TIMEOUT_MS` ajustam os prazos em milissegundos, aceitos de 1 a
120000; qualquer outro valor é ignorado em favor do padrão.

## Execução local

Com a API em execução:

```powershell
corepack pnpm --filter @senai-prescriptive-maintenance/web start
```

Em contêineres, a partir da raiz:

```powershell
uv run --frozen poe applications-up
```

O painel fica em `127.0.0.1:3000`.

## Verificações

```powershell
uv run --frozen poe web-contract-check
uv run --frozen poe web-typecheck
uv run --frozen poe web-test
corepack pnpm exec playwright install chromium
uv run --frozen poe web-browser-test
```

A verificação de tipos usa TypeScript sobre JavaScript anotado com JSDoc, sem
etapa de build: os mesmos arquivos que rodam no navegador são os verificados. Os
testes usam o runner do Node e tomam os exemplos do próprio snapshot OpenAPI, de
modo que nenhum material original participa da suíte. Eles cobrem as seis
operações do proxy e do cliente, os sete estados, teclado, bloqueio de duplo
envio, sucesso com falha da atualização, rejeição inválida com foco e a
distinção entre carregamento e lista vazia. As três tarefas também fazem parte
de `uv run --frozen poe check`.

`web-browser-test` é uma tarefa separada: usa somente Playwright/Chromium para
provar zero chamadas à API no modo offline, cinco outcomes, retry, resposta
fora de ordem, preservação do último resultado, teclado, foco, navegação,
reduced motion, alvos de 44 px e reflow nos viewports da demonstração.
