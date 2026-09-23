# Steve AI — Launcher GitHub Release, FASE 0 + FASE 1

**Data**: 2026-09-22
**Branch**: `steve-main-sync`
**Escopo desta fase**: contrato de atualização (FASE 0) + pipeline de distribuição via GitHub Actions
(FASE 1). O Launcher **não consome** nada disso ainda — ver seção 13.

---

## RESUMO EXECUTIVO

```
IMPLEMENTADO:
  FASE 0 — contrato de atualização (VERSION + STEVE_UPDATE_CONTRACT.md)
  FASE 1 — pipeline de release (scripts/build_release.py + .github/workflows/release.yml)

BLOQUEADOR PRÉ-EXISTENTE (fora do escopo desta fase):
  3 bugs reais em ui/desktop/window.py, encontrados ao rodar o pytest completo:
    1. título da janela: 'STEVE' (real) vs 'Steve' (esperado pelo teste)
    2. label da status bar retorna um caractere corrompido ('�') em vez do texto do modelo
    3. o fluxo entry+botão "enviar" não chega a acionar o Orchestrator

RELEASE DE PRODUÇÃO:
  AINDA NÃO LIBERADA. O workflow desta fase usa o pytest completo como gate
  obrigatório, e o pytest completo está vermelho por causa dos 3 bugs acima
  (não relacionados a esta fase). Portanto nenhuma tag publicada hoje geraria
  uma Release real — o job pararia no passo de testes, como deveria.
```

Não afirmo, em nenhum momento deste relatório, que a FASE 1 está pronta para gerar uma
Release de produção enquanto o pytest completo estiver vermelho. Está pronta como
**pipeline** (construção, validação, checksum, assinatura) — não como **release liberável
hoje**.

---

## 1. Objetivo

Implementar, sem tocar em UI/Launcher-consumidor/voz, os dois primeiros elos da cadeia de
distribuição decidida em
[`STEVE_LAUNCHER_ARCHITECTURE_DECISION.md`](STEVE_LAUNCHER_ARCHITECTURE_DECISION.md)
(Opção D — GitHub Releases + SHA-256 + Ed25519, sem tufup):

- **FASE 0**: formalizar o contrato de atualização (fonte de versão, nomenclatura de
  release, política de checksum/assinatura/downgrade, backup, health check, máquina de
  estados, separação source/distribution/runtime).
- **FASE 1**: um workflow do GitHub Actions que, a partir de uma tag `vX.Y.Z`, roda os
  testes, monta o pacote distribuível, valida seu conteúdo, calcula o checksum, assina com
  Ed25519 e publica uma GitHub Release com os assets — falhando de forma clara e segura
  quando qualquer pré-condição não for satisfeita.

## 2. Estado anterior

- Nenhum arquivo `VERSION` existia em lugar nenhum do projeto.
- Nenhum mecanismo de versão existia no código (`grep` por `__version__`/`VERSION =` em
  `main.py`, `settings/config.py`, `tools/steve_status.py` não encontrou nada — auditado na
  FASE 0, antes de criar o arquivo).
- Não existia `.github/` na branch `steve-main-sync` (só existe, com conteúdo totalmente
  diferente e não relacionado, na branch separada `nexora-code-build-20260921`, que não foi
  tocada nesta fase).
- Não existia nenhum script de empacotamento/assinatura em `scripts/`.
- O Launcher (`launcher/SteveLauncher.py`) seguia exatamente como auditado em
  `STEVE_GITHUB_INITIAL_SYNC.md`: sem versão, sem GitHub, sem update, sem rollback.

## 3. Arquivos modificados/criados nesta fase

Todos **novos** — nenhum arquivo já versionado foi alterado:

```
VERSION                                  (novo)
docs/reports/STEVE_UPDATE_CONTRACT.md    (novo — FASE 0)
scripts/build_release.py                 (novo — FASE 1)
scripts/requirements-release.txt         (novo — FASE 1)
.github/workflows/release.yml            (novo — FASE 1)
docs/reports/STEVE_LAUNCHER_GITHUB_RELEASE_PHASE1.md  (este relatório)
```

`docs/reports/STEVE_LAUNCHER_ARCHITECTURE_DECISION.md` foi criado numa etapa anterior
(decisão arquitetural) e é commitado junto por ainda estar pendente de commit — não foi
alterado nesta fase.

