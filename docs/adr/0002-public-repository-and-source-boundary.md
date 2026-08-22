# ADR 0002 — Repositório público e fronteira dos materiais

- Data: 2026-08-22
- Status: Aceito

## Contexto

A banca precisa inspecionar organização, código e justificativas técnicas. Por
isso, o repositório deve ser acessível para avaliação. Os oito materiais
originais fornecidos para o desafio, entretanto, não são ativos públicos do
projeto e não há autorização de redistribuição ou reutilização para eles.

Também não foi tomada uma decisão de licenciar o código e a documentação para
uso por terceiros. Tornar um repositório visível não equivale a conceder uma
licença.

## Decisão

Manter o repositório público para leitura, clone e avaliação, sem adicionar um
arquivo `LICENSE`. Todos os direitos permanecem reservados a Renan Mocelin, e a
visibilidade pública não cria licença implícita para copiar, modificar,
redistribuir ou reutilizar o conteúdo.

Os materiais originais permanecem locais, somente leitura, fora do Git e
cobertos por `.gitignore`. O repositório registra apenas os nomes, tamanhos e
hashes SHA-256 já presentes em `data/source-manifest.json`, suficientes para
conferência de integridade sem redistribuição.

Dados públicos de teste devem ser inteiramente sintéticos. Conteúdo original ou
derivado não pode aparecer em fixtures, testes, logs, documentação, issues,
pull requests, caches ou artefatos versionados.

## Alternativas consideradas

### Repositório privado

Reduziria a exposição por padrão, mas dificultaria o acesso direto da banca e a
avaliação pública que motivam o projeto.

### Adotar uma licença aberta

Esclareceria permissões de reutilização, porém concederia direitos que o autor
não decidiu oferecer e não resolveria a falta de direitos sobre os materiais
recebidos.

### Versionar os materiais ou amostras derivadas

Facilitaria reprodução local, mas criaria risco de redistribuição não
autorizada, exposição de conteúdo e aumento desnecessário do histórico. O
manifesto e fixtures sintéticas atendem à validação sem esse risco.

## Consequências

- A banca consegue inspecionar e clonar a fundação sem acesso especial.
- Terceiros não recebem permissão de reutilização apenas por acessarem o
  repositório.
- Um consumidor precisa fornecer localmente seus próprios materiais autorizados
  e conferir a integridade pelo manifesto.
- Testes públicos não podem depender do conteúdo original; fixtures sintéticas
  precisam representar apenas estruturas e comportamentos necessários.
- Ausência de licença pode limitar colaboração e adoção externa. Esse efeito é
  deliberado até uma decisão explícita do responsável.
- A exposição pública aumenta a importância de revisão de segredos, logs e
  arquivos não rastreados antes de cada push.

## Gatilhos de revisão

Reavaliar se o responsável decidir conceder uma licença específica, se a forma
de avaliação deixar de exigir visibilidade pública, se houver autorização
documentada para distribuir algum conjunto de dados ou se requisitos legais e
contratuais alterarem a fronteira dos materiais.

Uma revisão de licença deve distinguir direitos sobre o código, a documentação,
as dependências de terceiros e os materiais fornecidos; nenhuma licença futura
pode presumir direitos inexistentes sobre esses materiais.
