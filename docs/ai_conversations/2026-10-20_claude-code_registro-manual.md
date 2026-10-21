# Sessão Claude Code — 2026-10-20 (REGISTRO MANUAL)

> **Tipo: registro manual.** Este arquivo **não** é uma exportação original. É um resumo fiel,
> escrito pela própria IA durante a sessão, das mensagens do usuário e do que foi feito em
> resposta. Detalhes técnicos, problemas e resultados de testes ficam em
> [`../AI_DEVELOPMENT_LOG.md`](../AI_DEVELOPMENT_LOG.md).

- **Ferramenta:** Claude Code, aplicativo desktop (Windows 11), modelo Claude Opus 5.5.
- **Modo:** agente com acesso ao terminal, ao sistema de arquivos e a um navegador embutido.
- **Duração:** uma sessão contínua, em 2026-10-20.

## Mensagens do usuário (em ordem)

1. **Enunciado do desafio** (texto integral "DESAFIO TÉCNICO — AGENTE DE IA ORQUESTRADOR
   MULTIDOMÍNIO", seções 1 a 13) + anexo `drive-download-20261020T183127Z-1-001.zip` + a frase
   "Os arquivos estao no zip".
2. `/senior-backend`: ativação de uma skill genérica de boas práticas de backend.
3. `/senior-frontend`: ativação de uma skill de frontend (voltada a React; só as práticas gerais
   foram aproveitadas, porque o enunciado proíbe frameworks).
4. `/git-commit-helper` com o pedido literal: *"para cada parte criada suba um commit em brenchs
   diferentes e va dando merge sem erro"*.
5. URL do repositório: `https://github.com/henriquepella/areco`.
6. "rode o projeto".
7. Captura de tela da interface + pedido: *"Preciso do projeto com uma identidade visual melhor e
   mais bonita, as os textos precisam estar melhores posicionados e quando for feito uma pergunta
   colocar uma leve animacao como se o robo estivesse digitando, como se fosse uma conversa com
   alguem mesmo"*.
8. Texto do e-mail original do desafio + pergunta: *"Tudo que esta aqui foi incluido?"*
9. *"Sim, complete tudo: logs de interação do agente (arquivo + exemplo real), exportação da
   conversa anonimizada no repositório e a seção de status da entrega no README."*
10. `/clean-code` com pedido de reorganização do código **sem alterar o comportamento**.
11. Especificação "Implementação do menu inteligente de perguntas — Aurora IA" (interrompida e
    retomada com *"Continue de onde parou"* e *"Continue"*).
12. *"Sim, exporte a conversa de novo, anonimize com o mesmo script e publique no repositório."*

## O que a IA fez em resposta (resumo)

| # | Etapa | Resultado | Branch |
|---|---|---|---|
| 1 | Extraiu e inventariou o zip; classificou os arquivos por domínio; conferiu valores distintos dos CSVs. | Domínio definido pela pasta; `documentacao/` e `sql/` não indexados. | — |
| 2 | Criou `AGENTS.md` e o diário antes de qualquer código. | Exigência da seção 10. | `docs/agents-and-log` |
| 3 | Iniciou o git; integrou o README inicial do remoto (`--allow-unrelated-histories`); normalizou fins de linha. | Sem conflitos. | `chore/gitattributes` |
| 4 | Ingestão + BM25 por domínio. | 13 testes. | `feat/rag-retrieval` |
| 5 | Camada de provedor de IA + três agentes. Consultou a skill `claude-api` para IDs de modelo e uso do SDK. | Financeiro determinístico; barreiras contra alucinação. | `feat/agents` |
| 6 | Orquestrador híbrido com confiança explicável. | Correções no T003 e na margem de ambiguidade. | `feat/orchestrator` |
| 7 | API FastAPI; servidor iniciado e testado com `curl`. | 87 testes. | `feat/api` |
| 8 | Frontend; testado no navegador embutido (desktop e 375 px, erro de rede, nova tentativa, envio duplicado). | Corrigida rolagem horizontal no celular. | `feat/frontend` |
| 9 | Docker, compose, `.env.example`. **Docker não instalado:** não executado. | YAML validado; app iniciado com as variáveis da imagem. | `feat/docker` |
| 10 | Avaliação separada por métrica; ajustes guiados por ela. | 32/32, 23/23, 27/27, 10/10 (sem modelo); 88 testes. | `feat/evaluation` |
| 11 | README, registros de conversa, revisão final. | — | `docs/readme` |
| 12 | Subiu o app com uvicorn (Docker ausente) e abriu no navegador. | Funcionando em `localhost:8000`. | — |
| 13 | Nova identidade visual, texto das respostas reorganizado, animação de digitação em 3 fases. Skills `frontend-design` e `animate`. | Corrigidos: digitação desligada por "reduzir movimento" e rAF parado com painel oculto. | `feat/ui-refresh` |
| 14 | Conferiu cada item do e-mail no repositório público. | Faltavam logs de interação e a exportação da conversa; Docker sem execução. | — |
| 15 | Registro de interações em JSON Lines + exemplo real com 38 perguntas via HTTP. | Corrigido: mascaramento apagava canais oficiais nas respostas. | `feat/interaction-logs` |
| 16 | Workflow de CI que segue o README com Docker. | Resultado não conferido: o repositório passou a responder 404 sem login. | `ci/docker-validation` |
| 17 | Seção "Status da entrega"; anonimização e publicação da exportação desta sessão. | — | `docs/delivery-status`, `docs/conversation-export` |
| 18 | Limpeza de código comprovada por retrato byte a byte das saídas antes/depois e teste de mutação. | 102 testes; retrato idêntico. | `refactor/clean-code` |
| 19 | Menu inteligente: `/api/assist`, `/api/suggestions`, catálogo de intenções gerado das bases, interface com agentes clicáveis. | 128 testes; 31 sugestões executadas até a resposta. | `feat/menu-inteligente` |
| 20 | Nova exportação da sessão, anonimizada com o mesmo script e verificada à parte. | 1.389 entradas; nenhum termo bloqueado. | `docs/conversation-export-v2` |

## Sugestões consideradas e rejeitadas pela IA

- **Banco vetorial / embeddings:** rejeitado por desproporção (cerca de 650 trechos curtos).
- **Modelo gerando SQL para o financeiro:** rejeitado; o próprio pacote desaconselha SQL
  arbitrário, e os valores precisam sair exatamente das fontes.
- **Abreviações de mês no parser** ("dez", "mar"): removidas após identificar colisão com
  palavras comuns ("dez maiores despesas").
- **Endpoint para recarregar a base:** não implementado; reiniciar o container basta e evita
  uma rota extra sem autenticação.