Confirmado via `git diff --name-only HEAD` (antes de qualquer commit): vazio — ou seja,
**nenhum arquivo rastreado foi modificado**, só criação de arquivos novos.

## 4. Contrato implementado (FASE 0)

Ver o documento completo em
[`STEVE_UPDATE_CONTRACT.md`](STEVE_UPDATE_CONTRACT.md). Pontos-chave:

- **VERSION**: arquivo único na raiz, SemVer, sem prefixo `v`. Valor inicial `1.6.0`.
- **RELEASE**: tag `vX.Y.Z` (com `v`), título `Steve AI vX.Y.Z`.
- **Assets**: `steve-ai-vX.Y.Z.zip`, `.zip.sha256`, `.zip.sig` — o `.sha256` separado foi uma
  decisão tomada nesta fase (formato padrão `sha256sum`), não estava no rascunho da decisão
  arquitetural anterior.
- **CHECKSUM**: SHA-256 do ZIP final, calculado depois de toda a montagem.
- **Assinatura**: Ed25519 via `cryptography`, chave privada só como GitHub Secret
  `STEVE_UPDATE_PRIVATE_KEY` (PEM), nunca em código/git/log/docs.
- **Downgrade**: remoto > local → atualiza; remoto == local → nada; remoto < local → rejeita
  sempre (implementação do lado que consome fica para a fase do Launcher).
- **Backup**: `launcher/backups/vX.Y.Z/`, fora do pacote distribuído (desenhado, não criado).
- **Health check / máquina de estados**: definidos conceitualmente no contrato; nenhum dos
  dois foi implementado em código nesta fase (isso pertence à fase do Launcher).
- **Source/Distribution/Runtime**: reafirmados — `git pull` não é o mecanismo de atualização
  do usuário final.

## 5. Workflow (`.github/workflows/release.yml`)

Gatilho: `push` de tag `v*.*.*`. Roda em **`windows-latest`** — decisão deliberada, não
default: o projeto usa `pywin32`, `pycaw`, `comtypes`, `winsound`, `ctypes.windll` em
`launcher/SteveLauncher.py`, `security/authenticode.py`, `settings/autostart.py`,
`settings/single_instance.py`, `system_monitor/gpu.py`, `tools/active_window.py`, entre
outros — sem platform markers no `requirements.txt`. Em `ubuntu-latest` o próprio
`pip install -r requirements.txt` falharia antes de chegar ao pytest.

Sequência real de passos:

1. `actions/checkout@v4`
2. `actions/setup-python@v5` (Python 3.11 — mesma versão do ambiente de desenvolvimento
   local, não havia `.python-version`/`runtime.txt` para herdar)
3. `pip install -r requirements.txt`
4. `python -m pytest` — **gate obrigatório**; se falhar, o job para aqui, nada é publicado.
5. `python scripts/build_release.py verify-tag --tag <tag>` — falha se `VERSION` ≠ tag.
6. `python scripts/build_release.py package --tag <tag> --output-dir dist_release` — monta
   e já valida o conteúdo do ZIP (forbidden-file check embutido); falha o job se algo
   proibido entrar.
7. `python scripts/build_release.py checksum <zip>` — grava `.sha256`.
8. `pip install -r scripts/requirements-release.txt` (só `cryptography`, só aqui).
9. `python scripts/build_release.py sign <zip>` — assina se `STEVE_UPDATE_PRIVATE_KEY`
   estiver configurado; se não estiver, sai com código 2 e imprime a mensagem exigida
   (`PIPELINE PREPARADO — ASSINATURA DE PRODUÇÃO PENDENTE DE SECRET`) sem falhar o job
   inteiro — um código de saída diferente de 0 e 2 (falha real de assinatura) continua
   falhando o job.
10. `actions/upload-artifact@v4` — sempre roda (`if: always()`), sobe o ZIP + `.sha256` (e
    `.sig` se existir) como artifact do workflow run, mesmo sem secret.
11. `gh release create` — **só roda `if: steps.sign.outputs.signed == 'true'`**. Usa o `gh`
    CLI (já pré-instalado nos runners hospedados do GitHub, autenticado via
    `GITHUB_TOKEN` automático) em vez de uma Action de terceiros — decisão deliberada de
    superfície de ataque mínima (menos uma dependência de marketplace para auditar).
