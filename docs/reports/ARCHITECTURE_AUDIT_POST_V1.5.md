# Auditoria Arquitetural — Steve Desktop Assistant (pós-V1.5)

## 1. Escopo e metodologia

Leitura direta e completa de todo o código-fonte não-teste do projeto (90 arquivos
`.py` fora de `tests/`): `core/`, `ai/`, `memory/`, `security/`, `settings/`,
`system_monitor/`, `tools/`, `ui/desktop/` (incluindo `dashboard/` e `orb/`), `voice/`,
`main.py`. Grande parte já havia sido lida linha a linha durante as etapas PATCH 03,
PATCH 04 e V1.5 desta mesma sessão; o restante (`ai/`, `core/orchestrator.py`,
`core/context.py`, `core/session.py`, `core/errors.py`, `memory/service.py`, `voice/*`,
`settings/autostart.py`, `settings/single_instance.py`, e o restante de
`ui/desktop/`) foi lido agora, por completo, para esta auditoria.

Cada achado abaixo foi confirmado por leitura direta do código e, quando aplicável, por
`grep` de todo o repositório para verificar uso real (não presumido) — mesma disciplina
das etapas anteriores: nenhum achado é especulativo.

Esta é uma auditoria, não uma implementação — nenhum código foi alterado. Achados estão
ordenados por severidade/impacto real.

## 2. Visão geral da arquitetura

```
ui/cli.py, ui/desktop/  →  core/orchestrator.py  →  ai/router.py → ai/providers/*
                                    ↓
                          core/context.py, core/session.py, core/tool_manager.py
                                    ↓
                          tools/* (LOW-only hoje) → security/permissions.py + confirmations.py
                                    ↓
                          memory/service.py → memory/database.py (SQLite)

core/events.py (EventBus único) conecta: core/lifecycle.py, core/orchestrator.py,
voice/service.py, system_monitor/service.py, security/engine.py, security/scanner.py
→ consumido por ui/desktop/dashboard/window.py (Dashboard) e ui/desktop/window.py
  (indicadores de status).
```

A camada é limpa e consistentemente respeitada: `core/` nunca importa `ui/`;
`tools/`/`security/`/`system_monitor/` nunca importam `tkinter`/`customtkinter`;
`ai/base.py` isola o Core de qualquer detalhe do Ollama. Nenhuma violação de
dependência circular foi encontrada.

## 3. Padrões consistentes confirmados (o que está genuinamente sólido)

- **Um único `EventBus`, uma única fonte de verdade de lifecycle** — nunca duplicado,
  em nenhuma das 5 versões que o usaram (Lifecycle, Orchestrator, Voice, System
  Monitor, Security).
- **Um único modelo de threading/lifecycle** para os dois serviços de longa duração
  (`SystemMonitorService`, `SecurityEngine`): `_lifecycle_lock`, thread daemon nomeada,
  `_stop_event`, `thread.join()` com timeout e log se excedido. O Security Center
  (V1.5) replicou esse modelo deliberadamente em vez de inventar um novo.
- **`BackgroundRunner` é o único ponto de marshaling para a thread do Tk** — toda
  callback de worker thread passa por ele; nenhum widget é tocado fora da thread
  principal em nenhum lugar do código lido.
- **Tool-calling nativo, nunca parsing de texto** — `AIResponse.tool_calls` é
  estruturado desde `ai/base.py`; nenhum lugar do código faz parsing de string para
  decidir qual ferramenta rodar.
- **Nenhum dado sai da máquina** fora do necessário (Ollama local, download de modelo
  Whisper na primeira execução) — confirmado em voz (V1.1), Security Center (V1.5, com
  varredura estrutural de código como teste automatizado) e no restante do sistema.
- **`AuditLogger._redact()`** é aplicado uniformemente onde há logging de auditoria
  (Orchestrator desde V1, AIRouter desde V1.2.1, Security Engine desde V1.5).
