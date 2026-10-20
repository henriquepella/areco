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

### 2026-10-20 — Etapa 4: provedor de IA e agentes especializados (branch `feat/agents`)
- **Ferramenta:** Claude Code; consultada a skill `claude-api` para IDs de modelo e uso do SDK.
  Modelo padrão para `LLM_PROVIDER=anthropic`: `claude-opus-5-5` (configurável por `LLM_MODEL`).
- **Decisões:**
  - **Financeiro determinístico:** somas, contagens, orçamento, execução e inadimplência são
    calculadas com `Decimal` sobre os CSVs, seguindo `indicadores_e_regras.json`
    (competência, não vencimento; "pagas" = status Pago; sem status = despesas incorridas).
    O modelo **não** participa desses cálculos; só perguntas conceituais usam RAG + modelo.
    Sugestão de "deixar o LLM gerar SQL" **rejeitada** (o próprio pacote desaconselha SQL arbitrário).
  - **Barreiras pós-geração:** a resposta do modelo é descartada (volta ao modo extrativo)
    se citar arquivo não fornecido ou trouxer valor monetário ausente das fontes; o token
    `NAO_ENCONTRADO` vira resposta de "sem evidência". As fontes exibidas vêm sempre da
    recuperação, nunca do texto do modelo.
  - **Prompt injection:** trechos vão entre `<fontes>` (delimitadores removidos do conteúdo) e o
    prompt de sistema manda ignorar instruções contidas neles.
  - **Ouvidoria:** protocolo por correspondência exata (normaliza `ouv 2` → `OUV-0002`); listas
    oficiais lidas de `referencia_ouvidoria.json`; pedido de registro → explica que não há
    integração transacional e que nenhum protocolo foi gerado; pedidos de identidade → recusa.
  - **Guarda de segurança (T010):** pedidos de senha/credencial/dados de terceiros são recusados
    pelo agente de informações internas, citando `04_seguranca_da_informacao.md` e `09_politica_privacidade.md`.
  - Abreviações de mês ("dez", "mar") removidas do parser após perceber colisão com palavras comuns.
- **Valores de referência conferidos à parte (script Python sobre os CSVs):** T006 = R$ 305.937,01
  (27 lançamentos); T007 = Tecnologia, R$ 43.229,05; T011 = R$ 275.727,70 (6 lançamentos).
- **Testes executados:** `cd backend && python -m pytest` → `43 passed in 1.49s`
  (inclui LLM simulado: resposta fiel, citação inventada, valor inventado, falha de rede).
