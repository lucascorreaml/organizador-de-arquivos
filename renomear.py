# -*- coding: utf-8 -*-
"""
Organizador de Arquivos — motor local.

Aplicativo com varias ferramentas para arquivos e pastas (renomear, renomear em
lote, criar, organizar, exportar lista) + pre-visualizacao de arquivos.
Visual sobrio "site antigo" (Times New Roman, preto e branco).
So usa a biblioteca padrao do Python (sem pip install).

Inicie pelo arquivo "Renomear.bat" (dois cliques).
"""

import io
import csv
import json
import os
import re
import sys
import uuid
import shutil
import socket
import tempfile
import datetime
import mimetypes
import threading
import webbrowser
import subprocess
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------------------------------------------------------
# Validacao de nomes (regras do Windows)
# ----------------------------------------------------------------------------

INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def validate_name(name):
    if name is None or name.strip() == "":
        return False, "Nome vazio."
    if name in (".", ".."):
        return False, 'Nome reservado pelo sistema ("." ou "..").'
    bad = INVALID_CHARS_RE.findall(name)
    if bad:
        amostra = " ".join(sorted(set(c for c in bad if c.strip())))
        return False, f"Contem caractere(s) proibido(s): {amostra or 'caractere de controle'}"
    if name != name.rstrip(" ."):
        return False, "Nome nao pode terminar com espaco ou ponto."
    base = name.split(".")[0].upper()
    if base in RESERVED_NAMES:
        return False, f'"{base}" e um nome reservado do Windows.'
    if len(name) > 255:
        return False, "Nome muito longo (mais de 255 caracteres)."
    return True, ""


def suggest_name(name):
    if not name:
        return "novo nome"
    s = INVALID_CHARS_RE.sub("-", name)
    s = re.sub(r"-{2,}", "-", s)
    s = s.rstrip(" .")
    if not s:
        s = "novo nome"
    base = s.split(".")[0].upper()
    if base in RESERVED_NAMES:
        parts = s.split(".", 1)
        parts[0] = parts[0] + "_"
        s = ".".join(parts)
    if len(s) > 255:
        s = s[:255].rstrip(" .")
    return s


# ----------------------------------------------------------------------------
# Listagem
# ----------------------------------------------------------------------------

def list_folder(path):
    if not path or not os.path.isdir(path):
        raise ValueError("Pasta nao encontrada.")
    entries = []
    with os.scandir(path) as it:
        for e in it:
            if e.name.startswith(".renomear_tmp_"):
                continue
            is_dir = e.is_dir()
            try:
                size = e.stat().st_size if not is_dir else 0
            except OSError:
                size = 0
            entries.append({"name": e.name, "is_dir": is_dir, "size": size,
                            "path": e.path})
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return entries


def folder_payload(path):
    path = os.path.abspath(path)
    items = list_folder(path)
    parent = os.path.dirname(path)
    if not parent or parent == path:
        parent = None
    return {"path": path, "parent": parent, "items": items}


# ----------------------------------------------------------------------------
# Renomeacao
# ----------------------------------------------------------------------------

def build_plan(folder, renames):
    plan, problems, seen_targets = [], [], {}
    freed = set()
    for r in renames:
        cur = r.get("current", "")
        new = (r.get("new", "") or "").strip()
        if new and new != cur:
            freed.add(cur)
    existing = set(os.listdir(folder)) if os.path.isdir(folder) else set()
    for r in renames:
        cur = r.get("current", "")
        new = (r.get("new", "") or "").strip()
        if not new or new == cur:
            continue
        ok, reason = validate_name(new)
        if not ok:
            problems.append({"current": cur, "new": new, "reason": reason,
                             "suggestion": suggest_name(new)})
            continue
        key = new.lower()
        if key in seen_targets:
            problems.append({"current": cur, "new": new,
                             "reason": f'Dois itens ficariam com o mesmo nome ("{new}").',
                             "suggestion": suggest_name(new)})
            continue
        seen_targets[key] = cur
        if new in existing and new not in freed and new != cur:
            problems.append({"current": cur, "new": new,
                             "reason": f'Ja existe um item chamado "{new}" na pasta.',
                             "suggestion": suggest_name(new + " (2)")})
            continue
        plan.append((cur, new))
    return plan, problems


def apply_rename(folder, plan):
    done, temp_pairs = [], []
    try:
        for cur, new in plan:
            src = os.path.join(folder, cur)
            tmp = os.path.join(folder, f".renomear_tmp_{uuid.uuid4().hex}")
            os.rename(src, tmp)
            done.append((tmp, src))
            temp_pairs.append((tmp, os.path.join(folder, new)))
        result = []
        for tmp, dst in temp_pairs:
            os.rename(tmp, dst)
            origin = dict(done).get(tmp, tmp)
            result.append((os.path.basename(origin), os.path.basename(dst)))
        return result
    except Exception:
        for cur_path, orig_path in reversed(done):
            try:
                if os.path.exists(cur_path):
                    os.rename(cur_path, orig_path)
            except OSError:
                pass
        raise


# ----------------------------------------------------------------------------
# Criar pastas / arquivos
# ----------------------------------------------------------------------------

def create_items(folder, names, kind):
    if not os.path.isdir(folder):
        raise ValueError("Pasta de destino nao encontrada.")
    created, problems, seen = [], [], set()
    existing = set(os.listdir(folder))
    for raw in names:
        name = (raw or "").strip()
        if not name:
            continue
        ok, reason = validate_name(name)
        if not ok:
            problems.append({"name": name, "reason": reason, "suggestion": suggest_name(name)})
            continue
        key = name.lower()
        if key in seen or name in existing:
            problems.append({"name": name, "reason": "Ja existe um item com esse nome.",
                             "suggestion": suggest_name(name + " (2)")})
            continue
        seen.add(key)
        path = os.path.join(folder, name)
        try:
            if kind == "folder":
                os.mkdir(path)
            else:
                open(path, "x").close()
            created.append(path)
        except Exception as e:
            problems.append({"name": name, "reason": str(e), "suggestion": ""})
    return created, problems


# ----------------------------------------------------------------------------
# Organizar por tipo / data
# ----------------------------------------------------------------------------

EXT_CATEGORIES = {
    "Imagens": {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "heic", "heif", "tif", "tiff", "ico"},
    "Documentos": {"pdf", "doc", "docx", "txt", "rtf", "odt", "md", "tex", "epub"},
    "Planilhas": {"xls", "xlsx", "csv", "ods", "tsv"},
    "Apresentacoes": {"ppt", "pptx", "odp"},
    "Videos": {"mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v"},
    "Audio": {"mp3", "wav", "flac", "aac", "ogg", "m4a", "wma"},
    "Compactados": {"zip", "rar", "7z", "tar", "gz", "bz2"},
    "Programas": {"exe", "msi", "bat", "cmd", "ps1"},
    "Codigo": {"py", "js", "ts", "html", "css", "json", "xml", "java", "c", "cpp", "cs", "go", "rb", "php"},
}


def category_for(name):
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    if not ext:
        return "Sem extensao"
    for cat, exts in EXT_CATEGORIES.items():
        if ext in exts:
            return cat
    return "Outros"


def organize_plan(folder, mode):
    if not os.path.isdir(folder):
        raise ValueError("Pasta nao encontrada.")
    plan = []
    with os.scandir(folder) as it:
        for e in it:
            if e.name.startswith(".renomear_tmp_") or e.is_dir():
                continue
            if mode == "date":
                try:
                    target = datetime.datetime.fromtimestamp(e.stat().st_mtime).strftime("%Y-%m")
                except OSError:
                    target = "sem-data"
            else:
                target = category_for(e.name)
            plan.append((e.name, target))
    plan.sort(key=lambda x: (x[1].lower(), x[0].lower()))
    return plan


def organize_apply(folder, mode):
    plan = organize_plan(folder, mode)
    moves, created_dirs, results = [], [], []
    try:
        for name, target in plan:
            tdir = os.path.join(folder, target)
            if not os.path.isdir(tdir):
                os.mkdir(tdir)
                created_dirs.append(tdir)
            src = os.path.join(folder, name)
            dst = os.path.join(tdir, name)
            if os.path.exists(dst):
                base, ext = os.path.splitext(name)
                k = 2
                while os.path.exists(os.path.join(tdir, f"{base} ({k}){ext}")):
                    k += 1
                dst = os.path.join(tdir, f"{base} ({k}){ext}")
            shutil.move(src, dst)
            moves.append((dst, src))
            results.append({"name": name, "target": target})
        return moves, created_dirs, results
    except Exception:
        for newp, oldp in reversed(moves):
            try:
                shutil.move(newp, oldp)
            except OSError:
                pass
        for d in reversed(created_dirs):
            try:
                os.rmdir(d)
            except OSError:
                pass
        raise


# ----------------------------------------------------------------------------
# Exportar lista
# ----------------------------------------------------------------------------

def _row(name, is_dir, full, rel):
    try:
        st = os.stat(full)
        size = "" if is_dir else st.st_size
        modified = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        size, modified = "", ""
    return {"name": name, "type": "pasta" if is_dir else "arquivo",
            "size": size, "modified": modified, "rel": rel}


def export_list(folder, recursive, fmt):
    if not os.path.isdir(folder):
        raise ValueError("Pasta nao encontrada.")
    rows = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames.sort(key=str.lower)
            for d in sorted(dirnames, key=str.lower):
                full = os.path.join(dirpath, d)
                rows.append(_row(d, True, full, os.path.relpath(full, folder)))
            for f in sorted(filenames, key=str.lower):
                full = os.path.join(dirpath, f)
                rows.append(_row(f, False, full, os.path.relpath(full, folder)))
    else:
        for it in list_folder(folder):
            rows.append(_row(it["name"], it["is_dir"], it["path"], it["name"]))

    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Nome", "Tipo", "Tamanho (bytes)", "Modificado", "Caminho"])
        for r in rows:
            w.writerow([r["name"], r["type"], r["size"], r["modified"], r["rel"]])
        return buf.getvalue(), "lista_de_arquivos.csv"
    else:
        lines = [f"Lista de: {folder}",
                 f"Total: {len(rows)} item(ns)",
                 "-" * 50, ""]
        for r in rows:
            mark = "[PASTA] " if r["type"] == "pasta" else ""
            lines.append(f"{mark}{r['rel']}")
        return "\n".join(lines), "lista_de_arquivos.txt"


# ----------------------------------------------------------------------------
# Dividir PDF (usa a biblioteca pypdf)
# ----------------------------------------------------------------------------

def pdf_info(path):
    if not path or not os.path.isfile(path):
        raise ValueError("PDF nao encontrado.")
    from pypdf import PdfReader
    reader = PdfReader(path, strict=False)
    return {"pages": len(reader.pages), "name": os.path.basename(path),
            "folder": os.path.dirname(os.path.abspath(path))}


def _unique_name(folder, fname):
    base, ext = os.path.splitext(fname)
    cand, k = fname, 2
    while os.path.exists(os.path.join(folder, cand)):
        cand = f"{base} ({k}){ext}"
        k += 1
    return cand


def pdf_split(pdf_path, dest_folder, ranges, auto_suffix=False):
    if not pdf_path or not os.path.isfile(pdf_path):
        raise ValueError("PDF nao encontrado.")
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(pdf_path, strict=False)
    total = len(reader.pages)

    plan, problems, seen = [], [], set()
    for idx, r in enumerate(ranges):
        name = (r.get("name", "") or "").strip()
        s_raw = str(r.get("start", "")).strip()
        e_raw = str(r.get("end", "")).strip()
        if not (s_raw or e_raw or name):
            continue  # caixa vazia: ignora
        if not name:
            problems.append({"row": idx + 1, "reason": "Nome do arquivo vazio."}); continue
        try:
            s, e = int(s_raw), int(e_raw)
        except ValueError:
            problems.append({"row": idx + 1, "reason": "Paginas precisam ser numeros."}); continue
        if auto_suffix:
            s = max(1, s); e = min(total, e)
        if s < 1 or e > total or s > e:
            problems.append({"row": idx + 1, "reason": f"Faixa invalida (o PDF tem {total} paginas)."}); continue
        fname = name if name.lower().endswith(".pdf") else name + ".pdf"
        ok, reason = validate_name(fname)
        if not ok:
            if auto_suffix:
                fname = (suggest_name(os.path.splitext(fname)[0]) or f"arquivo {idx+1}") + ".pdf"
            else:
                problems.append({"row": idx + 1, "reason": reason}); continue
        key = fname.lower()
        if key in seen:
            if auto_suffix:
                base, ext = os.path.splitext(fname)
                k = 2
                while f"{base} ({k}){ext}".lower() in seen:
                    k += 1
                fname = f"{base} ({k}){ext}"
                key = fname.lower()
            else:
                problems.append({"row": idx + 1, "reason": f'Nome repetido: "{fname}".'}); continue
        seen.add(key)
        plan.append((s, e, fname))

    if problems:
        return {"ok": False, "problems": problems, "total": total}
    if not plan:
        return {"ok": False, "problems": [{"row": 0, "reason": "Nenhuma caixa preenchida."}], "total": total}

    os.makedirs(dest_folder, exist_ok=True)
    results = []
    for s, e, fname in plan:
        writer = PdfWriter()
        for p in range(s - 1, e):
            writer.add_page(reader.pages[p])
        final = _unique_name(dest_folder, fname)
        with open(os.path.join(dest_folder, final), "wb") as f:
            writer.write(f)
        results.append({"name": final, "pages": f"{s}-{e}", "count": e - s + 1})
    return {"ok": True, "results": results, "dest": dest_folder, "total": total}


def pdf_outline(path):
    """Le os marcadores (bookmarks) do PDF como uma lista plana com nivel e pagina."""
    if not path or not os.path.isfile(path):
        raise ValueError("PDF nao encontrado.")
    from pypdf import PdfReader
    reader = PdfReader(path, strict=False)
    flat = []

    def walk(items, depth):
        for it in items:
            if isinstance(it, list):
                walk(it, depth + 1)
            else:
                try:
                    page = reader.get_destination_page_number(it) + 1
                except Exception:
                    page = None
                title = getattr(it, "title", None) or "(sem titulo)"
                if page:
                    flat.append({"title": str(title), "depth": depth, "page": page})

    try:
        walk(reader.outline, 1)
    except Exception:
        flat = []
    maxdepth = max((b["depth"] for b in flat), default=0)
    return {"pages": len(reader.pages), "maxdepth": maxdepth, "bookmarks": flat}