12. Passo de resumo (`if: always()`) — imprime claramente se uma Release foi publicada ou
    se o pipeline ficou no estado "preparado, pendente de secret".

Nenhum passo do workflow foi projetado para mascarar, pular ou desabilitar testes — o
`python -m pytest` roda a suíte inteira, sem seleção de arquivos, sem `-k`, sem
`--ignore`, sem marks de skip introduzidos por mim.

## 6. Processo de build

`scripts/build_release.py package` usa `git ls-files` (mesma fonte que `git archive`
usaria) para decidir exatamente quais arquivos entram no ZIP — não existe uma lista
manual de inclusão para ficar desatualizada. Isso automaticamente já exclui tudo que o
`.gitignore` cobre (`.venv/`, `data/`, `logs/`, `__pycache__/`, `.pytest_cache/`,
`.serena/`, `.claude/`, `*.exe`, `launcher/dist/`, `launcher/build/`, arquivos soltos).

## 7. Processo de validação (segunda camada, independente do git)

Depois de montado, o ZIP é reaberto e cada nome de arquivo dentro dele é comparado contra
uma lista de padrões proibidos (`.venv/*`, `data/*`, `logs/*`, `__pycache__/*`, `*.pyc`,
`.pytest_cache/*`, `.serena/*`, `.claude/*`, `*.exe`, `launcher/dist/*`, `launcher/build/*`,
`.env`, `.env.*`, `*.pem`, `*secret*`, `*token*`, `*.key`, mais os 3 arquivos soltos já
listados no `.gitignore`). Qualquer correspondência levanta `ReleaseError` e falha o
`package` — testado com um ZIP deliberadamente envenenado (seção 9).

## 8. Processo de assinatura

Ed25519 via `cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PrivateKey`. A chave
é lida **somente** da variável de ambiente `STEVE_UPDATE_PRIVATE_KEY` (que no workflow vem
de `secrets.STEVE_UPDATE_PRIVATE_KEY`) — nunca de um arquivo, nunca de um argumento de
linha de comando (evita aparecer em logs de processo). Se a variável estiver vazia/ausente,
`sign_zip()` retorna `None` e imprime a mensagem de pendência exigida, sem tentar adivinhar,
gerar ou usar qualquer chave alternativa.

## 9. Assets gerados

Para a tag `v1.6.0` (build de teste local, nunca publicado): `steve-ai-v1.6.0.zip` (202
arquivos, ~1,22 MB), `steve-ai-v1.6.0.zip.sha256`, e `steve-ai-v1.6.0.zip.sig` (64 bytes,
gerado só com uma chave de teste local descartável — ver seção 10).

## 10. Testes realizados (locais, antes de qualquer commit)

Rodados manualmente, um por um, com resultado observado:

| # | Teste | Resultado |
|---|---|---|
| 1 | `verify-tag --tag v1.6.0` (VERSION=1.6.0) | OK — match confirmado |
| 2 | `verify-tag --tag v9.9.9` (mismatch proposital) | OK — rejeitado com erro claro |
| 3 | `package --tag v1.6.0` | OK — ZIP de 202 arquivos, validação de conteúdo passou |
| 4 | Inspeção do ZIP por script (`zipfile.namelist()`) | OK — 0 ocorrências de `data/`, `.venv/`, `logs/`, `__pycache__`, `.serena`, `.claude` |
| 5 | `checksum` | OK — `.sha256` gerado, formato `<hash>  <nome>` |
| 6 | `sign` sem `STEVE_UPDATE_PRIVATE_KEY` | OK — saiu com código 2 e imprimiu "PIPELINE PREPARADO — ASSINATURA DE PRODUÇÃO PENDENTE DE SECRET" |
| 7 | `sign` com chave Ed25519 de teste gerada localmente (descartável) | OK — `.sig` de 64 bytes criado; chave de teste apagada em seguida e confirmada ausente do disco (`find` não encontrou nenhum `.pem`) |
| 8 | ZIP deliberadamente envenenado com `data/steve.db` | OK — `validate_package_contents` rejeitou, apontando o arquivo e o padrão que bateu |
| 9 | YAML do workflow (`yaml.safe_load`) | OK — parseia sem erro, 12 steps no job `release` |
| 10 | `python -m pytest` completo (584 testes, 51 arquivos) | **576 passed, 3 failed, 4 skipped, 1 error** — ver seção 11 |

