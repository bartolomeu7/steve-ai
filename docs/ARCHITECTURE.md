# Arquitetura — Steve Desktop Assistant

## Fluxo do Orchestrator (`core/orchestrator.py`)

```
usuário digita
   -> SessionManager.add_turn / MemoryService.add_history
   -> ContextManager monta as mensagens (system prompt + memórias relevantes + histórico)
   -> ToolManager.to_tool_specs() lista as ferramentas disponíveis
   -> loop (até max_tool_iterations, padrão 5):
        AIProvider.chat(messages, tools=tool_specs)
        -> sem tool_calls?  devolve response.content e encerra o loop
        -> com tool_calls?  para cada ToolCall:
             - ToolManager.has(nome)?            senão: resultado de erro "ferramenta desconhecida"
             - PermissionManager.requires_confirmation(nível)?
                  -> ConfirmationService.confirm() — se negado: resultado de erro "ação negada"
             - ToolManager.execute(nome, argumentos) roda a Tool de verdade
             - AuditLogger.log_tool_execution(...)
             - o ToolResult (success/verified/message/data/error) real é serializado como
               uma mensagem role="tool" e volta para o próximo giro do loop
   -> se o loop atingir o limite sem uma resposta final: TOOL_LIMIT_MESSAGE
   -> resposta final é salva na sessão/memória e devolvida ao usuário
```

Se o `AIProvider` ficar indisponível (Ollama fora do ar) em qualquer chamada do loop —
a primeira ou uma de follow-up depois de uma ferramenta — o Orchestrator responde com
`core/orchestrator.UNAVAILABLE_MESSAGE` em vez de travar ou inventar algo.

## Tool-calling: por que nativo, e como funciona

**Antes (V1):** o system prompt instruía o modelo a devolver texto no formato
`{"action": "tool_call", ...}` e o Orchestrator tentava fazer parsing desse texto. Um
teste real mostrou o problema: perguntado sobre uso de CPU, o llama3.2 às vezes
respondia direto com um valor **inventado**, sem nunca chegar a pedir a ferramenta.

**Agora:** o Ollama expõe tool-calling nativo em `/api/chat` (o `ollama list` já mostra
`"capabilities": ["completion", "tools"]` para modelos como o llama3.2). Em vez de pedir
por prompt, `OllamaProvider.chat()` manda a lista de ferramentas no campo `tools` da
própria requisição HTTP; se o modelo decidir usar uma, a resposta chega em
`message.tool_calls` — já estruturado (nome + argumentos como dict), sem parsing de
texto. Isso elimina a classe inteira de bugs de "o modelo não seguiu o formato pedido".

A abstração continua a mesma: `AIProvider.chat(messages, tools=...)` retorna um
`AIResponse` com um campo `tool_calls: list[ToolCall]`. Um provedor sem tool-calling
nativo simplesmente devolveria sempre `tool_calls=[]` — o Orchestrator não sabe nem
precisa saber qual provedor está por trás; ele só reage à presença ou ausência de
`ToolCall`s. **O Core nunca importa nada específico do Ollama.**

### Como uma Tool é exposta ao modelo

Toda `Tool` (`tools/base.py`) já tinha `name`, `description` e `permission_level`. Para
tool-calling nativo, cada uma também declara `parameters_schema`: um JSON Schema dos
seus argumentos (ex.: `CheckDiskTool.parameters_schema` descreve um `path` string
opcional). `ToolManager.to_tool_specs()` converte o registro inteiro de `Tool`s em
`ToolSpec`s (`ai/base.py`) — a representação provider-agnostic que `OllamaProvider`
então traduz para o formato `{"type": "function", "function": {...}}` que a API espera.

### Como adicionar uma nova Tool

1. Crie uma classe em `tools/` herdando de `Tool`, com `name`, `description`,
   `permission_level`, `parameters_schema` (schema JSON dos argumentos) e os métodos
   `validate()`/`execute()`.
2. Registre-a em `main.register_default_tools()`.
3. Pronto — nenhuma mudança em `core/orchestrator.py`, `ai/` ou no system prompt é
   necessária. O modelo passa a "ver" a ferramenta na próxima chamada porque
   `to_tool_specs()` lê o registro do `ToolManager` dinamicamente.

### Ciclo tool -> resultado -> resposta

O resultado de uma execução nunca é resumido ou reescrito pelo Orchestrator antes de
voltar ao modelo: `ToolResult.to_dict()` (com `success`, `verified`, `message`, `data`,
`error`) é serializado como JSON e vira o `content` de uma `AIMessage(role="tool", ...)`.
O modelo recebe esse JSON completo — incluindo a `message` legível em português que a
própria Tool já produz (ex. `"Uso de CPU: 23%."`) — e é isso, não uma estimativa, que
ele usa para formular a resposta final. Testado ao vivo (`tests/test_ollama_integration.py`):
em 5/5 execuções o valor real do `psutil` apareceu na resposta final.

### Limite de iterações

`Orchestrator.max_tool_iterations` (padrão 5) limita quantas vezes o loop pode pedir
mais uma ferramenta antes de desistir com `TOOL_LIMIT_MESSAGE` — protege contra um
modelo que fique pedindo ferramentas indefinidamente sem nunca produzir uma resposta
final.

## AI Router — V1.2.1

Antes desta etapa, o `Orchestrator` dependia de `OllamaProvider` diretamente (por trás da
abstração `AIProvider`, mas ainda assim o único objeto de IA que existia). Agora:

```
ANTES:  Orchestrator -> OllamaProvider -> Ollama
DEPOIS: Orchestrator -> AIRouter -> OllamaProvider -> Ollama
```

O `AIRouter` (`ai/router.py`) é a única coisa que o `Orchestrator` conhece — ele próprio
implementa a interface `AIProvider` (`chat()`, `is_available()`, `.model`,
`.capabilities`), então do ponto de vista do `Orchestrator` nada mudou de forma: só o
objeto concreto por trás mudou. `core/orchestrator.py` não tem — e não deve ganhar —
nenhum `if ollama` / `if claude`; toda decisão de qual provider usar mora no Router.

Só o Ollama existe como provider real nesta versão. **Claude ainda não está
implementado** — a arquitetura só prepara o lugar onde um segundo provider entraria, sem
fingir que ele já existe.

### Interface de provider (`ai/base.py`)

`AIProvider` ganhou, além de `chat()`/`is_available()` (que já existiam): `.name`
(identificador, ex. `"ollama"`), `.model` (modelo em uso) e `.capabilities` — todos com
defaults conservadores não-abstratos, então nenhum `AIProvider` já existente (nos testes,
por exemplo) quebrou ao ganhar esses campos. `ProviderCapabilities` é um dataclass com
`chat`, `tool_calling`, `streaming`, `vision`, `reasoning`, `max_context`, `local`,
`online` — cada provider declara só o que sua implementação realmente faz.
`OllamaProvider.capabilities` declara `chat=True, tool_calling=True, local=True` (nativo,
implementado e testado) e `streaming=False, vision=False, reasoning=False` (não
implementados) — nada inventado.

### Como o Router funciona

- **`register(name, provider, capabilities=None)`** — registra um provider sob um nome;
  registrar de novo sob o MESMO nome substitui o provider naquela posição (é assim que
  uma troca de modelo nas Configurações atualiza o Router sem recriar `Orchestrator` nem
  reatribuir nada nele — ver `ui/desktop/app.py::_apply_settings`).
- **`select(requires_tools=, requires_local=, requires_vision=, requires_streaming=,
  requires_reasoning=, requires_online=, provider_name=)`** — decisão pura em memória
  (sem chamada de rede): em modo `AUTO`, tenta o `preferred_provider` primeiro e depois a
  ordem de registro, escolhendo o primeiro que satisfaz os requisitos pedidos; em modo
  `MANUAL`, sempre usa o provider configurado, ignorando capacidades. `Router.select(
  requires_tools=True, requires_local=True)` escolhe o Ollama hoje porque ele satisfaz
  os dois requisitos — o mesmo mecanismo, sem mudar nada aqui, escolheria outro provider
  no futuro se ele fosse mais adequado.
- **`chat(messages, tools=None, **kwargs)`** — a única coisa que o `Orchestrator` chama.
  Seleciona o provider (`requires_tools=bool(tools)`, já que Steve sempre anuncia suas
  ferramentas registradas) e tenta, na ordem de fallback (provider selecionado primeiro,
  depois o resto por ordem de registro); só avança para o próximo se o `chat()` do
  provider atual levantar `ConnectionError`. Não há checagem de `is_available()` antes de
  cada tentativa — isso duplicaria uma chamada de rede por mensagem; o próprio `chat()`
  já é a tentativa real.
- **Fallback** — a infraestrutura existe e é testada (`tests/test_ai_router.py`), mas com
  apenas o Ollama registrado nesta versão a lista de fallback tem um único item: o
  comportamento é idêntico a chamar `OllamaProvider.chat()` diretamente, uma única
  chamada HTTP, nenhuma ferramenta executada duas vezes. Fallback real (tentar um segundo
  provider de verdade) só passa a acontecer quando um segundo provider for registrado.
- **Erros** — `NoProviderAvailableError` e `UnknownProviderError` (ambas em
  `ai/router.py`) herdam de `RouterError`, que herda de `ConnectionError` — de propósito:
  o `Orchestrator` já tinha um `except ConnectionError` ao redor de `chat()`
  (`core/orchestrator.py::_run_tool_loop`) que vira `UNAVAILABLE_MESSAGE`; qualquer falha
  do Router (nenhum provider registrado, nome desconhecido, todos indisponíveis) cai
  nesse mesmo caminho sem precisar de nenhum tratamento novo.
- **Observabilidade** — cada `chat()` bem-sucedido atualiza `Router.last_decision`
  (provider, modelo, motivo, se houve fallback) e, quando um `AuditLogger` é passado no
  construtor, grava um evento `ai_routing_decision`/`ai_routing_error` nele (mesmo
  `AuditLogger.log()` já existente — redação de segredos automática, nada novo aqui).
  Nunca registra conteúdo de conversa, só identificadores de provider/modelo.

### Tool-calling nativo — preservado integralmente

O Router nunca inspeciona nem reescreve uma `AIResponse`: `chat()` chama
`provider.chat(messages, tools=tool_specs)` e devolve exatamente o que veio —
`AIResponse`, `ToolCall`, `ToolSpec` (`ai/base.py`) continuam sendo os mesmos objetos,
sem cópia/transformação. Nenhum JSON-no-prompt, nenhum parsing manual, nenhum regex —
`tests/test_ai_router.py` tem testes específicos garantindo que `ToolCall`/`ToolSpec`
atravessam o Router sem alteração, além de um teste no `Orchestrator`
(`test_orchestrator_runs_the_full_native_tool_calling_loop_through_the_ai_router`)
construindo um `AIRouter` diretamente (não via helper de teste) para deixar esse fluxo
completo explícito.

### Configuração (`settings/config.py`)

Dois campos novos em `Settings`: `ai_provider: str = "ollama"` (qual provider usar) e
`ai_routing_mode: AIRoutingMode = AIRoutingMode.AUTO` (`"auto"` ou `"manual"`).
`AIRoutingMode` é definido em `settings/config.py`, não importado de `ai/router.py` —
`settings/` continua um módulo-folha sem dependência de `core`/`ai`/`tools` (mesmo
padrão de `ProactivityLevel`/`PermissionAutoApprove`); `main.py` converte entre os dois
enums ao montar o Router.

`Settings.validate()` (chamado em `__post_init__`, então roda em toda construção) rejeita
`ai_model`/`ai_provider` vazios e `ai_routing_mode` fora de `auto`/`manual`, levantando
`SettingsValidationError` — a mesma classe de erro documentada como a correção
estrutural para o incidente do "SIM" (um nome de modelo inválido que ficou salvo em
`config.json` e só se manifestou bem depois, como um 404 confuso do Ollama). Essa
validação é sintática (não-vazio, enum válido); se o MODELO realmente existe no Ollama
continua sendo responsabilidade do `OllamaProvider.is_available()`, que já existia e não
mudou. `ConfigManager.is_first_run()` agora também trata um `ValueError` (incluindo
`SettingsValidationError`) como "precisa rodar o assistente de configuração de novo", em
vez de deixar a exceção derrubar o app — mesma filosofia que já existia para JSON
corrompido, só que agora cobrindo valores corrompidos também. Configurações antigas
(sem `ai_provider`/`ai_routing_mode`) continuam carregando normalmente — os campos novos
simplesmente assumem o default, mesmo padrão usado para todo campo adicionado em versões
anteriores.

### GUI — preparação mínima, sem tela nova

