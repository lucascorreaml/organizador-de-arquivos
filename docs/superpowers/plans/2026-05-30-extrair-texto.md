# Extrair Texto (PDF → TXT) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar uma aba "Extrair Texto" que extrai a camada de texto de PDFs digitais (via `pypdf`, sem OCR) e baixa um `.txt`, avisando quando o PDF não tem texto (escaneado).

**Architecture:** Arquivo único `renomear.py` (servidor HTTP stdlib + UI embutida em `HTML_PAGE`). Nova função pura `pdf_extract_text`, uma rota `/api/pdf-extract-text`, e uma aba/painel novos com download via Blob (mesmo padrão de "Exportar lista"/"Exportar marcadores").

**Tech Stack:** Python 3.14, `pypdf` 6.7.3 (já presente), `reportlab` (dev/test only, já instalado), `pytest`, Node `node --check` para a UI.

**Convenções:** funções novas após a seção v1.5 (depois de `pdf_remove_password`), antes de `# Desfazer (pilha generica)`. Rota no `if/elif` de `do_POST`, antes de `/api/undo`. `HTML_PAGE` é raw string `r"""..."""` — em JS use aspas simples SEM escapar (`'x'`, nunca `\'`). Reusar helpers `q`,`api`,`toast`,`esc`,`baseName`,`makeDrop`,`pdf_info`.

---

## Task 1: `pdf_extract_text` (backend + testes TDD)

**Files:** Modify `renomear.py`; Modify `requirements-dev.txt`; Test `tests/test_pdf_tools.py`.

- [ ] **Step 1: Adicionar reportlab às deps de teste**
Acrescentar a linha `reportlab>=4` ao final de `requirements-dev.txt`. (Já está instalado no ambiente.)

- [ ] **Step 2: Escrever os testes que falham**
Append em `tests/test_pdf_tools.py`:
```python
def _text_pdf(path, lines):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(path)
    for ln in lines:
        c.drawString(72, 720, ln)
        c.showPage()
    c.save()
    return path

def test_pdf_extract_text_branco_sem_texto(pdf_factory):
    src = pdf_factory("b.pdf", 3)
    res = renomear.pdf_extract_text(src)
    assert res["ok"] and res["pages"] == 3 and res["has_text"] is False
    assert "--- Página 1 ---" in res["content"]
    assert "--- Página 3 ---" in res["content"]
    assert "(sem texto)" in res["content"]

def test_pdf_extract_text_com_texto(tmp_path):
    src = _text_pdf(str(tmp_path / "t.pdf"), ["Texto de teste", "Segunda pagina"])
    res = renomear.pdf_extract_text(src)
    assert res["has_text"] is True
    assert "Texto de teste" in res["content"]
    assert "--- Página 2 ---" in res["content"]

def test_pdf_extract_text_sem_marcadores(tmp_path):
    src = _text_pdf(str(tmp_path / "t.pdf"), ["Abc"])
    res = renomear.pdf_extract_text(src, page_markers=False)
    assert "--- Página" not in res["content"]
    assert "Abc" in res["content"]

def test_pdf_extract_text_inexistente(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        renomear.pdf_extract_text(str(tmp_path / "nada.pdf"))
```

- [ ] **Step 3: Rodar e ver falhar**
Run: `python -m pytest tests/test_pdf_tools.py -k extract_text -q`
Expected: FAIL (`module 'renomear' has no attribute 'pdf_extract_text'`).

- [ ] **Step 4: Implementar** (em `renomear.py`, após `pdf_remove_password`, antes de `# Desfazer`)
```python
def pdf_extract_text(pdf_path, page_markers=True):
    """Extrai a camada de texto de um PDF digital (sem OCR).

    Retorna {ok, content, pages, has_text}. has_text=False quando nenhuma
    pagina tem texto (provavel PDF escaneado/imagem).
    """
    if not pdf_path or not os.path.isfile(pdf_path):
        raise ValueError("PDF nao encontrado.")
    from pypdf import PdfReader
    reader = PdfReader(pdf_path, strict=False)
    total = len(reader.pages)
    blocks, has_text = [], False
    for i, page in enumerate(reader.pages, 1):
        try:
            txt = (page.extract_text() or "").strip()
        except Exception:
            txt = ""
        if txt:
            has_text = True
        body = txt if txt else "(sem texto)"
        blocks.append(f"--- Página {i} ---\n{body}" if page_markers else body)
    content = ("\n\n".join(blocks) + "\n") if blocks else ""
    return {"ok": True, "content": content, "pages": total, "has_text": has_text}
```

