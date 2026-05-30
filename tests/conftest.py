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
