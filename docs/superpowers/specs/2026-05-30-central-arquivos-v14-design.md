# Central de Arquivos — v1.4 (design)

**Data:** 2026-05-30
**Arquivo único:** `renomear.py` (servidor HTTP stdlib + UI HTML/CSS/JS embutida; PDF via `pypdf`).
**Premissa de design:** seguir o padrão visual/layout já adotado ("site antigo": Times New Roman, preto e branco, sóbrio e minimalista). Fazer apenas as melhorias necessárias — nada de redesenhar o que já funciona.

## Objetivo

Adicionar 6 funções que faltam na prática, todas integradas ao app existente:

1. Ordenação A-Z nas listagens (natural / A-Z / Z-A)
2. Arrastar PDF (drag-and-drop) nas abas de PDF
3. Excluir páginas de um PDF
4. Juntar / Unificar PDF (com controle de marcadores) — funde "juntar" + "unificar"
5. Comprimir PDF (Ghostscript embutido no .exe)
6. Exportar marcadores em .txt

---

## 1. Ordenação nas listagens

**Onde:** tabelas das abas que listam itens de uma pasta — Renomear, Renomear em lote, Colar do Excel.

- Controle no topo da tabela: três botões pequenos no estilo atual — `Natural ▲` · `A-Z` · `Z-A`.
- **Natural** = ordem estilo Windows Explorer (`1, 2, 10`, não `1, 10, 2`), pastas primeiro. Vira o padrão.
- **A-Z / Z-A** = alfabético simples crescente/decrescente (pastas primeiro mantido).
- Implementação **client-side (JS)**: os itens já vêm do servidor; reordenar é só no navegador → troca instantânea, sem round-trip.
- A ordem visível na tabela é a ordem usada ao colar do Excel / gerar a lista de renomeação.

**Comparador natural (JS):** dividir o nome em pedaços de dígitos/não-dígitos e comparar numericamente os trechos numéricos; `localeCompare` com `{numeric:true, sensitivity:'base'}` é a base.

## 2. Arrastar PDF (drag-and-drop)

**Restrição conhecida:** o navegador não entrega o caminho absoluto de arquivos/pastas arrastados (só o conteúdo). Logo, **só PDFs** (não pastas), e via upload de bytes.

- Cada aba de PDF ganha uma área pontilhada: *"Arraste um PDF aqui ou clique em Escolher arquivo"* (estilo sóbrio, borda tracejada fina).
- Ao soltar: JS lê os bytes (`FileReader`/`arrayBuffer`) e faz POST para nova rota **`/api/upload`** (corpo cru + `?name=`). Servidor grava em diretório temporário próprio e devolve `{path, name}`.
- As ferramentas de PDF passam a operar nesse caminho temporário.
- **Saída quando o PDF veio de drag (sem pasta de origem):** resultado oferecido como **download** via `/file?path=...` e/ou "Salvar como…" pelo seletor nativo já existente. Quando o PDF veio do botão "Escolher arquivo" (tem caminho real), mantém o comportamento atual (grava ao lado do original).
- Temporários ficam num subdiretório dedicado (`tempfile.mkdtemp` por sessão); limpeza best-effort.

## 3. Excluir páginas de um PDF — nova aba "Excluir Páginas"

- Entrada de PDF: botão "Escolher arquivo" + área de arrastar.
- Campo de texto de páginas: `ex.: 1, 3, 5-8, 12`. Mostra o total de páginas do PDF; valida faixa.
- Remove as páginas listadas, mantém o resto, na ordem original.
- **Saída — as duas opções** (decisão do usuário):
  - **Novo arquivo** (padrão): sufixo tipo `<nome> (sem páginas).pdf`, com `_unique_name` para não colidir.
  - **Sobrescrever o original**: caixa de seleção; grava no próprio caminho (via `os.replace` a partir de `.tmp`). Entra na pilha de Desfazer quando o original tinha caminho real.
- Bloqueia excluir 100% das páginas (resultaria em PDF vazio).

**Backend:** `parse_page_spec(spec, total) -> set[int]` (1-based → índices), `pdf_delete_pages(src, pages, out)`.

## 4. Juntar / Unificar PDF — nova aba "Juntar PDF"

Funde o pedido "juntar PDF" com "unificar arquivos escolhendo o nível do marcador" (usuário escolheu **seleção manual**).

- Lista de PDFs adicionados (arrastar vários e/ou botão "Adicionar"), com **reordenar (↑/↓)** e **remover (✕)** — mesma estética dos editores de linha já existentes (ex.: Marcadores).
- **Marcadores ("nível")** — escolha de modo:
  - **Um marcador por arquivo** (título = nome do arquivo sem extensão) — padrão;
  - **Agrupar por pasta de origem** (pasta = marcador nível 1, arquivo = nível 2);
  - **Sem marcadores**.
- Saída: 1 PDF único; usuário define o nome (e destino, via seletor nativo ou download se a origem foi drag).
- Borda: "agrupar por pasta" só se aplica a PDFs vindos do seletor (têm pasta real); arrastados (sem pasta) caem em "um marcador por arquivo".

