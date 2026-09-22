# STEVE — FULL SYSTEM ARCHITECTURE & REUSE AUDIT

**Modo**: Auditoria + Pesquisa + Proposta. Nenhum código foi modificado, nenhuma
dependência foi instalada/atualizada, nenhuma implementação foi executada durante esta
sessão. Todos os achados sobre o código do Steve vêm de leitura direta e verificação
via grep (não presumidos); toda recomendação externa vem de pesquisa real na internet
(fontes listadas na seção 22).

---

## 1. Executive Summary

O Steve (V1.0→V1.5, 505 testes) tem uma arquitetura **fundamentalmente sólida**: uma
única camada de Core, um único `EventBus`, um único modelo de lifecycle replicado
consistentemente entre `SystemMonitorService` e `SecurityEngine`, abstrações de
provider/STT/TTS corretas, e nenhuma violação de camada encontrada em nenhum dos 90
arquivos de código de produção. **Veredito geral: KEEP a arquitetura atual como base.**
Nenhuma migração de framework (GUI, agente de IA, banco de dados, memória vetorial) se
justifica com a evidência disponível hoje.

As oportunidades reais dividem-se em três grupos:

1. **Consertar conexões quebradas já existentes** (achados da auditoria arquitetural
   anterior desta sessão: `permission_auto_approve_level` nunca lido, GUI sem diálogo
   de confirmação real, `core/errors.py` morto, histórico de conversa não sobrevive a
   restart, `ToolManager`/CLI sem rede de segurança contra exceção de ferramenta) —
   **P1, baixo esforço, sem necessidade de dependência nova**.
2. **Reuso pontual e bem justificado** de bibliotecas maduras e permissivas para
   preencher lacunas reais: Silero VAD (detecção de fala melhor que RMS simples),
   openWakeWord (wake word "Steve..." nunca implementado), YARA (heurística estrutural
   do Security Center ganha detecção por assinatura real), pywinauto (automação
   Windows além de abrir app/URL), pluggy (arquitetura de Skills futura), APScheduler
   (Planner futuro) — **nenhum deles substitui algo que já funciona; todos preenchem
   uma lacuna hoje vazia ou claramente listada como "futuro"**.
3. **Uma dependência não declarada real** (`pywin32`, usado extensivamente mas ausente
   de `requirements.txt`) — achado concreto de auditoria de dependências, não de
   pesquisa externa.

Nenhum componente do Steve foi classificado como REPLACE nesta auditoria. Um caso
(TTS: pyttsx3 → Piper) foi avaliado e **descartado por ora** por causa de uma mudança
de licença real descoberta durante a pesquisa (ver seção 8).

## 2. Current System

Confirmado por inventário direto (não presumido do conhecimento anterior — árvore
recontada nesta sessão): **90 arquivos `.py` de produção**, **505 testes** coletados
(`pytest --collect-only -q`), 8 versões incrementais concluídas (V1.0 → V1.5), git
inicializado no diretório do projeto porém sem nenhum commit ainda (`git log` retorna
"does not have any commits yet" — o histórico de versões existe apenas nos relatórios
em `docs/reports/`, não em commits).

Dependências declaradas (`requirements.txt`): `requests`, `psutil`, `pyttsx3`,
`pytest`, `sounddevice`, `faster-whisper`, `numpy`, `customtkinter`, `Pillow`. Ver
seção 16 para o que está instalado mas não declarado.

## 3. Architecture Map

O fluxo proposto pelo usuário foi **confirmado no código, com uma correção real**: o
Security Center e o System Monitor não penduram só do EventBus como consumidores
passivos — ambos são **serviços de longa duração com lifecycle próprio**, publicando
no EventBus, não apenas roteados por ele.

```
USER
 ↓
ui/cli.py  OU  ui/desktop/window.py + voice/service.py (mesmo Orchestrator, entradas diferentes)
 ↓
ui/desktop/controller.py (ChatController — só marshaling de thread + tradução p/ OrbState)
 ↓
core/orchestrator.py (Orchestrator — entende → planeja → permissão → executa → verifica → responde)
 ↓                                    ↓
ai/router.py (AIRouter)      core/tool_manager.py (ToolManager)
 ↓                                    ↓
ai/providers/ollama_provider.py    tools/*.py (15 ferramentas, todas LOW)
 ↓                                    ↓
Ollama (HTTP local)          security/permissions.py + security/confirmations.py
 ↓
core/context.py + core/session.py + memory/service.py (histórico/memória → prompt)
 ↓
memory/database.py (SQLite)

core/events.py (EventBus, ÚNICO) ←→ core/lifecycle.py, core/orchestrator.py,
    voice/service.py, system_monitor/service.py (thread própria),
    security/engine.py (thread própria) + security/scanner.py
 ↓
ui/desktop/dashboard/window.py (Overview/Performance/Processes/Security/Steve Core)
```

Nenhum "God object" encontrado — `Orchestrator` é o componente mais central, mas
delega tudo (seleção de IA ao Router, execução ao ToolManager, permissão ao
PermissionManager, persistência ao MemoryService).

## 4. Component Inventory

| Área | Módulos | Classes/serviços principais |
|---|---|---|
| Core | `core/` (9 arquivos) | `Orchestrator`, `EventBus`, `LifecycleManager`, `ContextManager`, `SessionManager`, `ToolManager` |
| AI | `ai/` (5 arquivos) | `AIProvider` (ABC), `AIRouter`, `OllamaProvider` |
| Memory | `memory/` (2 arquivos) | `MemoryService`, `Database` |
| Tools | `tools/` (7 arquivos, 15 ferramentas) | `Tool` (ABC), todas `PermissionLevel.LOW` |
| Security | `security/` (13 arquivos) | `PermissionManager`, `AuditLogger`, `SecurityEngine`, `SecurityScanner`, `ProcessAnalyzer` |
| System Monitor | `system_monitor/` (6 arquivos) | `SystemMonitorService`, `GpuMonitor`, `ProcessCollector` |
| Voice | `voice/` (5 arquivos) | `VoiceService`, `FasterWhisperSTT`, `Pyttsx3TTS`, `AudioCapture`, `WakeWordDetector` (não implementado) |
| Settings | `settings/` (3 arquivos) | `ConfigManager`, `Settings`, `autostart`, `SingleInstanceLock` |
| UI Desktop | `ui/desktop/` (18 arquivos) | `SteveApp`, `MainWindow`, `ChatController`, `DashboardWindow`, `Orb` |
| Testes | `tests/` (43 arquivos) | 505 testes coletados |

