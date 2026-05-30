# Central de Arquivos v1.4 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar 6 ferramentas ao app `renomear.py` (ordenação A-Z nas listagens, arrastar PDF, excluir páginas, juntar/unificar PDF, comprimir PDF via Ghostscript, exportar marcadores .txt) mantendo o visual minimalista atual.

**Architecture:** Arquivo único `renomear.py` = servidor HTTP stdlib (`Handler.do_POST` com rotas `/api/*`) + UI HTML/CSS/JS embutida na string `HTML_PAGE`. Lógica de PDF nova fica em funções puras (testáveis) na metade superior do arquivo; rotas novas em `do_POST`; UI nova dentro de `HTML_PAGE`. Compressão chama o binário do Ghostscript (já instalado em dev; embutido no .exe via PyInstaller).

**Tech Stack:** Python 3.14 (stdlib), `pypdf` 6.7.3, Ghostscript (`gswin64c.exe`), `pytest` (dev), PyInstaller (empacotamento).

**Convenções do projeto a respeitar:** comentários/strings sem acento em nomes de função internos (segue o estilo atual: ASCII em código, acentos só em textos de UI). Funções puras agrupadas por seção com cabeçalho `# ---- ... ----`. CSS reusa classes existentes (`.box`, `.opts`, `.opt`, `.toolbar`, `.btn-primary`, `.btn-link`, `.field`, `.lbl`, `.hint`, `.actionbar`, `.count`, `.pathbox`).

**Onde inserir (âncoras atuais):**
- Funções de PDF puras: depois de `pdf_save_outline` (termina ~linha 494), antes de `# Desfazer (pilha generica)`.
- Rotas: dentro de `Handler.do_POST` (bloco `if/elif` que começa ~linha 622).
- UI: barra de abas ~linhas 994-1003; painéis `<section class="panel">`; JS no fim do `<script>` (~linha 1330 em diante).

---

## Task 1: Setup de testes (pytest + helper de PDF)

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/test_pdf_tools.py`
- Create: `requirements-dev.txt`

- [ ] **Step 1: Instalar pytest**

Run: `python -m pip install pytest pypdf`
Expected: instala `pytest` (pypdf já presente).

- [ ] **Step 2: Criar `requirements-dev.txt`**

```
pypdf>=6
pytest>=8
```

- [ ] **Step 3: Criar helper de PDF de amostra em `tests/conftest.py`**

```python
import os
import pytest
from pypdf import PdfWriter


def make_pdf(path, n_pages, outline=None):
    """Gera um PDF com n_pages paginas em branco. outline = lista de (titulo, depth, page_index_0based)."""
    w = PdfWriter()
    for _ in range(n_pages):
        w.add_blank_page(width=200, height=200)
    parents = {}
    for title, depth, page in (outline or []):
        parent = parents.get(depth - 1) if depth > 1 else None
        ref = w.add_outline_item(title, page, parent=parent)
        parents[depth] = ref
        for d in [k for k in list(parents) if k > depth]:
            del parents[d]
    with open(path, "wb") as f:
        w.write(f)
    return path


@pytest.fixture
def pdf_factory(tmp_path):
    def _make(name, n_pages, outline=None):
        return make_pdf(os.path.join(str(tmp_path), name), n_pages, outline)
    return _make
```

- [ ] **Step 4: Tornar `renomear.py` importável a partir de `tests/`**

Adicionar no topo de `tests/test_pdf_tools.py`:

```python
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import renomear  # noqa: E402
```

- [ ] **Step 5: Rodar pytest para confirmar coleta vazia funciona**

Run: `python -m pytest tests/ -q`
Expected: `no tests ran` (ou 0 testes) sem erro de import.

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_pdf_tools.py requirements-dev.txt
git commit -m "test: scaffold pytest + helper de PDF de amostra"
```

---

## Task 2: `parse_page_spec` (parser de páginas)

**Files:**
- Modify: `renomear.py` (nova função após `pdf_save_outline`)
- Test: `tests/test_pdf_tools.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicionar em `tests/test_pdf_tools.py`:

```python
def test_parse_page_spec_basico():
    assert renomear.parse_page_spec("1,3,5-8,12", 20) == {1, 3, 5, 6, 7, 8, 12}

def test_parse_page_spec_inverte_faixa():
    assert renomear.parse_page_spec("8-5", 10) == {5, 6, 7, 8}

def test_parse_page_spec_aceita_ponto_e_virgula():
    assert renomear.parse_page_spec("1; 2 ;4", 10) == {1, 2, 4}

def test_parse_page_spec_fora_do_intervalo():
    import pytest
    with pytest.raises(ValueError):
        renomear.parse_page_spec("1,99", 10)

def test_parse_page_spec_vazio():
    import pytest
    with pytest.raises(ValueError):
        renomear.parse_page_spec("   ", 10)

def test_parse_page_spec_invalido():
    import pytest
    with pytest.raises(ValueError):
        renomear.parse_page_spec("1,abc", 10)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_pdf_tools.py -k parse_page_spec -q`
Expected: FAIL — `AttributeError: module 'renomear' has no attribute 'parse_page_spec'`.

- [ ] **Step 3: Implementar `parse_page_spec`**

Inserir em `renomear.py` logo após a função `pdf_save_outline` (antes do bloco `# Desfazer`):

```python
# ----------------------------------------------------------------------------
# Paginas: excluir / juntar / comprimir / marcadores em txt
# ----------------------------------------------------------------------------

def parse_page_spec(spec, total):
    """Converte '1,3,5-8' em um conjunto de paginas 1-based validas (1..total).

    Aceita virgula ou ponto-e-virgula como separador e faixas 'a-b' (em
    qualquer ordem). Levanta ValueError em entrada invalida ou fora do intervalo.
    """
    if total <= 0:
        raise ValueError("PDF sem paginas.")
    pages = set()
    for tok in (spec or "").replace(";", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "-" in tok:
            a, _, b = tok.partition("-")
            try:
                start, end = int(a.strip()), int(b.strip())
            except ValueError:
                raise ValueError(f'Trecho invalido: "{tok}".')
            if start > end:
                start, end = end, start
            pages.update(range(start, end + 1))
        else:
            try:
                pages.add(int(tok))
            except ValueError:
                raise ValueError(f'Numero invalido: "{tok}".')
    if not pages:
        raise ValueError("Nenhuma pagina informada.")
    bad = sorted(p for p in pages if p < 1 or p > total)
    if bad:
        raise ValueError(f"Fora do intervalo 1..{total}: {', '.join(map(str, bad))}.")
    return pages
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_pdf_tools.py -k parse_page_spec -q`
Expected: PASS (6 testes).

- [ ] **Step 5: Commit**

```bash
git add renomear.py tests/test_pdf_tools.py
git commit -m "feat: parse_page_spec (parser de paginas '1,3,5-8')"
```

---

## Task 3: `pdf_delete_pages` (excluir páginas)