A chave de teste usada no item 7 foi gerada e apagada só para validar que o mecanismo de
assinatura funciona tecnicamente — em nenhum momento foi tratada como, nomeada como, ou
usada como a chave de produção. A chave de produção real nunca foi vista, pedida, gerada ou
manipulada por mim nesta fase.

## 11. Bloqueador pré-existente encontrado (fora do escopo desta fase)

Ao rodar o pytest completo pela primeira vez neste ambiente (exigido pelo workflow como
gate), a suíde retornou **3 failed, 576 passed, 4 skipped, 1 error** — não 584/584. Isso foi
investigado antes de decidir qualquer coisa sobre o commit:

- **`tests/test_security_engine.py::test_audit_logger_records_finding_created`** — falhou na
  corrida completa, mas **passou ao rodar isolado**. Timing de `threading.Event.wait(timeout=10)`
  correndo contra uma thread de análise em background — condizente com o padrão de
  instabilidade de ambiente já documentado neste projeto (contenção de threads sob carga
  prolongada de sessão), não uma regressão real.
- **`tests/test_desktop_window.py::test_window_creates_with_expected_title`** — falha
  **reproduzível** isolado: `window.title()` retorna `'STEVE'`, o teste espera `'Steve'`.
- **`tests/test_desktop_window.py::test_apply_status_updates_labels`** — falha
  **reproduzível** isolado: o label de status do modelo retorna um único caractere
  corrompido (`'�'`) em vez de conter `'qwen2.5'` — indício de um bug real de
  encoding/render na status bar.
- **`tests/test_desktop_window.py::test_send_via_entry_and_button_end_to_end`** — falha
  **reproduzível** isolado: `orchestrator.received` fica `[]` em vez de `['Oi Steve']` — o
  fluxo entry+botão não está acionando o Orchestrator como deveria.

**Confirmado que os 3 bugs reproduzíveis são pré-existentes e não relacionados a esta
fase**: `git diff --name-only HEAD` (antes de qualquer commit desta fase) não inclui
`ui/desktop/window.py`, `system_monitor/`, nem `security/engine.py` — nenhum arquivo dessas
áreas foi tocado por mim, nem nesta fase nem em nenhuma fase anterior desta sessão. São
bugs no código de UI (HUD/Workspace) construído por outra sessão entre 18 e 22/09, fora do
escopo desta tarefa, que a regra "não alterar UI/HUD/Workspace/Orb" proíbe explicitamente
que eu toque.

**Decisão tomada (autorizada explicitamente pelo usuário)**: os 3 bugs **não foram
corrigidos**, os testes **não foram alterados, mascarados, pulados ou desabilitados**, e a
UI **não foi tocada**. Eles ficam documentados aqui como bloqueador pré-existente, fora do
escopo da FASE 0+1, a ser tratado em uma tarefa futura dedicada à UI.

**Consequência direta**: o `python -m pytest` no passo 4 do workflow (seção 5) vai
**falhar** em qualquer execução real até que esses 3 bugs sejam corrigidos — o que é o
comportamento **correto e esperado** de um gate de qualidade, não um defeito do pipeline.
Isso significa que, na configuração atual do repositório, **nenhuma tag publicada hoje
resultaria numa Release real** — o job pararia no passo de testes, antes mesmo de chegar
ao empacotamento.

## 12. Auditoria de segurança

- `STEVE_UPDATE_PRIVATE_KEY` aparece em `scripts/build_release.py` e
  `.github/workflows/release.yml` **somente como nome de variável/secret**, nunca como
  valor — confirmado por busca textual dedicada nos dois arquivos antes do commit.
- Nenhum arquivo `.pem`, `.key`, `.env` ou similar existe em nenhum lugar do projeto
  (confirmado por busca recursiva, excluindo `.venv/`) — inclusive a chave de teste gerada
  para o item 7 da seção 10 foi apagada e a ausência foi reconfirmada.
- O ZIP de release nunca contém `.venv/`, `data/`, `logs/`, segredos, `.env`, tokens, chaves
  privadas, cache ou artefatos de build — validado tanto pela origem (`git ls-files`) quanto
  por uma segunda checagem independente sobre o conteúdo final do ZIP (seção 7), com teste
  negativo confirmando que a checagem realmente rejeita uma violação (seção 10, item 8).
