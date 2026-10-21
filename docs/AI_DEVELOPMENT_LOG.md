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

### 2026-10-20 — Etapa 11: execução local e nova identidade visual (branch `feat/ui-refresh`)
- **Prompts (resumo):** "rode o projeto" (Docker continua ausente; app iniciado com uvicorn na
  porta 8000 e aberto no navegador do Claude Code). Depois, com uma captura de tela: pediu
  identidade visual mais bonita, textos melhor posicionados e uma animação "como se o robô
  estivesse digitando, como se fosse uma conversa com alguém".
- **Ferramenta:** Claude Code; skills `frontend-design` (direção visual) e `animate` (critérios de
  movimento: propriedades, curvas, duração, movimento reduzido).
- **Problemas na captura do usuário:** cabeçalho e conteúdo desalinhados; resposta extrativa com
  cabeçalho técnico ("Trechos encontrados... modo extrativo"), nome de arquivo entre colchetes e
  listas numeradas renderizadas como parágrafos; aviso técnico `LLM_PROVIDER=none...` repetido
  em toda resposta; muitos selos soltos acima do balão.
- **Decisões:**
  - Identidade "aurora": arco nascendo sobre a linha do horizonte (marca e avatar da orquestradora).
    Cada agente com avatar e cor próprios (verde-azulado, índigo, ameixa). Barra lateral como lista,
    sem repetir cards; títulos em caixa normal (sem rótulos em maiúsculas).
  - Linha de **encaminhamento** visível ("A Aurora encaminhou ao Agente X, confiança de N%"),
    deixando a orquestração explícita na conversa.
  - Animação em 3 fases: Aurora "analisando" (≥ 650 ms) → agente "digitando" (600 ms) → texto
    revelado em velocidade constante (16 ms/caractere, entre 0,5 e 2,4 s) com cursor; rodapé e
    fontes aparecem depois (opacidade + 4 px, 200 ms, ease-out forte). Clique ou Esc mostram tudo;
    "Nova conversa" interrompe. Entrada de mensagens com `@starting-style` (opacidade + 6 px, 220 ms).
  - Backend: resposta extrativa reorganizada (resposta da FAQ primeiro, depois "**arquivo** (seção)"
    com o trecho; JSON exibido como lista). `notice` deixa de repetir "sem modelo"/"calculado
    nos CSVs": fica só para avisos excepcionais (falha do provedor, resposta descartada).
- **Problemas encontrados e correções durante a verificação:**
  1. Esta máquina informa `prefers-reduced-motion: reduce` (efeitos de animação do Windows
     desligados). A 1ª versão desligava a digitação nesse caso, então o usuário **não veria** o
     efeito pedido. Decisão: a escrita progressiva continua (é revelação de texto, sem movimento na
     tela, e pode ser pulada); com movimento reduzido, só os deslocamentos viram esmaecimento.
  2. Com o painel do navegador oculto, `requestAnimationFrame` não disparava (embora
     `visibilityState` fosse `visible`) e a resposta ficava em branco. A digitação passou a usar
     `setTimeout` com progresso pelo tempo real.
  3. No celular, "R$" quebrava a linha separado do valor; o frontend troca o espaço por espaço
     não separável (o texto da API não muda).
- **Verificação no navegador (medições via DOM, 1440×900 e 375×812):** fases medidas em
  ~0,65 s / ~0,6 s / ~2,5 s; clique e Esc completam o texto na hora; "Nova conversa" no meio da
  digitação limpa tudo e a próxima pergunta funciona; envio bloqueado durante a digitação; erro
  de rede simulado mostra "Tentar novamente"; sem rolagem horizontal em 375 px; console sem erros.
- **Testes executados:** `python -m pytest` → `88 passed in 1.79s`; `python -m tests.eval.run_eval`
  → 32/32, 23/23, 27/27, 10/10.

### 2026-10-20 — Etapa 12: conferência da entrega e registro de interações (branch `feat/interaction-logs`)
- **Prompt (resumo):** o usuário colou o e-mail original do desafio e perguntou se tudo estava
  incluído. A IA conferiu item por item no repositório público (arquivos baixados pelo
  `raw.githubusercontent.com`) e apontou: item 6 (logs das interações com o agente) ausente; item 8
  (exportação da conversa) só fora do repositório; Docker nunca executado; README sem "status da
  entrega" nem "o que faria diferente". O usuário pediu para completar tudo, incluindo publicar a
  conversa anonimizada.