## 5. Architecture Health

**Confirmado sólido**: nenhuma dependência circular; `core/` nunca importa `ui/`;
`tools/`/`security/`/`system_monitor/` nunca importam `tkinter`; um único `EventBus`;
um único modelo de threading/lifecycle para os dois serviços de longa duração.

**Gaps reais confirmados** (já documentados em detalhe no relatório
`ARCHITECTURE_AUDIT_POST_V1.5.md` desta mesma sessão — resumidos aqui, não
reexplicados por completo):

1. `settings.permission_auto_approve_level` é persistido mas nunca lido —
   `PermissionManager` é sempre construído com `LOW` hardcoded em `main.py`.
2. GUI não tem nenhum diálogo de confirmação real (`AutoDenyConfirmationService` nega
   tudo silenciosamente) — inofensivo hoje porque todas as 15 ferramentas são LOW.
3. `core/errors.py` define 3 exceções nunca usadas em lugar nenhum do projeto.
4. `conversation_history` é gravada a cada turno mas nunca lida de volta —
   `SessionManager` começa vazio a cada processo; a conversa não sobrevive a um
   restart (memória permanente funciona normalmente, é um mecanismo separado).
5. `ToolManager.execute()` não tem `try/except` ao redor de `tool.execute()` — uma
   ferramenta que lance exceção propaga sem rede de segurança nesta camada.
6. `ui/cli.py::run_chat_loop()` não tem a mesma proteção que a GUI
   (`BackgroundRunner.run()`) contra uma exceção de ferramenta — no CLI, isso
   derrubaria o processo inteiro.

## 6. AI Architecture

`ai/base.py` (contrato `AIProvider`/`ToolCall`/`ToolSpec`), `ai/router.py`
(`AIRouter`, fallback por capacidade), `ai/providers/ollama_provider.py`
(implementação concreta), `core/orchestrator.py` (loop entender→tool→responder).
**Veredito: KEEP.** A abstração já separa corretamente "o que o Core precisa" de "qual
provider serve" — adicionar um segundo provider (ex.: Claude via API) significa
registrar em `main.py::build_ai_router()`, sem tocar `Orchestrator`/`ContextManager`.

**Pesquisa: frameworks de agente (LangChain/AutoGen/CrewAI)** — comparados
explicitamente para o caso de uso do Steve (uma IA, tool-calling nativo simples, sem
múltiplos agentes conversando entre si). Conclusão da pesquisa: "se você só precisa de
um agente único usando algumas ferramentas, o modelo multi-agente do AutoGen é
overkill"; CrewAI/LangGraph existem para orquestração multi-agente com papéis — não é
o problema que o Steve tem hoje. **Veredito: BUILD (manter o Orchestrator próprio).**
Nenhum desses frameworks resolve algo que o tool-calling nativo do Ollama +
`ToolManager` já não resolvam, e todos adicionariam uma dependência pesada e um
modelo mental novo para um ganho não demonstrado.

## 7. Memory

`memory/service.py::MemoryService` — SQLite + relevância por sobreposição de palavras
(`get_relevant_memories`), categorias IDENTITY/PERMANENT/TEMPORARY, chave para
atualização em vez de duplicação. **Veredito: KEEP.**

**Pesquisa: bancos vetoriais locais (sqlite-vec, ChromaDB, LanceDB)** — todos maduros,
embutidos, sem servidor. Mas o Steve hoje tem, tipicamente, dezenas a centenas de
memórias por usuário, não milhares — a pesquisa mostra que a vantagem de um vetor
semântico sobre sobreposição de palavras aparece em volumes muito maiores ou quando a
frase de busca é semanticamente distante do texto salvo (sinônimos). **Não há
evidência hoje de que a relevância por palavra-chave esteja falhando.** Recomendação:
não adicionar banco vetorial agora; se o volume de memórias crescer muito ou usuários
reportarem que o Steve "não lembra" de algo relacionado mas com palavras diferentes,
`sqlite-vec` (extensão de UM arquivo, sem processo separado, licença MIT/Apache-2.0,
zero mudança de infraestrutura — continua sendo o mesmo arquivo SQLite) é o candidato
natural, não ChromaDB/LanceDB (que trazem um modelo de storage próprio).

**Achado da auditoria de código (não de pesquisa)**: ver item 4 da seção 5 —
`conversation_history` não é lida de volta. Isso é uma lacuna de CONEXÃO, não de
tecnologia — não precisa de nenhuma biblioteca nova para corrigir.

## 8. Voice

### STT — `voice/stt.py::FasterWhisperSTT`
**Veredito: KEEP.** Pesquisa confirma faster-whisper (MIT) continua a escolha correta:
leve (~80MB de instalação, sem PyTorch), boa precisão em pt-BR, já testado
extensivamente em produção real no Steve desde V1.1.

