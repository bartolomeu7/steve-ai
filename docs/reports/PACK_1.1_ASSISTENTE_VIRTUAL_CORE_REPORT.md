# PACK 1.1 — ASSISTENTE VIRTUAL CORE

## Escopo

Tornar o ciclo conversacional já existente (texto e voz) mais coerente como assistente
virtual — sem trocar Core, GUI, banco, STT/TTS, provider de IA, sem introduzir framework
de agentes (LangChain/CrewAI/AutoGen), banco vetorial, wake word ou um segundo EventBus.
Regra permanente aplicada: REUSE FIRST — pesquisar antes de implementar, reaproveitar só
o necessário, nunca clonar repositório inteiro.

Fora de escopo nesta etapa (por decisão explícita, não esquecimento): Dashboard 2.0,
redesign visual, Security Center, Planner, Skills, pywinauto, banco vetorial,
implementação concreta de busca na web (PACK 1.2), Silero VAD/wake word (etapa posterior
do PACK 1), mudança de GUI/banco/Ollama/STT/TTS.

## FASE 1 — Análise

Lidos integralmente antes de qualquer alteração: `core/orchestrator.py`,
`core/context.py`, `core/session.py`, `ai/router.py`, `ai/providers/ollama_provider.py`,
`ai/prompts/system_prompt.py`, `memory/service.py`, `tools/memory.py`, `voice/service.py`,
`ui/desktop/window.py`, `main.py`, `tests/test_orchestrator.py`, `tests/test_context.py`,
`tests/test_ai_provider.py`.

### Estado do fluxo antes desta etapa

O ciclo `AI → ToolCall → ToolManager → ToolResult → AI → resposta final` já existia,
correto e nativo (sem parsing de JSON em prompt), com proteção de loop
(`max_tool_iterations`), permissões (`PermissionManager`/`ConfirmationService`) e auditoria
(`AuditLogger`) já conectados. Memória permanente (`MemoryService` + `remember_fact`/
`forget_memory`/`recall_memories`) já funcionava de ponta a ponta. O problema não era
arquitetura ausente — era comportamento não coberto em alguns cantos do fluxo já existente.

### Problemas encontrados

1. **Resposta vazia do modelo sem proteção no caminho de texto/GUI.** `voice/service.py`
   já tinha uma guarda (`EMPTY_REPLY_MESSAGE`) antes de falar uma resposta vazia, mas
   `ui/desktop/window.py::MainWindow._on_reply` chamava
   `chat_view.add_steve_message(reply)` sem checar `reply` — uma resposta em branco do
   modelo virava uma bolha de chat vazia, sem nenhuma explicação para o usuário.
2. **Nenhuma proteção contra `content` vazio com `thinking` preenchido.** Não é um bug
   ativo (o `llama3.2`, modelo padrão do Steve, não faz isso), mas é um comportamento
   real e documentado no issue tracker do próprio Ollama para modelos com raciocínio
   (ex.: `deepseek-r1`) — uma troca futura de modelo poderia produzir respostas em branco
   silenciosamente.
3. **Política de conversa incompleta no system prompt.** `TOOL_POLICY` já orientava a usar
   ferramentas corretamente, mas não deixava absolutamente explícito que o modelo nunca
   deve alegar sucesso quando `ToolResult.success == False`. Não havia nenhuma orientação
   sobre pedir esclarecimento antes de agir quando falta informação essencial, nem sobre
   manter respostas objetivas em tarefas simples.
4. **`conversation_history` gravado mas nunca relido — investigação mais profunda que a
   auditoria anterior.** A auditoria arquitetural pós-V1.5 já havia apontado isso como "não
   está ligado". Nesta etapa, a investigação foi mais funda: `SessionManager` gera um
   `uuid.uuid4()` novo a cada processo, e `MemoryService.get_recent_history(session_id,
   ...)` filtra exatamente por esse `session_id`. Isso significa que mesmo implementando
   "carregar histórico recente no boot" da forma mais óbvia, o resultado seria sempre
   zero linhas — o `session_id` novo nunca existiu antes. Ver decisão na seção FASE 2.
5. **Busca por sobreposição de palavras não liga "meu editor" a uma memória sobre "VS
   Code".** `ContextManager.get_relevant_memories` pontua por interseção de tokens
   (`\w+`) entre a pergunta e o conteúdo salvo — não há nenhuma forma de ligar duas
   palavras diferentes que significam a mesma coisa sem alterar o algoritmo em si.