- **Problema na própria conferência:** um teste com `git cat-file -e origin/main:.env.example`
  acusou o arquivo como ausente. Era o Git Bash do Windows convertendo o argumento em
  `origin\main;.env.example`. Confirmado pelo GitHub (HTTP 200) antes de relatar; não era uma falha.
- **Implementação:** `services/interaction_log.py` grava JSON Lines (pergunta mascarada, categoria,
  agente, roteamento, status, modo, fontes, resposta, latência). Erros são registrados só pelo tipo
  da exceção. Falha de escrita gera aviso e não interrompe o chat. No Docker, um volume nomeado
  `interaction_logs` (não bind mount, para o usuário sem root conseguir gravar em host Linux).
  Desligado por padrão em `Settings()` (testes) e ligado em `Settings.from_env()`.
- **Correção:** a 1ª versão mascarava também a resposta e apagava canais oficiais da base
  (`seguranca@aurora.example`). Passou a mascarar só a pergunta, que é o único texto livre do usuário.
- **Exemplo real:** uvicorn na porta 8010 com `INTERACTION_LOG_PATH=docs/logs/interacoes_exemplo.jsonl`
  + `python -m tests.eval.simular_interacoes --url http://localhost:8010` → 38 requisições HTTP, todas
  200, 38 linhas no arquivo; conferido que CPF/e-mail fictícios não aparecem em claro.
- **Testes executados:** `python -m pytest` → `102 passed in 1.94s` (14 novos sobre o registro).

### 2026-10-20 — Etapa 13: CI com Docker, status da entrega e exportação da conversa
- **CI (branch `ci/docker-validation`):** como não há Docker local, foi criado
  `.github/workflows/ci.yml` com dois jobs: (1) `pytest` + avaliação; (2) o passo a passo do README
  com Docker (`cp .env.example .env`, `docker compose up -d --build`, espera do healthcheck,
  `/health`, frontend, `/api/chat` conferindo R$ 275.727,70, 38 perguntas contra o container, volume
  de logs com ≥ 39 linhas, `pytest` dentro do container). Também passou a gravar o `.jsonl` com LF
  no Windows (antes saía CRLF).
- **Problema:** o resultado do CI não pôde ser conferido daqui. Logo após o push, a API pública do
  GitHub passou a responder 404 para o repositório (antes respondia 200), o que indica que ele ficou
  privado; o `gh` não está instalado e as credenciais do Git do usuário não foram usadas para outra
  finalidade. O push foi confirmado com `git ls-remote` (main = `e2505ab`). **O resultado da
  primeira execução do CI está pendente de conferência pelo usuário na aba Actions.**
- **README:** nova seção "Status da entrega" (tabela dos 9 itens, o que funciona, o que ficou
  pendente, dificuldades e alternativas, decisões principais incluindo CSV em memória em vez de
  PostgreSQL, o que faria diferente, próximos passos). A situação do Docker foi escrita de forma
  verdadeira independentemente do resultado do CI.
- **Exportação da conversa:** `scripts/anonimizar_conversa.py` filtra a exportação do Claude Code
  (mantém mensagens, ferramentas e resultados; remove prompt de sistema, listas de ferramentas e
  conectores da conta, lembretes do sistema, raciocínio interno e metadados da máquina; troca
  e-mail e caminhos pessoais). O script falha se sobrar algum termo bloqueado. Teste na exportação
  anterior: 938 → 373 entradas; a 1ª execução barrou um ID de pasta da conta (adicionada a troca
  por `<conta>`) e um falso positivo ("gmail" dentro de um padrão de busca de segredos).
- **Exportação final:** `session-export-1792528276337.zip` (2.008 entradas) → 833 entradas mantidas em
  `docs/ai_conversations/2026-10-20_claude-code_transcricao_anonimizada.jsonl` + versão legível.
  Problemas encontrados pela verificação do próprio script e corrigidos: (1) meus comandos de
  inspeção desta sessão citavam nomes de ferramentas da conta, agora trocados por `[termo removido]`;
  (2) trocar "gmail.com" antes do e-mail deixava o nome do e-mail exposto, então a ordem foi invertida;
  (3) a palavra "vaga" aparecia por acaso dentro de dados base64 de imagem, então a verificação passou a
  ignorar base64 e as imagens não são alteradas; (4) caminhos eram cortados pelo termo bloqueado
  antes de virar `<HOME>`, então a troca de caminhos passou a vir antes. Resultado final: "nenhum termo
  proibido encontrado", JSONL válido (833 linhas) e 19/19 imagens decodificáveis.