**Files:**
- Modify: `renomear.py`
- Test: `tests/test_pdf_tools.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_pdf_delete_pages_remove_e_mantem(pdf_factory, tmp_path):
    src = pdf_factory("src.pdf", 10)
    out = str(tmp_path / "out.pdf")
    res = renomear.pdf_delete_pages(src, "2,4-6", out)
    assert res["ok"] and res["kept"] == 6 and res["removed"] == 4
    from pypdf import PdfReader
    assert len(PdfReader(out).pages) == 6

def test_pdf_delete_pages_bloqueia_tudo(pdf_factory, tmp_path):
    import pytest
    src = pdf_factory("src.pdf", 3)
    with pytest.raises(ValueError):
        renomear.pdf_delete_pages(src, "1-3", str(tmp_path / "o.pdf"))

def test_pdf_delete_pages_pdf_inexistente(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        renomear.pdf_delete_pages(str(tmp_path / "nada.pdf"), "1", str(tmp_path / "o.pdf"))
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_pdf_tools.py -k pdf_delete_pages -q`
Expected: FAIL — atributo inexistente.

- [ ] **Step 3: Implementar `pdf_delete_pages`** (logo após `parse_page_spec`)

```python
def pdf_delete_pages(src, spec, out):
    """Grava em `out` o PDF `src` sem as paginas indicadas em `spec`.

    `spec` e a string aceita por parse_page_spec (ex.: '1,3,5-8').
    Levanta ValueError se removeria todas as paginas.
    """
    if not src or not os.path.isfile(src):
        raise ValueError("PDF nao encontrado.")
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(src, strict=False)
    total = len(reader.pages)
    remove = parse_page_spec(spec, total)
    keep = [i for i in range(total) if (i + 1) not in remove]
    if not keep:
        raise ValueError("Isso removeria todas as paginas do PDF.")
    writer = PdfWriter()
    for i in keep:
        writer.add_page(reader.pages[i])
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        writer.write(f)
    os.replace(tmp, out)
    return {"ok": True, "path": out, "kept": len(keep),
            "removed": total - len(keep), "total": total}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_pdf_tools.py -k pdf_delete_pages -q`
Expected: PASS (3 testes).

- [ ] **Step 5: Commit**

```bash
git add renomear.py tests/test_pdf_tools.py
git commit -m "feat: pdf_delete_pages (excluir paginas de um PDF)"
```

---

## Task 4: `pdf_merge` (juntar / unificar PDF)

**Files:**
- Modify: `renomear.py`
- Test: `tests/test_pdf_tools.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
def _count_outline(path):
    from pypdf import PdfReader
    r = PdfReader(path)
    n = 0
    def walk(items):
        nonlocal n
        for it in items:
            if isinstance(it, list):
                walk(it)
            else:
                n += 1
    walk(r.outline)
    return n

def test_pdf_merge_soma_paginas(pdf_factory, tmp_path):
    a = pdf_factory("a.pdf", 2); b = pdf_factory("b.pdf", 3)
    out = str(tmp_path / "merged.pdf")
    res = renomear.pdf_merge([{"path": a}, {"path": b}], out, "file")
    assert res["ok"] and res["pages"] == 5 and res["files"] == 2
    from pypdf import PdfReader
    assert len(PdfReader(out).pages) == 5

def test_pdf_merge_marcador_por_arquivo(pdf_factory, tmp_path):
    a = pdf_factory("a.pdf", 1); b = pdf_factory("b.pdf", 1)
    out = str(tmp_path / "m.pdf")
    renomear.pdf_merge([{"path": a, "title": "Doc A"}, {"path": b, "title": "Doc B"}], out, "file")
    assert _count_outline(out) == 2

def test_pdf_merge_sem_marcadores(pdf_factory, tmp_path):
    a = pdf_factory("a.pdf", 1); b = pdf_factory("b.pdf", 1)
    out = str(tmp_path / "m.pdf")
    renomear.pdf_merge([{"path": a}, {"path": b}], out, "none")
    assert _count_outline(out) == 0

def test_pdf_merge_vazio(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        renomear.pdf_merge([], str(tmp_path / "x.pdf"), "file")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_pdf_tools.py -k pdf_merge -q`
Expected: FAIL — atributo inexistente.

- [ ] **Step 3: Implementar `pdf_merge`** (após `pdf_delete_pages`)

```python
def pdf_merge(items, out, bookmark_mode="file"):
    """Junta varios PDFs (na ordem de `items`) num unico arquivo `out`.

    items: lista de {"path": ..., "title"?: ..., "group"?: ...}
    bookmark_mode: "file" (1 marcador por arquivo), "folder" (agrupa por pasta
    de origem: pasta = nivel 1, arquivo = nivel 2) ou "none".
    """
    if not items:
        raise ValueError("Nenhum PDF para juntar.")
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    group_parents = {}
    files = 0
    for it in items:
        path = it.get("path", "")
        if not path or not os.path.isfile(path):
            raise ValueError(f"PDF nao encontrado: {path}")
        reader = PdfReader(path, strict=False)
        start = len(writer.pages)
        for pg in reader.pages:
            writer.add_page(pg)
        title = (it.get("title") or "").strip() or os.path.splitext(os.path.basename(path))[0]
        if bookmark_mode == "folder":
            group = (it.get("group") or os.path.basename(os.path.dirname(os.path.abspath(path)))
                     or "(raiz)")
            parent = group_parents.get(group)
            if parent is None:
                parent = writer.add_outline_item(group, start)
                group_parents[group] = parent
            writer.add_outline_item(title, start, parent=parent)
        elif bookmark_mode != "none":
            writer.add_outline_item(title, start)
        files += 1
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        writer.write(f)
    os.replace(tmp, out)
    return {"ok": True, "path": out, "files": files, "pages": len(writer.pages)}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_pdf_tools.py -k pdf_merge -q`
Expected: PASS (4 testes).

- [ ] **Step 5: Commit**

```bash
git add renomear.py tests/test_pdf_tools.py
git commit -m "feat: pdf_merge (juntar/unificar PDFs com marcadores)"
```

---

## Task 5: `outline_to_txt` (exportar marcadores .txt)

**Files:**
- Modify: `renomear.py`
- Test: `tests/test_pdf_tools.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_outline_to_txt_indenta_e_pagina():
    bms = [{"title": "Cap 1", "depth": 1, "page": 1},
           {"title": "1.1", "depth": 2, "page": 3},
           {"title": "Cap 2", "depth": 1, "page": 20}]
    txt = renomear.outline_to_txt(bms)
    linhas = txt.splitlines()
    assert linhas[0] == "Cap 1  (p.1)"
    assert linhas[1] == "  1.1  (p.3)"
    assert linhas[2] == "Cap 2  (p.20)"

def test_outline_to_txt_vazio():
    txt = renomear.outline_to_txt([])
    assert "marcadores" in txt.lower()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_pdf_tools.py -k outline_to_txt -q`
Expected: FAIL — atributo inexistente.

- [ ] **Step 3: Implementar `outline_to_txt`** (após `pdf_merge`)

```python
def outline_to_txt(bookmarks):
    """Formata a lista plana de marcadores (de pdf_outline) como texto indentado."""
    if not bookmarks:
        return "(Este PDF nao tem marcadores.)\n"
    lines = []
    for b in bookmarks:
        try:
            depth = max(1, int(b.get("depth", 1)))
        except (TypeError, ValueError):
            depth = 1
        title = str(b.get("title", "")).strip() or "(sem titulo)"
        page = b.get("page")
        indent = "  " * (depth - 1)
        lines.append(f"{indent}{title}  (p.{page})" if page else f"{indent}{title}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_pdf_tools.py -k outline_to_txt -q`
