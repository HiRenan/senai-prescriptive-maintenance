# Preparação dos materiais locais

Os oito arquivos recebidos para o desafio são fontes locais e não devem ser
adicionados ao Git. Este diretório mantém o manifesto de integridade, fixtures
sintéticas e as exceções públicas estritas da baseline agregada e do inventário
categórico auditado descritas abaixo.

## Arquivos esperados

- `11 - prova prtica.pdf`
- `banner.csv`
- `Doc1.pdf`
- `Doc2.pdf`
- `Doc3.pdf`
- `Doc4.pdf`
- `Doc5.pdf`
- `Doc6.pdf`

## Posicionamento

Crie `data/raw/original/` dentro da sua cópia do repositório e copie os oito
arquivos para esse diretório. A origem deve ser preservada sem alterações, e o
destino já está coberto pelo `.gitignore`.

No PowerShell, execute a partir da raiz do repositório e ajuste apenas o caminho
de origem:

```powershell
$sourceDirectory = "C:\caminho\para\os\materiais"
$destinationDirectory = Join-Path $PWD "data\raw\original"
$files = @(
    "11 - prova prtica.pdf",
    "banner.csv",
    "Doc1.pdf",
    "Doc2.pdf",
    "Doc3.pdf",
    "Doc4.pdf",
    "Doc5.pdf",
    "Doc6.pdf"
)

New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
foreach ($file in $files) {
    Copy-Item -LiteralPath (Join-Path $sourceDirectory $file) `
        -Destination (Join-Path $destinationDirectory $file)
}
```

Em sistemas compatíveis com Bash:

```bash
source_directory="/caminho/para/os/materiais"
destination_directory="data/raw/original"
files=(
  "11 - prova prtica.pdf"
  "banner.csv"
  "Doc1.pdf"
  "Doc2.pdf"
  "Doc3.pdf"
  "Doc4.pdf"
  "Doc5.pdf"
  "Doc6.pdf"
)

mkdir -p "$destination_directory"
for file in "${files[@]}"; do
  cp -- "$source_directory/$file" "$destination_directory/$file"
done
```

Não use `git add -f` nesses arquivos. Caso precise reorganizar a cópia local,
mantenha os originais intactos no diretório em que foram recebidos.

## Manifesto de integridade

O arquivo `source-manifest.json` segue um formato JSON simples:

- `schema_version`: versão inteira do formato;
- `hash_algorithm`: algoritmo aplicado a todos os arquivos;
- `files`: lista de objetos com `name`, `size_bytes` e `sha256`.

O hash é representado por 64 caracteres hexadecimais em minúsculas. O tamanho é
o número exato de bytes, sem conversão de unidade.

No backend, o consumo de `banner.csv` passa exclusivamente pela porta segura do
pacote `prescriptive_maintenance.data`. `consume_banner_source()` preserva a
interface compatível e devolve somente o resultado do consumidor. O caminho da
baseline usa `consume_banner_source_audited()`, que devolve esse resultado junto
de um recibo imutável dos fingerprints efetivamente observados antes e depois do
consumo. O caminho da fonte e o caminho deste manifesto são obrigatórios e
explícitos; as duas verificações ocorrem no mesmo descritor binário read-only.

Para comparar as cópias locais com o manifesto no PowerShell:

```powershell
$dataDirectory = Join-Path $PWD "data\raw\original"
$manifest = Get-Content -Raw "data\source-manifest.json" | ConvertFrom-Json
$errors = @()

foreach ($expected in $manifest.files) {
    $path = Join-Path $dataDirectory $expected.name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $errors += "ausente: $($expected.name)"
        continue
    }

    $actualSize = (Get-Item -LiteralPath $path).Length
    $actualHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()

    if ($actualSize -ne $expected.size_bytes) {
        $errors += "tamanho divergente: $($expected.name)"
    }
    if ($actualHash -ne $expected.sha256) {
        $errors += "hash divergente: $($expected.name)"
    }
}

if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Error $_ }
    throw "Falha na verificação dos materiais."
}

