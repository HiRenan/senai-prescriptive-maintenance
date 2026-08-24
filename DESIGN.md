# Design

Sistema visual do painel (`apps/web`). Fonte da verdade dos tokens:
`apps/web/src/styles/tokens.css`. Direção: produto SaaS elegante e sóbrio
(referências Linear e Stripe), temas claro e escuro, cor contida com um acento
comprometido, fontes de sistema afinadas.

## Theme

- Contrato: `<html data-theme="light" | "dark">`, sempre resolvido antes do
  primeiro paint por `apps/web/public/theme-init.js` (script externo; a CSP
  proíbe scripts inline). Ordem: `localStorage["pm.theme"]` → preferência do
  sistema (`prefers-color-scheme`).
- Um único bloco escuro em CSS: `:root[data-theme="dark"] { … }`.
  `color-scheme` acompanha o tema. O alternador persiste a escolha; "Usar tema
  do sistema" remove a chave e volta a seguir o sistema.

## Color

OKLCH em todos os tokens. Nunca `#000`/`#fff`; neutros tingidos ao matiz do
acento (chroma 0.004–0.012). Estratégia contida: acento reservado a ações
primárias, seleção e foco.

- Acento índigo, matiz 262: claro `oklch(50% 0.19 262)` (hover 45%, active
  41%), escuro `oklch(60% 0.18 262)` (hover 65%, active 56%);
  `--accent-subtle` para fundos selecionados/callouts; `--on-accent`
  `oklch(98.5% 0.008 262)`.
- Superfícies claro: page `97.5%`, raised `99.3%`, sunken `95.2%`, overlay
  `99.5%`; escuro: `16%` / `19.5%` / `13.5%` / `23.5%` (chroma 0.01–0.014,
  matiz 262).
- Texto claro: `21%` / `41%` / `50%`; escuro: `93%` / `72%` / `60%`.
- Bordas claro: subtle `91%`, strong `83%`; escuro: `28%` / `37%`.
- Tons semânticos de desfecho, cada um com `--tone-fg/--tone-bg/--tone-border`
  por tema: `settled` matiz 162 · `prescribed` 292 · `withheld` 82 · `outside`
  45 · `degraded` 250 (dessaturado) · `failed` 25. Estados de documento mapeiam
  para esses tons (+ `info` 235 para `processing`; neutro para `superseded`).
- Vocabulário genérico: success 162, warning 82, error 25, info 235.
- Todo par texto/fundo mantém contraste ≥ 4.5:1 nos dois temas.

## Typography

- Famílias de sistema: sans `system-ui, -apple-system, "Segoe UI", Roboto,
  "Helvetica Neue", Arial, sans-serif`; mono `ui-monospace, "Cascadia Mono",
  "SFMono-Regular", "Segoe UI Mono", Consolas, "Liberation Mono", monospace`.
- Escala rem (razão ≈ 1.2): 0.6875 / 0.75 / 0.875 / 1 / 1.1875 / 1.4375 /
  1.75; `--text-2xl: clamp(1.625rem, 1.25rem + 1.6vw, 2.25rem)` é exclusivo do
  título do veredito.
- Pesos: 400 corpo · 500 rótulos de UI · 600 títulos · 650 veredito.
- Medidas sempre em mono com `tabular-nums`. Uppercase rastreado (`.overline`)
  tem orçamento fixo: kicker do veredito, rótulo "Próximo passo" e cabeçalhos
  de tabela — nada além.

## Spacing, radius, elevation

- Espaço em grade de 4px: 0.25 / 0.5 / 0.75 / 1 / 1.5 / 2 / 3 / 4 rem.
- Raios: 4 (badges) · 6 (controles) · 10 (cards) · 14 (laudo, popover, toast)
  · 999 (pills).
- Elevação sutil com sombra de tinta tingida (nunca preto puro): `--shadow-1`
  cards em repouso, `--shadow-2` interação/sticky, `--shadow-3`
  popover/toast/menu. No escuro, elevação vem do degrau de superfície + borda;
  sombra real apenas no nível 3. Card = fundo raised + borda subtle + sombra.
- Alvo mínimo de toque: `--hit: 2.75rem` (44px) em todo controle interativo.

## Motion

- Durações 120 / 180 / 240 ms; `--ease-out: cubic-bezier(0.25, 1, 0.5, 1)`.
- Movimento só comunica estado (entrada de laudo, toast, disclosure). Sem
  coreografia de carregamento. `prefers-reduced-motion` zera durações
  globalmente.

## Components

- Primitivos: Button (primary/quiet/ghost/danger, estado busy com spinner),
  Field/NumericInput (unidade embutida, erro visível vinculado por
  `aria-describedby`), Card, Badge, Tile, Banner por tom, Toast (pilha visual
  + região `role="status"` oculta com o mesmo texto), ConfirmPopover (Escape
  cancela e devolve o foco), Disclosure, Skeleton, EmptyState (glifo + guia +
  ação).
- Glifos semânticos: 13 marcas SVG próprias (6 desfechos + 7 estados de
  documento), traço 1.75, sempre pareadas com texto — cor nunca é o único
  sinal. Ícones de interface: lucide, um único conjunto.
- Padrões banidos: barras laterais coloridas como acento, texto em gradiente,
  glassmorphism, hero-metric, grades de cards idênticos, modal como primeiro
  recurso. Prescrição não emitida permanece como bloco hachurado com borda
  tracejada sobre superfície rebaixada — jamais parece conteúdo.
