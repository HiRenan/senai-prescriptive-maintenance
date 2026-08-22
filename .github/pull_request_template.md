## Contexto

<!-- Explique o problema, a solução adotada e por que este PR é necessário. -->

## Linear

- Tarefa: `SEN-XX`

## Tipo e destino

Marque exatamente o fluxo aplicável:

- [ ] Tarefa: branch curta criada de `origin/develop` → `develop`.
- [ ] Release: branch local `release/sen-<id>-<slug>` criada de
      `origin/develop`, contendo somente a reconciliação com `origin/main` →
      `main`.
- [ ] Hotfix: branch local `hotfix/sen-<id>-<slug>` criada de `main` → `main`.
- [ ] Sincronização de hotfix: branch criada de `origin/develop` → `develop`,
      com referência ao PR e ao commit squash em `main`.

Base deste PR: `<!-- develop ou main -->`

## Alterações

- <!-- Descreva cada alteração relevante. -->

## Decisões, alternativas e consequências

<!-- Resuma decisões relevantes ou aponte para o ADR correspondente. -->

## Validação

Liste somente comandos realmente executados e seus resultados:

```text
comando — resultado
```

## Segurança

- [ ] Revisei o diff e os arquivos não rastreados em busca de segredos.
- [ ] Não adicionei credenciais reais; exemplos usam apenas valores fictícios
      para desenvolvimento local.
- [ ] Avaliei se a mudança exige atualização de `SECURITY.md`.
- [ ] Não publiquei detalhes de vulnerabilidade que exijam reporte privado.

## Dados e materiais

- [ ] Não li, copiei, movi, removi ou adicionei ao Git materiais originais fora do
      escopo autorizado.
- [ ] Dados públicos adicionados são inteiramente sintéticos e não reproduzem
      os materiais originais.
- [ ] Manifesto, `.gitignore` e fronteiras de dados permanecem protegidos.

## Documentação

- [ ] A documentação foi atualizada quando comportamento, comandos ou decisões
      mudaram.
- [ ] O texto diferencia estado implementado de visão futura.
- [ ] Links relativos e referências apontam para recursos existentes.

## Checklist de escopo e revisão

- [ ] O título do PR está em inglês, segue Conventional Commits, tem descrição
      ASCII iniciada por letra minúscula ou número, até 120 caracteres e sem
      ponto final.
- [ ] A descrição está em português e referencia a tarefa Linear.
- [ ] A branch tem nome em inglês no padrão do projeto.
- [ ] O diff contém somente alterações justificadas pela tarefa.
- [ ] Locks congelados e arquivos rastreados permaneceram estáveis durante as
      verificações.
- [ ] Em uma release, o `HEAD` tem exatamente dois pais — `origin/develop`
      primeiro e `origin/main` segundo —, contém a `main` vigente e tem árvore
      idêntica à `develop` vigente; o merge virtual é limpo e neutro, ou o
      fallback legado de árvore alcançável foi comprovado.
- [ ] `git diff --check` e os checks obrigatórios passaram.
- [ ] Conversas de revisão foram resolvidas.
- [ ] Tarefas e hotfixes serão integrados por squash; releases serão integradas
      por merge commit. Não haverá push direto em `develop` ou `main`.
- [ ] Riscos, limitações e trabalho restante estão descritos abaixo.

## Riscos, limitações e trabalho restante

- <!-- Registre riscos, limitações ou informe que não há itens conhecidos. -->
