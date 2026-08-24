# Prova dinâmica de chunk e recuperação RAG — SEN-77

## Objetivo e escopo

Esta validação demonstra, somente com dados sintéticos gerados em memória, que
um JSON de extração novo atravessa as implementações de chunking, indexação
local, ciclo documental, recuperação aprovada, filtro governado, guardrails e
projeção da análise. A citação resultante é ligada ao chunk efetivamente
selecionado pelo ranking, sem carregar uma resposta pronta de fixture.

O cenário não lê PDFs, `banner.csv`, derivados locais ou as cinco fixtures do
golden set. Também não usa rede, PostgreSQL, credenciais ou provider pago.

## Roteiro executável

O teste direcionado é executado por:

```powershell
uv run --frozen pytest apps/api/tests/test_dynamic_rag_e2e.py -q --no-cov
```

O arquivo está sob o `testpaths` canônico do pytest. Por isso, entra
automaticamente em `poe test` e em `poe check`, sem criar um gate paralelo nem
adicionar dependências externas.

O fluxo exercitado é:

```text
evento JSON sintético novo
  -> AnalysisRequest validado
  -> diagnóstico sintético compatível
  -> mapping documental versionado
  -> recuperação governada
  -> guardrails e geração offline
  -> resultado citado e metadado persistido em memória

JSON de extração sintética novo
  -> chunk_extracted_document
  -> LocalHashEmbeddingProvider
  -> index_extracted_document
  -> InMemoryChunkRepository
  -> DocumentGovernanceService
  -> versão approved atual
  -> ranking sobre os vetores armazenados
```

`LocalHashEmbeddingProvider` e `FakeGenerationProvider` são adapters sintéticos
determinísticos já destinados a testes. O score é calculado a partir do vetor
armazenado de cada candidato; não existe tabela de chunk para resposta nem
retriever fake entre o índice e a orquestração.

## Controles verificáveis

- o payload de extração passa por serialização JSON e é segmentado novamente
  para conferir a mesma linhagem;
- a segunda indexação produz os mesmos registros e não aumenta o repositório;
- o replay do registro documental preserva snapshot, revisão e relógio;
- a versão 1 aprovada torna-se `superseded` quando a versão 2 é aprovada;
- o conjunto cobre `received`, `processing`, `pending_approval`, `approved`,
  `rejected`, `failed` e `superseded`, mas somente a versão aprovada atual chega
  ao scorer;
- duas análises do mesmo evento produzem a mesma resposta, desconsiderando
  apenas o identificador novo da análise;
- documento, versão, chunk, seção, página, hash e intervalo de caracteres são
  conferidos contra o registro selecionado; a citação pública e a referência
  persistida apontam para essa mesma identidade;
- quatro mutações coerentes a jusante simulam atalhos no chunker, indexador,
  lifecycle e filtro de aprovação. Todas são recusadas pela prova de linhagem;
- marcadores de conteúdo bruto não aparecem no resultado, no `repr` da prova
  nem nos logs capturados.

## Evidência executada

Em 2026-08-23, o comando direcionado concluiu com `2 passed`. As verificações
locais de Ruff e Pyright sobre o teste também concluíram sem erro. A evidência
pública registra somente contagens, estados e identidades sintéticas; texto
documental e hashes de conteúdo não são reproduzidos aqui.

Na mesma revisão, `uv run --frozen poe check` concluiu com 1.156 testes
aprovados, 43 skips condicionais e cobertura total de 80,69%. O gate confirmou
formatação, lint e tipagem antes de executar a suíte funcional agregada.

## Limites

A prova valida a composição local governada e o contrato de citação. Ela não
afirma upload público de PDF, embedding semântico de produção, qualidade
semântica da prescrição, retreino online, SageMaker ou desempenho pgvector.
