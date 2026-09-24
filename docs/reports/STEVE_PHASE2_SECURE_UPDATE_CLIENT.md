# Steve AI — FASE 2: Secure Update Client / Launcher Update Engine

**Data**: 2026-09-23
**Branch**: `steve-main-sync`

---

## 1. Objetivo

Implementar o cliente de atualização do Launcher — a metade que **consome** o pipeline já
publicado na FASE 0/1 (`VERSION` + `docs/reports/STEVE_UPDATE_CONTRACT.md` +
`scripts/build_release.py` + `.github/workflows/release.yml`): ler a versão local, consultar
a versão publicada no GitHub, comparar, baixar, verificar (SHA-256 + Ed25519), preparar em
staging, fazer backup, aplicar, checar saúde, e reverter automaticamente em caso de falha —
sem recriar um segundo sistema de atualização paralelo, sem tocar UI/HUD/Workspace, sem criar
tag/Release real.

## 2. Estado inicial

- `launcher/SteveLauncher.py`: só sabia iniciar o Steve já instalado — `get_project_root()` +
  `subprocess.Popen([python.exe, main.py])`. Sem versão, sem rede, sem update, sem rollback
  (confirmado por leitura completa, igual às auditorias anteriores).
- `VERSION` = `1.6.0`, `STEVE_UPDATE_CONTRACT.md` já definia o contrato completo (seção 4.8 —
  máquina de estados; seção 4.4 — Ed25519; seção 4.5 — política de downgrade) mas nada do lado
  do cliente existia em código.
- `docs/reports/STEVE_LAUNCHER_ARCHITECTURE_DECISION.md` já tinha decidido a **Opção D**
  (GitHub Releases + verificação própria), rejeitando tufup — esta fase reavalia essa decisão
  com pesquisa adicional (seção 3) antes de prosseguir com ela.
- `.venv` não tinha `cryptography` instalado de fato (só tinha sido instalado num Python fora
  do projeto durante a FASE 1) nem `packaging` declarado explicitamente (estava presente só
  como dependência transitiva).

## 3. Pesquisa REUSE FIRST

Pesquisa real via `gh repo view`/`gh api` (metadados de estrelas/atividade/licença direto do
GitHub) e `WebFetch` (conteúdo do README), não presumida.

| Projeto | Licença | Atividade | Windows | Python | PyInstaller | GitHub Releases | Assinatura | Rollback | Complexidade | Dependências | Adequação ao Steve |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **A) tufup** | MIT | 146★, último push 2025-10-04 (~11 meses) | Sim | Sim | Sim (caso de uso principal) | Indireta — exige repositório TUF próprio (`keystore/`+`metadata/`+`targets/`), não é "anexar zip numa Release" | Sim, nativa (TUF) | Sim, nativa | Alta (keystore + repo TUF a manter para sempre) | `tufup` + transitivas | Baixa — infraestrutura desproporcional para um projeto de um usuário só |
| **B) python-tuf** | Apache-2.0 | 1727★, muito ativo (push de ontem) | Sim | Sim (é a implementação de referência) | Não é o foco — framework de baixo nível | Não resolve isso sozinho — é só o motor de metadados TUF, a infra de repositório teria que ser construída do zero, mais trabalho que a Opção A | Sim, nativa | Fora do escopo do framework (ele versiona confiança, não aplica/reverte arquivos) | Muito alta — é o nível que o tufup já embrulha | `python-tuf` + transitivas | Pior que A para este caso: mesmo problema de infraestrutura, com mais trabalho manual |
| **C) desktop_app_source_updater** | MIT | **0★, 0 forks, 0 contributors**, criado 2026-07-01, último push 2026-08-06 (~7 semanas antes desta pesquisa) | Sim (público-alvo declarado) | Sim, 3.10+, **stdlib-only** | Sim, caso de uso principal | Sim, direto — é literalmente o mecanismo dele | **Não** — só SHA-256; o próprio README declara que a confiança vem de "sua conta GitHub + TLS", sem assinatura criptográfica sobre o payload | Sim — "backup-and-rollback transaction" | Baixa (zero deps) | Nenhuma | **Não adequado**: sem assinatura, o modelo de confiança conflita diretamente com o contrato já publicado (Ed25519 obrigatório); e maturidade/adoção comunitária essencialmente zero para um componente que roda com as permissões do usuário e pode substituir arquivos da aplicação |
| **D) Velopack** | MIT | 2345★, muito ativo (push de ontem) | Sim | Só via bindings — ferramenta nativa é C#/.NET/Rust | Não é o alvo — tem seu próprio empacotador/instalador | Sim, suporta | Sim, robusta | Sim, nativa | Média-alta — outro ecossistema de build inteiro | Toolchain .NET/Rust adicional | Baixa — mismatch de modelo (instalador/serviço) e de linguagem de build para um app PyInstaller puro-Python |
| **E) Implementação própria sobre GitHub Releases** (decisão já tomada) | — | — | Sim | Sim | Sim | Sim, direto | Precisa compor (Ed25519 via `cryptography`, já adicionada na FASE 1) | Precisa compor (escrito nesta fase) | Média — sem framework, lógica própria pequena e testável | `requests` (já existe) + `cryptography` (já existe) + `packaging` (nova, PyPA, madura) | **Alta** — mesmo fluxo já desenhado, sem infraestrutura nova, compatível com o contrato já publicado |

