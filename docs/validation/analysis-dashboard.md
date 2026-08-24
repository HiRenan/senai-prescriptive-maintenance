# Painel de análise: decisões e validação (SEN-47)

Este documento registra o que foi decidido e o que foi efetivamente verificado no
fluxo principal do painel de demonstração. Ele descreve apenas o que existe.

## Escopo entregue

- Console com as 18 features do contrato, agrupadas por métrica, com os eixos X e
  Z lado a lado e a contagem de vizinhos.
- Importação por exemplo sintético do contrato, por JSON colado e por arquivo.
- Chamada real a `POST /analysis`, com estados de carregamento, sucesso e erro.
- Laudo com desfecho, próximo passo, prescrição, diagnóstico, suporte,
  abstenção, citações, vizinhos opacos e avisos.
- Comparação visual descritiva das features enviadas.
- Testes essenciais do fluxo.

Fora de escopo: gestão documental (SEN-63) e o acabamento final de
acessibilidade, responsividade e estados (SEN-64).

## Decisões

### O contrato é a fonte, não uma cópia

`scripts/generate_web_contract.py` lê `apps/api/openapi/v1.json` e emite
`apps/web/src/generated/analysis-contract.js` com o `.d.ts` irmão. Nenhum tipo de
request ou response é escrito à mão no frontend.

O gerador não copia apenas nomes: ele deriva, por desfecho, se há diagnóstico, se
há abstenção e qual o motivo, qual o nível de suporte, se aquele desfecho emite
prescrição e quantas citações aceita. A regra de disponibilidade da prescrição na
interface consulta essa tabela derivada em vez de condições escritas à mão.

As ressalvas exibidas sobre o suporte e sobre a distância dos vizinhos são o
próprio texto de `description` do contrato, não uma paráfrase.

`uv run --frozen poe web-contract-check` falha se o material rastreado divergir
do snapshot, e a tarefa participa de `poe check`.

### Um 200 só vira laudo depois de decodificado

O gerador também emite a tabela de esquemas das cinco variantes de resposta:
propriedades declaradas, membros obrigatórios, constantes, enums, padrões de
texto, limites de texto, limites numéricos e limites de lista. Um padrão só é
publicado se estiver ancorado e usar o subconjunto que Python e o navegador leem
igual, o que exclui `\d`, `\w` e `\s`, Unicode de um lado e ASCII do outro;
qualquer outro faz a geração falhar em vez de ser reinterpretado. O cliente decodifica o corpo contra
a variante do desfecho recebido em vez de reimplementar as regras: membro
ausente, membro que o contrato não declara em qualquer nível, constante trocada,
identificador fora do padrão publicado, número fora de faixa, inteiro
fracionário, lista fora dos limites ou elemento inválido derrubam a resposta
para `malformed`, apresentado como falha.

Somente o status de sucesso publicado no contrato vira resultado. Um `201` ou um
`204` é reportado como resposta inesperada, nunca como laudo.

Isso fecha buracos concretos: um nível de suporte fora do enum produziria um
rótulo `undefined` no laudo; um desfecho sem diagnóstico omitiria a seção sem
avisar ninguém; e uma propriedade extra passaria despercebida por um contrato
que declara `additionalProperties: false`.

O prazo do cliente cobre a leitura do corpo, não apenas os cabeçalhos: uma
resposta que começa e nunca termina vira tempo limite.

### Prescrição indisponível nunca parece válida

A apresentação classifica a prescrição em quatro estados e apenas `emitida`
renderiza conteúdo. `normal` recebe "não se aplica"; as três abstenções recebem
"retida"; um corpo que contradiga o contrato recebe "indisponível" com nota de
integridade.

A verificação é defensiva nos dois sentidos: uma prescrição presente num desfecho
que não prescreve é recusada, e a ausência de prescrição em `documented_fault`
também. Prioridade fora do enum e lista de ações vazia igualmente derrubam a
prescrição para indisponível.

Essa checagem permanece mesmo com o cliente endurecido. São duas camadas
independentes: o cliente recusa o corpo contraditório antes do laudo, e a
apresentação continua incapaz de exibir uma prescrição que o contrato não
autoriza, qualquer que seja o caminho de entrada.

Visualmente, todo estado que não seja `emitida` usa um bloco hachurado com borda
tracejada, distinto de qualquer bloco de conteúdo. O texto é redundante com a
forma e com a cor.