`ui/desktop/app.py::_status_snapshot()` ganhou duas chaves (`ai_provider`,
`ai_routing_mode`) no dicionário que já existia, prontas para uma futura tela mostrar
"AI: Ollama / Model: llama3.2 / Mode: Auto" — nenhuma tela nova foi criada. Trocar o
modelo em ⚙ Configurações agora chama `ai_router.register(...)` de novo sob o mesmo nome
em vez de reatribuir `orchestrator.ai_provider` (que não existe mais) — o
`Orchestrator`/Router seguem sendo os mesmos objetos depois da troca.

## Permissões (`security/permissions.py`)

| Nível  | Exemplos (V1)                                   | Confirmação? |
|--------|--------------------------------------------------|--------------|
| LOW    | abrir app, listar processos, CPU/RAM/disco, listar/criar diretório, abrir arquivo/URL | Não (auto-aprovado) |
| MEDIUM | mover arquivos, alterar configurações (futuro)   | Sim |
| HIGH   | excluir, instalar, ações irreversíveis (futuro)  | Sim |

Nenhuma ferramenta MEDIUM/HIGH foi implementada na V1 — apenas os níveis e o mecanismo de
confirmação já existem, prontos para as próximas ferramentas.

Essa checagem é feita pelo `Orchestrator` para **toda** `ToolCall`, venha ela de onde vier
— o modelo nunca executa nada diretamente nem contorna essa etapa; ele só solicita um
nome de ferramenta, e é o `Orchestrator` quem decide se ela roda ou não.

## Pipeline de voz (`voice/`) — V1.1

```
🎙️ microfone
   -> AudioCapture.record_until_silence()   (voice/audio.py, sounddevice)
      grava até detectar silêncio após fala (endpointing por energia/RMS,
      sem VAD pesado) ou até um limite de segurança (max_duration); grava em
      um WAV temporário de 16 kHz mono
   -> SpeechToText.transcribe()             (voice/stt.py, FasterWhisperSTT)
      100% local (CTranslate2 + modelo Whisper "base" por padrão)
   -> Orchestrator.handle_message(texto)    <-- EXATAMENTE o mesmo método que
      ui/cli.py chama para uma mensagem digitada. VoiceService não duplica
      nenhuma lógica de conversa, IA, tools ou permissões — só troca a
      entrada/saída de texto por voz.
   -> TextToSpeech.speak()                  (voice/tts.py, Pyttsx3TTS/SAPI5)
🔊 alto-falante
```

`voice/service.py::VoiceService.listen_and_respond()` coordena esse ciclo e devolve um
`VoiceTurnResult` (success/transcript/reply/error/timings) — nunca lança exceção para
fora; todo erro (microfone, STT, IA, resposta vazia) vira uma mensagem amigável em
português, e o restante do sistema (permissões, tools, Ollama) continua funcionando
exatamente como no modo texto, porque é o **mesmo** `Orchestrator`.

Ativação nesta etapa é manual (`push_to_talk_key`, padrão `v`, digitado na UI/CLI) —
sem escuta contínua nem wake word. `voice/wakeword.py` já existe como abstração pronta
para uma futura ativação por "Steve..." (V1.2), mas não tem implementação ainda.

### Por que faster-whisper (e não Whisper puro, nem só o reconhecimento do Windows)

- **Reconhecimento de voz nativo do Windows (SAPI)**: descartado — o pt-BR do SAPI é
  historicamente fraco/quase inexistente para reconhecimento (diferente da síntese,
  onde a voz "Maria" já vem instalada e funciona bem).
- **openai-whisper puro**: melhor precisão que o SAPI, mas exige PyTorch completo —
  facilmente 1-2 GB+ de instalação. Era exatamente essa dependência pesada que a V1
  evitou instalar antes da fundação estar validada.
- **faster-whisper (escolhido)**: mesma família de modelos Whisper (boa precisão em
  pt-BR), mas roda sobre CTranslate2 — um motor de inferência C++ enxuto, com
  quantização int8 para CPU. A instalação (faster-whisper + ctranslate2 + tokenizers +
  huggingface-hub) soma **~80 MB**, nada de PyTorch. O modelo "base" (padrão,
  configurável via `settings.stt_model_size`) baixa **~141 MB** do Hugging Face na
  primeira execução — depois disso, a transcrição é 100% local e offline.

### Privacidade

Áudio nunca sai da máquina: captura, transcrição e síntese são locais. A única chamada
de rede em todo o pipeline de voz é o download do modelo Whisper na primeira execução
(mesmo princípio já aceito para o `llama3.2` via `ollama pull`) — depois disso, nenhuma
chamada externa acontece. O WAV temporário gravado a cada turno é apagado logo após a
transcrição (`VoiceService._cleanup`), então áudio falado não fica acumulado em disco.

### Configurações novas (`settings/config.py`)

`voice_input_enabled`, `voice_input_device`, `voice_output_device`, `stt_engine`,
`stt_model_size`, `tts_engine`, `tts_voice`, `speaking_rate`, `speaking_volume`,
`push_to_talk_key` — todas com defaults seguros (voz desligada por padrão). O toggle de
saída de voz em modo texto continua sendo o já existente `voice_output_enabled`; o novo
`voice_input_enabled` é o par simétrico para a captura por microfone, mantendo a
convenção de nomes já estabelecida em vez de introduzir um terceiro nome genérico.

`voice_output_device` é aceito na configuração, mas o pyttsx3/SAPI5 não expõe seleção de
dispositivo de saída por chamada — ele sempre toca no dispositivo padrão do Windows. Essa
limitação está documentada aqui e no relatório da etapa; o campo fica pronto para um
motor de TTS futuro que suporte a seleção.

## GUI Desktop (`ui/desktop/`) — V1.1.5

`python main.py` abre a GUI por padrão; `python main.py --cli` abre o terminal. Ambas
consomem `main.build_app_services(settings, logger, confirmation_service=...)` — a
única função que monta `Database`, `MemoryService`, `OllamaProvider`, `ToolManager`,
`Orchestrator`, `TextToSpeech` e `VoiceService`. Nenhuma lógica de IA/ferramentas é
duplicada entre as duas interfaces; a GUI só troca a entrada/saída de texto por
widgets.

### Toolkit: CustomTkinter

Tkinter é built-in no Python (nenhuma instalação extra) e já seria suficiente conforme
pedido, mas o visual ttk padrão do Windows fica datado sem um trabalho grande de
estilização manual — e nenhuma solução de tema claro/escuro nativa. **CustomTkinter**
resolve isso: é Tkinter por baixo (mesma leveza, mesma ausência de servidor web, mesmo
funcionamento offline), adiciona widgets com visual moderno prontos (`CTkButton`,
`CTkEntry`, `CTkScrollableFrame`, ...) e `ctk.set_appearance_mode("light"/"dark")`
nativo. Custo: **1,5 MB** instalado, duas dependências pequenas (`darkdetect`,
`packaging`) — nada de motor web, nada de Electron/CEF.

### Por que não uma alternativa web (Eel, pywebview, Flask+navegador)

Descartadas de propósito: o pedido foi explícito em não transformar o projeto numa
aplicação web, e qualquer uma delas envolve um servidor HTTP local e/ou um motor de
navegador embutido (dezenas a centenas de MB) — o oposto de "leve" e "sem servidor web".

### Arquitetura em camadas

```
GUI (widgets)                    core/ e services (sem mudança de "cérebro")
─────────────────                 ─────────────────────────────────────────
window.py, chat_view.py,          ChatController (ui/desktop/controller.py)
status_bar.py, controls.py,           -> Orchestrator.handle_message(texto, on_tool_call=...)
settings_dialog.py,                   -> VoiceService.listen_and_respond(on_state=..., on_tool_call=...)
first_run_wizard.py               BackgroundRunner (ui/desktop/async_bridge.py)
       ↓ eventos de widget            -> roda a chamada numa thread daemon
       ↓ (clique/Enter)               -> devolve resultados/eventos de progresso
ChatController  ←───────────────      por uma queue.Queue, drenada só via
       ↓                              widget.after(...) — nunca toca um widget
Orchestrator / VoiceService            fora da main thread
```

- **`async_bridge.py::BackgroundRunner`** — Tkinter não é thread-safe. Toda chamada que
  bloqueia (`Orchestrator.handle_message`, `VoiceService.listen_and_respond`,
  `TextToSpeech.speak`) roda numa `threading.Thread` daemon; o resultado (ou um evento
  de progresso, como o nome de uma ferramenta em uso) é colocado numa `queue.Queue` e só
  é entregue ao código que mexe em widgets através de um polling em `widget.after(...)`
  — a única thread que pode tocar a UI é a principal.
- **`controller.py::ChatController`** — não importa `customtkinter`; é a única ponte
  entre widgets e o `Orchestrator`/`VoiceService`. Mantém uma flag `_busy` que **impede
  uma segunda mensagem de começar enquanto a primeira não terminou** — os botões Enviar
  e 🎙️ ficam desabilitados nesse meio-tempo (`InputBar.set_busy`).
- **Indicador de ferramenta em uso** — usa o hook opcional `on_tool_call` adicionado a
  `Orchestrator.handle_message()` e `VoiceService.listen_and_respond()` (parâmetro
  padrão `None`, 100% retrocompatível — os 96 testes anteriores a esta etapa continuam
  passando sem alteração). O hook só *observa* qual ferramenta está prestes a rodar;
  ele nunca decide nada nem contorna `ToolManager`/`PermissionManager`.
  `ChatController` traduz o nome da ferramenta para um rótulo discreto em português
  (`tool_status_label`, ex. `check_cpu` -> "🧠 Consultando CPU...") — o JSON bruto do
  `ToolResult` nunca chega à tela.
- **Erros** — a GUI nunca mostra traceback: reaproveita as mensagens amigáveis que já
  existiam (`UNAVAILABLE_MESSAGE`, `TOOL_LIMIT_MESSAGE` do Orchestrator;
  `MIC_ERROR_MESSAGE`, `NO_SPEECH_MESSAGE`, etc. do VoiceService) e, para uma exceção
  inesperada de verdade, mostra `GENERIC_ERROR_MESSAGE` enquanto o traceback completo
  vai para `logs/steve.log` via `logger.exception` (dentro de `BackgroundRunner.run`).
- **Configurações** — `SettingsDialog` só coleta um `dict` e devolve via `on_save`; quem
  aplica (persistir com `ConfigManager.save`, reconstruir `OllamaProvider` se o modelo
  mudou, recarregar `VoiceService`/`TextToSpeech` se algo de voz mudou) é
  `SteveApp._apply_settings` (`app.py`) — o diálogo em si não conhece `ConfigManager`
  nem serviços.
- **Primeira execução** — `FirstRunWizard` é o equivalente em GUI de
  `ConfigManager.run_first_run_wizard()`: mesmo `Settings`/`ConfigManager`, só que
  preenchido por widgets em vez de `input()`.

### Como adicionar um componente novo à GUI

1. Crie o widget em `ui/desktop/` (siga o padrão de `chat_view.py`/`status_bar.py`:
   recebe a `Palette` no construtor, não importa serviços diretamente).
2. Se ele precisa acionar algo do Core, adicione um método em `ChatController` (ou use
   os já existentes `send_text`/`send_voice`) — nunca chame `Orchestrator`/`ToolManager`
   direto do widget.
3. Para qualquer chamada potencialmente lenta, use `BackgroundRunner.run(...)`; nunca
   chame algo bloqueante diretamente num handler de clique.

## Núcleo visual — o Orb (`ui/desktop/orb/`)

Uma esfera animada no topo da janela é o "núcleo vivo" do Steve — reage ao que ele está
fazendo de verdade (nunca um estado inventado) e à própria voz, do usuário ou do Steve.
Identidade visual própria: gradiente azul/violeta (não o preto-e-branco minimalista nem
o verde de assistentes conhecidos), com um brilho suave ao redor do blob.

```
ui/desktop/orb/
├── states.py        OrbState (IDLE/LISTENING/THINKING/TOOL_EXECUTION/SPEAKING/ERROR)
├── animation.py      física pura (sem Tkinter): interpola forma/cor por estado, sem I/O
├── audio_reactive.py  RMS puro (sem FFT) — envelope pré-computado (TTS) + RMS ao vivo (mic)
├── orb.py             widget Canvas real: numpy monta o frame, Pillow converte pra Tk
└── reactive_tts.py    faz a fala do próprio Steve mover o orb
```

### Como o estado chega ao orb

`ChatController` (não `orb.py`) decide o `OrbState` — a partir dos MESMOS eventos que já
alimentavam a barra de status (`on_tool_call`, as mensagens de estado do
`VoiceService`), nunca um estado paralelo. Fluxo de voz observado num teste real:
```
LISTENING -> THINKING -> TOOL_EXECUTION -> SPEAKING -> IDLE
```
`Orchestrator.handle_message()` e `VoiceService.listen_and_respond()` ganharam um
parâmetro opcional `on_tool_call`/`on_orb_state` (padrão `None`, 100% retrocompatível —
nenhum teste anterior a esta etapa precisou mudar de comportamento) só para reportar
"uma ferramenta está prestes a rodar"; ele nunca decide nada.