Expected: PASS (2 testes).

- [ ] **Step 5: Commit**

```bash
git add renomear.py tests/test_pdf_tools.py
git commit -m "feat: outline_to_txt (marcadores -> texto indentado)"
```

---

## Task 6: Ghostscript — `resolve_ghostscript` + `pdf_compress`

**Files:**
- Modify: `renomear.py`
- Test: `tests/test_pdf_tools.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_resolve_ghostscript_retorna_str_ou_none():
    gs = renomear.resolve_ghostscript()
    assert gs is None or isinstance(gs, str)

def test_pdf_compress_se_gs_disponivel(pdf_factory, tmp_path):
    import pytest
    if not renomear.resolve_ghostscript():
        pytest.skip("Ghostscript nao encontrado neste ambiente.")
    src = pdf_factory("src.pdf", 3)
    out = str(tmp_path / "out.pdf")
    res = renomear.pdf_compress(src, out, "balance")
    assert res["ok"] and os.path.isfile(out)
    assert res["before"] > 0 and res["after"] > 0

def test_pdf_compress_sem_gs_levanta(monkeypatch, pdf_factory, tmp_path):
    import pytest
    monkeypatch.setattr(renomear, "resolve_ghostscript", lambda: None)
    src = pdf_factory("src.pdf", 1)
    with pytest.raises(ValueError):
        renomear.pdf_compress(src, str(tmp_path / "o.pdf"), "balance")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_pdf_tools.py -k "ghostscript or compress" -q`
Expected: FAIL — atributos inexistentes.

- [ ] **Step 3: Implementar resolução + compressão** (após `outline_to_txt`)

```python
GS_PRESETS = {"max": "/screen", "balance": "/ebook", "high": "/printer"}


def resolve_ghostscript():
    """Retorna o caminho do executavel do Ghostscript, ou None se nao achar.

    Procura, nesta ordem: pasta embutida no .exe (_MEIPASS/gs/), copia
    vendorizada ao lado do script (./gs/), e por fim o PATH do sistema.
    """
    candidates = []
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        candidates += [os.path.join(base, "gs", "gswin64c.exe"),
                       os.path.join(base, "gs", "gswin32c.exe")]
    here = os.path.dirname(os.path.abspath(__file__))
    candidates += [os.path.join(here, "gs", "gswin64c.exe"),
                   os.path.join(here, "gs", "gswin32c.exe")]
    for c in candidates:
        if os.path.isfile(c):
            return c
    for name in ("gswin64c", "gswin32c", "gs"):
        found = shutil.which(name)
        if found:
            return found
    return None


def pdf_compress(src, out, quality="balance"):
    """Comprime `src` em `out` via Ghostscript. quality: max | balance | high."""
    if not src or not os.path.isfile(src):
        raise ValueError("PDF nao encontrado.")
    gs = resolve_ghostscript()
    if not gs:
        raise ValueError("Ghostscript nao encontrado. (Necessario para comprimir.)")
    preset = GS_PRESETS.get(quality, "/ebook")
    before = os.path.getsize(src)
    tmp = out + ".tmp"
    cmd = [gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
           f"-dPDFSETTINGS={preset}", "-dNOPAUSE", "-dBATCH", "-dQUIET",
           "-sOutputFile=" + tmp, src]
    proc = subprocess.run(cmd, capture_output=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0 or not os.path.isfile(tmp):
        detail = proc.stderr.decode("utf-8", "ignore")[:200] if proc.stderr else ""
        raise ValueError("Falha ao comprimir (Ghostscript). " + detail)
    os.replace(tmp, out)
    after = os.path.getsize(out)
    saved = round((1 - after / before) * 100, 1) if before else 0.0
    return {"ok": True, "path": out, "before": before, "after": after, "saved_pct": saved}
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_pdf_tools.py -k "ghostscript or compress" -q`
Expected: PASS (3 testes; o de compressão roda de fato porque o gs está instalado em dev).

- [ ] **Step 5: Commit**

```bash
git add renomear.py tests/test_pdf_tools.py
git commit -m "feat: resolve_ghostscript + pdf_compress (compressao via gs)"
```

---

## Task 7: Upload de PDF (temp) + backup p/ desfazer

**Files:**
- Modify: `renomear.py`
- Test: `tests/test_pdf_tools.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
def test_save_upload_grava_pdf(tmp_path, monkeypatch):
    src = renomear  # so para garantir import
    full = renomear.save_upload("Documento.pdf", b"%PDF-1.4 fake")
    assert os.path.isfile(full)
    assert full.lower().endswith(".pdf")
    with open(full, "rb") as f:
        assert f.read() == b"%PDF-1.4 fake"

def test_save_upload_acrescenta_extensao():
    full = renomear.save_upload("semext", b"x")
    assert full.lower().endswith(".pdf")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_pdf_tools.py -k save_upload -q`
Expected: FAIL — atributo inexistente.

- [ ] **Step 3: Implementar upload + backup** (após `pdf_compress`)

```python
_UPLOAD_DIR = None


def upload_dir():
    global _UPLOAD_DIR
    if _UPLOAD_DIR is None or not os.path.isdir(_UPLOAD_DIR):
        _UPLOAD_DIR = tempfile.mkdtemp(prefix="renomear_up_")
    return _UPLOAD_DIR


def save_upload(name, data):
    """Grava bytes de um PDF arrastado num diretorio temporario; devolve o caminho."""
    safe = suggest_name(os.path.basename(name or "arquivo.pdf")) or "arquivo.pdf"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    dest = _unique_name(upload_dir(), safe)
    full = os.path.join(upload_dir(), dest)
    with open(full, "wb") as f:
        f.write(data)
    return full


def _backup_for_undo(path):
    """Copia `path` para um temporario e devolve o caminho do backup."""
    fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="renomear_bak_")
    os.close(fd)
    shutil.copy2(path, tmp)
    return tmp
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_pdf_tools.py -k save_upload -q`
Expected: PASS (2 testes).

- [ ] **Step 5: Rodar a suíte toda**

Run: `python -m pytest tests/ -q`
Expected: PASS (todos os testes do backend).

- [ ] **Step 6: Commit**

```bash
git add renomear.py tests/test_pdf_tools.py
git commit -m "feat: upload de PDF p/ temp + backup p/ desfazer"
```

---

## Task 8: Rotas HTTP novas + desfazer "restore"

**Files:**
- Modify: `renomear.py` (`Handler.do_POST` e bloco `/api/undo`)

- [ ] **Step 1: Adicionar branch de upload no início do `do_POST`**

Em `do_POST`, logo após `try:` e ANTES de `data = self._read_json()`, inserir:

```python
            if urlparse(self.path).path == "/api/upload":
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b""
                name = parse_qs(urlparse(self.path).query).get("name", ["arquivo.pdf"])[0]
                try:
                    full = save_upload(name, raw)
                    self._send(200, {"ok": True, "path": full, "name": os.path.basename(full)})
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e)})
                return
```

