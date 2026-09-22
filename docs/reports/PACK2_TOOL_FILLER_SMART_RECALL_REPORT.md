# PACK 2 — TOOL FILLER + SMART RECALL

## Escopo

Aplicar duas melhorias de conversação vindas de um pacote externo (Grok) sobre o que
os Packs 1.1 e 1 (voz/streaming/tema) já deixaram pronto: **Tool Filler** (fala uma
frase curta enquanto uma ferramenta roda) e **Smart Recall** (seleção mais inteligente
de memórias relevantes). Aplicação cirúrgica — nenhuma reescrita, mudanças mínimas e
localizadas.

## A. Checklist de aplicação

- [x] `voice/tool_filler.py` adicionado — copiado como veio do pacote, sem alterações
  (é autocontido, sem acoplamento a nada específico do Steve).
- [x] `ToolFiller` integrado no local correto de execução de ferramentas —
  **adaptado**: não da forma que o guia sugeria (ver seção B).
- [x] `memory/smart_recall.py` adicionado — copiado como veio, sem alterações.
- [x] Adapter de memória criado — `memory/service.py::_to_memory_item()`, converte
  `MemoryRecord` (schema real do Steve) para `MemoryItem` (o que o `SmartRecall` espera).
- [x] `SmartRecall` sendo usado na montagem do contexto — dentro de
  `MemoryService.get_relevant_memories()`, chamado por `summarize_for_prompt()`, chamado
  por `ContextManager.build()` — o caminho real que já existia, não um novo.
- [x] Nenhuma quebra de import ou método — suíte completa rodada (ver seção C), 568
  testes passando nas áreas verificadas.

**Arquivos tocados**: `voice/tool_filler.py` (novo), `memory/smart_recall.py` (novo),
`voice/service.py` (integração), `memory/service.py` (integração + remoção de código
morto), mais os testes correspondentes e esta documentação. Nenhum outro arquivo do
projeto foi alterado nesta etapa.

## O que foi adaptado (nomes/métodos reais são diferentes do guia)

### Tool Filler — guia sugeria algo que o Orchestrator não pode fazer

O guia (`patches/TOOL_FILLER_INTEGRATION.md`) sugeria:
```python
# No Orchestrator:
self.tool_filler = ToolFiller(tts=self.tts)
with self.tool_filler.during_tool(tool_name):
    result = self.tool_manager.run(tool_name, arguments)
```
Dois problemas reais com isso no Steve: **(1)** `Orchestrator` nunca teve, e não devia
ganhar, uma referência a TTS — fala só existe em `VoiceService`; misturar as duas
quebraria a separação limpa que o projeto mantém desde a V1 (Orchestrator não sabe se
está sendo usado por voz, texto ou CLI). **(2)** `ToolManager.execute()` (o método real,
não `run()`, que não existe) já é chamado dentro de `Orchestrator._do_execute_tool_call`,
vários níveis dentro do loop de tool-calling — não dava pra simplesmente envolver essa
chamada sem furar essa fronteira.

**Solução real**: o Orchestrator já publica `TOOL_STARTED`/`TOOL_COMPLETED` no EventBus
compartilhado (V1.4, usado pelo Live Activity) toda vez que uma ferramenta roda.
`voice/service.py::VoiceService._filler_active()` assina esses dois eventos só durante
o turno de voz atual, e conduz o `ToolFiller` a partir deles — `TOOL_STARTED` inicia o
timer do filler, `TOOL_COMPLETED` para. Isso mantém o Orchestrator sem nenhuma
dependência de voz, e ainda captura exatamente o momento certo (nem antes, nem depois).

**Por que não usar `on_tool_call`** (o hook que já existia): ele dispara uma vez, antes
da ferramenta rodar, sem nenhum "terminou" correspondente — não dava pra saber quando
parar o filler só com ele.

### Smart Recall — bug real encontrado na própria adaptação, corrigido

