# ADR 0001 — Monorepo e monólito modular

- Data: 2026-08-22
- Status: Aceito

## Contexto

O projeto precisa reunir backend, fronteira web, infraestrutura local,
documentação, dados sintéticos, experimentos e automação com uma configuração
reproduzível. A equipe atual é pequena e a fundação implementada contém um único
backend FastAPI, sem domínios independentes, cargas operacionais distintas ou
necessidade comprovada de implantações separadas.

Distribuir essas áreas cedo demais aumentaria o número de repositórios, versões,
pipelines e contratos sem resolver um problema observado. Ao mesmo tempo, um
backend sem fronteiras internas facilitaria acoplamento conforme as regras de
manutenção fossem implementadas.

## Decisão

Manter um monorepo com as fronteiras `apps/api`, `apps/web`, `infra`, `docs`,
`data`, `experiments` e `scripts`.

O backend de produção será um monólito modular: uma aplicação implantável no
namespace Python `prescriptive_maintenance`, com módulos internos organizados
por responsabilidade quando as tarefas de domínio existirem. Fronteira modular
não autoriza antecipar módulos vazios, contratos ou integrações futuras.

`apps/web` permanece como limite do workspace Node sem implementação visual até
que uma tarefa defina UI. Infraestrutura e automação local apoiam a aplicação,
mas não se tornam serviços de domínio separados.

## Alternativas consideradas

### Múltiplos repositórios e microserviços

Separariam implantação e ownership por serviço, mas hoje não existem limites de
domínio, equipes ou requisitos de escala que paguem o custo de contratos de
rede, observabilidade distribuída, versionamento e múltiplos pipelines.

### Monólito sem módulos explícitos

Seria inicialmente simples, porém favoreceria dependências cruzadas e tornaria
mais difícil justificar ou testar limites quando o domínio crescer.

### Repositório exclusivo do backend

Reduziria a árvore inicial, mas dispersaria documentação, dados permitidos,
infraestrutura reprodutível e a fronteira web que precisam evoluir sob as mesmas
decisões e locks.

## Consequências

- Há uma única fonte de verdade para código, decisões, dados públicos e
  automação.
- Mudanças transversais podem ser revisadas e validadas em um único pull
  request.
- O backend mantém implantação simples e pode ganhar limites internos sem custo
  operacional distribuído.
- O repositório e a suíte de checks podem crescer; tarefas devem permanecer
  cirúrgicas para conter esse efeito.
- Módulos continuam compartilhando processo e ciclo de release. Isolamento de
  falha ou escala independente não é oferecido por esta decisão.

## Gatilhos de revisão

Reavaliar quando houver evidência de pelo menos uma destas condições:

- módulos com necessidades incompatíveis de escala, disponibilidade ou
  implantação;
- equipes independentes bloqueadas por um único ciclo de release;
- requisitos de segurança que imponham isolamento de processo ou dados;
- crescimento de checks ou dependências que torne o monorepo materialmente
  improdutivo;
- fronteiras de domínio estáveis e observadas que justifiquem extração.

Preferência abstrata por microserviços não é evidência suficiente.