- [ ] **Step 2: Adicionar as rotas de PDF**

Inserir antes do `elif self.path == "/api/undo":` (mantendo o estilo dos `elif` existentes):

```python
            elif self.path == "/api/gs-check":
                self._send(200, {"available": resolve_ghostscript() is not None})

            elif self.path == "/api/pdf-delete":
                pdf = data.get("pdf", "")
                spec = data.get("pages", "")
                overwrite = bool(data.get("overwrite"))
                try:
                    info = pdf_info(pdf)
                    folder, base = info["folder"], os.path.splitext(info["name"])[0]
                    out = pdf if overwrite else os.path.join(
                        folder, _unique_name(folder, base + " (sem paginas).pdf"))
                    backup = _backup_for_undo(pdf) if overwrite else None
                    res = pdf_delete_pages(pdf, spec, out)
                    if overwrite and backup:
                        UNDO_STACK.append({"type": "restore", "folder": folder,
                                           "target": pdf, "backup": backup})
                        res["can_undo"] = True
                    self._send(200, res)
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e)})

            elif self.path == "/api/pdf-merge":
                items = data.get("items", [])
                out = (data.get("out") or "").strip()
                mode = data.get("mode", "file")
                if not out:
                    self._send(200, {"ok": False, "error": "Caminho de saida nao informado."})
                    return
                if not out.lower().endswith(".pdf"):
                    out += ".pdf"
                try:
                    self._send(200, pdf_merge(items, out, mode))
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e)})

            elif self.path == "/api/pdf-compress":
                pdf = data.get("pdf", "")
                quality = data.get("quality", "balance")
                overwrite = bool(data.get("overwrite"))
                try:
                    info = pdf_info(pdf)
                    folder, base = info["folder"], os.path.splitext(info["name"])[0]
                    out = pdf if overwrite else os.path.join(
                        folder, _unique_name(folder, base + " (comprimido).pdf"))
                    backup = _backup_for_undo(pdf) if overwrite else None
                    res = pdf_compress(pdf, out, quality)
                    if overwrite and backup:
                        UNDO_STACK.append({"type": "restore", "folder": folder,
                                           "target": pdf, "backup": backup})
                        res["can_undo"] = True
                    self._send(200, res)
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e)})

            elif self.path == "/api/pdf-outline-txt":
                try:
                    o = pdf_outline(data.get("path", ""))
                    self._send(200, {"ok": True, "content": outline_to_txt(o["bookmarks"]),
                                     "filename": "marcadores.txt"})
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e)})
```

- [ ] **Step 3: Tratar o tipo "restore" no `/api/undo`**

No bloco do `/api/undo`, dentro da cadeia `if t == ... elif ...`, adicionar antes do `else:` final:

```python
                elif t == "restore":
                    target, backup = op.get("target"), op.get("backup")
                    if backup and os.path.isfile(backup):
                        shutil.copy2(backup, target)
                        try:
                            os.remove(backup)
                        except OSError:
                            pass
                    msg = "Arquivo restaurado."
```

- [ ] **Step 4: Smoke test manual das rotas**

Run: `python renomear.py` (abre no navegador). Em um terminal separado:

```bash
python -c "import urllib.request,json; r=urllib.request.urlopen('http://127.0.0.1:PORT/api/gs-check',data=b'{}'); print(r.read())"
```
(Substitua PORT pela porta exibida no console.) Expected: `{"available": true}`.
Feche a janela do app ao terminar.

- [ ] **Step 5: Commit**

```bash
git add renomear.py
git commit -m "feat: rotas /api/upload, pdf-delete, pdf-merge, pdf-compress, pdf-outline-txt + undo restore"
```

---

## Task 9: Abas novas (HTML) + painéis vazios

**Files:**
- Modify: `renomear.py` (`HTML_PAGE`: barra de abas ~994-1003 e seção de painéis)

- [ ] **Step 1: Inserir as 3 abas na ordem confirmada**

Substituir o trecho da barra de abas (linhas ~1000-1003) por:

```html
    <button class="tab" data-tab="pdf">Extrair Páginas</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="delpages">Excluir Páginas</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="divide">Dividir PDF</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="merge">Juntar PDF</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="compress">Comprimir PDF</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="marks">Marcadores</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="compare">Comparar Arquivos</button>
```

- [ ] **Step 2: Adicionar os 3 painéis vazios**

Logo após o painel `panel-marks` (ou em qualquer ponto entre painéis existentes — a ordem dos `<section>` não importa, só a das abas), adicionar:

```html
  <!-- ===== EXCLUIR PAGINAS ===== -->
  <section class="panel" id="panel-delpages">
    <div class="toolbar">
      <button class="btn-primary" id="dpPick">Escolher PDF</button>
      <span class="pathbox" id="dpPath">Nenhum PDF selecionado</span>
    </div>
    <div class="drop" id="dpDrop">Arraste um PDF aqui</div>
    <div class="box">
      <div class="field">
        <span class="lbl">Páginas a excluir</span>
        <input type="text" id="dpPages" placeholder="ex.: 1, 3, 5-8, 12">
        <p class="hint" id="dpInfo"></p>
      </div>
      <div class="opts">
        <label class="opt"><input type="radio" name="dpMode" value="new" checked> Salvar como novo arquivo</label>
        <label class="opt"><input type="radio" name="dpMode" value="over" id="dpOver"> Sobrescrever o original</label>
      </div>
    </div>
    <div class="actionbar">
      <span class="count" id="dpCount">Escolha um PDF.</span>
      <button id="dpUndo" disabled>Desfazer</button>
      <button id="dpGo" disabled class="btn-primary">Excluir páginas</button>
    </div>
    <div id="dpResult"></div>
  </section>

  <!-- ===== JUNTAR PDF ===== -->
  <section class="panel" id="panel-merge">
    <div class="toolbar">
      <button class="btn-primary" id="mgAdd">Adicionar PDF(s)</button>
      <button id="mgClear" disabled>Limpar lista</button>
    </div>
    <div class="drop" id="mgDrop">Arraste um ou mais PDFs aqui</div>
    <div id="mgList"></div>
    <div class="box">
      <div class="field">
        <span class="lbl">Marcadores</span>
        <div class="opts">
          <label class="opt"><input type="radio" name="mgBm" value="file" checked> Um marcador por arquivo</label>
          <label class="opt"><input type="radio" name="mgBm" value="folder"> Agrupar por pasta de origem</label>
          <label class="opt"><input type="radio" name="mgBm" value="none"> Sem marcadores</label>
        </div>
      </div>
      <div class="field">
        <span class="lbl">Nome do arquivo final</span>
        <input type="text" id="mgName" value="Juntado.pdf" style="width:60%">
      </div>
    </div>
    <div class="actionbar">
      <span class="count" id="mgCount">Adicione ao menos 2 PDFs.</span>
      <button id="mgGo" disabled class="btn-primary">Juntar PDF</button>
    </div>
    <div id="mgResult"></div>
  </section>

  <!-- ===== COMPRIMIR PDF ===== -->
  <section class="panel" id="panel-compress">
    <div class="toolbar">
      <button class="btn-primary" id="cpPick">Escolher PDF</button>
      <span class="pathbox" id="cpPath">Nenhum PDF selecionado</span>
    </div>
    <div class="drop" id="cpDrop">Arraste um PDF aqui</div>
    <div class="box">
      <div class="field">
        <span class="lbl">Qualidade / compressão</span>
        <div class="opts">
          <label class="opt"><input type="radio" name="cpQ" value="max"> Máxima compressão (~72 dpi)</label>
          <label class="opt"><input type="radio" name="cpQ" value="balance" checked> Equilíbrio (~150 dpi)</label>
          <label class="opt"><input type="radio" name="cpQ" value="high"> Alta qualidade (~300 dpi)</label>
        </div>
      </div>
      <div class="opts">
        <label class="opt"><input type="radio" name="cpMode" value="new" checked> Salvar como novo arquivo</label>
        <label class="opt"><input type="radio" name="cpMode" value="over"> Sobrescrever o original</label>
      </div>
      <p class="hint" id="cpGsWarn" style="display:none"><b>Ghostscript não encontrado</b> — a compressão ficará indisponível.</p>
    </div>
    <div class="actionbar">
      <span class="count" id="cpCount">Escolha um PDF.</span>
      <button id="cpUndo" disabled>Desfazer</button>
      <button id="cpGo" disabled class="btn-primary">Comprimir</button>
    </div>
    <div id="cpResult"></div>
  </section>
```