`MemoryRecord` (schema real, V1.2) não tem campo `tags` nem `importance` float —
`important` é `bool`. O adapter (`memory/service.py::_to_memory_item`) mapeia isso com
cuidado — detalhes no arquivo. **Mas a primeira tentativa continha um bug real**: mapear
`important=False → importance=0.5` (o default do próprio dataclass do pacote) faz
`0.25 × 0.5 = 0.125`, que já é maior que o `min_score=0.12` do `SmartRecall` sozinho —
ou seja, **nenhuma memória seria excluída de verdade, não importa quão irrelevante**; o
"filtro de relevância" teria virado só um re-ranking, nunca uma exclusão real. Encontrado
fazendo a conta antes de escrever o teste, não depois. Corrigido para `0.3`
(`0.25 × 0.3 = 0.075 < 0.12`) — agora uma memória velha, sem nenhuma palavra em comum com
a pergunta e não marcada como importante realmente sai da lista, enquanto uma memória
recente continua entrando mesmo sem overlap (a recência sozinha já garante isso). Testado
de verdade manipulando o timestamp no banco (não só teoria) —
`tests/test_memory.py::test_get_relevant_memories_excludes_an_old_unimportant_unrelated_memory`
e o teste irmão que confirma que uma memória nova não é descartada.

### Staleness de TTS evitada proativamente

O guia sugere `ToolFiller(tts=self.tts)` capturado uma vez. Isso ficaria desatualizado
depois que `ui/desktop/app.py::_wrap_tts_for_orb` troca `voice_service.tts` por um
`OrbReactiveTTS` (acontece DEPOIS que `VoiceService` é construído — mesmo padrão que já
tinha me mordido no Pack 1 com o `StreamingSpeaker`). Corrigido usando
`speak_fn=lambda text: self.tts.speak(text)`, que sempre lê `self.tts` no momento da
fala. Testado explicitamente —
`test_filler_uses_the_live_tts_not_a_stale_reference_from_construction`.

## B. Análise de qualidade

**A integração do filler está segura (threads, delay, fallback)?** Sim, com uma ressalva
conhecida. `ToolFiller`/`_FillerContext` (arquivo original, não alterado) já usa
`threading.Event`, thread daemon, e `join(timeout=1.0)` — meu código só liga/desliga isso
a partir de eventos reais, sem tocar na lógica de threading em si. Testado com uma
ferramenta real lenta (0.6s) e uma instantânea, confirmando que o filler fala num caso e
não no outro, com o `ToolFiller` de verdade (não um mock). Ressalva real: o `join(timeout
=1.0)` do próprio arquivo não é uma garantia — se o filler ainda estiver no meio da fala
exatamente quando o resultado real chega, pode haver uma sobreposição breve de áudio.
Não corrigi isso alterando `tool_filler.py` (mantido como veio, por escopo), mas
documentei e recomendo testar especificamente (ver seção C).

**O Smart Recall está realmente filtrando memórias irrelevantes?** Sim, mas só depois da
correção do piso de importância (ver acima) — e mesmo assim, só para memórias que sejam
tanto antigas (dias/semanas) quanto sem nenhuma palavra em comum com a pergunta. Uma
memória recém-criada sem relação nenhuma com a pergunta atual ainda entra na lista,
sustentada só pela recência — comportamento razoável (uma memória nova pode ser
relevante para o futuro mesmo não sendo para a pergunta de agora), mas significa que o
"filtro" não é agressivo logo de cara. Validado com Ollama real: ensinei 3 fatos
diferentes (editor preferido, projeto principal, comida preferida) e perguntei sobre o
projeto — a memória certa (`Prime Ges`) veio em 1º lugar.

**Há risco de prompt ainda ficar grande demais?** O `limit` (padrão 8, vindo de
`get_relevant_memories`) não mudou — o Smart Recall só troca COMO as 8 são escolhidas,
não quantas. `summarize_for_prompt` continua truncando conteúdo longo em 200
caracteres (`MAX_SUMMARY_CONTENT_CHARS`, inalterado). Sem mudança de risco nesse eixo.

**Algum ponto frágil ou que precisa de ajuste manual?** Dois:
1. O risco de sobreposição de áudio filler↔resposta final descrito acima — mais
   provável se o streaming do Pack 1 (`voice_streaming_enabled`) também estiver ligado
   ao mesmo tempo (duas features experimentais empilhadas).
2. `_score()` do `SmartRecall` (arquivo original) pesa recência com decaimento de 90
   dias fixo, sem ajuste por tipo de memória — um hábito antigo mas ainda válido
   ("prefere café de manhã") perde pontos por recência mesmo sem ter ficado menos
   verdadeiro. Não é um bug desta integração, é uma limitação inerente ao algoritmo do
   pacote — vale saber que existe.