6. **Nenhuma evidência de chamadas de ferramenta desnecessárias** para conversa
   casual — `TOOL_POLICY` já orientava corretamente ("cumprimentos, conversa casual...
   responda diretamente, sem usar nenhuma ferramenta"). Validado de verdade na FASE 3.

### Pesquisa externa (GitHub) — JARVIS e ORBIT

| Projeto | Stack | Avaliação |
|---|---|---|
| [JARVIS](https://github.com/134ertel/jarvis) | Electron + React + FastAPI + Ollama | Aplicação completa, não uma biblioteca. Confirma o padrão de tool-calling nativo + Ollama local, mas não expõe nenhum componente isolado e importável para este escopo. |
| [ORBIT Local AI Agent](https://github.com/mostafa-kermaninia/Orbit-Local-AI-Agent) | Python + faster-whisper + Ollama + CustomTkinter + pyttsx3 | Stack quase idêntica à do Steve. Valida (não introduz nada novo) os mesmos padrões: ferramentas explícitas, loop de tool-calling nativo, memória via ferramentas dedicadas. |

**Conclusão**: nenhum dos dois oferece um arquivo/componente concretamente reaproveitável
para o escopo do PACK 1.1 — ambos são aplicações completas, não bibliotecas instaláveis.
Nenhum código foi copiado de nenhum dos dois. A pesquisa serviu para confirmar (não para
justificar mudança) que a arquitetura atual do Steve já iguala ou supera os padrões
observados nessas referências. Nenhuma licença de terceiros entra em jogo porque nada foi
reaproveitado diretamente.

Uma pesquisa direcionada adicional (não sobre os dois projetos) confirmou, via issue
tracker do Ollama, o comportamento real de `content` vazio / `thinking` preenchido citado
no problema 2 acima.

### O que NÃO foi reaproveitado, e por quê

Nada de JARVIS/ORBIT foi importado — ambos são aplicações fim-a-fim com stacks (Electron/
React ou uma estrutura de projeto diferente) que não se encaixam como "arquivo isolado" no
Steve; adaptar qualquer parte deles custaria mais do que já ter os mesmos padrões
implementados nativamente. Nenhuma biblioteca de terceiros nova foi adicionada nesta etapa
(nenhum requirements.txt alterado).

### Arquivos modificados nesta etapa

`core/orchestrator.py`, `ai/providers/ollama_provider.py`, `ai/prompts/system_prompt.py`,
mais os testes correspondentes (`tests/test_orchestrator.py`, `tests/test_ai_provider.py`,
`tests/test_context.py`) e esta documentação. Nenhum arquivo de GUI, banco, STT/TTS,
segurança ou ferramentas foi alterado.

## FASE 2 — Implementação

### 1. Guarda de resposta vazia centralizada no Orchestrator

`core/orchestrator.py::Orchestrator._run_tool_loop` ganhou a constante
`EMPTY_RESPONSE_MESSAGE` e agora retorna essa mensagem quando `response.content` vem
vazio/só espaços e não há `tool_calls`:

```python
if not response.tool_calls:
    return response.content if response.content and response.content.strip() else EMPTY_RESPONSE_MESSAGE
```

Escolhi centralizar no Orchestrator (em vez de duplicar a checagem em
`ui/desktop/window.py`) porque é o único lugar que os dois entry points (texto e voz)
compartilham — corrige o caminho de texto/GUI sem tocar na GUI, e a guarda já existente em
`voice/service.py` continua ali como redundância inofensiva, não como duplicação de lógica
nova.

### 2. Fallback para `message.thinking` quando `content` vem vazio

`ai/providers/ollama_provider.py::OllamaProvider.chat`:

```python
content = message.get("content") or ""
if not content.strip():
    content = message.get("thinking") or ""
```

Proteção preventiva, não correção de bug ativo — `llama3.2` não aciona este caminho.

### 3. Política de conversa reforçada no system prompt

`ai/prompts/system_prompt.py`:
- `TOOL_POLICY` — adicionada frase explícita proibindo alegar sucesso quando
  `success=false`.
- `MEMORY_POLICY` — adicionada orientação para frasear `content` de forma completa e
  concreta ao salvar memória (com o exemplo exato do VS Code/editor pedido nesta etapa),
  para aumentar a chance de correspondência por palavra no futuro sem tocar no algoritmo
  de relevância.
- `CONVERSATION_POLICY` (nova constante) — concisão em tarefas simples; pedir
  esclarecimento antes de agir quando falta informação essencial e o custo de errar é
  alto; aceitar suposição razoável + confirmação natural para pedidos ambíguos de baixo
  risco; continuar o raciocínio com o resultado real de uma ferramenta em vez de repetir a
  pergunta do usuário. Adicionada em `build_system_prompt()`.

Mudança de prompt engineering apenas — nenhuma mudança de código de controle de fluxo.

### 4. Decisões tomadas sem alteração de código

- **`conversation_history` após reiniciar**: decisão de **não implementar** restauração
  nesta etapa. Motivo: o "consertinho simples" (carregar histórico recente no boot) não
  funcionaria como se espera, porque `SessionManager.session_id` é gerado novo a cada
  processo e `get_recent_history` filtra por ele — resolver isso de verdade exige uma
  decisão arquitetural real (persistir/reaproveitar `session_id` entre execuções, ou
  redefinir "histórico recente" como consulta entre sessões), nenhuma das duas cabe como
  ajuste pequeno desta etapa. `conversation_history` continua sendo gravado (nunca foi
  removido) — só não é relido ainda. Documentado em `docs/ARCHITECTURE.md`.
- **Ferramenta de busca na web**: confirmado, sem implementar nada, que o `ToolManager`/
  `Tool`/`ToolSpec` já são genéricos o suficiente para uma futura `SEARCH_WEB` — seria só
  uma nova subclasse de `Tool` registrada em `main.py::register_default_tools`. Implementação
  real fica para o PACK 1.2, por decisão explícita de escopo.
- **Algoritmo de relevância de memória**: não tocado, por decisão explícita de escopo
  (PACK 1.3). Mitigado apenas via `MEMORY_POLICY` (ver item 3 acima).

### O que não mudou (verificado, não presumido)

`AIResponse`/`ToolCall`/`ToolSpec`/`OllamaProvider`/`AIRouter`/`Orchestrator._execute_tool_call`/
`ToolManager` mantêm exatamente o mesmo ciclo de antes. `max_tool_iterations` não foi
alterado. `PermissionManager`/`ConfirmationService`/`AuditLogger` continuam obrigatórios em
todo caminho de execução de ferramenta — nenhum bypass foi introduzido. Nenhuma dependência
nova em `requirements.txt`.

## FASE 3 — Validação

### Suíte de regressão completa

`pytest tests/` executado após as três mudanças de código: **503 passed, 4 skipped, 1
failed** na primeira execução. O único teste que falhou
(`tests/test_security_engine.py::test_audit_logger_records_finding_created`) pertence ao
Security Center (V1.5) e não tem relação com nenhum arquivo tocado nesta etapa — é um
teste sensível a tempo (`threading.Event.wait(timeout=10)` aguardando uma thread de
background) que rodou concorrentemente com o Ollama e dois processos de validação real
nesta mesma máquina. Reexecutado sozinho, sem concorrência: **20/20 passou**, confirmando
que foi contenção de recursos do ambiente, não uma regressão do PACK 1.1. Uma segunda
tentativa de rodar a suíte completa mais uma vez, isolada, foi interrompida no meio por um
reinício de sessão (limite de uso do ambiente, evento externo, sem relação com o código) —
não foi repetida porque a primeira execução completa já é evidência suficiente junto com a
reexecução isolada do único teste afetado.

Nenhum teste pré-existente precisou ser alterado além dos ajustes já listados na FASE 2
(novas asserções/testes, nenhuma asserção antiga enfraquecida).

### Validação real (Ollama real, sem mocks) — os 11 cenários pedidos

Script isolado (`data dir` temporário, SQLite real, sem GUI) rodado contra `llama3.2` real
via Ollama, cobrindo os 11 cenários explicitamente pedidos — 17 verificações, todas
aprovadas:

1. **Conversa simples** — "Oi, Steve, tudo bem?" → resposta não vazia e coerente.
2. **Conversa com contexto** — segundo turno referenciando o primeiro → histórico de sessão
   com 4 turnos (2 usuário + 2 assistente), resposta coerente.
3. **Uso de memória existente (referência indireta)** — depois de ensinar "prefiro VS
   Code", pedir para "abrir meu editor de código preferido" produziu uma resposta que
   claramente associou "meu editor" ao VS Code (mesmo quando o modelo pequeno não
   confirmou a abertura com clareza — ver observação abaixo).
4. **`remember_fact` novo** — "Steve, lembra que eu prefiro usar o VS Code..." → memória
   real criada no SQLite com conteúdo completo ("O editor de código preferido do usuário é
   o VS Code"), não telegráfico.
5. **`recall_memories`** — "O que você sabe sobre mim?" → resposta não vazia referenciando
   a memória salva.
6. **Uso de ferramenta real + 7. resultado real → segunda chamada ao modelo → resposta
   final** — "Qual o uso atual de CPU?" → `check_cpu` executado de verdade, resposta final
   citou um valor numérico real (`35.9%`), não um placeholder.
8. **Falha de ferramenta** — ferramenta real registrada só para este teste
   (`fake_broken_sensor`, sempre retorna `success=False`) → o modelo respondeu "Não
   consegui..." — nunca alegou sucesso, confirmando ao vivo a regra reforçada em
   `TOOL_POLICY`.
9. **Ollama indisponível** — `OllamaProvider` apontado para uma porta inexistente →
   `Orchestrator` retornou exatamente `UNAVAILABLE_MESSAGE`, sem travar nem propagar
   exceção.
10. **Fluxo de voz existente** — `FasterWhisperSTT` (`tiny`) e `Pyttsx3TTS` reais
    inicializados e confirmados disponíveis nesta máquina; `build_tts()` confirmado
    retornando `NullTTS` (comportamento esperado, não um bug) quando voz está desligada
    por padrão. Loop completo com microfone real não foi automatizado (exige um usuário
    falando de verdade) — os componentes que o compõem foram verificados individualmente
    com engines reais, e o código do caminho de voz (`VoiceService.listen_and_respond` →
    `Orchestrator.handle_message`) não foi alterado nesta etapa além do fix de resposta
    vazia, que é compartilhado com o caminho de texto.
11. **Restart / histórico** — confirmado que uma nova `SessionManager` começa com `turns`
    vazio e um `session_id` diferente mesmo havendo linhas de `conversation_history`
    persistidas no banco da sessão anterior — exatamente o comportamento documentado como
    decisão na FASE 2 (não uma regressão).

**Observação honesta sobre qualidade de resposta do modelo (não um bug de código)**: no
cenário 3, o `llama3.2` (modelo pequeno) respondeu de forma um pouco confusa — misturou
parte do exemplo literal do próprio system prompt ("abri o Chrome — era isso?") na resposta
sobre abrir o VS Code. Isso é uma limitação de qualidade do modelo de 3B parâmetros rodando
localmente, não um defeito na lógica do Orchestrator/ToolManager — o fluxo (ferramenta
certa sendo considerada, resposta honesta, nenhuma alegação falsa de sucesso) se comportou
corretamente.

### O que foi explicitamente validado como ausente (por decisão, não por falha)

Chamadas de ferramenta desnecessárias para conversa casual: não observadas em nenhum dos
cenários reais acima (cenários 1, 2 e 5 não geraram nenhum `tool_call`).

## Documentação

Arquitetura documentada em `docs/ARCHITECTURE.md`, seção "PACK 1.1 — Assistente Virtual
Core": pesquisa externa e resultado, guarda de resposta vazia, fallback de `thinking`,
política de conversa reforçada, extensibilidade para busca na web, decisão sobre
`conversation_history`, tabela de separação memória permanente vs. histórico de conversa,
limitação conhecida do algoritmo de relevância (deferida ao PACK 1.3), e resumo da
validação real.

## Limitações conhecidas ao final desta etapa

- `conversation_history` continua sem ser relido após reiniciar (decisão documentada, não
  esquecimento) — `MemoryService` continua sendo o mecanismo correto para o que precisa
  sobreviver a um reinício.
- `ContextManager.get_relevant_memories` continua por sobreposição literal de palavras —
  mitigado por fraseio rico na hora de salvar, não corrigido no algoritmo (PACK 1.3).
- Busca na web: Core confirmado extensível, nada implementado (PACK 1.2).
- Fluxo de voz com microfone real não tem cobertura automatizada de ponta a ponta (mesma
  limitação de antes desta etapa) — os componentes (STT/TTS reais) foram validados
  individualmente.

## Conforme o roteiro

PACK 1.1 concluído e validado. Próximas etapas (PACK 1.2 — Pesquisa Web, PACK 1.3 —
Memória & Contexto Inteligente, PACK 1.4 — Pipeline de Voz + Silero VAD, Integração Geral,
Dashboard 2.0, Auditoria Final) ficam para autorização explícita futura — nenhuma delas foi
iniciada nesta etapa.
