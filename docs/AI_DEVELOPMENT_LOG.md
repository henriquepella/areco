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

### 2026-10-20 — Etapa 2: arquitetura e decisões técnicas
- **Prompt (resumo):** usuário pediu commits por parte em branches separados com merge na `main`
  (skill `git-commit-helper`) e informou o remoto `github.com/henriquepella/areco`.
- **Decisões:**
  - **Busca:** BM25 implementado em Python puro, um índice por domínio. Alternativas
    consideradas: `rank_bm25` (dependência extra para ~40 linhas) e embeddings/banco vetorial
    (desproporcional para ~670 trechos curtos). **Aceita** a versão simples.
  - **Índice:** reconstruído em memória ao iniciar (milissegundos). Não é persistido em disco;
    a pasta `knowledge_base` é montada como volume no Docker para atualizar documentos.
  - **Formatos:** `.md`, `.txt`, `.csv`, `.json`. PDF/DOCX **não** foram implementados porque
    não há nenhum anexo nesses formatos (o enunciado pede só os "realmente necessários").
  - **Chunking:** Markdown por seção `##` (cabeçalho com versão anexado à 1ª seção); JSON por
    item/chave; CSV uma linha por trecho. Metadados: documento, domínio, seção, versão. `page`
    fica sempre `null` (nenhum anexo é paginado).
  - **Provedor de IA:** SDK oficial `anthropic` para Claude e `httpx` para APIs compatíveis
    com OpenAI (Ollama, OpenRouter etc.), escolhidos por `LLM_PROVIDER`. Sem chave: modo
    extrativo (resposta montada só com trechos/cálculos, sem simular um modelo).
- **Git:** remoto já tinha um commit "Initial commit" com README de 1 linha; integrado com
  `--allow-unrelated-histories` sem conflitos. Adicionado `.gitattributes` (LF) após avisos de CRLF.

### 2026-10-20 — Etapa 3: ingestão e recuperação (branch `feat/rag-retrieval`)
- Arquivos: `core/config.py`, `services/document_service.py`, `services/retrieval_service.py`,
  `tests/test_retrieval.py`.
- Validações: extensão permitida, tamanho máximo, UTF-8, vazio, JSON/CSV malformado,
  links simbólicos que saiam da base. Arquivos fora das pastas de domínio são ignorados.
- Smoke test manual: financeiro 3 docs/451 trechos, internas 11/20, ouvidoria 2/185.
- **Observação:** a pergunta "Quais status podem aparecer na ouvidoria?" recupera mal por BM25
  ("status" aparece em todas as linhas do CSV, IDF baixo). Será resolvida no agente de
  ouvidoria lendo os campos enumerados de `referencia_ouvidoria.json` diretamente.
- **Testes executados:** `cd backend && python -m pytest` → `13 passed in 0.15s`.