### Como o orb reage a áudio (RMS, sem FFT)

- **LISTENING** (voz do usuário): `AudioCapture.record_until_silence()` ganhou um
  `on_amplitude` opcional, chamado com o RMS bruto de cada chunk de ~100ms — o mesmo
  RMS que já era calculado para decidir quando parar de gravar, só que agora também
  observado pela UI. `ui.desktop.orb.audio_reactive.normalized_rms()` normaliza esse
  valor para 0..1 antes de repassar ao orb.
- **SPEAKING** (voz do Steve): pyttsx3/SAPI5 não tem callback de amplitude ao vivo, então
  `OrbReactiveTTS` sintetiza a fala num WAV primeiro (`Pyttsx3TTS.speak_to_file`, já
  existia), pré-computa a curva de amplitude (RMS por janela de 50ms) com
  `AmplitudeEnvelope`, toca esse WAV de forma assíncrona (`winsound`, stdlib) e, durante
  a reprodução, amostra a curva pré-computada contra o tempo decorrido — o movimento é
  genuinamente guiado pelo que está sendo dito (sílabas mais altas mexem mais o orb),
  não um timer aleatório. `speak()` continua bloqueando até terminar, então
  `VoiceService`/CLI nunca precisam saber que essa troca aconteceu.

### Um bug real encontrado e corrigido nesta etapa (não específico do orb)

Ao testar o ciclo completo pela primeira vez, o Steve travava indefinidamente em
SPEAKING. Isolei a causa com reproduções mínimas: `pyttsx3.init()` guarda em cache **um
único** `Engine` por processo; chamar `runAndWait()` uma segunda vez nesse mesmo Engine
trava para sempre — mesmo a partir da mesma thread, sem nenhuma concorrência envolvida.
Como o Steve já reaproveitava uma única instância de `Pyttsx3TTS` durante toda a sessão
(para qualquer resposta falada, não só via orb), **isso já era um bug latente da V1.1
GUI** antes desta etapa — só nunca tinha aparecido porque nenhum teste anterior falava
duas vezes na mesma sessão. Corrigido na origem: `voice/tts.py::Pyttsx3TTS` agora cria um
Engine novo (contornando o cache do pyttsx3) a cada `speak()`/`speak_to_file()`; a
detecção de voz continua acontecendo uma única vez, na construção, via um Engine de
sondagem descartável.

### Desempenho

Medido com o loop real do Tk (`mainloop`, não polling manual): **~14% de um núcleo**
durante animação ativa a 20 FPS/140px (era ~19% a 30 FPS — reduzido de propósito). Cada
frame é ~1-2ms de trabalho (numpy vetorizado + conversão Pillow), então mesmo a 20 FPS
sobra folga entre frames — a IA/Ollama continuam sendo o gargalo real do sistema, não o
orb.

## GUI validada e endurecida — V1.1.6

Etapa de auditoria/hardening sobre a GUI já construída (não uma reconstrução). Antes de
mexer em qualquer coisa, revisei `main.py`, `ui/desktop/app.py`, `window.py`,
`controller.py`, `async_bridge.py`, `VoiceService`, `Orchestrator`, `ToolManager`,
`PermissionManager`, `voice/tts.py`, `voice/stt.py`, `settings/config.py` e a suíte de
testes — três lacunas reais apareceram (nenhuma exigiu reestruturar nada):

1. **Sem seletor de tema na tela de Configurações.** `Settings.theme` ("light"/"dark")
   é novo; `SettingsDialog` agora tem um campo Claro/Escuro; `SteveApp` usa
   `settings.theme` como `appearance_mode` inicial da janela. Trocar o tema salva
   imediatamente, mas só se aplica visualmente na próxima abertura — recriar todos os
   widgets já desenhados com as cores novas ao vivo não é simples no CustomTkinter, e
   o chat mostra um aviso claro disso em vez de fingir que já mudou.
2. **Clicar em ⚙ Configurações duas vezes empilhava dois diálogos.** `SteveApp` agora
   guarda uma referência (`self._settings_dialog`) e, se um já está aberto, só traz ele
   pra frente em vez de criar outro.
3. **Fechar a janela com um turno em andamento.** Testado de propósito (seção
   "Fechamento durante processamento" abaixo): não trava nem derruba o processo — a
   thread do turno é `daemon` e termina sozinha — mas ela tenta gravar a resposta final
   na memória depois que `Database.close()` já rodou, gerando um
   `sqlite3.ProgrammingError` capturado (não um crash) e registrado como ERROR no log.
   Isso é uma consequência esperada de fechar no meio de uma resposta, não um bug —
   `SteveApp._handle_close()` agora registra um aviso INFO explicando isso antes de
   fechar, pra não parecer uma falha real numa investigação futura.

### Fechamento durante processamento (testado de verdade, não presumido)

Fluxo verificado com um turno real em andamento (chamada ao Ollama no meio):
`window._handle_close()` → `orb.stop()` → callback de fechamento do app →
`runner.close()` (para de entregar novos callbacks à UI) → `database.close()` →
`window.destroy()`. A thread de trabalho em segundo plano, que não tem como ser
cancelada no meio de uma chamada de rede sem complexidade desproporcional para esta
etapa, morre sozinha (thread `daemon`) assim que o processo termina — sem processo
órfão, sem travamento da janela ao fechar.

## Memória, Identidade e Contexto — V1.2

Etapa de arquitetura/backend (sem mudança visual grande na GUI): estende `memory/`,
`core/context.py`, `ai/prompts/system_prompt.py` e adiciona `tools/memory.py` — nada foi
reconstruído; a `MemoryService`/`ContextManager`/tool-calling nativa da V1 continuam
sendo a base.

### As quatro camadas conceituais de memória

| Camada     | Onde vive                                   | Exemplo |
|------------|----------------------------------------------|---------|
| IDENTITY   | `ai/prompts/system_prompt.py::IDENTITY` (texto fixo) | "Você é Steve, um assistente..." |
| PERMANENT  | tabela `memories`, `category='permanent'`, sem `expires_at` | "O projeto principal é o Prime Ges." |
| TEMPORARY  | tabela `memories`, `category='temporary'`, com `expires_at` | "Hoje está na tela de login." |
| SESSION    | `SessionManager.turns` (em memória) + `conversation_history` (SQLite) — **não** é uma `category` da tabela `memories` | o histórico da conversa atual |

SESSION ficou deliberadamente fora da tabela `memories`: `SessionManager` e
`conversation_history` já cumpriam esse papel desde a V1; misturar "a conversa atual"
na mesma tabela que fatos duradouros era exatamente o tipo de mistura de camadas que a
V1.2 pediu para evitar.

### Schema (`memory/database.py`)

Migração aditiva e idempotente sobre a tabela `memories` já existente — `Database._migrate_memories_v1_2()`
confere `PRAGMA table_info(memories)` antes de cada `ALTER TABLE ADD COLUMN`, então roda
sem problema tanto num banco novo quanto num banco de uma V1/V1.1 já em uso, sem apagar
nenhuma linha:

```
type, content, important, metadata, created_at, updated_at   -- já existiam (V1)
category   TEXT NOT NULL DEFAULT 'permanent'                 -- identity/permanent/temporary
key        TEXT                                              -- chave estável p/ upsert
source     TEXT NOT NULL DEFAULT 'user'                      -- user/explicit_command/inferred/system
confidence REAL NOT NULL DEFAULT 1.0
expires_at TEXT                                               -- NULL = nunca expira
active     INTEGER NOT NULL DEFAULT 1                         -- soft-delete
```

### Resolução de conflito: upsert por `key`, não sobrescrita nem duplicação

`MemoryService.remember(content, key=..., ...)` é o caminho central de escrita: se já
existe uma memória **ativa** com a mesma `key`, ela é marcada `active=0` (soft-delete,
continua no banco para auditoria) e uma linha nova assume como versão ativa. Assim
"na verdade agora prefiro Y" depois de "prefere X" vira uma correção — nunca duas
memórias ativas conflitantes ao mesmo tempo. `forget_by_key`/`forget_matching` seguem o
mesmo princípio (soft-delete); só `delete_memory`/`forget` (API V1, ainda usada por quem
já a chamava) apaga de verdade.

### Como o contexto é montado (`core/context.py`)

`ContextManager.build(session, settings, history_limit=10, user_text=None)` monta, nessa
ordem: (1) identidade fixa, (2) política de ferramentas, (3) política de memória, (4) um
resumo das memórias **relevantes** — nunca o banco inteiro — inserido no mesmo prompt de
sistema, (5) os últimos `history_limit` turnos da sessão atual. `user_text` (a mensagem
atual do usuário, quando conhecida) direciona `MemoryService.get_relevant_memories()`: a
seleção pontua por sobreposição de palavras (stdlib `re`, sem embeddings/serviço externo)
entre a mensagem atual e o conteúdo/chave de cada memória, então o que importa *agora*
tem prioridade sobre uma memória antiga e importante mas fora de assunto.
`Orchestrator.handle_message()` passa o `user_text` que acabou de receber para
`context_manager.build()` — uma linha nova no meio do fluxo já existente, tool-calling
nativo intocado.

### Como Steve lembra/esquece: ferramentas nativas, não um parser de texto

`tools/memory.py` define três `Tool`s (`remember_fact`, `forget_memory`,
`recall_memories`), todas `PermissionLevel.LOW`, registradas por
`main.register_default_tools()` como qualquer outra ferramenta. "Lembre que...",
"esqueça X", "o que você lembra sobre mim?" e as muitas variações de frase não dependem
de um parser regex frágil tentando cobrir cada jeito de perguntar — o modelo já decide
quando uma ferramenta é apropriada (via `MEMORY_POLICY` no system prompt) e o pedido
passa pelo mesmo loop de tool-calling nativo (`ai/base.ToolCall`/`ToolSpec`) já testado
para `check_cpu` e as demais ferramentas da V1.

Achado real ao validar contra o Ollama ao vivo: o histórico de sessão só reproduz o
texto final de `user`/`assistant` (`_remember_turn`), nunca os argumentos brutos de uma
tool call — então, num turno seguinte, o modelo às vezes não lembra a `key` exata usada
para salvar um fato e tenta esquecê-lo só por `search_text`. `ForgetMemoryTool.execute()`
tenta `key` primeiro e cai para `search_text` (busca por conteúdo) quando a chave não
bate, em vez de falhar sem tentar a alternativa — corrigido depois de reproduzir o caso
ao vivo (ver relatório da etapa).

### Regras de memorização

O modelo decide via `MEMORY_POLICY` (`ai/prompts/system_prompt.py`), não um filtro
determinístico no código: salvar quando (a) pedido explícito ("lembre que...") ou (b)
informação claramente duradoura ("meu projeto principal é X"); classificar como
`temporary` (expira em 24h) quando vale só para agora ("hoje estou em X"); **não** salvar
comandos comuns ("abra o Chrome") nem conversa casual sem valor futuro. Isso é reforçado
tanto na política quanto na própria descrição do parâmetro `content` de `remember_fact`
(que pede a frase completa com o valor específico, não só a categoria da informação —
outro ajuste feito depois de observar o llama3.2 3B truncar o valor em teste ao vivo).

## Lifecycle, Startup e Shutdown — V1.3

Etapa de infraestrutura de ciclo de vida — não muda o que Steve faz numa conversa, muda
como ele nasce, se recupera de falhas parciais e termina. `core/lifecycle.py` é a peça
nova central; o resto é fiação em `main.py`/`ui/desktop/app.py`.

### Estados (`core/lifecycle.py::LifecycleState`)

`CREATED -> INITIALIZING -> READY | DEGRADED -> SHUTTING_DOWN -> STOPPED`. `LifecycleManager`
é a fonte única de verdade: `begin_startup()` reseta o snapshot e entra em INITIALIZING;
cada peça do boot chama `report_component(nome, ok, detail, essential)` conforme é
construída; `finish_startup()` calcula o estado final (READY se tudo `ok`, DEGRADED se
qualquer coisa falhou) — ninguém seta READY/DEGRADED na mão. `SystemStatus.degraded_reasons`
lista os motivos; `SystemStatus.component(nome)`/`is_ok(nome)` consultam um componente
específico. Isso é exatamente o "system_status" pedido: uma única fonte, sem duplicar
estado em `ai_available`, `voice_service is None`, etc. espalhados pelo código (esses
sinais continuam existindo — `LifecycleManager` só passou a também refletir os mesmos
fatos de um jeito consultável uniformemente).

### Sequência de boot