- `gh release create` usa `GITHUB_TOKEN`, o token automático e escopado do próprio
  workflow run — nenhum PAT (personal access token) de longa duração foi criado ou é
  necessário.
- `permissions: contents: write` é o único escopo concedido ao job — nenhuma permissão
  adicional (issues, packages, etc.) foi solicitada.
- Actions de terceiros usadas: `actions/checkout@v4`, `actions/setup-python@v5`,
  `actions/upload-artifact@v4` — todas mantidas pela própria GitHub, pinadas por tag maior
  (prática padrão; fixar por SHA completo é um possível endurecimento futuro, não feito
  aqui). Nenhuma Action de marketplace de terceiro não-GitHub foi adicionada — a criação da
  Release usa o `gh` CLI, já presente nos runners hospedados, em vez de uma Action externa
  dedicada a isso.

## 13. Secrets necessários

| Secret | Status | Onde configurar |
|---|---|---|
| `STEVE_UPDATE_PRIVATE_KEY` | **NÃO CONFIGURADO** (não verificável/configurável por mim — decisão exclusiva do usuário no GitHub) | Settings → Secrets and variables → Actions, no repositório `bartolomeu7/steve-ai` |

Sem esse secret, o workflow (quando algum dia o pytest ficar verde) vai construir, testar,
empacotar e gerar checksum normalmente, subir os artefatos como workflow artifact, mas
**não vai assinar nem criar uma GitHub Release** — reportando explicitamente
`PIPELINE PREPARADO — ASSINATURA DE PRODUÇÃO PENDENTE DE SECRET`, nunca simulando sucesso.

## 14. O que NÃO foi implementado (deliberadamente, fora de escopo)

- Lógica do Launcher consumindo qualquer parte disso: checar versão, baixar asset, verificar
  assinatura durante a execução, aplicar atualização, rollback — **nada disso existe em
  código**, só o desenho conceitual no contrato (seção 4.7/4.8).
- `launcher/backups/` não foi criado.
- Chave pública Ed25519 não foi embutida em lugar nenhum (só a privada, como Secret,
  quando configurada, é usada — do lado do publicador).
- Nenhuma correção nos 3 bugs de UI da seção 11.
- Nenhuma alteração em `main`, `nexora-code-build-20260921`, UI/HUD/Workspace/Orb, voz,
  ferramentas ou memória.
- Nenhum daemon/serviço, nenhum tufup, nenhum updater completo.

## 15. Limitações conhecidas

- O pytest completo está vermelho por um motivo alheio a esta fase (seção 11) — até ser
  corrigido, o gate de testes bloqueia legitimamente qualquer Release real.
- `STEVE_UPDATE_PRIVATE_KEY` depende de uma ação manual do usuário no GitHub que está fora
  do alcance desta sessão.
- O pipeline nunca foi executado de verdade no GitHub Actions (nenhuma tag foi publicada) —
  toda a validação desta fase foi local, com os mesmos comandos que o workflow chama.
- Ao final desta fase, **Git for Windows foi removido do ambiente local** por um processo
  externo (IObit Uninstaller, `IObitUninstaler.exe`/`UninstallMonitor.exe`, iniciados
  22/09/2026 20:25:15, fora desta sessão) — `git.exe` deixou de existir/foi renomeado para
  `bash_IObitDel.exe` dentro de `C:\Program Files\Git\bin`. Isso impediu, no momento em que
  este relatório foi escrito, a execução do commit/push descritos na seção 16 — não é um
  problema no pipeline, no script ou no workflow em si, é um evento de ambiente concorrente,
  fora do controle desta sessão.

## 16. Próximos passos

1. Corrigir os 3 bugs de `ui/desktop/window.py` (fora desta fase, requer autorização
   explícita para tocar em UI).
2. Rodar o pytest completo de novo depois da correção — só faz sentido cortar uma tag real
   depois disso.
3. Usuário configurar `STEVE_UPDATE_PRIVATE_KEY` no GitHub (gerar localmente, nunca
   commitar, colar só na página de Secrets).
4. Confirmar que o Git for Windows está reinstalado/disponível neste ambiente antes de
   qualquer novo commit.