- **505 testes automatizados**, cobrindo cada módulo não-trivial (incluindo os 9
  arquivos de teste dedicados ao Security Center e os 4 de voz/orb).

## 4. Achados

### 4.1 `settings.permission_auto_approve_level` é uma configuração morta (real, confirmado)

`Settings.permission_auto_approve_level` (`settings/config.py:77`) é um campo
totalmente modelado — tipo próprio (`PermissionAutoApprove`), validado, persistido em
`config.json`, com round-trip em `to_dict()`/`from_dict()`. Mas `main.py:209` constrói
o `PermissionManager` com um valor **hardcoded**:

```python
permission_manager = PermissionManager(auto_approve_up_to=PermissionLevel.LOW)
```

O valor salvo pelo usuário nunca é lido. Nenhum lugar do código faz a conversão
`PermissionAutoApprove` → `PermissionLevel` (são enums diferentes hoje). Como todas as
15 ferramentas existentes são `PermissionLevel.LOW` (confirmado via grep — nenhuma
MEDIUM/HIGH existe ainda), isso não tem efeito observável **hoje**, mas é uma
configuração que o usuário poderia (em tese) mudar sem que nada aconteça — e não há
sequer um controle para ela em `SettingsDialog` (confirmado: os campos do diálogo não
incluem `permission_auto_approve_level`). Provavelmente um remanescente do desenho
original da V1, nunca conectado.

**Impacto**: nenhum agora; torna-se relevante no momento em que a primeira ferramenta
MEDIUM/HIGH for implementada.

### 4.2 GUI não tem diálogo de confirmação real (real, confirmado)

`ui/desktop/app.py` usa `AutoDenyConfirmationService` (`security/confirmations.py`) —
que **sempre nega** qualquer ação que exija confirmação, sem mostrar nada ao usuário.
Não existe, em nenhum lugar de `ui/desktop/`, uma classe de diálogo modal para
aprovar/negar uma ação (equivalente ao prompt de `CLIConfirmationService` no terminal).

Combinado com o achado 4.1: hoje isso é inofensivo porque nada pede confirmação. Mas é
uma peça arquitetural que falta, não apenas "não implementada ainda" — se uma
ferramenta MEDIUM/HIGH for adicionada (ex.: mover arquivo, uma futura ação do Security
Center além de scan/revisão), ela seria **silenciosamente negada toda vez** no modo
GUI, sem qualquer explicação visível ao usuário sobre por que "Steve se recusa" a
fazer algo. O CLI já tem o mecanismo certo (`CLIConfirmationService`, um prompt de
terminal) — só a GUI carece do equivalente visual.

**Impacto**: nenhum agora; bloqueador silencioso e confuso no dia em que uma
ferramenta MEDIUM/HIGH for adicionada, se não for corrigido antes.

### 4.3 `core/errors.py` é código morto (real, confirmado)

`SteveError`, `AIProviderUnavailableError`, `ToolExecutionError` são definidos mas
**nunca importados em lugar nenhum** do projeto (confirmado via grep em todo o
repositório, incluindo testes). O padrão real usado em produção é outro:
`ConnectionError` (capturado explicitamente pelo Orchestrator) e `ToolResult(success=
False, error=...)` — nunca uma exceção customizada dessas três. Provavelmente
definidas no desenho original da V1 e substituídas por um padrão diferente conforme o
projeto evoluiu, sem que o arquivo fosse removido.

**Impacto**: nenhum funcional — é só uma abstração não utilizada ocupando espaço.

### 4.4 `conversation_history` é persistida mas nunca lida de volta (real, confirmado)

Toda mensagem de usuário/assistente é gravada em `conversation_history` via
`MemoryService.add_history()` (chamado a cada turno por
`Orchestrator._remember_turn()`). Mas `MemoryService.get_recent_history()` — o único
método que lê essa tabela — **só é chamado em testes** (confirmado via grep:
`test_ollama_integration.py`, `test_memory.py`, `test_orchestrator.py`; nenhuma
chamada em `main.py`, `ContextManager`, `Orchestrator` ou qualquer caminho de
produção). `ContextManager.build()` monta o histórico enviado à IA a partir de
`session.recent_turns()` — a lista **em memória** de `SessionManager`, que começa
vazia a cada novo processo.

