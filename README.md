# Steve Desktop Assistant

Assistente pessoal de IA local para Windows, com uma interface desktop nativa. Conversa
por texto ou voz, executa ferramentas seguras no computador do usuário e mantém memória
local em SQLite. Roda inteiramente offline, usando um modelo local via
[Ollama](https://ollama.com).

**V1 — Fundação**: núcleo, configuração, memória, IA (tool-calling nativo do Ollama),
ferramentas seguras, permissões, logs e testes.
**V1.1 — Voz**: conversa por voz de ponta a ponta (microfone -> STT local -> o mesmo
Orchestrator/IA/Tools do modo texto -> TTS local -> alto-falante), desligada por padrão.
**V1.1.5 — GUI Desktop**: `python main.py` agora abre uma janela (CustomTkinter) em vez
do terminal; o CLI continua disponível via `python main.py --cli`. A janela tem um
**orb** animado — o "núcleo visual" do Steve — que reage ao estado real dele (ouvindo,
pensando, executando uma ferramenta, falando) e à própria voz.
**V1.1.6 — GUI validada**: tema claro/escuro configurável (⚙ Configurações), diálogo de
Configurações não duplica mais ao abrir duas vezes, fechamento da janela durante uma
resposta em andamento tratado sem crash. Testada de ponta a ponta com Ollama real:
texto, ferramenta (CPU), abrir aplicativo (Chrome), voz, voz+ferramenta, responsividade
durante processamento e recuperação de Ollama offline — ver
[docs/reports/V1.1.6_GUI_REPORT.md](docs/reports/V1.1.6_GUI_REPORT.md).
**V1.2 — Memória, identidade e contexto**: Steve agora distingue memória permanente,
temporária e de sessão, decide o que vale a pena guardar (pedido explícito ou fato
duradouro — não comandos comuns nem conversa casual), atualiza em vez de duplicar quando
uma informação muda, e monta o contexto enviado ao modelo com as memórias relevantes
para a mensagem atual em vez do banco inteiro — tudo via ferramentas nativas
(`remember_fact`, `forget_memory`, `recall_memories`), sem parser de texto paralelo. Tudo
local, tool-calling nativo intocado. Detalhes em
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
**V1.2.1 — AI Router**: o Steve não fala mais direto com o Ollama — agora passa por um
**AI Router** (`ai/router.py`) que registra providers, seleciona qual usar (automático ou
manual), sabe fazer fallback entre eles e expõe uma única interface para o resto do
sistema, sem revelar qual modelo respondeu. Só o Ollama existe como provider real por
enquanto (Claude ainda **não** está implementado) — mas Orchestrator, tool-calling
nativo e memória continuam funcionando exatamente como antes, agora através do Router.
**V1.3 — Presença e startup**: ciclo de vida explícito (`core/lifecycle.py`:
CREATED → INITIALIZING → READY/DEGRADED → SHUTTING_DOWN → STOPPED) — se o Ollama ou a
voz falharem na inicialização, o Steve continua funcionando em modo DEGRADED em vez de
travar. Primeiro início e retorno agora são corretamente diferenciados na saudação (nos
dois modos, CLI e GUI). "Iniciar com o Windows" (⚙ Configurações) agora funciona de
verdade; "Minimizar em vez de fechar" oculta a janela em vez de encerrar o processo
(ainda sem ícone de bandeja clicável — ver `docs/ARCHITECTURE.md`); só uma instância do
Steve roda por vez. Nenhuma dependência nova, AI Router/memória/voz/orb intocados.
**V1.4 — System Dashboard**: um segundo painel (botão "📊 Dashboard") mostra CPU, GPU,
RAM, disco e rede reais do computador (inclusive GPU AMD/Intel/NVIDIA, sem depender de
SDK de fabricante nenhum), processos ativos numa tabela ordenável, gráficos de histórico
leves e uma área "Steve Core" que reage a eventos reais do próprio Steve (pedido à IA,
resposta, ferramenta em execução, voz) com uma apresentação estilizada — nunca dado
inventado. Só monitoramento: nenhuma ação de matar/suspender/apagar processo ou
arquivo existe nesta versão. Nenhuma dependência nova (reaproveita `psutil` e
`pywin32`, já presentes); nenhum dado sai do computador.
**V1.4.1 — Stability & Hardening**: correção de uma race de concorrência real no
ciclo de vida do System Monitor (`start()`/`stop()` protegidos por lock dedicado),
métricas de hardware endurecidas — temperatura de CPU sempre "Indisponível" (fonte
WMI comprovadamente não confiável, nunca mais consultada), uso de CPU/GPU nunca mostra
0% na primeira amostra (era um falso "ocioso"), disco inacessível mostra "Indisponível"
em vez de um disco de 0 bytes inventado, frequência de CPU mostra atual e máxima lado a
lado. 400 testes passando.
**V1.5 — Security Center**: primeira versão de uma camada complementar de
monitoramento e detecção de segurança — **não** substitui o Microsoft Defender. Um
**Security Engine** (`security/engine.py`) roda desde a inicialização do Steve (não
espera o Dashboard abrir), reaproveita os processos que o System Monitor já coleta
(nenhuma enumeração paralela) e amostra conexões de rede reais (processo ↔ endereço
remoto, nunca conteúdo). Sinais estruturais e auditáveis (localização incomum, extensão
dupla disfarçada, processo novo, ausência de assinatura, arquivo recém-criado) se
combinam num score de risco sempre explicável (nunca um número sem motivo, nunca um
único sinal fraco virando HIGH/CRITICAL sozinho). Scan sob demanda (Quick/Arquivo/
Pasta), sempre cancelável, sempre em segundo plano. **Nenhuma ação automática
destrutiva existe**: nada aqui mata processo, apaga arquivo, desabilita o Defender/
Firewall ou executa código baixado — apenas detectar, analisar e alertar. 498 testes
passando. Ver [docs/reports/V1.5_SECURITY_CENTER_REPORT.md](docs/reports/V1.5_SECURITY_CENTER_REPORT.md).
Planner, Skills, proatividade, reuniões, um segundo provider de IA, ícone de bandeja
literal, quarentena e Security Intelligence via web chegam nas próximas versões.

## Pré-requisitos

- Python 3.11+
- [Ollama](https://ollama.com) instalado e em execução, com um modelo baixado:

```bash
ollama pull llama3.2
```

## Instalação

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Executar

```bash
python main.py
```

Abre a janela do Steve. Na primeira execução (`FIRST_RUN`), a própria janela mostra um
formulário de boas-vindas (nome, idioma, modelo, microfone, voz, proatividade) — nada
precisa ser digitado no terminal. Depois disso, a configuração fica salva em
`data/config.json` e a janela principal abre direto, com a saudação de acordo com o
horário na primeira mensagem do histórico.

### Interface de linha de comando (diagnóstico/desenvolvimento)

```bash
python main.py --cli
```

Mantida para debug, testes e ambientes sem tela — usa o wizard e o loop de chat em
texto originais. Consome exatamente os mesmos serviços (`main.build_app_services`) que
a GUI usa; nenhuma lógica é duplicada entre as duas interfaces.

### Conversa por voz

Na GUI: abra ⚙ **Configurações** e ative "Entrada por voz (microfone)" e/ou "Responder
por voz (TTS)" — ou já ative os dois na tela de primeira execução. Com o microfone
disponível, o botão 🎙️ na barra de entrada fica habilitado; clique nele para falar com
o Steve (ele grava até você parar de falar, transcreve localmente com faster-whisper,
processa pelo mesmo Orchestrator do texto e responde por voz). Na CLI, o equivalente é
digitar `v`. Na primeira vez que a voz é usada, o modelo de reconhecimento (~141 MB) é
baixado automaticamente; depois disso tudo roda offline.

### Tema

⚙ Configurações tem um seletor Claro/Escuro. A troca é salva imediatamente, mas só é
aplicada visualmente na próxima abertura do Steve (o chat mostra um aviso pedindo para
reiniciar) — recriar todos os widgets já abertos com as cores novas ao vivo não é
simples no CustomTkinter, então essa é uma limitação assumida por enquanto.

### Iniciar com o Windows e minimizar em vez de fechar

⚙ Configurações tem três novos controles: "Iniciar com o Windows" (liga/desliga o
atalho em `HKCU\...\Run` de verdade, reversível a qualquer momento), "Minimizar em vez
de fechar" (o botão de fechar da janela oculta o Steve em vez de encerrá-lo — o botão
"Sair", ao lado de ⚙ Configurações, sempre encerra por completo) e "Iniciar minimizado"
(só some se "Minimizar em vez de fechar" também estiver ligado). Só uma instância do
Steve roda por vez — abrir o Steve de novo enquanto ele já está aberto (visível ou
minimizado) traz a janela existente para frente em vez de abrir uma segunda.

### System Dashboard

O botão "📊 Dashboard" (ao lado de ⚙ Configurações) abre um segundo painel com cinco
abas: **Overview** (CPU/GPU/RAM/disco/rede em tempo real), **Performance** (gráficos dos
últimos ~2 minutos), **Processes** (tabela de processos reais, ordenável por CPU/RAM/
nome), **Security** (Security Center — ver seção própria abaixo) e **Steve Core** (um
feed estilizado que reage a eventos reais do Steve — pedido à IA, resposta, ferramenta em
execução, voz, Security Center). Fechar o Dashboard não encerra o Steve, e reabri-lo
nunca cria um segundo coletor. É só monitoramento: nenhuma ação de matar/
suspender/apagar processo existe. GPU é detectada para qualquer fabricante (AMD/Intel/
NVIDIA) sem precisar de um SDK específico; quando uma métrica não está disponível no seu
hardware, o Dashboard mostra "Indisponível" em vez de inventar um número.

### Security Center

Dentro do Dashboard, a aba **Security** mostra o estado da proteção (MONITORANDO/
PROTEGIDO/DEGRADADO/DESATIVADO), os achados recentes com evidências concretas (nunca
"vírus detectado" sem prova) e três botões de scan: **Quick Scan** (executáveis dos
processos em execução), **Scan File** e **Scan Folder** (escolhidos pelo usuário) — todos
cancelináveis e em segundo plano, nunca travam a janela. Um achado (`SecurityFinding`)
sempre traz os motivos concretos que levaram à severidade (INFO/LOW/MEDIUM/HIGH/
CRITICAL), nunca um score sem explicação. **O que esta versão explicitamente NÃO faz**:
não apaga, move ou coloca em quarentena nenhum arquivo; não mata nem suspende nenhum
processo; não desabilita o Defender/Firewall/UAC; não executa nenhum arquivo ou script
analisado; não envia nada para a internet (tudo local); não afirma ser um antivírus
completo. Detalhes e limitações completas em
[docs/reports/V1.5_SECURITY_CENTER_REPORT.md](docs/reports/V1.5_SECURITY_CENTER_REPORT.md)
e `docs/ARCHITECTURE.md`.

## Testes

```bash
pytest
```

## Estrutura

```
core/         orquestrador, contexto, sessão, eventos, lifecycle, saudação, logging
ai/           AI Router + abstração de provedor de IA + implementação Ollama + prompts
memory/       SQLite (preferências, hábitos, projetos, objetivos, fatos, histórico) —
              memórias permanentes/temporárias com categoria, chave, origem e expiração
tools/        ferramentas seguras (nível LOW): apps, processos, CPU/RAM/disco, arquivos, URLs, Security Center
security/     permissões, confirmações, auditoria e o Security Center (engine, regras,
              risco, análise de processo/arquivo/rede, scanner) — thread própria, sem tkinter
settings/     configuração central + wizard de primeira execução + auto-start (Windows) +
              guarda de instância única (Windows)
voice/        captura de áudio, STT (faster-whisper), TTS (pyttsx3), VoiceService e wake word (abstração, não implementada)
system_monitor/  coleta de métricas reais (CPU/GPU/RAM/disco/rede/processos) — thread própria, sem tkinter
ui/cli.py     chat em linha de comando (diagnóstico/desenvolvimento)
ui/desktop/   GUI desktop (CustomTkinter) — ver docs/ARCHITECTURE.md
ui/desktop/dashboard/  System Dashboard (Overview/Performance/Processes/Security/Steve Core)
tests/        suíte pytest
```

## Dados locais

`data/` (config e banco SQLite) e `logs/` (log de aplicação e auditoria) ficam fora do
controle de versão — tudo permanece na máquina do usuário.
