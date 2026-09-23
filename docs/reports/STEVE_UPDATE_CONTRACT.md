# Steve AI — Contrato de Atualização

**Status**: contrato arquitetural (FASE 0). Implementa a **Opção D** decidida em
[`STEVE_LAUNCHER_ARCHITECTURE_DECISION.md`](STEVE_LAUNCHER_ARCHITECTURE_DECISION.md) —
GitHub Releases + verificação própria (SHA-256 + Ed25519), sem tufup.

Esta etapa (FASE 0 + FASE 1) define o contrato e constrói o **pipeline de publicação**
(GitHub Actions → build → assinatura → Release). **O Launcher não consome nada disso
ainda** — consultar versão, baixar, verificar e aplicar ficam para uma fase futura.

---

## 4.1 VERSION

Fonte única de versão: arquivo `VERSION` na raiz do projeto, formato `MAJOR.MINOR.PATCH`
(SemVer), sem prefixo `v`, sem quebra de linha extra além da própria versão.

Auditoria feita antes de criar: **não existe nenhum outro mecanismo de versão hoje**
(`grep` por `__version__`/`VERSION =` em `main.py`, `settings/config.py`,
`tools/steve_status.py` não encontrou nada) — não há nada para consolidar, é um ponto de
partida limpo.

Valor inicial: `1.6.0` (mesmo número usado como exemplo na decisão arquitetural, marca o
primeiro estado "publicável" do projeto pós-sincronização com o GitHub).

Consumidores futuros (fora de escopo desta fase, só para registro): `tools/steve_status.py`
deveria passar a ler esse arquivo em vez de não reportar versão nenhuma; o Launcher, quando
ganhar a etapa de update, também lê daqui — mas nenhum dos dois foi alterado nesta etapa.

## 4.2 RELEASE

- **Tag**: `vX.Y.Z` (ex.: `v1.6.0`) — sempre com o prefixo `v`, ao contrário do arquivo
  `VERSION`, que não tem.
- **GitHub Release**: título `Steve AI vX.Y.Z`.
- **Assets**: `steve-ai-vX.Y.Z.zip`, `steve-ai-vX.Y.Z.zip.sig`, `steve-ai-vX.Y.Z.zip.sha256`
  (arquivo de checksum separado, formato descrito em 4.3 — decidido nesta fase, não estava
  no rascunho original, mas é a forma padrão de publicar um checksum verificável sem exigir
  que quem for conferir recalcule tudo manualmente).
- Nenhum manifesto adicional foi necessário além desses 3 assets.

## 4.3 CHECKSUM

SHA-256 do ZIP final, calculado **depois** de toda a montagem do pacote (nunca de um
estado intermediário).

**SHA-256 detecta corrupção/modificação do arquivo comparado ao hash esperado. SHA-256
NÃO substitui assinatura criptográfica** — um SHA-256 sozinho só prova "este arquivo é
bit-a-bit igual ao que eu calculei", não prova "isso veio de mim". Por isso os dois
mecanismos (checksum + assinatura) coexistem no pacote, com papéis diferentes.

Formato do arquivo `.sha256`, mesmo padrão que `sha256sum` produz:
```
<hash>  steve-ai-vX.Y.Z.zip
```

O cliente de verificação (Launcher) **não foi implementado nesta fase** — só o lado que
gera o checksum.

## 4.4 Assinatura Ed25519

- **Algoritmo**: Ed25519 (via `cryptography`, biblioteca Python madura e amplamente
  auditada — única dependência nova desta fase, só usada no workflow de build, não no
  runtime do Steve em si).
- **Chave privada**: existe **somente** como GitHub Secret (`STEVE_UPDATE_PRIVATE_KEY`,
  formato PEM). Nunca em código, nunca no Git, nunca dentro do ZIP, nunca impressa em log,
  nunca dentro de `docs/`.
- **Chave pública**: será embutida no Launcher quando a fase de verificação for
  implementada (fora de escopo agora) — a chave pública, ao contrário da privada, é segura
  para distribuir junto com o próprio código.
- **Assinatura**: asset separado `steve-ai-vX.Y.Z.zip.sig` (bytes brutos da assinatura
  Ed25519 sobre o conteúdo do ZIP).

**CONFIGURAÇÃO MANUAL NECESSÁRIA** (não foi e não deveria ser feita automaticamente):

