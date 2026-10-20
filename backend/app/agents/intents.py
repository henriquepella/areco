"""Catálogo de intenções: as perguntas que cada agente sabe responder, geradas a partir das bases.

Uma intenção tem o texto da sugestão, os parâmetros (slots) que precisa e como montar a pergunta
final enviada ao agente especializado. Os textos, as opções dos parâmetros (anos, meses,
departamentos, status) e a própria disponibilidade de cada intenção saem dos arquivos carregados:
se um arquivo, coluna ou registro não existir, a sugestão correspondente não aparece.

Para criar uma sugestão nova: acrescente um `Intent` no construtor do domínio (`_financial`,
`_internal` ou `_ombudsman`), com uma condição de disponibilidade baseada nos dados.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Callable

from app.agents.financial_agent import LANC, ORC, REGRAS, Clarify, has_partial_period, parse_period
from app.agents.ombudsman_agent import MANIF, REF
from app.services.document_service import DOMAINS, Chunk, KnowledgeBase
from app.services.retrieval_service import BM25Index, normalize

MONTH_LABELS = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
                "setembro", "outubro", "novembro", "dezembro")
FAQ = "faq_interno.json"
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_PROTOCOL_RE = re.compile(r"\bouv\s*-?\s*(\d{1,6})\b")
_PLACEHOLDER_YEAR = "2000"  # ano fictício, só para descobrir o trecho de período citado sem ano

# Como cada parâmetro é citado nas mensagens ("Não identifiquei <isto> na resposta.")
SLOT_LABELS = {"periodo": "o período", "ano": "o ano", "departamento": "o departamento",
               "status": "o status", "protocolo": "um protocolo no formato OUV-NNNN"}


@dataclass(frozen=True)
class Slot:
    name: str  # periodo | ano | departamento | status | protocolo
    prompt: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Intent:
    id: str
    domain: str
    label: str  # texto da sugestão exibida ao usuário
    build: Callable[[dict[str, str]], str]  # pergunta final enviada ao agente
    slots: tuple[Slot, ...] = ()  # parâmetros obrigatórios, na ordem em que são pedidos
    trigger: str = ""  # regex (texto normalizado): pergunta livre que pede esta intenção
    hints: str = ""  # sinônimos usados só para encontrar perguntas parecidas
    optional: tuple[str, ...] = ()  # parâmetros aproveitados quando citados (ex.: departamento)

    def missing(self, slots: dict[str, str]) -> Slot | None:
        return next((s for s in self.slots if not slots.get(s.name)), None)


# ---------------------------------------------------------------- extração de parâmetros

def period_phrase(start: date, end: date) -> str:
    """Período em palavras que o agente financeiro entende: "2025", "janeiro de 2026",
    "janeiro a março de 2025" ou "janeiro de 2025 a março de 2026"."""
    if (start.month, start.day, end.month, end.day) == (1, 1, 12, 31):
        return str(start.year) if start.year == end.year else f"{start.year} a {end.year}"
    first, last = MONTH_LABELS[start.month - 1], MONTH_LABELS[end.month - 1]
    if start.year != end.year:
        return f"{first} de {start.year} a {last} de {end.year}"
    if start.month == end.month:
        return f"{first} de {start.year}"
    return f"{first} a {last} de {start.year}"


def extract_period(text: str, partial: str | None = None) -> tuple[str | None, str | None]:
    """Devolve (período completo, trecho sem ano).

    "janeiro de 2026" -> ("janeiro de 2026", None); "janeiro" -> (None, "janeiro");
    com partial="janeiro" e texto "2026" -> ("janeiro de 2026", None).
    """
    q = normalize(text)
    year = _YEAR_RE.search(q)
    if partial and year and not has_partial_period(q):  # resposta só com o ano
        q = normalize(f"{partial} de {year.group(1)}")
    try:
        start, end = parse_period(q)
    except Clarify:
        start, end = parse_period(f"{q} {_PLACEHOLDER_YEAR}")
        return None, period_phrase(start, end).replace(f" de {_PLACEHOLDER_YEAR}", "")
    if start is None:
        return None, partial
    return period_phrase(start, end), None


def extract_year(text: str) -> str | None:
    m = _YEAR_RE.search(text)
    return m.group(1) if m else None


def extract_protocol(text: str) -> str | None:
    m = _PROTOCOL_RE.search(normalize(text))
    return f"OUV-{int(m.group(1)):04d}" if m else None


def match_option(text: str, options: tuple[str, ...]) -> str | None:
    """Opção citada no texto, aceitando plural e feminino ("pendentes", "abertas")."""
    q = normalize(text)
    for option in options:
        n = normalize(option)
        stem = n[:max(5, len(n) - 2)] if len(n) > 5 else n
        if re.search(rf"\b{re.escape(stem)}", q):
            return option
    return None


# ---------------------------------------------------------------- catálogo


class IntentCatalog:
    def __init__(self, kb: KnowledgeBase, find_department: Callable[[str], str | None]):
        self.kb = kb
        self._find_department = find_department  # regra do agente financeiro para texto livre
        self._years: dict[str, tuple[str, ...]] = {}
        self._optional: dict[str, Slot] = {}
        intents = [*self._financial(), *self._internal(), *self._ombudsman()]
        self.intents = {i.id: i for i in intents}
        self._index = {d: BM25Index([Chunk(id=i.id, domain=d, document="intencoes", text=f"{i.label} {i.hints}")
                                     for i in self.by_domain(d)]) for d in DOMAINS}

    # ------------------------------------------------------------ consulta

    def get(self, intent_id: str | None) -> Intent | None:
        return self.intents.get(intent_id or "")

    def by_domain(self, domain: str) -> list[Intent]:
        return [i for i in self.intents.values() if i.domain == domain]

    def years(self, domain: str) -> tuple[str, ...]:
        return self._years.get(domain, ())

    def similar(self, text: str, domain: str, k: int = 3) -> list[Intent]:
        """Perguntas conhecidas mais parecidas com o texto (busca BM25 nos textos das sugestões).
        Sem nenhuma parecida, devolve as primeiras sugestões do domínio."""
        if domain not in self._index:
            return []
        hits = [self.intents[h.chunk.id] for h in self._index[domain].search(text, k)]
        return hits or self.by_domain(domain)[:k]

    def suggestions(self) -> dict[str, list[dict]]:
        return {d: [{"id": i.id, "domain": d, "label": i.label, "needs": [s.name for s in i.slots]}
                    for i in self.by_domain(d)] for d in DOMAINS}

    def extract(self, slot: Slot, text: str, slots: dict[str, str], answering: bool) -> dict[str, str]:
        """Valores do parâmetro encontrados no texto. `answering` indica que o texto é a resposta
        a uma pergunta sobre este parâmetro (aceita o nome da opção sozinho, como "Financeiro")."""
        if slot.name == "periodo":
            period, partial = extract_period(text, slots.get("periodo_parcial"))
            if period:
                return {"periodo": period}
            return {"periodo_parcial": partial} if partial else {}
        if slot.name == "ano":
            value = extract_year(text)
        elif slot.name == "protocolo":
            value = extract_protocol(text)
        elif slot.name == "departamento" and not answering:
            value = self._find_department(normalize(text))
        else:  # departamento (respondendo) e status
            value = match_option(text, slot.options)
        return {slot.name: value} if value else {}

    def optional_slots(self, intent: Intent) -> tuple[Slot, ...]:
        return tuple(self._optional[n] for n in intent.optional if n in self._optional)

    # ------------------------------------------------------------ construção por domínio

    def _financial(self) -> list[Intent]:
        lanc, orc = self.kb.tables.get(LANC, []), self.kb.tables.get(ORC, [])
        if not (lanc or orc):
            return []

        def has(rows: list[dict], **equals: str) -> bool:
            return any(all(r.get(k) == v for k, v in equals.items()) for r in rows)

        years = tuple(sorted({r["data_competencia"][:4] for r in lanc if r.get("data_competencia")}
                             | {r["ano"] for r in orc if r.get("ano")}))
        self._years["financeiro"] = years
        departments = tuple(sorted({r["departamento"] for r in orc if r.get("departamento")}))
        # Primeiro e último mês com orçamento em cada ano, como exemplos de mês específico.
        months = tuple(f"{MONTH_LABELS[int(m) - 1]} de {y}"
                       for y, m in sorted({(r["ano"], r["mes"]) for r in orc}) if m in ("01", "12"))
        period = Slot("periodo", "Qual período você quer consultar? Pode ser um ano, um mês (ex.: janeiro "
                                 "de 2026) ou um intervalo (ex.: janeiro a março de 2025).", years)
        month = Slot("periodo", "Qual mês e ano você quer consultar?", months)
        year = Slot("ano", "Para qual ano?", years)
        department = Slot("departamento", "Qual departamento?", departments)
        self._optional["departamento"] = department

        def dep(s: dict[str, str]) -> str:
            return f" do departamento {s['departamento']}" if s.get("departamento") else ""

        out = []
        if has(lanc, tipo="Despesa", status="Pago"):
            out.append(Intent("fin_despesas_pagas", "financeiro", "Quanto foi gasto em despesas pagas em um período?",
                              lambda s: f"Quanto foi gasto em despesas pagas{dep(s)} em {s['periodo']}?", (period,),
                              trigger=r"(despesa|gast)\w*.*\bpag(o|a|os|as)\b|\bpag(o|a|os|as)\b.*(despesa|gast)",
                              hints="gasto pago quitado desembolsado despesa paga", optional=("departamento",)))
        if orc:
            out.append(Intent("fin_maior_orcamento", "financeiro", "Qual departamento tem o maior orçamento em um mês específico?",
                              lambda s: f"Qual departamento tem maior orçamento em {s['periodo']}?", (month,),
                              trigger=r"(maior|menor) orcamento|orcamento (maior|menor)",
                              hints="ranking departamento orçamento maior"))
        if has(lanc, tipo="Receita"):
            out.append(Intent("fin_receitas_ano", "financeiro", "Qual foi a soma das receitas registradas em um ano?",
                              lambda s: f"Qual foi a soma de todas as receitas registradas em {s['ano']}?", (year,),
                              trigger=r"soma .*receitas?|receitas? registrad", hints="faturamento total receitas ano"))
        if orc and departments and has(lanc, tipo="Despesa", status="Pago"):
            out.append(Intent("fin_execucao_departamento", "financeiro", "Como está a execução orçamentária de um departamento?",
                              lambda s: f"Qual a execução orçamentária do departamento {s['departamento']} em {s['ano']}?",
                              (department, year), trigger=r"execucao orcamentaria",
                              hints="executado realizado orçamento departamento"))
        if has(lanc, tipo="Despesa", status="Pendente"):
            out.append(Intent("fin_despesas_pendentes", "financeiro", "Quais despesas estão pendentes?",
                              lambda s: "Qual o total de despesas pendentes?", hints="a pagar em aberto pendente"))
        if has(lanc, tipo="Despesa"):
            out.append(Intent("fin_despesas_categoria", "financeiro", "Qual é o valor total de despesas por categoria?",
                              lambda s: "Qual o total de despesas por categoria?", hints="categoria agrupado"))
        if orc:
            out.append(Intent("fin_orcamento_periodo", "financeiro", "Qual foi o orçamento aprovado em um período?",
                              lambda s: f"Qual o orçamento aprovado{dep(s)} em {s['periodo']}?", (period,),
                              trigger=r"orcamento aprovado", hints="orçado previsto", optional=("departamento",)))
        if has(lanc, tipo="Receita", status="Pago"):
            out.append(Intent("fin_receitas_recebidas", "financeiro", "Quanto foi recebido em receitas em um período?",
                              lambda s: f"Qual o total de receitas pagas{dep(s)} em {s['periodo']}?", (period,),
                              trigger=r"receitas? (recebid|pag)", hints="recebido entrada",
                              optional=("departamento",)))
        rules = self.kb.json_docs.get(REGRAS)
        indicators = rules.get("indicadores_exemplo", []) if isinstance(rules, dict) else []
        for n, item in enumerate(i for i in indicators if isinstance(i, dict) and i.get("nome")):
            question = f"O que significa “{item['nome']}”?"
            out.append(Intent(f"fin_indicador_{n}", "financeiro", question, lambda s, q=question: q,
                              hints=f"definição indicador {item.get('definicao', '')}"))
        return out

    def _internal(self) -> list[Intent]:
        faq = [i for i in self.kb.json_docs.get(FAQ, []) if isinstance(i, dict) and i.get("pergunta")]
        out = [Intent(f"int_{re.sub(r'[^a-z0-9]', '_', str(item.get('id', n)).lower())}", "informacoes_internas",
                      item["pergunta"], lambda s, q=item["pergunta"]: q, hints=item.get("resposta", ""))
               for n, item in enumerate(faq)]
        # Documentos sem pergunta na FAQ viram uma sugestão a partir do título.
        covered = {i.get("documento") for i in faq}
        seen = set()
        for chunk in self.kb.chunks.get("informacoes_internas", []):
            if not chunk.document.endswith(".md") or chunk.document in covered | seen:
                continue
            seen.add(chunk.document)
            title = chunk.text.split("\n", 1)[0].split(" — ")[0].strip()
            question = f"Quais são as orientações sobre {title[0].lower()}{title[1:]}?"
            out.append(Intent(f"int_doc_{re.sub(r'[^a-z0-9]', '_', chunk.document[:-3].lower())}",
                              "informacoes_internas", question, lambda s, q=question: q))
        return out

    def _ombudsman(self) -> list[Intent]:
        ref = self.kb.json_docs.get(REF)
        ref = ref if isinstance(ref, dict) else {}
        manif = self.kb.tables.get(MANIF, [])
        years = tuple(sorted({r["data_abertura"][:4] for r in manif if r.get("data_abertura")}))
        self._years["ouvidoria"] = years
        out = []
        if manif and "protocolo" in manif[0]:
            protocol = Slot("protocolo", "Qual é o número do protocolo? Use o formato OUV-NNNN.")
            out.append(Intent("ouv_status_protocolo", "ouvidoria", "Qual é o status de um protocolo?",
                              lambda s: f"Qual o status do protocolo {s['protocolo']}?", (protocol,),
                              trigger=r"\bprotocolo", hints="situação andamento número protocolo manifestação"))
        for key, intent_id, label in (("status_validos", "ouv_status_validos", "Quais status podem aparecer na Ouvidoria?"),
                                      ("categorias", "ouv_categorias", "Quais categorias de manifestação existem?"),
                                      ("prioridades_validas", "ouv_prioridades", "Quais prioridades uma manifestação pode ter?"),
                                      ("regras", "ouv_regras", "Quais são as regras da Ouvidoria?")):
            if ref.get(key):
                out.append(Intent(intent_id, "ouvidoria", label, lambda s, q=label: q))
        if manif and ref.get("status_validos"):
            status = Slot("status", "Qual status você quer consultar?", tuple(ref["status_validos"]))
            out.append(Intent("ouv_contagem_status", "ouvidoria", "Quantas manifestações estão com determinado status?",
                              lambda s: f"Quantas manifestações estão com status {s['status']}?", (status,),
                              trigger=r"quant\w*.*\bstatus\b", hints="em análise aguardando aberto respondido concluído"))
        if manif and years:
            period = Slot("periodo", "Qual período? Pode ser um ano, um mês ou um intervalo, como "
                                     "“janeiro a março de 2026”.", years)
            out.append(Intent("ouv_contagem_periodo", "ouvidoria", "Quantas manifestações foram registradas em um período?",
                              lambda s: f"Quantas manifestações foram registradas em {s['periodo']}?", (period,),
                              trigger=r"quant\w*.*registrad", hints="abertas recebidas registradas período"))
        if manif:
            out.append(Intent("ouv_por_categoria", "ouvidoria", "Quantas manifestações existem por categoria?",
                              lambda s: "Quantas manifestações existem por categoria?", hints="distribuição categoria"))
        return out
