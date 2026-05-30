# Central de Arquivos — v1.5 (design)

**Data:** 2026-05-30
**Arquivo único:** `renomear.py` (servidor HTTP stdlib + UI HTML/CSS/JS embutida; PDF via `pypdf`; compressão e rasterização via Ghostscript embutido).
**Premissa de design:** seguir o padrão visual/layout existente ("site antigo": Times New Roman, P&B, sóbrio, minimalista). Apenas melhorias necessárias; reaproveitar classes CSS e padrões de JS já presentes. App permanece em arquivo único.

## Objetivo

Agregar 6 ferramentas de PDF/imagem, organizadas de forma **consolidada** na barra de abas (uma linha):

1. Dividir PDF por tamanho (MB) — **modo novo** na aba "Dividir PDF" (sem aba nova)
2. Girar páginas + Reordenar/mover páginas — **1 aba nova** "Girar e Reordenar"
3. Imagens → PDF e PDF → Imagens — **1 aba nova** "Imagens ⇄ PDF" (seletor de direção)
4. Proteger / remover senha — **1 aba nova** "Senha"

Total de abas: 13 → **16** (3 novas).

## Layout / abas

Ordem confirmada da barra (novas em **negrito**):
`Renomear | Renomear em lote | Colar do Excel | Criar | Organizar | Exportar lista | Extrair Páginas | Excluir Páginas | Dividir PDF* | Juntar PDF | `**`Girar e Reordenar`**` | `**`Imagens ⇄ PDF`**` | Comprimir PDF | `**`Senha`**` | Marcadores | Comparar Arquivos`

`* "Dividir PDF" ganha o 5º modo "Por tamanho (MB)".`

Sugestão de `data-tab`: `pageops` (Girar e Reordenar), `imgpdf` (Imagens ⇄ PDF), `password` (Senha). Painéis: `panel-pageops`, `panel-imgpdf`, `panel-password`.

---

## 1. Dividir por tamanho (modo na aba "Dividir PDF")

- Na aba existente `divide` (radios `dvMode`: every/parts/burst/bookmarks), adicionar radio **`size`**: "Por tamanho — máx. `[__]` MB por arquivo" (padrão **10**).
- Comportamento: preenchimento guloso — adiciona páginas a uma parte enquanto o arquivo serializado ficar ≤ limite; ao estourar, fecha a parte na página anterior e começa outra. Mede o tamanho real serializando o writer em memória.
- Borda: se uma única página já exceder o limite, ela vira uma parte sozinha (inevitável) — registrar aviso no resultado.
- Saída: mesma subpasta/destino dos outros modos de divisão; nomes `<base> (parte 01).pdf`, etc.

**Backend:** `pdf_split_by_size(pdf_path, dest_folder, max_mb, base_name=None, auto_suffix=True) -> {ok, results:[{name, pages, count, size}], dest, oversize:[...]}`.
**Rota:** `/api/pdf-split-size` (recebe `pdf`, `dest`, `subfolder`, `max_mb`, `base`).
**UI:** a aba `divide` mostra o campo de MB quando o modo `size` está selecionado (padrão escondido como os demais opts), e o handler `dvSplit` chama `/api/pdf-split-size` quando `dvMode == "size"`.

## 2. Girar e Reordenar (aba "Girar e Reordenar")

- Escolher/arrastar um PDF → carrega `pdf_info` (nº de páginas).
- Lista textual das páginas (sem miniaturas, p/ manter leve), uma linha por página na ordem atual:
  - `Página <orig> · [↑] [↓] · [↻ girar]` exibindo a rotação acumulada (0/90/180/270). `↻` soma 90° (cicla). `↑/↓` movem a página na ordem.
- Estado no JS: lista de itens `{src: indiceOriginal, rotate: graus}` na ordem desejada.
- Aplica → grava PDF de saída com as páginas na nova ordem e rotação. Saída: novo arquivo (sufixo ` (girado)`) ou sobrescrever (com backup p/ desfazer).