def pdf_save_outline(pdf_path, out_path, items):
    """Grava uma nova estrutura de marcadores no PDF (copia as paginas)."""
    if not pdf_path or not os.path.isfile(pdf_path):
        raise ValueError("PDF nao encontrado.")
    from pypdf import PdfReader, PdfWriter
    with open(pdf_path, "rb") as f:
        data = f.read()
    reader = PdfReader(io.BytesIO(data), strict=False)
    total = len(reader.pages)
    writer = PdfWriter()
    try:
        writer.append(reader, import_outline=False)
    except TypeError:
        writer.append(reader)

    parents, count = {}, 0
    for it in items:
        title = (it.get("title") or "").strip() or "(sem titulo)"
        try:
            page = int(it.get("page", 1))
        except (TypeError, ValueError):
            page = 1
        page = max(1, min(total, page)) - 1
        try:
            depth = max(1, int(it.get("depth", 1)))
        except (TypeError, ValueError):
            depth = 1
        parent = parents.get(depth - 1) if depth > 1 else None
        ref = writer.add_outline_item(title, page, parent=parent)
        parents[depth] = ref
        for d in [k for k in parents if k > depth]:
            del parents[d]
        count += 1

    tmp = out_path + ".tmp"
    with open(tmp, "wb") as f:
        writer.write(f)
    os.replace(tmp, out_path)
    return {"ok": True, "path": out_path, "count": count}


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
    """Comprime `src` em `out` via Ghostscript.

    quality: 'max' (maxima compressao, /screen), 'balance' (/ebook),
    'high' (alta qualidade, /printer).
    """
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
        try:
            os.remove(tmp)
        except OSError:
            pass
        detail = proc.stderr.decode("utf-8", "ignore")[:200] if proc.stderr else ""
        raise ValueError("Falha ao comprimir (Ghostscript). " + detail)
    os.replace(tmp, out)
    after = os.path.getsize(out)
    saved = round((1 - after / before) * 100, 1) if before else 0.0
    return {"ok": True, "path": out, "before": before, "after": after, "saved_pct": saved}


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
    udir = upload_dir()
    dest = _unique_name(udir, safe)
    full = os.path.join(udir, dest)
    with open(full, "wb") as f:
        f.write(data)
    return full


def _backup_for_undo(path):
    """Copia `path` para um temporario e devolve o caminho do backup."""
    fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="renomear_bak_")
    os.close(fd)
    try:
        shutil.copy2(path, tmp)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return tmp


# ----------------------------------------------------------------------------
# Desfazer (pilha generica)
# ----------------------------------------------------------------------------

UNDO_STACK = []


# ----------------------------------------------------------------------------
# Seletor de pasta nativo (tkinter, em subprocesso p/ vir ao topo)
# ----------------------------------------------------------------------------

def pick_path_tk(kind="folder", initialdir=None):
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes("-topmost", 1)
    root.update()
    ini = initialdir if (initialdir and os.path.isdir(initialdir)) else None
    if kind in ("file", "anyfile"):
        if kind == "anyfile":
            ft = [("Todos os arquivos", "*.*"), ("PDF", "*.pdf")]
            ttl = "Escolha o arquivo"
        else:
            ft = [("PDF", "*.pdf"), ("Todos os arquivos", "*.*")]
            ttl = "Escolha o arquivo PDF"
        path = filedialog.askopenfilename(title=ttl, parent=root, filetypes=ft, initialdir=ini)
    else:
        kw = {"title": "Escolha a pasta", "mustexist": True, "parent": root}
        if ini:
            kw["initialdir"] = ini
        path = filedialog.askdirectory(**kw)
    root.destroy()
    return path or ""


def choose_path_dialog(kind="folder", start=None):
    """Chama este mesmo programa em subprocesso (modo --pick) e devolve o caminho.

    O resultado e gravado num arquivo temporario (robusto em .exe sem console).
    """
    fd, tmp = tempfile.mkstemp(suffix=".txt", prefix="renomear_pick_")
    os.close(fd)
    try:
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--pick", kind, start or "", tmp]
        else:
            cmd = [sys.executable, os.path.abspath(__file__), "--pick", kind, start or "", tmp]
        subprocess.run(cmd, timeout=600,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with open(tmp, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def choose_folder_dialog(start=None):
    return choose_path_dialog("folder", start)


# ----------------------------------------------------------------------------
# Servidor HTTP
# ----------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path):
        try:
            if not path or not os.path.isfile(path):
                self.send_response(404)
                self.end_headers()
                return
            ctype, _ = mimetypes.guess_type(path)
            if not ctype:
                ctype = "application/octet-stream"
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            try:
                self.send_response(500)
                self.end_headers()
            except Exception:
                pass

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._send(200, HTML_PAGE, "text/html; charset=utf-8")
        elif parsed.path == "/file":
            qs = parse_qs(parsed.query)
            self._send_file(qs.get("path", [""])[0])
        else:
            self._send(404, {"error": "nao encontrado"})

    def do_POST(self):
        try:
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

            data = self._read_json()

            if self.path == "/api/choose-folder":
                path = choose_folder_dialog(data.get("start"))
                if not path:
                    self._send(200, {"cancelled": True})
                    return
                self._send(200, folder_payload(path))

            elif self.path == "/api/list":
                self._send(200, folder_payload(data.get("path", "")))

            elif self.path == "/api/validate":
                folder = data.get("folder", "")
                plan, problems = build_plan(folder, data.get("renames", []))
                self._send(200, {"ok": len(problems) == 0, "count": len(plan),
                                 "plan": [{"current": c, "new": n} for c, n in plan],
                                 "problems": problems})

            elif self.path == "/api/rename":
                folder = data.get("folder", "")
                plan, problems = build_plan(folder, data.get("renames", []))
                if problems:
                    self._send(200, {"ok": False, "problems": problems})
                    return
                if not plan:
                    self._send(200, {"ok": True, "renamed": 0, **folder_payload(folder)})
                    return
                results = apply_rename(folder, plan)
                UNDO_STACK.append({"type": "rename", "folder": folder,
                                   "pairs": [(new, cur) for cur, new in plan]})
                self._send(200, {"ok": True, "renamed": len(results),
                                 "can_undo": True, **folder_payload(folder)})

            elif self.path == "/api/create":
                folder = data.get("folder", "")
                kind = data.get("kind", "folder")
                created, problems = create_items(folder, data.get("names", []), kind)
                if created:
                    UNDO_STACK.append({"type": "create", "folder": folder,
                                       "paths": list(created)})
                self._send(200, {"ok": len(problems) == 0,
                                 "created": [os.path.basename(p) for p in created],
                                 "problems": problems, "can_undo": len(created) > 0,
                                 **folder_payload(folder)})

            elif self.path == "/api/organize":
                folder = data.get("folder", "")
                mode = data.get("mode", "type")
                if not data.get("apply"):
                    plan = organize_plan(folder, mode)
                    counts = {}
                    for _, t in plan:
                        counts[t] = counts.get(t, 0) + 1
                    self._send(200, {"ok": True, "total": len(plan), "counts": counts,
                                     "plan": [{"name": n, "target": t} for n, t in plan]})
                else:
                    moves, created_dirs, results = organize_apply(folder, mode)
                    if moves:
                        UNDO_STACK.append({"type": "move", "folder": folder,
                                           "moves": moves, "created_dirs": created_dirs})
                    self._send(200, {"ok": True, "moved": len(results),
                                     "can_undo": len(moves) > 0, **folder_payload(folder)})

            elif self.path == "/api/export":
                content, filename = export_list(data.get("folder", ""),
                                                bool(data.get("recursive")),
                                                data.get("format", "txt"))
                self._send(200, {"ok": True, "content": content, "filename": filename})

            elif self.path == "/api/choose-file":
                path = choose_path_dialog(data.get("kind", "file"), data.get("start"))
                if not path:
                    self._send(200, {"cancelled": True})
                    return
                self._send(200, {"path": path})

            elif self.path == "/api/pdf-info":
                try:
                    self._send(200, {"ok": True, **pdf_info(data.get("path", ""))})
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e)})

            elif self.path == "/api/pdf-outline":
                try:
                    self._send(200, {"ok": True, **pdf_outline(data.get("path", ""))})
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e)})

            elif self.path == "/api/pdf-save-outline":
                pdf = data.get("pdf", "")
                out = pdf if data.get("overwrite") else data.get("out", "")
                if not out:
                    self._send(200, {"ok": False, "error": "Caminho de saida nao informado."})
                    return
                try:
                    self._send(200, pdf_save_outline(pdf, out, data.get("items", [])))
                except Exception as e:
                    self._send(200, {"ok": False, "error": str(e)})

            elif self.path == "/api/pdf-split":
                pdf = data.get("pdf", "")
                dest = data.get("dest", "") or (os.path.dirname(pdf) if pdf else "")
                sub = (data.get("subfolder") or "").strip()
                if sub:
                    ok, reason = validate_name(sub)
                    if not ok:
                        self._send(200, {"ok": False, "problems": [{"row": 0, "reason": f"Subpasta invalida: {reason}"}]})
                        return
                    dest = os.path.join(dest, sub)
                self._send(200, pdf_split(pdf, dest, data.get("ranges", []),
                                          auto_suffix=bool(data.get("auto"))))

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

            elif self.path == "/api/undo":
                if not UNDO_STACK:
                    self._send(200, {"ok": False, "error": "Nada para desfazer."})
                    return
                op = UNDO_STACK.pop()
                t = op.get("type")
                folder = op.get("folder")
                if t == "rename":
                    apply_rename(folder, op["pairs"])
                    msg = "Renomeacao desfeita."
                elif t == "create":
                    n = 0
                    for p in reversed(op["paths"]):
                        try:
                            if os.path.isdir(p) and not os.listdir(p):
                                os.rmdir(p); n += 1
                            elif os.path.isfile(p) and os.path.getsize(p) == 0:
                                os.remove(p); n += 1
                        except OSError:
                            pass
                    msg = f"{n} item(ns) criado(s) removido(s)."
                elif t == "move":
                    n = 0
                    for newp, oldp in reversed(op["moves"]):
                        try:
                            shutil.move(newp, oldp); n += 1
                        except OSError:
                            pass
                    for d in reversed(op.get("created_dirs", [])):
                        try:
                            if os.path.isdir(d) and not os.listdir(d):
                                os.rmdir(d)
                        except OSError:
                            pass
                    msg = f"{n} arquivo(s) movido(s) de volta."
                elif t == "restore":
                    target, backup = op.get("target"), op.get("backup")
                    if backup and os.path.isfile(backup):
                        shutil.copy2(backup, target)
                        try:
                            os.remove(backup)
                        except OSError:
                            pass
                    msg = "Arquivo restaurado."
                else:
                    self._send(200, {"ok": False, "error": "Operacao desconhecida."})
                    return
                self._send(200, {"ok": True, "message": msg, "folder": folder,
                                 "can_undo": len(UNDO_STACK) > 0})

            else:
                self._send(404, {"error": "rota desconhecida"})

        except Exception as e:
            self._send(200, {"ok": False, "error": str(e)})


def find_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _control_window(url, server):
    """Janelinha de controle (usada quando rodando como .exe sem console)."""
    try:
        import tkinter as tk
        root = tk.Tk()
        root.title("Organizador de Arquivos")
        root.geometry("400x170")
        root.resizable(False, False)
        tk.Label(root, text="Organizador de Arquivos está rodando",
                 font=("Segoe UI", 12, "bold")).pack(pady=(20, 6))
        tk.Label(root, text="O app abriu no seu navegador.").pack()
        tk.Label(root, text="Feche esta janela para encerrar o programa.",
                 fg="#555").pack(pady=(2, 10))
        tk.Button(root, text="Abrir no navegador novamente",
                  command=lambda: webbrowser.open(url)).pack()

        def on_close():
            try:
                server.shutdown()
            except Exception:
                pass
            root.destroy()
            os._exit(0)

        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()
    except Exception:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main():
    port = find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    if getattr(sys, "frozen", False):
        # empacotado como .exe (sem console): janelinha de controle
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _control_window(url, server)
    else:
        # rodando via Renomear.bat (com console)
        print("=" * 56)
        print("  Organizador de Arquivos")
        print("=" * 56)
        print(f"  Aberto no navegador: {url}")
        print("  Deixe esta janela aberta enquanto usa o app.")
        print("  Para encerrar: feche esta janela.")
        print("=" * 56)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


