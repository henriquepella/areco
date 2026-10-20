"""Agente Financeiro.

Perguntas com números (somas, contagens, orçamento, execução, inadimplência) são calculadas
de forma determinística sobre os CSVs, com `Decimal` — o modelo de linguagem nunca estima
valores. Perguntas conceituais ("o que é execução orçamentária?") usam RAG sobre
`indicadores_e_regras.json`.

Fluxo de `answer`: interpreta a pergunta em `Filters` (tipo, status, período, departamento,
categoria, centro de custo) e escolhe uma operação pelo vocabulário da pergunta.
"""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from app.agents.base import EXCERPT_CHARS, BaseAgent, brl, summarize_ids
from app.models.schemas import AgentAnswer, Source
from app.services.retrieval_service import normalize

LANC = "lancamentos_financeiros.csv"
ORC = "orcamento_mensal.csv"
REGRAS = "indicadores_e_regras.json"

# ---------------------------------------------------------------- vocabulário de período

MONTHS = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
          "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
# Só nomes completos: abreviações como "dez" e "mar" colidem com palavras comuns.
_MONTH_RE = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b")
_ORD = {"1": 1, "primeiro": 1, "2": 2, "segundo": 2, "3": 3, "terceiro": 3, "4": 4, "quarto": 4}
_QUARTER_RE = re.compile(r"\b(1|2|3|4|primeiro|segundo|terceiro|quarto)\s*[oº°]?\s*trimestre")
_SEMESTER_RE = re.compile(r"\b(1|2|primeiro|segundo)\s*[oº°]?\s*semestre")

# ---------------------------------------------------------------- vocabulário de filtros

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
_REVENUE_RE = re.compile(r"receita|faturamento|recebi|recebiveis")
_EXPENSE_RE = re.compile(r"despesa|gast[oa]|gastou|gastamos|custo|saida")
_PAID_RE = re.compile(r"\bpag[oa]s?\b|efetivamente pag|quitad|liquidad")
_PENDING_RE = re.compile(r"pendente")
_LATE_RE = re.compile(r"atrasad|vencid")
_COST_CENTER_RE = re.compile(r"\bcc\s?-?(\d00)\b")
# "financeiro" só conta como departamento se dito explicitamente (a palavra é comum nas perguntas).
_FINANCE_DEPT_RE = re.compile(r"(departamento|area|setor|equipe|time)\s+(de\s+|do\s+)?financeiro")

# ---------------------------------------------------------------- vocabulário de operações

_DEFINITION_RE = re.compile(r"o que (e|sao|significa)|defini|como (e|sao) calculad|como calcul|criterio|regras? de calculo")
_BUDGET_RE = re.compile(r"orcament|orcad")
_EXECUTION_RE = re.compile(r"execuc|executad|realizad")
_RANKING_RE = re.compile(r"\b(maior|menor|ranking|qual departamento|qual area|quais departamentos)\b")
_DEFAULT_RATE_RE = re.compile(r"inadimpl")
_METRIC_RE = re.compile(r"quant|total|soma|valor|maior|menor|\bpor\b|media|gast|receita|despesa")
_GROUP_BY_RE = re.compile(r"\bpor (departamento|categoria|centro de custo|status|mes)\b")
_EXTREME_RE = re.compile(r"\b(maior|menor)\b")
_SMALLEST_RE = re.compile(r"\bmenor\b")
_COUNT_RE = re.compile(r"quantos|quantidade|numero de")
_HOW_MUCH_RE = re.compile(r"quanto (foi|e|custou|gast)")

GROUP_COLUMNS = {"departamento": "departamento", "categoria": "categoria",
                 "centro de custo": "centro_custo", "status": "status"}  # "mes" agrupa por AAAA-MM


class Clarify(Exception):
    """A pergunta precisa de um dado que falta (ex.: o ano). A mensagem vai para o usuário."""


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


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_period(q: str) -> tuple[date | None, date | None]:
    """Período citado no texto normalizado: trimestre, semestre, meses e/ou anos.
    Sem período, devolve (None, None). Mês, trimestre ou semestre sem ano gera `Clarify`."""
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


