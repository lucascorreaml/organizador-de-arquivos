# Central de Arquivos v1.5 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar 6 ferramentas ao `renomear.py` (dividir PDF por tamanho/MB, girar+reordenar páginas, imagens→PDF, PDF→imagens, proteger/remover senha) de forma consolidada (3 abas novas + 1 modo novo), mantendo o visual minimalista.

**Architecture:** Arquivo único `renomear.py` = servidor HTTP stdlib (`Handler.do_POST`, rotas `/api/*`) + UI embutida na string `HTML_PAGE`. Funções de PDF/imagem novas vão na seção após as funções da v1.4 (depois de `_backup_for_undo`). PDF via `pypdf`; rasterização (PDF→imagens) via Ghostscript já embutido; imagens→PDF via **Pillow** (nova dependência).

**Tech Stack:** Python 3.14, `pypdf` 6.7.3, `Pillow` 12.1, Ghostscript (`gswin64c.exe` embutido), `pytest`, PyInstaller.

**Convenções:** ASCII em código/comentários (acento só em textos de UI). Padrão de escrita atômica `tmp = out + ".tmp"` + `os.replace`. `import pypdf`/`PIL` local dentro das funções. Reusar helpers existentes: `validate_name`, `suggest_name`, `_unique_name`, `pdf_info`, `resolve_ghostscript`, `_backup_for_undo`, `upload_dir`. CSS/JS reusam classes/painéis da v1.4 (`.drop`, `.mglist-row`, `makeDrop`, `downloadLink`, `q`, `api`, `toast`, `setCanUndo`, `undoLast`).

**Âncoras atuais:**
- Funções novas: após `_backup_for_undo` (fim da seção da v1.4), antes de `# Desfazer (pilha generica)`.
- Rotas: no `if/elif` de `do_POST`, antes de `elif self.path == "/api/undo":`.
- Abas: barra ~linhas 1000-1012 (tail com `merge`,`compress`,`marks`,`compare`).
- Painéis novos: após `panel-compress`.
- JS dos painéis: antes do comentário `//  Desfazer global + Modal`.
- Aba "Dividir PDF" (`divide`): radios `dvMode` (~1564-1568); `dvComputePlan`/`dvRenderPreview`/`dvBmVis` (~2206-2289); handler `dvSplit` (após ~2305, posta em `/api/pdf-split`).
- `save_upload` e `makeDrop`: já existem (v1.4).

---

## Task 1: `pdf_split_by_size` (dividir por tamanho)

**Files:** Modify `renomear.py`; Test `tests/test_pdf_tools.py`.

- [ ] **Step 1: Teste que falha**
```python
def test_split_by_size_limite_grande_uma_parte(pdf_factory, tmp_path):
    src = pdf_factory("s.pdf", 6)
    d = str(tmp_path / "out")
    res = renomear.pdf_split_by_size(src, d, 100)  # 100 MB: cabe tudo
    assert res["ok"] and res["parts"] == 1
    assert res["results"][0]["count"] == 6

def test_split_by_size_limite_minusculo_uma_pagina_por_parte(pdf_factory, tmp_path):
    src = pdf_factory("s.pdf", 4)
    d = str(tmp_path / "out")
    res = renomear.pdf_split_by_size(src, d, 0.00001)  # ~10 bytes: forca 1 pag/parte
    assert res["parts"] == 4
    assert sum(r["count"] for r in res["results"]) == 4
    assert res["oversize"] == [1, 2, 3, 4]

def test_split_by_size_invalido(pdf_factory, tmp_path):
    import pytest
    src = pdf_factory("s.pdf", 2)
    with pytest.raises(ValueError):
        renomear.pdf_split_by_size(src, str(tmp_path / "o"), 0)
```

- [ ] **Step 2: Rodar e ver falhar**
Run: `python -m pytest tests/test_pdf_tools.py -k split_by_size -q` → FAIL (atributo inexistente).

- [ ] **Step 3: Implementar** (após `_backup_for_undo`, iniciando a seção v1.5)
```python
# ----------------------------------------------------------------------------
# v1.5: dividir por tamanho / girar+reordenar / imagens / senha
# ----------------------------------------------------------------------------

def pdf_split_by_size(pdf_path, dest_folder, max_mb, base_name=None):
    """Divide `pdf_path` em partes sequenciais, cada uma <= max_mb (preenchimento guloso)."""
    if not pdf_path or not os.path.isfile(pdf_path):
        raise ValueError("PDF nao encontrado.")
    try:
        max_bytes = int(float(max_mb) * 1024 * 1024)
    except (TypeError, ValueError):
        raise ValueError("Tamanho maximo invalido.")
    if max_bytes <= 0:
        raise ValueError("Tamanho maximo deve ser maior que zero.")
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(pdf_path, strict=False)
    total = len(reader.pages)
    if total == 0:
        raise ValueError("PDF sem paginas.")
    base = (base_name or os.path.splitext(os.path.basename(pdf_path))[0]).strip() or "parte"

    def measure(idxs):
        w = PdfWriter()
        for j in idxs:
            w.add_page(reader.pages[j])
        buf = io.BytesIO()
        w.write(buf)
        return buf.tell()

    parts, current, oversize = [], [], []
    for i in range(total):
        if current and measure(current + [i]) > max_bytes:
            parts.append(current)
            current = [i]
        else:
            current.append(i)
        if len(current) == 1 and measure([i]) > max_bytes:
            oversize.append(i + 1)
    if current:
        parts.append(current)

    os.makedirs(dest_folder, exist_ok=True)
    results, pw = [], max(2, len(str(len(parts))))
    for idx, pages in enumerate(parts, 1):
        writer = PdfWriter()
        for j in pages:
            writer.add_page(reader.pages[j])
        fname = _unique_name(dest_folder, f"{base} (parte {str(idx).zfill(pw)}).pdf")
        full = os.path.join(dest_folder, fname)
        with open(full, "wb") as f:
            writer.write(f)
        results.append({"name": fname, "pages": f"{pages[0] + 1}-{pages[-1] + 1}",
                        "count": len(pages), "size": os.path.getsize(full)})
    return {"ok": True, "results": results, "dest": dest_folder,
            "parts": len(parts), "oversize": sorted(set(oversize))}
```

- [ ] **Step 4: Rodar e ver passar**
Run: `python -m pytest tests/test_pdf_tools.py -k split_by_size -q` → PASS (3).

- [ ] **Step 5: Commit**
```bash
git add renomear.py tests/test_pdf_tools.py
git commit -m "feat: pdf_split_by_size (dividir PDF por tamanho em MB)"
```

---

## Task 2: `pdf_rearrange` (girar + reordenar)

**Files:** Modify `renomear.py`; Test `tests/test_pdf_tools.py`.

- [ ] **Step 1: Teste que falha**
```python
def test_pdf_rearrange_ordem_e_rotacao(pdf_factory, tmp_path):
    src = pdf_factory("s.pdf", 3)
    out = str(tmp_path / "o.pdf")
    ops = [{"src": 2, "rotate": 90}, {"src": 0, "rotate": 180}, {"src": 1, "rotate": 0}]
    res = renomear.pdf_rearrange(src, ops, out)
    from pypdf import PdfReader
    r = PdfReader(out)
    assert res["pages"] == 3 and len(r.pages) == 3
    rots = [int(p.get("/Rotate") or 0) for p in r.pages]
    assert rots == [90, 180, 0]

def test_pdf_rearrange_indice_fora(pdf_factory, tmp_path):
    import pytest
    src = pdf_factory("s.pdf", 2)
    with pytest.raises(ValueError):
        renomear.pdf_rearrange(src, [{"src": 9, "rotate": 0}], str(tmp_path / "o.pdf"))

def test_pdf_rearrange_vazio(pdf_factory, tmp_path):
    import pytest
    src = pdf_factory("s.pdf", 2)
    with pytest.raises(ValueError):
        renomear.pdf_rearrange(src, [], str(tmp_path / "o.pdf"))
```