## C. Testes recomendados no seu PC

1. **Filler com ferramenta real e lenta**: pergunte por voz algo que force uma
   ferramenta (ex.: "abre o Bloco de Notas" ou "lista os processos") e confirme que
   Steve fala algo como "Só um segundo..." antes do resultado.
2. **Filler com ferramenta rápida**: pergunte "qual o uso de CPU?" (`check_cpu` costuma
   ser quase instantâneo) — confirme que o filler NÃO fala (comportamento esperado, não
   um bug).
3. **Filler + streaming juntos**: ligue `voice_streaming_enabled` nas Configurações E
   force uma pergunta com ferramenta por voz — ouça com atenção se há alguma sobreposição
   ou corte estranho entre o filler e a resposta real (o risco conhecido da seção B).
4. **Smart Recall com fatos conflitantes**: ensine "prefiro X" e depois "na verdade
   prefiro Y" (mesma categoria) e confirme que só a memória mais recente/relevante
   aparece quando você pergunta sobre isso depois.
5. **Smart Recall com muitas memórias**: ensine 10+ fatos variados ao longo de uma
   sessão e depois pergunte algo específico — confirme que a resposta usa o fato certo,
   não um genérico ou desatualizado.
6. **Turno de texto puro (sem voz)**: confirme que o filler nunca aparece/interfere numa
   conversa por texto (ele só existe dentro do `VoiceService`).

## D. Nota final

**8/10.** As duas melhorias em si são bem pequenas e bem direcionadas — nenhuma exige
mudar arquitetura, e as duas se encaixaram nos pontos certos (`EventBus` para o filler,
dentro de `get_relevant_memories` para o recall) sem duplicar nada que já existia. O
que tira pontos: nenhum dos dois arquivos originais do pacote foi escrito pensando no
Steve de verdade — os dois guias de integração propunham código que não bateria com a
arquitetura real (Orchestrator com TTS, `tts=self.tts` capturado cedo demais, e o
default de importância que silenciosamente desativava a exclusão do Smart Recall) — nada
disso é culpa do código em si, mas mostra que "aplicar como veio" não teria funcionado
direito sem essa adaptação.

**Deixam o Steve perceptivelmente mais natural?** O Tool Filler sim, de forma bem
direta — o silêncio de 2-4s durante uma ferramenta era o tipo de coisa que entrega
"script automatizado" em vez de "assistente"; uma frase curta ali muda a sensação
imediatamente. O Smart Recall é uma melhoria real mas mais sutil — só fica visível
quando você já tem várias memórias acumuladas e pergunta algo específico; numa conta
nova com poucas memórias, o efeito prático é pequeno (o algoritmo antigo já dava conta
de listas curtas). Juntos, empurram o Steve na direção certa sem serem, sozinhos, a
diferença entre "assistente qualquer" e "Thomas" — isso continua dependendo mais do
Pack 1 (voz natural + streaming) do que deste Pack 2.

## Validação

`pytest tests/` rodado em duas partes (a suíte completa travou duas vezes com uma
exceção do Windows — `0x80000003`, dentro de threads do `SystemMonitorService`/Tcl,
não relacionada a nenhum arquivo desta etapa — ao rodar ~500 testes Tkinter+threading
num único processo por muito tempo nesta máquina; confirmado como fragilidade do
ambiente, não regressão, rodando as mesmas partes separadamente):
- Sem os arquivos de GUI pesada (Tkinter): **458 passed, 3 failed** — os 3 falhos
  (`test_ollama_integration`, `test_security_engine`, `test_security_scanner`) não têm
  nenhuma relação com os arquivos desta etapa; reexecutados sozinhos, **passaram os 3**,
  confirmando contenção de recursos (sessão de horas com Ollama e pytest repetidos), não
  regressão.
- Arquivos de GUI (`test_dashboard`, `test_desktop_*`, `test_orb_widget`): **107 passed**.
- Validação real com Ollama (sem mocks): Smart Recall rankeou a memória certa em 1º
  lugar para uma pergunta real depois de ensinar 3 fatos reais; Tool Filler falou
  quando a ferramenta demorou e ficou em silêncio quando foi instantânea, ambos com as
  classes reais do pacote.

Total: **568 testes passando** nas áreas relevantes, mais a validação real end-to-end.
