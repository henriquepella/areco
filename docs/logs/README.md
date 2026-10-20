# Logs das interações com o agente

[`interacoes_exemplo.jsonl`](interacoes_exemplo.jsonl) é um registro **real**, gravado pela própria
API em 2026-10-20: o servidor (uvicorn, `LLM_PROVIDER=none`) foi iniciado com
`INTERACTION_LOG_PATH` apontando para este arquivo e recebeu 38 perguntas por HTTP:

- as 12 perguntas de avaliação do pacote (`backend/tests/eval/perguntas_avaliacao.json`);
- as 20 perguntas extras do desenvolvedor (`backend/tests/eval/casos_adicionais.json`);
- 6 perguntas de demonstração do script, incluindo uma com CPF e e-mail **fictícios** para
  mostrar o mascaramento.

Para reproduzir (com a API rodando localmente ou no Docker):

```bash
cd backend
python -m tests.eval.simular_interacoes --url http://localhost:8000
```

## Formato

Uma interação por linha (JSON Lines):

| Campo | Conteúdo |
|---|---|
| `id`, `ts` | Identificador curto e horário UTC. |
| `question` | Pergunta com dados pessoais mascarados (`[email]`, `[cpf]`, `[telefone]`, `[numero]`, `[segredo]`). |
| `question_chars` | Tamanho original da pergunta. |
| `category`, `agent` | Domínio escolhido pelo orquestrador e agente que respondeu. |
| `routing_method`, `routing_scores`, `confidence` | Como a classificação foi feita (`rules`, `llm`, `guard`), pontos por domínio e confiança. |
| `status`, `mode` | `answered`, `not_found`, `clarification`, `refused`, `out_of_scope` ou `error`; `deterministic`, `extractive`, `llm` ou `none`. |
| `sources` | Documentos e seções consultados. |
| `answer` | Resposta enviada ao usuário (até 1.200 caracteres). |
| `notice` | Aviso excepcional (falha do provedor, resposta do modelo descartada). |
| `latency_ms`, `llm_provider` | Tempo de processamento no backend e provedor configurado. |

Erros internos ficam registrados como `status: "error"` e `error: <tipo da exceção>`, sem
mensagem nem detalhes.

## Onde fica o registro em execução

- **Docker:** `/app/logs/interacoes.jsonl`, no volume nomeado `interaction_logs`, que sobrevive a
  `docker compose down`; `docker compose down -v` apaga o volume. Para copiar para o host:
  `docker compose cp app:/app/logs/interacoes.jsonl ./interacoes.jsonl`
- **Local:** `logs/interacoes.jsonl`, na raiz do projeto (fora do Git).
- `INTERACTION_LOG_PATH` muda o caminho; vazio desliga o registro.

Os logs técnicos do servidor (inicialização, `chat_handled` com latência) continuam saindo em
JSON no console: `docker compose logs app`.
