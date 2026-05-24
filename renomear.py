# -*- coding: utf-8 -*-
"""
Central de Arquivos — motor local.

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
# Desfazer (pilha generica)
# ----------------------------------------------------------------------------

UNDO_STACK = []


# ----------------------------------------------------------------------------
# Seletor de pasta nativo (tkinter, em subprocesso p/ vir ao topo)
# ----------------------------------------------------------------------------

def pick_folder_tk(initialdir=None):
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes("-topmost", 1)
    root.update()
    kw = {"title": "Escolha a pasta", "mustexist": True, "parent": root}
    if initialdir and os.path.isdir(initialdir):
        kw["initialdir"] = initialdir
    path = filedialog.askdirectory(**kw)
    root.destroy()
    return path or ""


def choose_folder_dialog(start=None):
    try:
        out = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--pick", start or ""],
            capture_output=True, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out.stdout.decode("utf-8", "replace").strip()
    except Exception:
        return ""


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


def main():
    port = find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print("=" * 56)
    print("  Central de Arquivos")
    print("=" * 56)
    print(f"  Aberto no navegador: {url}")
    print("  Deixe esta janela aberta enquanto usa o app.")
    print("  Para encerrar: feche esta janela.")
    print("=" * 56)
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
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
<title>Central de Arquivos</title>
<style>
  body{font-family:"Times New Roman",Times,serif;color:#000;background:#fff;margin:26px;font-size:19px;line-height:1.55}
  .wrap{max-width:1860px;margin:0 auto}
  h1{font-size:32px;font-weight:bold;margin:0 0 4px}
  .sub{font-style:italic;margin:0 0 12px;font-size:17px}
  hr{border:0;border-top:1px solid #000;margin:14px 0}
  a{color:#000}
  a:hover{background:#eee}

  .tabs{margin:0 0 6px}
  .tab{font-family:inherit;font-size:20px;color:#000;background:none;border:0;padding:0 5px;cursor:pointer;text-decoration:underline}
  .tab:hover{background:#eee}
  .tab.active{font-weight:bold;text-decoration:none}
  .tabsep{color:#000}
  .panel{display:none}
  .panel.active{display:block}

  button{font-family:inherit;font-size:18px;color:#000;background:#fff;border:1px solid #000;padding:6px 14px;cursor:pointer}
  button:hover{background:#eee}
  button:disabled{color:#999;border-color:#999;cursor:not-allowed;background:#fff}
  .btn-primary{font-weight:bold}
  .btn-link{border:0;background:none;padding:0;text-decoration:underline;font-size:18px;cursor:pointer}
  .btn-link:hover{background:#eee}

  .toolbar{margin:10px 0}
  .toolbar button{margin-right:5px;margin-bottom:4px}
  .pathbox{display:inline-block;border:1px solid #000;padding:6px 10px;font-style:italic;font-size:16px;max-width:760px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle}
  .crumbs{margin:10px 0;min-height:24px;font-size:18px}
  .crumbs .c{cursor:pointer;text-decoration:underline}
  .crumbs .cur{font-weight:bold}
  .crumbs .sep{margin:0 5px}

  table{border-collapse:collapse;width:100%}
  th,td{border:1px solid #000;padding:9px 12px;text-align:left;vertical-align:top}
  th{font-weight:bold}
  tbody tr.sel td{background:#eee}
  .colw-num{width:36px;text-align:right;color:#555}
  .tag{font-style:italic;font-size:15px;color:#333}
  .ftype{font-style:italic;font-size:15px;color:#555;margin-top:2px}
  .cur{word-break:break-word}
  .cur.dir{font-weight:bold}
  .fname{cursor:pointer;text-decoration:underline;word-break:break-word}
  .newcell{word-break:break-word}
  .empty{border:1px solid #000;padding:42px 14px;text-align:center;font-style:italic;font-size:18px}
  .legend{font-style:italic;font-size:16px;margin-top:8px}

  input[type=text],input[type=number],textarea,select{font-family:inherit;font-size:18px;color:#000;background:#fff;border:1px solid #000;padding:6px 8px}
  input[type=text]{width:100%}
  input.bad{border:2px solid #000;background:#eee}
  textarea{width:100%;min-height:230px;line-height:1.55}

  .row-msg{font-size:16px;margin-top:5px}
  .msg-err{font-weight:bold}
  .sugg{font-family:inherit;border:1px solid #000;background:#fff;cursor:pointer;font-size:16px;padding:2px 8px;margin-left:6px}
  .sugg:hover{background:#000;color:#fff}
  .badge{font-size:16px}
  .b-same{color:#777}
  .b-ok{font-style:italic}
  .b-err{font-weight:bold}

  .field{margin:14px 0}
  .lbl{display:block;font-weight:bold;margin-bottom:5px}
  .opts{display:flex;flex-wrap:wrap;gap:8px 22px;align-items:center}
  .opt{display:flex;align-items:center;gap:6px}
  .row2{display:flex;gap:20px;flex-wrap:wrap}
  .row2>div{flex:1;min-width:220px}
  .box{border:1px solid #000;padding:14px 16px;margin:12px 0}

  .actionbar{margin-top:14px}
  .actionbar button{margin-left:5px;margin-bottom:4px}
  .count{font-style:italic;font-size:17px}

  .rnlayout{display:flex;gap:30px;align-items:flex-start}
  .rnleft{flex:4;min-width:0}
  .rnright{flex:6;min-width:0;position:sticky;top:12px}
  .preview-box{border:1px solid #000;padding:12px;min-height:620px;max-height:88vh;overflow:auto}
  .preview-box img{max-width:100%;height:auto;display:block}
  .preview-box iframe{width:100%;height:84vh;min-height:700px;border:1px solid #000}
  .preview-box pre{white-space:pre-wrap;word-break:break-word;font-family:"Courier New",monospace;font-size:15px;margin:0}
  @media(max-width:1100px){.rnlayout{display:block}.rnright{flex:auto;position:static;margin-top:16px}}

  .cols{display:flex;gap:26px;align-items:flex-start}
  .cols .col{flex:1;min-width:0}
  .xltext{width:100%;min-height:480px;white-space:pre;line-height:1.7;font-size:18px}
  @media(max-width:760px){.cols{display:block}.xltext{min-height:260px}}

  .toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#fff;border:1px solid #000;padding:10px 18px;font-size:17px;display:none;z-index:50}
  .toast.show{display:block}
  .modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.35);display:none;align-items:center;justify-content:center;z-index:60;padding:20px}
  .modal-bg.show{display:flex}
  .modal{background:#fff;border:2px solid #000;max-width:720px;width:100%;max-height:84vh;overflow:auto;padding:22px}
  .modal h3{margin:0 0 8px;font-size:22px}
  .modal .pv{border:1px solid #000;padding:12px;margin:12px 0;font-family:"Courier New",monospace;font-size:15px}
  .pv .line{padding:3px 0}
  .modal .acts{margin-top:12px;text-align:right}
  .modal .acts button{margin-left:6px}
  .hint{font-style:italic;margin:0;font-size:17px}
</style>
</head>
<body>
<div class="wrap">
  <h1>Central de Arquivos</h1>
  <p class="sub">Ferramentas para renomear, criar, organizar e catalogar arquivos e pastas.</p>

  <p class="tabs">
    <button class="tab active" data-tab="rename">Renomear</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="batch">Renomear em lote</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="excel">Colar do Excel</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="create">Criar</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="organize">Organizar</button> <span class="tabsep">|</span>
    <button class="tab" data-tab="export">Exportar lista</button>
  </p>
  <hr>

  <!-- ===== RENOMEAR ===== -->
  <section class="panel active" id="panel-rename">
    <div class="toolbar">
      <button class="btn-primary" id="rnPick">Escolher pasta</button>
      <button id="rnUp" disabled>Subir</button>
      <button id="rnReload" disabled>Recarregar</button>
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
const RN={root:null,current:null,parent:null,items:[],sel:-1};
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
function rnAll(){rnRenderCrumbs();rnRender();rnUpd();}
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

// ===================================================================
//  RENOMEAR EM LOTE
// ===================================================================
const BT={folder:null,items:[]};
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
const XL={folder:null,items:[],filtered:[]};
function xlScope(){return (document.querySelector('input[name=xlScope]:checked')||{}).value||"all";}
function xlBuild(){
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
// sincroniza rolagem das duas colunas
["xlCurrent","xlNew"].forEach((a,i)=>{const b=["xlCurrent","xlNew"][1-i];q(a).addEventListener("scroll",()=>{const o=q(b);if(o.scrollTop!==q(a).scrollTop)o.scrollTop=q(a).scrollTop;});});

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
        initial = sys.argv[2] if len(sys.argv) >= 3 else ""
        try:
            sys.stdout.buffer.write(pick_folder_tk(initial).encode("utf-8"))
            sys.stdout.buffer.flush()
        except Exception:
            pass
        sys.exit(0)
    main()
