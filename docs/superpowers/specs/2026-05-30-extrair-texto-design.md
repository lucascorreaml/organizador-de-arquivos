# Extrair Texto (PDF → TXT, sem OCR) — v1.6 (design)

**Data:** 2026-05-30
**Arquivo único:** `renomear.py` (servidor HTTP stdlib + UI HTML/CSS/JS embutida; PDF via `pypdf`).
**Premissa:** seguir o padrão visual/layout existente (minimalista, "site antigo"); apenas o necessário; reaproveitar classes e padrões de JS já presentes; app permanece em arquivo único.

## Objetivo

Extrair a **camada de texto** de PDFs **digitais** (não escaneados) usando `pypdf` e baixar um `.txt`. **Não faz OCR**: se o PDF for imagem/escaneado, o app avisa que não há texto selecionável.

## Layout / aba

- **Nova aba "Extrair Texto"** (17ª aba), inserida **logo após "Extrair Páginas"** na barra (ambas começam com "Extrair"). `data-tab="text"`, painel `panel-text`. Linha única, mesmo estilo.

## UI (painel `panel-text`)

- Toolbar: botão `txtPick` "Escolher PDF" + `pathbox` `txtPath`.
- Área de arrastar `txtDrop` (classe `.drop`), aceitando PDF (filtro padrão do `makeDrop`).
- Ao carregar (via `/api/pdf-info`): mostra nome + nº de páginas em `txtCount`.
- Botão `txtGo` "Extrair texto (.txt)" (desabilitado até carregar um PDF).
- Área `txtResult` para mensagens (ex.: aviso de PDF escaneado).

## Comportamento

- Ao clicar em "Extrair texto": chama `/api/pdf-extract-text` com o caminho do PDF.
- **Saída:** o servidor devolve `{ok, content, filename, has_text, pages}`; o JS **baixa um `.txt`** via Blob (mesmo padrão de `exGen` em "Exportar lista" e do "Exportar marcadores"). Nome do arquivo: `<nome do PDF sem extensão>.txt`.
- **Formato do conteúdo:** para cada página, uma linha `--- Página N ---` seguida do texto da página; páginas sem texto mostram `(sem texto)`. Páginas separadas por uma linha em branco.
- **PDF escaneado / sem texto:** se **nenhuma** página tiver texto (`has_text == False`), o JS **não baixa** o arquivo e exibe em `txtResult`/toast: *"Este PDF não tem texto selecionável (provavelmente escaneado). Seria preciso OCR."*
- **Escopo:** todas as páginas (sem seleção de faixa — YAGNI).
- Funciona tanto com PDF **escolhido** pelo botão quanto **arrastado** (upload), pois só lê o conteúdo e devolve texto para download (não grava em disco no servidor).

## Backend

`pdf_extract_text(pdf_path, page_markers=True) -> {ok, content, pages, has_text}`:
- Valida o arquivo; abre com `PdfReader(pdf_path, strict=False)`.
- Para cada página: `txt = (page.extract_text() or "").strip()`; monta o bloco com marcador `--- Página N ---` (quando `page_markers`) e o texto (ou `(sem texto)`).
- `has_text = True` se ao menos uma página retornou texto não vazio.
- Retorna `content` (string completa), `pages` (total) e `has_text`.

Rota `/api/pdf-extract-text` (POST `{path}`):
- Chama `pdf_info` para o nome base e `pdf_extract_text` para o conteúdo.
- Responde `{ok: True, content, filename: "<base>.txt", has_text, pages}` ou `{ok: False, error}`.

## Testes (pytest)

- PDF **com texto**: criar um PDF com texto real (gerar via `reportlab` se disponível, ou — mais simples e sem dependência — construir um PDF mínimo cujo `extract_text()` retorne algo; se não for viável de forma determinística com o helper atual de páginas em branco, testar `has_text=False` no branco e validar o **formato/marcadores** chamando `pdf_extract_text` e checando que `--- Página 1 ---` aparece e que `pages` bate). 
  - Teste garantido sem novas dependências: PDF de N páginas em branco → `has_text == False`, `content` contém `--- Página 1 ---` ... `--- Página N ---` e `(sem texto)`, `pages == N`.
  - Teste de texto real: **se `reportlab` estiver instalado**, gerar 1 página com a string "Texto de teste" e afirmar `has_text == True` e que a string aparece em `content`; caso contrário, `skip`.
- PDF inexistente → `ValueError`.

## Empacotamento

- **Nenhuma dependência nova** (`pypdf` já presente). Sem mudança no `.spec`.

## Fora de escopo (YAGNI)

- OCR (PDF escaneado) — assunto à parte.
- Seleção de faixa de páginas; exportar em Markdown; salvar ao lado do PDF.
