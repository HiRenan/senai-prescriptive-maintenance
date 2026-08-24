# Product

## Register

product

## Users

- Banca avaliadora do desafio ISI/SENAI: assiste a uma demonstração de 15
  minutos projetada em sala de reunião, com o painel em `?mode=offline`, e
  depois audita o repositório público por conta própria.
- Avaliadores técnicos e recrutadores que abrem o repositório e executam o
  painel localmente em laptops, à luz do dia, sem contexto prévio.
- Perfil secundário de referência: engenharia de manutenção que consulta um
  laudo prescritivo pontual e decide o próximo passo por conta própria.

O trabalho do usuário em qualquer tela: submeter uma leitura industrial (18
métricas), ler o veredito com clareza imediata e entender exatamente o que a
plataforma afirma, o que ela se recusa a afirmar e qual é o próximo passo.

## Product Purpose

Plataforma demonstrável de manutenção prescritiva para ativos industriais.
Transforma uma leitura de vibração/temperatura/rotação em um laudo auditável
com um de cinco desfechos fechados; quando há falha documentada, emite uma
prescrição governada por evidência documental aprovada, com citações. A
plataforma nunca autoriza manutenção e nunca inventa um desfecho: abstenção,
degradação e recusa são estados de primeira classe. Sucesso é a banca (e
qualquer auditor) confiar no que vê porque cada afirmação é rastreável e cada
limite é explícito.

## Brand Personality

Sóbria, precisa, confiável. O painel é um instrumento profissional: transmite
competência de engenharia sem espetáculo. A voz é direta em pt-BR, honesta
sobre limites (dados sintéticos, heurísticas não calibradas, decisão humana),
e nunca vende mais do que o contrato entrega.

## Anti-references

- Painel "sala de controle" escuro com néon e densidade artificial.
- Dashboard genérico de produto de IA: hero-metrics, gradientes em texto,
  glassmorphism, grades de cards idênticos.
- Qualquer visual que faça um estado sem prescrição parecer conteúdo válido.

## Design Principles

1. O veredito é a resposta: nada na tela compete com ele em hierarquia.
2. Familiaridade conquistada: padrões de produto reconhecíveis (referências
   Linear e Stripe), sem reinventar affordances padrão.
3. Honestidade estrutural: estados de ausência (prescrição retida, abstenção,
   vazio, erro) têm forma própria e nunca imitam conteúdo.
4. Feedback sempre visível: toda ação produz confirmação perceptível para
   usuários videntes e para leitores de tela, com o mesmo texto.
5. Andaimes de demonstração não são navegação: metadados de contrato e modos
   ficam acessíveis, mas fora do caminho principal.

## Accessibility & Inclusion

Manter a barra atual, verificada por testes de navegador: alvos mínimos de
44px, reflow sem rolagem horizontal até 320px de largura (equivalente a 400%
de zoom), navegação completa por teclado com gestão de foco, regiões de status
`aria-live`, cor nunca como único sinal (glifos dedicados por estado) e
respeito integral a `prefers-reduced-motion`. Interface em pt-BR.
