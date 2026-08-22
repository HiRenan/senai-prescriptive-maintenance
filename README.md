# Manutenção Prescritiva — Desafio SENAI

Este repositório registra o desenvolvimento de uma solução de manutenção
prescritiva para ativos industriais. O objetivo do projeto é evoluir, de forma
auditável, da organização e validação dos dados até recursos de análise,
recuperação de conhecimento técnico e recomendação de ações de manutenção.

> **Estado atual:** esta é a baseline pública inicial. Ainda não há aplicação,
> API, pipeline de dados, modelo de inteligência artificial ou infraestrutura
> executável implementados.

## Escopo desta baseline

A fundação atual estabelece somente:

- regras para impedir o versionamento dos materiais originais e de artefatos
  locais;
- documentação para preparar e validar os arquivos fornecidos fora do Git;
- um manifesto com tamanho e SHA-256 dos oito arquivos originais;
- fixtures pequenas e inteiramente sintéticas para futuros testes básicos;
- normalização de fim de linha para os formatos textuais já versionados.

Essa separação mantém o repositório leve e permite comprovar a integridade das
fontes sem redistribuir conteúdo recebido para o desafio.

## Materiais originais

Os PDFs e o arquivo `banner.csv` fornecidos para a prova não fazem parte do
repositório. Eles devem permanecer em armazenamento local e, quando necessários,
ser copiados para `data/raw/original/`, diretório ignorado pelo Git.

O arquivo [`data/source-manifest.json`](data/source-manifest.json) contém apenas
os nomes, os tamanhos em bytes e os hashes SHA-256 esperados. As instruções de
preparação e conferência estão em [`data/README.md`](data/README.md).

As fixtures em `data/fixtures/` foram escritas especificamente para este projeto,
com identificadores, datas, medições e relatos fictícios. Elas não reproduzem
registros do CSV nem trechos dos documentos fornecidos.

## Estrutura atual

```text
.
├── data/
│   ├── fixtures/
│   │   ├── banner.synthetic.csv
│   │   └── maintenance.synthetic.txt
│   ├── README.md
│   └── source-manifest.json
├── .gitattributes
├── .gitignore
└── README.md
```

Não há dependências ou comandos de execução nesta etapa. Para preparar os dados
locais, siga o guia do diretório `data/`.

## Próximos passos

Após a revisão desta baseline, a próxima etapa será definir a estrutura do
repositório e fixar os runtimes. As etapas posteriores poderão então implementar,
de maneira incremental, validação e processamento de dados, recursos de IA e
RAG, uma API com FastAPI e a infraestrutura necessária na AWS.

Esta branch é a exceção de bootstrap criada a partir de `main`. Depois da
aprovação da baseline e da criação de `develop`, as tarefas seguintes deverão
partir de `develop` em worktrees próprias e retornar por pull request.

## Acesso e direitos

O repositório é público para permitir a leitura, o clone e a avaliação pela
banca. Essa disponibilidade não concede autorização para copiar, modificar,
redistribuir ou reutilizar o conteúdo.

Não há arquivo `LICENSE`. Todos os direitos permanecem reservados a Renan
Mocelin.
