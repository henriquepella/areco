"""Agente Financeiro.

Perguntas com números (somas, contagens, orçamento, execução, inadimplência) são calculadas
de forma determinística sobre os CSVs, com `Decimal` — o modelo de linguagem nunca estima
valores. Perguntas conceituais ("o que é execução orçamentária?") usam RAG sobre
`indicadores_e_regras.json`.
"""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.agents.base import BaseAgent, brl
from app.models.schemas import AgentAnswer, Source
from app.services.retrieval_service import normalize

LANC = "lancamentos_financeiros.csv"
ORC = "orcamento_mensal.csv"
REGRAS = "indicadores_e_regras.json"

MONTHS = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
          "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
# Só nomes completos: abreviações como "dez" e "mar" colidem com palavras comuns.
_MONTH_RE = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b")
_ORD = {"1": 1, "primeiro": 1, "2": 2, "segundo": 2, "3": 3, "terceiro": 3, "4": 4, "quarto": 4}
_QUARTER_RE = re.compile(r"\b(1|2|3|4|primeiro|segundo|terceiro|quarto)\s*[oº°]?\s*trimestre")
_SEMESTER_RE = re.compile(r"\b(1|2|primeiro|segundo)\s*[oº°]?\s*semestre")

CATEGORY_ALIASES = {
    "Salários e encargos": r"salario|folha|encargo",
    "Treinamento": r"treinamento|curso|capacitacao|certificacao",
    "Material de escritório": r"material de escritorio|papelaria|suprimento",
    "Viagens": r"viage[mn]|hospedage|passage",
    "Software e licenças": r"software|licenc",
    "Serviços profissionais": r"servicos profissionais|consultoria|auditoria|juridic",
    "Receita de serviços": r"receita de servicos",
    "Infraestrutura cloud": r"infraestrutura|cloud|nuvem",
    "Impostos e taxas": r"imposto|taxa|tributo",
    "Marketing": r"categoria\W+(de\s+)?marketing|marketing\W+como categoria",
}


class Clarify(Exception):
    pass


@dataclass
class Filters:
    tipo: str | None = None
    status: str | None = None
    start: date | None = None
    end: date | None = None
    departamento: str | None = None
    categoria: str | None = None
    centro: str | None = None

    def describe(self) -> str:
        parts = [f"tipo={self.tipo}" if self.tipo else None,
                 f"status={self.status}" if self.status else None,
                 f"departamento={self.departamento}" if self.departamento else None,
                 f"categoria={self.categoria}" if self.categoria else None,
                 f"centro de custo={self.centro}" if self.centro else None,
                 f"data_competencia de {self.start.isoformat()} a {self.end.isoformat()}"
                 if self.start else "todo o período da base"]
        return "; ".join(p for p in parts if p)

    def period_label(self) -> str:
        if not self.start:
            return "em todo o período da base"
        return f"de {self.start:%d/%m/%Y} a {self.end:%d/%m/%Y}"


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def parse_period(q: str) -> tuple[date | None, date | None]:
    years = [int(y) for y in re.findall(r"\b(20\d{2})\b", q)]
    months = [MONTHS[m] for m in _MONTH_RE.findall(q)]
    quarter, semester = _QUARTER_RE.search(q), _SEMESTER_RE.search(q)
    if (quarter or semester or months) and not years:
        raise Clarify("Para qual ano? A base financeira cobre lançamentos de 2025 e 2026.")
    if quarter:
        n = _ORD[quarter.group(1)]
        return date(years[0], 3 * n - 2, 1), _month_end(years[0], 3 * n)
    if semester:
        n = _ORD[semester.group(1)]
        return date(years[0], 6 * n - 5, 1), _month_end(years[0], 6 * n)
    if months:
        y_start, y_end = years[0], years[-1]
        if len(months) >= 2:
            return date(y_start, months[0], 1), _month_end(y_end, months[-1])
        return date(y_start, months[0], 1), _month_end(y_start, months[0])
    if years:
        return date(min(years), 1, 1), date(max(years), 12, 31)
    return None, None