**Conclusão da pesquisa**: nenhuma das três opções pesquisadas (A/B/C) supera a decisão já
tomada em `STEVE_LAUNCHER_ARCHITECTURE_DECISION.md`, e a nova opção investigada nesta fase (C)
na verdade **reforça** essa decisão com uma razão concreta adicional — seu modelo de confiança
(sem assinatura) é incompatível com o contrato já publicado, e sua adoção comunitária é zero.
D permanece descartada pelo mesmo motivo da etapa anterior (mismatch de toolchain).

**REUSE FIRST aplicado corretamente não é "adote um framework maior"** — é reconhecer que a
decisão de arquitetura (Opção D) já foi tomada com justificativa própria, e então aplicar REUSE
FIRST **dentro** dela, subproblema por subproblema:

| Subproblema | Decisão | Motivo |
|---|---|---|
| Comparação de versão | **REUSE** `packaging.version.Version` (PyPA, a mesma lib que `pip`/`setuptools` usam) | Evita reimplementar um parser SemVer; comparação de string erraria `'1.10.0' < '1.6.0'` |
| Cliente HTTP | **REUSE** `requests` | Já é dependência do projeto |
| Checksum | **REUSE** stdlib `hashlib` | Nenhuma dependência nova necessária |
| Assinatura | **REUSE** `cryptography` (Ed25519) | Já adicionada na FASE 1 para o lado que assina; aqui só usa a metade pública (verify) |
| Extração de ZIP | **REUSE** stdlib `zipfile`, **BUILD** a validação de path-traversal/zip-bomb por cima | Não encontrei uma lib padrão amplamente adotada só para "extração segura"; a validação é pequena (~30 linhas) e diretamente testável |
| Backup/staging/apply/rollback | **BUILD** (específico do layout do Steve) | Nenhuma lib conhece de antemão que `data/`/`logs/`/`.venv/` do Steve nunca podem ser tocados — isso é, por natureza, específico da aplicação |

## 4. Solução escolhida

Opção D reafirmada — implementação própria sobre GitHub Releases, com verificação SHA-256 +
Ed25519, backup/apply/rollback e health check escritos especificamente para o layout do Steve.

## 5. Licença

Todas as bibliotecas reutilizadas (`packaging`, `cryptography`, `requests`) são licenças
permissivas (BSD/Apache-2.0/MIT-compatíveis), sem restrição de uso comercial ou de distribuição
para o Steve.

## 6. Justificativa

Ver seção 3 — nenhuma alternativa pesquisada é superior à combinação já decidida; a pesquisa
desta fase reforça a decisão anterior com um exemplo real, atual (`desktop_app_source_updater`)
de por que "código já pronto para GitHub Releases + PyInstaller" não é automaticamente
adequado sem avaliar seu modelo de confiança e maturidade.

## 7. Arquitetura

```
launcher/
    SteveLauncher.py          # ponto de entrada existente — só ganhou uma chamada
                               # não-bloqueante para check_for_update_best_effort()
    updater/
        __init__.py            # superfície pública (check_for_update, apply_update, ...)
        models.py               # UpdateState (enum, espelha o contrato 4.8), ReleaseInfo,
                                 # ReleaseAsset, UpdateResult
        version.py               # leitura de VERSION + comparação via packaging.version
        security.py               # SHA-256, Ed25519 (fail-closed), extração segura de ZIP
        fs.py                       # staging, backup, apply, rollback, health check
        client.py                    # orquestração: check_for_update / download_and_verify /
                                      # apply_update (com dry_run)
```