```
1. carregar configuração         (ConfigManager.load_or_run_wizard / Settings.validate)
2. inicializar logging            (core.logging_setup, já existia)
3. lifecycle.begin_startup(first_run, startup_mode)
4. abrir memória/SQLite           -> report_component("memory", essential=True)
5. inicializar AI Router          -> report_component("ai_router")   (Ollama incluso)
6. inicializar ferramentas        -> report_component("tools", essential=True)
7. inicializar Orchestrator       (não reportado à parte — depende só do que já foi checado)
8. inicializar voz, se habilitada -> report_component("voice")
9. inicializar GUI, se aplicável  -> report_component("gui", essential=True)
10. lifecycle.finish_startup()    -> READY ou DEGRADED
11. determinar saudação           (GreetingService, com first_run correto)
12. mostrar janela / iniciar loop de chat
```

Passos 1-3 e 10-12 vivem em `main.py`/`ui/desktop/app.py`; 4-9 vivem dentro de
`main.build_app_services()`, que ganhou um parâmetro opcional `lifecycle` — passá-lo é
retrocompatível (todo chamador pré-V1.3 que o omite continua funcionando exatamente como
antes, só sem essa observabilidade). A ordem é a mesma que já existia informalmente no
código (memória antes do Router, Router antes das ferramentas, ferramentas antes do
Orchestrator, voz depois) — V1.3 só tornou essa ordem explícita e observável, não a
reinventou.

### DEGRADED não trava o Steve

`ai_router` e `voice` nunca são `essential=True` — uma falha neles vira DEGRADED, não um
crash. `memory`, `tools` e `gui` são `essential=True` (informativo nesta versão — ver
`ComponentStatus.essential`): uma falha ali continuaria resultando em DEGRADED também
(a mesma lógica simples "READY se nada falhou, senão DEGRADED"), mas na prática essas
três são as únicas que, se falharem de verdade (ex.: `Database()` não conseguindo abrir
o arquivo `.db`), fazem a exceção propagar e o processo encerrar de forma clara em vez de
fingir estar pronto — o flag `essential` existe para consumidores futuros (um System
Dashboard, por exemplo) distinguirem visualmente "isso é sério" de "isso é cosmético",
sem exigir nenhuma mudança neste módulo quando esse consumidor existir.

Testado ao vivo (processo real, Ollama real): apontar `ollama_host` para uma porta
inexistente produz exatamente `Steve iniciado em modo DEGRADED: provider 'ollama'
indisponível`, e a mensagem `Aviso — iniciei em modo reduzido (...)` aparece tanto na CLI
quanto (via `chat_view.add_system_message`) na GUI — mas a conversa, a memória e as
ferramentas continuam funcionando normalmente.

### Primeiro início vs. retornando

`ConfigManager.is_first_run()` (já existia) continua sendo a única fonte para essa
decisão — V1.3 não criou um segundo mecanismo. O que estava errado antes: a CLI chamava
`GreetingService().greet(..., is_first_run=False)` **hard-coded**, e a GUI nem usava
`GreetingService` (mensagem própria, só baseada em `ai_available`). Agora `main.py`
determina `first_run` uma vez (`config_manager.is_first_run()`, antes de
`load_or_run_wizard()` consumir esse estado) e propaga esse mesmo booleano até a
saudação, nos dois caminhos.