`degraded` é o caso crítico: ele traz citações e nenhuma prescrição. O laudo
mostra as citações e mantém a prescrição retida.

### Abstenção e erro terminam em instrução

Cada um dos cinco desfechos e cada um dos seis modos de falha tem um próximo
passo próprio, verificado por teste. A abstenção mostra a mensagem da própria API
somada ao próximo passo do painel.

### Mesma origem em vez de CORS

O navegador chama `POST /api/analysis` no processo web, que encaminha para
`POST /analysis` da API. Nenhuma mudança foi feita no backend. O proxy encaminha
somente essa rota e distingue "a API não foi alcançada" de "a API recusou".

Os dois sentidos são limitados e temporizados. Na entrada há teto de 64 KiB,
prazo de leitura e uma única liquidação: o corpo excedente é descartado, a
recusa é respondida antes de qualquer encerramento e o restante é drenado sob
teto em vez de derrubar a conexão no meio da resposta. Na saída há teto de
256 KiB, prazo que cobre a leitura do corpo e não apenas os cabeçalhos, recusa
de redirecionamento, verificação do tipo de mídia por token exato e repasse
apenas dos status que o contrato publica. Qualquer outra coisa vira `502` ou
`504` com envelope próprio; o tipo de mídia devolvido ao navegador é sempre o
do contrato.

### Sem framework e sem build

> Decisão superada pela
> [ADR 0005](../adr/0005-react-vite-typescript-frontend.md): o painel passou a
> ser React com TypeScript estrito, empacotado pelo Vite. O registro abaixo
> descreve o que valia na SEN-47.

O painel é ESM servido direto ao navegador. A única dependência introduzida é
`typescript` como devDependency de raiz, usada para verificar JavaScript anotado
com JSDoc contra os tipos gerados. Não há bundler, transpilação nem artefato de
build: os arquivos verificados são os mesmos que rodam no navegador e os mesmos
que entram na imagem.

### Comparação descritiva

Os oito pares de eixo são escalados pela própria magnitude máxima, porque as 18
features não compartilham unidade. Valores negativos são medidos pela magnitude e
sinalizados. Nada é comparado com o modelo, com os vizinhos ou com limiares, e o
painel declara explicitamente que a comparação não indica causa, gravidade nem
relação com o desfecho.

## Validação executada

Ambiente: Windows 11, Python 3.13.5, Node.js 22.18.0, pnpm 10.15.1,
Docker 28.4.0.

| Verificação | Comando | Resultado |
| --- | --- | --- |
| Contrato web em dia | `poe web-contract-check` | sem divergência |
| Tipos do painel | `poe web-typecheck` | sem erro |
| Testes do fluxo | `poe web-test` | 92 testes, 92 aprovados |
| Verificação agregada | `poe check` | aprovada, 1178 testes Python |
| Matriz de falhas e auditoria pública | `poe failure-matrix` | 30 testes aprovados, auditoria `passed` |
| Demonstração sintética ponta a ponta | `poe golden-e2e` | aprovada |
| Guardrails do repositório | `poe hooks` | aprovada |
| Smoke offline | `poe smoke` | aprovada |
| Contexto e builder Docker | `poe applications-audit` | contexto web com 19 arquivos |
| Imagens da API e do painel | `poe applications-build` | duas imagens construídas |
| Stack containerizada | `docker compose up --wait postgres api web` | três contêineres saudáveis |

### Cobertura dos testes

Os testes tomam todos os exemplos do próprio snapshot OpenAPI, de requisição e de
resposta. Nenhum material original participa da suíte e nenhuma fixture foi
escrita à mão.

- Contrato: ordem e limites das 18 features, limites de `top_k`, os cinco
  desfechos derivados da união discriminada, apenas `documented_fault`
  prescrevendo, o enum de prioridades, as ressalvas do contrato e a igualdade
  entre os exemplos de importação e os do snapshot.
- Features: rótulos e unidades resolvidos, agrupamento sem perda, vírgula e ponto
  decimais, recusa de entradas que `Number()` aceitaria por engano, limites e
  mensagens acionáveis, ida e volta entre requisição e console.
- Importação: os cinco exemplos sintéticos, documento vazio, JSON inválido, JSON
  que não é objeto, features ausentes, chaves fora do contrato, números em texto,
  valores fora de faixa e `top_k` fora dos limites.
