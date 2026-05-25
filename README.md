# 🗂️ Organizador de Arquivos

Aplicativo **local** para **renomear, criar, organizar e catalogar** arquivos e pastas, com uma interface simples que abre no navegador. Tudo roda no seu computador — **nada é enviado para a internet**.

> Feito em **Python** + **HTML**. Usa quase só a biblioteca padrão; a única dependência externa é a **pypdf** (para dividir PDFs) — que já vem **embutida** no `.exe`.

---

## ✨ Funcionalidades

O app é organizado em abas:

| Aba | O que faz |
|---|---|
| **Renomear** | Renomeia arquivos e pastas manualmente, com **navegação por subpastas**, **pré-visualização** do arquivo ao lado (imagens, PDF, texto), validação de nomes inválidos com sugestão automática e opção de colar uma lista de nomes. |
| **Renomear em lote** | Renomeação por padrões: localizar/substituir, prefixo/sufixo, **numeração sequencial**, MAIÚSCULAS/minúsculas, remover acentos e preservar a extensão. |
| **Colar do Excel** | Uma coluna mostra os nomes atuais; ao lado, você **cola a coluna de novos nomes direto do Excel**. Cada linha corresponde, na ordem, ao nome da esquerda. |
| **Criar** | Cria várias **pastas ou arquivos em branco** de uma vez (com numeração automática opcional). |
| **Organizar** | Move os arquivos soltos para subpastas **automaticamente**, por tipo (Imagens, Documentos, Planilhas…) ou por data (ano-mês). |
| **Exportar lista** | Gera um inventário da pasta em **TXT** ou **CSV**. |
| **Extrair Páginas** | Abre um PDF (com pré-visualização) e **extrai faixas de páginas** em vários arquivos (página inicial/final + nome), com até 250 cortes de uma vez. |
| **Dividir PDF** | Divisão **automática** de um PDF: a cada X páginas, em N partes iguais, uma página por arquivo, ou **por capítulos (marcadores)** com escolha de nível e prévia detalhada. |
| **Marcadores** | Editor completo de marcadores (bookmarks): ver, **renomear, mudar página/nível, reordenar, adicionar/excluir**, criar em massa colando uma lista, salvar no PDF e dividir por eles. |
| **Comparar Arquivos** | Abre **dois visualizadores lado a lado** (PDF, imagem ou texto) para comparar arquivos. |

Extras: botão **Desfazer** (para renomear, criar e organizar) e **prévia/confirmação** antes de aplicar qualquer mudança.

---

## 🚀 Como usar

### Opção 1 — Programa pronto (`.exe`), sem instalar nada
1. Vá em **[Releases](../../releases)** e baixe o `Organizador-de-Arquivos.exe`.
2. Dê **dois cliques**.
3. O app abre no seu navegador. Para encerrar, feche a janelinha de controle.

> ⚠️ Na primeira vez, o Windows pode mostrar o **SmartScreen** ("Windows protegeu seu PC"). Clique em **Mais informações → Executar assim mesmo**. O programa não é assinado digitalmente, mas é seguro e roda 100% local.

### Opção 2 — Rodar com Python
Requer Python 3 instalado.
```bash
git clone https://github.com/lucascorreaml/organizador-de-arquivos.git
cd organizador-de-arquivos
pip install pypdf
python renomear.py
```
No Windows, também é possível usar o atalho **`Renomear.bat`** (dois cliques).

---

## 🔒 Privacidade

O app roda **localmente** na sua máquina e só acessa as pastas que você escolhe, para realizar as operações. **Nenhum arquivo ou dado é enviado para a internet.**

## 🛠️ Tecnologia

- **Python** — biblioteca padrão (`http.server`, `tkinter`, etc.) + **pypdf** (divisão de PDFs).
- Interface em **HTML/CSS/JS** embutida, servida localmente em `127.0.0.1`.
- Empacotado como `.exe` com **PyInstaller** (a pypdf vai junto, embutida).

## 📄 Licença

Projeto pessoal — sinta-se à vontade para usar e adaptar.