### 2026-10-20 — Etapa 14: limpeza de código sem mudança de comportamento (branch `refactor/clean-code`)
- **Prompt (resumo):** skill `/clean-code` com pedido de análise completa e reescrita para legibilidade,
  com a regra principal de **não alterar o comportamento** (rotas, contratos, cálculos, textos, layout).
- **Método de equivalência:** antes de qualquer edição foi gravado um "retrato" do comportamento:
  respostas completas de `/api/chat` e `/api/classify` para 87 perguntas (todos os ramos e status),
  `/health`, `/api/agents`, `/api/knowledge/status`, 5 casos de erro HTTP, cabeçalhos de segurança, 7
  cenários com LLM simulado (inclusive os prompts enviados), registro de interações, funções unitárias e
  avaliação. Também a saída do anonimizador e o HTML final de uma conversa de 8 perguntas no navegador.
- **Mudanças (só estrutura):** `deterministic_answer` e `summarize_ids` na base (antes duplicados nos
  agentes financeiro e ouvidoria); `_build_prompt` e um único `fallback_answer` em `generate`; regex do
  financeiro nomeadas; `sum_decimal`/`format_percent`; `dataclasses.replace` no lugar de
  `Filters(**{**f.__dict__, ...})`; `_find_department`/`_find_category`; `count` da ouvidoria dividido em
  `_filter_manifestations` + resumo; `score_question` avaliando cada regex uma vez; `handle` dividido em
  `_undefined_response`/`_agent_response`; `_optional_path` no config; constantes no lugar de números
  mágicos (320, 12, 0,6); stopword duplicada removida; variável global do anonimizador virou parâmetro;
  docstrings curtas nas funções públicas; tabela "Onde fazer manutenções" no README.
- **Não alterado:** frontend (todas as funções e seletores CSS estão em uso; repetições de uma linha não
  justificavam mexer na estrutura do DOM), `document_service`, `llm_service`, `interaction_log`, schemas.
- **Verificação executada:**
  - `python -m compileall` → ok; `python -m pytest` → `102 passed in 2.14s`.
  - Retrato depois × antes → **idêntico byte a byte** (367.258 bytes). Teste de mutação: trocar
    `RELATIVE_CUTOFF` de 0,40 para 0,41 em memória altera 16 linhas do retrato (a comparação detecta mudanças).
  - Anonimizador: saídas `.jsonl` e `.md` idênticas (`cmp`).
  - Avaliação: 32/32, 23/23, 27/27, 10/10 (inalterada).
  - Navegador: mesma sequência de 8 perguntas (com erro de rede simulado) → HTML final idêntico
    (19.122 caracteres); requisições 200 após o reinício do servidor.

