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
