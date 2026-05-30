# 🗂️ Organizador de Arquivos

Aplicativo **local** para **renomear, organizar e trabalhar com arquivos, pastas e PDFs**, com uma interface simples que abre no navegador. Tudo roda no seu computador — **nada é enviado para a internet**.

> Feito em **Python** + **HTML**. Empacotado como `.exe` autossuficiente: as dependências (**pypdf**, **Pillow** e o **Ghostscript**) já vêm **embutidas** — não é preciso instalar nada.

**Versão atual: 1.5**

---

## ✨ Funcionalidades

O app é organizado em abas:

### Arquivos e pastas
| Aba | O que faz |
|---|---|
| **Renomear** | Renomeia arquivos e pastas manualmente, com **navegação por subpastas**, **pré-visualização** ao lado (imagens, PDF, texto), validação de nomes inválidos com sugestão automática, **ordenação** (Natural estilo Windows / A‑Z / Z‑A) e opção de colar uma lista de nomes. |
| **Renomear em lote** | Renomeação por padrões: localizar/substituir, prefixo/sufixo, **numeração sequencial**, MAIÚSCULAS/minúsculas, remover acentos e preservar a extensão. |
| **Colar do Excel** | Uma coluna mostra os nomes atuais; ao lado, você **cola a coluna de novos nomes direto do Excel**. Cada linha corresponde, na ordem, ao nome da esquerda. |
| **Criar** | Cria várias **pastas ou arquivos em branco** de uma vez (com numeração automática opcional). |
| **Organizar** | Move os arquivos soltos para subpastas **automaticamente**, por tipo (Imagens, Documentos, Planilhas…) ou por data (ano‑mês). |
| **Exportar lista** | Gera um inventário da pasta em **TXT** ou **CSV**. |

### Ferramentas de PDF
| Aba | O que faz |
|---|---|
| **Extrair Páginas** | Abre um PDF (com pré‑visualização) e **extrai faixas de páginas** em vários arquivos, com até 250 cortes de uma vez. |
| **Excluir Páginas** | **Remove páginas específicas** (ex.: `1, 3, 5‑8`); salva como novo arquivo ou sobrescreve o original (com desfazer). |
| **Dividir PDF** | Divisão automática: a cada X páginas, em N partes iguais, uma página por arquivo, **por capítulos (marcadores)** ou **por tamanho (MB)** — gerando partes abaixo de um limite (ótimo para limites de upload). |
| **Juntar PDF** | Junta vários PDFs numa ordem definida (lista reordenável), com **marcadores** (um por arquivo, agrupados por pasta de origem, ou nenhum). |
| **Girar e Reordenar** | **Gira páginas** (90°) e **muda a ordem** das páginas de um PDF. |
| **Imagens ⇄ PDF** | **Imagens → PDF** (junta JPG/PNG… num PDF, ajustando à imagem ou em A4) e **PDF → Imagens** (exporta páginas como PNG/JPG em 96/150/300 dpi). |
| **Comprimir PDF** | Reduz o tamanho via Ghostscript (Máxima compressão / Equilíbrio / Alta qualidade); mostra **antes → depois** e o % economizado. |
| **Senha** | **Protege** um PDF com senha ou **remove** a senha de um PDF. |
| **Marcadores** | Editor completo de marcadores: ver, **renomear, mudar página/nível, reordenar, adicionar/excluir**, criar em massa colando uma lista, **exportar a lista em .txt**, salvar no PDF e dividir por eles. |
| **Comparar Arquivos** | Abre **dois visualizadores lado a lado** (PDF, imagem ou texto) para comparar arquivos. |

**Recursos transversais:**
- **Arrastar e soltar** PDFs (e imagens, na aba Imagens ⇄ PDF) direto nas abas de PDF.
- **Desfazer** para renomear, criar, organizar e para operações que sobrescrevem o arquivo original.
- **Prévia/confirmação** antes de aplicar mudanças.

---

## 🚀 Como usar

### Opção 1 — Programa pronto (`.exe`), sem instalar nada
1. Vá em **[Releases](../../releases)** e baixe o **`Central de Arquivos.exe`**.
2. Dê **dois cliques**.
3. O app abre no seu navegador. Para encerrar, feche a janelinha de controle.

> ⚠️ Na primeira vez, o Windows pode mostrar o **SmartScreen** ("Windows protegeu seu PC"). Clique em **Mais informações → Executar assim mesmo**. O programa não é assinado digitalmente, mas é seguro e roda 100% local. Requer **Windows 64‑bit**.

### Opção 2 — Rodar com Python
Requer Python 3 instalado.
```bash
git clone https://github.com/lucascorreaml/organizador-de-arquivos.git
cd organizador-de-arquivos
pip install pypdf pillow
python renomear.py
```
No Windows, também é possível usar o atalho **`Renomear.bat`** (dois cliques).

> A compressão e o "PDF → Imagens" usam o **Ghostscript**. No `.exe` ele já vem embutido; rodando via Python, é preciso ter o Ghostscript instalado (ou uma cópia em `gs/` ao lado do script).

---

## 🔒 Privacidade

O app roda **localmente** na sua máquina e só acessa as pastas que você escolhe, para realizar as operações. **Nenhum arquivo ou dado é enviado para a internet.**

## 🛠️ Tecnologia

- **Python** — biblioteca padrão (`http.server`, `tkinter`, etc.).
- **pypdf** — manipulação de PDFs (extrair, dividir, juntar, girar, marcadores, senha).
- **Pillow** — imagens → PDF.
- **Ghostscript** — compressão de PDF e rasterização (PDF → imagens). Licença **AGPL**; embutido no executável.
- Interface em **HTML/CSS/JS** embutida, servida localmente em `127.0.0.1`.
- Empacotado como `.exe` com **PyInstaller** (todas as dependências vão juntas).

---

## 📄 Licença

Projeto pessoal — sinta‑se à vontade para usar e adaptar. Inclui o **Ghostscript** (AGPL) de forma embutida.
