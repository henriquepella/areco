# Diário de desenvolvimento assistido por IA

Registro contínuo conforme [`AGENTS.md`](../AGENTS.md). Entradas em ordem cronológica.
Nenhuma entrada contém segredos. Resultados de testes são copiados de execuções reais.

---

### 2026-10-20 — Etapa 0: briefing e análise dos anexos
- **Ferramenta:** Claude Code (app desktop), modelo Claude Opus 5.5.
- **Prompt (resumo):** enunciado completo do "Desafio técnico — agente de IA orquestrador
  multidomínio" + zip `drive-download-20261020T183127Z-1-001.zip` com as bases.
  O usuário também ativou a skill `/senior-backend` durante a sessão.
- **Resposta/proposta da IA (resumo):** extrair o zip, inventariar os arquivos e
  classificá-los por domínio antes de escrever código.
- **Achados da análise:**
  - Empresa fictícia "Aurora Serviços Digitais S.A."; todos os dados são sintéticos (`manifesto_pacote.json`).
  - `financeiro/`: `lancamentos_financeiros.csv` (300 linhas), `orcamento_mensal.csv` (144), `indicadores_e_regras.json`.
  - `informacoes_internas/`: 10 Markdown de políticas + `faq_interno.json` (7 itens).
  - `ouvidoria/`: `manifestacoes.csv` (180 protocolos OUV-NNNN) + `referencia_ouvidoria.json`.
  - `documentacao/` e `sql/`: orientações de ingestão e esquema PostgreSQL de referência (metadocumentação, não conhecimento da organização).
  - `testes/perguntas_avaliacao.json`: 12 casos de avaliação com domínio e fonte esperados.
  - Não há PDF nem DOCX nos anexos.
- **Decisão:** a pasta de cada arquivo define o domínio (classificação automática).
  `documentacao/` e `sql/` foram movidos para `docs/fonte_pacote/` e **não** são indexados;
  o caso de avaliação foi para `backend/tests/eval/`.
- **Limitação de ambiente:** Docker não está instalado na máquina de desenvolvimento
  (`docker: command not found`). Os arquivos Docker serão escritos, mas a validação
  local será feita com Python 3.12.10 + uvicorn. Isso será reafirmado no fim.

### 2026-10-20 — Etapa 1: AGENTS.md e diário
- Criados `AGENTS.md`, este diário e `docs/ai_conversations/`.
- **Decisão:** aceita (exigência do enunciado).
