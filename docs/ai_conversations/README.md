# Conversas com IA

Todo o desenvolvimento foi feito numa única sessão do **Claude Code** (aplicativo desktop,
Windows 11, modelo Claude Opus 5.5) em 2026-10-20. Não foram usadas outras ferramentas de IA.

| Arquivo | Tipo | Conteúdo |
|---|---|---|
| [`2026-10-20_claude-code_transcricao_anonimizada.jsonl`](2026-10-20_claude-code_transcricao_anonimizada.jsonl) | **Exportação original, filtrada e anonimizada** | Transcrição exportada pelo próprio Claude Code (menu da sessão → *Export*), uma entrada por linha: mensagens, chamadas de ferramentas e seus resultados. |
| [`2026-10-20_claude-code_legivel.md`](2026-10-20_claude-code_legivel.md) | Gerado a partir da exportação | A mesma conversa em Markdown: mensagens do usuário, respostas e ferramentas usadas (sem os resultados longos). |
| [`2026-10-20_claude-code_registro-manual.md`](2026-10-20_claude-code_registro-manual.md) | **Registro manual** | Resumo escrito durante a sessão: pedidos do usuário e o que foi feito em cada etapa. |

## Como a exportação foi anonimizada

O `.zip` exportado pelo Claude Code passou por [`scripts/anonimizar_conversa.py`](../../scripts/anonimizar_conversa.py):

```bash
python scripts/anonimizar_conversa.py <session-export.zip> docs/ai_conversations \
  --name 2026-10-20_claude-code --email <email-real> --user <pasta-do-usuario> --banned <termos>
```

**Mantido sem alteração de conteúdo:** mensagens do usuário (inclusive as enviadas durante a
execução), respostas do assistente, chamadas de ferramentas com os parâmetros, resultados das
ferramentas e capturas de tela dos testes no navegador.

**Removido ou substituído:**

| O quê | Por quê |
|---|---|
| E-mail pessoal → `[email removido]` | Dado pessoal. E-mails fictícios das bases (`@aurora.example`) foram mantidos. |
| Caminhos da pasta do usuário → `<HOME>`; pasta de dados da conta → `<conta>` | Dados da máquina pessoal. |
| Requisições internas à API (prompt de sistema completo) | Configuração do ambiente, sem relação com o desafio. |
| Lembretes injetados pelo sistema nas mensagens → `[contexto do sistema removido]` | Listam ferramentas, conectores e preferências da conta pessoal. |
| Anexos de ambiente (listas de ferramentas, skills e conectores, contadores de tokens, hooks) | Mesmo motivo. Os anexos com mensagens do usuário (`queued_command`) foram mantidos. |
| Nomes de ferramentas e conectores da conta pessoal citados em comandos de inspeção → `[termo removido]` | Aparecem só nos comandos usados para auditar a própria exportação; sem relação com o desafio. |
| Texto de raciocínio interno do modelo → `[raciocínio interno omitido]` | Não faz parte da conversa visível; as decisões estão nas respostas e no diário. |
| Metadados (pasta de trabalho, branch, IDs de requisição, histórico de arquivos) | Metadados da máquina; o histórico real do código está no Git. |

O script termina com erro se o e-mail, a pasta do usuário ou outros termos bloqueados
sobrarem na saída. Nenhuma chave de API ou token foi digitado, lido ou exibido na sessão: a chave
do workflow manual de avaliação com modelo foi cadastrada pelo próprio usuário como secret do
GitHub, e os testes usam só valores fictícios (`fixture-not-a-key`, `sk-ant-teste-123`).

**Versão atual:** exportação refeita em 2026-10-20, depois da tentativa de avaliação com modelo
real. Ela cobre todos os pedidos da sessão, do enunciado inicial ao pedido de atualizar esta
exportação (4.851 entradas exportadas, 1.963 mantidas). Nesta versão a lista de bloqueio ganhou
um fragmento de nome de plugin da conta pessoal que aparecia cortado numa saída de comando. Esta versão também corrige um
vazamento das anteriores: caminhos da pasta do usuário escritos dentro de comandos citados na
conversa (com barras duplicadas ou sem letra de drive) não eram substituídos. **Limite:** a exportação é feita antes da mensagem
final do assistente nesse último pedido, que por isso não aparece nela. O `.zip` original, sem anonimização, fica só
na máquina do autor.
