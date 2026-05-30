import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import renomear  # noqa: E402


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


def test_save_upload_grava_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(renomear, "_UPLOAD_DIR", str(tmp_path))
    full = renomear.save_upload("Documento.pdf", b"%PDF-1.4 fake")
    assert os.path.isfile(full)
    assert full.lower().endswith(".pdf")
    with open(full, "rb") as f:
        assert f.read() == b"%PDF-1.4 fake"

def test_save_upload_acrescenta_extensao(tmp_path, monkeypatch):
    monkeypatch.setattr(renomear, "_UPLOAD_DIR", str(tmp_path))
    full = renomear.save_upload("semext", b"x")
    assert full.lower().endswith(".pdf")


def test_pdf_merge_agrupa_por_pasta(pdf_factory, tmp_path):
    a = pdf_factory("a.pdf", 1)
    b = pdf_factory("b.pdf", 1)
    out = str(tmp_path / "g.pdf")
    items = [{"path": a, "title": "A", "group": "Pasta X"},
             {"path": b, "title": "B", "group": "Pasta X"}]
    renomear.pdf_merge(items, out, "folder")
    # 1 marcador-pai (grupo) + 2 filhos = 3 itens no total
    assert _count_outline(out) == 3

def test_backup_for_undo_copia_e_restaura(pdf_factory, tmp_path):
    src = pdf_factory("orig.pdf", 2)
    bak = renomear._backup_for_undo(src)
    try:
        assert os.path.isfile(bak)
        assert os.path.getsize(bak) == os.path.getsize(src)
    finally:
        if os.path.isfile(bak):
            os.remove(bak)


def test_split_by_size_limite_grande_uma_parte(pdf_factory, tmp_path):
    src = pdf_factory("s.pdf", 6)
    d = str(tmp_path / "out")
    res = renomear.pdf_split_by_size(src, d, 100)
    assert res["ok"] and res["parts"] == 1
    assert res["results"][0]["count"] == 6

def test_split_by_size_limite_minusculo_uma_pagina_por_parte(pdf_factory, tmp_path):
    src = pdf_factory("s.pdf", 4)
    d = str(tmp_path / "out")
    res = renomear.pdf_split_by_size(src, d, 0.00001)
    assert res["parts"] == 4
    assert sum(r["count"] for r in res["results"]) == 4
    assert res["oversize"] == [1, 2, 3, 4]

def test_split_by_size_invalido(pdf_factory, tmp_path):
    import pytest
    src = pdf_factory("s.pdf", 2)
    with pytest.raises(ValueError):
        renomear.pdf_split_by_size(src, str(tmp_path / "o"), 0)


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


def test_save_upload_aceita_imagem(tmp_path, monkeypatch):
    monkeypatch.setattr(renomear, "_UPLOAD_DIR", str(tmp_path))
    full = renomear.save_upload("foto.PNG", b"\x89PNG")
    assert full.lower().endswith(".png")