| Item | Valor |
|---|---|
| Nome do secret | `STEVE_UPDATE_PRIVATE_KEY` |
| Formato esperado | Chave privada Ed25519 em PEM (`-----BEGIN PRIVATE KEY-----...`) |
| Onde configurar | Settings → Secrets and variables → Actions, no repositório `bartolomeu7/steve-ai` no GitHub |
| Como gerar (fora deste pipeline, localmente, nunca commitado) | `cryptography`: `Ed25519PrivateKey.generate()` + serializar em PEM |
| Como validar sem expor a chave | O workflow falha explicitamente (`STEVE_UPDATE_PRIVATE_KEY ausente`) se o secret não estiver configurado — nunca imprime nem tenta adivinhar um valor |

Sem esse secret configurado, o pipeline **não publica** — ver seção "Critério especial"
no relatório da FASE 1.

## 4.5 Política de downgrade

Uma versão mais antiga **não** deve ser aceita automaticamente só porque tem assinatura
válida (assinatura prova autenticidade, não recência).

Comparação por SemVer completo (`MAJOR.MINOR.PATCH`, tupla de inteiros, não comparação de
string).

| Cenário | Ação (definida agora; implementação fica para a fase do Launcher) |
|---|---|
| Versão remota > versão local | Atualização normal |
| Versão remota == versão local | Nada a fazer |
| Versão remota < versão local | Rejeitar — nunca aplicar downgrade automático |
| Release sem assinatura válida | Rejeitar, tratar como possível adulteração |
| Release malformada (VERSION ≠ tag, assets faltando) | Rejeitar — o workflow desta fase já impede que isso chegue a existir como Release pública (ver FASE 1) |

## 4.6 Backup

Estrutura futura (não criada nesta fase — só o desenho):
```
launcher/
    backups/
        v1.5.0/
            ... (cópia da instalação anterior)
```
`launcher/backups/` ficaria **fora** do pacote distribuível (nunca vai para dentro do
ZIP de release — é um diretório local, específico de cada instalação, não conteúdo
publicado). Avaliação da estrutura atual: `launcher/` hoje só tem `dist/`/`build/`
(artefatos do PyInstaller, já ignorados no `.gitignore`) — nenhum conflito de nome.

## 4.7 Health check

Definição conceitual (implementação fica para a fase do Launcher):

- Arquivos essenciais presentes (`main.py`, os pacotes `core/`/`ai/`/etc.).
- `.venv/Scripts/python.exe` existente e executável.
- Importação mínima bem-sucedida (`python -c "import main"`, sem exceção).
- Inicialização mínima do Steve sem erro fatal imediato (mesmo critério que o Launcher
  atual já usa para detectar "iniciou e morreu na hora" — ver
  `launcher/SteveLauncher.py::main()`, checagem de 1,5s).

## 4.8 Estados da atualização

Máquina de estados conceitual (documentada agora; o Launcher **não implementa nenhum
desses estados nesta fase** — hoje ele só faz `IDLE → inicia o Steve`):

```
IDLE
  │
  ▼
CHECKING ──────────────► FAILED (rede indisponível, GitHub inacessível)
  │
  ▼
UPDATE_AVAILABLE (ou volta a IDLE se já está na versão mais recente)
  │
  ▼
DOWNLOADING ───────────► FAILED (download incompleto/interrompido)
  │
  ▼
VERIFYING ─────────────► FAILED (checksum ou assinatura inválidos — tratar como possível
  │                                adulteração, não só "erro comum")
  ▼
BACKING_UP ────────────► FAILED (sem espaço em disco, arquivo em uso)
  │
  ▼
APPLYING ──────────────► FAILED → ROLLBACK
  │
  ▼
VALIDATING ────────────► FAILED → ROLLBACK
  │
  ▼
SUCCESS                              ROLLBACK → ROLLED_BACK (restaura o backup, informa o usuário)
```

## 4.9 Source / Distribution / Runtime

- **SOURCE**: `github.com/bartolomeu7/steve-ai`, branch `steve-main-sync` (nesta fase —
  qual branch vira a oficial de release fica em aberto, não decidido aqui).
- **DISTRIBUTION**: GitHub Releases (assets de um repositório `steve-ai`, não um serviço
  externo).
- **RUNTIME**: instalação local do usuário, inalterada por este contrato.

O pacote distribuído **nunca** contém: `.venv/`, `data/`, `logs/`, modelos locais, cache,
segredos, credenciais, dados pessoais, arquivos temporários, artefatos de build
desnecessários — mesma lista já usada na sincronização inicial (ver
`STEVE_GITHUB_INITIAL_SYNC.md`), reafirmada aqui como parte formal do contrato.

`git pull` **não é** o mecanismo de atualização do usuário final — confirmado como
decisão definitiva, não só recomendação.

---

Ver [`STEVE_LAUNCHER_GITHUB_RELEASE_PHASE1.md`](STEVE_LAUNCHER_GITHUB_RELEASE_PHASE1.md)
para a implementação do pipeline que publica releases seguindo este contrato.