5. Só então: publicar a primeira tag real (`v1.6.0` ou a versão que fizer sentido no
   momento) e observar o workflow rodar de ponta a ponta no GitHub Actions de verdade.
6. FASE 2 (Launcher GitHub-aware, consumindo este pipeline) fica para depois — não iniciada
   nesta tarefa, por instrução explícita.

## 17. Commits, branch e estado final

- **Branch**: `steve-main-sync`.
- **Commit(s) desta fase**: pendente — ver seção 15/18 (bloqueado por ambiente).
- **Estado da branch remota**: inalterado nesta fase (`origin/steve-main-sync` continua em
  `2bab58e`, o mesmo de antes desta fase começar).
- **`main`**: intacta, não tocada.
- **`nexora-code-build-20260921`**: intacta, não tocada.

---

## 18. Status estruturado final

```
STATUS FASE 0: IMPLEMENTADA
STATUS FASE 1: IMPLEMENTADA (pipeline) / RELEASE DE PRODUÇÃO NÃO LIBERADA (pytest vermelho)
BRANCH: steve-main-sync
COMMIT(S): PENDENTE — bloqueado por Git for Windows ter sido removido do ambiente local
           por um processo externo (IObit Uninstaller) durante esta tarefa; nenhum commit
           foi feito ainda. Será feito assim que o git estiver disponível de novo,
           mediante confirmação do usuário.
ARQUIVOS ALTERADOS: VERSION, docs/reports/STEVE_UPDATE_CONTRACT.md,
                     scripts/build_release.py, scripts/requirements-release.txt,
                     .github/workflows/release.yml,
                     docs/reports/STEVE_LAUNCHER_GITHUB_RELEASE_PHASE1.md
                     (+ docs/reports/STEVE_LAUNCHER_ARCHITECTURE_DECISION.md, de fase
                     anterior, ainda pendente de commit)
VERSION: 1.6.0
WORKFLOW: .github/workflows/release.yml criado e validado (YAML parseia; 12 steps;
          testado localmente comando a comando) — nunca executado de verdade no GitHub
          Actions (nenhuma tag publicada)
PACOTE: steve-ai-v1.6.0.zip, 202 arquivos, ~1,22 MB, construído e validado localmente,
        0 arquivos proibidos
ASSETS: .zip / .zip.sha256 / .zip.sig — os três gerados e validados localmente (o .sig
        só com uma chave de teste descartável, apagada em seguida)
SHA-256: funcionando — testado, arquivo .sha256 gerado no formato sha256sum
ED25519: funcionando tecnicamente — testado com chave de teste local descartável;
         mecanismo de "ausência de secret" testado e correto (exit 2 + mensagem exigida)
SECRET NECESSÁRIO: STEVE_UPDATE_PRIVATE_KEY — NÃO CONFIGURADO (ação exclusiva do usuário
                    no GitHub)
TESTES: pytest completo = 576 passed, 3 failed, 4 skipped, 1 error (1467,84s). 3 falhas
        reproduzíveis são bugs pré-existentes em ui/desktop/window.py, fora do escopo
        desta fase, não corrigidos por instrução explícita. 1 falha adicional
        (test_security_engine) é flaky/ambiente, passou isolada.
AUDITORIA: realizada — ver seções 11 e 12
SEGURANÇA: nenhum segredo real exposto; nenhuma chave privada de produção manipulada;
           chave de teste gerada e apagada, ausência reconfirmada
UI: INTACTA — nenhum arquivo de ui/desktop/ tocado nesta fase
UPDATER: NÃO IMPLEMENTADO — consumo pelo Launcher fica para fase futura
MAIN: NÃO ALTERADA
NEXORA: NÃO ALTERADO — branch nexora-code-build-20260921 intacta
PENDÊNCIAS:
  1. Git for Windows precisa voltar a existir neste ambiente antes do commit+push
  2. Corrigir os 3 bugs de UI (fora desta fase) antes de qualquer Release real
  3. Usuário configurar STEVE_UPDATE_PRIVATE_KEY no GitHub
  4. Publicar a primeira tag real e observar o workflow rodar de ponta a ponta
PRÓXIMA FASE: NÃO INICIADA (FASE 2 — Launcher GitHub-aware — aguardando instrução
              explícita, conforme pedido)
```