### 2026-10-20 — Etapa 15: menu inteligente de perguntas (branch `feat/menu-inteligente`)
- **Ferramenta:** Claude Code (Claude Opus 5.5).
- **Prompt (resumo):** especificação "Implementação do menu inteligente de perguntas — Aurora IA":
  domínios clicáveis, perguntas sugeridas geradas das bases (não fixas no frontend), pergunta
  livre com identificação de intenção, confirmação quando ambígua ("Quanto a empresa gastou em
  janeiro?"), coleta só dos parâmetros faltantes, troca de domínio preservando o texto, 12 testes
  de aceitação. O pedido foi interrompido uma vez e retomado com "Continue".
- **Decisões:**
  - **Endpoint novo `POST /api/assist`** em vez de mudar `/api/chat`: o contrato antigo, a avaliação e
    os testes continuam valendo, e a camada nova reutiliza orquestrador e agentes (nenhum cálculo
    ou recuperação duplicados). **Aceita.**
  - **Estado no cliente** (`follow_up` com intenção e parâmetros, devolvidos na mensagem seguinte) em
    vez de sessão no servidor: sem armazenamento novo e sem dados de conversa no backend. **Aceita.**
  - **Catálogo de intenções gerado dos dados** (`agents/intents.py`): cada sugestão tem condição de
    disponibilidade (arquivo, coluna e registros), e as opções dos parâmetros saem dos CSV/JSON.
    Uma lista fixa de perguntas no frontend foi **rejeitada** (a especificação proíbe); as 6 perguntas
    que estavam no `index.html` foram removidas.
  - **Detecção de "pergunta conhecida com parâmetro faltando"** por gatilho (regex) e não por
    semelhança BM25: a semelhança sozinha pedia parâmetros desnecessários ("Quantas manifestações
    existem?" casava com "...com determinado status?"). BM25 ficou só para sugerir perguntas
    parecidas quando não há evidência. **Modificada** em relação à ideia inicial.
  - Período parcial: mês sem ano vira `periodo_parcial`; a resposta seguinte com o ano completa
    ("janeiro" + "2026" → "janeiro de 2026"). A frase final é reconstruída em palavras que o
    `parse_period` existente entende.
  - Agentes ganharam o campo interno `missing` (`ano`/`protocolo`) no `AgentAnswer`, para a conversa
    transformar o pedido de esclarecimento do agente num parâmetro pendente. Não aparece em `/api/chat`.
  - Sinônimos: "pagamos/pagou/pagaram", "recebemos/recebido" e verbos genéricos ("tirar", "obter",
    "conseguir", "quero") como stopwords, para entender frases equivalentes.
- **Problemas e correções:**
  1. "Como faço para tirar férias?" não era entendida: "tirar" (fora do índice) baixava a cobertura
     IDF para 0,29 (< 0,3). Verbos genéricos viraram stopwords; avaliação antiga continuou 32/32, 23/23, 27/27, 10/10.
  2. No navegador, toda pergunta mostrava "Não foi possível conectar". O `POST` nem saía: o
     parâmetro novo `body` de `request()` colidia com `let body` no mesmo bloco (zona morta
     temporal → `ReferenceError` capturado pelo `catch` de rede). `node --check` não detecta; o
     diagnóstico foi interceptar o `fetch` na página. Parâmetro renomeado.
  3. "Nenhuma pergunta parecida" deixava a resposta sem alternativas; agora mostra as primeiras
     sugestões do agente.
  4. No celular, a linha "Conversa com o Agente…" quebrava o ícone e o texto em linhas separadas;
     `flex-wrap` removido. O navegador do painel usava o CSS em cache; foi preciso forçar o recarregamento.
  5. Durante a verificação o servidor foi parado pelo usuário (painel fechado); foi reiniciado para concluir os testes.
- **Testes executados:**
  - `python -m pytest` → `128 passed in 3.70s` (25 novos em `tests/test_assistant.py`: sugestões
    vindas das bases, nenhuma pergunta fixa no frontend, **cada uma das 31 sugestões executada até
    `answered` com fontes**, opções dos parâmetros iguais aos dados, período → só o ano, confirmação
    de gasto ambíguo mantendo departamento, protocolo pedido/inválido/inexistente/existente, troca de
    domínio preservando o texto, domínio escolhido respeitado, modo automático, pergunta nova no meio
    da coleta, ano faltando em pergunta livre, guarda de segurança, validação do payload, `/api/chat` inalterado).
  - `python -m tests.eval.run_eval` → 32/32, 23/23, 27/27, 10/10.
  - Navegador (1440×900 e 375×812): escolha de agente pelo cartão e pela barra lateral; sugestão →
    "janeiro" → clique em "2026" → R$ 54.778,07; "Quanto a empresa gastou em janeiro?" → 3 alternativas →
    "2025" → R$ 134.103,47 (11 lançamentos; conferido à parte com Decimal sobre o CSV); protocolo
    inválido, OUV-9999 sem invenção; troca de agente; "Trocar agente" → modo automático; faixa de
    sugestões; sem rolagem horizontal; console sem erros.
  - **Não verificado no navegador:** a mensagem de falha ao carregar `/api/suggestions` (o código
    trata o erro, mas não foi possível simular a falha só dessa rota sem alterar o servidor).

### 2026-10-20 — Etapa 16: exportação da conversa atualizada (branch `docs/conversation-export-v2`)
- **Prompt (resumo):** "Sim, exporte a conversa de novo, anonimize com o mesmo script e publique no repositório."
- **Feito:** nova exportação pelo Claude Code (`session-export-1792540752788.zip`, 7 MB, fica só na
  máquina do autor) → `scripts/anonimizar_conversa.py` com os mesmos parâmetros da versão anterior →
  3.326 entradas exportadas, 1.389 mantidas; tipos novos de metadado (`cost-state`, `mode`, `system`)
  ficaram de fora pelo filtro existente, sem mudar o script.
- **Verificação executada:** saída do script "nenhum termo proibido encontrado"; verificação à parte
  com `grep` (e-mail, nome do e-mail, caminhos da pasta do usuário, pasta de dados da conta, nomes de
  ferramentas da conta, padrões de chave `sk-`/`ghp_`/`AKIA`) → 0 ocorrências nos dois arquivos;
  JSONL válido (1.389 linhas); 24/24 imagens decodificáveis; as 12 mensagens do usuário presentes;
  e-mails restantes são só os fictícios das bases/fixtures, o de atribuição de commit e falsos
  positivos de decorators Python (`@router.get`).

### 2026-10-20 — Etapa 17: correções de uma revisão externa (branch `feat/natural-questions`)
- **Prompt (resumo):** o usuário colou uma revisão de outra ferramenta com 4 prioridades: (1) Docker
  nunca executado, confirmar o CI; (2) modo com IA nunca testado com provedor real; (3) perguntas
  naturais falham e os 100% da avaliação são suspeitos (ajustes feitos no mesmo conjunto), criar um
  conjunto separado; (4) identificador parcial e trecho de caminho do usuário no histórico/exportação.
- **(1) Docker — verificado:** o repositório voltou a responder sem login; a API do GitHub mostra 13
  execuções do CI, todas `success`. Na execução `39518745059` (main `621788e`) todas as etapas do job
  Docker passaram (compose up --build, healthcheck, /health, frontend, /api/chat com R$ 275.727,70,
  38 perguntas contra o container, volume de logs, pytest no container). Link citado no README.
  **Aceita** a sugestão de citar o run como prova.
- **(3) Perguntas naturais — protocolo para não contaminar a medição:** (a) escrito primeiro
  `casos_validacao.json` (15 perguntas naturais, valores esperados calculados direto dos CSVs) e
  medido **antes** de qualquer correção: roteamento 11/15, recuperação 7/14, fidelidade 7/14;
  (b) correções guiadas **só** pelos dois exemplos da revisão; (c) medição final, sem ajustes depois
  dela: 11/15, 9/14, 9/14. Sinônimos das perguntas da validação ("dinheiro de volta", "folga", "cair",
  "trabalhar de casa", "e-mail estranho") **não** foram adicionados, de propósito.
  - Causa 1: perguntas com muitas palavras de conversa derrubam a cobertura ponderada por IDF.
    Correção: **âncora no título** — abaixo do limiar de cobertura, um trecho ainda vale se a
    pergunta citar um termo exclusivo do título do documento (para itens da FAQ, o título do
    documento citado). Termos presentes em vários títulos ("política") não contam: "política de
    viagens internacionais" e "refeitório" continuam sem resposta (testado).
  - Causa 2: "pedir" puxava a FAQ de acesso para o topo. "pedir" virou stopword.
  - Causa 3: "Como pedir reembolso de despesas?" empatava financeiro × interno. Regra nova (peso 3):
    pedir/solicitar/prazo/procedimento + reembolso → política interna.
  - Achado durante a medição: com um único domínio provável, a Aurora perguntava "Qual deles deve
    responder?". Texto corrigido ("Parece uma pergunta para o Agente X. Quer que ele responda?").
- **(2) Modo com IA:** sem chave, Ollama ou Docker nesta máquina, não houve execução com modelo real
  (**pendente**, depende do usuário). Feito: `tests/test_llm_http.py` sobe um servidor HTTP local que
  imita a API de Mensagens da Anthropic e a API compatível com OpenAI; o SDK oficial monta e envia a
  requisição, e a resposta passa pelas barreiras (citação inventada descartada, erro 500 e recusa →
  modo extrativo, classificação pelo modelo quando as regras empatam). **Achado:** o SDK anthropic
  1.x usa `httpx2`, que registrava cada requisição ao provedor no log em INFO; silenciado. Criado o
  workflow manual `llm-eval.yml` (usa o secret `LLM_API_KEY`; não roda em push, para não gerar custo).
- **(4) Privacidade:** a revisão estava certa sobre o trecho `users\henri`. Eram os meus próprios
  comandos citados na conversa (`--banned "users\henri"` e um `echo "C:\Users\henri: ..."`): o
  anonimizador exigia letra de drive e capturava o nome junto com a pontuação (`henri:`). Corrigido
  (qualquer separador, com ou sem drive) e a checagem final do script passou a ignorar separadores.
  **Problema na minha verificação:** a conferência anterior em Python dizia "0 ocorrências" porque o
  escape de barras invertidas passando pelo shell estragou a regex; o `git grep` mostrava 11. As
  verificações passaram a ser escritas em arquivo. Histórico do Git: versões antigas ainda contêm
  esses trechos (commits `db487cb`, `283becd`, `688b8c4`, `6212757`); reescrever o histórico exige
  force-push e fica para decisão do usuário.
- **Testes executados:** `python -m pytest` → `141 passed in 9.65s`; `run_eval` → 32/32, 23/23,
  27/27, 10/10; `run_eval --validacao` → 11/15, 9/14, 9/14, 1/1.