- [ ] **Step 2: Rodar e ver falhar**
Run: `python -m pytest tests/test_pdf_tools.py -k pdf_rearrange -q` → FAIL.

- [ ] **Step 3: Implementar** (após `pdf_split_by_size`)
```python
def pdf_rearrange(pdf_path, ops, out):
    """Gera `out` com as paginas na ordem/rotacao de `ops`.

    ops: lista de {"src": indice 0-based, "rotate": graus (multiplo de 90)}.
    """
    if not pdf_path or not os.path.isfile(pdf_path):
        raise ValueError("PDF nao encontrado.")
    if not ops:
        raise ValueError("Nenhuma pagina informada.")
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(pdf_path, strict=False)
    total = len(reader.pages)
    writer = PdfWriter()
    for op in ops:
        try:
            src = int(op.get("src"))
        except (TypeError, ValueError):
            raise ValueError("Indice de pagina invalido.")
        if src < 0 or src >= total:
            raise ValueError(f"Pagina fora do intervalo: {src + 1}.")
        try:
            rot = int(op.get("rotate", 0)) % 360
        except (TypeError, ValueError):
            rot = 0
        if rot % 90 != 0:
            raise ValueError("Rotacao deve ser multiplo de 90.")
        page = reader.pages[src]
        if rot:
            page = page.rotate(rot)
        writer.add_page(page)
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        writer.write(f)
    os.replace(tmp, out)
    return {"ok": True, "path": out, "pages": len(writer.pages)}
```

- [ ] **Step 4: Rodar e ver passar**
Run: `python -m pytest tests/test_pdf_tools.py -k pdf_rearrange -q` → PASS (3).

- [ ] **Step 5: Commit**
```bash
git add renomear.py tests/test_pdf_tools.py
git commit -m "feat: pdf_rearrange (girar + reordenar paginas)"
```

---

## Task 3: `images_to_pdf` (imagens → PDF, via Pillow)

**Files:** Modify `renomear.py`, `requirements-dev.txt`; Test `tests/test_pdf_tools.py`.

- [ ] **Step 1: Garantir Pillow**
Run: `python -m pip install pillow` (já presente: 12.1). Adicionar `pillow>=10` a `requirements-dev.txt`.

- [ ] **Step 2: Teste que falha**
```python
def _img(path, w, h, color=(200, 100, 50)):
    from PIL import Image
    Image.new("RGB", (w, h), color).save(path)
    return path

def test_images_to_pdf_fit(tmp_path):
    a = _img(str(tmp_path / "a.png"), 100, 60)
    b = _img(str(tmp_path / "b.png"), 80, 120)
    out = str(tmp_path / "o.pdf")
    res = renomear.images_to_pdf([a, b], out, "fit")
    from pypdf import PdfReader
    r = PdfReader(out)
    assert res["ok"] and res["pages"] == 2 and len(r.pages) == 2
    # pagina 1 ~ 100x60 pontos (tolerancia 2)
    assert abs(float(r.pages[0].mediabox.width) - 100) < 2
    assert abs(float(r.pages[0].mediabox.height) - 60) < 2

def test_images_to_pdf_a4(tmp_path):
    a = _img(str(tmp_path / "a.png"), 100, 60)
    out = str(tmp_path / "o.pdf")
    renomear.images_to_pdf([a], out, "a4")
    from pypdf import PdfReader
    p = PdfReader(out).pages[0]
    assert abs(float(p.mediabox.width) - 595) < 2 and abs(float(p.mediabox.height) - 842) < 2

def test_images_to_pdf_vazio(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        renomear.images_to_pdf([], str(tmp_path / "o.pdf"), "fit")
```

- [ ] **Step 3: Rodar e ver falhar**
Run: `python -m pytest tests/test_pdf_tools.py -k images_to_pdf -q` → FAIL.

- [ ] **Step 4: Implementar** (após `pdf_rearrange`)
```python
def images_to_pdf(image_paths, out, page_mode="fit"):
    """Gera um PDF (1 imagem por pagina). page_mode: 'fit' (pagina = tamanho da
    imagem) ou 'a4' (imagem centralizada numa pagina A4 retrato)."""
    if not image_paths:
        raise ValueError("Nenhuma imagem informada.")
    from PIL import Image
    pages = []
    for p in image_paths:
        if not p or not os.path.isfile(p):
            raise ValueError(f"Imagem nao encontrada: {p}")
        img = Image.open(p).convert("RGB")
        if page_mode == "a4":
            a4 = (595, 842)  # pontos a 72 dpi (retrato)
            canvas = Image.new("RGB", a4, "white")
            iw, ih = img.size
            scale = min(a4[0] / iw, a4[1] / ih)
            nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
            canvas.paste(img.resize((nw, nh)), ((a4[0] - nw) // 2, (a4[1] - nh) // 2))
            pages.append(canvas)
        else:
            pages.append(img)
    tmp = out + ".tmp"
    pages[0].save(tmp, "PDF", resolution=72.0, save_all=True, append_images=pages[1:])
    os.replace(tmp, out)
    return {"ok": True, "path": out, "pages": len(pages)}
```

- [ ] **Step 5: Rodar e ver passar**
Run: `python -m pytest tests/test_pdf_tools.py -k images_to_pdf -q` → PASS (3).

- [ ] **Step 6: Commit**
```bash
git add renomear.py tests/test_pdf_tools.py requirements-dev.txt
git commit -m "feat: images_to_pdf (imagens -> PDF via Pillow)"
```

---

## Task 4: `pdf_to_images` (PDF → imagens, via Ghostscript)

**Files:** Modify `renomear.py`; Test `tests/test_pdf_tools.py`.

- [ ] **Step 1: Teste que falha**
```python
def test_pdf_to_images_gera_arquivos(pdf_factory, tmp_path):
    import pytest
    if not renomear.resolve_ghostscript():
        pytest.skip("Ghostscript nao encontrado.")
    src = pdf_factory("s.pdf", 3)
    d = str(tmp_path / "imgs")
    res = renomear.pdf_to_images(src, d, "png", 96)
    assert res["ok"] and res["count"] == 3
    assert all(f.lower().endswith(".png") for f in res["files"])

def test_pdf_to_images_formato_invalido(pdf_factory, tmp_path):
    import pytest
    if not renomear.resolve_ghostscript():
        pytest.skip("Ghostscript nao encontrado.")
    src = pdf_factory("s.pdf", 1)
    with pytest.raises(ValueError):
        renomear.pdf_to_images(src, str(tmp_path / "x"), "gif", 96)
```

- [ ] **Step 2: Rodar e ver falhar**
Run: `python -m pytest tests/test_pdf_tools.py -k pdf_to_images -q` → FAIL.

