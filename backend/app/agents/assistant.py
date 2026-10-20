"""Conversa guiada (POST /api/assist): domínio escolhido, perguntas sugeridas, confirmação de
intenção e coleta de parâmetros.

Esta camada só decide o que perguntar ao usuário. Toda resposta final vem do agente
especializado (o mesmo usado por /api/chat), então cálculos, fontes e recusas são os mesmos.

O estado da conversa fica no cliente: cada resposta traz `follow_up` com a intenção e os
parâmetros já informados, que o frontend devolve na mensagem seguinte.
"""
from __future__ import annotations

import logging
import re

from app.agents.intents import SLOT_LABELS, Intent, IntentCatalog, Slot, extract_period, extract_year
from app.agents.orchestrator import OUT_OF_SCOPE, Orchestrator, score_question
from app.models.schemas import AssistRequest, AssistResponse, ChatResponse, Choice, FollowUp, Routing
from app.services.document_service import DOMAINS
from app.services.retrieval_service import normalize, tokenize

logger = logging.getLogger(__name__)

FREE_YEAR = "_completar_ano"  # pergunta livre que só precisa do ano
ASKED = "_perguntado"  # parâmetro que acabou de ser pedido (para reconhecer resposta inválida)

# Gasto genérico ("quanto a empresa gastou?") sem dizer se é despesa paga, orçamento ou receita.
_SPEND_RE = re.compile(r"\bgast\w*|\bcustou\b|\bdesembols\w*")
_EXPLICIT_RE = re.compile(r"despesa|receita|orcament|orcad|lancamento|inadimpl|execuc"
                          r"|\bpag(o|a|os|as|amos|ou|aram)\b|pendente|atrasad")
SPEND_OPTIONS = (("fin_despesas_pagas", "Despesas pagas"),
                 ("fin_orcamento_periodo", "Orçamento aprovado"),
                 ("fin_receitas_recebidas", "Receitas recebidas"))