Na prática: **a conversa não sobrevive a um restart do Steve**. O dado fica
persistido em SQLite (útil como trilha/auditoria), mas uma nova sessão nunca o
carrega de volta — mesmo reabrindo o Steve minutos depois de fechá-lo, ele não lembra
do que foi dito na conversa anterior (diferente da memória permanente/identidade, que
persiste corretamente via `memories`, um mecanismo separado e funcionando como
projetado). O próprio docstring de `memory/service.py` sugere que essa era a intenção
original ("SESSION context is... SessionManager's and conversation_history's job"),
mas a ligação de volta nunca foi implementada.

**Impacto**: real, mas de severidade moderada — é uma lacuna de continuidade de
conversa, não de memória factual (que funciona corretamente). Pode ser intencional
(sessão = vida do processo, por design) ou uma peça pendente; vale confirmar com
quem definiu a arquitetura original antes de decidir se é bug ou decisão.

### 4.5 `ToolManager.execute()` não protege contra uma ferramenta que lança exceção (real, confirmado)

```python
def execute(self, name: str, params: dict) -> ToolResult:
    tool = self.get(name)
    valid, error = tool.validate(params)
    if not valid:
        return ToolResult(success=False, verified=False, message=error, error=error)
    return tool.execute(params)  # <- sem try/except
```

Cada ferramenta é responsável por capturar seus próprios erros de OS internamente —
e a maioria faz isso bem (`OpenApplicationTool`, `CreateDirectoryTool`, `OpenFileTool`,
`CheckDiskTool` todos têm `try/except OSError` nos pontos certos). Mas isso não é
garantido estruturalmente pelo `ToolManager`: uma ferramenta nova (ou uma já existente,
num caminho não previsto) que deixe uma exceção escapar propaga direto para o
Orchestrator sem nenhuma rede de segurança nesta camada.

**Impacto**: mitigado na prática por: (a) toda ferramenta atual já trata seus erros
conhecidos; (b) a GUI tem uma segunda rede de segurança (`BackgroundRunner.run()` já
captura qualquer exceção do worker — ver 4.6). O risco real está isolado ao CLI.

### 4.6 CLI não tem a mesma proteção que a GUI contra exceção de ferramenta (real, confirmado)

`ui/cli.py::run_chat_loop()` chama `orchestrator.handle_message(user_text)` sem
try/except. Se uma exceção escapar de uma ferramenta (ver 4.5), o processo inteiro do
CLI quebra com um traceback Python cru — o `finally:` em `main.py::run_cli()` ainda
executa o shutdown corretamente, mas o usuário vê uma falha, não uma mensagem
amigável. A GUI está protegida porque `BackgroundRunner.run()` (usado por
`ChatController.send_text()`) já envolve `work()` em try/except e roteia para
`on_error` → uma mensagem genérica na tela, sem derrubar o processo.

**Impacto**: baixo — o CLI é documentado como "diagnóstico/desenvolvimento", não a
interface principal — mas é uma assimetria de robustez real entre os dois modos.

### 4.7 Lacunas cosméticas/de completude da V1.5 (reais, menores)

- `ui/desktop/controller.py::TOOL_STATUS_LABELS` não tem entradas para as 3 novas
  ferramentas do Security Center (`check_security_status`, `list_security_findings`,
  `scan_file`) — caem no rótulo genérico `"🔧 Executando ferramenta..."` em vez de um
  texto específico como as demais 12 ferramentas têm. Sem impacto funcional.
- `ai/prompts/system_prompt.py::TOOL_POLICY`/`MEMORY_POLICY` não ganharam uma seção
  equivalente para as ferramentas de segurança — a IA depende só da `description` de
  cada tool (razoavelmente clara) para saber quando usá-las, sem uma política
  explícita no prompt como existe para memória. Funciona, mas é menos consistente que
  o padrão já estabelecido.
