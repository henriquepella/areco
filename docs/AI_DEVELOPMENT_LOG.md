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

### 2026-10-20 — Etapa 5: orquestrador (branch `feat/orchestrator`)
- **Decisão:** classificação **híbrida**. Regras com pesos por domínio primeiro (rápidas,
  explicáveis, testáveis sem API); o modelo só classifica quando as regras são inconclusivas
  **e** há provedor configurado, com saída restrita às categorias válidas. Sem modelo, pergunta
  ambígua → pedido de esclarecimento; sem nenhum termo de domínio → fora de escopo.
- **Métrica de confiança (regras):** `pontos_vencedor / soma_pontos × min(1, pontos_vencedor / 4)`,
  exigindo confiança ≥ 0,4 e margem ≥ 1,5 ponto sobre o 2º domínio. Classificação pelo
  modelo devolve `confidence = null` (sem estimativa calibrada, conforme o enunciado).
- **Problemas e correções:**
  - T003 ("aprovações para uma compra de R$ 12.000") caía em `indefinido`: "compra" e
    "aprovação" estavam na mesma regra e somavam um único peso. Separadas em duas regras.
  - Teste de ambiguidade ("gasto e política") era classificado como financeiro (3 × 2 pontos,
    confiança 0,45). Adicionada a exigência de margem mínima entre o 1º e o 2º domínio.
- Log estruturado por requisição sem o texto da pergunta (só tamanho, categoria, agente,
  fontes, latência), para não registrar dados pessoais.
- **Testes executados:** `cd backend && python -m pytest` → `70 passed in 1.42s`.

### 2026-10-20 — Etapa 6: API FastAPI (branch `feat/api`)
- Endpoints: `GET /health`, `POST /api/chat`, `POST /api/classify` (classificação isolada da
  geração, exigência do item 4), `GET /api/agents`, `GET /api/knowledge/status`.
- Erros: 422 com mensagem amigável (sem eco do payload), 500 genérico sem stack/caminhos;
  limite configurável de 1000 caracteres (`MAX_QUESTION_CHARS`). Cabeçalhos de segurança
  (CSP `self`, nosniff, frame-ancestors none). Frontend servido pelo mesmo app → sem CORS.
- Logs JSON por linha; logger do `httpx` em WARNING para não registrar URLs do provedor.
- **Testes executados:** `cd backend && python -m pytest` → `87 passed in 1.74s`.
- **Verificação manual:** `uvicorn app.main:app --port 8765` + `curl /health` e `curl POST /api/chat`
  (OUV-0002) → 200, resposta correta; logs `knowledge_loaded` (3/11/2 documentos) e `chat_handled`
  sem o texto da pergunta.

### 2026-10-20 — Etapa 7: interface web (branch `feat/frontend`)
- **Prompt (resumo):** usuário ativou a skill `/senior-frontend`; ela é voltada a React/Next,
  então só as práticas gerais foram aproveitadas (o enunciado proíbe frameworks).
- **Decisões:** HTML/CSS/JS puro, servido pelo próprio FastAPI (mesma origem). Sem fontes ou
  scripts externos e sem estilos inline, para respeitar a CSP `self`. Texto das respostas
  renderizado por DOM (`textContent`), com suporte só a negrito e listas: nada de `innerHTML`
  com conteúdo vindo da API. Ícones SVG embutidos; cor por domínio (verde/azul/âmbar).
- **Verificação no navegador (painel do Claude Code, uvicorn na porta 8000):**
  - Sugestão financeira → R$ 305.937,01, badge "Financeiro", modo "Calculado sobre os dados", fontes expansíveis.
  - OUV-9999 digitado + Enter → "Sem evidência suficiente", sem dados inventados.
  - Console sem erros (nenhuma violação de CSP).
  - Servidor parado → mensagem "Não foi possível conectar ao serviço" (após ~5 s no Windows).
  - Falha de rede simulada via `fetch` → botão "Tentar novamente" refaz a pergunta sem duplicar a mensagem.
  - 3 cliques rápidos em sugestões → 1 única chamada a `/api/chat` (envio duplicado bloqueado).
  - "Nova conversa" limpa o histórico e volta ao estado inicial.
- **Problema e correção:** em 375 px a página tinha 422 px de largura (rolagem horizontal):
  a lista rolável de domínios esticava a coluna do grid. Corrigido com `min-width: 0` na
  barra lateral; título com `nowrap` no celular. Medido depois: 375 = 375.

### 2026-10-20 — Etapa 8: Docker e configuração (branch `feat/docker`)
- Arquivos: `backend/Dockerfile` (python:3.12-slim, usuário sem privilégios, HEALTHCHECK em
  `/health`), `docker-compose.yml` (porta `APP_PORT`, `.env` opcional, volume somente leitura
  para `knowledge_base`), `.dockerignore`, `.env.example` (`LLM_PROVIDER=none` por padrão).
