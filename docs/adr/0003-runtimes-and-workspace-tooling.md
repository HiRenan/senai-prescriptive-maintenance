# ADR 0003 — Runtimes e ferramentas do workspace

- Data: 2026-08-22
- Status: Aceito

## Contexto

A fundação precisa executar de forma reproduzível no Windows e no Ubuntu,
manter um pacote Python instalável e reservar uma fronteira Node para a futura
aplicação web. Comandos dispersos e versões implícitas dificultariam a
reprodução e a apresentação das evidências.

O repositório já fixa Python, Node.js, pnpm, locks únicos e uma interface Poe.
Python 3.13 é uma escolha deliberada que pode revelar incompatibilidades em
bibliotecas ainda não atualizadas, mas receio abstrato não demonstra que o
runtime inviabiliza o projeto.

## Decisão

- Fixar Python em `>=3.13,<3.14` e registrar `3.13` em `.python-version`.
- Fixar Node.js em `>=22,<23`, registrar `22` em `.node-version` e usar pnpm
  `10.15.1` por Corepack.
- Usar uv para o workspace Python, com um único `uv.lock` na raiz.
- Usar pnpm para o workspace Node, com um único `pnpm-lock.yaml` na raiz.
- Usar Poe the Poet como interface canônica para setup, qualidade, testes,
  hooks, smoke e controle da infraestrutura local.
- Executar tarefas com locks congelados e manter comandos equivalentes entre
  os sistemas suportados.

Uma incompatibilidade comprovada com Python 3.13 exige, antes de qualquer
downgrade, um registro contendo:

1. biblioteca e versão afetadas;
2. reprodução mínima e evidência do erro;
3. impacto no requisito que precisa ser entregue;
4. versões ou alternativas técnicas avaliadas;
5. justificativa de por que atualização, substituição, correção ou isolamento
   não resolvem o problema.

Somente essa evidência pode fundamentar uma nova decisão de runtime. Preferência
pessoal, receio de compatibilidade ou ausência de teste não constituem
incompatibilidade.

## Alternativas consideradas

### Adotar preventivamente uma versão anterior do Python

Poderia ampliar a compatibilidade com bibliotecas antigas, mas abandonaria a
versão escolhida sem um bloqueio reproduzível e criaria retrabalho de
configuração e locks.

### Usar comandos diretos sem Poe

Reduziria uma camada de automação, porém duplicaria sequências entre
documentação e ambientes, além de tornar mais fácil variar flags e ordem dos
checks.

### Manter locks por pacote

Permitiria ciclos mais independentes, mas aumentaria o risco de versões
divergentes em um monorepo pequeno e tornaria o bootstrap menos previsível.

### Usar npm ou instalação global de pnpm

Removeria o Corepack, mas deixaria a versão efetiva do gerenciador mais
dependente da máquina e não aproveitaria o lock e o workspace já definidos.

## Consequências

- O ambiente esperado e a interface de comandos são explícitos e auditáveis.
- Locks únicos reduzem divergência entre pacotes e entre Windows e Ubuntu.
- `uv run --frozen poe ...` falha em vez de atualizar dependências
  silenciosamente durante uma validação.
- A fronteira Node existe mesmo sem UI, adicionando uma verificação de runtime
  à fundação.
- Bibliotecas incompatíveis com Python 3.13 podem exigir substituição ou uma
  nova decisão documentada; isso é um risco aceito e mensurável.
- Poe se torna uma dependência de desenvolvimento, compensada por uma interface
  única e comandos descobertos no `pyproject.toml`.

## Gatilhos de revisão

Reavaliar quando uma incompatibilidade com Python 3.13 satisfizer o registro de
evidências acima, quando uma versão de runtime sair da janela de suporte
adequada ao projeto, quando uma vulnerabilidade exigir atualização ou quando
uma necessidade implementada não puder ser atendida pelas ferramentas atuais.

Mudanças de versão devem atualizar arquivos de pin, metadados, locks,
automação, documentação e validações no mesmo pull request.
