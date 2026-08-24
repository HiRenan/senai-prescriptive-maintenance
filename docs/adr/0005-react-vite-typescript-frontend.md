# ADR 0005 — Frontend em React, Vite e TypeScript

- Data: 2026-08-24
- Status: Aceito
- Relação: complementa o ADR 0003 (runtimes e workspace) e o ADR 0004 (gates de
  CI); não os substitui.

## Contexto

O painel vanilla sem dependências cumpriu a fase de fundação: contrato gerado,
proxy same-origin fechado, modo offline sem rede, PKCE restrito à origem
publicada, acessibilidade verificada em navegador e entrega AWS fail-closed.
A renovação visual aprovada pelo responsável exige componentização real,
tipos estáticos de ponta a ponta, temas claro e escuro e testes de componente —
capacidades que a construção manual de DOM tornava progressivamente cara e
frágil de evoluir.

Invariantes que não podem regredir com a mudança: CSP estrita sem código
inline, proxy same-origin derivado do contrato gerado, modo offline com zero
chamadas de rede, login PKCE apenas na origem publicada, entrega AWS
fail-closed, interface em pt-BR com código em inglês e a suíte de navegador
como contrato de comportamento (alvos de 44px, reflow até 320px, foco,
reduced-motion, preservação do último laudo).

## Decisão

- Adotar React 19 com Vite e TypeScript estrito (`.ts`/`.tsx`) em `apps/web`,
  com dois projetos de verificação (`tsconfig.app.json` sem ambiente Node;
  `tsconfig.test.json` com `@types/node` para testes e configs).
- Manter `server.mjs` como servidor de produção, agora servindo o `dist/` do
  Vite com nomes hasheados; proxy, limites, cabeçalhos e CSP permanecem
  intactos. O servidor de desenvolvimento do Vite (porta 5173) usa proxy
  `/api` para a API local; a porta 3000 continua exclusiva do caminho de
  produção.
- Não adotar roteador (navegação por hash preservada) nem biblioteca de
  estado; o estado permanece em hooks e contexts.
- Migrar os testes unitários de `node --test` para Vitest com Testing Library
  e happy-dom; Playwright permanece e passa a exercitar o caminho real de
  produção (build seguido de `server.mjs` com CSP).
- Manter os contratos gerados (`src/generated/*`) intocados, importados do
  TypeScript com a extensão `.js` explícita.
- Publicar na AWS o `dist/` validado por uma gramática fechada (forma exata do
  diretório, sufixos, limites de tamanho, índice sem código inline), em lugar
  da allowlist de arquivos-fonte.
- Reconstruir a imagem Docker em múltiplos estágios; o runtime não carrega
  `node_modules` (React entra no bundle e `server.mjs` usa apenas builtins).
- Acrescentar a tarefa `web-build` à interface Poe e incluí-la na sequência de
  `check` antes da regressão de entrega.

## Alternativas consideradas

### Permanecer vanilla e apenas redesenhar o CSS

Preservaria o custo zero de dependências, mas manteria a construção imperativa
de DOM como gargalo de evolução visual e não ofereceria tipos reais nem testes
de componente.

### Preact ou React via import maps sem build

Reduziriam a cadeia de build, porém com ecossistema menor ou sem TSX,
minificação e dev server maduro; o ganho não compensaria a divergência das
convenções do ecossistema.

### Next.js ou outra solução com SSR

Substituiria o `server.mjs` auditado por um servidor com dependências de
produção, ampliando a superfície de ataque e invalidando os testes de limites
do proxy sem necessidade demonstrada por requisito.

## Consequências

- Positivas: componentização com estados explícitos, tipos estritos, temas
  claro e escuro viáveis, testes de componente, base sólida para o redesign.
- Custos aceitos: cadeia de dependências npm com superfície de supply chain,
  build obrigatório antes de servir, reescrita da entrega AWS e de suas
  regressões, estágio de build na imagem.
- As invariantes listadas no contexto permanecem cobertas pelos mesmos gates:
  `web-contract-check`, `web-typecheck`, `web-test`, `web-build`, regressão de
  entrega e suíte de navegador.

## Gatilhos de revisão

Reavaliar se surgir necessidade implementada de SSR ou rotas reais, se o
orçamento de bundle comprometer a demonstração, se uma vulnerabilidade na
cadeia de dependências exigir mudança estrutural ou se a manutenção do
`server.mjs` próprio deixar de se justificar frente ao custo.