- [ ] **Step 5: Rodar e ver passar**
Run: `python -m pytest tests/test_pdf_tools.py -k extract_text -q`
Expected: PASS (4 testes; o de texto roda de fato pois reportlab está instalado).

- [ ] **Step 6: Commit**
```bash
git add renomear.py tests/test_pdf_tools.py requirements-dev.txt
git commit -m "feat: pdf_extract_text (PDF -> texto, sem OCR)"
```

---

## Task 2: rota `/api/pdf-extract-text`

**Files:** Modify `renomear.py` (`do_POST`).

- [ ] **Step 1: Inserir a rota** antes de `elif self.path == "/api/undo":` (mesma indentação dos `elif` irmãos)
```python
            elif self.path == "/api/pdf-extract-text":
                try:
                    info = pdf_info(data.get("path", ""))
                    base = os.path.splitext(info["name"])[0]
                    res = pdf_extract_text(data.get("path", ""))
                    res["filename"] = base + ".txt"
                    self._send(200, res)
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e)})
```

- [ ] **Step 2: Verificar import + probe**
Run: `python -c "import renomear; print('ok')"` → ok.
Probe inline (gera um PDF de texto e checa o retorno da rota):
```bash
cd "C:/projetos-claude-code/PROJETO RENOMEAR"
python -c "
import threading, time, json, urllib.request, tempfile, os
import renomear
from http.server import ThreadingHTTPServer
from reportlab.pdfgen import canvas
d = tempfile.mkdtemp(); src = os.path.join(d, 't.pdf')
c = canvas.Canvas(src); c.drawString(72,720,'Ola mundo'); c.showPage(); c.save()
port = renomear.find_free_port()
srv = ThreadingHTTPServer(('127.0.0.1', port), renomear.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start(); time.sleep(0.3)
r = urllib.request.urlopen('http://127.0.0.1:%d/api/pdf-extract-text'%port, data=json.dumps({'path':src}).encode()).read().decode()
print('extract-text:', r[:160])
srv.shutdown()
"
```
Expected: JSON com `"ok": true`, `"has_text": true`, `"filename": "t.txt"`, e o texto contendo "Ola mundo".
Run: `python -m pytest tests/ -q` → todos verdes (sem regressão).

- [ ] **Step 3: Commit**
```bash
git add renomear.py
git commit -m "feat: rota /api/pdf-extract-text"
```

---

## Task 3: aba + painel + JS

**Files:** Modify `renomear.py` (`HTML_PAGE`).

- [ ] **Step 1: Inserir a aba** (na barra, imediatamente após o botão `data-tab="pdf"` "Extrair Páginas" e antes de `data-tab="delpages"`)
```html
    <button class="tab" data-tab="text">Extrair Texto</button> <span class="tabsep">|</span>
```
(Preservar os demais `data-tab`.)

- [ ] **Step 2: Inserir o painel** (após o `</section>` do `panel-password`)
```html
  <!-- ===== EXTRAIR TEXTO ===== -->
  <section class="panel" id="panel-text">
    <div class="toolbar">
      <button class="btn-primary" id="txtPick">Escolher PDF</button>
      <span class="pathbox" id="txtPath">Nenhum PDF selecionado</span>
    </div>
    <div class="drop" id="txtDrop">Arraste um PDF aqui</div>
    <p class="hint">Extrai o texto de PDFs digitais (não escaneados). PDFs escaneados precisam de OCR.</p>
    <div class="actionbar">
      <span class="count" id="txtCount">Escolha um PDF.</span>
      <button id="txtGo" disabled class="btn-primary">Extrair texto (.txt)</button>
    </div>
    <div id="txtResult"></div>
  </section>
```