# ----------------------------------------------------------------------------
# Pagina (HTML + CSS + JS) — visual sobrio "site antigo" (Times, P&B)
# ----------------------------------------------------------------------------

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Organizador de Arquivos</title>
<style>
  body{font-family:"Times New Roman",Times,serif;color:#000;background:#fff;margin:24px;font-size:17px;line-height:1.5}
  .wrap{max-width:1860px;margin:0 auto}
  h1{font-size:27px;font-weight:bold;margin:0 0 4px}
  .sub{font-style:italic;margin:0 0 12px;font-size:15px}
  hr{border:0;border-top:1px solid #000;margin:13px 0}
  a{color:#000}
  a:hover{background:#eee}

  .tabs{margin:0 0 6px}
  .tab{font-family:inherit;font-size:17px;color:#000;background:none;border:0;padding:0 5px;cursor:pointer;text-decoration:underline}
  .tab:hover{background:#eee}
  .tab.active{font-weight:bold;text-decoration:none}
  .tabsep{color:#000}
  .panel{display:none}
  .panel.active{display:block}

  button{font-family:inherit;font-size:15px;color:#000;background:#fff;border:1px solid #000;padding:5px 12px;cursor:pointer}
  button:hover{background:#eee}
  button:disabled{color:#999;border-color:#999;cursor:not-allowed;background:#fff}
  .btn-primary{font-weight:bold}
  .btn-link{border:0;background:none;padding:0;text-decoration:underline;font-size:15px;cursor:pointer}
  .btn-link:hover{background:#eee}

  .toolbar{margin:10px 0}
  .toolbar button{margin-right:5px;margin-bottom:4px}
  .pathbox{display:inline-block;padding:5px 2px;font-style:italic;font-size:13.5px;color:#555;max-width:760px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle}
  .crumbs{margin:9px 0;min-height:22px;font-size:15px}
  .crumbs .c{cursor:pointer;text-decoration:underline}
  .crumbs .cur{font-weight:bold}
  .crumbs .sep{margin:0 5px}

  table{border-collapse:collapse;width:100%}
  th,td{border:0;border-bottom:1px solid #ddd;padding:7px 10px;text-align:left;vertical-align:top}
  th{font-weight:bold;border-bottom:2px solid #000}
  tbody tr.sel td{background:#eee}
  .colw-num{width:32px;text-align:right;color:#555}
  .tag{font-style:italic;font-size:13px;color:#333}
  .ftype{font-style:italic;font-size:13px;color:#555;margin-top:2px}
  .cur{word-break:break-word}
  .cur.dir{font-weight:bold}
  .fname{cursor:pointer;text-decoration:underline;word-break:break-word}
  .newcell{word-break:break-word}
  .empty{padding:30px 14px;text-align:center;font-style:italic;font-size:15px;color:#777}
  .legend{font-style:italic;font-size:13.5px;margin-top:7px}

  input[type=text],input[type=number],textarea,select{font-family:inherit;font-size:15px;color:#000;background:#fff;border:1px solid #000;padding:5px 8px}
  input[type=text]{width:100%}
  input.bad{border:2px solid #000;background:#eee}
  textarea{width:100%;min-height:210px;line-height:1.5}

  .row-msg{font-size:13.5px;margin-top:5px}
  .msg-err{font-weight:bold}
  .sugg{font-family:inherit;border:1px solid #000;background:#fff;cursor:pointer;font-size:13.5px;padding:2px 8px;margin-left:6px}
  .sugg:hover{background:#000;color:#fff}
  .badge{font-size:13.5px}
  .b-same{color:#777}
  .b-ok{font-style:italic}
  .b-err{font-weight:bold}

  .field{margin:12px 0}
  .lbl{display:block;font-weight:bold;margin-bottom:5px}
  .opts{display:flex;flex-wrap:wrap;gap:8px 20px;align-items:center}
  .opt{display:flex;align-items:center;gap:6px}
  .row2{display:flex;gap:18px;flex-wrap:wrap}
  .row2>div{flex:1;min-width:210px}
  .box{background:#f6f6f6;padding:12px 14px;margin:11px 0}
  .drop{border:1px dashed #999;padding:14px;margin:10px 0;text-align:center;font-style:italic;color:#555;cursor:default}
  .drop.dragover{border-color:#000;background:#f0f0f0;color:#000}
  .sortctl{font-size:13.5px;margin-left:8px}
  .sortbtn{font-size:13px;padding:2px 7px;margin-left:3px}
  .sortbtn.on{font-weight:bold;background:#eee}
  .mglist-row{display:flex;align-items:center;gap:8px;padding:5px 2px;border-bottom:1px solid #eee}
  .mglist-row .nm{flex:1;min-width:0;word-break:break-word}
  .mglist-row .pg{font-style:italic;font-size:13px;color:#555}

  .actionbar{margin-top:12px}
  .actionbar button{margin-left:5px;margin-bottom:4px}
  .count{font-style:italic;font-size:14.5px}

  .rnlayout{display:flex;gap:26px;align-items:flex-start}
  .rnleft{flex:4;min-width:0}
  .rnright{flex:6;min-width:0;position:sticky;top:12px}
  .preview-box{border:1px solid #000;padding:12px;min-height:560px;max-height:86vh;overflow:auto}
  .preview-box img{max-width:100%;height:auto;display:block}
  .preview-box iframe{width:100%;height:82vh;min-height:640px;border:0}
  .preview-box pre{white-space:pre-wrap;word-break:break-word;font-family:"Courier New",monospace;font-size:13.5px;margin:0}
  @media(max-width:1100px){.rnlayout{display:block}.rnright{flex:auto;position:static;margin-top:16px}}

  /* aba Dividir PDF: campos a esquerda (maior), preview a direita (menor) */
  .pdlayout{display:flex;gap:26px;align-items:flex-start}
  .pdmain{flex:6;min-width:0}
  .pdside{flex:4;min-width:0;position:sticky;top:12px}
  @media(max-width:1100px){.pdlayout{display:block}.pdside{position:static;margin-top:16px}}

  .cols{display:flex;gap:22px;align-items:flex-start}
  .cols .col{flex:1;min-width:0}
  .xltext{width:100%;min-height:440px;white-space:pre;line-height:1.6;font-size:15px}
  @media(max-width:760px){.cols{display:block}.xltext{min-height:240px}}
  .pgin{width:108px}

  /* previa da divisao automatica */
  .planwrap{max-height:480px;overflow:auto;margin-top:6px;border-top:1px solid #ddd}
  .planrow{padding:7px 2px;border-bottom:1px solid #eee}
  .planpg{font-style:italic;font-size:13px;color:#777;margin-top:2px}
  .planseq{color:#555;font-weight:bold}
  .planinc{font-size:12.5px;color:#666;margin-top:2px}
  .pdfext{color:#888}
  input.bmname{width:55%;min-width:240px;font-size:15px;border:1px solid #bbb;padding:3px 6px}

  /* aba Comparar Arquivos */
  .cmplayout{display:flex;gap:20px;align-items:flex-start}
  .cmplayout .cmpcol{flex:1;min-width:0}
  .cmpview{min-height:80vh;max-height:90vh}
  .cmpview iframe{height:82vh;min-height:600px}
  @media(max-width:900px){.cmplayout{display:block}}

  /* aba Marcadores */
  .mkrow{display:flex;align-items:center;gap:6px;padding:4px 0;border-bottom:1px solid #eee}
  .mkdepth{color:#aaa;font-size:12px;width:30px;text-align:right;flex:none}
  input.mktitle{flex:1;min-width:120px;font-size:15px;border:1px solid #bbb;padding:3px 6px}
  input.mkpage{width:66px;flex:none}
  .mkbtns{flex:none;white-space:nowrap}
  .mkbtns button{padding:2px 7px;font-size:14px;margin-left:2px}

  .toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#fff;border:1px solid #000;padding:9px 16px;font-size:14.5px;display:none;z-index:50}
  .toast.show{display:block}
  .modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.35);display:none;align-items:center;justify-content:center;z-index:60;padding:20px}
  .modal-bg.show{display:flex}
  .modal{background:#fff;border:2px solid #000;max-width:680px;width:100%;max-height:84vh;overflow:auto;padding:20px}
  .modal h3{margin:0 0 8px;font-size:19px}
  .modal .pv{background:#f6f6f6;padding:12px;margin:11px 0;font-family:"Courier New",monospace;font-size:13.5px}
  .pv .line{padding:3px 0}
  .modal .acts{margin-top:11px;text-align:right}
  .modal .acts button{margin-left:6px}
  .hint{font-style:italic;margin:0;font-size:14.5px}
</style>
</head>
<body>
<div class="wrap">
  <h1>Organizador de Arquivos</h1>
  <p class="sub">Ferramentas para renomear, criar, organizar e catalogar arquivos e pastas.</p>

  <p class="tabs">
    <button class="tab active" data-tab="rename">Renomear</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="batch">Renomear em lote</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="excel">Colar do Excel</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="create">Criar</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="organize">Organizar</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="export">Exportar lista</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="pdf">Extrair Páginas</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="delpages">Excluir Páginas</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="divide">Dividir PDF</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="merge">Juntar PDF</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="compress">Comprimir PDF</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="marks">Marcadores</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="compare">Comparar Arquivos</button>
  </p>
  <hr>

  <!-- ===== RENOMEAR ===== -->
  <section class="panel active" id="panel-rename">
    <div class="toolbar">
      <button class="btn-primary" id="rnPick">Escolher pasta</button>
      <button id="rnUp" disabled>Subir</button>
      <button id="rnReload" disabled>Recarregar</button>
      <span class="sortctl" id="rnSort">Ordem: <button class="sortbtn on" data-sort="natural">Natural</button><button class="sortbtn" data-sort="az">A-Z</button><button class="sortbtn" data-sort="za">Z-A</button></span>
      <button id="rnPaste" disabled>Colar nomes</button>
      <button id="rnClear" disabled>Limpar</button>
    </div>
    <div class="crumbs" id="rnCrumbs"></div>
    <div class="rnlayout">
      <div class="rnleft">
        <div id="rnTable"><div class="empty">Clique em <b>Escolher pasta</b> para começar.</div></div>
        <div class="legend" id="rnLegend"></div>
        <div class="actionbar">
          <span class="count" id="rnCount">—</span>
          <button id="rnRename" disabled class="btn-primary">Renomear tudo</button>
          <button id="rnPreview" disabled>Prévia</button>
          <button id="rnUndo" disabled>Desfazer</button>
        </div>
      </div>
      <div class="rnright">
        <p style="margin:0 0 4px"><b>Pré-visualização</b></p>
        <div class="preview-box" id="rnPrev"><i>Clique no nome de um arquivo para ver aqui.</i></div>
      </div>
    </div>
  </section>

  <!-- ===== RENOMEAR EM LOTE ===== -->
  <section class="panel" id="panel-batch">
    <div class="toolbar">
      <button class="btn-primary" id="btPick">Escolher pasta</button>
      <span class="pathbox" id="btPath">Nenhuma pasta selecionada</span>
      <button id="btReload" disabled>Recarregar</button>
      <span class="sortctl" id="btSort">Ordem: <button class="sortbtn on" data-sort="natural">Natural</button><button class="sortbtn" data-sort="az">A-Z</button><button class="sortbtn" data-sort="za">Z-A</button></span>
    </div>
    <div class="box">
      <div class="row2">
        <div class="field" style="margin:0"><span class="lbl">Localizar</span><input type="text" id="btFind" placeholder="texto a localizar"></div>
        <div class="field" style="margin:0"><span class="lbl">Substituir por</span><input type="text" id="btRepl" placeholder="novo texto"></div>
      </div>
      <div class="row2" style="margin-top:10px">
        <div class="field" style="margin:0"><span class="lbl">Prefixo</span><input type="text" id="btPre" placeholder="vai antes"></div>
        <div class="field" style="margin:0"><span class="lbl">Sufixo</span><input type="text" id="btSuf" placeholder="vai depois (antes da extensão)"></div>
      </div>
      <div class="field">
        <span class="lbl">Transformações</span>
        <div class="opts">
          <label class="opt">Caixa
            <select id="btCase"><option value="">manter</option><option value="upper">MAIÚSCULAS</option><option value="lower">minúsculas</option><option value="title">Cada Palavra</option><option value="sentence">Como frase</option></select></label>
          <label class="opt">Espaços
            <select id="btSpace"><option value="">manter</option><option value="-">trocar por -</option><option value="_">trocar por _</option><option value="remove">remover</option></select></label>
          <label class="opt"><input type="checkbox" id="btAccents"> remover acentos</label>
          <label class="opt"><input type="checkbox" id="btExt" checked> preservar extensão</label>
        </div>
      </div>
      <div class="field">
        <span class="lbl">Numeração sequencial</span>
        <div class="opts">
          <label class="opt"><input type="checkbox" id="btNum"> numerar</label>
          <label class="opt">início <input type="number" id="btNumStart" value="1" min="0" style="width:60px"></label>
          <label class="opt">dígitos <input type="number" id="btNumDigits" value="2" min="1" max="6" style="width:54px"></label>
          <label class="opt">posição <select id="btNumPos"><option value="prefix">início</option><option value="suffix">fim</option></select></label>
          <label class="opt">separador <input type="text" id="btNumSep" value=". " style="width:60px"></label>
        </div>
      </div>
      <div class="field" style="margin-bottom:0">
        <span class="lbl">Aplicar a</span>
        <div class="opts">
          <label class="opt"><input type="radio" name="btScope" value="all" checked> tudo</label>
          <label class="opt"><input type="radio" name="btScope" value="files"> só arquivos</label>
          <label class="opt"><input type="radio" name="btScope" value="dirs"> só pastas</label>
        </div>
      </div>
    </div>
    <div id="btTable"><div class="empty">Escolha uma pasta para ver a prévia.</div></div>
    <div class="actionbar">
      <span class="count" id="btCount">—</span>
      <button id="btApply" disabled class="btn-primary">Aplicar renomeação</button>
      <button id="btUndo" disabled>Desfazer</button>
    </div>
  </section>

  <!-- ===== COLAR DO EXCEL ===== -->
  <section class="panel" id="panel-excel">
    <div class="toolbar">
      <button class="btn-primary" id="xlPick">Escolher pasta</button>
      <span class="pathbox" id="xlPath">Nenhuma pasta selecionada</span>
      <button id="xlReload" disabled>Recarregar</button>
      <span class="sortctl" id="xlSort">Ordem: <button class="sortbtn on" data-sort="natural">Natural</button><button class="sortbtn" data-sort="az">A-Z</button><button class="sortbtn" data-sort="za">Z-A</button></span>
      <button id="xlCopy" disabled>Copiar nomes atuais</button>
    </div>
    <div class="field">
      <span class="lbl">Renomear</span>
      <div class="opts">
        <label class="opt"><input type="radio" name="xlScope" value="all" checked> tudo</label>
        <label class="opt"><input type="radio" name="xlScope" value="files"> só arquivos</label>
        <label class="opt"><input type="radio" name="xlScope" value="dirs"> só pastas</label>
      </div>
    </div>
    <p class="hint">Cole a coluna do Excel na caixa da direita. Cada linha corresponde, na mesma ordem, ao nome da esquerda. Dica: use <b>Copiar nomes atuais</b>, cole no Excel, escreva os novos ao lado e traga a coluna de volta. (Se colar duas colunas, ele usa a última.)</p>
    <div class="cols">
      <div class="col">
        <p style="margin:8px 0 4px"><b>Nomes atuais</b> <span class="hint" id="xlCurCount"></span></p>
        <textarea id="xlCurrent" class="xltext" wrap="off" readonly placeholder="Escolha uma pasta…"></textarea>
      </div>
      <div class="col">
        <p style="margin:8px 0 4px"><b>Novos nomes</b> <i>(cole do Excel)</i></p>
        <textarea id="xlNew" class="xltext" wrap="off" placeholder="Cole aqui a coluna do Excel…"></textarea>
      </div>
    </div>
    <div class="actionbar">
      <span class="count" id="xlCount">—</span>
      <button id="xlRename" disabled class="btn-primary">Renomear</button>
      <button id="xlUndo" disabled>Desfazer</button>
    </div>
  </section>

  <!-- ===== CRIAR ===== -->
  <section class="panel" id="panel-create">
    <div class="field">
      <span class="lbl">O que criar</span>
      <div class="opts">
        <label class="opt"><input type="radio" name="crKind" value="folder" checked> Pastas</label>
        <label class="opt"><input type="radio" name="crKind" value="file"> Arquivos</label>
      </div>
    </div>
    <div class="toolbar">
      <button class="btn-primary" id="crPick">Escolher pasta de destino</button>
      <span class="pathbox" id="crPath">Nenhuma pasta selecionada</span>
    </div>
    <div class="field">
      <span class="lbl">Nomes — um por linha (pode colar uma coluna do Excel)</span>
      <textarea id="crNames" placeholder="01. Documentos&#10;02. Contratos&#10;Fotos do evento"></textarea>
    </div>
    <div class="field">
      <div class="opts"><label class="opt"><input type="checkbox" id="crNum"> adicionar numeração automática (01., 02., …)</label></div>
    </div>
    <div class="actionbar">
      <span class="count" id="crCount">—</span>
      <button id="crCreate" disabled class="btn-primary">Criar</button>
      <button id="crUndo" disabled>Desfazer</button>
    </div>
    <div id="crResult"></div>
  </section>

  <!-- ===== ORGANIZAR ===== -->
  <section class="panel" id="panel-organize">
    <div class="toolbar">
      <button class="btn-primary" id="ogPick">Escolher pasta</button>
      <span class="pathbox" id="ogPath">Nenhuma pasta selecionada</span>
    </div>
    <div class="field">
      <span class="lbl">Como organizar os arquivos soltos</span>
      <div class="opts">
        <label class="opt"><input type="radio" name="ogMode" value="type" checked> por tipo (extensão: PDF, Imagens, …)</label>
        <label class="opt"><input type="radio" name="ogMode" value="date"> por data (ano-mês)</label>
      </div>
    </div>
    <div class="actionbar">
      <span class="count" id="ogCount">—</span>
      <button id="ogPreview" disabled>Pré-visualizar</button>
      <button id="ogApply" disabled class="btn-primary">Organizar agora</button>
      <button id="ogUndo" disabled>Desfazer</button>
    </div>
    <div id="ogResult"></div>
  </section>

  <!-- ===== EXPORTAR ===== -->
  <section class="panel" id="panel-export">
    <div class="toolbar">
      <button class="btn-primary" id="exPick">Escolher pasta</button>
      <span class="pathbox" id="exPath">Nenhuma pasta selecionada</span>
    </div>
    <div class="field">
      <div class="opts">
        <label class="opt"><input type="checkbox" id="exRec"> incluir subpastas (recursivo)</label>
        <label class="opt">Formato <select id="exFmt"><option value="txt">TXT (lista)</option><option value="csv">CSV (planilha)</option></select></label>
      </div>
    </div>
    <div class="actionbar">
      <span class="count" id="exCount">Escolha uma pasta.</span>
      <button id="exGen" disabled class="btn-primary">Gerar e baixar</button>
    </div>
  </section>

  <!-- ===== DIVIDIR PDF ===== -->
  <section class="panel" id="panel-pdf">
    <div class="pdlayout">
      <div class="pdmain">
        <div class="toolbar">
          <button class="btn-primary" id="pdPick">Escolher PDF</button>
          <span class="pathbox" id="pdPath">Nenhum PDF selecionado</span>
        </div>
        <div class="box">
          <div class="opts">
            <button id="pdDestBtn">Escolher pasta de destino</button>
            <span class="pathbox" id="pdDest">(padrão: a mesma pasta do PDF)</span>
          </div>
          <div class="opts" style="margin-top:10px">
            <label class="opt"><input type="checkbox" id="pdSubChk" checked> Salvar numa subpasta:</label>
            <input type="text" id="pdSubName" value="Separados" style="width:220px">
          </div>
        </div>
        <p class="hint">Preencha, em cada caixa, a página inicial, a final e o nome do arquivo a gerar. Caixas vazias são ignoradas.</p>
        <div id="pdBoxes"></div>
        <div style="margin-top:10px">
          <button id="pdAddBtn">+ Adicionar caixas</button>
          <span class="hint">(até 250 caixas no total)</span>
        </div>
        <div class="actionbar">
          <span class="count" id="pdCount">Escolha um PDF para começar.</span>
          <button id="pdSplit" disabled class="btn-primary">Separar PDF</button>
        </div>
        <div id="pdResult"></div>
      </div>
      <div class="pdside">
        <p style="margin:0 0 4px"><b>Pré-visualização do PDF</b></p>
        <div class="preview-box" id="pdPrev"><i>Escolha um PDF para visualizar aqui.</i></div>
      </div>
    </div>
  </section>

  <!-- ===== DIVIDIR PDF (automatico) ===== -->
  <section class="panel" id="panel-divide">
    <div class="pdlayout">
      <div class="pdmain">
        <div class="toolbar">
          <button class="btn-primary" id="dvPick">Escolher PDF</button>
          <span class="pathbox" id="dvPath">Nenhum PDF selecionado</span>
        </div>
        <div class="box">
          <div class="opts">
            <button id="dvDestBtn">Escolher pasta de destino</button>
            <span class="pathbox" id="dvDest">(padrão: a mesma pasta do PDF)</span>
          </div>
          <div class="opts" style="margin-top:10px">
            <label class="opt"><input type="checkbox" id="dvSubChk" checked> Salvar numa subpasta:</label>
            <input type="text" id="dvSubName" value="Dividido" style="width:220px">
          </div>
        </div>
        <div class="box">
          <span class="lbl">Como dividir</span>
          <div class="opts"><label class="opt"><input type="radio" name="dvMode" value="every" checked> A cada <input type="number" id="dvEvery" value="50" min="1" style="width:80px"> páginas</label></div>
          <div class="opts" style="margin-top:8px"><label class="opt"><input type="radio" name="dvMode" value="parts"> Em <input type="number" id="dvParts" value="4" min="1" style="width:70px"> partes iguais</label></div>
          <div class="opts" style="margin-top:8px"><label class="opt"><input type="radio" name="dvMode" value="burst"> Uma página por arquivo</label></div>
          <div class="opts" style="margin-top:8px">
            <label class="opt"><input type="radio" name="dvMode" value="bookmarks"> Por capítulos (marcadores)</label>
            <span class="hint" id="dvBmInfo"></span>
          </div>
          <div id="dvBmOpts" style="display:none;margin-top:10px;padding-left:22px">
            <div class="opts">
              <label class="opt">Nível: <select id="dvLevel"></select></label>
              <label class="opt"><input type="checkbox" id="dvNum"> Numerar (01, 02, …)</label>
              <label class="opt"><input type="checkbox" id="dvFront"> Incluir páginas antes do 1º marcador</label>
            </div>
          </div>
        </div>
        <div class="field" id="dvBaseField">
          <span class="lbl">Nome base dos arquivos</span>
          <input type="text" id="dvBase" placeholder="(usa o nome do PDF)">
        </div>
        <div class="actionbar">
          <span class="count" id="dvCount">Escolha um PDF para começar.</span>
          <button id="dvSplit" disabled class="btn-primary">Dividir PDF</button>
        </div>
        <div style="margin-top:10px"><b>Prévia da divisão</b></div>
        <div id="dvPreview"></div>
        <div id="dvResult"></div>
      </div>
      <div class="pdside">
        <p style="margin:0 0 4px"><b>Pré-visualização do PDF</b></p>
        <div class="preview-box" id="dvPrev"><i>Escolha um PDF para visualizar aqui.</i></div>
      </div>
    </div>
  </section>

  <!-- ===== MARCADORES ===== -->
  <section class="panel" id="panel-marks">
    <div class="pdlayout">
      <div class="pdmain">
        <div class="toolbar">
          <button class="btn-primary" id="mkPick">Escolher PDF</button>
          <span class="pathbox" id="mkPath">Nenhum PDF selecionado</span>
        </div>
        <div class="toolbar">
          <button id="mkAdd" disabled>+ Adicionar marcador</button>
          <button id="mkPaste" disabled>Colar lista</button>
          <button id="mkDivide" disabled>Dividir por estes</button>
          <button class="btn-primary" id="mkSave" disabled>Salvar marcadores</button>
        </div>
        <p class="hint">Edite título, página e nível. Use <b>⇥</b>/<b>⇤</b> para mudar o nível e <b>↑</b>/<b>↓</b> para reordenar.</p>
        <div id="mkList"></div>
      </div>
      <div class="pdside">
        <p style="margin:0 0 4px"><b>Pré-visualização do PDF</b></p>
        <div class="preview-box" id="mkPrev"><i>Escolha um PDF para visualizar aqui.</i></div>
      </div>
    </div>
  </section>

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

  <!-- ===== COMPARAR ARQUIVOS ===== -->
  <section class="panel" id="panel-compare">
    <p class="hint">Abra um arquivo de cada lado para comparar (PDF, imagem ou texto). Cada lado rola de forma independente.</p>
    <div class="cmplayout">
      <div class="cmpcol">
        <div class="toolbar"><button class="btn-primary" id="cpPickA">Escolher arquivo (esquerda)</button></div>
        <div class="pathbox" id="cpPathA" style="display:block;max-width:none">Nenhum arquivo</div>
        <div class="preview-box cmpview" id="cpA"><i>Lado esquerdo</i></div>
      </div>
      <div class="cmpcol">
        <div class="toolbar"><button class="btn-primary" id="cpPickB">Escolher arquivo (direita)</button></div>
        <div class="pathbox" id="cpPathB" style="display:block;max-width:none">Nenhum arquivo</div>
        <div class="preview-box cmpview" id="cpB"><i>Lado direito</i></div>
      </div>
    </div>
  </section>

  <hr>
  <p class="sub" style="font-size:13px">Aplicativo local — nada é enviado para a internet.</p>
</div>

<div class="toast" id="toast"></div>
<div class="modal-bg" id="modalBg"><div class="modal" id="modal"></div></div>

<script>
const INVALID=/[<>:"/\\|?*\x00-\x1f]/g;
const RESERVED=new Set(["CON","PRN","AUX","NUL","COM1","COM2","COM3","COM4","COM5","COM6","COM7","COM8","COM9","LPT1","LPT2","LPT3","LPT4","LPT5","LPT6","LPT7","LPT8","LPT9"]);
const IMG=new Set(["jpg","jpeg","png","gif","webp","bmp","svg","ico","avif","apng"]);
const TXT=new Set(["txt","md","csv","tsv","log","json","xml","html","htm","css","js","py","c","cpp","h","java","cs","go","rb","php","ini","cfg","yml","yaml","bat","ps1","sql","r"]);
const q=id=>document.getElementById(id);

function validateName(name){
  if(name===null) return [false,"vazio"];
  const t=name.trim();
  if(t==="") return [false,"Nome vazio."];
  if(t==="."||t==="..") return [false,"Nome reservado (. ou ..)."];
  const bad=name.match(INVALID);
  if(bad){const a=[...new Set(bad.filter(c=>c.trim()))].join(" ");return [false,"Caractere proibido: "+(a||"controle")];}
  if(name!==name.replace(/[ .]+$/,"")) return [false,"Não pode terminar com espaço ou ponto."];
  if(RESERVED.has(name.split(".")[0].toUpperCase())) return [false,"Nome reservado do Windows."];
  if(name.length>255) return [false,"Nome muito longo."];
  return [true,""];
}
function suggest(name){
  if(!name) return "novo nome";
  let s=name.replace(INVALID,"-").replace(/-{2,}/g,"-").replace(/[ .]+$/,"");
  if(!s) s="novo nome";
  if(RESERVED.has(s.split(".")[0].toUpperCase())){const p=s.split(".");p[0]=p[0]+"_";s=p.join(".");}
  return s.slice(0,255).replace(/[ .]+$/,"");
}
function toast(msg){const t=q("toast");t.textContent=msg;t.className="toast show";clearTimeout(t._t);t._t=setTimeout(()=>t.className="toast",2800);}
async function api(path,body){const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body||{})});return r.json();}
function esc(s){return (s+"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));}
function escA(s){return (s+"").replace(/"/g,"&quot;").replace(/</g,"&lt;");}
function baseName(p){const a=(p||"").split(/[\\/]+/).filter(Boolean);return a.length?a[a.length-1]:p;}
function sameLower(a,b){return (a||"").toLowerCase()===(b||"").toLowerCase();}
function extOf(name){const i=name.lastIndexOf(".");return i>0?name.slice(i+1).toLowerCase():"";}
async function chooseFolder(start){return api("/api/choose-folder",{start});}
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

let canUndo=false;
function setCanUndo(v){canUndo=v;["rnUndo","btUndo","xlUndo","crUndo","ogUndo"].forEach(id=>{const b=q(id);if(b)b.disabled=!v;});}

document.querySelectorAll(".tab").forEach(t=>t.addEventListener("click",()=>{
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll(".panel").forEach(x=>x.classList.remove("active"));
  t.classList.add("active");q("panel-"+t.dataset.tab).classList.add("active");
}));

// ===================================================================
//  RENOMEAR (manual) + PRE-VISUALIZACAO
// ===================================================================
const RN={root:null,current:null,parent:null,items:[],sel:-1,sortMode:'natural'};
function rnCrumbsArr(){
  const c=[{name:baseName(RN.root)||RN.root,path:RN.root}];
  if(RN.current.length>RN.root.length && RN.current.toLowerCase().startsWith(RN.root.toLowerCase())){
    let rest=RN.current.slice(RN.root.length).replace(/^[\\/]+/,""),acc=RN.root;
    rest.split(/[\\/]+/).filter(Boolean).forEach(p=>{acc=acc.replace(/[\\/]+$/,"")+"\\"+p;c.push({name:p,path:acc});});
  }
  return c;
}
function rnRenderCrumbs(){
  const el=q("rnCrumbs");if(!RN.current){el.innerHTML="";return;}
  const arr=rnCrumbsArr();
  el.innerHTML=arr.map((x,i)=>(i?'<span class="sep">›</span>':"")+(i===arr.length-1?`<b>${esc(x.name)}</b>`:`<span class="c" data-path="${escA(x.path)}">${esc(x.name)}</span>`)).join("");
  el.querySelectorAll(".c").forEach(s=>s.addEventListener("click",()=>rnNav(s.dataset.path)));
}
function rnRender(){
  const area=q("rnTable");
  if(!RN.items.length){area.innerHTML='<div class="empty">Esta pasta está vazia.</div>';q("rnLegend").textContent="";q("rnPrev").innerHTML="<i>Sem arquivos.</i>";rnUpd();return;}
  let rows="";
  RN.items.forEach((it,i)=>{
    const opener=it.is_dir?` <a href="#" data-open="${i}">[abrir]</a>`:"";
    rows+=`<tr data-row="${i}">
      <td><a href="#" class="fname ${it.is_dir?"cur dir":""}" data-prev="${i}">${esc(it.name)}</a>${opener}<div class="ftype">${it.is_dir?"pasta":"arquivo"}</div></td>
      <td><input type="text" data-i="${i}" value="${escA(it.name)}" spellcheck="false"> <span class="badge b-same" id="rnbdg-${i}">—</span><div class="row-msg" id="rnmsg-${i}"></div></td>
    </tr>`;
  });
  area.innerHTML=`<table><thead><tr><th>Arquivo / pasta</th><th>Novo nome</th></tr></thead><tbody>${rows}</tbody></table>`;
  area.querySelectorAll("input[data-i]").forEach(inp=>inp.addEventListener("input",()=>rnCheck(+inp.dataset.i)));
  area.querySelectorAll("[data-open]").forEach(a=>a.addEventListener("click",e=>{e.preventDefault();rnNav(RN.items[+a.dataset.open].path);}));
  area.querySelectorAll(".fname").forEach(a=>a.addEventListener("click",e=>{e.preventDefault();const i=+a.dataset.prev;if(RN.items[i].is_dir)rnNav(RN.items[i].path);else rnPreview(i);}));
  const nDirs=RN.items.filter(x=>x.is_dir).length;
  q("rnLegend").innerHTML=nDirs?"<i>Clique no nome de uma pasta (ou [abrir]) para entrar; no nome de um arquivo para pré-visualizar.</i>":"<i>Clique no nome de um arquivo para pré-visualizar ao lado.</i>";
  RN.items.forEach((_,i)=>rnCheck(i));
  const first=RN.items.findIndex(x=>!x.is_dir);
  if(first>=0)rnPreview(first); else q("rnPrev").innerHTML="<i>Nenhum arquivo para pré-visualizar.</i>";
}
function rnPreview(i){
  RN.sel=i;
  document.querySelectorAll("#rnTable tr[data-row]").forEach(tr=>tr.classList.toggle("sel",+tr.dataset.row===i));
  const it=RN.items[i],box=q("rnPrev"),url="/file?path="+encodeURIComponent(it.path),e=extOf(it.name);
  if(IMG.has(e))box.innerHTML='<img src="'+url+'" alt="">';
  else if(e==="pdf")box.innerHTML='<iframe src="'+url+'"></iframe>';
  else if(TXT.has(e)){box.innerHTML="<i>carregando…</i>";
    fetch(url).then(r=>r.text()).then(t=>{const pre=document.createElement("pre");pre.textContent=t.length>20000?t.slice(0,20000)+"\n…(cortado)":t;box.innerHTML="";box.appendChild(pre);}).catch(()=>box.innerHTML="<i>Não foi possível ler o arquivo.</i>");}
  else box.innerHTML="<i>Sem pré-visualização para este tipo (."+esc(e||"?")+").</i>"+(it.size?"<br><i>"+Math.max(1,Math.round(it.size/1024))+" KB</i>":"");
}
function rnCheck(i){
  const inp=document.querySelector(`#rnTable input[data-i="${i}"]`);if(!inp)return;
  const msg=q("rnmsg-"+i),bdg=q("rnbdg-"+i),val=inp.value,cur=RN.items[i].name;
  inp.classList.remove("bad");msg.innerHTML="";
  if(val.trim()===""||val===cur){bdg.className="badge b-same";bdg.textContent="—";rnUpd();return;}
  const [ok,reason]=validateName(val.trim());
  if(!ok){inp.classList.add("bad");bdg.className="badge b-err";bdg.textContent="inválido";
    const sg=suggest(val.trim());
    msg.innerHTML=`<span class="msg-err">${esc(reason)}</span><button class="sugg" data-s="${escA(sg)}">usar: ${esc(sg)}</button>`;
    msg.querySelector(".sugg").addEventListener("click",ev=>{inp.value=ev.target.dataset.s;rnCheck(i);inp.focus();});
  }else{bdg.className="badge b-ok";bdg.textContent="renomear";}
  rnDup();rnUpd();
}
function rnDup(){
  const map={};
  RN.items.forEach((it,i)=>{const inp=document.querySelector(`#rnTable input[data-i="${i}"]`);if(!inp)return;
    const v=inp.value.trim();if(v===""||v===it.name)return;const [ok]=validateName(v);if(!ok)return;
    const k=v.toLowerCase();(map[k]=map[k]||[]).push(i);});
  Object.values(map).forEach(idxs=>{if(idxs.length>1)idxs.forEach(i=>{
    const inp=document.querySelector(`#rnTable input[data-i="${i}"]`),bdg=q("rnbdg-"+i),msg=q("rnmsg-"+i);
    inp.classList.add("bad");bdg.className="badge b-err";bdg.textContent="conflito";
    if(!msg.querySelector(".dup"))msg.insertAdjacentHTML("afterbegin",'<span class="msg-err dup">Nome repetido nesta lista. </span>');});});
}
function rnCollect(){return RN.items.map((it,i)=>{const inp=document.querySelector(`#rnTable input[data-i="${i}"]`);return {current:it.name,new:inp?inp.value:""};});}
function rnPending(){let n=0,bad=0;RN.items.forEach((it,i)=>{const inp=document.querySelector(`#rnTable input[data-i="${i}"]`);if(!inp)return;const v=inp.value.trim();if(v===""||v===it.name)return;n++;if(inp.classList.contains("bad"))bad++;});return {n,bad};}
function rnUpd(){
  const {n,bad}=rnPending();
  q("rnCount").innerHTML=n===0?"Nenhuma alteração pendente":`<b>${n}</b> alteração(ões)`+(bad?` — ${bad} com problema`:"");
  q("rnRename").disabled=n===0||bad>0;q("rnPreview").disabled=n===0||bad>0;
  q("rnClear").disabled=!RN.items.length;q("rnReload").disabled=!RN.current;q("rnPaste").disabled=!RN.items.length;
  q("rnUp").disabled=!RN.current||sameLower(RN.current,RN.root);q("rnUndo").disabled=!canUndo;
}
function rnAll(){RN.items=sortItems(RN.items,RN.sortMode||'natural');rnRenderCrumbs();rnRender();rnUpd();}
function rnHasPending(){return rnPending().n>0;}
function rnNav(path){if(rnHasPending())confirmModal("Sair desta pasta?","Você digitou nomes que ainda não foram aplicados. Eles serão descartados.","Sair sem aplicar",()=>rnDo(path));else rnDo(path);}
async function rnDo(path){closeModal();const r=await api("/api/list",{path});if(r.error){toast(r.error);return;}RN.current=r.path;RN.parent=r.parent;RN.items=r.items;rnAll();}
async function rnReloadList(){const r=await api("/api/list",{path:RN.current});if(!r.error){RN.current=r.path;RN.parent=r.parent;RN.items=r.items;rnAll();}}
q("rnPick").addEventListener("click",async()=>{q("rnPick").disabled=true;const r=await chooseFolder(RN.current);q("rnPick").disabled=false;if(r.cancelled)return;if(r.error){toast(r.error);return;}RN.root=r.path;RN.current=r.path;RN.parent=r.parent;RN.items=r.items;rnAll();toast(r.items.length+" item(ns).");});
q("rnUp").addEventListener("click",()=>{if(RN.parent)rnNav(RN.parent);});
q("rnReload").addEventListener("click",()=>{if(RN.current)rnNav(RN.current);});
q("rnClear").addEventListener("click",()=>{document.querySelectorAll("#rnTable input[data-i]").forEach(inp=>inp.value=RN.items[+inp.dataset.i].name);RN.items.forEach((_,i)=>rnCheck(i));toast("Nomes restaurados.");});
q("rnPaste").addEventListener("click",()=>{
  q("modal").innerHTML=`<h3>Colar nomes</h3><p class="hint">Cole uma coluna (um nome por linha, ex.: do Excel). Os nomes preenchem os campos de cima para baixo.</p><div class="field"><textarea id="pasteArea" placeholder="Nome 1&#10;Nome 2&#10;Nome 3"></textarea></div><div class="acts"><button onclick="closeModal()">Cancelar</button><button class="btn-primary" id="pasteFill">Preencher</button></div>`;
  openModal();q("pasteArea").focus();
  q("pasteFill").addEventListener("click",()=>{
    const lines=q("pasteArea").value.split(/\r?\n/);
    document.querySelectorAll("#rnTable input[data-i]").forEach((inp,idx)=>{if(idx<lines.length&&lines[idx].trim()!=="")inp.value=lines[idx].trim();});
    RN.items.forEach((_,i)=>rnCheck(i));closeModal();toast("Campos preenchidos.");});
});
q("rnPreview").addEventListener("click",async()=>{const r=await api("/api/validate",{folder:RN.current,renames:rnCollect()});showPreview(r,false,rnApply);});
q("rnRename").addEventListener("click",async()=>{const r=await api("/api/validate",{folder:RN.current,renames:rnCollect()});showPreview(r,true,rnApply);});
async function rnApply(){const r=await api("/api/rename",{folder:RN.current,renames:rnCollect()});closeModal();if(!r.ok){toast(r.error||"Falha ao renomear.");return;}setCanUndo(!!r.can_undo);RN.current=r.path;RN.parent=r.parent;RN.items=r.items;rnAll();toast(r.renamed+" renomeado(s).");}
q("rnUndo").addEventListener("click",undoLast);
q("rnSort").querySelectorAll(".sortbtn").forEach(b=>b.addEventListener("click",()=>{RN.sortMode=b.dataset.sort;RN.items=sortItems(RN.items,RN.sortMode);rnRenderCrumbs();rnRender();rnUpd();q("rnSort").querySelectorAll(".sortbtn").forEach(x=>x.classList.toggle("on",x===b));}));

// ===================================================================
//  RENOMEAR EM LOTE
// ===================================================================
const BT={folder:null,items:[],sortMode:'natural'};
function noAccents(s){return s.normalize("NFD").replace(/[̀-ͯ]/g,"");}
function applyCase(s,mode){
  if(mode==="upper")return s.toUpperCase();
  if(mode==="lower")return s.toLowerCase();
  if(mode==="title")return s.toLowerCase().replace(/\b\p{L}/gu,c=>c.toUpperCase());
  if(mode==="sentence")return s.toLowerCase().replace(/^\s*\p{L}/u,c=>c.toUpperCase());
  return s;
}
function btScope(){return (document.querySelector('input[name=btScope]:checked')||{}).value||"all";}
function btCompute(){
  const find=q("btFind").value,repl=q("btRepl").value,pre=q("btPre").value,suf=q("btSuf").value;
  const cs=q("btCase").value,sp=q("btSpace").value,acc=q("btAccents").checked,keepExt=q("btExt").checked;
  const num=q("btNum").checked,start=parseInt(q("btNumStart").value||"1",10),digits=Math.max(1,parseInt(q("btNumDigits").value||"2",10));
  const pos=q("btNumPos").value,sep=q("btNumSep").value,scope=btScope();
  let idx=start,rows=[];
  BT.items.forEach(it=>{
    const inScope=scope==="all"||(scope==="files"&&!it.is_dir)||(scope==="dirs"&&it.is_dir);
    if(!inScope){rows.push({current:it.name,new:it.name,changed:false,valid:true,is_dir:it.is_dir});return;}
    let base=it.name,ext="";
    if(keepExt&&!it.is_dir){const dot=it.name.lastIndexOf(".");if(dot>0){base=it.name.slice(0,dot);ext=it.name.slice(dot);}}
    if(find!=="")base=base.split(find).join(repl);
    if(acc)base=noAccents(base);
    if(sp==="remove")base=base.replace(/\s+/g,"");else if(sp)base=base.replace(/\s+/g,sp);
    base=applyCase(base,cs);
    let core=pre+base+suf;
    if(num){const nn=String(idx).padStart(digits,"0");core=pos==="prefix"?(nn+sep+core):(core+sep+nn);idx++;}
    const nn=core+ext,changed=nn!==it.name&&nn.trim()!=="";
    const [valid]=changed?validateName(nn.trim()):[true];
    rows.push({current:it.name,new:nn,changed,valid,is_dir:it.is_dir});
  });
  const seen={};
  rows.forEach(r=>{if(r.changed&&r.valid){const k=r.new.toLowerCase();(seen[k]=seen[k]||[]).push(r);}});
  Object.values(seen).forEach(g=>{if(g.length>1)g.forEach(r=>{r.valid=false;r.dup=true;});});
  return rows;
}
function btRender(){
  BT.items=sortItems(BT.items,BT.sortMode||'natural');
  const area=q("btTable");
  if(!BT.items.length){area.innerHTML='<div class="empty">Escolha uma pasta para ver a prévia.</div>';q("btCount").textContent="—";q("btApply").disabled=true;return;}
  const rows=btCompute();let html="";
  rows.forEach((r,i)=>{
    let st='<span class="badge b-same">—</span>';
    if(r.changed&&r.valid)st='<span class="badge b-ok">renomear</span>';
    else if(r.changed&&!r.valid)st=`<span class="badge b-err">${r.dup?"conflito":"inválido"}</span>`;
    html+=`<tr><td class="colw-num">${i+1}</td>
      <td><span class="${r.is_dir?"cur dir":""}">${esc(r.current)}</span> <span class="tag">(${r.is_dir?"pasta":"arquivo"})</span></td>
      <td class="newcell">${r.changed?"<b>"+esc(r.new)+"</b>":'<span style="color:#999">'+esc(r.current)+"</span>"}</td>
      <td>${st}</td></tr>`;
  });
  area.innerHTML=`<table><thead><tr><th class="colw-num">#</th><th>Item atual</th><th>Vai virar</th><th>Status</th></tr></thead><tbody>${html}</tbody></table>`;
  const n=rows.filter(r=>r.changed).length,bad=rows.filter(r=>r.changed&&!r.valid).length;
  q("btCount").innerHTML=n===0?"Nenhuma alteração":`<b>${n}</b> alteração(ões)`+(bad?` — ${bad} com problema`:"");
  q("btApply").disabled=n===0||bad>0;
}
["btFind","btRepl","btPre","btSuf","btCase","btSpace","btAccents","btExt","btNum","btNumStart","btNumDigits","btNumPos","btNumSep"].forEach(id=>{const el=q(id);el.addEventListener("input",btRender);el.addEventListener("change",btRender);});
document.querySelectorAll('input[name=btScope]').forEach(r=>r.addEventListener("change",btRender));
async function btLoad(path){const r=await api("/api/list",{path});if(r.error){toast(r.error);return;}BT.folder=r.path;BT.items=r.items;q("btPath").textContent=r.path;q("btPath").title=r.path;q("btReload").disabled=false;btRender();}
q("btPick").addEventListener("click",async()=>{q("btPick").disabled=true;const r=await chooseFolder(BT.folder);q("btPick").disabled=false;if(r.cancelled)return;if(r.error){toast(r.error);return;}BT.folder=r.path;BT.items=r.items;q("btPath").textContent=r.path;q("btPath").title=r.path;q("btReload").disabled=false;btRender();toast(r.items.length+" item(ns).");});
q("btReload").addEventListener("click",()=>{if(BT.folder)btLoad(BT.folder);});
async function btReloadList(){if(BT.folder)await btLoad(BT.folder);}
q("btApply").addEventListener("click",()=>{
  const rows=btCompute().filter(r=>r.changed);
  const li=rows.map(r=>`<div class="line">${esc(r.current)} → <b>${esc(r.new)}</b></div>`).join("");
  q("modal").innerHTML=`<h3>Confirmar renomeação em lote</h3><p class="hint">${rows.length} item(ns) serão renomeados:</p><div class="pv">${li}</div><div class="acts"><button onclick="closeModal()">Cancelar</button><button class="btn-primary" id="btGo">Aplicar</button></div>`;
  openModal();
  q("btGo").addEventListener("click",async()=>{q("btGo").disabled=true;q("btGo").textContent="Renomeando…";
    const r=await api("/api/rename",{folder:BT.folder,renames:rows.map(x=>({current:x.current,new:x.new}))});
    closeModal();if(!r.ok){toast(r.error||"Falha ao renomear.");return;}
    setCanUndo(!!r.can_undo);BT.items=r.items;btRender();toast(r.renamed+" renomeado(s).");});
});
q("btUndo").addEventListener("click",undoLast);
q("btSort").querySelectorAll(".sortbtn").forEach(b=>b.addEventListener("click",()=>{BT.sortMode=b.dataset.sort;btRender();q("btSort").querySelectorAll(".sortbtn").forEach(x=>x.classList.toggle("on",x===b));}));

// ===================================================================
//  CRIAR
// ===================================================================
const CR={folder:null};
function crKind(){return (document.querySelector('input[name=crKind]:checked')||{}).value||"folder";}
function crNamesList(){
  let lines=q("crNames").value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
  if(q("crNum").checked){const d=Math.max(2,String(lines.length).length);lines=lines.map((n,i)=>String(i+1).padStart(d,"0")+". "+n);}
  return lines;
}
function crUpd(){const n=crNamesList().length;q("crCount").innerHTML=n===0?"Digite ao menos um nome":`<b>${n}</b> ${crKind()==="folder"?"pasta(s)":"arquivo(s)"} a criar`;q("crCreate").disabled=!CR.folder||n===0;q("crUndo").disabled=!canUndo;}
q("crNames").addEventListener("input",crUpd);
q("crNum").addEventListener("change",crUpd);
document.querySelectorAll('input[name=crKind]').forEach(r=>r.addEventListener("change",crUpd));
q("crPick").addEventListener("click",async()=>{q("crPick").disabled=true;const r=await chooseFolder(CR.folder);q("crPick").disabled=false;if(r.cancelled)return;if(r.error){toast(r.error);return;}CR.folder=r.path;q("crPath").textContent=r.path;q("crPath").title=r.path;crUpd();});
q("crCreate").addEventListener("click",async()=>{
  const names=crNamesList(),kind=crKind();
  const r=await api("/api/create",{folder:CR.folder,kind,names});
  if(r.error){toast(r.error);return;}
  setCanUndo(!!r.can_undo);
  let html=`<div class="box"><b>${r.created.length}</b> ${kind==="folder"?"pasta(s)":"arquivo(s)"} criado(s).`;
  if(r.problems&&r.problems.length)html+=`<div class="field"><span class="lbl">Não criados (${r.problems.length})</span>`+r.problems.map(p=>`<div class="line"><b>${esc(p.name)}</b> — ${esc(p.reason)}${p.suggestion?` <i>(sugestão: ${esc(p.suggestion)})</i>`:""}</div>`).join("")+`</div>`;
  html+=`</div>`;q("crResult").innerHTML=html;toast(r.created.length+" criado(s).");crUpd();
});
q("crUndo").addEventListener("click",undoLast);

// ===================================================================
//  ORGANIZAR
// ===================================================================
const OG={folder:null};
function ogMode(){return (document.querySelector('input[name=ogMode]:checked')||{}).value||"type";}
function ogUpd(){q("ogPreview").disabled=!OG.folder;q("ogApply").disabled=!OG.folder;q("ogUndo").disabled=!canUndo;}
q("ogPick").addEventListener("click",async()=>{q("ogPick").disabled=true;const r=await chooseFolder(OG.folder);q("ogPick").disabled=false;if(r.cancelled)return;if(r.error){toast(r.error);return;}OG.folder=r.path;q("ogPath").textContent=r.path;q("ogPath").title=r.path;q("ogResult").innerHTML="";q("ogCount").textContent="—";ogUpd();});
document.querySelectorAll('input[name=ogMode]').forEach(r=>r.addEventListener("change",()=>{q("ogResult").innerHTML="";q("ogCount").textContent="—";}));
function ogClearPreview(){q("ogResult").innerHTML="";q("ogCount").textContent="—";}
q("ogPreview").addEventListener("click",async()=>{
  const r=await api("/api/organize",{folder:OG.folder,mode:ogMode(),apply:false});
  if(r.error){toast(r.error);return;}
  if(!r.total){q("ogResult").innerHTML='<div class="box">Nenhum arquivo solto para organizar.</div>';q("ogCount").textContent="0 arquivos";return;}
  const cats=Object.entries(r.counts).sort((a,b)=>a[0].localeCompare(b[0]));
  let html=`<table><thead><tr><th>Arquivo</th><th>Irá para a pasta</th></tr></thead><tbody>`;
  r.plan.forEach(p=>{html+=`<tr><td>${esc(p.name)}</td><td><b>${esc(p.target)}</b></td></tr>`;});
  html+=`</tbody></table>`;q("ogResult").innerHTML='<div style="margin-top:10px">'+html+'</div>';
  q("ogCount").innerHTML=`<b>${r.total}</b> arquivo(s) em ${cats.length} pasta(s): `+cats.map(c=>`${esc(c[0])} (${c[1]})`).join(", ");
});
q("ogApply").addEventListener("click",()=>{
  confirmModal("Organizar agora?","Os arquivos soltos serão movidos para subpastas. Você pode desfazer depois.","Organizar",async()=>{
    closeModal();const r=await api("/api/organize",{folder:OG.folder,mode:ogMode(),apply:true});
    if(r.error){toast(r.error);return;}
    setCanUndo(!!r.can_undo);q("ogResult").innerHTML=`<div class="box"><b>${r.moved}</b> arquivo(s) organizado(s).</div>`;
    q("ogCount").textContent=r.moved+" movido(s).";ogUpd();toast(r.moved+" organizado(s).");});
});
q("ogUndo").addEventListener("click",undoLast);

// ===================================================================
//  EXPORTAR
// ===================================================================
const EX={folder:null};
q("exPick").addEventListener("click",async()=>{q("exPick").disabled=true;const r=await chooseFolder(EX.folder);q("exPick").disabled=false;if(r.cancelled)return;if(r.error){toast(r.error);return;}EX.folder=r.path;q("exPath").textContent=r.path;q("exPath").title=r.path;q("exCount").textContent=r.items.length+" item(ns) no nível atual.";q("exGen").disabled=false;});
q("exGen").addEventListener("click",async()=>{
  const fmt=q("exFmt").value,rec=q("exRec").checked;
  const r=await api("/api/export",{folder:EX.folder,recursive:rec,format:fmt});
  if(!r.ok){toast(r.error||"Falha ao gerar.");return;}
  let content=r.content;if(fmt==="csv")content="﻿"+content;
  const blob=new Blob([content],{type:fmt==="csv"?"text/csv;charset=utf-8":"text/plain;charset=utf-8"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=r.filename;document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(a.href);
  toast("Arquivo gerado (verifique os Downloads).");
});

// ===================================================================
//  COLAR DO EXCEL
// ===================================================================
const XL={folder:null,items:[],filtered:[],sortMode:'natural'};
function xlScope(){return (document.querySelector('input[name=xlScope]:checked')||{}).value||"all";}
function xlBuild(){
  XL.items=sortItems(XL.items,XL.sortMode||'natural');
  const sc=xlScope();
  XL.filtered=XL.items.filter(it=>sc==="all"||(sc==="files"&&!it.is_dir)||(sc==="dirs"&&it.is_dir));
  q("xlCurrent").value=XL.filtered.map(it=>it.name).join("\n");
  q("xlCurCount").textContent=XL.filtered.length?("("+XL.filtered.length+" item(ns))"):"";
  q("xlCopy").disabled=!XL.filtered.length;
  xlUpd();
}
function xlNewLines(){return q("xlNew").value.split(/\r?\n/).map(l=>l.includes("\t")?l.split("\t").pop():l);}
function xlRenames(){const lines=xlNewLines();return XL.filtered.map((it,i)=>({current:it.name,new:(lines[i]||"").trim()}));}
function xlUpd(){
  const rs=xlRenames();let n=0,filled=0;
  rs.forEach(r=>{if(r.new!==""){filled++;if(r.new!==r.current)n++;}});
  q("xlCount").innerHTML=XL.filtered.length?`<b>${n}</b> alteração(ões) — ${filled}/${XL.filtered.length} preenchidos`:"—";
  q("xlRename").disabled=!XL.folder||n===0;
  q("xlReload").disabled=!XL.folder;
  q("xlUndo").disabled=!canUndo;
}
q("xlNew").addEventListener("input",xlUpd);
document.querySelectorAll('input[name=xlScope]').forEach(r=>r.addEventListener("change",xlBuild));
async function xlLoad(path){const r=await api("/api/list",{path});if(r.error){toast(r.error);return;}XL.folder=r.path;XL.items=r.items;q("xlPath").textContent=r.path;q("xlPath").title=r.path;xlBuild();}
async function xlReload(){if(XL.folder)await xlLoad(XL.folder);}
q("xlPick").addEventListener("click",async()=>{q("xlPick").disabled=true;const r=await chooseFolder(XL.folder);q("xlPick").disabled=false;if(r.cancelled)return;if(r.error){toast(r.error);return;}XL.folder=r.path;XL.items=r.items;q("xlPath").textContent=r.path;q("xlPath").title=r.path;xlBuild();toast(r.items.length+" item(ns).");});
q("xlReload").addEventListener("click",()=>{if(XL.folder)xlLoad(XL.folder);});
q("xlCopy").addEventListener("click",()=>{const txt=XL.filtered.map(it=>it.name).join("\n");
  if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(txt).then(()=>toast("Nomes atuais copiados.")).catch(()=>{q("xlCurrent").select();toast("Selecionado — use Ctrl+C.");});
  else{q("xlCurrent").select();toast("Selecionado — use Ctrl+C.");}});
q("xlRename").addEventListener("click",async()=>{const r=await api("/api/validate",{folder:XL.folder,renames:xlRenames()});showPreview(r,true,xlApply);});
async function xlApply(){const r=await api("/api/rename",{folder:XL.folder,renames:xlRenames()});closeModal();if(!r.ok){toast(r.error||"Falha ao renomear.");return;}setCanUndo(!!r.can_undo);XL.items=r.items;q("xlNew").value="";xlBuild();toast(r.renamed+" renomeado(s).");}
q("xlUndo").addEventListener("click",undoLast);
q("xlSort").querySelectorAll(".sortbtn").forEach(b=>b.addEventListener("click",()=>{XL.sortMode=b.dataset.sort;xlBuild();q("xlSort").querySelectorAll(".sortbtn").forEach(x=>x.classList.toggle("on",x===b));}));
// sincroniza rolagem das duas colunas
["xlCurrent","xlNew"].forEach((a,i)=>{const b=["xlCurrent","xlNew"][1-i];q(a).addEventListener("scroll",()=>{const o=q(b);if(o.scrollTop!==q(a).scrollTop)o.scrollTop=q(a).scrollTop;});});

// ===================================================================
//  DIVIDIR PDF
// ===================================================================
const PD={pdf:null,pages:0,dest:null,data:[]};
function pdReadInputs(){
  PD.data.forEach((b,i)=>{
    const s=q("pd-s-"+i),e=q("pd-e-"+i),n=q("pd-n-"+i);
    if(s)b.start=s.value; if(e)b.end=e.value; if(n)b.name=n.value;
  });
}
function pdRender(){
  const area=q("pdBoxes");let rows="";
  PD.data.forEach((b,i)=>{
    rows+=`<tr>
      <td class="colw-num">${i+1}</td>
      <td><input type="number" class="pgin" id="pd-s-${i}" min="1" value="${escA(b.start)}" placeholder="1"></td>
      <td><input type="number" class="pgin" id="pd-e-${i}" min="1" value="${escA(b.end)}" placeholder="${PD.pages||''}"></td>
      <td><input type="text" id="pd-n-${i}" value="${escA(b.name)}" placeholder="ex.: Parte ${i+1}" spellcheck="false"></td>
      <td><span class="badge b-same" id="pd-b-${i}">—</span></td>
    </tr>`;
  });
  area.innerHTML=`<table><thead><tr><th class="colw-num">#</th><th>Pág. inicial</th><th>Pág. final</th><th>Nome do arquivo (.pdf)</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table>`;
  area.querySelectorAll("input").forEach(inp=>inp.addEventListener("input",()=>{pdReadInputs();pdValidate();}));
  pdValidate();
}
function pdAdd(n){
  pdReadInputs();
  n=Math.max(0,Math.min(n,250-PD.data.length));
  for(let i=0;i<n;i++)PD.data.push({start:"",end:"",name:""});
  pdRender();
  if(PD.data.length>=250)toast("Limite de 250 caixas atingido.");
}
function pdValidate(){
  let valid=0,bad=0,filled=0;const seen={};
  PD.data.forEach((b,i)=>{
    const bdg=q("pd-b-"+i);if(!bdg)return;
    const s=(b.start+"").trim(),e=(b.end+"").trim(),n=(b.name+"").trim();
    if(!(s||e||n)){bdg.className="badge b-same";bdg.textContent="—";return;}
    filled++;
    let err="";
    if(!s||!e||!n)err="preencha início, fim e nome";
    else{
      const si=parseInt(s,10),ei=parseInt(e,10);
      if(isNaN(si)||isNaN(ei))err="páginas inválidas";
      else if(PD.pages&&(si<1||ei>PD.pages))err="fora de 1–"+PD.pages;
      else if(si>ei)err="início maior que fim";
      else{
        let fn=n.toLowerCase().endsWith(".pdf")?n:n+".pdf";
        const [ok,reason]=validateName(fn);
        if(!ok)err=reason;
        else{const k=fn.toLowerCase();if(seen[k])err="nome repetido";else seen[k]=1;}
      }
    }
    if(err){bad++;bdg.className="badge b-err";bdg.textContent="⚠ "+err;}
    else{valid++;bdg.className="badge b-ok";bdg.textContent="ok";}
  });
  const lbl=q("pdCount");
  if(!PD.pdf)lbl.textContent="Escolha um PDF para começar.";
  else if(valid)lbl.innerHTML=`<b>${valid}</b> arquivo(s) a gerar`+(bad?` — ${bad} com problema`:"");
  else lbl.textContent=filled?"Há caixas com problema":"Preencha ao menos uma caixa";
  q("pdSplit").disabled=!PD.pdf||valid===0||bad>0;
}
q("pdPick").addEventListener("click",async()=>{
  q("pdPick").disabled=true;
  const r=await api("/api/choose-file",{start:PD.dest});
  q("pdPick").disabled=false;
  if(r.cancelled)return;if(r.error){toast(r.error);return;}
  const info=await api("/api/pdf-info",{path:r.path});
  if(!info.ok){toast(info.error||"Não consegui ler este PDF.");return;}
  PD.pdf=r.path;PD.pages=info.pages;PD.dest=info.folder;
  q("pdPath").textContent=info.name+"  ("+info.pages+" páginas)";q("pdPath").title=r.path;
  q("pdDest").textContent=info.folder;q("pdDest").title=info.folder;
  q("pdPrev").innerHTML='<iframe src="/file?path='+encodeURIComponent(r.path)+'"></iframe>';
  pdRender();
  toast("PDF com "+info.pages+" páginas carregado.");
});
q("pdDestBtn").addEventListener("click",async()=>{
  const r=await chooseFolder(PD.dest);
  if(r.cancelled)return;if(r.error){toast(r.error);return;}
  PD.dest=r.path;q("pdDest").textContent=r.path;q("pdDest").title=r.path;
});
q("pdAddBtn").addEventListener("click",()=>{
  if(PD.data.length>=250){toast("Já são 250 caixas (máximo).");return;}
  const restam=250-PD.data.length;
  q("modal").innerHTML=`<h3>Adicionar caixas</h3><p class="hint">Quantas caixas quer adicionar? (máximo agora: ${restam})</p><div class="field"><input type="number" id="pdAddN" min="1" max="${restam}" value="5" style="width:120px"></div><div class="acts"><button onclick="closeModal()">Cancelar</button><button class="btn-primary" id="pdAddGo">Adicionar</button></div>`;
  openModal();q("pdAddN").focus();
  q("pdAddGo").addEventListener("click",()=>{const n=parseInt(q("pdAddN").value||"0",10)||0;pdAdd(n);closeModal();});
});
q("pdSplit").addEventListener("click",async()=>{
  pdReadInputs();
  const sub=q("pdSubChk").checked?((q("pdSubName").value||"Separados").trim()):"";
  const ranges=PD.data.filter(b=>((b.start+"").trim()||(b.end+"").trim()||(b.name+"").trim()))
    .map(b=>({start:b.start,end:b.end,name:b.name}));
  q("pdSplit").disabled=true;q("pdSplit").textContent="Separando…";
  const r=await api("/api/pdf-split",{pdf:PD.pdf,dest:PD.dest,subfolder:sub,ranges});
  q("pdSplit").textContent="Separar PDF";pdValidate();
  if(!r.ok){
    const msg=(r.problems||[]).map(p=>"Caixa "+p.row+": "+esc(p.reason)).join("<br>")||esc(r.error||"Falha ao separar.");
    q("modal").innerHTML=`<h3>Há itens a corrigir</h3><div class="pv">${msg}</div><div class="acts"><button onclick="closeModal()">Fechar</button></div>`;openModal();return;
  }
  let html=`<div class="box"><b>${r.results.length}</b> arquivo(s) gerado(s) em:<br><i>${esc(r.dest)}</i><div class="field">`+r.results.map(x=>`<div class="line">${esc(x.name)} — páginas ${x.pages} (${x.count} pág.)</div>`).join("")+`</div></div>`;
  q("pdResult").innerHTML=html;toast("✓ "+r.results.length+" PDF(s) gerado(s).");
});
PD.data=[{start:"",end:"",name:""},{start:"",end:"",name:""},{start:"",end:"",name:""},{start:"",end:"",name:""},{start:"",end:"",name:""}];
pdRender();

// ===================================================================
//  DIVIDIR PDF (automatico)
// ===================================================================
const DV={pdf:null,pages:0,dest:null,outline:[],maxdepth:0,name:"",_plan:[]};
function dvMode(){return (document.querySelector('input[name=dvMode]:checked')||{}).value||"every";}
function pad(n,w){return String(n).padStart(w,"0");}
function sanTitle(t){t=(t||"").replace(/[<>:"/\\|?*\x00-\x1f]/g,"_").replace(/\s+/g," ").trim().replace(/[ .]+$/,"");return t||"(sem titulo)";}
function dvComputePlan(){
  if(!DV.pdf||!DV.pages)return [];
  const total=DV.pages,mode=dvMode(),base=(q("dvBase").value||DV.name||"arquivo").trim(),W=String(total).length;
  let plan=[];
  if(mode==="every"){
    let X=Math.max(1,parseInt(q("dvEvery").value||"1",10));
    for(let s=1;s<=total;s+=X){const e=Math.min(s+X-1,total);plan.push({name:base+" - "+pad(s,W)+"-"+pad(e,W),start:s,end:e});}
  }else if(mode==="parts"){
    let P=Math.max(1,Math.min(parseInt(q("dvParts").value||"1",10),total)),bse=Math.floor(total/P),rem=total%P,s=1,PW=String(P).length;
    for(let i=0;i<P;i++){const size=bse+(i<rem?1:0);if(size<=0)break;const e=s+size-1;plan.push({name:base+" - parte "+pad(i+1,PW),start:s,end:e});s=e+1;}
  }else if(mode==="burst"){
    for(let i=1;i<=total;i++)plan.push({name:base+" - pag "+pad(i,W),start:i,end:i});
  }else if(mode==="bookmarks"){
    const L=parseInt(q("dvLevel").value||"1",10),bnd=DV.outline.filter(b=>b.depth<=L);
    if(q("dvFront").checked && bnd.length && bnd[0].page>1)
      plan.push({name:"(inicio)",start:1,end:bnd[0].page-1,includes:[]});
    for(let i=0;i<bnd.length;i++){
      const s=bnd[i].page,e=(i+1<bnd.length)?bnd[i+1].page-1:total,ee=(e<s?s:e);
      const inc=DV.outline.filter(b=>b.depth>L && b.page>=s && b.page<=ee).map(b=>({t:b.title,p:b.page}));
      plan.push({name:sanTitle(bnd[i].title),start:s,end:ee,includes:inc});
    }
  }
  const seen={};
  plan.forEach(r=>{let nm=r.name,k=nm.toLowerCase(),n=2;while(seen[k]){nm=r.name+" ("+n+")";k=nm.toLowerCase();n++;}r.name=nm;seen[k]=1;});
  return plan;
}
function dvRenderPreview(){
  const wrap=q("dvPreview");
  if(!DV.pdf){wrap.innerHTML="";q("dvCount").textContent="Escolha um PDF para começar.";q("dvSplit").disabled=true;return;}
  const plan=dvComputePlan();DV._plan=plan;
  if(!plan.length){wrap.innerHTML='<div class="hint">Nada para dividir.</div>';q("dvCount").textContent="0 arquivos";q("dvSplit").disabled=true;return;}
  const bm=dvMode()==="bookmarks",num=bm&&q("dvNum").checked,NW=Math.max(2,String(plan.length).length);
  let html='<div class="planwrap">';
  plan.forEach((r,i)=>{
    const prefix=num?('<span class="planseq">'+pad(i+1,NW)+'. </span>'):'';
    if(bm){
      html+='<div class="planrow">'+prefix+'<input class="bmname" data-i="'+i+'" value="'+escA(r.name)+'" spellcheck="false"><span class="pdfext">.pdf</span>'
          +'<div class="planpg">páginas '+r.start+'–'+r.end+' · '+(r.end-r.start+1)+' pág.</div>';
      if(r.includes&&r.includes.length){
        const mx=6,shown=r.includes.slice(0,mx).map(x=>esc(x.t)+' (p.'+x.p+')').join(' · ');
        const extra=r.includes.length>mx?(' · +'+(r.includes.length-mx)+' mais'):'';
        html+='<div class="planinc">inclui: '+shown+extra+'</div>';
      }
      html+='</div>';
    }else{
      html+='<div class="planrow"><b>'+esc(r.name)+'.pdf</b><div class="planpg">páginas '+r.start+'–'+r.end+' · '+(r.end-r.start+1)+' pág.</div></div>';
    }
  });
  html+='</div>';wrap.innerHTML=html;
  const pgTotal=plan.reduce((a,r)=>a+(r.end-r.start+1),0);
  q("dvCount").innerHTML='<b>'+plan.length+'</b> arquivo(s) · '+pgTotal+' páginas no total'+(plan.length>200?' — atenção: muitos arquivos':'');
  q("dvSplit").disabled=false;
}
function dvFinalRanges(){
  const plan=DV._plan||[];
  if(dvMode()!=="bookmarks")return plan.map(r=>({start:r.start,end:r.end,name:r.name}));
  const num=q("dvNum").checked,NW=Math.max(2,String(plan.length).length);
  return plan.map((r,i)=>{
    let nm=r.name;const inp=document.querySelector('.bmname[data-i="'+i+'"]');
    if(inp&&inp.value.trim())nm=inp.value.trim();
    if(num)nm=pad(i+1,NW)+" - "+nm;
    return {start:r.start,end:r.end,name:nm};
  });
}
function dvBuildLevels(){
  const sel=q("dvLevel");sel.innerHTML="";
  const bmRadio=document.querySelector('input[name=dvMode][value=bookmarks]');
  if(!DV.outline.length){
    q("dvBmInfo").textContent="(este PDF não tem marcadores)";bmRadio.disabled=true;
    if(bmRadio.checked)document.querySelector('input[name=dvMode][value=every]').checked=true;
    dvBmVis();return;
  }
  bmRadio.disabled=false;q("dvBmInfo").textContent=DV.maxdepth+" nível(is) de marcadores";
  for(let L=1;L<=DV.maxdepth;L++){const c=DV.outline.filter(b=>b.depth<=L).length;const o=document.createElement("option");o.value=L;o.textContent="nível "+L+" ("+c+" arquivos)";sel.appendChild(o);}
  dvBmVis();
}
function dvBmVis(){
  const bm=dvMode()==="bookmarks";
  q("dvBmOpts").style.display=(bm&&DV.outline.length)?"block":"none";
  q("dvBaseField").style.display=bm?"none":"block";
}
q("dvPick").addEventListener("click",async()=>{
  q("dvPick").disabled=true;
  const r=await api("/api/choose-file",{start:DV.dest});
  q("dvPick").disabled=false;
  if(r.cancelled)return;if(r.error){toast(r.error);return;}
  const info=await api("/api/pdf-info",{path:r.path});
  if(!info.ok){toast(info.error||"Não consegui ler este PDF.");return;}
  DV.pdf=r.path;DV.pages=info.pages;DV.dest=info.folder;DV.name=info.name.replace(/\.pdf$/i,"");
  q("dvPath").textContent=info.name+"  ("+info.pages+" páginas)";q("dvPath").title=r.path;
  q("dvDest").textContent=info.folder;q("dvDest").title=info.folder;
  q("dvBase").value=DV.name;
  q("dvPrev").innerHTML='<iframe src="/file?path='+encodeURIComponent(r.path)+'"></iframe>';
  const ol=await api("/api/pdf-outline",{path:r.path});
  DV.outline=(ol.ok&&ol.bookmarks)?ol.bookmarks:[];DV.maxdepth=(ol.ok&&ol.maxdepth)?ol.maxdepth:0;
  dvBuildLevels();dvRenderPreview();
  toast("PDF com "+info.pages+" páginas carregado.");
});
q("dvDestBtn").addEventListener("click",async()=>{const r=await chooseFolder(DV.dest);if(r.cancelled)return;if(r.error){toast(r.error);return;}DV.dest=r.path;q("dvDest").textContent=r.path;q("dvDest").title=r.path;});
document.querySelectorAll('input[name=dvMode]').forEach(r=>r.addEventListener("change",()=>{dvBmVis();dvRenderPreview();}));
["dvEvery","dvParts","dvBase"].forEach(id=>q(id).addEventListener("input",dvRenderPreview));
["dvLevel","dvNum","dvFront"].forEach(id=>q(id).addEventListener("change",dvRenderPreview));
q("dvSplit").addEventListener("click",()=>{
  const ranges=dvFinalRanges();if(!ranges.length)return;
  const go=async()=>{
    closeModal();
    const sub=q("dvSubChk").checked?((q("dvSubName").value||"Dividido").trim()):"";
    q("dvSplit").disabled=true;q("dvSplit").textContent="Dividindo…";
    const r=await api("/api/pdf-split",{pdf:DV.pdf,dest:DV.dest,subfolder:sub,auto:true,ranges});
    q("dvSplit").textContent="Dividir PDF";dvRenderPreview();
    if(!r.ok){const msg=(r.problems||[]).map(p=>esc(p.reason)).join("<br>")||esc(r.error||"Falha.");q("modal").innerHTML='<h3>Não foi possível dividir</h3><div class="pv">'+msg+'</div><div class="acts"><button onclick="closeModal()">Fechar</button></div>';openModal();return;}
    q("dvResult").innerHTML='<div class="box"><b>'+r.results.length+'</b> arquivo(s) gerado(s) em:<br><i>'+esc(r.dest)+'</i></div>';
    toast("✓ "+r.results.length+" PDF(s) gerado(s).");
  };
  if(ranges.length>100)confirmModal("Gerar "+ranges.length+" arquivos?","Isso vai criar "+ranges.length+" PDFs na pasta de destino. Deseja continuar?","Sim, dividir",go);
  else go();
});

// ===================================================================
//  COMPARAR ARQUIVOS
// ===================================================================
function cpRender(side,path){
  const box=q(side==="A"?"cpA":"cpB"),url="/file?path="+encodeURIComponent(path),e=extOf(path.split(/[\\/]/).pop());
  if(IMG.has(e))box.innerHTML='<img src="'+url+'" alt="">';
  else if(e==="pdf")box.innerHTML='<iframe src="'+url+'"></iframe>';
  else if(TXT.has(e)){box.innerHTML="<i>carregando…</i>";fetch(url).then(r=>r.text()).then(t=>{const pre=document.createElement("pre");pre.textContent=t.length>50000?t.slice(0,50000)+"\n…(cortado)":t;box.innerHTML="";box.appendChild(pre);}).catch(()=>box.innerHTML="<i>não foi possível ler.</i>");}
  else box.innerHTML="<i>Sem pré-visualização para este tipo de arquivo.</i>";
}
async function cpPick(side){
  const r=await api("/api/choose-file",{kind:"anyfile"});
  if(r.cancelled)return;if(r.error){toast(r.error);return;}
  const nm=r.path.split(/[\\/]/).pop();
  const lbl=q(side==="A"?"cpPathA":"cpPathB");lbl.textContent=nm;lbl.title=r.path;
  cpRender(side,r.path);
}
q("cpPickA").addEventListener("click",()=>cpPick("A"));
q("cpPickB").addEventListener("click",()=>cpPick("B"));

// ===================================================================
//  MARCADORES (editor completo)
// ===================================================================
const MK={pdf:null,pages:0,folder:null,name:"",items:[]};
function mkReadInputs(){
  MK.items.forEach((it,i)=>{
    const t=q("mk-t-"+i),p=q("mk-p-"+i);
    if(t)it.title=t.value;
    if(p){const v=parseInt(p.value,10);if(!isNaN(v))it.page=v;}
  });
}
function mkUpd(){
  const has=!!MK.pdf;
  q("mkAdd").disabled=!has;q("mkPaste").disabled=!has;q("mkSave").disabled=!has;
  q("mkDivide").disabled=!has||!MK.items.length;
}
function mkRender(){
  const area=q("mkList");
  if(!MK.pdf){area.innerHTML='<div class="empty">Escolha um PDF para ver e editar os marcadores.</div>';mkUpd();return;}
  if(!MK.items.length){area.innerHTML='<div class="empty">Este PDF não tem marcadores. Use "Adicionar marcador" ou "Colar lista".</div>';mkUpd();return;}
  let html="";
  MK.items.forEach((it,i)=>{
    const ml=(Math.max(1,it.depth)-1)*22;
    html+='<div class="mkrow" style="margin-left:'+ml+'px">'
      +'<span class="mkdepth">N'+it.depth+'</span>'
      +'<input class="mktitle" id="mk-t-'+i+'" value="'+escA(it.title)+'" spellcheck="false">'
      +'<span>p.</span><input type="number" min="1" max="'+MK.pages+'" class="mkpage" id="mk-p-'+i+'" value="'+escA(it.page)+'">'
      +'<span class="mkbtns">'
      +'<button data-a="out" data-i="'+i+'" title="Recuar nível">⇤</button>'
      +'<button data-a="in" data-i="'+i+'" title="Aumentar nível">⇥</button>'
      +'<button data-a="up" data-i="'+i+'" title="Subir">↑</button>'
      +'<button data-a="down" data-i="'+i+'" title="Descer">↓</button>'
      +'<button data-a="del" data-i="'+i+'" title="Excluir">✕</button>'
      +'</span></div>';
  });
  area.innerHTML=html;
  area.querySelectorAll(".mktitle,.mkpage").forEach(inp=>inp.addEventListener("input",mkReadInputs));
  area.querySelectorAll(".mkbtns button").forEach(b=>b.addEventListener("click",()=>mkAction(b.dataset.a,+b.dataset.i)));
  mkUpd();
}
function mkAction(a,i){
  mkReadInputs();
  const it=MK.items[i];
  if(a==="del")MK.items.splice(i,1);
  else if(a==="in")it.depth=Math.min(it.depth+1,6);
  else if(a==="out")it.depth=Math.max(1,it.depth-1);
  else if(a==="up"&&i>0)MK.items.splice(i-1,0,MK.items.splice(i,1)[0]);
  else if(a==="down"&&i<MK.items.length-1)MK.items.splice(i+1,0,MK.items.splice(i,1)[0]);
  mkRender();
}
async function mkLoadFrom(path){
  const info=await api("/api/pdf-info",{path});
  if(!info.ok){toast(info.error||"Não consegui ler este PDF.");return false;}
  MK.pdf=path;MK.pages=info.pages;MK.folder=info.folder;MK.name=info.name.replace(/\.pdf$/i,"");
  q("mkPath").textContent=info.name+"  ("+info.pages+" páginas)";q("mkPath").title=path;
  q("mkPrev").innerHTML='<iframe src="/file?path='+encodeURIComponent(path)+'&t='+Date.now()+'"></iframe>';
  const ol=await api("/api/pdf-outline",{path});
  MK.items=(ol.ok&&ol.bookmarks)?ol.bookmarks.map(b=>({title:b.title,depth:b.depth,page:b.page})):[];
  mkRender();return true;
}
q("mkPick").addEventListener("click",async()=>{
  q("mkPick").disabled=true;
  const r=await api("/api/choose-file",{start:MK.folder});
  q("mkPick").disabled=false;
  if(r.cancelled)return;if(r.error){toast(r.error);return;}
  if(await mkLoadFrom(r.path))toast(MK.items.length+" marcador(es) carregado(s).");
});
q("mkAdd").addEventListener("click",()=>{mkReadInputs();MK.items.push({title:"Novo marcador",depth:1,page:1});mkRender();});
q("mkPaste").addEventListener("click",()=>{
  q("modal").innerHTML='<h3>Colar lista de marcadores</h3><p class="hint">Um por linha. Indente com espaços/tabs para criar níveis. Informe a página com "| 12" no fim. Ex.: <i>Capítulo 1 | 1</i></p><div class="field"><textarea id="mkPasteArea" placeholder="Capítulo 1 | 1&#10;  Seção 1.1 | 3&#10;  Seção 1.2 | 5&#10;Capítulo 2 | 9"></textarea></div><div class="acts"><button onclick="closeModal()">Cancelar</button><button class="btn-primary" id="mkPasteGo">Adicionar</button></div>';
  openModal();q("mkPasteArea").focus();
  q("mkPasteGo").addEventListener("click",()=>{
    mkReadInputs();
    q("mkPasteArea").value.split(/\r?\n/).forEach(raw=>{
      if(!raw.trim())return;
      const lead=raw.match(/^[ \t]*/)[0];
      const tabs=(lead.match(/\t/g)||[]).length,spaces=lead.replace(/\t/g,"").length;
      let depth=Math.max(1,Math.min(1+tabs+Math.floor(spaces/2),6));
      let content=raw.trim(),page=1;
      const m=content.match(/^(.*?)[\|\t]\s*(\d+)\s*$/);
      if(m){content=m[1].trim();page=parseInt(m[2],10);}
      page=Math.max(1,Math.min(page,MK.pages||page));
      MK.items.push({title:content||"(sem titulo)",depth,page});
    });
    closeModal();mkRender();toast("Marcadores adicionados.");
  });
});
q("mkSave").addEventListener("click",()=>{
  mkReadInputs();
  if(!MK.items.length){toast("Não há marcadores para salvar.");return;}
  const defName=MK.name+" - marcado.pdf";
  q("modal").innerHTML='<h3>Salvar marcadores</h3><p class="hint">Como você quer salvar?</p>'
    +'<div class="field"><label class="opt"><input type="radio" name="mkSaveMode" value="new" checked> Novo arquivo:</label> <input type="text" id="mkSaveName" value="'+escA(defName)+'" style="width:58%"></div>'
    +'<div class="field"><label class="opt"><input type="radio" name="mkSaveMode" value="over"> Sobrescrever o PDF original</label></div>'
    +'<div class="acts"><button onclick="closeModal()">Cancelar</button><button class="btn-primary" id="mkSaveGo">Salvar</button></div>';
  openModal();
  q("mkSaveGo").addEventListener("click",async()=>{
    const mode=(document.querySelector('input[name=mkSaveMode]:checked')||{}).value;
    const body={pdf:MK.pdf,items:MK.items};
    if(mode==="over")body.overwrite=true;
    else{let nm=(q("mkSaveName").value||defName).trim();if(!nm.toLowerCase().endsWith(".pdf"))nm+=".pdf";body.out=MK.folder+"\\"+nm;}
    q("mkSaveGo").disabled=true;q("mkSaveGo").textContent="Salvando…";
    const r=await api("/api/pdf-save-outline",body);
    closeModal();
    if(!r.ok){toast(r.error||"Falha ao salvar.");return;}
    toast("✓ Salvo: "+r.path.split(/[\\/]/).pop()+" ("+r.count+" marcadores)");
    await mkLoadFrom(r.path);
  });
});
q("mkDivide").addEventListener("click",()=>{
  mkReadInputs();
  if(!MK.items.length){toast("Não há marcadores.");return;}
  const maxd=Math.max.apply(null,MK.items.map(b=>b.depth));
  let optsHtml="";
  for(let L=1;L<=maxd;L++){const c=MK.items.filter(b=>b.depth<=L).length;optsHtml+='<option value="'+L+'">nível '+L+' ('+c+' arquivos)</option>';}
  q("modal").innerHTML='<h3>Dividir por estes marcadores</h3><p class="hint">Escolha o nível. Os arquivos saem na subpasta "Dividido", nomeados pelos marcadores.</p><div class="field">Nível: <select id="mkDivLevel">'+optsHtml+'</select></div><div class="acts"><button onclick="closeModal()">Cancelar</button><button class="btn-primary" id="mkDivGo">Dividir</button></div>';
  openModal();
  q("mkDivGo").addEventListener("click",async()=>{
    const L=parseInt(q("mkDivLevel").value||"1",10);
    const bnd=MK.items.filter(b=>b.depth<=L).slice().sort((a,b)=>a.page-b.page);
    const ranges=[];
    for(let i=0;i<bnd.length;i++){const s=bnd[i].page,e=(i+1<bnd.length)?bnd[i+1].page-1:MK.pages;ranges.push({start:s,end:(e<s?s:e),name:sanTitle(bnd[i].title)});}
    q("mkDivGo").disabled=true;q("mkDivGo").textContent="Dividindo…";
    const r=await api("/api/pdf-split",{pdf:MK.pdf,dest:MK.folder,subfolder:"Dividido",auto:true,ranges});
    closeModal();
    if(!r.ok){toast((r.problems&&r.problems[0]&&r.problems[0].reason)||r.error||"Falha ao dividir.");return;}
    toast("✓ "+r.results.length+" PDF(s) gerado(s) na subpasta 'Dividido'.");
  });
});
mkRender();

// ===================================================================
//  EXCLUIR PAGINAS
// ===================================================================
const DP={pdf:null,name:"",pages:0,fromUpload:false};
function dpUpd(){
  const hasSpec=q("dpPages").value.trim()!=="";
  q("dpGo").disabled=!(DP.pdf&&hasSpec);
  q("dpOver").disabled=DP.fromUpload;
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

// ===================================================================
//  Desfazer global + Modal
// ===================================================================
async function undoLast(){
  const r=await api("/api/undo",{});
  if(!r.ok){toast(r.error||"Nada para desfazer.");setCanUndo(false);return;}
  setCanUndo(!!r.can_undo);toast(r.message||"Desfeito.");
  const f=r.folder;
  if(f){
    if(RN.current&&sameLower(RN.current,f))await rnReloadList();
    if(BT.folder&&sameLower(BT.folder,f))await btReloadList();
    if(XL.folder&&sameLower(XL.folder,f))await xlReload();
    if(OG.folder&&sameLower(OG.folder,f))ogClearPreview();
  }
}
function showPreview(r,confirm,applyFn){
  const m=q("modal");
  if(r.problems&&r.problems.length){
    const li=r.problems.map(p=>`<div class="line"><b>${esc(p.current)}</b> → ${esc(p.new)}<br><i>${esc(p.reason)} Sugestão: ${esc(p.suggestion)}</i></div>`).join("");
    m.innerHTML=`<h3>Há itens a corrigir</h3><p class="hint">Ajuste antes de renomear.</p><div class="pv">${li}</div><div class="acts"><button onclick="closeModal()">Fechar</button></div>`;openModal();return;
  }
  if(!r.plan||!r.plan.length){toast("Nenhuma alteração para aplicar.");return;}
  const li=r.plan.map(p=>`<div class="line">${esc(p.current)} → <b>${esc(p.new)}</b></div>`).join("");
  m.innerHTML=`<h3>${confirm?"Confirmar renomeação":"Prévia"}</h3><p class="hint">${r.plan.length} item(ns) ${confirm?"serão renomeados":"seriam renomeados"}:</p><div class="pv">${li}</div><div class="acts"><button onclick="closeModal()">${confirm?"Cancelar":"Fechar"}</button>${confirm?'<button class="btn-primary" id="pvGo">Renomear tudo</button>':""}</div>`;
  openModal();if(confirm)q("pvGo").addEventListener("click",applyFn);
}
function confirmModal(title,msg,yesLabel,onYes){
  q("modal").innerHTML=`<h3>${esc(title)}</h3><p class="hint">${esc(msg)}</p><div class="acts"><button onclick="closeModal()">Cancelar</button><button class="btn-primary" id="cfYes">${esc(yesLabel)}</button></div>`;
  openModal();q("cfYes").addEventListener("click",onYes);
}
function openModal(){q("modalBg").classList.add("show");}
function closeModal(){q("modalBg").classList.remove("show");}
q("modalBg").addEventListener("click",e=>{if(e.target.id==="modalBg")closeModal();});
document.addEventListener("keydown",e=>{if(e.key==="Escape")closeModal();});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--pick":
        kind = sys.argv[2] if len(sys.argv) >= 3 else "folder"
        initial = sys.argv[3] if len(sys.argv) >= 4 else ""
        outfile = sys.argv[4] if len(sys.argv) >= 5 else ""
        path = pick_path_tk(kind, initial)
        if outfile:
            try:
                with open(outfile, "w", encoding="utf-8") as f:
                    f.write(path)
            except Exception:
                pass
        else:
            try:
                sys.stdout.buffer.write(path.encode("utf-8"))
                sys.stdout.buffer.flush()
            except Exception:
                pass
        sys.exit(0)
    main()