### TTS — `voice/tts.py::Pyttsx3TTS`
**Veredito: IMPROVE (não replace ainda).** Pesquisa confirma Piper produz voz
sensivelmente mais natural que pyttsx3/SAPI5 (que soa robótico). **Achado crítico da
pesquisa**: o repositório MIT original do Piper (`rhasspy/piper`) está arquivado
(somente leitura) desde outubro de 2025; o fork ativamente mantido hoje é **GPL-3.0**.
Isso muda a recomendação — GPL-3.0 é copyleft forte; importar o código Piper como
biblioteca Python dentro do processo do Steve exigiria avaliação jurídica cuidadosa
(potencialmente forçando o próprio Steve a ser GPL-3.0 se distribuído). **Mitigação
real**: rodar o binário/CLI do Piper como um **subprocesso separado** (o padrão comum
de uso do Piper) é geralmente entendido como não criando obra derivada exigindo
GPL — mas isso não é aconselhamento jurídico, é uma leitura técnica comum da FSF sobre
"agregação" vs "vinculação". Recomendação: **avaliar como um adicional opcional
futuro** (voz de melhor qualidade via subprocesso, nunca via `import piper`), não uma
substituição do pyttsx3 nesta auditoria — mantendo pyttsx3 como padrão sempre
disponível e sem questão de licença.

### VAD — `voice/audio.py::AudioCapture.record_until_silence` (RMS simples)
**Veredito: REUSE (recomendado).** O endpointing atual é um limiar de energia
(RMS) fixo — funciona, mas é sensível a ruído de fundo (documentado no próprio
código: "the default works reasonably... in a quiet-ish room"). Pesquisa: **Silero
VAD** — modelo de 1.8MB, MIT, processa 30ms de áudio em ~1ms numa CPU, "significativamente
mais preciso que VAD por energia (WebRTC VAD) em ambientes ruidosos" por usar uma rede
neural pequena em vez de um limiar simples. Reuso concreto: substituir só a lógica de
detecção de fim-de-fala dentro de `AudioCapture.record_until_silence` — nenhuma outra
parte do pipeline de voz muda. Baixo risco, dependência leve, licença permissiva.

### Wake Word — `voice/wakeword.py` (abstração pronta, nunca implementada)
**Veredito: REUSE quando a funcionalidade for de fato priorizada.** Pesquisa:
**openWakeWord** (Apache-2.0, ativamente mantido) vs **Porcupine** (comercial, US$
6mil+/ano acima de limites gratuitos, precisa de "AccessKey"). openWakeWord roda bem
em CPU (Raspberry Pi 3 roda 15-20 modelos simultâneos em tempo real) e não tem custo
de licença recorrente — ajuste direto para "local-first" do Steve. `voice/wakeword.py`
já tem a abstração (`WakeWordDetector`) — a implementação concreta usaria openWakeWord
sem mudar o contrato. **Não implementar agora** (fora do escopo desta auditoria e não
solicitado), apenas confirmar a escolha para quando a etapa "V1.2: Wake word" for
autorizada.

## 9. GUI

`ui/desktop/` — CustomTkinter. **Veredito: KEEP.** Pesquisa comparativa (2026):
CustomTkinter tem o menor tempo de inicialização (0.5-1s) e menor uso de memória
(50-100MB) dos três candidatos avaliados; PySide6/Qt tem widgets nativos e um
ecossistema mais maduro mas inicialização 1.5-3s, mais memória (100-200MB), e "lição
de casa de conformidade LGPL"; Flet é o mais jovem, com histórico de quebra de
compatibilidade entre versões menores. **Nenhuma evidência concreta no Steve hoje
(nenhum problema de performance, nenhuma limitação de widget relatada) justifica pagar
o custo de uma migração de GUI.** A limitação já documentada (troca de tema exige
reiniciar) é cosmética, não arquitetural, e não é resolvida trocando de framework sem
reescrever a camada de renderização de qualquer forma.

## 10. Tools

15 ferramentas, todas `PermissionLevel.LOW`, cada uma um arquivo pequeno e
independente. **Veredito: KEEP a arquitetura; IMPROVE a rede de segurança** (ver
achado 5 da seção 5 — `ToolManager.execute()`).

