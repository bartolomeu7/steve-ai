# Steve AI — Desktop Window: correção dos 3 testes falhando

**Data**: 2026-09-23 · **Branch**: `steve-main-sync` · **Status**: correção pronta e testada (suíte completa verde), **NÃO commitada** (ver seção 17)

## 1. Objetivo
Analisar, achar a causa raiz e corrigir as 3 falhas de `tests/test_desktop_window.py` (as únicas da suíte de 608 testes), sem tocar na FASE 2 nem em áreas protegidas.

## 2. Estado inicial
Suíte completa: 601 passed / 3 failed / 4 skipped. Falhas: `test_window_creates_with_expected_title`, `test_apply_status_updates_labels`, `test_send_via_entry_and_button_end_to_end`. Causa comum descoberta: `ui/desktop/window.py` foi reconstruído como janela de dois modos (HUD, padrão, e Workspace); os testes foram escritos para a janela antiga de modo único.

## 3. Falha 1 — título
Esperado `'Steve'`, obtido `'STEVE'`.

## 4. Causa raiz 1
**Bug real (deriva de constante).** `window.py` tinha o literal `self.title("STEVE")`, enquanto `settings/single_instance.py::WINDOW_TITLE = "Steve"` é a fonte usada por `bring_existing_instance_to_front()` (`FindWindow(None, WINDOW_TITLE)`) e documentada no Launcher ("MainWindow title is 'Steve'"). Verificado empiricamente que o `FindWindow` do Windows é case-insensitive (achou a janela com `'Steve'` e com `'STEVE'`), então **não havia quebra funcional hoje** — mas o acoplamento estava frágil e duplicado.

## 5. Correção 1
`ui/desktop/window.py`: importa `WINDOW_TITLE` de `settings.single_instance` e usa `self.title(WINDOW_TITLE)` (2 linhas). O teste não foi alterado. Os rótulos visuais "STEVE" dentro do HUD/Workspace não foram tocados.

## 6. Falha 2 — status label
Label do modelo com um caractere "corrompido".

## 7. Causa raiz 2
**Não é bug de encoding nem de código.** O caractere é `'—'` (o "—" inicial do `StatusBar`), que o console cp1252 exibe como `�`. A janela do teste é criada no modo **HUD** (padrão do app), onde `apply_status()` intencionalmente atualiza só os cantos do HUD (`"MODEL  qwen2.5"`) e não a `StatusBar` (estacionada, invisível); além disso `connection_label` é `None` no HUD. O teste verificava widgets que só existem no modo Workspace. Em Workspace, o mesmo fluxo funciona (modelo, Ollama, conexão e microfone corretos — verificado).
Tipo: teste desatualizado (BUG DE TESTE), comportamento da aplicação correto por design.

## 8. Correção 2
`tests/test_desktop_window.py`: novo fixture `workspace_window` (`ui_mode="workspace"`) usado pelo teste; e cobertura HUD adicionada em vez de descartada: `test_hud_mode_is_the_default`, `test_apply_status_in_hud_mode_updates_corner_labels`.

## 9. Falha 3 — botão Send
`orchestrator.received == []`.

## 10. Causa raiz 3
Mesma causa: no modo HUD, `InputBar(voice_only=True)` esconde o campo de texto e `_handle_send()` retorna de imediato ("voice_only hides text (HUD)", documentado em `controls.py`). O caminho Entry→Send→Controller→Orchestrator existe e funciona em Workspace.

## 11. Correção 3
Teste roda com `workspace_window`; adicionado `test_hud_mode_ignores_text_send_because_it_is_voice_only` para garantir que o HUD segue voice-only.

## 12. Arquivos alterados
- `ui/desktop/window.py` (+2/−1: import e título) — correção de aplicação.
- `tests/test_desktop_window.py` (+3 testes novos, 1 fixture, 2 testes redirecionados ao modo correto).
- `tests/test_security_engine.py` (+10 linhas: sincronização com o baseline em `test_audit_logger_records_finding_created`, ver seção 15).
Nenhum arquivo de `launcher/updater/`, `tests/test_update_client.py` ou relatório da FASE 2 foi tocado por esta correção (os arquivos da FASE 2 seguem não commitados, como antes).