- `SecurityEngine.analysis_interval_seconds` não é configurável via `Settings` (fica
  fixo no default de `main.py`), diferente de `system_monitor_interval_seconds`, que
  já é. Inconsistência menor entre dois serviços com o mesmo formato de configuração.

## 5. Segurança e privacidade — revisão geral

Nenhum problema novo encontrado além do já documentado nos relatórios da V1.4.1/V1.5.
Confirmado nesta auditoria: `AuditLogger` nunca grava senha/token/secret em nenhum
ponto de chamada real (Orchestrator, AIRouter, Security Engine); todas as 15
ferramentas são LOW e somente-leitura ou de efeito local reversível (abrir app/URL/
arquivo, criar pasta, ler CPU/RAM/disco/processos, ler/escrever memória, análise de
segurança); nenhuma ferramenta apaga, mata processo ou executa código baixado.

## 6. Saúde dos testes

505 testes coletados (confirmado via `pytest --collect-only -q`, não assumido).
Cobertura por área, por contagem de arquivos de teste dedicados: Core/Orchestrator (4),
AI (2), Memory (1 + tools), Security Center (9), System Monitor (1, extenso), Voice (4),
Desktop/GUI (7, incluindo Dashboard), Settings (2), Orb (4). Nenhuma área do código de
produção ficou sem teste dedicado, pela varredura desta auditoria.

## 7. Recomendações priorizadas (para avaliação futura — nenhuma ação tomada aqui)

1. **Decidir o destino de `permission_auto_approve_level`** (4.1): ou conectá-lo de
   verdade a `PermissionManager` (com a conversão de enum necessária), ou removê-lo do
   modelo de configuração se a decisão for manter tudo LOW por ora — manter um campo
   persistido e sem efeito é o tipo de coisa que confunde quem lê o código depois.
2. **Construir um diálogo de confirmação real para a GUI** (4.2) antes (não depois) da
   primeira ferramenta MEDIUM/HIGH ser implementada — hoje é seguro adiar, mas vale
   registrar como pré-requisito explícito dessa futura etapa, não descobrir na hora.
3. **Confirmar se a continuidade de conversa entre reinícios é desejada** (4.4) — se
   sim, é uma peça pequena e bem isolada (`ContextManager.build()` passaria a
   pré-carregar `get_recent_history()` quando `session.turns` está vazio); se não, o
   comentário em `memory/service.py` que sugere o contrário deveria ser ajustado para
   não induzir a leitura errada.
4. Remover `core/errors.py` (4.3) se de fato não houver uso planejado, ou passar a
   usá-lo nos pontos onde faz sentido (ex.: `ToolExecutionError` em vez de deixar uma
   exceção genérica escapar em 4.5).
5. Adicionar um `try/except` em `ToolManager.execute()` (4.5) como rede de segurança
   estrutural, e/ou envolver `run_chat_loop()` em `ui/cli.py` (4.6) — baixo custo, fecha
   a assimetria entre CLI e GUI.
6. Itens cosméticos (4.7) — baixo esforço, sem urgência.

## 8. Conclusão

A arquitetura do Steve, após 8 versões incrementais (V1 até V1.5), permanece
coerente: um único Core, um único EventBus, um único modelo de lifecycle para
serviços de longa duração, camadas respeitadas sem violação de dependência
encontrada. Os achados desta auditoria são, em sua maioria, lacunas de conexão
("peça existe, mas não está ligada a nada" — 4.1, 4.3, 4.4) ou proteções estruturais
ausentes com impacto hoje limitado (4.2, 4.5, 4.6), não bugs ativos que afetam o uso
atual do sistema. Nenhum problema de segurança, vazamento de dado ou violação da regra
"nunca apresentar um valor não confiável como verdadeiro" (o princípio consistentemente
aplicado desde a V1.4.1) foi encontrado nesta revisão.