- Apresentação: os cinco desfechos com tom e título distintos, a disponibilidade
  da prescrição em cada um, `degraded` com citações e prescrição retida,
  prescrição forjada em `normal`, `documented_fault` sem prescrição, prioridade
  fora do enum, ações vazias, desfecho fora do contrato, abstenção completa,
  suporte com ressalva e próximo passo em todos os seis modos de falha.
- Cliente: sucesso, forma da requisição, `422` com campos recusados, `503`,
  gateway `502`/`504`, status fora do contrato, falha de rede, tempo limite,
  corpo não JSON e corpo incompleto.
- Decodificação do `200`: os cinco exemplos do contrato continuam aceitos, e
  viram `malformed` o desfecho fora do contrato, o identificador ausente ou de
  outro tipo, o nível de suporte divergente do desfecho, o escore fora de 0 a 1,
  o diagnóstico ausente ou inventado, o texto acima do comprimento publicado, a
  abstenção com motivo de outro desfecho, a prescrição ausente em
  `documented_fault` ou presente onde o contrato não prescreve, a prioridade
  fora do enum, a lista de ações vazia ou acima do máximo, o vizinho com posto,
  distância ou tipo inválido, a lista de vizinhos fora dos limites, a citação
  com página inválida, a lista de citações fora dos limites do desfecho, o aviso
  malformado, a lista de avisos vazia onde há mínimo e propriedade extra em
  qualquer um dos sete níveis do corpo. Também é exercitado o identificador com
  tamanho válido e formato inválido em cada família publicada: análise, modelo,
  vizinho, código de falha, documento, versão e trecho. Também são exercitados os status `2xx`
  fora do contrato e um corpo que nunca termina.
- Importação por tamanho: o limite é aplicado pela contagem declarada, antes de
  ler o arquivo, e de novo sobre o texto importado.
- Servidor: liveness inalterada, documento e ativos com tipo e cabeçalhos de
  segurança, recusa de travessia de caminho e de `.d.ts`, encaminhamento para
  `POST /analysis`, preservação do status de erro, método não permitido, corpo
  acima do limite, cliente lento respondido com `408` sem chamar a API, resposta
  da API acima do teto, tipo de mídia fora do contrato e o vizinho
  `application/jsonmalicious`, status fora do contrato, corpo da API que nunca
  termina, redirecionamento recusado, resposta recusada cancelada sem vazar o
  corpo e faixa segura do prazo configurável.

### Verificação no navegador

A stack containerizada foi levantada e o painel foi carregado no Chrome.

- O documento monta os 18 campos a partir do contrato e o estado vazio aparece
  sem erro de console.
- Os cinco desfechos foram renderizados contra a API real. Cada um apresentou tom
  e título distintos, próximo passo próprio, três vizinhos e os oito pares de
  comparação. `documented_fault` apresentou prescrição emitida com prioridade
  "Programada", duas ações e três citações. `degraded` apresentou três citações
  com a prescrição retida. `out_of_distribution` não apresentou diagnóstico e
  registrou suporte insuficiente.
- Em 390 px de largura o layout empilha em uma coluna e não há rolagem
  horizontal: `scrollWidth` e `clientWidth` coincidem.

Um defeito encontrado nessa verificação foi corrigido: o bloco de problemas de
importação exibia sua borda mesmo oculto, porque `display: grid` vencia o
atributo `hidden`.

## Limitações e riscos residuais

- A composição HTTP padrão da API permanece inteiramente sintética. O painel
  chama a API real, mas o resultado observado na demonstração vem dos fakes do
  contrato; o desfecho é selecionado pelo sentinela de RPM.
- Não há teste de ponta a ponta automatizado em navegador. A verificação visual
  descrita acima foi manual e não roda em CI; a automação pertence a SEN-64.
- O painel não cobre `GET /analysis/{id}` nem o ciclo documental. Enquanto SEN-63
  não existir, a instrução de "registrar e aprovar documentação" descreve o
  processo, não uma tela.
- A comparação de features é descritiva por decisão. Ela não substitui análise de
  causa e não deve ser lida como explicação do desfecho.
- O carimbo de execução exibido no laudo vem do relógio do navegador e está
  rotulado como execução local. Ele não é metadado da API.