- [ ] **Step 3: Implementar** (após `images_to_pdf`)
```python
GS_IMG_DEVICE = {"png": "png16m", "jpg": "jpeg", "jpeg": "jpeg"}


def pdf_to_images(pdf_path, dest_folder, fmt="png", dpi=150):
    """Rasteriza cada pagina do PDF como imagem (PNG/JPG) via Ghostscript."""
    if not pdf_path or not os.path.isfile(pdf_path):
        raise ValueError("PDF nao encontrado.")
    gs = resolve_ghostscript()
    if not gs:
        raise ValueError("Ghostscript nao encontrado. (Necessario para gerar imagens.)")
    fmt = (fmt or "png").lower().lstrip(".")
    device = GS_IMG_DEVICE.get(fmt)
    if not device:
        raise ValueError("Formato deve ser PNG ou JPG.")
    try:
        dpi = max(20, min(600, int(dpi)))
    except (TypeError, ValueError):
        dpi = 150
    ext = "jpg" if device == "jpeg" else "png"
    os.makedirs(dest_folder, exist_ok=True)
    pattern = os.path.join(dest_folder, "pagina-%03d." + ext)
    cmd = [gs, "-sDEVICE=" + device, "-r" + str(dpi), "-dNOPAUSE", "-dBATCH",
           "-dQUIET", "-sOutputFile=" + pattern, pdf_path]
    proc = subprocess.run(cmd, capture_output=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    files = sorted(f for f in os.listdir(dest_folder)
                   if f.lower().startswith("pagina-") and f.lower().endswith("." + ext))
    if proc.returncode != 0 or not files:
        detail = proc.stderr.decode("utf-8", "ignore")[:200] if proc.stderr else ""
        raise ValueError("Falha ao gerar imagens (Ghostscript). " + detail)
    return {"ok": True, "dest": dest_folder, "count": len(files), "files": files}
```

- [ ] **Step 4: Rodar e ver passar**
Run: `python -m pytest tests/test_pdf_tools.py -k pdf_to_images -q` → PASS (2; gs presente em dev).

- [ ] **Step 5: Commit**
```bash
git add renomear.py tests/test_pdf_tools.py
git commit -m "feat: pdf_to_images (PDF -> imagens via Ghostscript)"
```

---

## Task 5: senha — `pdf_set_password` / `pdf_remove_password`

**Files:** Modify `renomear.py`; Test `tests/test_pdf_tools.py`.

- [ ] **Step 1: Teste que falha**
```python
def test_pdf_set_and_remove_password(pdf_factory, tmp_path):
    src = pdf_factory("s.pdf", 2)
    prot = str(tmp_path / "prot.pdf")
    renomear.pdf_set_password(src, prot, "segredo")
    from pypdf import PdfReader
    assert PdfReader(prot).is_encrypted
    out = str(tmp_path / "open.pdf")
    renomear.pdf_remove_password(prot, out, "segredo")
    assert not PdfReader(out).is_encrypted

def test_pdf_remove_password_errada(pdf_factory, tmp_path):
    import pytest
    src = pdf_factory("s.pdf", 1)
    prot = str(tmp_path / "p.pdf")
    renomear.pdf_set_password(src, prot, "abc")
    with pytest.raises(ValueError):
        renomear.pdf_remove_password(prot, str(tmp_path / "o.pdf"), "errada")

def test_pdf_set_password_vazia(pdf_factory, tmp_path):
    import pytest
    src = pdf_factory("s.pdf", 1)
    with pytest.raises(ValueError):
        renomear.pdf_set_password(src, str(tmp_path / "o.pdf"), "")
```

- [ ] **Step 2: Rodar e ver falhar**
Run: `python -m pytest tests/test_pdf_tools.py -k password -q` → FAIL.

- [ ] **Step 3: Implementar** (após `pdf_to_images`)
```python
def pdf_set_password(pdf_path, out, password):
    """Gera `out` criptografado com `password` (senha de abertura)."""
    if not pdf_path or not os.path.isfile(pdf_path):
        raise ValueError("PDF nao encontrado.")
    if not password:
        raise ValueError("Informe uma senha.")
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(pdf_path, strict=False)
    if reader.is_encrypted:
        raise ValueError("Este PDF ja esta protegido.")
    writer = PdfWriter()
    writer.append(reader)
    try:
        writer.encrypt(user_password=password, algorithm="AES-256")
    except Exception:
        writer.encrypt(user_password=password)
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        writer.write(f)
    os.replace(tmp, out)
    return {"ok": True, "path": out}


def pdf_remove_password(pdf_path, out, password):
    """Gera `out` sem senha. Levanta ValueError se a senha estiver incorreta."""
    if not pdf_path or not os.path.isfile(pdf_path):
        raise ValueError("PDF nao encontrado.")
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(pdf_path, strict=False)
    if reader.is_encrypted:
        if not reader.decrypt(password or ""):
            raise ValueError("Senha incorreta.")
    writer = PdfWriter()
    writer.append(reader)
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        writer.write(f)
    os.replace(tmp, out)
    return {"ok": True, "path": out}
```

- [ ] **Step 4: Rodar e ver passar**
Run: `python -m pytest tests/test_pdf_tools.py -k password -q` → PASS (3).

- [ ] **Step 5: Commit**
```bash
git add renomear.py tests/test_pdf_tools.py
git commit -m "feat: pdf_set_password / pdf_remove_password"
```

---

## Task 6: estender `save_upload` para imagens

**Files:** Modify `renomear.py`; Test `tests/test_pdf_tools.py`.

- [ ] **Step 1: Teste que falha**
```python
def test_save_upload_aceita_imagem(tmp_path, monkeypatch):
    monkeypatch.setattr(renomear, "_UPLOAD_DIR", str(tmp_path))
    full = renomear.save_upload("foto.PNG", b"\x89PNG")
    assert full.lower().endswith(".png")
```
(Os testes existentes `test_save_upload_grava_pdf` e `test_save_upload_acrescenta_extensao` devem continuar passando.)

- [ ] **Step 2: Rodar e ver falhar**
Run: `python -m pytest tests/test_pdf_tools.py -k save_upload -q` → o novo de imagem FALHA (hoje vira `.pdf`).

- [ ] **Step 3: Substituir `save_upload`** pela versão que aceita imagens
```python
ALLOWED_UPLOAD_EXTS = {"pdf", "jpg", "jpeg", "png", "bmp", "gif", "webp", "tif", "tiff"}


def save_upload(name, data):
    """Grava bytes de um arquivo arrastado (PDF ou imagem) num temporario."""
    safe = suggest_name(os.path.basename(name or "arquivo.pdf")) or "arquivo"
    ext = os.path.splitext(safe)[1].lower().lstrip(".")
    if ext not in ALLOWED_UPLOAD_EXTS:
        safe = os.path.splitext(safe)[0] + ".pdf"
    udir = upload_dir()
    dest = _unique_name(udir, safe)
    full = os.path.join(udir, dest)
    with open(full, "wb") as f:
        f.write(data)
    return full
```

- [ ] **Step 4: Rodar e ver passar**
Run: `python -m pytest tests/test_pdf_tools.py -k save_upload -q` → PASS (3). Depois a suíte toda: `python -m pytest tests/ -q` (todos verdes).

- [ ] **Step 5: Commit**
```bash
git add renomear.py tests/test_pdf_tools.py
git commit -m "feat: save_upload aceita imagens (alem de PDF)"
```

---

## Task 7: rotas HTTP novas

**Files:** Modify `renomear.py` (`do_POST`).