**Backend:** `pdf_rearrange(pdf_path, ops, out) -> {ok, path, pages}` onde `ops` = lista `[{"src": int(0-based), "rotate": int}]` na ordem final. Usa `reader.pages[src]`, aplica `.rotate(graus)` (múltiplos de 90), adiciona ao writer; grava via `tmp + os.replace`.
**Rota:** `/api/pdf-rearrange` (recebe `pdf`, `ops`, `overwrite`/`out`). Sobrescrever empilha undo `restore`.

## 3. Imagens ⇄ PDF (aba "Imagens ⇄ PDF")

Seletor de direção (radio) no topo: **Imagens → PDF** | **PDF → Imagens**.

### 3a. Imagens → PDF
- Adicionar/arrastar imagens (jpg, jpeg, png, bmp, gif, webp, tif/tiff). Lista reordenável (↑/↓, remover) no mesmo estilo de Juntar PDF. Cada imagem = 1 página, na ordem da lista.
- **Tamanho da página (seletor):** "ajustar à imagem" (página = tamanho da imagem) ou "A4 retrato" (imagem centralizada/escalada para caber em A4, mantendo proporção).
- Saída: 1 PDF (nome definido pelo usuário). Se as imagens vieram de arrastar (sem pasta real), oferecer download.

**Backend:** `images_to_pdf(image_paths, out, page_mode="fit") -> {ok, path, pages}`. Usa **Pillow**: abre cada imagem, converte para RGB; modo `fit` salva no tamanho da imagem; modo `a4` cola numa página A4 (em pontos, 72 dpi) mantendo proporção. Gera o PDF (Pillow `save(..., save_all=True, append_images=...)` ou monta via pypdf a partir de PDFs de 1 página). Implementação de referência: criar cada página como PDF de 1 imagem via Pillow e juntar com pypdf, OU usar `img.save(out, "PDF", save_all=True, append_images=rest)`.
**Rota:** `/api/images-to-pdf` (recebe `images` [paths], `out`, `mode`).

### 3b. PDF → Imagens
- Escolher/arrastar um PDF. **Formato (seletor):** PNG ou JPG. **Resolução (seletor):** Tela (96) / Boa (150, padrão) / Alta (300) dpi.
- Exporta cada página como imagem numa subpasta (padrão `Imagens`), nomes `pagina-001.png`, `pagina-002.png`, ….
- Usa o **Ghostscript já embutido** (sem Pillow nessa direção).

**Backend:** `pdf_to_images(pdf_path, dest_folder, fmt="png", dpi=150) -> {ok, dest, count, files:[...]}`. Chama gs: device `png16m` (PNG) ou `jpeg` (JPG), `-r<dpi>`, `-sOutputFile=<dest>/pagina-%03d.<ext>`, `-dNOPAUSE -dBATCH -dQUIET`, `CREATE_NO_WINDOW`. Usa `resolve_ghostscript()` existente; se ausente, erro amigável.
**Rota:** `/api/pdf-to-images` (recebe `pdf`, `dest`, `subfolder`, `fmt`, `dpi`).

## 4. Senha (aba "Senha")

Seletor de modo (radio): **Proteger** | **Remover**.
- **Proteger:** campo senha (e confirmação). Gera PDF criptografado: `writer.append(reader); writer.encrypt(user_password=..., algorithm="AES-256")` (com fallback se o algoritmo não for suportado pela versão do pypdf). 
- **Remover:** campo "senha atual". Abre o PDF com `PdfReader(...); reader.decrypt(senha)`; se falhar, erro "senha incorreta"; senão grava sem criptografia.
- Saída: novo arquivo (` (protegido)` / ` (sem senha)`) ou sobrescrever (backup p/ desfazer).

