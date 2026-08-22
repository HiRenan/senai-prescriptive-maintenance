# Preparação dos materiais locais

Os oito arquivos recebidos para o desafio são fontes locais e não devem ser
adicionados ao Git. Este diretório mantém somente o manifesto de integridade e
fixtures sintéticas que podem ser compartilhadas publicamente.

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
  rótulo textual e rotação numérica.
- `fixtures/maintenance.synthetic.txt` contém um relato fictício curto para
  futuros testes de leitura e recuperação textual.

Todos os valores e textos das fixtures são sintéticos e independentes dos
materiais originais.
