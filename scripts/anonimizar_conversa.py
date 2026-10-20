"""Anonimiza a exportação de uma sessão do Claude Code antes de publicá-la.

Uso:
    python scripts/anonimizar_conversa.py <session-export.zip> <pasta_saida> --email <email_real>

Mantém: mensagens do usuário, respostas do assistente, chamadas de ferramentas e seus
resultados (inclusive capturas de tela) e mensagens enviadas durante a execução.
Remove: requisições internas à API (prompt de sistema), lembretes injetados pelo sistema,
listas de ferramentas/skills/conectores do ambiente, metadados da máquina e o texto de
raciocínio interno. Substitui e-mails pessoais por [email removido] e caminhos da pasta do
usuário por <HOME>. Termina com erro se algum termo proibido sobrar na saída.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

# E-mails fictícios do projeto e o de atribuição de commits podem ficar.
ALLOWED_EMAIL_DOMAINS = ("aurora.example", "exemplo.com", "empresa.com.br", "anthropic.com")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
HOME_RES = [
    re.compile(r"(?i)[a-z]:(?:\\|/)+users(?:\\|/)+([^\\/\s\"'`]+)"),
    re.compile(r"(?i)/[a-z]/users/([^/\s\"'`]+)"),
    re.compile(r"(?i)\b[a-z]--users-([a-z0-9]+)"),
]
REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
ACCOUNT_DIR_RE = re.compile(r"\bclaude_[a-z0-9]{10,}\b")  # pasta de dados da conta no app
KEEP_ATTACHMENTS = {"queued_command"}  # mensagens que o usuário enviou durante a execução


def scrub_text(text: str, user: str, banned: re.Pattern | None = None) -> str:
    """Aplica as trocas na ordem: lembretes do sistema, e-mails, caminhos, pasta da conta, termos."""
    text = REMINDER_RE.sub("[contexto do sistema removido]", text)
    # E-mails antes dos termos bloqueados: senão "gmail.com" some e o nome antes do @ fica exposto.
    text = EMAIL_RE.sub(lambda m: m.group(0) if m.group(0).lower().endswith(ALLOWED_EMAIL_DOMAINS)
                        else "[email removido]", text)
    for rx in HOME_RES:
        text = rx.sub(lambda m: "<HOME>" if m.group(1).lower() == user.lower() else m.group(0), text)
    text = ACCOUNT_DIR_RE.sub("<conta>", text)
    if banned is not None:  # por último: o que sobrar dos termos da conta pessoal
        text = banned.sub("[termo removido]", text)
    return text


def scrub(obj, user: str, banned: re.Pattern | None = None):
    """Aplica `scrub_text` a todas as strings da estrutura (exceto dados base64 de imagens)."""
    if isinstance(obj, str):
        return scrub_text(obj, user, banned)
    if isinstance(obj, list):
        return [scrub(x, user, banned) for x in obj]
    if isinstance(obj, dict):
        if obj.get("type") == "base64":  # dados de imagem: não alterar (evita corromper o arquivo)
            return {k: (v if k == "data" else scrub(v, user, banned)) for k, v in obj.items()}
        if obj.get("type") == "thinking":
            return {"type": "thinking", "thinking": "[raciocínio interno omitido]"}
        return {k: scrub(v, user, banned) for k, v in obj.items() if k != "signature"}
    return obj


def filter_entries(lines: list[dict]) -> list[dict]:
    out = []
    for o in lines:
        t = o.get("type")
        if t in ("user", "assistant") and isinstance(o.get("message"), dict):
            msg = o["message"]
            out.append({"type": t, "timestamp": o.get("timestamp"),
                        "message": {"role": msg.get("role"), "content": msg.get("content"),
                                    **({"model": msg["model"]} if msg.get("model") else {})}})
        elif t == "attachment" and o.get("attachment", {}).get("type") in KEEP_ATTACHMENTS:
            out.append({"type": "attachment", "timestamp": o.get("timestamp"), "attachment": o["attachment"]})
    return out


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def to_markdown(entries: list[dict], title: str) -> str:
    md = [f"# {title}", "",
          "> Versão legível gerada a partir da exportação anonimizada (`.jsonl` ao lado). Mostra as",
          "> mensagens e as ferramentas chamadas; os resultados completos das ferramentas estão no `.jsonl`.", ""]
    for e in entries:
        ts = (e.get("timestamp") or "")[:19].replace("T", " ")
        if e["type"] == "attachment":
            a = e["attachment"]
            prompt = a.get("prompt") or a.get("content") or ""
            md += [f"### Usuário, durante a execução · {ts}", "", _text_of(prompt).strip(), ""]
            continue
        content = e["message"]["content"]
        if e["type"] == "user":
            if isinstance(content, list) and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                continue  # resultado de ferramenta: só no .jsonl
            text = _text_of(content).strip()
            if not text or text == "[contexto do sistema removido]":
                continue
            if text.startswith("Base directory for this skill") or text.startswith("<command-message>"):
                first = text.splitlines()[0][:160]
                md += [f"### Skill carregada · {ts}", "", f"`{first}`", ""]
                continue
            md += [f"### Usuário · {ts}", "", text, ""]
        else:
            for b in content if isinstance(content, list) else []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    md += [f"### Claude · {ts}", "", b["text"].strip(), ""]
                elif b.get("type") == "tool_use":
                    inp = b.get("input", {})
                    desc = inp.get("description") or inp.get("file_path") or inp.get("url") or inp.get("query") or ""
                    md += [f"- ferramenta `{b.get('name')}`" + (f": {str(desc)[:160]}" if desc else "")]
            md.append("")
    return "\n".join(md).replace("\n\n\n", "\n\n")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("zip")
    ap.add_argument("out_dir")
    ap.add_argument("--email", required=True, help="e-mail real a ser removido (verificação final)")
    ap.add_argument("--user", required=True, help="nome da pasta do usuário no sistema (ex.: henri)")
    ap.add_argument("--name", default="sessao")
    ap.add_argument("--banned", nargs="*", default=[], help="termos que não podem sobrar na saída")
    args = ap.parse_args()

    terms = args.banned + [args.email.split("@")[0]]  # o nome do e-mail também, se aparecer solto
    banned = None
    if terms:
        alts = sorted({t for t in terms if t}, key=len, reverse=True)
        banned = re.compile("|".join(re.escape(t) for t in alts), re.IGNORECASE)

    z = zipfile.ZipFile(args.zip)
    raw = [json.loads(l) for l in z.read("transcript.jsonl").decode("utf-8").splitlines() if l.strip()]
    entries = [scrub(e, args.user, banned) for e in filter_entries(raw)]

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jsonl = out / f"{args.name}_transcricao_anonimizada.jsonl"
    jsonl.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")
    md = out / f"{args.name}_legivel.md"
    md.write_text(to_markdown(entries, f"Conversa com o Claude Code ({args.name})"), encoding="utf-8")

    banned = [args.email, args.email.split("@")[0]] + args.banned
    leaks = {}
    for f in (jsonl, md):
        # Verifica o texto sem os dados base64 das imagens (sequências aleatórias geram falsos positivos).
        text = re.sub(r'"data": "[A-Za-z0-9+/=]{200,}"', '"data": ""', f.read_text(encoding="utf-8")).lower()
        for term in banned:
            if term and term.lower() in text:
                leaks.setdefault(f.name, []).append(term)
    kinds = {}
    for e in raw:
        kinds[e.get("type")] = kinds.get(e.get("type"), 0) + 1
    print(f"entradas originais: {len(raw)} {kinds}")
    print(f"entradas mantidas: {len(entries)} -> {jsonl.name}, {md.name}")
    if leaks:
        print(f"ERRO: termos proibidos encontrados: {leaks}")
        return 1
    print("verificação: nenhum termo proibido encontrado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