`GreetingService.greet()` (`core/greeting.py`) também foi corrigido: o branch
`is_first_run=True` respondia com o texto de **introdução do wizard** ("Vamos configurar
seu assistente") repetido *depois* que a configuração já tinha acabado — confuso, e sem
hora do dia nem nome, ao contrário do branch de retorno. Agora os dois branches usam
`time_of_day_greeting()` e o nome real:

```python
"Boa tarde, Junior. É um prazer conhecê-lo."     # is_first_run=True
"Boa tarde, Junior. Bem-vindo de volta."          # is_first_run=False
```

Validado ao vivo: primeiro início mostra a primeira frase (uma única vez, logo após o
wizard salvar); toda execução seguinte mostra a segunda.

### Autostart (`settings/autostart.py`) — agora conectado

O mecanismo (`HKCU\...\Run`, via `winreg`) já existia desde uma versão anterior, mas
**nada o chamava** — `Settings.autostart_enabled` era um campo morto. V1.3 conecta os
dois pontos: `SettingsDialog` ganhou um switch "Iniciar com o Windows";
`SteveApp._apply_settings()` detecta a transição e chama
`autostart.enable_autostart(autostart.default_launch_command())` ou
`autostart.disable_autostart()`. `default_launch_command()` (novo) monta
`"{sys.executable}" "{main.py}"` — o suficiente para relançar o mesmo `.venv` deste
projeto; um build empacotado (`.exe`) passaria seu próprio caminho em vez disso.

Idempotência vem de graça da própria API do Windows: `Run` é um único valor nomeado por
chave, então ativar duas vezes apenas sobrescreve o mesmo valor (não existe "múltiplas
entradas" possível). `is_autostart_enabled()` agora captura `OSError` (antes só
`FileNotFoundError`) — um estado de registro inesperado/inacessível não deve derrubar um
diagnóstico de inicialização, só ser relatado como "não ativado". Testado ao vivo contra
o registro real (`enable -> is_enabled -> enable de novo -> disable -> disable de novo`,
sempre terminando limpo, sem entrada residual) — ver relatório da etapa.

### Single instance (`settings/single_instance.py`)

Um mutex nomeado do Windows (`win32event.CreateMutex`, via `pywin32` — já era dependência
transitiva do `pyttsx3`/SAPI5, nenhuma dependência nova) garante que só um processo Steve
rode por vez. Escolhido em vez de um arquivo de lock porque o próprio Windows libera o
mutex quando o processo termina — inclusive num crash/kill — sem nenhuma limpeza manual
de arquivo obsoleto para acertar. `main()` tenta adquirir o lock **antes** de construir
qualquer serviço (nenhuma segunda conexão SQLite, nenhuma segunda sessão do Ollama); se
falhar, tenta `bring_existing_instance_to_front()` (`win32gui.FindWindow` pelo título da
janela + `ShowWindow`/`SetForegroundWindow`) e encerra — sem nunca criar uma segunda
instância funcional.

Validado ao vivo com dois processos reais (não dois objetos no mesmo processo): o
primeiro processo (GUI, diretório de dados isolado) chega a READY e mantém o mutex; um
segundo processo lançado em seguida termina em ~270ms, registrando "Já existe uma
instância do Steve em execução" e nunca chegando a abrir o banco de dados ou consultar o
Ollama; o primeiro processo continua rodando, intocado. Depois de encerrar o primeiro
processo à força (`Stop-Process -Force`, simulando um crash), um terceiro lançamento
funciona normalmente — confirma que o mutex não fica "preso".

### Minimizar / "Sair" — sem ícone de bandeja literal nesta versão

`MainWindow` ganhou `hide()`/`show()` (`withdraw`/`deiconify`+foco) e um botão "Sair"
explícito, separado do botão de fechar da barra de título. O contrato de
`on_close` mudou de "procedimento" para "decisão": retornar `False` significa "já
ocultei, não destrua a janela" (usado quando `Settings.minimize_to_tray` está ligado);
qualquer outro retorno (incluindo `None`, o contrato antigo) resulta no fechamento
completo de sempre — 100% retrocompatível com quem já passava um `on_close` sem valor de
retorno. "Sair" (`on_quit`) sempre encerra por completo, ignorando `minimize_to_tray` —
precisa sempre existir uma saída inequívoca.

**Decisão consciente de escopo**: não existe um ícone de bandeja literal (nenhum
`pystray`/`Shell_NotifyIcon` nesta versão) — integrar um loop de mensagens Win32 bruto
com o mainloop do Tkinter de forma estável e testável não coube com segurança no tempo
desta etapa (ver instrução original: "implementar somente se puder ser feito de forma
estável e testável"). Em vez disso, "voltar" para uma janela oculta reaproveita o
mecanismo de single-instance: relançar o Steve (atalho, menu iniciar, ou o próprio
autostart) detecta a instância já ativa e chama `bring_existing_instance_to_front()` —
mesmo mecanismo construído para o requisito 9, sem duplicar lógica. `minimize_to_tray`/
`start_minimized` ficam prontos em `Settings`; um ícone de bandeja de verdade (`pystray`,
nova dependência pequena) é o candidato natural de uma etapa futura dedicada à GUI.

### Shutdown

`SteveApp._shutdown()` (novo, extraído do antigo `_handle_close`) é o único caminho de
encerramento completo: `lifecycle.begin_shutdown()` -> loga se havia um turno em
andamento (comportamento da V1.1.6, inalterado) -> `BackgroundRunner.close()` (para de
entregar novos callbacks) -> `Database.close()` -> `lifecycle.finish_shutdown()`. O mutex
de single-instance é liberado explicitamente (`SingleInstanceLock.release()`, no
`finally` de `main()`) além de ser liberado automaticamente pelo Windows ao processo
terminar — os dois juntos garantem que um `main()` seguinte nunca encontre um lock
"fantasma". Nada de memória/histórico é apagado no encerramento — `Database.close()` só
fecha a conexão, os dados em `data/steve.db` continuam lá (confirmado com o mesmo teste
de reconexão já usado na V1.2/V1.2.1).

### Boot health check

Não é uma rotina separada — é o próprio conjunto de `report_component()` chamados durante
o boot (seção "Sequência de boot" acima): `config`, `memory`, `ai_router`, `tools`,
`voice`, `gui`. Nenhuma checagem pesada é feita só para diagnóstico — cada componente já
precisa ser inicializado de qualquer forma; o "health check" é simplesmente reportar o
resultado de cada inicialização que já aconteceria, ao `LifecycleManager`, em vez de só
ao log.

### Eventos de lifecycle (`core/events.py` + `core/lifecycle.py`)

`core/events.py::EventBus` já existia desde uma versão anterior, mas nunca tinha sido
usado em lugar nenhum do código real (só a classe, sem nenhum publisher/subscriber). V1.3
é o primeiro consumidor: `LifecycleManager` publica `lifecycle.startup_begin`,
`lifecycle.component_ready`/`component_failed`, `lifecycle.steve_ready`/`steve_degraded`,
`lifecycle.shutdown_begin`/`shutdown_complete` — os nomes pedidos, com o prefixo
`lifecycle.` para não colidir com eventos de outra área futura no mesmo bus. Nenhum
assinante real existe ainda (não é o objetivo desta etapa) — isso é exatamente a base que
um futuro System Dashboard/Live Activity vai consumir, sem precisar mudar nada aqui.

### O que NÃO mudou (AI Router, Memória, Orb/Voz)

Por instrução explícita: `ai/router.py` não foi tocado — o boot só chama
`build_ai_router()` (já existia desde a V1.2.1) e reporta o resultado ao lifecycle, sem
nenhum `if ollama`/`if provider` novo em lugar nenhum. `memory/` não foi tocado — o boot
só chama `MemoryService.purge_expired()` (já existia) e relata sucesso/falha, sem migração
nem nova regra de memorização. O orb (`ui/desktop/orb/`) não foi tocado — `orb.start()`
continua acontecendo do mesmo jeito de antes; iniciar minimizado (`start_minimized`) não
impede a animação de rodar em segundo plano (limitação conhecida, ver relatório da etapa)
mas também não introduz nenhum estado novo nem chamada de TTS duplicada.

## System Dashboard — V1.4

Um segundo painel — `ui/desktop/dashboard/` — monitorando o computador (CPU/GPU/RAM/
disco/rede/processos, todos com dados reais) e a própria atividade do Steve (Live
Activity), alimentado pelo `EventBus` já ativado na V1.3. **Somente monitoramento**:
nada aqui mata, suspende, apaga ou bloqueia processo, arquivo, IP ou serviço algum.

### Arquitetura: uma fonte de coleta, muitos leitores

```
system_monitor/                          (coleta — nunca importa tkinter)
├── models.py     SystemSnapshot, ProcessSnapshot, ... (dataclasses frozen, só tuplas)
├── metrics.py    CPU/RAM/disco via psutil (mesma lib que tools/system.py já usava)
├── gpu.py        GPU via WMI + Performance Counters do Windows (ver abaixo)
├── network.py    RateTracker genérico — rede e E/S de disco reaproveitam a mesma lógica
├── processes.py  ProcessCollector — mesma psutil de tools/applications.py
└── service.py    SystemMonitorService — thread própria, único coletor do processo

ui/desktop/dashboard/                    (apresentação — nunca chama psutil/WMI direto)
├── window.py          DashboardWindow (CTkToplevel) — 4 abas, assina o EventBus
├── overview.py         cards CPU/GPU/RAM/Disco/Rede
├── performance.py      4 LineGraph (histórico)
├── graph.py             LineGraph — Canvas puro, sem lib de gráficos
├── processes_view.py   tabela ttk.Treeview ordenável, somente leitura
├── live_activity.py    feed estilizado de eventos reais do Steve
└── formatting.py       "Indisponível"/unidades — compartilhado entre as abas
```

Nenhum widget chama `psutil`/WMI diretamente — `SystemMonitorService` é o único coletor;
todo o resto (Overview, Performance, Processes) só lê snapshots que ele já produziu, seja
via evento (`SYSTEM_METRICS_UPDATED`/`PROCESS_LIST_UPDATED`) seja via os atributos
públicos (`latest_snapshot`, `cpu_history`, ...) ao reabrir a janela.

### GPU cross-vendor sem SDK de fabricante

`psutil` não tem suporte a GPU. Em vez de `pynvml`/`GPUtil` (só NVIDIA — o pedido foi
explícito em não assumir um fabricante; a máquina de desenvolvimento deste projeto, aliás,
tem uma AMD Radeon RX 6750 XT dedicada + Intel UHD 770 integrada, nenhuma NVIDIA),
`system_monitor/gpu.py` usa dois mecanismos nativos do Windows, ambos já alcançáveis via
`pywin32` (dependência já existente — ver `voice/tts.py`'s SAPI5 via o mesmo pacote,
**nenhuma dependência nova**):

- **WMI** (`Win32_VideoController`) para o nome dos adaptadores — funciona para
  qualquer fabricante.
- **Performance Counters** ("GPU Engine", "GPU Adapter Memory") para utilização/VRAM —
  o mesmo mecanismo que o Gerenciador de Tarefas usa desde o Windows 10 1803, também
  cross-vendor (funciona com qualquer driver WDDM).

Validado ao vivo nesta própria máquina (AMD + Intel): utilização real (~24-26% sob carga
observada durante o desenvolvimento), VRAM usada real (~1,2 a ~4 GB observados), e os
dois nomes de adaptador corretos.

`"Utilization Percentage"` é um contador de taxa — como `psutil.cpu_percent()`, precisa
de duas amostras ao longo de um tempo real para significar algo; por isso `GpuMonitor`
mantém sua query PDH **aberta entre chamadas** (primada uma vez, lida a cada `sample()`
seguinte) em vez de abrir/amostrar duas vezes por chamada, o que bloquearia ~1s dentro de
cada poll — o próprio intervalo natural entre polls do `SystemMonitorService` (~1s) já
faz esse papel de "priming".

**VRAM total**: `Win32_VideoController.AdapterRAM` (WMI) é uma limitação documentada do
Windows — é um DWORD de 32 bits e estoura/trunca silenciosamente para qualquer placa com
mais de 4GB. Verificado nesta mesma máquina: uma AMD de 12GB reporta ~4GB (errado) via
WMI. `gpu.py` usa em vez disso a chave de registro do próprio driver
(`HardwareInformation.qwMemorySize`), que reportou corretamente ~12GB. Sem essa chave
(comum em GPUs integradas, que compartilham RAM do sistema em vez de ter VRAM fixa),
o valor fica `None` — nunca um número inventado.

**Temperatura de GPU nunca é reportada** (sempre `None`/indisponível) — não existe API
do Windows cross-vendor para isso; só SDKs por fabricante (NVML/ADL/igcl), fora de escopo
por instrução explícita ("não instalar dependências pesadas sem necessidade").

### Temperatura de CPU: sempre N/A, por decisão (V1.4.1 PATCH 04)

`MSAcpi_ThermalZoneTemperature` (WMI) é a única fonte nativa do Windows para temperatura
de CPU — e é conhecida por ser pouco confiável em placas-mãe desktop. Confirmado
empiricamente nesta máquina, DUAS vezes (V1.4 e novamente na auditoria da V1.4.1 PATCH
04): o valor voltou **congelado em exatos 27.9°C** nas duas ocasiões, mesmo sob carga —
um sensor de CPU real se moveria visivelmente. A partir da PATCH 04, `metrics.py` **não
consulta mais essa WMI class** — `_cpu_temperature_celsius()` sempre retorna `None`,
deliberadamente, por regra explícita do projeto: uma fonte já comprovadamente pouco
confiável não deve ser exibida como se fosse uma leitura real, mesmo sendo saída
genuína de API. `psutil.sensors_temperatures()` também foi verificado e não existe nesta
plataforma (`AttributeError` — é Linux/FreeBSD-only). Não existe alternativa nativa do
Windows sem um driver de kernel específico de fabricante (o que HWiNFO/Core Temp usam),
fora de escopo aqui. O Dashboard mostra "Indisponível" para este campo permanentemente.

### Frequência de CPU: "atual" pode ser, na prática, a nominal (V1.4.1 PATCH 04)

`psutil.cpu_freq().current` é a melhor fonte disponível sem dependências novas, mas um
achado real desta auditoria: nesta máquina de desenvolvimento, `current` e `max`
retornaram **exatamente o mesmo valor (3700 MHz) antes, durante e depois de 2 segundos
de carga sustentada de CPU** — ou seja, o valor rotulado "atual" pelo psutil comportou-se
como a frequência nominal/máxima nesta máquina, não como uma leitura dinâmica real (uma
limitação conhecida de `Win32_Processor.CurrentClockSpeed` em alguns sistemas AMD, que
não expõe o P-state real via essa WMI class genérica). Diferente da temperatura, esta
não é uma limitação universalmente documentada da API — por isso o código não passou a
mostrar N/A; em vez disso, `CpuMetrics` agora expõe `frequency_mhz` (atual) E
`frequency_max_mhz` (máxima, `psutil.cpu_freq().max`) lado a lado no Dashboard, permitindo
notar quando os dois números são idênticos, em vez de esconder essa informação atrás de
um único campo "Frequência".

### Uso de CPU e GPU: primeira amostra é sempre `None`, nunca 0% (V1.4.1 PATCH 04)

Dois bugs reais confirmados nesta auditoria, ambos com o mesmo formato (uma leitura que
precisa de "priming" sendo tratada como se já fosse válida na primeira chamada):

- **CPU**: `psutil.cpu_percent(interval=None)` é documentado pelo próprio psutil como
  retornando um valor sem sentido na primeira chamada (nada para comparar ainda) —
  verificado ao vivo: a primeira chamada retornou `0.0`, a segunda (0.3s depois) um
  `26.2` real. `system_monitor/metrics.py::CpuUsageTracker` agora rastreia isso (uma
  instância por `SystemMonitorService`, como `GpuMonitor`/`RateTracker`) e retorna
  `None` só na primeira amostra.
- **GPU**: `"Utilization Percentage"` (PDH) é um contador de taxa — a primeira
  `CollectQueryData` logo após `AddCounter` **sempre** levanta `PDH_INVALID_DATA` para
  cada contador recém-adicionado, verificado ao vivo (todos os ~116 contadores de
  engine 3D falharam na primeira coleta desta máquina). O código anterior tratava cada
  falha como "pular" e retornava a soma parcial (`0.0`, já que nenhum contador
  contribuiu) — indistinguível de uma GPU genuinamente ociosa.
  `GpuMonitor._utilization_percent()` agora conta quantos contadores tiveram sucesso
  nesse ciclo; se nenhum teve, retorna `None` em vez de `0.0`.

Ambos os campos (`CpuMetrics.percent`, `GpuMetrics.utilization_percent`) já eram/são
`float | None` — o Dashboard já trata `None` corretamente via `format_percent()`
("Indisponível"), e os gráficos de histórico (`LineGraph`) já pulam `None` como lacuna na
linha em vez de desenhar zero, o mesmo padrão já usado para os históricos de GPU/rede.

### VRAM e uso de GPU com múltiplos adaptadores: soma documentada, não escolha (V1.4.1 PATCH 04)

Esta máquina tem uma GPU dedicada (AMD RX 6750 XT) e uma integrada (Intel UHD 770), e o
código sempre reportou `utilization_percent`/`memory_used_bytes` como a SOMA de todos os
adaptadores presentes, não a de um único adaptador escolhido — isso já era o
comportamento antes da PATCH 04, mas não estava documentado. Auditoria concluiu: manter a
soma (implementar seleção de adaptador seria "suporte complexo a múltiplas GPUs",
explicitamente fora de escopo desta etapa), mas documentar claramente o que ela significa
(ver `GpuMonitor`'s docstring de classe) — na prática é inofensivo quando a GPU
integrada está ociosa (relatou 0 MB neste teste, então a soma equivale ao valor real da
dedicada), mas combina os dois adaptadores se ambos estiverem ativos ao mesmo tempo.
`memory_total_bytes` não sofre desse problema na prática: vem da chave de registro
(`HardwareInformation.qwMemorySize`), que normalmente só existe para a GPU dedicada (a
integrada deste dev não tem essa chave), então somar "quando presente" já reduz
naturalmente ao valor real de uma única GPU no caso comum. `utilization_percent` agora
também é limitado (`min(..., 100.0)`) para nunca mostrar mais que 100% numa métrica que
implica uma escala 0-100 para quem lê.

### Disco: falha na consulta agora é N/A, não um disco vazio fabricado (V1.4.1 PATCH 04)

`collect_disk_metrics()` antes retornava `DiskMetrics(percent=0.0, used_bytes=0,
free_bytes=0, total_bytes=0)` quando `psutil.disk_usage(path)` falhava (ex.: uma unidade
removível/de rede desconectada) — isso renderizava no Dashboard como "Uso: 0.0%" e
"Livre: 0 B / 0 B", indistinguível de um disco real, vazio, de 0 bytes. Corrigido para
retornar `None` em todos os quatro campos (`percent`/`used_bytes`/`free_bytes`/
`total_bytes`), que o Dashboard já renderiza corretamente como "Indisponível" via
`format_percent`/`format_bytes` — mesmo padrão de todos os outros campos já opcionais.
Verificado ao vivo: `C:\` real retorna valores reais (95.2% usado); um caminho
inexistente retorna `None` em todos os campos, não mais um disco fabricado.

### Atualização: thread própria, nunca no callback do Tkinter

`SystemMonitorService._run()` roda numa `threading.Thread(daemon=True)` própria — nunca
na thread principal do Tkinter, nunca dentro de um `after()`. A cada ciclo, coleta
métricas (padrão ~1s) e, a cada `process_interval` (padrão ~2s), também processos —
publicando `SYSTEM_METRICS_UPDATED`/`PROCESS_LIST_UPDATED`/`NETWORK_METRICS_UPDATED` no
`EventBus` compartilhado (o mesmo de `core/lifecycle.py`, nenhum sistema paralelo).
Histórico limitado a `HISTORY_LENGTH = 120` amostras (~2 minutos a 1s) via `collections.
deque(maxlen=...)` — nunca cresce sem limite.

**Callback do EventBus roda na thread que publicou o evento** — para o monitor, isso é a
thread de coleta, nunca a principal. `DashboardWindow` nunca toca um widget direto num
desses callbacks: cada um só faz `self._runner.post(...)`, reaproveitando o MESMO
`BackgroundRunner` da janela principal (`SteveApp.runner`) em vez de criar um segundo
loop de polling — a marshalling para a thread principal do Tk continua sendo,
exclusivamente, essa fila já existente.

### Ciclo de vida: monitor ≠ janela do Dashboard

`SystemMonitorService.start()` é chamado quando o Dashboard é aberto pela primeira vez
(idempotente — reabrir ou abrir duas vezes nunca cria um segundo coletor, e
`SteveApp._open_dashboard()` também guarda contra uma segunda `DashboardWindow` com o
mesmo padrão já usado para `SettingsDialog`). Fechar a janela do Dashboard **não** chama
`.stop()` — o monitor continua rodando (impacto medido como desprezível, ver relatório da
etapa) até o Steve encerrar de verdade, quando `SteveApp._shutdown()` chama
`services.system_monitor.stop()`. Essa separação deliberada (monitor vs. janela) é o que
permite reabrir o Dashboard sem reprimar os contadores de GPU/processo do zero.

**Confirmado por auditoria na V1.4.1 (PATCH 03)**: `SystemMonitorService` é um serviço do
Steve, independente da existência do Dashboard — não "só existe enquanto o Dashboard está
aberto". Essa é a relação certa porque futuras funcionalidades (Security Center,
inteligência de sistema contínua) vão precisar do monitor rodando sem depender de uma
janela de visualização estar aberta; o Dashboard é só um consumidor/visualização entre
outros possíveis, nunca o dono do ciclo de vida do coletor.

A mesma auditoria encontrou um problema real de concorrência (não hipotético): `start()`/
`stop()` faziam uma checagem-depois-ação sem lock em `self._thread` — duas chamadas
verdadeiramente concorrentes a `start()` (nunca acontece com os chamadores atuais, que só
chamam de dentro da thread principal do Tk, mas um requisito explícito da etapa era
auditar essa robustez) podiam ambas passar pela checagem `is_running` antes de qualquer
uma atribuir `self._thread`, vazando uma thread real sem rastreamento. Corrigido com um
`self._lifecycle_lock` dedicado (separado do `_lock` que já protegia leituras/escritas de
snapshot/histórico) envolvendo toda a sequência de `start()`/`stop()` — a única mudança de
código desta etapa; tudo o mais já estava correto (ver relatório do PATCH 03).

### Live Activity ("Steve Core"): semântica real, apresentação estilizada

`core/orchestrator.py` ganhou quatro eventos novos —`AI_REQUEST_STARTED`,
`AI_RESPONSE_RECEIVED`, `TOOL_STARTED`, `TOOL_COMPLETED` — publicados no mesmo
`EventBus` (parâmetro `event_bus` opcional no construtor, `None` por padrão,
retrocompatível com todo teste/chamador anterior). `voice/service.py` ganhou
`VOICE_STATE_CHANGED`, publicado nos mesmos pontos que já chamavam `on_state(...)` para
mover o orb — nenhuma máquina de estados nova. Os payloads são deliberadamente mínimos
(nome da ferramenta + booleano de sucesso; nunca o conteúdo da mensagem, o resultado da
ferramenta ou um caminho de arquivo) — Live Activity nunca vê dado sensível porque ele
nunca é publicado em primeiro lugar. `ui/desktop/dashboard/live_activity.py` ainda aplica
uma sanitização defensiva de caminhos (`_sanitize`) por cima disso, como segunda camada.

`live_activity.py::_EVENT_PRESENTATION` mapeia cada evento real para uma apresentação
estilizada (`TOOL_STARTED` → "> CORE MODULE / > EXECUTING", `TOOL_COMPLETED` → "> OPERATION
/ > COMPLETE", etc.) — a semântica é sempre real, só a apresentação é estilizada, como
pedido. Estado IDLE por padrão; um evento de "início" (ex. `TOOL_STARTED`) muda para um
estado ativo, e um evento de "conclusão" correspondente agenda a volta a IDLE após ~2.5s
— nunca uma animação contínua sem relação com eventos reais.

### Responsividade

Cada aba se religa num grid de 1/2/3 colunas conforme a largura (`<Configure>` na aba),
sem recriar nenhum widget — só `grid_forget()`/`grid()` nos cards/gráficos já existentes.
`update_snapshot()`/`update_history()`/`update_processes()` só chamam `.configure(...)`
em labels/canvas/linhas de tabela já existentes — nada é reconstruído a cada tick (~1s).

### Testes

`tests/test_system_monitor.py` (unitário, hardware totalmente mockado — psutil, WMI,
PDH) e `tests/test_dashboard.py` (GUI real + integração: System Monitor → EventBus →
Dashboard; Orchestrator → EventBus → Live Activity; Lifecycle → System Monitor →
Shutdown) — ver relatório da etapa para a lista completa e os resultados.

## O que fica para depois

Estas peças aparecem na estrutura de pastas descrita no prompt original, mas **não** têm
lógica implementada nesta V1 — a arquitetura evita módulos monolíticos, então elas entram
como pacotes próprios quando chegar a hora, sem tocar no Core:

- **Configurações do orb** (`orb_enabled`, `animation_quality`, `animation_fps`,
  `audio_reactive`, `orb_size`) — mencionadas como "futuro" no pedido original; hoje
  `Orb`/`MainWindow` já aceitam esses parâmetros no construtor (`fps`, `size`,
  `orb_enabled`), só não são configuráveis pela tela de Configurações ainda.

- **Planner** (`core/planner`) — versão futura. Vai transformar objetivos em planos de etapas.
- **Skills** (`skills/`) — versão futura. Registro de capacidades plugáveis sem alterar o Core.
- **Proactive Engine** (`proactive/`) — versão futura. Sugestões e detecção de padrões.
- **Automation** (`automation/`) — versão futura.
- **Meetings copilot** (`meetings/`) — versão futura.
- **Wake word ("Steve...")** — versão futura (não reservada, reavaliar quando chegar a
  vez). `voice/wakeword.py` já tem a abstração (`WakeWordDetector`), sem implementação
  real ainda.
- **Claude Bridge, Android, System Dashboard, Cyberpunk Live Activity, Security Center,
  novas integrações externas** — versões futuras, explicitamente fora do escopo da
  V1.3. `core/lifecycle.py`'s eventos (ver seção "Lifecycle, Startup e Shutdown — V1.3")
  são a base que System Dashboard/Live Activity vão consumir quando chegar a vez.
- **Ícone de bandeja literal (systray)** — versão futura dedicada à GUI. `Settings.
  minimize_to_tray`/`start_minimized` e o hide/show de `MainWindow` já existem (V1.3);
  falta só o ícone clicável em si (`pystray` ou `Shell_NotifyIcon`) — ver seção
  "Minimizar / 'Sair'" acima para o porquê de ter ficado de fora desta etapa.
- **Android** — versão futura. O Core não depende dele; nada foi feito nesta etapa.

## Segurança de dados

`AuditLogger` (`security/audit.py`) nunca grava campos como `password`, `token`, `api_key`
ou `secret` — eles são substituídos por `***REDACTED***` antes de tocar o disco.

## Auto-start com o Windows

`settings/autostart.py` implementa `enable_autostart` / `disable_autostart` via
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. Desde a V1.3, isso está conectado:
`SettingsDialog` tem um switch "Iniciar com o Windows" e `SteveApp._apply_settings()`
liga/desliga o mecanismo quando o usuário muda esse switch — nunca automaticamente, só
por escolha explícita. Ver seção "Lifecycle, Startup e Shutdown — V1.3" para detalhes
(idempotência, `default_launch_command()`, validação de estado corrompido).

## V1.5 — Security Center

Primeira versão de uma camada complementar de monitoramento, análise e detecção de
segurança — **não** um antivírus completo, **não** um substituto do Microsoft Defender.
Princípio fundamental: DETECTAR → ANALISAR → ALERTAR, nunca DETECTAR → AGIR
automaticamente. Nenhuma ação destrutiva automática existe nesta versão (ver seção
"O que NÃO existe nesta versão" abaixo).

### Arquitetura: `security/` ganhou um núcleo novo, além do que já existia

`security/audit.py`, `security/confirmations.py` e `security/permissions.py` (V1)
permanecem intocados. Novos módulos, cada um com uma responsabilidade única:

- `security/models.py` — formas de dado puras (`SecurityFinding`, `Severity`,
  `FindingCategory`, `FindingStatus`, `FileMetadata`, `ScanResultStatus`,
  `NetworkEvidence`, `ProtectionState`, `ScanKind`/`ScanSummary`) — dataclasses
  congeladas, o mesmo padrão de `system_monitor/models.py`.
- `security/events.py` — nomes de evento, publicados no **mesmo** `EventBus`
  compartilhado (nenhum segundo EventBus).
- `security/risk.py` — pontuação de risco explicável: uma `Severity` sempre vem
  acompanhada dos sinais concretos que a produziram, nunca um número sem motivo. Um
  único sinal fraco nunca produz HIGH/CRITICAL sozinho (limite explícito, testado
  diretamente, não apenas uma propriedade emergente dos pesos escolhidos).
- `security/rules.py` — cada sinal ("localização incomum", "extensão dupla
  disfarçada", "processo novo", "sem assinatura", "criado recentemente", "conexão
  externa") é uma função nomeada e testável isoladamente — heurísticas estruturais
  simples, não uma engine de assinaturas de antivírus.
- `security/authenticode.py` — **deliberadamente sempre retorna `None`** (não
  verificado). Uma implementação real via `ctypes`+`WinVerifyTrust` foi tentada e
  testada contra binários reais conhecidos (`notepad.exe`/`cmd.exe`, assinados pela
  Microsoft) — o resultado foi o oposto do correto (reportou "não assinado" para
  binários assinados, "assinado" para um binário aleatório não relacionado). Por regra
  do próprio projeto (nunca apresentar uma fonte comprovadamente não confiável como
  verdadeira), a verificação foi revertida para "não verificado" em vez de arriscar um
  veredito errado. `security/rules.py::unsigned_signal` só dispara em `signed is
  False` explícito, nunca em `None` — logo, esse sinal simplesmente nunca contribui
  nesta versão, sem quebrar nada.
- `security/file_analysis.py` — metadados de arquivo (caminho, existência, tamanho,
  extensão, timestamps, SHA-256) via `pathlib.Path.stat()` + leitura em stream para o
  hash — **nunca executa, abre para interpretar conteúdo, ou carrega como código** o
  arquivo analisado. Arquivos maiores que 200MB não são hasheados nesta versão
  (retorna `UNKNOWN`, nunca um hash de leitura truncada).
- `security/network_analysis.py` — correlação processo↔endereço remoto via
  `psutil.net_connections()` (a mesma informação do `netstat -ano`) — nunca conteúdo
  de pacote, nunca MITM, nunca TLS. Dado genuinamente novo que o System Monitor não
  coletava (que só agrega bytes/s totais, não por processo/conexão) — justificado
  conforme a regra "se precisar de coleta específica para segurança, justificar".
- `security/process_analysis.py` (`ProcessAnalyzer`) — transforma o `ProcessSnapshot`
  que o System Monitor já coleta em achados. O primeiro ciclo estabelece uma baseline
  silenciosa (nada já em execução é "novo"); só processos que aparecem **depois**
  dessa baseline são avaliados — evita um dilúvio de achados para processos normais já
  rodando. Metadados de arquivo são cacheados por caminho (nunca re-hasheados a cada
  ciclo para o mesmo executável).
- `security/scanner.py` (`SecurityScanner`) — Quick Scan (executáveis dos processos em
  execução), Scan File, Scan Folder (limitado a 500 arquivos por scan nesta versão).
  Protegido por um lock (mesmo padrão/justificativa do PATCH 03: no máximo um scan por
  vez). Sempre cancelável (`threading.Event`), nunca bloqueia a GUI — a chamada em si é
  síncrona/bloqueante, e quem a executa em segundo plano é o `BackgroundRunner`
  já existente, reaproveitado, não uma fila nova.
- `security/engine.py` (`SecurityEngine`) — o núcleo. Ciclo de vida deliberadamente
  espelha o modelo endurecido do PATCH 03 (`_lifecycle_lock`, thread daemon, stop
  Event, `thread.join()` com timeout e log se excedido) — os mesmos invariantes
  (nenhuma thread duplicada, nenhuma thread órfã, shutdown limpo) importam aqui pelos
  mesmos motivos, e não foram reimplementados diferente "por ser segurança".

### Diferença deliberada do System Monitor: inicia no boot, não no Dashboard

O PATCH 03 confirmou que `SystemMonitorService` só inicia quando o Dashboard é aberto
pela primeira vez — correto para um coletor passivo de métricas. `SecurityEngine` é
diferente: inicia dentro de `main.build_app_services()`, antes mesmo da janela
principal existir, e só para no shutdown completo do Steve (`SteveApp._shutdown()`/
`main.run_cli()`). "Proteção em tempo real" que só protege enquanto uma janela de
dashboard está aberta não seria proteção em tempo real.

**Problema real encontrado por isso, via validação em hardware, e corrigido**:
`SecurityEngine.start()` roda antes do Dashboard jamais ter sido aberto — e portanto
antes de `SystemMonitorService` ter iniciado (que só inicia quando o Dashboard abre).
Sem tratamento, o primeiro ciclo do `ProcessAnalyzer` estabeleceria a baseline com uma
lista de processos **vazia** (nenhum dado ainda disponível) — e quando o Dashboard
finalmente fosse aberto e dados reais chegassem, **todo processo já em execução**
pareceria "novo", produzindo um dilúvio de falsos positivos. Corrigido: `SecurityEngine.
start()` agora também chama `system_monitor.start()` (idempotente, seguro mesmo se o
Dashboard já o tiver iniciado), garantindo dados reais de processo antes do primeiro
ciclo de análise. Como defesa adicional, `_run()` agora pula a análise inteira (não
apenas trata como "zero processos") enquanto nenhum snapshot real existir.

**Segundo problema relacionado, também corrigido**: a espera entre ciclos de análise
(`analysis_interval_seconds`) antes esperava o intervalo inteiro mesmo que dados novos
chegassem no meio da espera — significando que, se o primeiro ciclo real do System
Monitor demorasse um pouco mais que o esperado, a primeira análise real do Security
Engine só aconteceria depois de um intervalo inteiro extra e desnecessário. Corrigido
com um segundo `threading.Event` (`_new_data_event`), que interrompe a espera assim
que dados novos chegam — sem alterar o comportamento em regime estável (uma análise
extra com "nenhum processo novo" é praticamente gratuita).

**Terceiro problema real, encontrado pela mesma validação em hardware**:
`system_monitor/processes.py::ProcessCollector.sample()` ordenava por uso de CPU e
cortava no top-100 (`limit=100`) — adequado para a aba Processes do Dashboard (que já
tem seu próprio limite de exibição de 60 linhas, então nunca dependia desse corte), mas
catastrófico para monitoramento de segurança: um processo genuinamente ocioso
(exatamente o perfil de um processo furtivo/malicioso bem comportado) nunca aparecia
no snapshot assim que a máquina tinha mais de 100 processos rodando — comprovado ao
vivo (máquina de desenvolvimento real: 245+ processos). Corrigido elevando o limite
padrão para 1000 (não removido — ainda protege contra um cenário patológico de
contagem de processos descontrolada); a aba Processes do Dashboard não foi afetada (seu
próprio limite de exibição já existia independentemente).

### Fluxo de dados (reaproveitado, nunca duplicado)

```
SystemMonitorService (processos)  --EventBus (PROCESS_LIST_UPDATED)-->  SecurityEngine
psutil.net_connections() (rede)   ------------------------------------> SecurityEngine
                                                                              |
                                                                    ProcessAnalyzer
                                                                    + rules.py + risk.py
                                                                              |
                                                                       SecurityFinding
                                                                              |
                                                          EventBus (SECURITY_FINDING_CREATED)
                                                                              |
                                                    Dashboard "Security" tab / tools/security.py
```

Nenhuma segunda enumeração de processos, nenhum segundo EventBus.

### Dashboard: uma quinta aba, não uma segunda aplicação

`ui/desktop/dashboard/security_tab.py` (`SecurityTab`) é a quinta aba da **mesma**
`DashboardWindow` (Overview/Performance/Processes/Security/Steve Core) — nunca uma
janela nova. Segue o mesmo padrão das outras abas: uma view "burra" que só expõe
métodos `apply_*`/`add_*`, chamados pelo `DashboardWindow` (que é quem possui todas as
assinaturas do EventBus, incluindo os novos eventos de segurança) — a única exceção é
disparar um scan, que precisa de `security_engine`/`runner` diretamente, o mesmo motivo
pelo qual a própria `DashboardWindow` já precisa de `system_monitor` para o `start()`
idempotente. Mostra estado de proteção, achados recentes (tabela + painel de detalhes
com as evidências concretas) e os três botões de scan. "Steve Core" (Live Activity)
ganhou linhas estilizadas para os eventos do Security Engine também — a mesma "alerta
leve" da V1.4, não um sistema de popup/modal novo.

### IA: explica achados existentes, nunca inventa um

`tools/security.py` registra três ferramentas somente-leitura (mais uma de escrita
mínima): `check_security_status`, `list_security_findings` (ambas somente leitura) e
`scan_file` (lê e hasheia um arquivo a pedido explícito do usuário — nunca o executa).
O fluxo é sempre evidência real → `SecurityEngine` → `SecurityFinding` → IA explica/
contextualiza usando o tool-calling nativo já existente — nunca o contrário. Nenhuma
ferramenta permite à IA criar, aprovar ou descartar um achado.

### O que NÃO existe nesta versão (por decisão explícita, não esquecimento)

Quarentena automática, exclusão/movimentação automática de arquivo, encerramento/
suspensão automática de processo, desabilitar Defender/Firewall/UAC, execução de
qualquer código baixado da internet, consulta automática de reputação/Security
Intelligence via web, telemetria de qualquer tipo, autoaperfeiçoamento do próprio
Security Engine. Tudo permanece local.

### Limitações conhecidas

- Verificação de assinatura Authenticode: sempre "não verificado" nesta versão (ver
  `security/authenticode.py` acima) — não é uma limitação de escopo, é uma decisão
  deliberada após uma tentativa real ter se mostrado não confiável.
- `SecurityFinding`/`FileScanResult` usam heurísticas estruturais (localização,
  extensão, assinatura, idade, conexão), não um motor de assinaturas de malware —
  "SAFE" significa "nenhum indicador desta versão foi encontrado", nunca "garantido
  livre de malware"; "UNKNOWN" significa "não foi possível determinar com confiança".
- Uso/VRAM de GPU com múltiplos adaptadores (já documentado na seção PATCH 04) segue
  sendo uma soma agregada — sem relação direta com o Security Center, mas relevante se
  uma futura correlação processo↔GPU for adicionada.
- `scan_folder_path` é limitado a 500 arquivos por scan nesta versão (ver
  `security/scanner.py::DEFAULT_MAX_FILES_PER_FOLDER_SCAN`).

## PACK 1.1 — Assistente Virtual Core

Objetivo desta etapa: tornar o ciclo conversacional (texto e voz) já existente mais
coerente como assistente — sem trocar Core, GUI, banco, STT/TTS ou provider de IA, e
sem introduzir framework de agentes, banco vetorial ou um segundo EventBus. Ver
`docs/reports/PACK_1.1_ASSISTENTE_VIRTUAL_CORE_REPORT.md` para a análise completa
(fluxo antes/depois, pesquisa em JARVIS/ORBIT, decisões e validação real).

### Pesquisa externa (REUSE FIRST) — resultado

JARVIS (Electron+React+FastAPI+Ollama) e ORBIT (Python+faster-whisper+Ollama+
CustomTkinter+pyttsx3, arquitetura quase idêntica à do Steve) foram avaliados como
referência. Nenhum dos dois expõe um componente instalável/importável para o escopo
desta etapa — são aplicações completas, não bibliotecas. Nenhum código foi reaproveitado
de nenhum dos dois; a pesquisa serviu para confirmar que os padrões já existentes no
Steve (tool-calling nativo, ferramentas de memória explícitas, ToolManager genérico,
confirmação para ações arriscadas) já equivalem ou superam o que essas referências fazem
para este escopo. Nenhuma dependência nova foi adicionada nesta etapa.

### Resposta vazia do modelo: guarda centralizada no Orchestrator

Antes desta etapa, `voice/service.py::VoiceService` já tratava uma resposta vazia do
modelo antes de falar (`EMPTY_REPLY_MESSAGE`), mas o caminho de texto/GUI
(`ui/desktop/window.py::MainWindow._on_reply`) não tinha proteção equivalente — uma
resposta em branco do modelo virava uma bolha de chat vazia e sem explicação. Corrigido
centralizando a guarda em `core/orchestrator.py::Orchestrator._run_tool_loop` (nova
constante `EMPTY_RESPONSE_MESSAGE`), então os dois pontos de entrada (texto e voz) recebem
uma mensagem real; a guarda própria da voz continua existindo (redundância inofensiva,
não um bug).

### `content` vazio com `thinking` preenchido (Ollama) — proteção preventiva

Alguns modelos servidos pelo Ollama com capacidade de raciocínio (não o `llama3.2`, modelo
padrão do Steve, mas um comportamento real documentado no issue tracker do próprio Ollama
para modelos como o `deepseek-r1`) podem devolver `message.content` vazio e colocar a saída
real em `message.thinking`. `ai/providers/ollama_provider.py::OllamaProvider.chat` agora
cai para `message.thinking` quando `content` vem vazio. Isso não corrige um bug ativo hoje
— é uma proteção para que uma troca futura de modelo não produza respostas em branco
silenciosamente; `EMPTY_RESPONSE_MESSAGE` continua sendo a rede de segurança final mesmo
que os dois campos venham vazios.

### Política de conversa reforçada (`ai/prompts/system_prompt.py`)

- `TOOL_POLICY`: reforçada para deixar explícito que o modelo nunca deve afirmar sucesso
  quando o resultado de uma ferramenta indica falha (`success=false`).
- `MEMORY_POLICY`: passou a orientar o modelo a frasear o `content` salvo de forma completa
  e concreta (ex.: "O editor de código preferido do usuário é o VS Code", não apenas
  "VS Code") — ajuda a busca por sobreposição de palavras (ver limitação abaixo) a
  reconhecer referências indiretas futuras ("abre meu editor") sem tocar no algoritmo.
- `CONVERSATION_POLICY` (nova): concisão em tarefas simples, pedir esclarecimento antes de
  agir quando falta informação essencial e o custo de errar é alto, aceitar suposição
  razoável + confirmação natural para pedidos ambíguos de baixo risco, e continuar o
  raciocínio com o resultado real de uma ferramenta em vez de repetir a pergunta do
  usuário.

Mudança de prompt engineering apenas — zero risco arquitetural.

### Ferramenta de busca na web: Core já suporta, nada implementado agora

Confirmado (não implementado) que uma futura ferramenta `SEARCH_WEB` seria apenas uma nova
subclasse de `tools.base.Tool` registrada em `main.py::register_default_tools` — o
`ToolManager`/`ToolSpec`/ciclo `AIResponse→ToolCall→ToolResult` já são genéricos o
suficiente. Implementação concreta fica para o PACK 1.2, por decisão explícita de escopo.

### Decisão: não restaurar `conversation_history` após reiniciar (nesta etapa)

Investigação encontrou algo mais profundo do que "não está ligado": `SessionManager`
gera um `uuid.uuid4()` novo a cada processo, e `MemoryService.get_recent_history(session_id,
...)` filtra por esse `session_id` exato. Mesmo que o Steve carregasse o histórico recente
no boot, uma sessão nova sempre encontraria zero linhas, porque o `session_id` que ela
usaria para buscar nunca existiu antes. Resolver isso de verdade exige uma decisão
arquitetural real — persistir/reaproveitar o `session_id` entre execuções, ou redefinir
"histórico recente" como uma consulta entre sessões (removendo o filtro por `session_id`)
— nenhuma das duas é um ajuste pequeno, e nenhuma foi pedida para esta etapa. Por isso,
nesta etapa o comportamento permanece o mesmo de antes: `conversation_history` continua
sendo gravado no SQLite (nunca foi removido), mas uma nova execução do Steve começa com
`SessionManager.turns` vazio, como sempre foi. Memória permanente (`MemoryService`/
`remember_fact`/`recall_memories`) não é afetada por isso — é o mecanismo correto e já
funcional para o que precisa sobreviver a um reinício.

### Memória permanente vs. histórico de conversa — separação (inalterada, agora documentada)

| Camada | Onde vive | Sobrevive a reinício? | Quando é escrita |
|---|---|---|---|
| Memória permanente | `memories` (SQLite), via `MemoryService` | Sim | `remember_fact` (explícito ou fato duradouro relevante) |
| Histórico de conversa | `SessionManager.turns` (em memória) + `conversation_history` (SQLite, auditoria) | Não (turns) / Sim mas não relido (tabela) | Todo turno, via `Orchestrator._remember_turn` |

Nem toda frase do usuário deve virar memória permanente — `MEMORY_POLICY` já existia para
isso e não foi enfraquecida nesta etapa.

### Limitação conhecida, deliberadamente não tocada nesta etapa

`ContextManager.get_relevant_memories` pontua relevância por sobreposição de palavras
(regex `\w+` + interseção de conjuntos). Estruturalmente incapaz de ligar "meu editor" a
uma memória cujo conteúdo não contenha literalmente a palavra "editor". Mitigado nesta
etapa apenas via `MEMORY_POLICY` (fraseio rico no momento de salvar, ver acima) — o
algoritmo em si fica para o PACK 1.3 (Memória & Contexto Inteligente), por decisão
explícita do roteiro.

### Validação real desta etapa

Executada com Ollama real (`llama3.2`) e SQLite real, banco isolado, sem GUI — 17
verificações cobrindo os 11 cenários pedidos (conversa simples, conversa com contexto,
memória existente, `remember_fact` novo, `recall_memories`, uso de ferramenta real
(`check_cpu`) com valor numérico real na resposta final, falha de ferramenta comunicada
sem afirmar sucesso, Ollama indisponível retornando a mensagem correta, STT/TTS reais
inicializando, e confirmação de que uma sessão nova começa vazia mesmo com histórico
persistido). Todas passaram. Suíte de regressão completa (`pytest tests/`) também
executada após as mudanças — ver relatório para a contagem final.

## Pacote de melhorias externo — voz neural, streaming por frase, tema Cyber

Origem: um pacote de arquivos prontos gerado externamente (Grok), aplicado de forma
cirúrgica sobre a arquitetura existente — nenhum arquivo foi copiado sem antes ser
comparado com o equivalente real do Steve. Ver
`docs/reports/GROK_IMPROVEMENTS_INTEGRATION_REPORT.md` para a auditoria completa
(riscos, testes, nota final).

### Voz neural (EdgeTTS) — camada acima do pyttsx3, nunca uma substituição

`voice/tts.py` ganhou `EdgeTTS` (vozes neurais da Microsoft, ex. `pt-BR-AntonioNeural`)
e uma factory `create_tts(preferred="auto"|"edge"|"pyttsx3", ...)`. `Settings.tts_engine`
passou a valer "auto" por padrão — tenta EdgeTTS primeiro, cai para `Pyttsx3TTS`
automaticamente se o pacote `edge-tts`/backend de áudio (`pygame`) não estiverem
disponíveis (sem internet, sem instalar, etc.). `Pyttsx3TTS`/`NullTTS` continuam
exatamente como antes — nenhuma regressão no fallback offline original.

**Incompatibilidade real encontrada e corrigida**: `ui/desktop/orb/reactive_tts.py`
(`OrbReactiveTTS`, usado para o orb pulsar com a própria voz do Steve) assumia que
`TextToSpeech.speak_to_file()` sempre produz um WAV real tocável por `winsound` — verdade
para `Pyttsx3TTS`/SAPI5, mas `EdgeTTS.speak_to_file()` sempre grava MP3 (é tudo que o
serviço da Microsoft devolve), e `winsound.PlaySound` não toca MP3. Sem correção, isso
faria `speak()` levantar uma exceção não tratada em todo turno de voz reativo ao orb, uma
vez que EdgeTTS estivesse ativo — nunca testado no pacote original, porque
`OrbReactiveTTS` é específico do Steve. Corrigido tratando "arquivo não é WAV de verdade"
como equivalente a "síntese falhou": cai para `inner.speak()` (que sabe tocar o próprio
formato) em vez de propagar a exceção — o orb só perde a reatividade de amplitude nesse
caso, continua falando normalmente. Coberto por
`tests/test_orb_reactive_tts.py::test_speak_falls_back_to_plain_speak_when_inner_writes_a_non_wav_file`.

### Streaming por frase — implementado com o ciclo de ferramentas intacto

O guia do pacote (`patches/VOICE_SERVICE_STREAMING.md`) propunha um
`handle_message_stream` que chamava `ollama.chat(..., stream=True)` diretamente,
ignorando por completo `AIRouter`/`ToolManager`/`PermissionManager`/`ConfirmationService`/
`AuditLogger`/`_remember_turn`/EventBus — ou seja, nenhuma ferramenta seria executada
durante um turno de voz com streaming, e nada seria auditado ou lembrado. Isso violaria a
regra permanente do projeto de nunca abrir um caminho de execução de ferramenta sem
passar pelo `ToolManager`/`PermissionManager`. **Não foi implementado como sugerido.**

Implementação real (cirúrgica, aditiva — `handle_message` e `_run_tool_loop` não foram
tocados):

- `ai/base.py::StreamChunk` — um pedaço incremental de resposta (`content`, `tool_calls`,
  `done`).
- `ai/providers/ollama_provider.py::OllamaProvider.chat_stream()` — streaming real via
  `POST /api/chat` com `"stream": true`, parseando o NDJSON linha a linha.
- `ai/router.py::AIRouter.chat_stream()` — mesmo `select()`, sem fallback entre
  providers no meio de um stream (diferente de `chat()`) — retomar um stream já iniciado
  em outro provider é um problema mais complexo, e hoje só existe um provider registrado.
- `core/orchestrator.py::Orchestrator.handle_message_stream()` — mesmo loop de
  `_run_tool_loop`, mas a rodada final é buscada via `chat_stream()` em vez de uma
  chamada bloqueante; toda rodada com `tool_calls` continua passando por
  `_execute_tool_call()` sem nenhuma alteração (mesma permissão, confirmação, auditoria).
- `voice/service.py::VoiceService.listen_and_respond_streaming()` — mesmos passos de
  captura/STT de `listen_and_respond()`, mas fala com `voice/streaming_speaker.py`
  (`StreamingSpeaker` + `SentenceBuffer`, ambos do pacote, copiados como estão — nenhuma
  lógica de tool-calling neles, então nada a adaptar).

**Trade-off de corretude, documentado e deixado desligado por padrão**: falar um trecho
de `content` assim que ele chega, em vez de esperar o texto completo, só é seguro se um
turno nunca misturar prosa falada com uma decisão de chamar ferramenta — a mesma suposição
que `_run_tool_loop` (não-streaming) já faz ao descartar `response.content` numa rodada
com `tool_calls`. É o comportamento normal de tool-calling no Ollama/OpenAI, mas não há
garantia formal por modelo. Por isso `Settings.voice_streaming_enabled` nasce `False` —
ativável em Configurações ("Falar frase a frase (streaming, experimental)") depois que o
usuário testar alguns turnos de voz que acionem ferramentas e confirmar que nada estranho
é falado antes do resultado real.

### Otimização real de latência: `keep_alive` por request, não variável de ambiente

O pacote recomendava `OLLAMA_KEEP_ALIVE=24h` como variável de ambiente do processo do
Ollama (exige reiniciar o serviço). Implementado de forma melhor: `OllamaProvider` agora
envia `"keep_alive": "24h"` (configurável) em todo request `/api/chat` — Ollama já suporta
esse campo por chamada, então o modelo permanece carregado sem exigir reiniciar nada. O
`OLLAMA_FLASH_ATTENTION=1` continua sendo só variável de ambiente do servidor (não dá pra
setar por request) — documentado, com um `start_steve_optimized.bat` opcional na raiz do
projeto para quem quiser aplicá-lo manualmente.

### Tema Cyber/Thomas

`ui/desktop/styles.py` ganhou `DARK_CYBER` (preto profundo + cyan neon) e
`palette_for("cyber"|"thomas")`; `ui/desktop/orb/animation.py` teve só as cores de
`STATE_PARAMS` trocadas para combinar — a matemática de animação (dt clamp contra
stalls/GC, blend contínuo ao interromper uma transição) não foi tocada, porque já era mais
robusta que a versão do pacote (que removia essas duas proteções). Selecionável em
Configurações → Tema → "Cyber"; `data/config.json` desta máquina já foi atualizado com
`"theme": "cyber"`.

## Pack 2 — Tool Filler + Smart Recall

Origem: segundo pacote externo (Grok), aplicado sobre o que o Pack 1 já deixou pronto.
Ver `docs/reports/PACK2_TOOL_FILLER_SMART_RECALL_REPORT.md` para a auditoria completa.

### Tool Filler — acionado por evento, não por um `with` em volta da tool

`voice/tool_filler.py` (copiado como veio — `ToolFiller`/`_FillerContext`, sem
alterações) fala uma frase curta ("Só um segundo...") se uma ferramenta ainda estiver
rodando depois de `delay` segundos (0.35s por padrão).

O guia do pacote sugeria `with self.tool_filler.during_tool(name): result =
tool_manager.run(...)` dentro do Orchestrator — mas o Orchestrator nunca teve (e não
devia ganhar) uma referência a TTS; fala só existe no lado do `VoiceService`. Em vez de
furar essa fronteira, `voice/service.py::VoiceService._filler_active()` assina os
eventos que o Orchestrator já publica no EventBus — `TOOL_STARTED`/`TOOL_COMPLETED`
(`core/orchestrator.py`) — e conduz o mesmo `_FillerContext` a partir deles:
`TOOL_STARTED` chama `__enter__()` (inicia a thread do delay), `TOOL_COMPLETED` chama
`__exit__()` (para a thread). A assinatura dura só o turno atual (subscribe no início,
unsubscribe num `finally`), então nunca vaza para um turno de texto ou um turno de voz
seguinte.

**Por que não usar o `on_tool_call` já existente**: `on_tool_call` dispara uma vez,
logo antes da ferramenta rodar, sem nenhum sinal correspondente de "terminou" — não dá
para saber quando parar o filler a partir dele sozinho. E envolver a CHAMADA INTEIRA ao
Orchestrator (em vez de só a ferramenta) teria disparado o filler em toda resposta
puramente conversacional também, já que gerar uma resposta do modelo rotineiramente
leva mais que 0.35s mesmo sem nenhuma ferramenta envolvida — o `TOOL_STARTED`/
`TOOL_COMPLETED` é o único par de sinais que realmente delimita "uma ferramenta está
rodando agora." Coberto por
`tests/test_voice_service.py::test_filler_stays_silent_when_no_tool_is_called`.

**Adaptação de estabilidade**: o guia constrói o filler com `ToolFiller(tts=self.tts)`
— um `self.tts` capturado na construção. Isso ficaria desatualizado depois que
`ui/desktop/app.py::_wrap_tts_for_orb` troca `voice_service.tts` por um
`OrbReactiveTTS` (acontece DEPOIS que `VoiceService` é construído). Corrigido usando
`speak_fn=lambda text: self.tts.speak(text)`, que sempre lê `self.tts` no momento da
fala, não da construção — mesmo padrão já usado para `StreamingSpeaker` no Pack 1.
Coberto por
`tests/test_voice_service.py::test_filler_uses_the_live_tts_not_a_stale_reference_from_construction`.

**Risco conhecido, não eliminado**: `_FillerContext.__exit__` só espera a thread do
filler por até 1 segundo (`join(timeout=1.0)`, do próprio arquivo do pacote, não
alterado). Se o filler ainda estiver no meio da fala exatamente quando o resultado real
da ferramenta chega, a fala do filler e a resposta final podem se sobrepor por um
instante — não trava nem quebra nada, só um possível corte/sobreposição de áudio. Mais
provável de aparecer combinado com o streaming do Pack 1 (`voice_streaming_enabled`) —
os dois recursos são opt-in-adjacent (filler é automático quando há voz, streaming é
opt-in), então testar as duas coisas juntas é uma recomendação explícita da auditoria.

### Smart Recall — substitui só o algoritmo de pontuação, não a interface

`memory/smart_recall.py` (copiado como veio) troca a pontuação por overlap de palavras
por uma pontuação ponderada: `0.55×overlap_de_termos + 0.25×importância +
0.20×recência`. Isso é exatamente o algoritmo que a auditoria da PACK 1.1 já tinha
identificado como limitação conhecida e adiado para o (ainda não iniciado) PACK 1.3 —
o Pack 2 antecipa essa melhoria específica, por pedido explícito desta etapa.

Integrado dentro de `memory/service.py::MemoryService.get_relevant_memories()` — só o
branch "com query" foi trocado; o branch sem query (usado por
`ContextManager.build()` quando não há `user_text`, e testado explicitamente em
`test_build_does_not_dump_every_memory_into_the_prompt`/
`test_build_excludes_temporary_memories_after_expiry`) continua exatamente como
estava. Nenhuma interface mudou — `get_relevant_memories`/`summarize_for_prompt`/
`ContextManager.build` continuam devolvendo os mesmos tipos de sempre
(`list[MemoryRecord]`/`str`), então nada rio abaixo precisou mudar. `_tokenize` (a
função antiga de overlap) e o `import re` que só ela usava foram removidos por ficarem
mortos, não por decisão estética.

**Adapter `_to_memory_item` — de `MemoryRecord` (schema real) para `MemoryItem`**:
`MemoryRecord` não tem `tags` nem `importance` float — `important` é `bool`. Mapeado
com cuidado (ver docstring de `_to_memory_item` em `memory/service.py`): `tags` reaproveita
`type`/`category` (palavras de classificação que já existem, não inventadas);
`importance` usa `updated_at` para recência (não `created_at` — mesmo precedente que o
fallback sem query já usava) e mapeia `important=True→1.0`, `False→0.3`.

**Achado real, não assumido — "Smart Recall realmente filtra?"**: a primeira tentativa
mapeou `False→0.5` (o próprio default do dataclass do pacote). Fazendo a conta:
`0.25×0.5 = 0.125`, e `min_score` do `SmartRecall` é `0.12` — ou seja, *toda* memória
não marcada como importante já bateria o piso sozinha, não importa a recência ou o
overlap com a pergunta, e o filtro de exclusão nunca excluiria nada de verdade — só
re-rankearia. Corrigido para `0.3` (`0.25×0.3 = 0.075 < 0.12`), o que faz a exclusão
funcionar de verdade para memórias antigas e sem nenhuma relação com a pergunta atual,
sem prejudicar memórias recém-criadas (a recência sozinha ainda mantém uma memória
nova na lista mesmo com zero overlap). Validado com um teste real que engana o relógio
(`UPDATE memories SET updated_at = ...` direto no banco) —
`tests/test_memory.py::test_get_relevant_memories_excludes_an_old_unimportant_unrelated_memory`
e o teste irmão que confirma que uma memória nova NÃO é excluída
(`test_get_relevant_memories_keeps_a_fresh_unrelated_memory_despite_no_overlap`).


## SystemMonitor + Security (atualizado 2026-09-19)

`SecurityEngine.start()` (boot) chama `system_monitor.start()` em **light mode**:
- intervalos ~4–5s (não 1s)
- **sem** amostragem de GPU
- ainda coleta processos (evita falso positivo no ProcessAnalyzer)

Ao abrir o Dashboard, `set_light_mode(False)` restaura polls ~1s/2s com GPU.
A frase antiga “monitor só quando o Dashboard abre” ficou obsoleta: o monitor
sobe com o Security, só que leve até o Dashboard pedir qualidade total.