Ler `do_POST` primeiro. As funções `pdf_info`, `_unique_name`, `_backup_for_undo`, e o tipo undo `restore` já existem. `/api/choose-file` aceita `kind`.

- [ ] **Step 1: Inserir as rotas** antes de `elif self.path == "/api/undo":`
```python
            elif self.path == "/api/pdf-split-size":
                pdf = data.get("pdf", "")
                dest = data.get("dest", "") or (os.path.dirname(pdf) if pdf else "")
                sub = (data.get("subfolder") or "").strip()
                if sub:
                    ok, reason = validate_name(sub)
                    if not ok:
                        self._send(200, {"ok": False, "error": f"Subpasta invalida: {reason}"})
                        return
                    dest = os.path.join(dest, sub)
                try:
                    self._send(200, pdf_split_by_size(pdf, dest, data.get("max_mb", 10),
                                                       data.get("base")))
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e)})

            elif self.path == "/api/pdf-rearrange":
                pdf = data.get("pdf", "")
                overwrite = bool(data.get("overwrite"))
                try:
                    info = pdf_info(pdf)
                    folder, base = info["folder"], os.path.splitext(info["name"])[0]
                    out = pdf if overwrite else os.path.join(
                        folder, _unique_name(folder, base + " (girado).pdf"))
                    backup = _backup_for_undo(pdf) if overwrite else None
                    res = pdf_rearrange(pdf, data.get("ops", []), out)
                    if overwrite and backup:
                        UNDO_STACK.append({"type": "restore", "folder": folder,
                                           "target": pdf, "backup": backup})
                        res["can_undo"] = True
                    self._send(200, res)
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e)})

            elif self.path == "/api/images-to-pdf":
                items = data.get("images", [])
                out = (data.get("out") or "").strip()
                mode = data.get("mode", "fit")
                if not out:
                    self._send(200, {"ok": False, "error": "Caminho de saida nao informado."})
                    return
                if not out.lower().endswith(".pdf"):
                    out += ".pdf"
                try:
                    self._send(200, images_to_pdf(items, out, mode))
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e)})

            elif self.path == "/api/pdf-to-images":
                pdf = data.get("pdf", "")
                dest = data.get("dest", "") or (os.path.dirname(pdf) if pdf else "")
                sub = (data.get("subfolder") or "Imagens").strip()
                if sub:
                    ok, reason = validate_name(sub)
                    if not ok:
                        self._send(200, {"ok": False, "error": f"Subpasta invalida: {reason}"})
                        return
                    dest = os.path.join(dest, sub)
                try:
                    self._send(200, pdf_to_images(pdf, dest, data.get("fmt", "png"),
                                                  data.get("dpi", 150)))
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e)})

            elif self.path == "/api/pdf-password":
                pdf = data.get("pdf", "")
                mode = data.get("mode", "protect")
                password = data.get("password", "")
                overwrite = bool(data.get("overwrite"))
                try:
                    info = pdf_info(pdf)
                    folder, base = info["folder"], os.path.splitext(info["name"])[0]
                    suffix = " (protegido)" if mode == "protect" else " (sem senha)"
                    out = pdf if overwrite else os.path.join(
                        folder, _unique_name(folder, base + suffix + ".pdf"))
                    backup = _backup_for_undo(pdf) if overwrite else None
                    if mode == "remove":
                        res = pdf_remove_password(pdf, out, password)
                    else:
                        res = pdf_set_password(pdf, out, password)
                    if overwrite and backup:
                        UNDO_STACK.append({"type": "restore", "folder": folder,
                                           "target": pdf, "backup": backup})
                        res["can_undo"] = True
                    self._send(200, res)
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e)})
```

- [ ] **Step 2: Verificar import + probe**
Run: `python -c "import renomear; print('ok')"` → ok.
Probe inline (gera PDF temporário e exercita rearrange):
```bash
cd "C:/projetos-claude-code/PROJETO RENOMEAR"
python -c "
import threading, time, json, urllib.request, tempfile, os
import renomear
from http.server import ThreadingHTTPServer
from pypdf import PdfWriter
d = tempfile.mkdtemp(); src = os.path.join(d, 'x.pdf')
w = PdfWriter(); [w.add_blank_page(width=200, height=200) for _ in range(3)]
open(src, 'wb').write(b''); 
with open(src,'wb') as f: w.write(f)
port = renomear.find_free_port()
srv = ThreadingHTTPServer(('127.0.0.1', port), renomear.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
def post(p, b): return urllib.request.urlopen('http://127.0.0.1:%d%s'%(port,p), data=json.dumps(b).encode()).read().decode()
print('rearrange:', post('/api/pdf-rearrange', {'pdf':src,'ops':[{'src':2,'rotate':90},{'src':0,'rotate':0}]}))
srv.shutdown()
"
```
Expected: JSON `{"ok": true, ... "pages": 2}`.
Run também: `python -m pytest tests/ -q` (sem regressões).

- [ ] **Step 3: Commit**
```bash
git add renomear.py
git commit -m "feat: rotas pdf-split-size, pdf-rearrange, images-to-pdf, pdf-to-images, pdf-password"
```

---

## Task 8: abas + painéis (HTML) + modo "size" no Dividir

**Files:** Modify `renomear.py` (`HTML_PAGE`).

- [ ] **Step 1: Inserir 3 abas novas**
Substituir a parte da barra que vai de `Juntar PDF` (`data-tab="merge"`) até `Comprimir PDF` (`data-tab="compress"`) para inserir `pageops` e `imgpdf` entre elas, e `password` depois de `compress`. Ler a barra atual e ajustar para a ordem final:
`… merge | `**`pageops`**` | `**`imgpdf`**` | compress | `**`password`**` | marks | compare`. Concretamente, as novas linhas:
```html
    <button class="tab" data-tab="pageops">Girar e Reordenar</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="imgpdf">Imagens ⇄ PDF</button> <span class="tabsep">|</span>
```
inseridas após o botão `merge` e antes do `compress`; e após o botão `compress`:
```html
    <button class="tab" data-tab="password">Senha</button> <span class="tabsep">|</span>
```
(preservar os `data-tab` existentes `merge`,`compress`,`marks`,`compare`).

- [ ] **Step 2: Adicionar o modo "Por tamanho (MB)" na aba Dividir**
No painel `divide`, dentro do bloco de radios `dvMode` (após o radio `bookmarks`, ~linha 1568), inserir:
```html
          <div class="opts" style="margin-top:8px">
            <label class="opt"><input type="radio" name="dvMode" value="size"> Por tamanho — máx. <input type="number" id="dvMaxMb" value="10" min="1" step="1" style="width:80px"> MB por arquivo</label>
          </div>
```