class FinancialAgent(BaseAgent):
    id = "financeiro"
    name = "Agente Financeiro"
    description = ("Calcula e explica receitas, despesas, orçamento, execução orçamentária e "
                   "inadimplência a partir dos lançamentos e orçamentos da base financeira.")

    # ------------------------------------------------------------ parsing

    def _departments(self) -> list[str]:
        return sorted({r["departamento"] for r in self.kb.tables.get(ORC, [])})

    def parse_filters(self, q: str) -> Filters:
        f = Filters()
        has_rec = re.search(r"receita|faturamento|recebi|recebiveis", q)
        has_desp = re.search(r"despesa|gast[oa]|gastou|gastamos|custo|saida", q)
        f.tipo = "Receita" if has_rec and not has_desp else "Despesa" if has_desp and not has_rec else None
        if re.search(r"\bpag[oa]s?\b|efetivamente pag|quitad|liquidad", q):
            f.status = "Pago"
        elif re.search(r"pendente", q):
            f.status = "Pendente"
        elif re.search(r"atrasad|vencid", q):
            f.status = "Atrasado"
        f.start, f.end = parse_period(q)
        cc = re.search(r"\bcc\s?-?(\d00)\b", q)
        f.centro = f"CC{cc.group(1)}" if cc else None
        for dep in self._departments():
            nd = normalize(dep)
            if nd == "financeiro":  # "financeiro" só conta como área se dito explicitamente
                if re.search(r"(departamento|area|setor|equipe|time)\s+(de\s+|do\s+)?financeiro", q):
                    f.departamento = dep
            elif nd == "marketing":
                if re.search(r"\bmarketing\b", q) and "categoria" not in q:
                    f.departamento = dep
            elif re.search(rf"\b{nd}\b", q):
                f.departamento = dep
        for cat, pattern in CATEGORY_ALIASES.items():
            if re.search(pattern, q):
                f.categoria = cat
                break
        return f

    def _filter(self, f: Filters) -> list[dict]:
        rows = []
        for r in self.kb.tables.get(LANC, []):
            d = date.fromisoformat(r["data_competencia"])
            if ((f.tipo and r["tipo"] != f.tipo) or (f.status and r["status"] != f.status)
                    or (f.start and not f.start <= d <= f.end)
                    or (f.departamento and r["departamento"] != f.departamento)
                    or (f.categoria and r["categoria"] != f.categoria)
                    or (f.centro and r["centro_custo"] != f.centro)):
                continue
            rows.append(r)
        return rows

    def _budget(self, f: Filters) -> list[dict]:
        out = []
        for r in self.kb.tables.get(ORC, []):
            d = date(int(r["ano"]), int(r["mes"]), 1)
            if ((f.start and not f.start <= d <= f.end)
                    or (f.departamento and r["departamento"] != f.departamento)
                    or (f.centro and r["centro_custo"] != f.centro)):
                continue
            out.append(r)
        return out

    # ------------------------------------------------------------ fontes

    def _rows_source(self, rows: list[dict], f: Filters, doc: str = LANC) -> Source:
        key = "id_lancamento" if doc == LANC else None
        ids = [r[key] for r in rows] if key else [f"{r['ano']}-{r['mes']} {r['centro_custo']}" for r in rows]
        shown = ", ".join(ids[:12]) + (f" (+{len(ids) - 12})" if len(ids) > 12 else "")
        return Source(document=doc, domain=self.id, section="consulta determinística",
                      excerpt=f"Filtro: {f.describe()}. {len(rows)} registro(s): {shown}")

    def _rule_source(self, keyword: str) -> list[Source]:
        for c in self.kb.chunks.get(self.id, []):
            if c.document == REGRAS and keyword in normalize(c.text):
                return [Source(document=REGRAS, domain=self.id, section=c.section, excerpt=c.text[:320])]
        return []

    def _done(self, text: str, sources: list[Source], status: str = "answered") -> AgentAnswer:
        return AgentAnswer(answer=text, status=status, sources=sources, mode="deterministic")

    # ------------------------------------------------------------ resposta

    def answer(self, question: str) -> AgentAnswer:
        if LANC not in self.kb.tables:
            return self.not_found("A base de lançamentos financeiros não está carregada.")
        q = normalize(question)
        if re.search(r"o que (e|sao|significa)|defini|como (e|sao) calculad|como calcul|criterio|regras? de calculo", q):
            return self.rag_answer(question, docs={REGRAS})
        try:
            f = self.parse_filters(q)
        except Clarify as exc:
            return AgentAnswer(answer=str(exc), status="clarification", mode="deterministic")

        if re.search(r"orcament|orcad", q):
            if re.search(r"execuc|executad|realizad", q):
                return self.execution(f)
            if re.search(r"\b(maior|menor|ranking|qual departamento|qual area|quais departamentos)\b", q):
                return self.budget_rank(f, smallest=bool(re.search(r"\bmenor\b", q)))
            return self.budget_total(f)
        if re.search(r"inadimpl", q):
            return self.default_rate(f)
        has_metric = re.search(r"quant|total|soma|valor|maior|menor|\bpor\b|media|gast|receita|despesa", q)
        if not has_metric and not (f.tipo or f.status or f.departamento or f.categoria or f.centro):
            return self.rag_answer(question)
        group = re.search(r"\bpor (departamento|categoria|centro de custo|status|mes)\b", q)
        if group:
            return self.grouped(f, group.group(1))
        if re.search(r"\b(maior|menor)\b", q):
            return self.extreme(f, smallest=bool(re.search(r"\bmenor\b", q)))
        if re.search(r"quantos|quantidade|numero de", q) and not re.search(r"quanto (foi|e|custou|gast)", q):
            return self.count(f)
        return self.total(f, liquid="liquid" in q)

    def _empty(self, f: Filters) -> AgentAnswer:
        return self.not_found(
            f"Não há lançamentos que atendam aos filtros ({f.describe()}). "
            "A base cobre competências de 01/01/2025 a 31/12/2026.")

    def total(self, f: Filters, liquid: bool = False) -> AgentAnswer:
        tipos = [f.tipo] if f.tipo else ["Receita", "Despesa"]
        lines, all_rows = [], []
        for tipo in tipos:
            ff = Filters(**{**f.__dict__, "tipo": tipo})
            rows = self._filter(ff)
            all_rows += rows
            total = sum((Decimal(r["valor_brl"]) for r in rows), Decimal("0"))
            label = "Receitas" if tipo == "Receita" else "Despesas"
            status = f" com status {f.status}" if f.status else (
                " (todos os status — despesas incorridas)" if tipo == "Despesa" else " (todos os status)")
            lines.append(f"{label}{status} {f.period_label()}: **{brl(total)}** ({len(rows)} lançamentos).")
        if not all_rows:
            return self._empty(f)
        if liquid:
            lines.append("A base não registra deduções de receita; por isso a receita líquida "
                         "coincide com as receitas registradas, conforme a definição do indicador.")
        lines.append(f"Critério: soma de valor_brl em decimal com {f.describe()}.")
        return self._done("\n".join(lines), [self._rows_source(all_rows, f)] + self._rule_source("somar"))

    def count(self, f: Filters) -> AgentAnswer:
        rows = self._filter(f)
        if not rows:
            return self._empty(f)
        return self._done(f"Foram encontrados **{len(rows)} lançamentos** {f.period_label()} "
                          f"(filtro: {f.describe()}).", [self._rows_source(rows, f)])

    def extreme(self, f: Filters, smallest: bool) -> AgentAnswer:
        rows = self._filter(f)
        if not rows:
            return self._empty(f)
        r = (min if smallest else max)(rows, key=lambda x: Decimal(x["valor_brl"]))
        word = "menor" if smallest else "maior"
        return self._done(
            f"O {word} lançamento {f.period_label()} é **{r['id_lancamento']}**: {r['tipo']} de "
            f"**{brl(Decimal(r['valor_brl']))}** ({r['categoria']} — {r['descricao']}), "
            f"departamento {r['departamento']}, competência {r['data_competencia']}, status {r['status']}.",
            [self._rows_source([r], f)])

    def grouped(self, f: Filters, by: str) -> AgentAnswer:
        rows = self._filter(f)
        if not rows:
            return self._empty(f)
        col = {"departamento": "departamento", "categoria": "categoria",
               "centro de custo": "centro_custo", "status": "status"}.get(by)
        sums: dict[str, Decimal] = defaultdict(Decimal)
        for r in rows:
            key = r[col] if col else r["data_competencia"][:7]
            sums[key] += Decimal(r["valor_brl"])
        order = sorted(sums.items(), key=(lambda kv: kv[0]) if not col else (lambda kv: -kv[1]))
        lines = [f"Totais por {by} {f.period_label()} (filtro: {f.describe()}):"]
        lines += [f"- {k}: {brl(v)}" for k, v in order]
        return self._done("\n".join(lines), [self._rows_source(rows, f)])

    def budget_total(self, f: Filters) -> AgentAnswer:
        rows = self._budget(f)
        if not rows:
            return self.not_found(f"Não há orçamento registrado para o filtro ({f.describe()}).")
        total = sum((Decimal(r["orcamento_brl"]) for r in rows), Decimal("0"))
        scope = f.departamento or f.centro or "todos os departamentos"
        return self._done(f"Orçamento aprovado {f.period_label()} para {scope}: **{brl(total)}** "
                          f"({len(rows)} registros mensais por centro de custo).",
                          [self._rows_source(rows, f, ORC)])

    def budget_rank(self, f: Filters, smallest: bool) -> AgentAnswer:
        rows = self._budget(Filters(start=f.start, end=f.end))
        if not rows:
            return self.not_found(f"Não há orçamento registrado {f.period_label()}.")
        sums: dict[str, Decimal] = defaultdict(Decimal)
        for r in rows:
            sums[r["departamento"]] += Decimal(r["orcamento_brl"])
        order = sorted(sums.items(), key=lambda kv: kv[1], reverse=not smallest)
        word = "menor" if smallest else "maior"
        lines = [f"O departamento com {word} orçamento {f.period_label()} é **{order[0][0]}**, "
                 f"com **{brl(order[0][1])}**.", "Comparativo:"]
        lines += [f"- {d}: {brl(v)}" for d, v in order]
        return self._done("\n".join(lines), [self._rows_source(rows, f, ORC)])

    def execution(self, f: Filters) -> AgentAnswer:
        budget_rows = self._budget(f)
        spent_rows = self._filter(Filters(**{**f.__dict__, "tipo": "Despesa", "status": f.status or "Pago",
                                             "categoria": None}))
        budget = sum((Decimal(r["orcamento_brl"]) for r in budget_rows), Decimal("0"))
        if not budget:
            return self.not_found(f"Não há orçamento registrado para o filtro ({f.describe()}).")
        spent = sum((Decimal(r["valor_brl"]) for r in spent_rows), Decimal("0"))
        pct = (spent / budget * 100).quantize(Decimal("0.01"))
        scope = f.departamento or f.centro or "todos os departamentos"
        return self._done(
            f"Execução orçamentária {f.period_label()} ({scope}): **{str(pct).replace('.', ',')}%** — "
            f"despesas realizadas (status {f.status or 'Pago'}) de {brl(spent)} sobre orçamento aprovado de "
            f"{brl(budget)}.",
            [self._rows_source(spent_rows, Filters(**{**f.__dict__, "tipo": "Despesa"})),
             self._rows_source(budget_rows, f, ORC)] + self._rule_source("execucao orcamentaria"))

    def default_rate(self, f: Filters) -> AgentAnswer:
        rows = self._filter(Filters(**{**f.__dict__, "tipo": "Receita", "status": None}))
        if not rows:
            return self._empty(f)
        total = sum((Decimal(r["valor_brl"]) for r in rows), Decimal("0"))
        late = [r for r in rows if r["status"] == "Atrasado"]
        late_total = sum((Decimal(r["valor_brl"]) for r in late), Decimal("0"))
        pct = (late_total / total * 100).quantize(Decimal("0.01")) if total else Decimal("0")
        return self._done(
            f"Inadimplência de recebíveis {f.period_label()}: **{str(pct).replace('.', ',')}%** — "
            f"receitas com status Atrasado de {brl(late_total)} ({len(late)} lançamentos) sobre "
            f"{brl(total)} em receitas registradas ({len(rows)} lançamentos).",
            [self._rows_source(rows, Filters(**{**f.__dict__, "tipo": "Receita"}))]
            + self._rule_source("inadimplencia"))