- [ ] **Step 3: Adicionar CSS da área de arrastar e da lista de juntar**

No bloco `<style>` (após a regra `.box{...}` ~linha 924), adicionar:

```css
  .drop{border:1px dashed #999;padding:14px;margin:10px 0;text-align:center;font-style:italic;color:#555;cursor:default}
  .drop.dragover{border-color:#000;background:#f0f0f0;color:#000}
  .sortctl{font-size:13.5px;margin-left:8px}
  .sortbtn{font-size:13px;padding:2px 7px;margin-left:3px}
  .sortbtn.on{font-weight:bold;background:#eee}
  .mglist-row{display:flex;align-items:center;gap:8px;padding:5px 2px;border-bottom:1px solid #eee}
  .mglist-row .nm{flex:1;min-width:0;word-break:break-word}
  .mglist-row .pg{font-style:italic;font-size:13px;color:#555}
```

- [ ] **Step 4: Verificar que o app abre e as abas aparecem**

Run: `python renomear.py`
Expected: o navegador abre; as abas "Excluir Páginas", "Juntar PDF", "Comprimir PDF" aparecem e trocam para painéis (ainda sem lógica). Feche a janela.

- [ ] **Step 5: Commit**

```bash
git add renomear.py
git commit -m "feat(ui): abas + paineis (Excluir/Juntar/Comprimir) e CSS de arrastar"
```

---

## Task 10: Helpers de JS compartilhados (upload, drop, download, sort)

**Files:**
- Modify: `renomear.py` (`HTML_PAGE`, dentro do `<script>`, após `chooseFolder` ~linha 1363)

- [ ] **Step 1: Adicionar helpers JS**

Inserir logo após a linha `async function chooseFolder(start){...}`:

```javascript
async function uploadFile(file){
  const r=await fetch("/api/upload?name="+encodeURIComponent(file.name),{method:"POST",body:file});
  return r.json();
}
function makeDrop(el,onPdf){
  if(!el) return;
  el.addEventListener("dragover",e=>{e.preventDefault();el.classList.add("dragover");});
  el.addEventListener("dragleave",()=>el.classList.remove("dragover"));
  el.addEventListener("drop",async e=>{
    e.preventDefault();el.classList.remove("dragover");
    const files=[...(e.dataTransfer.files||[])].filter(f=>/\.pdf$/i.test(f.name));
    if(!files.length){toast("Solte um arquivo PDF.");return;}
    for(const f of files){const r=await uploadFile(f);if(r.ok)onPdf(r.path,r.name,true);else toast(r.error||"Falha no upload.");}
  });
}
function downloadLink(serverPath,label){
  const url="/file?path="+encodeURIComponent(serverPath);
  return '<a class="btn-link" href="'+url+'" download>'+esc(label||"baixar arquivo")+'</a>';
}
function cmpName(a,b,numeric){return a.localeCompare(b,undefined,{numeric:numeric,sensitivity:'base'});}
function sortItems(items,mode){
  const arr=items.slice();
  arr.sort((a,b)=>{
    if(a.is_dir!==b.is_dir)return a.is_dir?-1:1;
    if(mode==='natural')return cmpName(a.name,b.name,true);
    if(mode==='za')return cmpName(b.name,a.name,false);
    return cmpName(a.name,b.name,false); // 'az'
  });
  return arr;
}
function attachSort(barEl,getItems,setItems,reRender){
  if(!barEl) return;
  barEl.querySelectorAll(".sortbtn").forEach(btn=>btn.addEventListener("click",()=>{
    const mode=btn.dataset.sort;
    barEl.querySelectorAll(".sortbtn").forEach(b=>b.classList.toggle("on",b===btn));
    setItems(sortItems(getItems(),mode));
    reRender();
  }));
}
```

- [ ] **Step 2: Verificar sintaxe (app abre sem erro de JS)**

Run: `python renomear.py` → abrir o console do navegador (F12) e confirmar **sem erros** em vermelho. Feche a janela.

- [ ] **Step 3: Commit**

```bash
git add renomear.py
git commit -m "feat(ui): helpers JS (uploadFile, makeDrop, downloadLink, sort)"
```

---

## Task 11: Controle de ordenação nas listagens (Renomear, Lote, Excel)

**Files:**
- Modify: `renomear.py` (`HTML_PAGE`: toolbars dos 3 painéis + JS de cada um)

- [ ] **Step 1: Adicionar o controle de ordem nas 3 toolbars**

Em cada uma das toolbars dos painéis `rename`, `batch` e `excel`, logo após o botão "Recarregar" correspondente (`rnReload`, `btReload`, `xlReload`), inserir um `<span>` com `id` próprio:

Painel Renomear (após `<button id="rnReload" ...>`):
```html
      <span class="sortctl" id="rnSort">Ordem: <button class="sortbtn on" data-sort="natural">Natural</button><button class="sortbtn" data-sort="az">A-Z</button><button class="sortbtn" data-sort="za">Z-A</button></span>
```
Painel Renomear em lote (após `<button id="btReload" ...>`):
```html
      <span class="sortctl" id="btSort">Ordem: <button class="sortbtn on" data-sort="natural">Natural</button><button class="sortbtn" data-sort="az">A-Z</button><button class="sortbtn" data-sort="za">Z-A</button></span>
```
Painel Colar do Excel (após `<button id="xlReload" ...>`):
```html
      <span class="sortctl" id="xlSort">Ordem: <button class="sortbtn on" data-sort="natural">Natural</button><button class="sortbtn" data-sort="az">A-Z</button><button class="sortbtn" data-sort="za">Z-A</button></span>
```

- [ ] **Step 2: Aplicar ordem natural por padrão e ligar os botões — Renomear**

No JS do painel Renomear: localizar a função `rnAll()` (`function rnAll(){rnRenderCrumbs();rnRender();rnUpd();}`) e substituir por:

