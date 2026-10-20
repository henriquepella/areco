"""Agente de Informações Internas: políticas, procedimentos e FAQ (Markdown + JSON)."""
import re

from app.agents.base import BaseAgent, hit_to_source
from app.models.schemas import AgentAnswer
from app.services.retrieval_service import SearchHit, normalize

_SECRET = re.compile(r"senha|credencia|token|chave de api|chave da api")
_REVEAL = re.compile(r"\b(diga|digam|passe|passa|informe|mostre|revele|envie|forneca|descobr\w*|"
                     r"me de|outr[oa]s?|colega|alguem|terceir\w*)\b")
_OTHERS_DATA = re.compile(r"(holerite|salario|cpf|dados pessoais|saldo de ferias|endereco|telefone)"
                          r".*\b(outr[oa]s?|colega|alguem|terceir\w*)\b")
GUARD_DOCS = ("04_seguranca_da_informacao.md", "09_politica_privacidade.md")


def is_sensitive_request(question: str) -> bool:
    q = normalize(question)
    return bool((_SECRET.search(q) and _REVEAL.search(q)) or _OTHERS_DATA.search(q))


class InternalInfoAgent(BaseAgent):
    id = "informacoes_internas"
    name = "Agente de Informações Internas"
    description = ("Responde sobre políticas, procedimentos, processos internos e FAQ "
                   "institucional (férias, reembolso, compras, acessos, segurança, RH, privacidade).")
    extra_rules = ("9. Diferencie claramente o que está documentado do que não está.\n"
                   "10. Casos individuais (saldo de férias, holerite, elegibilidade) devem ser "
                   "encaminhados ao canal indicado no documento, sem presumir regras.")

    def answer(self, question: str) -> AgentAnswer:
        if is_sensitive_request(question):
            return self.refuse()
        return self.rag_answer(question)

    def refuse(self) -> AgentAnswer:
        hits = [SearchHit(c, 0.0, 0.0) for c in self.kb.chunks[self.id] if c.document in GUARD_DOCS]
        return AgentAnswer(
            status="refused", mode="deterministic", sources=[hit_to_source(h) for h in hits],
            answer=("Não posso fornecer senhas, credenciais ou dados pessoais de outras pessoas. "
                    "A política de segurança da informação determina nunca compartilhar senhas, "
                    "tokens ou chaves, e a política de privacidade proíbe revelar dados de outros "
                    "colaboradores ou clientes. Para problemas de acesso, abra um chamado no portal "
                    "de TI; suspeitas de incidente devem ser reportadas a seguranca@aurora.example."))

    def extractive_text(self, hits: list[SearchHit], lead: bool = True) -> str:
        """A resposta curada da FAQ vem primeiro (se for relevante); os documentos, como detalhe."""
        faq = {item.get("id"): item for item in self.kb.json_docs.get("faq_interno.json", [])
               if isinstance(item, dict)}
        best = next((h for h in hits if h.chunk.document == "faq_interno.json"
                     and h.chunk.section in faq and h.score >= hits[0].score * 0.6), None)
        if best is None:
            return super().extractive_text(hits, lead)
        item = faq[best.chunk.section]
        origin = item.get("documento", best.chunk.document)
        rest = [h for h in hits if h.chunk.document != "faq_interno.json"]
        head = item.get("resposta", "")
        if not any(h.chunk.document == origin for h in rest):
            head += f"\n\nFonte: **{origin}**"
        return head + ("\n\n" + super().extractive_text(rest, lead=False) if rest else "")