**Desvio deliberado da estrutura de referência do pedido** (`downloader.py`, `verifier.py`,
`staging.py`, `backup.py`, `rollback.py` como arquivos separados): consolidados em `security.py`
(tudo que é verificação/integridade) e `fs.py` (tudo que é operação de diretório —
staging+backup+apply+rollback são fortemente acoplados: o conjunto de payload de um é
exatamente o conjunto de payload dos outros três). O download em si é pequeno o bastante
(uma função) para viver dentro de `client.py`, que já orquestra o fluxo que o usa. Isso segue
a instrução explícita "evite abstrações vazias e arquivos desnecessários" — 6 arquivos, cada um
com responsabilidade real, em vez de 8 com alguns quase vazios.

## 8. Arquivos criados

```
launcher/updater/__init__.py
launcher/updater/models.py
launcher/updater/version.py
launcher/updater/security.py
launcher/updater/fs.py
launcher/updater/client.py
tests/test_update_client.py
docs/reports/STEVE_PHASE2_SECURE_UPDATE_CLIENT.md   (este relatório)
```

## 9. Arquivos modificados

- **`requirements.txt`**: +2 linhas (`packaging>=23`, `cryptography>=42`), com comentário
  explicando que ambas servem ao `launcher/updater/` e são construídas a partir do mesmo
  `.venv` do projeto (confirmado lendo `launcher/README.md` — o launcher é compilado via
  `pyinstaller` rodando dentro do mesmo venv do Steve, não um ambiente separado).
- **`launcher/SteveLauncher.py`**: +36 linhas, só adições — uma função
  `check_for_update_best_effort()` chamada de dentro de `main()`, antes de
  `steve_already_running()`. Roda em thread daemon (nunca bloqueia `launch_steve()`, nem pelo
  próprio timeout de rede) e só loga o resultado — não baixa, não aplica, não decide nada
  sozinha. Nenhuma linha existente foi removida ou alterada; `launch_steve()`,
  `get_project_root()`, `steve_already_running()`, `main()`'s fluxo de erro — todos
  inalterados.

Nenhum arquivo de `ui/`, `ai/`, `core/`, `voice/`, `memory/`, `security/`, `tools/`,
`learning/` foi tocado.

## 10. Por que a integração no Launcher é só "check", não "apply" automático

O pedido permite (seção 6: "determine exatamente onde o Update Client deve entrar") e a seção
17 pede um modo `dry-run` "importante antes da primeira Release". Decisão desta fase: o
Launcher agora dispara `check_for_update()` de forma assíncrona e best-effort a cada
inicialização (só loga o resultado em `logs/launcher.log`), mas **não** chama
`apply_update()` automaticamente. Motivo: `apply_update()` nunca foi exercitado contra uma
Release real (nenhuma foi publicada — nem pode ser, por instrução explícita desta fase), e
`TRUSTED_PUBLIC_KEY_PEM` está vazio (nenhuma chave de produção existe ainda — seção 12). Ligar
o apply automático agora seria, na prática, código morto (toda tentativa real cairia em
`UNTRUSTED_UPDATE` por falta de chave) e arriscaria comportamento não testado em produção
antes de qualquer validação end-to-end. Fica registrado como pendência explícita (seção 23),
não como esquecimento.

## 11. Segurança

- **Sem segredo real**: nenhuma chave privada, token ou credencial em nenhum arquivo novo —
  confirmado por busca dedicada (seção 21).
- **Fail-closed por padrão**: `security.verify_ed25519()` levanta `SecurityError` sempre que
  não há chave pública confiável configurada — nunca assume confiança, no mesmo espírito de
  `security/authenticode.py` (que retorna `None` em vez de arriscar um veredito errado).
