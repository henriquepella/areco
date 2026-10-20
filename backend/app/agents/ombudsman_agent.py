"""Agente de Ouvidoria.

- Protocolo: busca exata no CSV (nunca inventa; formato OUV-NNNN).
- Listas oficiais (status, prioridades, categorias, regras): lidas de referencia_ouvidoria.json.
- Contagens/filtros: determinísticos sobre manifestacoes.csv.
- Registro de manifestação: NÃO existe integração transacional; o agente explica isso
  e nunca afirma que algo foi registrado, encaminhado ou resolvido.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date

from app.agents.base import EXCERPT_CHARS, BaseAgent, summarize_ids
from app.agents.financial_agent import Clarify, parse_period
from app.models.schemas import AgentAnswer, Source
from app.services.retrieval_service import normalize

MANIF = "manifestacoes.csv"
REF = "referencia_ouvidoria.json"

_PROTO = re.compile(r"\bouv\s*-?\s*(\d{1,6})\b")
_PERSONAL = re.compile(
    r"(nome|cpf|e-?mail|telefone|endereco|contato|identidade)\s+(do|da|de)\s+"
    r"(manifestante|cliente|pessoa|cidadao|reclamante|autor|usuario)|quem (abriu|fez|registrou|enviou)")
_REGISTER = re.compile(
    r"(registrar|abrir|fazer|enviar|criar|formalizar|protocolar)\s+(uma?\s+|minha\s+|meu\s+)?"
    r"(nova\s+|novo\s+)?(reclamac|manifestac|denuncia|sugest|elogio|protocolo|queixa)|quero reclamar")
_COUNT = re.compile(r"\bquant[oa]s\b|numero de|total de|\bpor (status|categoria|prioridade|canal|area)\b")
_GROUP_BY = re.compile(r"\bpor (status|categoria|prioridade|canal|area)\b")
_PROTOCOL_WORD = re.compile(r"\bprotocolo\b")
_RULES_QUESTION = re.compile(r"\bregras?\b|\bprazo\b|orientac|como funciona")
CHANNELS = ("Portal", "E-mail", "Telefone", "Chat")
GROUP_COLUMNS = {"canal": "canal_entrada", "area": "area_responsavel"}  # demais: nome da coluna
# (chave em referencia_ouvidoria.json, palavras que pedem a lista, rótulo da resposta)
REFERENCE_LISTS = (
    ("status_validos", r"\bstatus\b|situac", "Status possíveis"),
    ("prioridades_validas", r"prioridade", "Prioridades possíveis"),
    ("categorias", r"categoria|tipos? de manifestac", "Categorias de manifestação"),
)


class OmbudsmanAgent(BaseAgent):
    id = "ouvidoria"
    name = "Agente de Ouvidoria"
    description = ("Consulta protocolos de manifestações (reclamações, solicitações, elogios, "
                   "sugestões), status, categorias e orientações de ouvidoria documentadas.")
    extra_rules = ("9. A base não contém dados pessoais; nunca tente identificar manifestantes.\n"
                   "10. O prazo de 10 dias é parâmetro demonstrativo, não prazo legal.\n"
                   "11. Status 'Respondido' não é necessariamente 'Concluído'.")

    @property
    def ref(self) -> dict:
        data = self.kb.json_docs.get(REF)
        return data if isinstance(data, dict) else {}

    def _ref_source(self, key: str) -> list[Source]:
        """Trecho de referencia_ouvidoria.json correspondente à chave (ex.: "regras")."""
        for c in self.kb.chunks.get(self.id, []):
            if c.document == REF and c.section == key:
                return [Source(document=REF, domain=self.id, section=key, excerpt=c.text[:EXCERPT_CHARS])]
        return []

    def answer(self, question: str) -> AgentAnswer:
        """Ordem: pedido de identidade (recusa) → protocolo → número sem formato → pedido de
        registro → contagem → listas oficiais → regras → RAG."""
        q = normalize(question)
        if _PERSONAL.search(q):
            return self.deterministic_answer(
                "Não posso identificar manifestantes. A base de ouvidoria é anonimizada e não "
                "contém nomes, CPF, e-mail ou telefone; a regra do domínio proíbe inferir a "
                "identidade de quem abriu a manifestação.", self._ref_source("regras"), "refused")
        m = _PROTO.search(q)
        if m:
            return self.protocol(f"OUV-{int(m.group(1)):04d}")
        if _PROTOCOL_WORD.search(q) and re.search(r"\d", q):
            return AgentAnswer(status="clarification", mode="deterministic",
                               answer="Informe o protocolo no formato OUV-NNNN (por exemplo, OUV-0002).")
        if _REGISTER.search(q):
            return self.cannot_register()
        if _COUNT.search(q):
            try:
                return self.count(q)
            except Clarify as exc:
                return AgentAnswer(answer=str(exc), status="clarification", mode="deterministic")
        for key, words, label in REFERENCE_LISTS:
            if re.search(words, q) and key in self.ref:
                values = ", ".join(self.ref[key])
                return self.deterministic_answer(
                    f"{label} na ouvidoria, conforme a referência do domínio: {values}.", self._ref_source(key))
        if _RULES_QUESTION.search(q) and "regras" in self.ref:
            rules = "\n".join(f"- {r}" for r in self.ref["regras"])
            return self.deterministic_answer(f"Regras documentadas para a ouvidoria:\n{rules}",
                                             self._ref_source("regras"))
        return self.rag_answer(question)

    # ------------------------------------------------------------ protocolo

    def protocol(self, proto: str) -> AgentAnswer:
        row = next((r for r in self.kb.tables.get(MANIF, []) if r.get("protocolo") == proto), None)
        if row is None:
            searched = Source(document=MANIF, domain=self.id, section="busca exata por protocolo",
                              excerpt=f"Nenhum registro com protocolo = {proto} entre "
                                      f"{len(self.kb.tables.get(MANIF, []))} manifestações.")
            return AgentAnswer(
                status="not_found", mode="deterministic", sources=[searched] + self._ref_source("regras"),
                answer=(f"Não encontrei o protocolo **{proto}** na base de manifestações. Não é "
                        "possível confirmar que ele exista nem informar status. Verifique o número "
                        "(formato OUV-NNNN)."))
        lines = [
            f"Protocolo **{proto}** (registro sintético da base):",
            f"- Status atual: **{row['status']}**",
            f"- Categoria / assunto: {row['categoria']} — {row['assunto']}",
            f"- Prioridade: {row['prioridade']} · Canal de entrada: {row['canal_entrada']}",
            f"- Área responsável: {row['area_responsavel']}",
            f"- Data de abertura: {date.fromisoformat(row['data_abertura']):%d/%m/%Y}",
        ]
        if row.get("data_resposta"):
            lines.append(f"- Data de resposta: {date.fromisoformat(row['data_resposta']):%d/%m/%Y} — "
                         f"{row.get('resposta_resumo', '')}")
        else:
            lines.append("- Resposta: ainda não há resposta registrada na base.")
        if row["status"] == "Respondido":
            lines.append("Observação: status Respondido não significa necessariamente Concluído.")
        lines.append(f"Prazo de referência: {row.get('prazo_referencia_dias', '10')} dias "
                     "(parâmetro demonstrativo, não prazo legal).")
        excerpt = "; ".join(f"{k}: {v}" for k, v in row.items()
                            if v and k not in {"observacao", "dados_pessoais"})
        return self.deterministic_answer(
            "\n".join(lines),
            [Source(document=MANIF, domain=self.id, section=proto, excerpt=excerpt[:EXCERPT_CHARS])])

    def cannot_register(self) -> AgentAnswer:
        channels = sorted({r["canal_entrada"] for r in self.kb.tables.get(MANIF, [])})
        return self.deterministic_answer(
            "Este assistente **não registra** manifestações: não há integração com um sistema "
            "transacional de ouvidoria, então nenhuma reclamação foi registrada e nenhum protocolo "
            "foi gerado. Na base consultada, as manifestações chegam pelos canais "
            f"{', '.join(channels)}, mas os documentos fornecidos não informam endereços nem o "
            "procedimento de abertura. O registro dependeria de uma integração futura e autorizada.",
            self._ref_source("regras"))

    # ------------------------------------------------------------ contagens

    def _filter_manifestations(self, q: str) -> tuple[list[dict], list[str]]:
        """Aplica, em ordem, os filtros citados (status, categoria, prioridade, canal e período
        de abertura). Devolve as manifestações e a descrição de cada critério."""
        rows = self.kb.tables.get(MANIF, [])
        criteria = []
        for field, values in (("status", self.ref.get("status_validos", [])),
                              ("categoria", self.ref.get("categorias", []))):
            for v in values:
                nv = normalize(v)
                stem = nv[:max(5, len(nv) - 2)]  # aceita plural/feminino: sugestões, abertas
                if re.search(rf"\b{re.escape(stem)}", q):
                    rows = [r for r in rows if r[field] == v]
                    criteria.append(f"{field}={v}")
                    break
        if "prioridade" in q:
            for v in self.ref.get("prioridades_validas", []):
                if re.search(rf"\b{normalize(v)}\b", q):
                    rows = [r for r in rows if r["prioridade"] == v]
                    criteria.append(f"prioridade={v}")
        for channel in CHANNELS:
            if re.search(rf"\b{normalize(channel)}\b", q):
                rows = [r for r in rows if r["canal_entrada"] == channel]
                criteria.append(f"canal={channel}")
        start, end = parse_period(q)  # pode levantar Clarify (mês sem ano)
        if start:
            rows = [r for r in rows if start <= date.fromisoformat(r["data_abertura"]) <= end]
            criteria.append(f"data_abertura de {start:%d/%m/%Y} a {end:%d/%m/%Y}")
        return rows, criteria

    def count(self, q: str) -> AgentAnswer:
        """Quantidade de manifestações filtradas e, se pedido, distribuição "por <campo>"."""
        rows, criteria = self._filter_manifestations(q)
        desc = "; ".join(criteria) or "todas as manifestações"
        lines = [f"Foram encontradas **{len(rows)} manifestações** ({desc})."]
        group = _GROUP_BY.search(q)
        if group and rows:
            col = GROUP_COLUMNS.get(group.group(1), group.group(1))
            lines += [f"- {k}: {v}" for k, v in Counter(r[col] for r in rows).most_common()]
        shown = summarize_ids([r["protocolo"] for r in rows])
        return self.deterministic_answer("\n".join(lines), [Source(
            document=MANIF, domain=self.id, section="consulta determinística",
            excerpt=f"Filtro: {desc}. Protocolos: {shown or 'nenhum'}")],
            "answered" if rows else "not_found")