**Backend:** `pdf_merge(items, out, bookmark_mode)` — `items` = `[{path, title, group}]`; usa `PdfWriter.append`/`add_page` + `add_outline_item` (mesmo padrão de `pdf_save_outline`).

## 5. Comprimir PDF — nova aba "Comprimir PDF"

- Motor: **Ghostscript embutido no .exe**.
  - `resolve_ghostscript()`: se `sys.frozen`, procura em `sys._MEIPASS/gs/gswin64c.exe`; senão procura cópia vendorizada no repo e depois no PATH (`gswin64c`/`gswin32c`); se nada, retorna erro amigável ("Ghostscript não encontrado").
- Presets de qualidade (→ `-dPDFSETTINGS`):
  - **Máxima compressão** → `/screen` (~72 dpi)
  - **Equilíbrio** → `/ebook` (~150 dpi) — padrão
  - **Alta qualidade** → `/printer` (~300 dpi)
- Chamada: `gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 -dPDFSETTINGS=/<preset> -dNOPAUSE -dBATCH -dQUIET -o <out> <in>`, com `CREATE_NO_WINDOW`, caminhos entre aspas.
- Mostra **tamanho antes → depois** e o % economizado. Saída: novo arquivo (sufixo `(comprimido)`) ou download (se drag).
- **Licença:** Ghostscript é AGPL; embutir em .exe distribuído tem implicações. Aceitável para uso pessoal do usuário (registrado aqui).

**Backend:** `pdf_compress(src, out, quality) -> {ok, before, after, saved_pct}`.

## 6. Exportar marcadores em .txt — na aba "Marcadores"

- Botão **"Exportar lista (.txt)"** reaproveitando `pdf_outline`.
- Formato: indentado por nível (2 espaços por nível) + página alinhada, ex.:
  ```
  Capítulo 1 .......... p.1
    1.1 Introdução .... p.3
  Capítulo 2 .......... p.20
  ```
- Retorna `{content, filename}` no mesmo padrão de `export_list` (download no navegador).

**Backend:** `outline_to_txt(bookmarks) -> str`; rota `/api/pdf-outline-txt`.

---

## Backend — resumo de adições em `renomear.py`

Funções: `parse_page_spec`, `pdf_delete_pages`, `pdf_merge`, `resolve_ghostscript`, `pdf_compress`, `outline_to_txt`, util de upload p/ temp.

Rotas novas: `/api/upload`, `/api/pdf-delete`, `/api/pdf-merge`, `/api/pdf-compress`, `/api/pdf-outline-txt`.

Desfazer (`UNDO_STACK`): incluir o caso de sobrescrever original (excluir páginas / comprimir sobre o original) quando houver caminho real — guardando cópia do original num temp para restaurar.

## UI / organização das abas

- **Uma linha só, uma aba por ferramenta** (padrão atual: links sublinhados separados por `|`). Sem agrupar em duas linhas. Sem mudar cores/tipografia.
- Ordem confirmada (novas em **negrito**):
  `Renomear` | `Renomear em lote` | `Colar do Excel` | `Criar` | `Organizar` | `Exportar lista` | `Extrair Páginas` | **`Excluir Páginas`** | `Dividir PDF` | **`Juntar PDF`** | **`Comprimir PDF`** | `Marcadores` | `Comparar Arquivos`
- Ordenação A-Z e Arrastar PDF **não** viram abas (entram nas abas existentes); Exportar marcadores .txt é só um botão dentro de `Marcadores`.
- Áreas de arrastar: borda tracejada fina (1px), texto em itálico cinza (`#555`), seguindo a paleta atual.
- Botões novos reutilizam classes existentes (`.btn-primary`, `.btn-link`, `.toolbar`, `.box`).

## Empacotamento

- PyInstaller: incluir `gswin64c.exe` + `gsdll64.dll` via `--add-binary` (ou seção `binaries` do `.spec`) numa pasta `gs/`. Atualizar `.spec` e, se necessário, o `Renomear.bat`.
- `pypdf` já é dependência. Documentar no README a inclusão do Ghostscript.

## Testes

- `pytest` para a lógica pura, com PDFs gerados via `pypdf` (`PdfWriter` + páginas em branco):
  - `parse_page_spec` — faixas, vírgulas, limites, entradas inválidas.
  - comparador natural — `1,2,10` vs `1,10,2` (testar a versão de referência em Python espelhando a do JS).
  - `pdf_delete_pages` — contagem de páginas resultante; bloqueio de "excluir tudo".
  - `pdf_merge` — total de páginas = soma; marcadores criados conforme o modo.
  - `outline_to_txt` — indentação e páginas.
- `pdf_compress` — teste roda só se `resolve_ghostscript()` encontrar o binário (senão `skip`).

## Fora de escopo (YAGNI)

- Arrastar pastas (inviável em navegador puro; exigiria app nativo).
- OCR, edição de conteúdo de página, assinatura.
- Compressão lossless avançada por reescrita de imagens em Python (Ghostscript cobre o caso).