- **Path traversal**: `security.safe_extract_zip()` rejeita entradas `../`, caminhos
  absolutos e caminhos com letra de unidade (`C:\`), testado com um ZIP malicioso real
  (seção 20).
- **Zip bomb**: limite de 200 MB de conteúdo descomprimido total (a release real tem ~1,2 MB —
  larga margem sem ser ilimitado).
- **Download**: limite de tamanho aplicado durante o streaming (`_download` aborta assim que
  excede `max_bytes`, não espera o download terminar para então rejeitar).
- **Dados do usuário nunca tocados**: `fs.py` só opera sobre o "payload de app" (código-fonte),
  nunca `data/`, `logs/`, `.venv/` — testado explicitamente (seção 20).

## 12. Version check

`launcher/updater/version.py`: lê `VERSION` do projeto e a tag da Release
(`vX.Y.Z` → `X.Y.Z`) com `packaging.version.Version` (PEP 440), não comparação de string —
testado explicitamente contra o caso clássico que quebraria com string (`'1.10.0'` vs
`'1.6.0'`, seção 20). `is_update_available()` só retorna `True` quando remoto > local — igual
não atualiza, remoto menor é rejeitado (contrato seção 4.5, política de downgrade).

## 13. Download

`client._download()`: streaming via `requests.get(..., stream=True)`, timeout de 60s, escreve
em staging (nunca sobre a instalação ativa), aborta e levanta erro se o corpo exceder o limite
de tamanho configurado.

## 14. Verificação

`client.download_and_verify()`: baixa `.zip` + `.sha256` + `.sig`, calcula SHA-256 do zip e
compara com o conteúdo do `.sha256` (mesmo formato que `scripts/build_release.py` gera —
`<hash>  <nome>`), depois verifica a assinatura Ed25519 do zip contra a chave pública
confiável. Qualquer uma das duas falhando aborta antes de qualquer extração ou aplicação —
nunca "confia porque veio do GitHub".

## 15. Staging

`fs.create_staging_dir()`: cria um diretório temporário isolado dentro de
`launcher/staging/` (nunca dentro da instalação ativa), usado tanto para os arquivos baixados
quanto para a extração do zip. `fs.cleanup_staging_dir()` remove tudo ao final (em `finally`,
mesmo se algo falhar no meio).

## 16. Backup

`fs.backup_current_install()`: copia o payload de app atual (excluindo `data/`, `logs/`,
`.venv/`, caches, e as próprias subpastas do updater —
`launcher/{dist,build,backups,staging}`) para `launcher/backups/v{versão_atual}/`, exatamente
como desenhado no contrato (seção 4.6).

## 17. Rollback

`fs.restore_backup()`: copia o backup de volta por cima do payload de app atual. Acionado
automaticamente por `client.apply_update()` sempre que a aplicação falha (erro de I/O) ou o
health check pós-update falha — nunca deixa a instalação "meio atualizada" (testado
explicitamente, seção 20).

## 18. Health check

`fs.health_check()`: `python.exe -c "import main"` com `cwd` no projeto, timeout de 20s —
rápido, não roda a suíte de testes inteira (conforme pedido explicitamente na seção 16 da
tarefa).

## 19. Dry-run

`client.apply_update(..., dry_run=True)`: baixa e verifica tudo normalmente, extrai o zip para
staging, mas retorna antes de qualquer chamada a `fs.backup_current_install()` ou
`fs.apply_update()` — nenhum arquivo da instalação ativa é tocado, nenhum backup é criado
(testado explicitamente, seção 20).

## 20. Testes

`tests/test_update_client.py`, 24 testes, todos com mocks/fixtures locais — nenhum depende de
uma Release real existir (que, aliás, ainda não existe: confirmado antes desta fase que não há
Release publicada). Segue a convenção já usada no projeto para simular `requests`
(`monkeypatch.setattr("modulo.requests.get", fake)`, igual a `tests/test_ai_provider.py`), sem
adicionar uma lib de mock de HTTP nova.

Cobertura dos 18 cenários pedidos na seção 21 da tarefa:

| # | Cenário pedido | Teste(s) |
|---|---|---|
| 1 | versão igual | `test_no_update_when_versions_are_equal`, `test_check_for_update_up_to_date` |
| 2 | versão mais nova | `test_update_available_when_remote_is_newer`, `test_check_for_update_update_available` |
| 3 | versão mais antiga | `test_no_update_when_remote_is_older_downgrade_rejected` |
| 4 | metadata inválida | `test_check_for_update_invalid_metadata_missing_assets`, `test_check_for_update_invalid_metadata_bad_tag` |
| 5 | timeout | `test_check_for_update_timeout_is_check_failed_not_raised` |
| 6 | download interrompido | `test_download_aborts_when_response_exceeds_max_bytes` |
| 7 | checksum correto | `test_download_and_verify_success` |
| 8 | checksum incorreto | `test_download_and_verify_rejects_bad_checksum` |
| 9 | assinatura válida | `test_download_and_verify_success` |
| 10 | assinatura inválida | `test_download_and_verify_rejects_bad_signature` |
| 11 | ZIP válido | `test_apply_update_end_to_end_success_then_rollback_on_bad_health` (primeira metade) |
| 12 | path traversal | `test_apply_update_rejects_path_traversal_package` |
| 13 | staging | `test_staging_dir_created_and_cleaned_up` |
| 14 | backup | `test_backup_excludes_data_and_venv` |
| 15 | health check | `test_health_check_true_for_importable_module`, `test_health_check_false_for_broken_module` |
| 16 | rollback | `test_apply_update_end_to_end_success_then_rollback_on_bad_health` (segunda metade) |
| 17 | offline | `test_check_for_update_offline_is_check_failed_not_raised` |
| 18 | dry-run | `test_apply_update_dry_run_does_not_mutate` |

Mais 6 testes extras (comparação de versão não-lexicográfica, `VERSION` ausente, "nenhuma
Release publicada ainda", verificação fail-closed sem chave configurada) — total **24
passed** localmente (ver seção 21 para o resultado da suíte completa do projeto).

## 21. Resultados

- `tests/test_update_client.py`: **24 passed** (0,33s).
- Segurança: busca por `api[_-]?key|token|password|secret|BEGIN.*PRIVATE KEY`
  (case-insensitive, via Grep) em todos os 6 arquivos novos de `launcher/updater/` — nenhuma
  ocorrência real (só o nome da variável `TRUSTED_PUBLIC_KEY_PEM`, vazia, e comentários
  explicando por quê).
- Suíte completa do projeto (`python -m pytest`, 584 + 24 = 608 testes): ver seção 23/relatório
  de auditoria final para o resultado exato — reexecutada nesta fase (não reaproveitada) porque
  `requirements.txt` e o Launcher foram alterados, o que exige confirmação de que nada regrediu.

## 22. Limitações

- `TRUSTED_PUBLIC_KEY_PEM` está vazio — nenhuma atualização real pode ser aplicada até uma
  chave de produção existir e ser embutida aqui (fail-closed, não um bug).
- `apply_update()` nunca foi exercitado contra uma Release/GitHub real, só contra fixtures
  locais — comportamento end-to-end real só será validado quando a primeira Release existir.
- O Launcher só faz `check_for_update()` automaticamente; `apply_update()` continua disponível
  como função de biblioteca, mas não é chamada automaticamente (seção 10).
- O `.exe` do Launcher **não foi recompilado** nesta fase — só o código-fonte
  (`SteveLauncher.py` + `updater/`) foi alterado/criado. Rebuild via PyInstaller e um teste de
  fumaça do binário resultante ficam pendentes (não pedido explicitamente nesta fase, que pediu
  "não redesenhar UI" e focou no motor, não na compilação).
- `fs.apply_update()`/`backup_current_install()` fazem cópia de arquivo, não uma troca atômica
  de ponteiro/pasta — no Windows, uma queda de energia exatamente no meio da cópia poderia
  deixar um arquivo individual pela metade (janela de risco pequena, mas não zero; documentado
  aqui em vez de reivindicar atomicidade perfeita que a implementação não tem).

## 23. Pendências

1. Gerar o par de chaves Ed25519 de produção (fora desta sessão, pelo usuário, localmente,
   nunca commitado) e embutir a chave **pública** em `TRUSTED_PUBLIC_KEY_PEM`
   (`launcher/updater/security.py`) — só então `apply_update()` deixa de falhar fechado por
   padrão.
2. Configurar o secret `STEVE_UPDATE_PRIVATE_KEY` no GitHub (mesma pendência já registrada nas
   fases anteriores).
3. Publicar a primeira tag/Release real e validar `check_for_update()` +
   `download_and_verify()` + `apply_update()` contra ela de ponta a ponta.
4. Decidir, só depois do item 3 validado, se/quando ligar `apply_update()` automaticamente no
   Launcher (hoje é só biblioteca disponível, não chamada automática).
5. Recompilar `SteveLauncher.exe` via PyInstaller incluindo o novo pacote `updater/` e suas
   dependências, com um teste de fumaça do binário.
6. Corrigir os 3 bugs pré-existentes de `ui/desktop/window.py` (fora do escopo de todas as
   fases desta sessão) — não bloqueiam a FASE 2 em si, mas continuam bloqueando qualquer
   Release real via o gate de pytest do workflow da FASE 1.

## 24. Conclusão

FASE 2 implementada como motor/biblioteca completo e testado (check → download → verify →
stage → backup → apply → health-check → rollback, mais dry-run), integrado ao Launcher
existente só no nível de checagem não-bloqueante, sem tocar UI, sem segundo sistema de
atualização paralelo, sem Release/tag real criada. Não é considerada "concluída" apenas por
este relatório existir — a auditoria (git diff, segurança, testes) e o commit/push (se
aprovados) são reportados separadamente, na resposta final desta tarefa.