class Assistant:
    def __init__(self, orchestrator: Orchestrator, catalog: IntentCatalog):
        self.orchestrator = orchestrator
        self.catalog = catalog
        self.financial = orchestrator.agents["financeiro"]

    def respond(self, req: AssistRequest) -> AssistResponse:
        if req.intent:
            resp = self._continue_intent(req)
        else:
            resp = self._free_text(req.question, req.domain, req.confirmed)
        logger.info("assist_handled", extra={
            "category": resp.category, "status": resp.status, "intent": resp.intent,
            "follow_up": resp.follow_up.kind if resp.follow_up else None, "active_domain": resp.active_domain})
        return resp

    # ------------------------------------------------------------ pergunta livre

    def _free_text(self, text: str, domain: str | None, confirmed: bool) -> AssistResponse:
        c = self.orchestrator.classify(text)
        if c.routing.method == "guard":  # credenciais/dados de terceiros: mesma recusa do /api/chat
            return self._wrap(self.orchestrator.handle(text), active_domain=domain)
        if domain and not confirmed and c.category not in (domain, "indefinido"):
            return self._offer_switch(text, domain, c.category)
        target = domain or (None if c.category == "indefinido" else c.category)
        if target is None:
            return self._undefined(text, c)
        routing, confidence = (None, None) if domain else (c.routing, c.confidence)
        return (self._confirm_spend(text, target)
                or self._ask_missing_for_known_question(text, target)
                or self._answer(text, target, routing=routing, confidence=confidence))

    def _offer_switch(self, text: str, current: str, other: str) -> AssistResponse:
        cur, oth = self._agent_name(current), self._agent_name(other)
        options = [Choice(label=f"Falar com o {oth}", question=text, domain=other, confirmed=True),
                   Choice(label=f"Continuar com o {cur}", question=text, domain=current, confirmed=True)]
        return self._follow_up(text, current, f"Essa pergunta parece ser para o {oth}. Quer mudar de "
                                              "agente? Sua pergunta será mantida.",
                               FollowUp(kind="switch_domain", options=options))

    def _undefined(self, text: str, c) -> AssistResponse:
        candidates = [d for d, s in c.routing.scores.items() if s > 0]
        if candidates:
            names = " ou o ".join(self._agent_name(d) for d in candidates)
            resp = self._follow_up(text, None, f"Sua pergunta pode ser para o {names}. Qual deles deve responder?",
                                   FollowUp(kind="confirm_domain", options=[
                                       Choice(label=self._agent_name(d), question=text, domain=d, confirmed=True)
                                       for d in candidates]))
        else:
            resp = self._follow_up(text, None, OUT_OF_SCOPE, FollowUp(kind="choose_domain", options=[
                Choice(label=self._agent_name(d), domain=d) for d in DOMAINS]))
            resp.status = "out_of_scope"
        resp.routing, resp.confidence = c.routing, c.confidence
        return resp

    def _confirm_spend(self, text: str, domain: str) -> AssistResponse | None:
        """"Quanto gastamos em janeiro?" pode ser despesa paga, orçamento ou receita: pergunta qual."""
        q = normalize(text)
        if (domain != "financeiro" or not _SPEND_RE.search(q) or _EXPLICIT_RE.search(q)
                or self.financial.find_category(q)):
            return None
        period, partial = extract_period(text)
        department = self.financial.find_department(q)
        slots = {k: v for k, v in (("periodo", period), ("periodo_parcial", partial),
                                   ("departamento", department)) if v}
        when = f" em {period or partial}" if (period or partial) else ""
        where = f" do departamento {department}" if department else ""
        options = [Choice(label=f"{label}{where}{when}", question=f"{label}{where}{when}", intent=intent_id,
                          domain="financeiro", slots=slots, confirmed=True)
                   for intent_id, label in SPEND_OPTIONS if self.catalog.get(intent_id)]
        if len(options) < 2:
            return None
        answer = (f"Você quer consultar o total de despesas pagas, o orçamento dos departamentos ou as "
                  f"receitas recebidas{where}{when}?")
        return self._follow_up(text, "financeiro", answer, FollowUp(kind="confirm_intent", slots=slots, options=options))

    def _ask_missing_for_known_question(self, text: str, domain: str) -> AssistResponse | None:
        """Pergunta livre equivalente a uma sugestão, mas sem um parâmetro: pede só o que falta."""
        q = normalize(text)
        for intent in self.catalog.by_domain(domain):
            if not (intent.trigger and intent.slots and re.search(intent.trigger, q)):
                continue
            slots: dict[str, str] = {}
            self._fill(intent, text, slots, answering=False)
            missing = intent.missing(slots)
            return self._ask(text, intent, slots, missing, retry=False) if missing else None
        return None

    # ------------------------------------------------------------ intenção em andamento

    def _continue_intent(self, req: AssistRequest) -> AssistResponse:
        text, slots = req.question, dict(req.slots)
        asked = slots.pop(ASKED, None)
        if req.intent == FREE_YEAR:
            return self._continue_free_year(req, slots)
        intent = self.catalog.get(req.intent)
        if intent is None:
            return self._free_text(text, req.domain, req.confirmed)
        filled = self._fill(intent, text, slots, answering=asked is not None)
        missing = intent.missing(slots)
        if missing is None:
            return self._answer(intent.build(slots), intent.domain, intent=intent.id)
        unanswered = asked == missing.name and not filled
        if unanswered and self._looks_like_new_question(text):  # o usuário mudou de assunto
            return self._free_text(text, req.domain or intent.domain, req.confirmed)
        return self._ask(text, intent, slots, missing, retry=unanswered)

    def _continue_free_year(self, req: AssistRequest, slots: dict[str, str]) -> AssistResponse:
        base, year, domain = slots.get("pergunta"), extract_year(req.question), req.domain
        if base and domain and year:
            return self._answer(f"{base} em {year}", domain)
        if not (base and domain) or self._looks_like_new_question(req.question):
            return self._free_text(req.question, domain, req.confirmed)
        resp = self._answer(base, domain)  # repete o pedido do ano
        resp.answer = f"Não identifiquei {SLOT_LABELS['ano']} na resposta. {resp.answer}"
        return resp

    def _fill(self, intent: Intent, text: str, slots: dict[str, str], answering: bool) -> dict[str, str]:
        """Completa `slots` com o que o texto informa; devolve só o que foi acrescentado."""
        added: dict[str, str] = {}
        for slot in intent.slots + self.catalog.optional_slots(intent):
            if slots.get(slot.name):
                continue
            found = self.catalog.extract(slot, text, slots, answering)
            added.update(found)
            slots.update(found)
        return added

    def _ask(self, text: str, intent: Intent, slots: dict[str, str], slot: Slot, retry: bool) -> AssistResponse:
        partial = slots.get("periodo_parcial") if slot.name == "periodo" else None
        if partial:  # o mês já foi informado: falta só o ano
            prompt, options = f"Para qual ano você quer consultar {partial}?", self.catalog.years(intent.domain)
        else:
            prompt, options = slot.prompt, slot.options
        if retry:
            prompt = f"Não identifiquei {SLOT_LABELS[slot.name]} na resposta. {prompt}"
        slots = {**slots, ASKED: slot.name}
        choices = [Choice(label=o, question=o, intent=intent.id, domain=intent.domain, slots=slots) for o in options]
        return self._follow_up(text, intent.domain, prompt, FollowUp(
            kind="slot", intent=intent.id, slot=slot.name, slots=slots, options=choices), intent=intent.id)

    # ------------------------------------------------------------ resposta do agente

    def _answer(self, question: str, domain: str, *, intent: str | None = None,
                routing: Routing | None = None, confidence: float | None = None) -> AssistResponse:
        agent = self.orchestrator.agents[domain]
        result = agent.answer(question)
        resp = AssistResponse(
            question=question, category=domain, agent=agent.name, answer=result.answer, status=result.status,
            sources=result.sources, confidence=confidence, mode=result.mode, notice=result.notice,
            routing=routing or self._context_routing(question, "domínio escolhido na conversa"),
            active_domain=domain, intent=intent)
        if result.missing == "ano":
            slots = {"pergunta": question, ASKED: "ano"}
            resp.follow_up = FollowUp(kind="slot", intent=FREE_YEAR, slot="ano", slots=slots, options=[
                Choice(label=y, question=y, intent=FREE_YEAR, domain=domain, slots=slots)
                for y in self.catalog.years(domain)])
        elif result.missing == "protocolo" and self.catalog.get("ouv_status_protocolo"):
            resp.follow_up = FollowUp(kind="slot", intent="ouv_status_protocolo", slot="protocolo",
                                      slots={ASKED: "protocolo"})
        elif result.status == "not_found":
            similar = self.catalog.similar(question, domain)
            if similar:
                resp.follow_up = FollowUp(kind="suggest", options=[
                    Choice(label=i.label, question=i.label, intent=i.id, domain=i.domain) for i in similar])
        return resp

    # ------------------------------------------------------------ utilitários

    def _agent_name(self, domain: str) -> str:
        return self.orchestrator.agents[domain].name

    @staticmethod
    def _looks_like_new_question(text: str) -> bool:
        return "?" in text or len(tokenize(text)) >= 4

    @staticmethod
    def _context_routing(text: str, reason: str) -> Routing:
        scores, matched = score_question(text)
        return Routing(method="context", scores=scores, matched_terms=matched, reason=reason)

    def _follow_up(self, text: str, domain: str | None, answer: str, follow_up: FollowUp,
                   intent: str | None = None) -> AssistResponse:
        return AssistResponse(
            question=text, category=domain or "indefinido", agent=self._agent_name(domain) if domain else None,
            answer=answer, status="clarification", sources=[], confidence=None, mode="none",
            routing=self._context_routing(text, "aguardando resposta do usuário"),
            active_domain=domain, intent=intent, follow_up=follow_up)

    @staticmethod
    def _wrap(resp: ChatResponse, active_domain: str | None) -> AssistResponse:
        return AssistResponse(**resp.model_dump(), active_domain=active_domain)
