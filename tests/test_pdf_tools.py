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