- [ ] **Step 3: Adicionar os 3 painéis** (após `panel-compress`)
```html
  <!-- ===== GIRAR E REORDENAR ===== -->
  <section class="panel" id="panel-pageops">
    <div class="toolbar">
      <button class="btn-primary" id="poPick">Escolher PDF</button>
      <span class="pathbox" id="poPath">Nenhum PDF selecionado</span>
    </div>
    <div class="drop" id="poDrop">Arraste um PDF aqui</div>
    <p class="hint">Use ↑/↓ para reordenar e ↻ para girar cada página (90°).</p>
    <div id="poList"></div>
    <div class="box">
      <div class="opts">
        <label class="opt"><input type="radio" name="poMode" value="new" checked> Salvar como novo arquivo</label>
        <label class="opt"><input type="radio" name="poMode" value="over" id="poOver"> Sobrescrever o original</label>
      </div>
    </div>
    <div class="actionbar">
      <span class="count" id="poCount">Escolha um PDF.</span>
      <button id="poUndo" disabled>Desfazer</button>
      <button id="poGo" disabled class="btn-primary">Aplicar</button>
    </div>
    <div id="poResult"></div>
  </section>

  <!-- ===== IMAGENS <-> PDF ===== -->
  <section class="panel" id="panel-imgpdf">
    <div class="box">
      <div class="opts">
        <label class="opt"><input type="radio" name="ipDir" value="i2p" checked> Imagens → PDF</label>
        <label class="opt"><input type="radio" name="ipDir" value="p2i"> PDF → Imagens</label>
      </div>
    </div>

    <div id="ipI2P">
      <div class="toolbar">
        <button class="btn-primary" id="ipAddImg">Adicionar imagem(ns)</button>
        <button id="ipClear" disabled>Limpar lista</button>
      </div>
      <div class="drop" id="ipImgDrop">Arraste imagens aqui (JPG, PNG…)</div>
      <div id="ipImgList"></div>
      <div class="box">
        <div class="field">
          <span class="lbl">Tamanho da página</span>
          <div class="opts">
            <label class="opt"><input type="radio" name="ipPage" value="fit" checked> Ajustar à imagem</label>
            <label class="opt"><input type="radio" name="ipPage" value="a4"> A4 retrato</label>
          </div>
        </div>
        <div class="field">
          <span class="lbl">Nome do PDF final</span>
          <input type="text" id="ipOutName" value="Imagens.pdf" style="width:60%">
        </div>
      </div>
      <div class="actionbar">
        <span class="count" id="ipI2PCount">Adicione ao menos 1 imagem.</span>
        <button id="ipI2PGo" disabled class="btn-primary">Gerar PDF</button>
      </div>
      <div id="ipI2PResult"></div>
    </div>

    <div id="ipP2I" style="display:none">
      <div class="toolbar">
        <button class="btn-primary" id="ipPdfPick">Escolher PDF</button>
        <span class="pathbox" id="ipPdfPath">Nenhum PDF selecionado</span>
      </div>
      <div class="drop" id="ipPdfDrop">Arraste um PDF aqui</div>
      <div class="box">
        <div class="opts">
          <label class="opt">Formato
            <select id="ipFmt"><option value="png">PNG</option><option value="jpg">JPG</option></select>
          </label>
          <label class="opt">Resolução
            <select id="ipDpi"><option value="96">Tela (96)</option><option value="150" selected>Boa (150)</option><option value="300">Alta (300)</option></select>
          </label>
        </div>
      </div>
      <div class="actionbar">
        <span class="count" id="ipP2ICount">Escolha um PDF.</span>
        <button id="ipP2IGo" disabled class="btn-primary">Gerar imagens</button>
      </div>
      <div id="ipP2IResult"></div>
    </div>
  </section>

  <!-- ===== SENHA ===== -->
  <section class="panel" id="panel-password">
    <div class="toolbar">
      <button class="btn-primary" id="pwPick">Escolher PDF</button>
      <span class="pathbox" id="pwPath">Nenhum PDF selecionado</span>
    </div>
    <div class="drop" id="pwDrop">Arraste um PDF aqui</div>
    <div class="box">
      <div class="opts">
        <label class="opt"><input type="radio" name="pwMode" value="protect" checked> Proteger (definir senha)</label>
        <label class="opt"><input type="radio" name="pwMode" value="remove"> Remover senha</label>
      </div>
      <div class="field">
        <span class="lbl" id="pwLbl">Senha</span>
        <input type="password" id="pwPass" placeholder="senha" style="width:50%">
      </div>
      <div class="opts">
        <label class="opt"><input type="radio" name="pwSave" value="new" checked> Salvar como novo arquivo</label>
        <label class="opt"><input type="radio" name="pwSave" value="over" id="pwOver"> Sobrescrever o original</label>
      </div>
    </div>
    <div class="actionbar">
      <span class="count" id="pwCount">Escolha um PDF.</span>
      <button id="pwUndo" disabled>Desfazer</button>
      <button id="pwGo" disabled class="btn-primary">Aplicar</button>
    </div>
    <div id="pwResult"></div>
  </section>
```

- [ ] **Step 4: Verificar**
Run: `python -c "import renomear; print('ok')"` → ok.
```bash
python -c "
import renomear; h=renomear.HTML_PAGE
for n in ['data-tab=\"pageops\"','data-tab=\"imgpdf\"','data-tab=\"password\"','id=\"panel-pageops\"','id=\"panel-imgpdf\"','id=\"panel-password\"','value=\"size\"','dvMaxMb','poList','ipImgList','pwPass']:
    assert n in h, 'MISSING '+n
print('ok abas/paineis')
"
```

- [ ] **Step 5: Commit**
```bash
git add renomear.py
git commit -m "feat(ui): abas Girar/Imagens/Senha + modo dividir por tamanho"
```

---

## Task 9: helper `makeDrop` com extensões + drop de imagens

**Files:** Modify `renomear.py` (`HTML_PAGE`, JS helpers).

- [ ] **Step 1: Estender `makeDrop`** para aceitar um filtro de extensões (mantendo compatível com chamadas existentes que só passam `(el, onPdf)`).
Localizar a função `makeDrop` e substituí-la por:
```javascript
function makeDrop(el,onFile,accept){
  if(!el) return;
  const re=accept||/\.pdf$/i;
  el.addEventListener("dragover",e=>{e.preventDefault();el.classList.add("dragover");});
  el.addEventListener("dragleave",()=>el.classList.remove("dragover"));
  el.addEventListener("drop",async e=>{
    e.preventDefault();el.classList.remove("dragover");
    const files=[...(e.dataTransfer.files||[])].filter(f=>re.test(f.name));
    if(!files.length){toast("Solte um arquivo válido aqui.");return;}
    for(const f of files){const r=await uploadFile(f);if(r.ok)onFile(r.path,r.name,true);else toast(r.error||"Falha no upload.");}
  });
}
```

- [ ] **Step 2: Verificar**
Run: `python -c "import renomear; print('ok')"` → ok. Extrair JS e checar sintaxe:
```bash
python -c "import re,renomear;m=re.search(r'<script>(.*?)</script>',renomear.HTML_PAGE,re.S);open('_jscheck.js','w',encoding='utf-8').write(m.group(1))" && node --check _jscheck.js && echo "JS OK" && rm -f _jscheck.js
```
Run: `python -m pytest tests/ -q` → verdes.

- [ ] **Step 3: Commit**
```bash
git add renomear.py
git commit -m "feat(ui): makeDrop com filtro de extensoes (suporte a imagens)"
```

---

## Task 10: ligar "Dividir por tamanho" no painel Dividir

**Files:** Modify `renomear.py` (`HTML_PAGE`, JS do painel `divide`).

Ler as funções `dvComputePlan`, `dvRenderPreview`, `dvBmVis` e o handler de clique `dvSplit` (posta em `/api/pdf-split`).

- [ ] **Step 1: Mostrar o campo MB e habilitar o botão no modo size**
Na função `dvRenderPreview`, logo no início (após pegar `wrap` e checar `DV.pdf`), tratar o modo `size` antes do cálculo normal de plano:
```javascript
  if(dvMode()==="size"){
    wrap.innerHTML='<div class="hint">As partes serão calculadas no servidor para ficar abaixo do limite de MB.</div>';
    q("dvCount").textContent="Dividir por tamanho";
    q("dvSplit").disabled=false;
    return;
  }
```
(Inserir esse bloco imediatamente após o `if(!DV.pdf){...return;}` do `dvRenderPreview`.)