**Backend:** `pdf_set_password(pdf_path, out, password) -> {ok, path}` e `pdf_remove_password(pdf_path, out, password) -> {ok, path}` (levanta ValueError em senha incorreta).
**Rota:** `/api/pdf-password` (recebe `pdf`, `mode` ∈ {protect, remove}, `password`, `overwrite`/`out`).

---

## Upload / arrastar (extensão)

- Hoje `save_upload` e o JS `makeDrop` só aceitam PDF. Estender para aceitar **imagens** no painel Imagens→PDF:
  - `save_upload` aceita a extensão original do arquivo (não força `.pdf`); valida que é uma extensão conhecida de PDF/imagem.
  - `makeDrop` ganha um parâmetro de extensões aceitas (ex.: `makeDrop(el, onFile, /\.(pdf|jpe?g|png|bmp|gif|webp|tiff?)$/i)`), default mantém só PDF para os painéis de PDF.
  - O seletor nativo (`/api/choose-file`) ganha um `kind` para imagens (filtros de imagem) quando usado no painel de imagens.

## Backend — resumo

Funções novas: `pdf_split_by_size`, `pdf_rearrange`, `images_to_pdf`, `pdf_to_images`, `pdf_set_password`, `pdf_remove_password`. Ajuste em `save_upload` (extensões). Rotas novas: `/api/pdf-split-size`, `/api/pdf-rearrange`, `/api/images-to-pdf`, `/api/pdf-to-images`, `/api/pdf-password`. Reuso do tipo de desfazer `restore` para sobrescritas.

## Dependência nova

- **Pillow** — necessária só para Imagens → PDF. Adicionar a `requirements-dev.txt` e instalar. PyInstaller detecta Pillow automaticamente; se necessário, incluir em `hiddenimports`/hooks no `.spec`. As demais 5 ferramentas usam só `pypdf` + Ghostscript embutido.

## UI / organização

- 3 abas novas em linha única (sem agrupar em 2 linhas), reaproveitando classes existentes (`.toolbar`, `.box`, `.opts`, `.opt`, `.field`, `.lbl`, `.hint`, `.actionbar`, `.count`, `.drop`, `.mglist-row`, `.btn-primary`, `.btn-link`). Áreas de arrastar com o estilo `.drop` já criado na v1.4.
- "Girar e Reordenar" e "Imagens → PDF" reutilizam o padrão de lista reordenável de "Juntar PDF" (`mglist-row`, botões ↑/↓/remover).
- Saída de operações sobre arquivos arrastados (sem pasta real) → link de download via `/file?path=` (padrão da v1.4).

## Testes (pytest)

- `pdf_split_by_size`: cada parte resultante ≤ limite (gerar PDF com páginas "pesadas" o suficiente para forçar ≥2 partes); soma de páginas preservada; caso de página única acima do limite.
- `pdf_rearrange`: ordem refletida (mapear páginas) e rotação aplicada (checar `/Rotate` nas páginas de saída); validação de graus múltiplos de 90.
- `images_to_pdf`: nº de páginas = nº de imagens; modo "fit" gera página do tamanho da imagem; modo "a4" gera A4. Gerar imagens de teste com Pillow.
- `pdf_to_images`: cria N arquivos no destino (usa gs; `skip` se `resolve_ghostscript()` for None — gs está presente em dev).
- `pdf_set_password`/`pdf_remove_password`: arquivo protegido fica `is_encrypted`; remover com senha correta volta a abrir sem senha; senha errada levanta ValueError.

## Empacotamento

- `central_de_arquivos.spec`: garantir Pillow no bundle (normalmente automático; senão `hiddenimports=['pypdf','PIL']`). Ghostscript continua embutido (pasta `gs/`).
- README: seção "Novidades v1.5".

## Fora de escopo (YAGNI)

- Miniaturas/thumbnails das páginas (manter lista textual leve).
- OCR, conversões Office, permissões granulares de PDF (só senha de abertura).
- Edição de conteúdo das imagens (recorte, filtros).