## 13. Testes antes
601 passed / 3 failed / 4 skipped (608).

## 14. Testes depois
`tests/test_desktop_window.py`: 23 passed (antes 20 + 3 novos). `tests/test_security_engine.py`: 20 passed. Suíte completa (611 testes), execuções registradas:
- Execuções anteriores à correção do teste de segurança: 606 passed / 1 failed / 4 skipped (duas vezes; falha = `test_audit_logger_records_finding_created`) e uma interrompida por `0x80000003` em `psutil.process_iter` (ambiente, resultado inválido).
- Após a correção: execução A = 606 passed / 4 skipped / **1 error** (`test_dashboard.py::test_live_activity_never_shows_raw_tool_payload`, erro de *setup*: o Tk não conseguiu ler `ttk/panedwindow.tcl` do Python do sistema, arquivo que existe e não muda desde 2022 — falha transitória de ambiente; o arquivo passa 29/29 na reexecução e o teste passou em todas as outras execuções); 250 s.
- Execução B (final): **607 passed / 4 skipped / 0 failed / 0 errors, exit code 0, 223 s.** Sem tracebacks nem "Windows fatal exception".
Os 4 skipped são os testes de integração com Ollama (Ollama não está rodando). Os 24 testes do Update Client passam.

## 15. Regressões e causa raiz da falha do SecurityEngine
Nenhuma regressão criada. A falha `test_audit_logger_records_finding_created` era um **defeito do próprio teste (categoria C)**, não um bug de produção: o `SecurityEngine` analisa somente o snapshot *mais recente* (`_latest_process_snapshot`, por design). O teste publicava o snapshot vazio (baseline) e o snapshot com `new.exe` (pid 55555) em sequência, sem esperar a thread de análise consumir o baseline; sob carga, a thread só via o segundo snapshot, o pid 55555 entrava no próprio baseline e nunca gerava finding. Evidência (script de reprodução fora do projeto): o analisador recebia `(55555, count 1)` como primeira chamada; falhou 1/12 sem carga e 9/12 sob carga de GIL (8 threads); com a espera pelo `baseline_established` — a mesma sincronização que o teste irmão `test_new_process_produces_a_finding_published_on_the_bus` já usava — 0/12 sob a mesma carga. Por isso passava isolado (thread acorda entre as duas publicações) e falhava em execução lenta. Sob carga também se observou snapshot real tardio do coletor órfão (`stop()` expirando em 10 s), mas a espera pelo baseline foi suficiente. Correção: só no teste; sem aumento de timeout, sem retry, sem xfail, sem tocar código de produção.

## 16. Segurança
Diff só de UI e testes; sem segredo, chave, banco, modelo ou cache. Achado fora de escopo (não corrigido, tarefa separada sugerida): alternar Workspace→HUD lança `TclError` (`set_ui_mode` usa `input_bar` já destruído, `window.py` ~linha 162) — crash real para o usuário no botão de alternância.

## 17. Commit
**Não realizado**, aguardando auditoria/aprovação final. Pré-condição de suíte verde atendida (execução B da seção 14).

## 18. Push
Não realizado.

## 19. Verificação remota
Não aplicável (sem push). Remoto inalterado: `origin/steve-main-sync` = `54c8c6c`.

## 20. Pendências
1. Auditoria final e autorização para commit — em commits separados: FASE 2 (`launcher/updater/`, `tests/test_update_client.py`, `SteveLauncher.py`, `requirements.txt`, relatório da FASE 2) e correção de testes/janela (`window.py`, `test_desktop_window.py`, `test_security_engine.py`, este relatório).
2. Corrigir o crash Workspace→HUD (`TclError` em `set_ui_mode`) — tarefa separada.
3. Erro transitório de Tk/Tcl observado uma vez (execução A) — ambiente, monitorar.