- [ ] **Step 2: Garantir que mudar o MB/radio re-renderiza**
Encontrar onde os radios `dvMode` e inputs (`dvEvery`,`dvParts`) disparam `dvRenderPreview` (provável `addEventListener('change'/'input', dvRenderPreview)` ou via `dvBmVis`). Adicionar listener para `#dvMaxMb` e para o novo radio: garantir que `dvRenderPreview` rode ao trocar para `size` ou alterar `dvMaxMb`. Se houver um listener genérico nos radios `input[name=dvMode]`, ele já cobre o radio; adicionar:
```javascript
q("dvMaxMb").addEventListener("input",dvRenderPreview);
```
junto aos outros listeners do painel divide.

- [ ] **Step 3: Branch no handler de dividir**
No handler de clique do `dvSplit` (a função async que hoje monta `ranges` e chama `/api/pdf-split`), adicionar no começo um atalho para o modo size. Ler o handler e inserir, logo após desabilitar o botão:
```javascript
  if(dvMode()==="size"){
    const sub=(q("dvSubChk").checked?(q("dvSubName").value||"Dividido").trim():"");
    const r=await api("/api/pdf-split-size",{pdf:DV.pdf,dest:DV.dest,subfolder:sub,
      max_mb:parseFloat(q("dvMaxMb").value||"10"),base:(q("dvBase").value||DV.name||"").trim()});
    q("dvSplit").disabled=false;
    if(!r.ok){toast(r.error||"Falha ao dividir.");return;}
    let msg="✓ "+r.parts+" arquivo(s) gerado(s).";
    if(r.oversize&&r.oversize.length)msg+=" (Páginas acima do limite: "+r.oversize.join(", ")+".)";
    toast(msg);
    return;
  }
```
> Use os ids reais de subpasta do painel divide (no código atual: `dvSubChk` e `dvSubName`). Verifique e ajuste se diferirem.

- [ ] **Step 4: Verificar**
Run: `python -c "import renomear; print('ok')"`; extrair JS + `node --check` (como na Task 9); `python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**
```bash
git add renomear.py
git commit -m "feat(ui): dividir por tamanho (MB) no painel Dividir PDF"
```

---

## Task 11: JS do painel "Girar e Reordenar"

**Files:** Modify `renomear.py` (`HTML_PAGE`, JS antes de `//  Desfazer global + Modal`).

- [ ] **Step 1: Inserir bloco**
```javascript
// ===================================================================
//  GIRAR E REORDENAR
// ===================================================================
const PO={pdf:null,name:"",fromUpload:false,items:[]}; // items: {src, rotate}
function poUpd(){
  q("poGo").disabled=!(PO.pdf&&PO.items.length);
  const ov=q("poOver");if(ov)ov.disabled=PO.fromUpload;
  q("poUndo").disabled=!canUndo;
}
function poRender(){
  const box=q("poList");
  if(!PO.items.length){box.innerHTML='<div class="empty">Nenhuma página.</div>';poUpd();return;}
  box.innerHTML=PO.items.map((it,i)=>(
    '<div class="mglist-row">'
    +'<span class="nm">Página '+(it.src+1)+' <span class="pg">(giro: '+it.rotate+'°)</span></span>'
    +'<button class="btn-link" data-up="'+i+'" '+(i===0?'disabled':'')+'>↑</button>'
    +'<button class="btn-link" data-down="'+i+'" '+(i===PO.items.length-1?'disabled':'')+'>↓</button>'
    +'<button class="btn-link" data-rot="'+i+'">↻ girar</button>'
    +'</div>')).join("");
  box.querySelectorAll("[data-up]").forEach(b=>b.addEventListener("click",()=>{const i=+b.dataset.up;[PO.items[i-1],PO.items[i]]=[PO.items[i],PO.items[i-1]];poRender();}));
  box.querySelectorAll("[data-down]").forEach(b=>b.addEventListener("click",()=>{const i=+b.dataset.down;[PO.items[i+1],PO.items[i]]=[PO.items[i],PO.items[i+1]];poRender();}));
  box.querySelectorAll("[data-rot]").forEach(b=>b.addEventListener("click",()=>{const i=+b.dataset.rot;PO.items[i].rotate=(PO.items[i].rotate+90)%360;poRender();}));
  poUpd();
}
async function poLoad(path,name,fromUpload){
  const r=await api("/api/pdf-info",{path});
  if(!r.ok){toast(r.error||"PDF invalido.");return;}
  PO.pdf=path;PO.name=r.name;PO.fromUpload=!!fromUpload;
  PO.items=Array.from({length:r.pages},(_,i)=>({src:i,rotate:0}));
  q("poPath").textContent=r.name+" ("+r.pages+" páginas)";q("poPath").title=path;
  q("poCount").textContent="Pronto.";
  if(fromUpload){const m=document.querySelector('input[name=poMode][value=new]');if(m)m.checked=true;}
  poRender();
}
q("poPick").addEventListener("click",async()=>{const r=await api("/api/choose-file",{kind:"file"});if(r.cancelled||!r.path)return;poLoad(r.path,baseName(r.path),false);});
makeDrop(q("poDrop"),(p,n)=>poLoad(p,n,true));
q("poUndo").addEventListener("click",undoLast);
q("poGo").addEventListener("click",async()=>{
  const overwrite=(document.querySelector('input[name=poMode]:checked')||{}).value==="over" && !PO.fromUpload;
  q("poGo").disabled=true;q("poGo").textContent="Aplicando…";
  const r=await api("/api/pdf-rearrange",{pdf:PO.pdf,ops:PO.items,overwrite});
  q("poGo").textContent="Aplicar";
  if(!r.ok){toast(r.error||"Falha.");poUpd();return;}
  setCanUndo(!!r.can_undo);
  q("poResult").innerHTML='<p class="hint">✓ PDF gerado ('+r.pages+' páginas). '
    +(PO.fromUpload?downloadLink(r.path,"baixar resultado"):"Salvo em: "+esc(baseName(r.path)))+'</p>';
  poUpd();
});
```

- [ ] **Step 2: Verificar** — import + `node --check` (extrair JS) + `pytest`.

- [ ] **Step 3: Commit**
```bash
git add renomear.py
git commit -m "feat(ui): painel Girar e Reordenar"
```

---

## Task 12: JS do painel "Imagens ⇄ PDF"

**Files:** Modify `renomear.py` (`HTML_PAGE`, JS).