```javascript
function rnAll(){RN.items=sortItems(RN.items,RN.sortMode||'natural');rnRenderCrumbs();rnRender();rnUpd();}
```

Adicionar `sortMode:'natural'` ao objeto `RN` (na linha `const RN={...}`), e logo após a definição de `rnAll` (ou junto aos outros `addEventListener` do painel) ligar os botões:

```javascript
attachSort(q("rnSort"),()=>RN.items,v=>{RN.items=v;},()=>{rnRenderCrumbs();rnRender();rnUpd();});
q("rnSort").querySelectorAll(".sortbtn").forEach(b=>b.addEventListener("click",()=>{RN.sortMode=b.dataset.sort;}));
```

- [ ] **Step 3: Repetir para Renomear em lote (BT) e Colar do Excel (XL)**

Localizar a função que faz o render após carregar a pasta em cada painel (no de lote, onde `BT.items` é renderizado; no de excel, onde `XL.items` é renderizado) e, **imediatamente antes do render**, ordenar os itens. Use o mesmo padrão:

Para o painel de lote — encontrar onde a tabela é montada a partir de `BT.items` (função de render do lote) e inserir no topo dela:
```javascript
  BT.items=sortItems(BT.items,BT.sortMode||'natural');
```
e ligar os botões (junto aos demais `addEventListener` do lote):
```javascript
attachSort(q("btSort"),()=>BT.items,v=>{BT.items=v;},btRender);
q("btSort").querySelectorAll(".sortbtn").forEach(b=>b.addEventListener("click",()=>{BT.sortMode=b.dataset.sort;}));
```
> Substitua `btRender` pelo nome real da função de render do painel de lote (verifique no arquivo; ex.: `btRenderList`). Faça o mesmo para o Excel usando `XL`, `q("xlSort")` e a função de render do Excel.

- [ ] **Step 4: Teste manual**

Run: `python renomear.py`. Numa pasta com itens nomeados `1`, `2`, `10`, `arquivo`, abra em "Renomear":
- "Natural" (padrão): ordem `1, 2, 10`.
- "A-Z": ordem `1, 10, 2`.
- "Z-A": ordem inversa.
Confirme o mesmo no "Colar do Excel". Feche a janela.

- [ ] **Step 5: Commit**

```bash
git add renomear.py
git commit -m "feat(ui): ordenacao Natural/A-Z/Z-A em Renomear, Lote e Excel"
```

---

## Task 12: Lógica do painel "Excluir Páginas"

**Files:**
- Modify: `renomear.py` (`HTML_PAGE`, JS — adicionar bloco novo antes de `// Desfazer global + Modal`)

- [ ] **Step 1: Adicionar o JS do painel**

Inserir um novo bloco de script (antes do comentário `//  Desfazer global + Modal`):

```javascript
// ===================================================================
//  EXCLUIR PAGINAS
// ===================================================================
const DP={pdf:null,name:"",pages:0,fromUpload:false};
function dpUpd(){
  const hasSpec=q("dpPages").value.trim()!=="";
  q("dpGo").disabled=!(DP.pdf&&hasSpec);
  q("dpOver").disabled=DP.fromUpload; // arrastado nao tem original p/ sobrescrever
  q("dpUndo").disabled=!canUndo;
}
async function dpLoad(path,name,fromUpload){
  const r=await api("/api/pdf-info",{path});
  if(!r.ok){toast(r.error||"PDF invalido.");return;}
  DP.pdf=path;DP.name=r.name;DP.pages=r.pages;DP.fromUpload=!!fromUpload;
  q("dpPath").textContent=r.name;q("dpPath").title=path;
  q("dpInfo").textContent="O PDF tem "+r.pages+" página(s).";
  q("dpCount").textContent="Pronto.";
  if(fromUpload){const m=document.querySelector('input[name=dpMode][value=new]');if(m)m.checked=true;}
  dpUpd();
}
q("dpPick").addEventListener("click",async()=>{
  const r=await api("/api/choose-file",{kind:"file"});
  if(r.cancelled||!r.path)return;dpLoad(r.path,baseName(r.path),false);
});
makeDrop(q("dpDrop"),(p,n)=>dpLoad(p,n,true));
q("dpPages").addEventListener("input",dpUpd);
q("dpUndo").addEventListener("click",undoLast);
q("dpGo").addEventListener("click",async()=>{
  const overwrite=(document.querySelector('input[name=dpMode]:checked')||{}).value==="over" && !DP.fromUpload;
  q("dpGo").disabled=true;q("dpGo").textContent="Excluindo…";
  const r=await api("/api/pdf-delete",{pdf:DP.pdf,pages:q("dpPages").value,overwrite});
  q("dpGo").textContent="Excluir páginas";
  if(!r.ok){toast(r.error||"Falha.");dpUpd();return;}
  setCanUndo(!!r.can_undo);
  q("dpResult").innerHTML='<p class="hint">✓ Geradas '+r.kept+' página(s) (removidas '+r.removed+'). '
    +(DP.fromUpload?downloadLink(r.path,"baixar resultado"):"Salvo em: "+esc(baseName(r.path)))+'</p>';
  dpUpd();
});
```

- [ ] **Step 2: Teste manual**

Run: `python renomear.py`. Em "Excluir Páginas":
- Escolha um PDF de 5+ páginas pelo botão; digite `2,4`; "Excluir páginas" → cria `<nome> (sem paginas).pdf` com 2 a menos. Confirme contagem.
- Marque "Sobrescrever o original" e repita → "Desfazer" deve restaurar o arquivo.
- Arraste um PDF → opção "Sobrescrever" fica desabilitada; resultado vira link "baixar resultado".
Feche a janela.

- [ ] **Step 3: Commit**

```bash
git add renomear.py
git commit -m "feat(ui): painel Excluir Paginas (novo arquivo / sobrescrever / arrastar)"
```

---

## Task 13: Lógica do painel "Juntar PDF"

**Files:**
- Modify: `renomear.py` (`HTML_PAGE`, JS)

- [ ] **Step 1: Adicionar o JS do painel**

Inserir após o bloco de Excluir Páginas:

```javascript
// ===================================================================
//  JUNTAR PDF
// ===================================================================
const MG={list:[]}; // cada item: {path,name,pages,group}
function mgRender(){
  const box=q("mgList");
  if(!MG.list.length){box.innerHTML='<div class="empty">Nenhum PDF na lista.</div>';mgUpd();return;}
  box.innerHTML=MG.list.map((it,i)=>(
    '<div class="mglist-row">'
    +'<span class="pg">#'+(i+1)+'</span>'
    +'<span class="nm">'+esc(it.name)+' <span class="pg">('+it.pages+' pág.)</span></span>'
    +'<button class="btn-link" data-up="'+i+'" '+(i===0?'disabled':'')+'>↑</button>'
    +'<button class="btn-link" data-down="'+i+'" '+(i===MG.list.length-1?'disabled':'')+'>↓</button>'
    +'<button class="btn-link" data-del="'+i+'">remover</button>'
    +'</div>')).join("");
  box.querySelectorAll("[data-up]").forEach(b=>b.addEventListener("click",()=>{const i=+b.dataset.up;[MG.list[i-1],MG.list[i]]=[MG.list[i],MG.list[i-1]];mgRender();}));
  box.querySelectorAll("[data-down]").forEach(b=>b.addEventListener("click",()=>{const i=+b.dataset.down;[MG.list[i+1],MG.list[i]]=[MG.list[i],MG.list[i+1]];mgRender();}));
  box.querySelectorAll("[data-del]").forEach(b=>b.addEventListener("click",()=>{MG.list.splice(+b.dataset.del,1);mgRender();}));
  mgUpd();
}
function mgUpd(){
  q("mgGo").disabled=MG.list.length<2;
  q("mgClear").disabled=!MG.list.length;
  q("mgCount").textContent=MG.list.length?(MG.list.length+" PDF(s) na lista."):"Adicione ao menos 2 PDFs.";
}
async function mgAddPath(path,name){
  const r=await api("/api/pdf-info",{path});
  if(!r.ok){toast(r.error||"PDF invalido: "+name);return;}
  MG.list.push({path,name:r.name,pages:r.pages,group:""});
  mgRender();
}
q("mgAdd").addEventListener("click",async()=>{
  const r=await api("/api/choose-file",{kind:"file"});
  if(r.cancelled||!r.path)return;await mgAddPath(r.path,baseName(r.path));
});
makeDrop(q("mgDrop"),(p,n)=>mgAddPath(p,n));
q("mgClear").addEventListener("click",()=>{MG.list=[];mgRender();});
q("mgGo").addEventListener("click",async()=>{
  const mode=(document.querySelector('input[name=mgBm]:checked')||{}).value||"file";
  let name=(q("mgName").value||"Juntado.pdf").trim();
  // grava na pasta do 1o PDF que tiver caminho real; se todos forem upload, cai no temp e baixa
  const firstReal=MG.list.find(it=>!/renomear_up_/.test(it.path));
  const folder=firstReal?firstReal.path.replace(/[\\/][^\\/]+$/,""):"";
  const out=(folder?folder+"\\":"")+name;
  q("mgGo").disabled=true;q("mgGo").textContent="Juntando…";
  const items=MG.list.map(it=>({path:it.path,title:it.name.replace(/\.pdf$/i,""),group:it.group}));
  const r=await api("/api/pdf-merge",{items,out:out,mode});
  q("mgGo").textContent="Juntar PDF";mgUpd();
  if(!r.ok){toast(r.error||"Falha ao juntar.");return;}
  q("mgResult").innerHTML='<p class="hint">✓ '+r.files+' PDF(s) juntos, '+r.pages+' página(s). '
    +(firstReal?"Salvo em: "+esc(baseName(r.path)):downloadLink(r.path,"baixar PDF juntado"))+'</p>';
});
mgRender();
```

- [ ] **Step 2: Teste manual**

Run: `python renomear.py`. Em "Juntar PDF":
- Adicione 2-3 PDFs (botão e/ou arrastando vários); reordene com ↑/↓; remova um.
- "Um marcador por arquivo" → "Juntar PDF" gera 1 PDF na pasta do primeiro; abra-o e confira páginas somadas e marcadores.
- Teste "Sem marcadores" e "Agrupar por pasta de origem".
- Junte só PDFs arrastados → resultado vira link "baixar PDF juntado".
Feche a janela.

- [ ] **Step 3: Commit**

```bash
git add renomear.py
git commit -m "feat(ui): painel Juntar PDF (lista reordenavel + modos de marcador)"
```

---

## Task 14: Lógica do painel "Comprimir PDF"

**Files:**
- Modify: `renomear.py` (`HTML_PAGE`, JS)

- [ ] **Step 1: Adicionar o JS do painel**

Inserir após o bloco de Juntar PDF:

```javascript
// ===================================================================
//  COMPRIMIR PDF
// ===================================================================
const CP={pdf:null,name:"",fromUpload:false,gsOk:true};
function fmtKB(n){return n>=1048576?(n/1048576).toFixed(1)+" MB":Math.max(1,Math.round(n/1024))+" KB";}
function cpUpd(){
  q("cpGo").disabled=!(CP.pdf&&CP.gsOk);
  const over=document.querySelector('input[name=cpMode][value=over]');if(over)over.disabled=CP.fromUpload;
  q("cpUndo").disabled=!canUndo;
}
async function cpLoad(path,name,fromUpload){
  const r=await api("/api/pdf-info",{path});
  if(!r.ok){toast(r.error||"PDF invalido.");return;}
  CP.pdf=path;CP.name=r.name;CP.fromUpload=!!fromUpload;
  q("cpPath").textContent=r.name;q("cpPath").title=path;q("cpCount").textContent="Pronto.";
  if(fromUpload){const m=document.querySelector('input[name=cpMode][value=new]');if(m)m.checked=true;}
  cpUpd();
}
(async()=>{const g=await api("/api/gs-check",{});CP.gsOk=!!g.available;if(!g.available)q("cpGsWarn").style.display="";cpUpd();})();
q("cpPick").addEventListener("click",async()=>{
  const r=await api("/api/choose-file",{kind:"file"});
  if(r.cancelled||!r.path)return;cpLoad(r.path,baseName(r.path),false);
});
makeDrop(q("cpDrop"),(p,n)=>cpLoad(p,n,true));
q("cpUndo").addEventListener("click",undoLast);
q("cpGo").addEventListener("click",async()=>{
  const quality=(document.querySelector('input[name=cpQ]:checked')||{}).value||"balance";
  const overwrite=(document.querySelector('input[name=cpMode]:checked')||{}).value==="over" && !CP.fromUpload;
  q("cpGo").disabled=true;q("cpGo").textContent="Comprimindo…";
  const r=await api("/api/pdf-compress",{pdf:CP.pdf,quality,overwrite});
  q("cpGo").textContent="Comprimir";
  if(!r.ok){toast(r.error||"Falha ao comprimir.");cpUpd();return;}
  setCanUndo(!!r.can_undo);
  q("cpResult").innerHTML='<p class="hint">✓ '+fmtKB(r.before)+' → <b>'+fmtKB(r.after)+'</b> (−'+r.saved_pct+'%). '
    +(CP.fromUpload?downloadLink(r.path,"baixar PDF comprimido"):"Salvo em: "+esc(baseName(r.path)))+'</p>';
  cpUpd();
});
```

- [ ] **Step 2: Teste manual**

Run: `python renomear.py`. Em "Comprimir PDF":
- Escolha um PDF "pesado" (com imagens). "Equilíbrio" → mostra `antes → depois (−X%)` e gera `<nome> (comprimido).pdf`.
- Teste "Máxima compressão". Teste "Sobrescrever" + "Desfazer".
- Arraste um PDF → resultado vira link de download; "Sobrescrever" desabilitado.
Feche a janela.

- [ ] **Step 3: Commit**

```bash
git add renomear.py
git commit -m "feat(ui): painel Comprimir PDF (presets gs + antes/depois)"
```

---

## Task 15: Exportar marcadores (.txt) no painel "Marcadores"

**Files:**
- Modify: `renomear.py` (`HTML_PAGE`: botão no painel marks + handler JS junto a `mkSave`/`mkDivide`)

- [ ] **Step 1: Adicionar o botão no painel Marcadores**