- [ ] **Step 3: Inserir o JS** (imediatamente antes do comentário `//  Desfazer global + Modal`)
```javascript
// ===================================================================
//  EXTRAIR TEXTO
// ===================================================================
const TX={pdf:null,name:""};
function txtUpd(){q("txtGo").disabled=!TX.pdf;}
async function txtLoad(path,name){
  const r=await api("/api/pdf-info",{path});
  if(!r.ok){toast(r.error||"PDF invalido.");return;}
  TX.pdf=path;TX.name=r.name;
  q("txtPath").textContent=r.name+" ("+r.pages+" páginas)";q("txtPath").title=path;
  q("txtCount").textContent="Pronto.";q("txtResult").innerHTML="";txtUpd();
}
q("txtPick").addEventListener("click",async()=>{const r=await api("/api/choose-file",{kind:"file"});if(r.cancelled||!r.path)return;txtLoad(r.path,baseName(r.path));});
makeDrop(q("txtDrop"),(p,n)=>txtLoad(p,n));
q("txtGo").addEventListener("click",async()=>{
  q("txtGo").disabled=true;q("txtGo").textContent="Extraindo…";
  const r=await api("/api/pdf-extract-text",{path:TX.pdf});
  q("txtGo").textContent="Extrair texto (.txt)";txtUpd();
  if(!r.ok){toast(r.error||"Falha ao extrair texto.");return;}
  if(!r.has_text){
    q("txtResult").innerHTML='<p class="hint"><b>Este PDF não tem texto selecionável</b> (provavelmente escaneado). Seria preciso OCR.</p>';
    toast("Sem texto selecionável (PDF escaneado?).");
    return;
  }
  const blob=new Blob([r.content],{type:"text/plain;charset=utf-8"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=r.filename;
  document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(a.href);
  q("txtResult").innerHTML='<p class="hint">✓ Texto extraído ('+r.pages+' páginas).</p>';
  toast("Texto baixado.");
});
txtUpd();
```

- [ ] **Step 4: Verificar**
1. `python -c "import renomear; print('ok')"` → ok.
2. Sintaxe JS: 
```bash
python -c "import re,renomear;m=re.search(r'<script>(.*?)</script>',renomear.HTML_PAGE,re.S);open('_jscheck.js','w',encoding='utf-8').write(m.group(1))" && node --check _jscheck.js && echo "JS OK" && rm -f _jscheck.js
```
3. Presença:
```bash
python -c "import renomear;h=renomear.HTML_PAGE;[print('MISSING',n) for n in ['data-tab=\"text\"','id=\"panel-text\"','const TX=','txtGo'] if n not in h] or print('all present')"
```
4. `python -m pytest tests/ -q` → todos verdes.

- [ ] **Step 5: Commit**
```bash
git add renomear.py
git commit -m "feat(ui): aba Extrair Texto (PDF -> .txt)"
```

---

## Task 4: verificação de interface (jsdom) + README

**Files:** Modify `README.md`.

- [ ] **Step 1: Teste de interface jsdom**
Instalar jsdom numa pasta temporária (node disponível), carregar `HTML_PAGE` com `fetch` stubado (gs-check→{available:true}; choose-file→{path:'C:\\d\\x.pdf'}; pdf-info→{ok:true,name:'x.pdf',pages:3,folder:'C:\\d'}; pdf-extract-text→{ok:true,has_text:true,content:'--- Página 1 ---\nabc',filename:'x.txt',pages:3}; senão {ok:true}) e verificar:
  - sem erros de JS no load;
  - clicar na aba `text` ativa `panel-text`;
  - clicar `txtPick` (escolher PDF) habilita `txtGo`;
  - clicar `txtGo` não lança erro (stub de URL.createObjectURL) e preenche `txtResult` com "Texto extraído".
Esperado: todos PASS, 0 erros de JS. (Limpar a pasta temporária ao fim.)

- [ ] **Step 2: README — adicionar a ferramenta**
Na tabela "Ferramentas de PDF" do `README.md`, adicionar a linha (logo após "Extrair Páginas"):
```markdown
| **Extrair Texto** | Extrai a camada de texto de PDFs **digitais** (não escaneados) e baixa um `.txt`, com marcador de página. Não faz OCR. |
```

- [ ] **Step 3: Commit**
```bash
git add README.md
git commit -m "docs: README com a aba Extrair Texto"
```

---

## Self-Review (autor)

**Cobertura do spec:**
- Aba nova "Extrair Texto" após "Extrair Páginas" → Task 3. ✓
- Backend `pdf_extract_text` + has_text + marcadores de página → Task 1. ✓
- Rota → Task 2. ✓
- Download .txt via Blob; aviso de PDF escaneado (has_text False, não baixa) → Task 3 (JS). ✓
- Funciona com arrastado e escolhido → Task 3 (`makeDrop` + `txtPick`). ✓
- Testes (branco→has_text False + marcadores; texto real via reportlab; sem marcadores; inexistente) → Task 1. ✓
- Sem dependência nova de runtime (reportlab é só teste) → Task 1 (requirements-dev). ✓

**Consistência de nomes:** função `pdf_extract_text(pdf_path, page_markers=True)`; rota `/api/pdf-extract-text` retorna `{ok, content, pages, has_text, filename}`; objeto JS `TX`; ids `txtPick/txtPath/txtDrop/txtCount/txtGo/txtResult`; `data-tab="text"`, `panel-text`. Consistentes entre tasks.

**Sem placeholders:** todo passo tem código/comando completo.