- **Decisão:** imagem única servindo API + frontend (mesma origem, sem CORS). Índice BM25
  não é persistido (reconstruído no start); o volume persiste/atualiza os documentos.
- **Limitação:** Docker **não está instalado** nesta máquina, então `docker compose up --build`
  **não foi executado**. O que foi verificado no lugar:
  - `docker-compose.yml` parseado com PyYAML (estrutura válida).
  - App iniciado com as mesmas variáveis da imagem (`KNOWLEDGE_DIR`, `FRONTEND_DIR`, cwd
    `backend/`): `/health` → `ok`, `/` e `/js/app.js` → 200.
  - Com `LLM_PROVIDER=anthropic` sem chave: `/health` informa `LLM_API_KEY não configurada`.

### 2026-10-20 — Etapa 9: avaliação e ajustes (branch `feat/evaluation`)
- `tests/eval/run_eval.py` mede separadamente **roteamento**, **recuperação** (documento
  esperado nas fontes), **fidelidade** (termos obrigatórios na resposta, proxy por palavras-chave)
  e **abstenção** (status correto quando não se deve responder).
- Casos: 12 do pacote (`perguntas_avaliacao.json`, intocado) + `casos_adicionais.json`,
  marcado como **elaborado pelo desenvolvedor**: traduz os critérios oficiais em verificações e
  acrescenta 20 perguntas (períodos sem dados, perguntas sem resposta, injeção na pergunta etc.).
- **Problemas encontrados pela avaliação e correções:**
  1. Falso positivo de recuperação: "horário do refeitório" retornava o documento de incidentes
     (cobertura simples de termos = 0,33 ≥ 0,3). A cobertura passou a ser **ponderada por IDF**
     (termo ausente do índice recebe IDF máximo) e verbos genéricos ("fazer", "houver"...) viraram
     stopwords. Medido antes da troca: casos irrelevantes 0,08–0,26; relevantes ≥ 0,33.
  2. A009 ("prazo... no fechamento financeiro") ficou dividida entre interno e financeiro
     (margem 0,5). Peso de "fechamento/conciliação" subiu de 2 para 3. **Ressalva:** ajuste feito
     olhando o próprio caso, então há risco de sobreajuste; o conjunto de avaliação é pequeno.
  3. OUV-9999 passou a citar `manifestacoes.csv` (a base consultada, com "nenhum registro")
     além da regra de não inventar protocolos.
  4. Adicionar `tests/__init__.py` quebrou `from conftest import ...`; trocado por `tests.conftest`.
- **Testes executados:**
  - `python -m tests.eval.run_eval --save` (sem modelo) → 1ª execução: roteamento 31/32,
    recuperação 22/23, fidelidade 26/27, abstenção 10/10. Depois das correções: **32/32, 23/23,
    27/27, 10/10**.
  - `python -m pytest` → `88 passed in 1.70s` (inclui `test_evaluation.py`, limiar ≥ 0,9 e 100% de
    roteamento nos 12 casos oficiais).
  - **Não avaliado:** o modo com modelo de linguagem (sem chave de API nesta sessão). As barreiras
    pós-geração foram testadas só com LLM simulado.

### 2026-10-20 — Etapa 10: documentação e revisão final (branch `docs/readme`)
- `README.md` completo em português (arquitetura com diagrama Mermaid, execução com e sem Docker,
  ajustes para Windows, provedor de IA, endpoints, interpretação das fontes, testes, limitações).
- **Correção:** o exemplo de resposta do README foi conferido contra a API real; a primeira versão
  trazia pontuação de roteamento errada (12,5 em vez de 8,5) e omitia o termo "quanto". Corrigido.
- `docs/ai_conversations/`: índice + registro **manual** da sessão (claramente marcado como não
  sendo exportação original). A exportação nativa do Claude Code gera um zip local com contexto da
  máquina; ela não foi versionada automaticamente para não publicar dados pessoais (ver o README da pasta).
- **Revisão de segurança:** `git grep` por padrões de chave, e-mail e caminhos locais nos arquivos
  versionados → só placeholders (`"..."` no README, `fixture-not-a-key` na fixture). `.env`,
  `.venv`, `.claude/` e `resultados_*.json` fora do Git.
- **Testes executados:** `cd backend && python -m pytest` → `88 passed in 1.75s`.

## Limitações conhecidas e melhorias futuras (consolidado)
- `docker compose up --build` não executado (Docker ausente na máquina de desenvolvimento).
- Modo com modelo de linguagem não exercitado contra um provedor real (sem chave nesta sessão);
  coberto apenas com LLM simulado.
- Sem memória de conversa, autenticação ou rate limiting; registro de manifestações depende de
  integração futura; PDF/DOCX não suportados (sem anexos nesses formatos).
- Avaliação pequena e parcialmente usada para ajuste (risco de sobreajuste); fidelidade medida por
  palavras-chave.