Write-Output "Integridade confirmada para os oito arquivos."
```

Uma verificação bem-sucedida confirma identidade binária com os arquivos usados
para gerar o manifesto; ela não concede licença para publicar esses materiais.

## Fixtures públicas

- `fixtures/banner.synthetic.csv` preserva o cabeçalho e os tipos essenciais do
  conjunto tabular: identificador inteiro, data ISO 8601, medições numéricas,
  rótulo textual e rotação numérica. Seus três rótulos de falha autorizados são
  `synthetic_healthy`, `synthetic_imbalance` e
  `synthetic_bearing_warning`; eles não representam o vocabulário original.
- `fixtures/maintenance.synthetic.txt` contém um relato fictício curto para
  futuros testes de leitura e recuperação textual.

Todos os valores e textos das fixtures são sintéticos e independentes dos
materiais originais.

## Derivados locais dos documentos PDF

A porta `extract_source_documents()` pode gravar o inventário e as extrações
de `Doc1.pdf` a `Doc6.pdf` em um destino explícito sob
`data/processed/documents/`. Antes de executar contra materiais reais,
confirme a proteção do destino:

```powershell
git check-ignore -v data/processed/documents/inventory.v1.json
```

Essa proteção também é aplicada pela porta antes de cada escrita quando o
destino pertence a uma worktree Git. Destinos rastreáveis, symlinks, escapes e
erros de verificação são rejeitados sem criar o artefato.

O inventário contém metadados e estados por documento; cada artefato de
extração contém o texto derivado e as métricas por página. Ambos permanecem
locais e ignorados. Não use `git add -f`, não publique esses JSON e não copie
seu conteúdo para logs, testes ou documentação. Os testes da porta constroem
PDFs, engines e respostas OCR inteiramente sintéticos em diretórios temporários.

## Artefatos públicos derivados aprovados

A baseline pública é o par exato abaixo, identificado pelo SHA-256 aprovado no
manifesto:

- `baselines/banner/<source-sha>/baseline.v1.json`;
- `baselines/banner/<source-sha>/summary.md`.

Os dois arquivos são artefatos de auditoria agregados, determinísticos,
sanitizados e somente leitura. `baseline.v1.json` registra apenas configuração,
integridade pre/post, gates, reconciliações e métricas agregadas aprovadas;
`summary.md` é derivado exclusivamente desse JSON sanitizado e não acrescenta
fatos independentes. Nenhum deles contém linhas, valores ou timestamps
individuais, identificadores, rótulos nominais, caminhos locais ou cópias dos
arquivos originais.

O inventário categórico autorizado é o arquivo exato abaixo, sob a mesma
identidade pública da fonte:

- `inventories/banner/<source-sha>/fault-labels.v1.json`.

Ele contém exatamente os 151 valores de `raw_label`, cada frequência global,
`normalized_label`, `slug` e o estado/resolução auditável de colisão. Metadados
adicionais limitam-se a versões de schema, normalização e Unicode, marca do
escopo categórico aprovado, identidade pública da fonte, recibos pre/post,
reconciliações agregadas, resumo e decisões aprovadas de colisões e
`inventory_id` derivado por SHA-256 da serialização canônica. Cada decisão
registra o fingerprint ligado à versão, ao destino normalizado e aos membros
categóricos exatos e é revalidada offline. Não há identificadores, medições,
timestamps, ocorrências, caminhos locais nem combinações por linha.

Essas exceções não autorizam outras saídas. Arquivos originais, dados brutos,
intermediários ou processados locais e qualquer outro artefato gerado permanecem
ignorados, proibidos de versionamento e fora do repositório público. Os
artefatos não devem ser editados manualmente: a validação pública é somente
leitura e não depende de acessar a fonte local.

## Conteúdo público

Somente fixtures inteiramente sintéticas, samples previamente sanitizados, o par
agregado da baseline e o inventário categórico estritamente limitado definidos
acima podem ser publicados. Um sample sanitizado não pode conter conteúdo
proprietário, identificadores reais, credenciais ou trechos que permitam
reconstruir os materiais originais; `data/raw/`, `data/processed/` e demais
saídas locais permanecem exclusivamente locais e ignoradas pelo Git.