**Pesquisa: automação desktop Windows.** `os.startfile`/`webbrowser.open` cobrem o
escopo atual (abrir app/URL/arquivo). Para automação mais rica no futuro (controlar
janelas, clicar em controles de outra aplicação — não solicitado nesta versão),
**pywinauto** (BSD, já bem estabelecido, usa Win32 API + MS UI Automation, "mais
robusto que PyAutoGUI por usar seletores baseados em acessibilidade") é o candidato
natural — mas isso é uma capacidade nova, não uma substituição de nada que exista.

## 11. Security Center

Auditado em profundidade no relatório `V1.5_SECURITY_CENTER_REPORT.md`. Aqui, o foco é
comparação com soluções externas:

- **YARA** — **REUSE recomendado, o achado mais concreto desta fase de pesquisa.** O
  Security Center hoje (`security/rules.py`) usa só heurísticas estruturais
  (localização, extensão dupla, idade, assinatura — nunca verificada, ver relatório
  V1.5). YARA adiciona o que falta: **detecção por assinatura/padrão binário real**,
  com um ecossistema enorme de regras públicas mantidas (`Yara-Rules/rules` no
  GitHub). `yara-python` é a integração oficial. Ponto de entrada natural:
  `security/file_analysis.py::scan_file()` ganharia um novo sinal
  (`yara_match_signal`) ao lado dos já existentes — sem mudar a filosofia "nunca
  executa o arquivo" (YARA escaneia bytes estáticos, nunca roda o binário). Licença:
  YARA em si é BSD-3-Clause-like (permissiva); regras de terceiros variam por
  repositório e precisam ser avaliadas uma a uma antes de empacotar qualquer conjunto
  específico.
- **Sysmon + Sigma** — **REFERENCE ONLY, não recomendado para reuso direto agora.**
  Sysmon é um serviço do Windows que precisa ser instalado separadamente e escreve no
  Event Log; Sigma é um formato de regra que várias ferramentas convertem para a
  consulta do backend de log escolhido. Isso é um modelo operacional mais pesado
  (instalar um serviço do sistema, um pipeline de ingestão de log) do que o Security
  Center embutido e leve do Steve hoje. Vale estudar o **formato/vocabulário** das
  regras Sigma como referência de "quais sinais importam", sem adotar a stack inteira.
- **ClamAV** — **NOT RECOMMENDED para integração direta nesta versão.** Pesquisa
  confirma: o escaneamento em tempo real (`clamonacc`) precisa do daemon `clamd`
  rodando como processo separado; o suporte a proteção em tempo real no Windows é
  fraco/desatualizado (ex.: "Clam Sentinel", a última opção encontrada, não é
  atualizado desde 2014). Licença GPL-2.0 (chamar um `clamd` externo via socket não
  vincula o código do Steve, mas empacotar o próprio ClamAV junto exigiria avaliação).
  Contradiz o modelo leve/embutido atual. Poderia, no futuro, ser um **conector
  opcional** para quem já roda um `clamd` local — não uma dependência do Security
  Center em si.

## 12. System Monitor

`psutil` + WMI (`Win32_VideoController`) + Performance Counters (PDH) — já
cross-vendor (AMD/Intel/NVIDIA) e endurecido no PATCH 04 (temperatura de CPU sempre
N/A por fonte comprovadamente não confiável, uso de CPU/GPU nunca fabrica 0% na
primeira amostra). **Veredito: KEEP.** Nenhuma alternativa pesquisada (DXGI direto,
outras libs de hardware) resolveria algo que o PATCH 04 já não tenha corrigido com a
mesma fonte (psutil/WMI/PDH); trocar a fonte de dados descartaria todo o trabalho de
correção já feito e testado.

## 13. Event System

`core/events.py::EventBus` — ~24 linhas, publish/subscribe síncrono, sem
dependência. **Veredito: KEEP.** Pesquisa: `blinker` e `pypubsub` são as opções mais
citadas — ambas maduras, mas resolvem problemas que o Steve não tem (roteamento por
namespace/wildcard, pub/sub assíncrono complexo). O EventBus atual já é usado
corretamente por 5 serviços diferentes (Lifecycle, Orchestrator, Voice, System
Monitor, Security) sem nenhum problema de escala reportado. Trocar por uma biblioteca
externa adicionaria uma dependência para resolver um problema inexistente — viola
diretamente a regra "não trocar tecnologia madura sem benefício mensurável".

## 14. Lifecycle

`core/lifecycle.py::LifecycleManager` + o padrão replicado em
`SystemMonitorService`/`SecurityEngine` (`_lifecycle_lock`, thread daemon, `_stop_event`,
`thread.join()` com timeout, log se excedido). **Veredito: KEEP.** Este é o padrão
mais maduro e testado do projeto (endurecido especificamente no PATCH 03, com testes
white-box determinísticos comprovando a proteção contra corrida). Não há framework de
lifecycle externo que se aplique melhor a "dois serviços com thread própria dentro de
um processo desktop único" do que o que já existe.

## 15. Database

SQLite via `sqlite3` da stdlib. **Veredito: KEEP.** Pesquisa não encontrou justificativa
para SQLAlchemy/SQLModel (o schema é pequeno e estável, `memory/database.py` já
centraliza todo o SQL num único lugar — exatamente o que um ORM tentaria oferecer,
sem a dependência) nem para DuckDB (analítico, não é o padrão de acesso do Steve) nem
para um vector store dedicado (ver seção 7).

## 16. Dependencies

| Pacote | Declarado em requirements.txt? | Instalado? | Uso confirmado |
|---|---|---|---|
| requests | Sim | Sim | `ai/providers/ollama_provider.py` |
| psutil | Sim | Sim | `system_monitor/`, `security/`, `tools/system.py` |
| pyttsx3 | Sim | Sim | `voice/tts.py` |
| pytest | Sim | Sim | suíte de testes |
| sounddevice | Sim | Sim | `voice/audio.py` |
| faster-whisper | Sim | Sim | `voice/stt.py` (traz `ctranslate2`, `tokenizers`, `huggingface_hub` como transitivas) |
| numpy | Sim | Sim | `voice/audio.py` |
| customtkinter | Sim | Sim | toda `ui/desktop/` |
| Pillow | Sim | Sim | (ícones/imagens da GUI) |
| **pywin32** | **Não** | **Sim (v312)** | `settings/single_instance.py`, `system_monitor/gpu.py` (win32com/win32pdh), `ui/desktop/orb/reactive_tts.py` usa `winsound` (stdlib, não pywin32) |

**Achado real confirmado**: `pywin32` é usado extensivamente mas não está em
`requirements.txt`. Hoje funciona porque está instalado no ambiente — mas
`pip install -r requirements.txt` numa máquina nova **não o instalaria**, quebrando
silenciosamente o guard de instância única, o auto-start, e o monitoramento de GPU
(alguns desses caminhos já têm fallback gracioso — `system_monitor/gpu.py` degrada
para "indisponível" — mas `settings/single_instance.py` e `settings/autostart.py`
fariam `ImportError` não tratado). **P1 — correção de baixíssimo esforço** (adicionar
uma linha ao `requirements.txt`), sem pesquisa adicional necessária.

Nenhuma dependência declarada está abandonada, com CVE conhecido relevante ao uso
específico do Steve, ou duplicada.

## 17. Performance

Números já medidos e documentados nos relatórios PATCH 04/V1.5 (não repetidos aqui por
não terem mudado): boot completo ~2.1-2.5s, RSS do processo ~77-79MB, ~25-27 threads
em regime estável, Dashboard abre em ~70-110ms, Quick Scan de dezenas de executáveis
em ~70-525ms. Nenhuma operação cara não identificada foi encontrada nesta auditoria
além do que já está documentado (ex.: o cache de nome de GPU/VRAM do PATCH 04, o
`_new_data_event` interruptível do Security Center).

## 18. Privacy

Mapa de TODAS as conexões externas conhecidas, confirmado por auditoria de código:

| Módulo | Destino | Propósito | Dado enviado | Automático? |
|---|---|---|---|---|
| `ai/providers/ollama_provider.py` | `localhost:11434` (Ollama) | Chat/tool-calling | Mensagens da conversa | Sim, mas 100% local (não sai da máquina) |
| `voice/stt.py` (faster-whisper) | Hugging Face | Download do modelo Whisper | Nenhum dado do usuário, só o download do modelo | Só na primeira execução |
| `security/network_analysis.py` | Nenhum (leitura passiva de `psutil.net_connections()`) | Observar conexões existentes de outros processos | N/A — nunca abre conexão própria | N/A |

Nenhuma outra chamada de rede encontrada em `tools/`, `security/`, `system_monitor/`,
`memory/`. `tools/browser.py::OpenURLTool` abre o navegador padrão do sistema (ação do
usuário, não uma chamada de rede do próprio Steve).

## 19. Security (revisão adversarial)

- **Prompt injection**: o Orchestrator nunca deixa o modelo executar código
  diretamente — só `ToolCall` estruturado, sempre validado pelo `ToolManager`/
  `PermissionManager`. Um texto malicioso no conteúdo de uma memória ou resultado de
  ferramenta poderia, em teoria, tentar instruir o modelo a chamar uma ferramenta
  fora de contexto — mitigado pelo fato de que toda ferramenta hoje é LOW/somente-
  leitura-ou-reversível, então o pior caso de uma "instrução injetada" bem-sucedida é
  limitado.
- **Path traversal / command injection**: ferramentas de arquivo (`tools/filesystem.py`)
  usam `pathlib.Path` diretamente nos parâmetros do usuário sem sanitização de `..`
  explícita — como o escopo de ação é local (o próprio computador do usuário, sem
  fronteira de privilégio a atravessar), o risco prático é baixo, mas não há teste
  dedicado confirmando que um caminho como `../../Windows/System32` é tratado com a
  mesma cautela que um caminho normal.
- **A segurança depende demais do LLM?** **Resposta: não, structuralmente.** Toda
  decisão de permitir uma ação passa por `PermissionManager` (código determinístico),
  nunca pelo julgamento do modelo. O ponto fraco real não é o LLM — é o gap já
  identificado na seção 5 (nenhum diálogo de confirmação real na GUI), que é uma
  lacuna de UI, não de dependência do LLM.

## 20. Testing

505 testes confirmados via `pytest --collect-only -q`. Padrões observados: uso
extensivo e correto de `threading.Event` para sincronização determinística (em vez de
`sleep`) nos testes de lifecycle mais recentes (PATCH 03/V1.5); um padrão de
flakiness real foi descoberto e corrigido durante a V1.5 (testes que dependiam de
tempo real de coleta do `SystemMonitorService` sob carga pesada da suíte completa —
corrigidos desacoplando-os de temporização real). Nenhuma área de código de produção
ficou sem teste dedicado nesta varredura. Testes de hardware real (GPU, microfone)
são corretamente isolados em scripts de validação manual separados, não na suíte
automática — evitando o "falso sinal de cobertura" de um teste que só passa na máquina
de quem o escreveu.

## 21. Documentation

`docs/ARCHITECTURE.md` (>1000 linhas) é extenso e, pela amostragem desta auditoria,
tecnicamente preciso em relação ao código — mas tem lacunas de **omissão**, não de
contradição: não documenta os 6 achados da seção 5 (porque eles nunca foram
descobertos antes desta sessão). `README.md` está alinhado com o estado atual (V1.5
mencionado corretamente). Nenhuma contradição factual entre código/README/
ARCHITECTURE.md/relatórios foi encontrada.

## 22. Open Source Research (fontes)

- STT: [faster-whisper license](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE)
- TTS: [Piper TTS licensing discussion](https://www.cekura.ai/discover/piper-tts), [Python TTS packages overview](https://smallest.ai/blog/python-packages-realistic-text-to-speech)
- VAD: [Silero VAD PyTorch Hub](https://pytorch.org/hub/snakers4_silero-vad_vad/), [py-silero-vad-lite](https://github.com/daanzu/py-silero-vad-lite)
- Wake word: [openWakeWord](https://github.com/dscripka/openWakeWord), [Porcupine FAQ](https://picovoice.ai/docs/faq/porcupine/), [wake word comparison](https://voxrt.com/wake-word-comparison)
- GUI: [Which Python GUI library 2026](https://www.pythonguis.com/faq/which-python-gui-library/), [Python GUI Frameworks 2026](https://www.pistack.xyz/posts/2026-08-30-python-gui-frameworks-tkinter-pyside6-flet-comparison/)
- Agent frameworks: [AutoGen vs CrewAI vs LangChain 2026](https://www.index.dev/skill-vs-skill/ai-langchain-vs-crewai-vs-autogen), [Practical comparison 2026](https://dev.to/agdex_ai/langchain-vs-crewai-vs-autogen-a-practical-comparison-2026-29k8)
- YARA: [YARA rules glossary](https://corelight.com/resources/glossary/yara-rules), [Yara-Rules repository](https://github.com/Yara-Rules/rules)
- Sysmon/Sigma: [Sigma Rules explained](https://panther.com/blog/your-guide-to-the-sigma-rules-open-standard-for-threat-detection), [SigmaHQ](https://github.com/SigmaHQ/sigma), [Rustinel open-source EDR](https://www.helpnetsecurity.com/2026/05/11/rustinel-open-source-endpoint-detection-windows-linux/)
- ClamAV: [On-Access Scanning docs](https://docs.clamav.net/manual/OnAccess.html), [clamav-client](https://github.com/artefactual-labs/clamav-client)
- Event bus: [Event Bus Architecture in Python](https://learnmodernpython.com/event-bus-architecture-in-python-explained/), [pypubsub](https://github.com/apache/infrastructure-pypubsub)
- Vector memory: [Embedded vector DB comparison](https://kanopylabs.com/blog/lancedb-vs-chroma-vs-sqlite-vec), [Vector DB comparison 2026](https://www.firecrawl.dev/blog/best-vector-databases)
- Desktop automation: [pywinauto](https://github.com/pywinauto/pywinauto), [UI Automation tools comparison](https://medium.com/@jagsmehra.092019/windows-ui-automation-tools-comparison-ae9f8a143f27)
- Packaging: [Nuitka vs PyInstaller vs cx_Freeze 2026](https://blog.thoughtparameters.com/post/nuitka_vs_pyinstaller_python_packaging/), [Briefcase](https://medium.com/@nohkachi/how-to-package-a-python-desktop-app-for-windows-with-briefcase-a270cf05da17)
- Scheduler: [APScheduler](https://github.com/agronholm/apscheduler)
- Plugin architecture: [pluggy](https://github.com/pytest-dev/pluggy)
- Memory frameworks: [Mem0](https://mem0.ai/blog/adding-persistent-memory-to-local-ai-agents-with-mem0-openclaw-and-ollama)
- Desktop AI assistants landscape: [awesome-desktop-ai-assistants](https://github.com/xinyao27/awesome-desktop-ai-assistants)

## 23. Reusable Components

| Project | GitHub | License | Component | O que reusar | Arquivos necessários | Por quê | Compatibilidade | Segurança | Manutenção | Esforço de adaptação |
|---|---|---|---|---|---|---|---|---|---|---|
| Silero VAD | snakers4/silero-vad | MIT | Modelo ONNX + wrapper | Só a detecção fim-de-fala | Modelo (.onnx, ~1.8MB) + `py-silero-vad-lite` (zero deps Python extras) | Substitui limiar RMS simples por VAD neural, mais robusto a ruído | Windows/CPU OK, já é o ambiente-alvo | Modelo pré-treinado estático, sem execução de código externo | Ativo | Baixo — troca só dentro de `AudioCapture.record_until_silence` |
| openWakeWord | dscripka/openWakeWord | Apache-2.0 | Framework de detecção + modelos pré-treinados | Detecção de "Steve..." | Pacote `openwakeword` + modelo(s) pré-treinados relevantes | Preenche `voice/wakeword.py`, hoje vazio | CPU-only, já compatível | Modelos locais, sem rede | Ativo | Médio — nova implementação concreta de `WakeWordDetector`, sem mudar a abstração |
| yara-python | VirusTotal/yara-python | BSD-3-Clause-like | Binding Python do motor YARA | Motor de scan por assinatura | `yara-python` + um conjunto de regras avaliado regra-a-regra | Adiciona detecção por assinatura ao Security Center (hoje só heurística estrutural) | Windows OK | Escaneia bytes estáticos, nunca executa o arquivo (mesma filosofia do Steve) | Ativo, muito usado (VirusTotal) | Médio — novo módulo `security/yara_analysis.py` + integração como sinal em `rules.py` |
| pywinauto | pywinauto/pywinauto | BSD | Automação de janelas Win32/UIA | Controle de janelas além de abrir app | `pywinauto` | Habilitaria automação mais rica que `os.startfile` | Windows-only (ok, é o alvo) | Requer cautela de permissão (nova classe de ferramenta, provavelmente MEDIUM) | Ativo, >10 anos | Alto — não é só importar, é desenhar as novas ferramentas e sua política de permissão |
| pluggy | pytest-dev/pluggy | MIT | Sistema de hooks/plugin | Base para uma futura arquitetura de Skills | `pluggy` | É o mesmo motor usado por pytest/tox — maduro e minimalista | Puro Python, sem SO específico | Nenhum código de terceiro executado sem revisão do hookspec | Ativo, mantido pelo time do pytest | Alto — é uma peça de arquitetura nova (Skills), não uma troca pontual |
| APScheduler | agronholm/apscheduler | MIT | `BackgroundScheduler` | Base para um futuro Planner/lembretes | `apscheduler` | Evita reinventar agendamento cron-like | Puro Python | Sem execução de código externo por si só | Ativo | Médio — só quando o Planner for de fato priorizado |
| sqlite-vec | asg017/sqlite-vec | MIT/Apache-2.0 (dual) | Extensão SQLite de vetores | Busca semântica, SE necessária no futuro | Extensão `.dll`/`.so` + `sqlite-vec` Python | Mantém tudo no mesmo arquivo SQLite, sem processo novo | Windows OK | Extensão local, sem rede | Ativo | Baixo, mas **condicional** — só se a relevância por palavra-chave se mostrar insuficiente na prática |

**Nenhum destes foi instalado ou integrado nesta sessão** — a tabela acima é a
resposta à Fase 30, não uma ação executada.

## 24. Components to Replace

**Nenhum componente foi classificado como REPLACE nesta auditoria.** O único
candidato avaliado com essa intenção (TTS: pyttsx3 → Piper) foi rebaixado para
"avaliar como adicional opcional futuro" por causa da mudança real de licença do
Piper para GPL-3.0 descoberta durante a pesquisa (seção 8) — substituir a dependência
padrão do Steve por algo GPL exigiria uma decisão de licenciamento que está fora do
escopo desta auditoria técnica.

## 25. Components to Improve

Agrupado por área (nenhum item novo além do já detalhado nas seções acima —
consolidado aqui para referência rápida):

- **Architecture**: conectar `permission_auto_approve_level` (5.1); construir
  diálogo de confirmação real na GUI (5.2); decidir o destino de `conversation_history`
  (5.4).
- **Tools**: rede de segurança em `ToolManager.execute()` (5.5); mesma proteção no
  CLI (5.6).
- **Voice**: VAD por energia → Silero VAD (seção 8).
- **Security**: detecção por assinatura via YARA (seção 11).
- **Dependencies**: declarar `pywin32` em `requirements.txt` (seção 16).
- **Documentation**: registrar os achados de código-morto/config-morta no
  `ARCHITECTURE.md` para não serem redescobertos do zero numa auditoria futura.

## 26. Components to Remove

- `core/errors.py` (`SteveError`, `AIProviderUnavailableError`, `ToolExecutionError`)
  — confirmado sem nenhum uso em todo o repositório (grep, incluindo testes). Único
  candidato real de remoção encontrado nesta auditoria completa.

## 27. Components to Keep

A grande maioria do sistema: `EventBus`, `LifecycleManager`, `Orchestrator`,
`AIRouter`/`OllamaProvider`, `ToolManager` + as 15 ferramentas LOW, `MemoryService`/
SQLite, `SystemMonitorService` (psutil/WMI/PDH), `SecurityEngine`/`SecurityScanner`/
`ProcessAnalyzer`, CustomTkinter + toda `ui/desktop/`, `faster-whisper` (STT). Todos
avaliados contra alternativas externas reais nesta auditoria e confirmados como a
escolha correta para o estágio atual do projeto.

## 28. Technical Debt

Consolidação dos achados de código (seção 5) + dependência não declarada (seção 16) +
lacuna de documentação (seção 21) — nenhum item novo além do já listado; esta seção
existe só para agrupar sob a lente de "dívida técnica" o que as seções anteriores já
descreveram em detalhe, evitando repetição.

## 29. 10x Growth Analysis

Se o Steve crescer 10x em uso (muito mais ferramentas, muito mais memórias, uso
contínuo por dias):

- **Quebra primeiro**: a única conexão SQLite (`memory/database.py::Database`,
  `check_same_thread=False` mas uma única `sqlite3.Connection`) sob escrita
  concorrente pesada de múltiplos serviços (Orchestrator salvando histórico +
  Security Center registrando auditoria + futuras Skills) — SQLite lida bem com
  escrita concorrente moderada via seu próprio locking, mas não foi testado sob carga
  10x.
- **Vira gargalo**: `pasta tools/` como uma lista fixa registrada manualmente em
  `main.py::register_default_tools()` — hoje são 15 chamadas explícitas; a 150
  ferramentas isso ficaria difícil de manter sem uma arquitetura de plugin (é
  exatamente o caso de uso do `pluggy`, seção 23).
- **Precisa desacoplar**: a lista fixa de `TOOL_STATUS_LABELS` em
  `ui/desktop/controller.py` (mapa nome→texto) não escala — precisaria virar parte da
  própria definição de cada `Tool` (um `status_label` na classe) em vez de um mapa
  externo mantido à parte.
- **Precisa virar plugin/service**: qualquer automação nova (ex.: pywinauto) deveria
  nascer já como um módulo independente registrável, não uma ferramenta a mais
  hardcoded em `register_default_tools()`.

## 30. 100x Growth Analysis

Apenas identificação de limites, não uma decisão de construir:

- SQLite como arquivo único começaria a ser um limite real de armazenamento/
  concorrência numa escala de "centenas de milhares de eventos de auditoria/achados
  de segurança acumulados" — mas isso está muito além do uso de um assistente pessoal
  desktop de um único usuário, que é o escopo declarado do Steve.
- Um verdadeiro sistema de Skills precisaria de sandboxing real (processo separado ou
  permissões restritas por skill) — `pluggy` sozinho não resolve isolamento, só
  descoberta/registro de hooks.
- CustomTkinter provavelmente atingiria um teto de complexidade de UI numa escala de
  "dezenas de painéis/telas simultâneas" — mas isso é uma escala de aplicação
  corporativa, não de assistente pessoal.

## 31. Future Architecture (proposta conceitual, não implementação)

```
STEVE CORE        → core/ (Orchestrator, EventBus, Lifecycle) — já existe, KEEP
STEVE AI          → ai/ (Router, Providers) — já existe, KEEP
STEVE MEMORY       → memory/ — já existe, KEEP; sqlite-vec como extensão condicional
STEVE VOICE        → voice/ — já existe; + Silero VAD, + openWakeWord quando priorizado
STEVE TOOLS        → tools/ — já existe, KEEP; ganha rede de segurança (seção 5.5)
STEVE SKILLS       → NOVO pacote futuro, usando pluggy como motor de registro/hooks,
                      nascendo ao lado de tools/ sem substituí-lo (skills = capacidades
                      plugáveis maiores; tools = ações atômicas, continuam existindo)
STEVE PLANNER      → NOVO pacote futuro (`core/planner`, já reservado no roadmap),
                      usando APScheduler como motor de agendamento
STEVE SECURITY      → security/ — já existe, KEEP; ganha YARA como sinal adicional
STEVE SYSTEM        → system_monitor/ — já existe, KEEP
STEVE UI            → ui/desktop/ — já existe, KEEP
STEVE AUTOMATION    → NOVO pacote futuro (pywinauto), separado de tools/ pela
                      permissão mais alta que provavelmente exigirá
STEVE INTEGRATIONS  → NOVO conceito — onde um futuro segundo provider de IA, ou um
                      conector opcional ClamAV, entraria sem tocar o Core
```

Cada módulo "NOVO" nasce como uma extensão registrável, nunca uma reescrita do que já
existe — mesma disciplina que já levou o Security Center (V1.5) a reaproveitar 100%
da coleta de processos do System Monitor em vez de duplicá-la.

## 32. Priority Matrix

| Item | Prioridade | Motivo |
|---|---|---|
| Declarar `pywin32` em requirements.txt | **P0** | Quebra silenciosa em instalação nova — segurança/estabilidade básica |
| Rede de segurança em `ToolManager.execute()` + CLI | **P1** | Necessário antes de crescer o número/complexidade de ferramentas |
| Diálogo de confirmação real na GUI | **P1** | Bloqueador silencioso assim que a primeira ferramenta MEDIUM/HIGH existir |
| Conectar `permission_auto_approve_level` | **P1** | Configuração morta é uma armadilha para quem lê o código depois |
| Decidir destino de `conversation_history` | **P2** | Melhoria importante de UX, não crítica |
| Remover `core/errors.py` | **P3** | Dívida técnica cosmética |
| Silero VAD | **P2** | Melhoria real de qualidade de voz, sem dependência pesada |
| YARA no Security Center | **P2** | Melhoria real de detecção, esforço médio |
| pluggy/Skills, APScheduler/Planner, pywinauto/Automation | **P4** | Futuro — só quando essas etapas forem de fato priorizadas |

## 33. Recommended Roadmap

Baseado nos resultados reais da auditoria (não todas as fases do template original se
justificam — omitidas as que não têm achado correspondente):

- **PHASE 0 — Architectural Hardening**: os 4 itens P0/P1 da seção 32 (dependência,
  rede de segurança de ferramentas, diálogo de confirmação, config morta). Nenhuma
  dependência nova.
- **PHASE 1 — Component Reuse (voz)**: Silero VAD substituindo o limiar RMS. Baixo
  risco, ganho de qualidade mensurável.
- **PHASE 2 — Security Center: detecção por assinatura**: integração YARA como sinal
  adicional, mantendo a filosofia "nunca executa o arquivo".
- **PHASE 3 — Wake word**: openWakeWord, quando a ativação por "Steve..." for
  priorizada (já estava no roadmap original como V1.2, nunca implementada).
- **PHASE 4 — Skills**: pluggy como motor, só quando houver uma necessidade real de
  capacidades plugáveis além de ferramentas atômicas.
- **PHASE 5 — Planner**: APScheduler, só quando o Planner for priorizado.

Fases não incluídas por falta de achado que as justifique nesta auditoria: migração de
GUI, adoção de framework de agente, banco vetorial (condicional, não agendada), banco
de dados diferente de SQLite, automação Windows mais rica (pywinauto — fica em standby
até haver um caso de uso concreto).

## 34. Implementation Plan (descritivo — NÃO EXECUTAR)

Para os 2 itens de maior prioridade concreta:

**1. Declarar pywin32**
- PROBLEM: dependência usada mas não declarada.
- CURRENT: `requirements.txt` sem `pywin32`.
- TARGET: `pywin32>=305` adicionado (versão mínima a confirmar com o que está em uso, v312).
- REUSE SOURCE: N/A (correção de metadado, não de código).
- FILES TO MODIFY: `requirements.txt`.
- MIGRATION STEPS: adicionar linha, testar `pip install -r requirements.txt` em ambiente limpo.
- TEST PLAN: nenhum teste novo necessário — é uma correção de manifesto.
- ROLLBACK: reverter a linha.
- RISK: nenhum.

**2. Rede de segurança em ToolManager.execute()**
- PROBLEM: uma exceção de ferramenta propaga sem proteção nesta camada.
- CURRENT: `return tool.execute(params)` sem try/except.
- TARGET: capturar `Exception`, retornar `ToolResult(success=False, error=...)`.
- REUSE SOURCE: N/A (código próprio, correção pontual).
- FILES TO MODIFY: `core/tool_manager.py`.
- FILES TO REVIEW (não modificar nesta fase): `ui/cli.py` (mesma classe de proteção,
  decisão separada).
- MIGRATION STEPS: adicionar try/except; teste unitário com uma ferramenta fake que
  lança exceção.
- TEST PLAN: novo teste em `tests/test_tools.py` confirmando que uma exceção não
  propagada vira `ToolResult(success=False)`.
- ROLLBACK: reverter o try/except.
- RISK: baixo — muda comportamento de "processo derruba" para "erro reportado", uma
  mudança estritamente mais segura.

(Os demais itens de P1/P2 seguem o mesmo nível de detalhe quando autorizados — não
expandidos aqui para não inflar um documento que já é, por natureza desta auditoria,
extenso.)

## 35. What NOT To Do

- Não reescrever o Core (`Orchestrator`/`EventBus`/`Lifecycle`) — nenhuma evidência de
  problema que justifique.
- Não adotar LangChain/AutoGen/CrewAI — overkill confirmado pela própria pesquisa para
  o caso de uso de agente único do Steve.
- Não migrar a GUI para PySide6/Flet sem uma dor real relatada — nenhuma encontrada.
- Não adicionar banco vetorial agora — nenhuma evidência de que a relevância por
  palavra-chave esteja insuficiente.
- Não integrar ClamAV como dependência obrigatória — exige daemon separado, suporte
  fraco a tempo real no Windows, licença GPL-2.0 a avaliar.
- Não adotar Piper TTS via `import` direto — mudança de licença real para GPL-3.0 no
  fork ativo.
- Não instalar Sysmon como serviço do sistema para o Security Center atual — modelo
  operacional mais pesado que o embutido/leve já escolhido.
- Não trocar SQLite por outro banco — nenhum volume de dados que justifique.
- Não clonar repositórios inteiros de nenhum candidato — cada reuso proposto na seção
  23 identifica exatamente o pacote/arquivo necessário, nunca "baixar o projeto
  inteiro".
- Não implementar nada desta lista nesta sessão — esta é uma auditoria.

## 36. Final Architecture Verdict

O Steve, após V1.0→V1.5, tem uma arquitetura que **já reflete corretamente** os
princípios que o pedido de auditoria pede para preservar: local-first, privacy-first,
modular, sem dependências desnecessárias. A pesquisa externa extensa realizada nesta
auditoria **não encontrou nenhum caso forte de substituição** — o padrão consistente
em cada área pesquisada foi "a escolha atual já é a correta para a escala e o escopo
de hoje; a alternativa externa resolve um problema que o Steve ainda não tem". As
oportunidades reais são, em ordem de valor: (1) consertar as conexões quebradas já
existentes no código (achados de auditoria, zero dependência nova), (2) dois reusos
pontuais bem justificados e de baixo risco (Silero VAD, YARA), (3) reusos maiores
reservados para quando as funcionalidades correspondentes (wake word, Skills,
Planner) forem de fato priorizadas — nunca antecipados sem necessidade.

**Nenhuma ação foi executada nesta sessão. Este documento é exclusivamente auditoria,
pesquisa, comparação e plano de evolução, conforme solicitado.**
