# Steve AI — Primeira Sincronização com o GitHub

**Data**: 2026-09-22
**Repositório**: [bartolomeu7/steve-ai](https://github.com/bartolomeu7/steve-ai)

---

## 1. Repositório utilizado

`https://github.com/bartolomeu7/steve-ai` — já existia (criado 09/09/2026), confirmado como o repositório correto do usuário (dono `bartolomeu7`, README com o mesmo texto-base do projeto local, mais antigo). Nenhum outro repositório foi tocado.

## 2. Branch criada

`steve-main-sync` — criada a partir do estado local atual (que não tinha nenhum commit), **sem compartilhar histórico** com `main` nem com `nexora-code-build-20260921`, exatamente como pedido ("não misturar o Steve com main antigo ou nexora-code-build").

## 3. Arquivo removido

`.github/workflows/build-nexora-code.yml`, na branch `nexora-code-build-20260921`.

## 4. Motivo da remoção

Pertencia a um fluxo separado ("NEXORA CODE" / sparse-checkout de source-pack), não ao projeto Steve AI. Remoção explicitamente autorizada pelo usuário na etapa anterior desta auditoria. Confirmado o caminho e a branch exatos via GitHub API antes de remover; nenhum outro arquivo dessa branch foi tocado.

**Commit da remoção**: `6577cde` (branch `nexora-code-build-20260921`, era `0ebe2df` antes — fast-forward, sem force push).

## 5. Arquivos incluídos

201 arquivos, 29.236 linhas — todo o código-fonte real do projeto: `ai/`, `core/`, `memory/`, `security/`, `settings/`, `system_monitor/`, `tools/`, `ui/`, `voice/`, `launcher/`, `learning/`, `docs/` (incluindo `docs/superpowers/`), `tests/`, mais `main.py`, `README.md`, `requirements.txt`, `pytest.ini`, `.gitignore`, `start_steve_optimized.bat`.

## 6. Arquivos excluídos do versionamento

- `.venv/` — ambiente virtual (regenerável via `requirements.txt`)
- `data/` — `config.json` (nome real do usuário) e `steve.db` (memória pessoal real) — **nunca deve ir para um repositório público**
- `logs/` — logs de aplicação/auditoria locais
- `__pycache__/`, `*.pyc`, `.pytest_cache/` — cache/bytecode
- `.serena/` — índice local de uma ferramenta, não é conteúdo do projeto
- `Steve.exe`, `SteveLauncher.exe`, `launcher/dist/`, `launcher/build/` — binários compilados (regeneráveis via PyInstaller, ~25 MB no total, não fazem sentido num repositório de código-fonte)
- `test_all_output.txt`, `test_output.txt`, `unused.wav` — arquivos soltos/incidentais na raiz, não fazem parte do projeto de fato

## 7. `.gitignore` utilizado

O `.gitignore` já existente cobria `__pycache__/`, `*.pyc`, `.pytest_cache/`, `data/`, `logs/`, `.venv/`, `venv/`. Foram adicionadas 4 seções novas (`.serena/`/`.claude/`, artefatos de build, arquivos soltos) — arquivo completo commitado junto com o primeiro commit.

## 8. Commit inicial

`1de42d6` — `"chore: initialize Steve AI current project"` — commit raiz da branch `steve-main-sync`, sem histórico anterior.

## 9. Push realizado

`git push -u origin steve-main-sync` — sucesso, sem force, criou a branch nova no remoto.

## 10. Estado local

- Branch: `steve-main-sync`
- HEAD: `1de42d6`
- Working tree: limpo (`git status --short` vazio após o push — nada além do que foi commitado)

## 11. Estado remoto

- `origin/steve-main-sync` → `1de42d6` (idêntico ao local, confirmado via `git rev-parse`)
- `origin/main` → `8f9e2d1` (**inalterado**, confirmado via API)
- `origin/nexora-code-build-20260921` → `6577cde` (só a remoção do arquivo autorizado)

## 12. Launcher atual — auditoria (leitura completa de `launcher/SteveLauncher.py`)

- **Como inicia o Steve**: resolve a raiz do projeto procurando `main.py` + `.venv/Scripts/python.exe` nos diretórios pai (funciona com o `.exe` na raiz, em `launcher/` ou em `launcher/dist/`), depois `subprocess.Popen([python.exe, main.py])`.
- **Steve.exe vs SteveLauncher.exe**: os dois são o **mesmo binário compilado** a partir do mesmo `SteveLauncher.py` — não existe um "Steve.exe" com lógica própria diferente.
- **Tratamento de erro**: real — `MessageBoxW` do Windows para pasta não encontrada, `main.py` não encontrado, `python.exe` do venv não encontrado, exceção ao iniciar, e uma checagem de 1,5s para detectar se o processo morreu imediatamente. Tudo logado em `logs/launcher.log`.
- **Configuração própria do launcher**: **NÃO EXISTE** — nenhum arquivo de config, nenhuma versão registrada.
- **Detecção de versão**: **NÃO EXISTE**.
- **Consulta ao GitHub**: **NÃO EXISTE** — confirmado por leitura completa do arquivo, nenhum código de rede.
- **Separação runtime/atualização**: **NÃO EXISTE** — o launcher só executa o que já está no disco, sem nenhum conceito de "atualizar".
- **Rollback**: **NÃO EXISTE**.
- **Achado de duplicação (REUSE FIRST)**: o launcher tem sua própria checagem "Steve já está rodando?" (varredura de títulos de janela via `EnumWindows`), que é redundante com o `SingleInstanceLock` que o próprio `main.py`/`settings/single_instance.py` já implementa corretamente (lock de verdade, não heurística de título de janela). Não foi alterado nesta etapa — só registrado, conforme instruído (auditar, não implementar).

## 13. O que foi implementado no Launcher

**Nada.** Esta etapa foi só auditoria, conforme instruído ("Primeiro auditar... NÃO implementar tudo cegamente").

## 14. O que ficou para próxima fase

Tudo que envolve o Launcher consultar o GitHub, versionar, atualizar ou fazer rollback — nada disso existe hoje e nada foi criado agora. Fica para uma etapa futura, com decisão explícita sobre REUSE FIRST (avaliar ferramentas de update/deploy existentes antes de construir algo do zero).

## 15. Testes realizados

- `main.py` importa sem erro (`python -c "import main"`).
- `launcher/SteveLauncher.py::get_project_root()` resolve corretamente a raiz real do projeto e a valida como um projeto Steve legítimo.
- `git status --short` limpo após o push — nenhum arquivo de UI/interface foi tocado por esta tarefa (confirmável: as únicas edições desta etapa foram `.gitignore` e a criação deste relatório).

Bateria pesada de testes **não** foi executada, conforme instruído.

## 16. Resultado

Sincronização inicial concluída com sucesso. Local e remoto (`steve-main-sync`) estão idênticos. `main` e `nexora-code-build-20260921` preservados, só com a remoção pontual autorizada nesse último.

## 17. Segurança

Nenhum secret, API key, senha ou credencial foi incluído — verificado por varredura de padrões no diff completo que foi commitado (vazio). Memória pessoal (`data/steve.db`) e configuração com nome real do usuário (`data/config.json`) ficaram fora do repositório, conforme exigido.

## 18. Próximos passos

1. Decidir formalmente o que fazer com o `main` antigo (não foi tocado nesta etapa).
2. Se quiser, abrir um Pull Request de `steve-main-sync` para `main` — **não fiz isso automaticamente**, já que não foi pedido.
3. Evoluir o Launcher para ficar GitHub-aware (sync/update/repair) — só depois de uma pesquisa REUSE FIRST de ferramentas de update/deploy já existentes, como instruído.
4. Resolver a duplicação de "detecção de instância única" entre o Launcher e o `SingleInstanceLock` real, quando o Launcher for revisitado.