- [ ] **Step 1: Inserir bloco** (após o de Girar e Reordenar)
```javascript
// ===================================================================
//  IMAGENS <-> PDF
// ===================================================================
const IMGRE=/\.(jpe?g|png|bmp|gif|webp|tiff?)$/i;
// direcao
document.querySelectorAll('input[name=ipDir]').forEach(r=>r.addEventListener("change",()=>{
  const d=(document.querySelector('input[name=ipDir]:checked')||{}).value||"i2p";
  q("ipI2P").style.display=d==="i2p"?"block":"none";
  q("ipP2I").style.display=d==="p2i"?"block":"none";
}));
// ---- Imagens -> PDF ----
const IP={imgs:[]}; // {path,name}
function ipRender(){
  const box=q("ipImgList");
  if(!IP.imgs.length){box.innerHTML='<div class="empty">Nenhuma imagem.</div>';ipUpd();return;}
  box.innerHTML=IP.imgs.map((it,i)=>(
    '<div class="mglist-row"><span class="pg">#'+(i+1)+'</span>'
    +'<span class="nm">'+esc(it.name)+'</span>'
    +'<button class="btn-link" data-up="'+i+'" '+(i===0?'disabled':'')+'>↑</button>'
    +'<button class="btn-link" data-down="'+i+'" '+(i===IP.imgs.length-1?'disabled':'')+'>↓</button>'
    +'<button class="btn-link" data-del="'+i+'">remover</button></div>')).join("");
  box.querySelectorAll("[data-up]").forEach(b=>b.addEventListener("click",()=>{const i=+b.dataset.up;[IP.imgs[i-1],IP.imgs[i]]=[IP.imgs[i],IP.imgs[i-1]];ipRender();}));
  box.querySelectorAll("[data-down]").forEach(b=>b.addEventListener("click",()=>{const i=+b.dataset.down;[IP.imgs[i+1],IP.imgs[i]]=[IP.imgs[i],IP.imgs[i+1]];ipRender();}));
  box.querySelectorAll("[data-del]").forEach(b=>b.addEventListener("click",()=>{IP.imgs.splice(+b.dataset.del,1);ipRender();}));
  ipUpd();
}
function ipUpd(){
  q("ipI2PGo").disabled=!IP.imgs.length;
  q("ipClear").disabled=!IP.imgs.length;
  q("ipI2PCount").textContent=IP.imgs.length?(IP.imgs.length+" imagem(ns)."):"Adicione ao menos 1 imagem.";
}
function ipAdd(path,name){IP.imgs.push({path,name:name||baseName(path)});ipRender();}
q("ipAddImg").addEventListener("click",async()=>{const r=await api("/api/choose-file",{kind:"image"});if(r.cancelled||!r.path)return;ipAdd(r.path,baseName(r.path));});
makeDrop(q("ipImgDrop"),(p,n)=>ipAdd(p,n),IMGRE);
q("ipClear").addEventListener("click",()=>{IP.imgs=[];ipRender();});
q("ipI2PGo").addEventListener("click",async()=>{
  const mode=(document.querySelector('input[name=ipPage]:checked')||{}).value||"fit";
  let name=(q("ipOutName").value||"Imagens.pdf").trim();
  const firstReal=IP.imgs.find(it=>!/renomear_up_/.test(it.path));
  const baseItem=firstReal||IP.imgs[0];
  const folder=baseItem?baseItem.path.replace(/[\\/][^\\/]+$/,""):"";
  const out=(folder?folder+"\\":"")+name;
  q("ipI2PGo").disabled=true;q("ipI2PGo").textContent="Gerando…";
  const r=await api("/api/images-to-pdf",{images:IP.imgs.map(it=>it.path),out,mode});
  q("ipI2PGo").textContent="Gerar PDF";ipUpd();
  if(!r.ok){toast(r.error||"Falha ao gerar PDF.");return;}
  q("ipI2PResult").innerHTML='<p class="hint">✓ PDF com '+r.pages+' página(s). '
    +(firstReal?"Salvo em: "+esc(baseName(r.path)):downloadLink(r.path,"baixar PDF"))+'</p>';
});
ipRender();
// ---- PDF -> Imagens ----
const PI={pdf:null,name:"",fromUpload:false};
function piUpd(){q("ipP2IGo").disabled=!PI.pdf;}
async function piLoad(path,name,fromUpload){
  const r=await api("/api/pdf-info",{path});
  if(!r.ok){toast(r.error||"PDF invalido.");return;}
  PI.pdf=path;PI.name=r.name;PI.fromUpload=!!fromUpload;
  q("ipPdfPath").textContent=r.name+" ("+r.pages+" páginas)";q("ipPdfPath").title=path;
  q("ipP2ICount").textContent="Pronto.";piUpd();
}
q("ipPdfPick").addEventListener("click",async()=>{const r=await api("/api/choose-file",{kind:"file"});if(r.cancelled||!r.path)return;piLoad(r.path,baseName(r.path),false);});
makeDrop(q("ipPdfDrop"),(p,n)=>piLoad(p,n,true));
q("ipP2IGo").addEventListener("click",async()=>{
  const fmt=q("ipFmt").value||"png",dpi=parseInt(q("ipDpi").value||"150",10);
  q("ipP2IGo").disabled=true;q("ipP2IGo").textContent="Gerando…";
  const dest=PI.fromUpload?"":PI.pdf.replace(/[\\/][^\\/]+$/,"");
  const r=await api("/api/pdf-to-images",{pdf:PI.pdf,dest,subfolder:"Imagens",fmt,dpi});
  q("ipP2IGo").textContent="Gerar imagens";piUpd();
  if(!r.ok){toast(r.error||"Falha ao gerar imagens.");return;}
  q("ipP2IResult").innerHTML='<p class="hint">✓ '+r.count+' imagem(ns) gerada(s) na subpasta "Imagens".</p>';
});
piUpd();
```
> Nota: para PDF→Imagens a partir de um PDF arrastado (sem pasta real), o `dest` fica vazio e as imagens caem na pasta temporária de upload — o resultado avisa o número gerado; aceitável (o foco do P→I é PDF escolhido pelo botão, que tem pasta real).

- [ ] **Step 2: Verificar** — import + `node --check` + `pytest`.

- [ ] **Step 3: Commit**
```bash
git add renomear.py
git commit -m "feat(ui): painel Imagens <-> PDF"
```

---

## Task 13: JS do painel "Senha"

**Files:** Modify `renomear.py` (`HTML_PAGE`, JS).

- [ ] **Step 1: Inserir bloco** (após o de Imagens ⇄ PDF)
```javascript
// ===================================================================
//  SENHA
// ===================================================================
const PW={pdf:null,name:"",fromUpload:false};
function pwUpd(){
  q("pwGo").disabled=!(PW.pdf&&q("pwPass").value);
  const ov=q("pwOver");if(ov)ov.disabled=PW.fromUpload;
  q("pwUndo").disabled=!canUndo;
  const mode=(document.querySelector('input[name=pwMode]:checked')||{}).value||"protect";
  q("pwLbl").textContent=mode==="remove"?"Senha atual":"Nova senha";
}
async function pwLoad(path,name,fromUpload){
  const r=await api("/api/pdf-info",{path});
  if(!r.ok){toast(r.error||"PDF invalido.");return;}
  PW.pdf=path;PW.name=r.name;PW.fromUpload=!!fromUpload;
  q("pwPath").textContent=r.name;q("pwPath").title=path;q("pwCount").textContent="Pronto.";
  if(fromUpload){const m=document.querySelector('input[name=pwSave][value=new]');if(m)m.checked=true;}
  pwUpd();
}
q("pwPick").addEventListener("click",async()=>{const r=await api("/api/choose-file",{kind:"file"});if(r.cancelled||!r.path)return;pwLoad(r.path,baseName(r.path),false);});
makeDrop(q("pwDrop"),(p,n)=>pwLoad(p,n,true));
q("pwPass").addEventListener("input",pwUpd);
document.querySelectorAll('input[name=pwMode]').forEach(r=>r.addEventListener("change",pwUpd));
q("pwUndo").addEventListener("click",undoLast);
q("pwGo").addEventListener("click",async()=>{
  const mode=(document.querySelector('input[name=pwMode]:checked')||{}).value||"protect";
  const overwrite=(document.querySelector('input[name=pwSave]:checked')||{}).value==="over" && !PW.fromUpload;
  q("pwGo").disabled=true;q("pwGo").textContent="Aplicando…";
  const r=await api("/api/pdf-password",{pdf:PW.pdf,mode,password:q("pwPass").value,overwrite});
  q("pwGo").textContent="Aplicar";
  if(!r.ok){toast(r.error||"Falha.");pwUpd();return;}
  setCanUndo(!!r.can_undo);
  q("pwResult").innerHTML='<p class="hint">✓ '+(mode==="remove"?"Senha removida.":"PDF protegido.")+' '
    +(PW.fromUpload?downloadLink(r.path,"baixar resultado"):"Salvo em: "+esc(baseName(r.path)))+'</p>';
  pwUpd();
});
pwUpd();
```