def sum_decimal(rows: list[dict], column: str) -> Decimal:
    """Soma uma coluna monetária convertendo para Decimal (nunca float)."""
    return sum((Decimal(r[column]) for r in rows), Decimal("0"))


def format_percent(value: Decimal) -> str:
    """Decimal("28.45") -> "28,45" (o símbolo % fica no texto da resposta)."""
    return str(value).replace(".", ",")


class FinancialAgent(BaseAgent):
    id = "financeiro"
    name = "Agente Financeiro"
    description = ("Calcula e explica receitas, despesas, orçamento, execução orçamentária e "
                   "inadimplência a partir dos lançamentos e orçamentos da base financeira.")

    # ------------------------------------------------------------ interpretação da pergunta

    def _departments(self) -> list[str]:
        return sorted({r["departamento"] for r in self.kb.tables.get(ORC, [])})

    def _find_department(self, q: str) -> str | None:
        found = None  # se mais de um casar, vale o último em ordem alfabética
        for dep in self._departments():
            nd = normalize(dep)
            if nd == "financeiro":
                matches = bool(_FINANCE_DEPT_RE.search(q))
            elif nd == "marketing":  # "marketing" também é categoria; vale como área se não houver "categoria"
                matches = bool(re.search(r"\bmarketing\b", q)) and "categoria" not in q
            else:
                matches = bool(re.search(rf"\b{nd}\b", q))
            if matches:
                found = dep
        return found

    @staticmethod
    def _find_category(q: str) -> str | None:
        return next((cat for cat, pattern in CATEGORY_ALIASES.items() if re.search(pattern, q)), None)

    def parse_filters(self, q: str) -> Filters:
        """Filtros a partir do texto já normalizado. Pode levantar `Clarify` (período sem ano)."""
        f = Filters()
        has_revenue, has_expense = _REVENUE_RE.search(q), _EXPENSE_RE.search(q)
        if has_revenue and not has_expense:
            f.tipo = "Receita"
        elif has_expense and not has_revenue:
            f.tipo = "Despesa"
        if _PAID_RE.search(q):
            f.status = "Pago"
        elif _PENDING_RE.search(q):
            f.status = "Pendente"
        elif _LATE_RE.search(q):
            f.status = "Atrasado"
        f.start, f.end = parse_period(q)
        cc = _COST_CENTER_RE.search(q)
        f.centro = f"CC{cc.group(1)}" if cc else None
        f.departamento = self._find_department(q)
        f.categoria = self._find_category(q)
        return f

    # ------------------------------------------------------------ consultas aos CSVs

    def _filter(self, f: Filters) -> list[dict]:
        """Lançamentos que atendem aos filtros (período pela data de competência)."""
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
        """Orçamentos mensais no período, departamento e centro de custo dos filtros."""
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
        """Fonte de uma consulta determinística: o filtro aplicado e os registros usados."""
        if doc == LANC:
            ids = [r["id_lancamento"] for r in rows]
        else:
            ids = [f"{r['ano']}-{r['mes']} {r['centro_custo']}" for r in rows]
        return Source(document=doc, domain=self.id, section="consulta determinística",
                      excerpt=f"Filtro: {f.describe()}. {len(rows)} registro(s): {summarize_ids(ids)}")

    def _rule_source(self, keyword: str) -> list[Source]:
        """Primeiro trecho de indicadores_e_regras.json que contém a palavra-chave."""
        for c in self.kb.chunks.get(self.id, []):
            if c.document == REGRAS and keyword in normalize(c.text):
                return [Source(document=REGRAS, domain=self.id, section=c.section,
                               excerpt=c.text[:EXCERPT_CHARS])]
        return []

    # ------------------------------------------------------------ resposta

    def answer(self, question: str) -> AgentAnswer:
        """Escolhe a operação, nesta ordem: definição (RAG) → orçamento (execução, ranking ou
        total) → inadimplência → RAG se não houver métrica nem filtro → agrupamento → maior/menor
        → contagem → soma."""
        if LANC not in self.kb.tables:
            return self.not_found("A base de lançamentos financeiros não está carregada.")
        q = normalize(question)
        if _DEFINITION_RE.search(q):
            return self.rag_answer(question, docs={REGRAS})
        try:
            f = self.parse_filters(q)
        except Clarify as exc:
            return AgentAnswer(answer=str(exc), status="clarification", mode="deterministic")

        if _BUDGET_RE.search(q):
            if _EXECUTION_RE.search(q):
                return self.execution(f)
            if _RANKING_RE.search(q):
                return self.budget_rank(f, smallest=bool(_SMALLEST_RE.search(q)))
            return self.budget_total(f)
        if _DEFAULT_RATE_RE.search(q):
            return self.default_rate(f)
        has_any_filter = f.tipo or f.status or f.departamento or f.categoria or f.centro
        if not _METRIC_RE.search(q) and not has_any_filter:
            return self.rag_answer(question)
        group = _GROUP_BY_RE.search(q)
        if group:
            return self.grouped(f, group.group(1))
        if _EXTREME_RE.search(q):
            return self.extreme(f, smallest=bool(_SMALLEST_RE.search(q)))
        if _COUNT_RE.search(q) and not _HOW_MUCH_RE.search(q):
            return self.count(f)
        return self.total(f, liquid="liquid" in q)

    def _empty(self, f: Filters) -> AgentAnswer:
        return self.not_found(
            f"Não há lançamentos que atendam aos filtros ({f.describe()}). "
            "A base cobre competências de 01/01/2025 a 31/12/2026.")

    def total(self, f: Filters, liquid: bool = False) -> AgentAnswer:
        """Soma por tipo. Sem tipo na pergunta, mostra receitas e despesas separadas."""
        tipos = [f.tipo] if f.tipo else ["Receita", "Despesa"]
        lines, all_rows = [], []
        for tipo in tipos:
            rows = self._filter(replace(f, tipo=tipo))
            all_rows += rows
            label = "Receitas" if tipo == "Receita" else "Despesas"
            if f.status:
                status = f" com status {f.status}"
            elif tipo == "Despesa":
                status = " (todos os status — despesas incorridas)"
            else:
                status = " (todos os status)"
            lines.append(f"{label}{status} {f.period_label()}: **{brl(sum_decimal(rows, 'valor_brl'))}** "
                         f"({len(rows)} lançamentos).")
        if not all_rows:
            return self._empty(f)
        if liquid:
            lines.append("A base não registra deduções de receita; por isso a receita líquida "
                         "coincide com as receitas registradas, conforme a definição do indicador.")
        lines.append(f"Critério: soma de valor_brl em decimal com {f.describe()}.")
        return self.deterministic_answer("\n".join(lines),
                                         [self._rows_source(all_rows, f)] + self._rule_source("somar"))

    def count(self, f: Filters) -> AgentAnswer:
        rows = self._filter(f)
        if not rows:
            return self._empty(f)
        return self.deterministic_answer(
            f"Foram encontrados **{len(rows)} lançamentos** {f.period_label()} (filtro: {f.describe()}).",
            [self._rows_source(rows, f)])

    def extreme(self, f: Filters, smallest: bool) -> AgentAnswer:
        """Maior (ou menor) lançamento individual."""
        rows = self._filter(f)
        if not rows:
            return self._empty(f)
        r = (min if smallest else max)(rows, key=lambda x: Decimal(x["valor_brl"]))
        word = "menor" if smallest else "maior"
        return self.deterministic_answer(
            f"O {word} lançamento {f.period_label()} é **{r['id_lancamento']}**: {r['tipo']} de "
            f"**{brl(Decimal(r['valor_brl']))}** ({r['categoria']} — {r['descricao']}), "
            f"departamento {r['departamento']}, competência {r['data_competencia']}, status {r['status']}.",
            [self._rows_source([r], f)])

    def grouped(self, f: Filters, by: str) -> AgentAnswer:
        """Totais por coluna (do maior para o menor) ou por mês (em ordem cronológica)."""
        rows = self._filter(f)
        if not rows:
            return self._empty(f)
        col = GROUP_COLUMNS.get(by)
        sums: dict[str, Decimal] = defaultdict(Decimal)
        for r in rows:
            key = r[col] if col else r["data_competencia"][:7]
            sums[key] += Decimal(r["valor_brl"])
        order = sorted(sums.items(), key=(lambda kv: -kv[1]) if col else (lambda kv: kv[0]))
        lines = [f"Totais por {by} {f.period_label()} (filtro: {f.describe()}):"]
        lines += [f"- {k}: {brl(v)}" for k, v in order]
        return self.deterministic_answer("\n".join(lines), [self._rows_source(rows, f)])

    def budget_total(self, f: Filters) -> AgentAnswer:
        rows = self._budget(f)
        if not rows:
            return self.not_found(f"Não há orçamento registrado para o filtro ({f.describe()}).")
        scope = f.departamento or f.centro or "todos os departamentos"
        return self.deterministic_answer(
            f"Orçamento aprovado {f.period_label()} para {scope}: **{brl(sum_decimal(rows, 'orcamento_brl'))}** "
            f"({len(rows)} registros mensais por centro de custo).",
            [self._rows_source(rows, f, ORC)])

    def budget_rank(self, f: Filters, smallest: bool) -> AgentAnswer:
        """Departamentos ordenados pelo orçamento do período (ignora os outros filtros)."""
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
        return self.deterministic_answer("\n".join(lines), [self._rows_source(rows, f, ORC)])

    def execution(self, f: Filters) -> AgentAnswer:
        """Despesas realizadas (status Pago, salvo outro citado) ÷ orçamento aprovado."""
        status = f.status or "Pago"
        budget_rows = self._budget(f)
        spent_rows = self._filter(replace(f, tipo="Despesa", status=status, categoria=None))
        budget = sum_decimal(budget_rows, "orcamento_brl")
        if not budget:
            return self.not_found(f"Não há orçamento registrado para o filtro ({f.describe()}).")
        spent = sum_decimal(spent_rows, "valor_brl")
        pct = (spent / budget * 100).quantize(Decimal("0.01"))
        scope = f.departamento or f.centro or "todos os departamentos"
        return self.deterministic_answer(
            f"Execução orçamentária {f.period_label()} ({scope}): **{format_percent(pct)}%** — "
            f"despesas realizadas (status {status}) de {brl(spent)} sobre orçamento aprovado de "
            f"{brl(budget)}.",
            [self._rows_source(spent_rows, replace(f, tipo="Despesa")),
             self._rows_source(budget_rows, f, ORC)] + self._rule_source("execucao orcamentaria"))

    def default_rate(self, f: Filters) -> AgentAnswer:
        """Receitas com status Atrasado ÷ total de receitas registradas."""
        rows = self._filter(replace(f, tipo="Receita", status=None))
        if not rows:
            return self._empty(f)
        total = sum_decimal(rows, "valor_brl")
        late = [r for r in rows if r["status"] == "Atrasado"]
        late_total = sum_decimal(late, "valor_brl")
        pct = (late_total / total * 100).quantize(Decimal("0.01")) if total else Decimal("0")
        return self.deterministic_answer(
            f"Inadimplência de recebíveis {f.period_label()}: **{format_percent(pct)}%** — "
            f"receitas com status Atrasado de {brl(late_total)} ({len(late)} lançamentos) sobre "
            f"{brl(total)} em receitas registradas ({len(rows)} lançamentos).",
            [self._rows_source(rows, replace(f, tipo="Receita"))] + self._rule_source("inadimplencia"))