No painel `panel-marks`, ao lado dos botões existentes (`mkSave`, `mkDivide`), adicionar:

```html
      <button id="mkExportTxt">Exportar lista (.txt)</button>
```
(Coloque-o na mesma linha/toolbar onde estão "Salvar" e "Dividir por estes marcadores".)

- [ ] **Step 2: Adicionar o handler JS**

Logo antes da chamada `mkRender();` (final do bloco de Marcadores), inserir:

```javascript
q("mkExportTxt").addEventListener("click",async()=>{
  if(!MK.pdf){toast("Escolha um PDF primeiro.");return;}
  const r=await api("/api/pdf-outline-txt",{path:MK.pdf});
  if(!r.ok){toast(r.error||"Falha ao exportar.");return;}
  const blob=new Blob([r.content],{type:"text/plain;charset=utf-8"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);
  a.download=(MK.name||"marcadores")+" - marcadores.txt";
  document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(a.href);
  toast("Lista de marcadores baixada.");
});
```

- [ ] **Step 3: Teste manual**

Run: `python renomear.py`. Em "Marcadores": carregue um PDF com marcadores, clique "Exportar lista (.txt)" → baixa um .txt indentado por nível com `(p.N)`. Para PDF sem marcadores, o .txt diz que não há marcadores. Feche a janela.

- [ ] **Step 4: Commit**

```bash
git add renomear.py
git commit -m "feat(ui): exportar lista de marcadores em .txt"
```

---

## Task 16: Empacotamento — embutir Ghostscript no .exe

**Files:**
- Create: `central_de_arquivos.spec` (raiz do projeto)
- Modify: `README.md`

- [ ] **Step 1: Copiar os binários do Ghostscript para `gs/` no projeto**

Run (PowerShell):
```powershell
New-Item -ItemType Directory -Force "gs"
Copy-Item "C:\Program Files\gs\gs10.06.0\bin\gswin64c.exe" "gs\"
Copy-Item "C:\Program Files\gs\gs10.06.0\bin\gsdll64.dll" "gs\"
```
Expected: `gs\gswin64c.exe` e `gs\gsdll64.dll` existem.

- [ ] **Step 2: Ignorar a pasta gs no git (binários grandes)**

Adicionar ao `.gitignore` (criar se não existir):
```
gs/
__pycache__/
build/
dist/
.pytest_cache/
```

- [ ] **Step 3: Criar o `.spec` do PyInstaller**

Create `central_de_arquivos.spec`:
```python
# -*- mode: python ; coding: utf-8 -*-
block_cipher = None

a = Analysis(
    ['renomear.py'],
    pathex=[],
    binaries=[
        ('gs/gswin64c.exe', 'gs'),
        ('gs/gsdll64.dll', 'gs'),
    ],
    datas=[],
    hiddenimports=['pypdf'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='Central de Arquivos',
    debug=False, bootloader_ignore_signals=False, strip=False,
    upx=True, runtime_tmpdir=None, console=False,
)
```

- [ ] **Step 4: Build e teste do .exe**

Run: `python -m PyInstaller --noconfirm "central_de_arquivos.spec"`
Expected: gera `dist/Central de Arquivos.exe`.
Rodar o .exe, abrir "Comprimir PDF" e comprimir um PDF → deve funcionar usando o gs embutido (mesmo sem gs no PATH). Para validar a resolução embutida, confirme que a aba não mostra o aviso "Ghostscript não encontrado".

- [ ] **Step 5: Atualizar README**

Adicionar uma seção no `README.md` listando as novas funções (Excluir Páginas, Juntar PDF, Comprimir PDF, ordenação, arrastar, exportar marcadores) e nota: "A compressão usa Ghostscript (AGPL), embutido no executável."

- [ ] **Step 6: Commit**

```bash
git add central_de_arquivos.spec README.md .gitignore
git commit -m "build: embute Ghostscript no .exe (spec) + README v1.4"
```

---

## Task 17: Verificação final + versão

**Files:**
- Modify: `renomear.py` (subtítulo/versão, se houver) e `dist/release_notes.txt`

- [ ] **Step 1: Rodar a suíte de testes completa**

Run: `python -m pytest tests/ -q`
Expected: todos PASS (compressão inclusa, pois gs está em dev).

- [ ] **Step 2: Checklist de fumaça manual (rodando `python renomear.py`)**

Confirmar, marcando cada um:
- [ ] Ordenação Natural/A-Z/Z-A em Renomear e Excel.
- [ ] Arrastar PDF nas 3 abas novas + Comprimir.
- [ ] Excluir páginas (novo arquivo, sobrescrever+desfazer, arrastado→download).
- [ ] Juntar PDF (ordem, 3 modos de marcador, arrastado→download).
- [ ] Comprimir (3 presets, sobrescrever+desfazer, arrastado→download).
- [ ] Exportar marcadores .txt.
- [ ] Abas antigas continuam funcionando (Renomear, Dividir, Marcadores etc.).

- [ ] **Step 3: Atualizar notas de versão**

Editar `dist/release_notes.txt` (ou criar entrada) descrevendo a v1.4 com a lista de funções.

- [ ] **Step 4: Commit final**

```bash
git add -A
git commit -m "chore: v1.4 — verificacao final e notas de versao"
```

---

## Self-Review (preenchido pelo autor do plano)

**Cobertura do spec:**
- Ordenação A-Z → Task 10 (helpers) + Task 11. ✓
- Arrastar PDF → Task 7 (backend) + Task 8 (rota upload) + Task 10 (makeDrop) + uso nas Tasks 12/13/14. ✓
- Excluir páginas → Tasks 2, 3, 8, 12. ✓
- Juntar/Unificar → Tasks 4, 8, 13. ✓
- Comprimir (gs embutido) → Tasks 6, 8, 14, 16. ✓
- Exportar marcadores .txt → Tasks 5, 8, 15. ✓
- Abas em linha única, ordem confirmada → Task 9. ✓
- Desfazer p/ sobrescrever → Task 8 (tipo "restore"). ✓
- Empacotamento gs → Task 16. ✓
- Testes → Tasks 1-7. ✓

**Consistência de tipos/nomes:** `parse_page_spec`, `pdf_delete_pages(src, spec, out)`, `pdf_merge(items, out, bookmark_mode)`, `outline_to_txt(bookmarks)`, `resolve_ghostscript()`, `pdf_compress(src, out, quality)`, `save_upload(name, data)`, `_backup_for_undo(path)` — usados de forma idêntica nas rotas (Task 8) e nos testes. Rotas: `/api/upload`, `/api/gs-check`, `/api/pdf-delete`, `/api/pdf-merge`, `/api/pdf-compress`, `/api/pdf-outline-txt`. Objetos JS: `DP`, `MG`, `CP`, reuso de `MK`/`RN`/`BT`/`XL`.

**Pontos de atenção para o executor (não são placeholders, são verificações no código existente):**
- Task 11 Step 3: confirmar os nomes reais das funções de render do painel de lote e do Excel antes de inserir a ordenação (o plano assume um `*Render` análogo a `rnRender`).
- Task 9 Step 1: ao substituir as linhas das abas, preservar exatamente os `data-tab` antigos (`pdf`, `divide`, `marks`, `compare`).