- [ ] **Step 2: Verificar** — import + `node --check` + `pytest`.

- [ ] **Step 3: Commit**
```bash
git add renomear.py
git commit -m "feat(ui): painel Senha (proteger / remover)"
```

---

## Task 14: seletor nativo aceitar imagens

**Files:** Modify `renomear.py` (`pick_path_tk`).

Hoje `pick_path_tk(kind, initialdir)` trata `kind` em `("file","anyfile")` e `folder`. O painel de imagens chama `/api/choose-file` com `kind:"image"`.

- [ ] **Step 1: Adicionar o filtro de imagens**
Em `pick_path_tk`, no ramo de arquivos, tratar `kind == "image"`:
```python
        if kind == "image":
            ft = [("Imagens", "*.jpg *.jpeg *.png *.bmp *.gif *.webp *.tif *.tiff"),
                  ("Todos os arquivos", "*.*")]
            ttl = "Escolha a(s) imagem(ns)"
        elif kind == "anyfile":
            ft = [("Todos os arquivos", "*.*"), ("PDF", "*.pdf")]
            ttl = "Escolha o arquivo"
        else:
            ft = [("PDF", "*.pdf"), ("Todos os arquivos", "*.*")]
            ttl = "Escolha o arquivo PDF"
        path = filedialog.askopenfilename(title=ttl, parent=root, filetypes=ft, initialdir=ini)
```
(Ajustar o `if/else` existente para incluir o ramo `image`; manter `folder` como está.) A rota `/api/choose-file` já repassa `kind` para `choose_path_dialog`/`pick_path_tk` — verifique que `choose_path_dialog` aceita `kind="image"` (ele só repassa a string; OK).

- [ ] **Step 2: Verificar** — `python -c "import renomear; print('ok')"`; `python -m pytest tests/ -q`.

- [ ] **Step 3: Commit**
```bash
git add renomear.py
git commit -m "feat: seletor nativo com filtro de imagens (kind=image)"
```

---

## Task 15: empacotamento (Pillow) + README + verificação final

**Files:** Modify `central_de_arquivos.spec`, `README.md`; rebuild exe.

- [ ] **Step 1: Garantir Pillow no bundle**
Editar `central_de_arquivos.spec`: trocar `hiddenimports=['pypdf']` por `hiddenimports=['pypdf', 'PIL']`.

- [ ] **Step 2: Rebuild (best-effort)**
Run: `python -m PyInstaller --noconfirm "central_de_arquivos.spec"` (até ~10 min). Esperado: `dist/Central de Arquivos.exe`. Se falhar, registrar o erro e seguir (config é o entregável durável). NÃO abrir o exe GUI.

- [ ] **Step 3: README — seção v1.5**
Adicionar ao `README.md`:
```markdown
## 🆕 Novidades v1.5

- **Dividir por tamanho (MB)** — novo modo na aba "Dividir PDF": gera partes abaixo de um limite de MB (útil p/ limites de upload).
- **Girar e Reordenar** — gira páginas (90°) e muda a ordem das páginas de um PDF.
- **Imagens ⇄ PDF** — junta imagens (JPG/PNG…) num PDF (ajustar à imagem ou A4) e exporta páginas de PDF como imagens (PNG/JPG, 96/150/300 dpi).
- **Senha** — protege com senha ou remove a senha de um PDF.
```

- [ ] **Step 4: Teste de interface (jsdom) + suíte**
Run a suíte: `python -m pytest tests/ -q` (todos verdes).
Teste de interface com jsdom (instalar em pasta temporária se necessário; node disponível). Script `test_ui.js` que carrega `HTML_PAGE`, stuba `fetch` (gs-check→{available:true}; choose-file→{path:'C:\\d\\x.pdf'}; pdf-info→{ok:true,name:'x.pdf',pages:4,folder:'C:\\d'}; senão {ok:true}) e verifica:
  - sem erros de JS no load;
  - trocar para abas `pageops`, `imgpdf`, `password` ativa os painéis;
  - radio `size` na aba `divide` mostra o campo `dvMaxMb` e habilita `dvSplit`;
  - em `pageops`, após escolher PDF, a lista mostra 4 páginas e `poGo` habilita;
  - em `imgpdf`, alternar direção mostra/oculta `ipI2P`/`ipP2I`;
  - em `password`, digitar senha habilita `pwGo` e o label muda com o modo.
Esperado: todos PASS, 0 erros de JS.

- [ ] **Step 5: Commit**
```bash
git add central_de_arquivos.spec README.md
git commit -m "build: Pillow no bundle + README v1.5"
```

---

## Self-Review (autor do plano)

**Cobertura do spec:**
- Dividir por MB → Task 1 (func) + Task 8 (radio) + Task 10 (wiring) + Task 7 (rota). ✓
- Girar + reordenar → Task 2 + Task 8 + Task 11 + Task 7. ✓
- Imagens→PDF → Task 3 + Task 8 + Task 12 + Task 7 + Task 14 (seletor) + Task 6/9 (upload/drop imagens). ✓
- PDF→Imagens → Task 4 + Task 8 + Task 12 + Task 7. ✓
- Senha → Task 5 + Task 8 + Task 13 + Task 7. ✓
- Pillow + empacotamento → Task 3 + Task 15. ✓
- Layout consolidado (3 abas + modo) → Task 8. ✓
- Desfazer (restore) p/ sobrescrever → Task 7 (rearrange/password). ✓

**Consistência de nomes:** `pdf_split_by_size`, `pdf_rearrange(ops)`, `images_to_pdf(images,out,mode)`, `pdf_to_images(pdf,dest,fmt,dpi)`, `pdf_set_password`/`pdf_remove_password`, `save_upload` (estendida). Rotas: `/api/pdf-split-size`, `/api/pdf-rearrange`, `/api/images-to-pdf`, `/api/pdf-to-images`, `/api/pdf-password`. Objetos JS: `PO`, `IP`/`PI`, `PW`. data-tab: `pageops`,`imgpdf`,`password`.

**Pontos de atenção p/ o executor:**
- Task 10: confirmar ids reais de subpasta no painel `divide` (`dvSubChk`/`dvSubName`) e o nome real da função do handler de `dvSplit` antes de inserir o branch.
- Task 8: ao reescrever a barra de abas, preservar `data-tab` existentes (`merge`,`compress`,`marks`,`compare`).
- Task 9: `makeDrop` ganha 3º parâmetro opcional — chamadas existentes (v1.4) continuam válidas (default = só PDF).
