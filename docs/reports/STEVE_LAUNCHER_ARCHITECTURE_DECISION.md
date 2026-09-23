# Steve AI — Decisão Arquitetural do Launcher

**Data**: 2026-09-22
**Status**: Decisão de arquitetura. **Nada foi implementado, instalado, commitado ou publicado nesta etapa.**

---

## 1. Estado atual do Launcher

`launcher/SteveLauncher.py` (auditado por leitura completa em duas etapas anteriores):

- Resolve a raiz do projeto procurando `main.py` + `.venv/Scripts/python.exe` nos diretórios pai (funciona com o `.exe` na raiz, em `launcher/` ou `launcher/dist/`).
- Lança `subprocess.Popen([python.exe, main.py])`, sem esperar nem supervisionar o processo depois.
- Não gerencia encerramento — sem `atexit`, sem `terminate`/`kill`, sem tratamento de sinal.
- Tem uma checagem própria (frágil, por título de janela via `EnumWindows`) de "já está rodando", redundante com o `SingleInstanceLock` real do app (mutex nomeado do Windows, `settings/single_instance.py`).
- **Não possui**: atualização, rollback, consulta ao GitHub, versionamento, configuração própria.

## 2. Problema que precisamos resolver

O Launcher hoje só sabe *iniciar* o Steve que já está no disco. Não existe nenhum caminho para: descobrir se há uma versão mais nova publicada, baixá-la, verificar sua integridade, aplicá-la com segurança e voltar atrás se algo der errado.

## 3. Requisitos

- Verificar versão local vs. versão disponível no GitHub.
- Baixar uma atualização de forma íntegra e autenticada (não só "confiar" no download).
- Fazer backup antes de aplicar.
- Aplicar de forma atômica (ou o mais próximo disso possível no Windows).
- Detectar falha e reverter (rollback).
- Não exigir reescrever o modelo atual do Steve (Python + runtime local + Ollama + HUD/Workspace).
- Não alterar a interface.

## 4. Modelo de distribuição

Separação explícita, conforme pedido:

- **SOURCE**: `github.com/bartolomeu7/steve-ai` (branch `steve-main-sync`) — o código-fonte completo, já publicado.
- **DISTRIBUTION**: ainda **não existe** um mecanismo formal — precisa ser decidido nesta etapa.
- **RUNTIME**: a instalação local do usuário (`.venv/`, `data/`, `logs/` — nunca versionados, conforme já implementado no `.gitignore`).

`git pull` **não é adequado como mecanismo final de atualização do usuário** — exigiria que o usuário final tivesse git instalado, um clone configurado corretamente, e não dá nenhuma garantia de integridade/assinatura por si só. Fica descartado como mecanismo de distribuição, mesmo sendo tecnicamente possível.

---

## 5. Opção A — tufup

**O que é**: biblioteca Python (MIT, [dennisvang/tufup](https://github.com/dennisvang/tufup), ~130 estrelas, ativamente mantida) construída sobre o **TUF** (The Update Framework — o mesmo modelo de segurança usado pelo PyPI, Docker, etc.).

**Como realmente funciona** (verificado, não presumido):
- tufup espera sua **própria estrutura de repositório**: `keystore/` (chaves de assinatura) + `repository/metadata/` + `repository/targets/` (pacotes/patches versionados) — confirmado no próprio repositório de exemplo oficial ([tufup-example](https://github.com/dennisvang/tufup-example)).
- Isso **não é** "anexar um zip a uma GitHub Release". É uma estrutura de metadados TUF própria, que precisa ser hospedada em algum lugar (poderia ser servida via GitHub Pages ou `raw.githubusercontent.com` apontando pra arquivos committados, mas isso é uma decisão de infraestrutura a mais, não algo que o tufup resolve sozinho).
- **Infraestrutura externa necessária**: sim, explicitamente — um "repositório TUF" separado do repositório de código-fonte, mais um keystore para gerenciar as chaves de assinatura (geração, rotação). Não encontrei uma integração oficial "tufup + GitHub Actions + GitHub Releases" documentada nos resultados desta pesquisa — teria que ser desenhada.
- Compatibilidade com PyInstaller: sim, é o caso de uso principal da biblioteca.
- Compatibilidade com o Launcher atual: exigiria reescrever boa parte da lógica de descoberta/execução para delegar ao cliente tufup.

**Segurança**: forte — assinatura, proteção contra rollback attack, múltiplos papéis de confiança (root/targets/snapshot/timestamp) é exatamente o que o TUF foi desenhado para resolver.

**Custo real**: gerenciar chaves + uma estrutura de repositório TUF própria é complexidade operacional genuína para um projeto de um usuário só.

## 6. Opção B — GitHub Releases (mecanismo próprio sobre a API/assets do GitHub)

**Fluxo**: `GitHub Actions → build → Release → asset versionado (zip) → Launcher baixa via API`.

- Corresponde exatamente ao fluxo que você desenhou.
- GitHub já calcula um checksum (SHA) por asset, acessível via API — dá pra verificar integridade de transporte sem nenhuma dependência nova.
- Autenticidade (garantir que o asset realmente veio de você, não só "não foi corrompido no caminho") exigiria uma camada extra — ex.: assinar o zip com uma chave própria (Ed25519) e publicar a assinatura como outro asset da mesma Release.
- `requests` já é dependência do projeto — nenhuma dependência nova exigida para o cliente.
- Rollback/backup/atomicidade ficam por conta de quem implementa (não vêm de graça).

## 7. Opção C — solução open source para PyInstaller + GitHub Releases

Pesquisa real, 3 candidatos concretos encontrados — **nenhum viável**:

| Projeto | Status real (verificado) |
|---|---|
| PyUpdater | ❌ Arquivado/abandonado — mantenedor confirmou publicamente que parou de fazer apps desktop |
| updater4pyi | ❌ Abandonado — último push há ~8 anos |
| Esky | ❌ Arquivado pelo dono em 25/02/2018 — e nem suporta PyInstaller nativamente (só py2exe/py2app/cxfreeze/bbfreeze) |

**Conclusão da Opção C**: não existe hoje uma biblioteca open source ativamente mantida, específica para "PyInstaller + GitHub Releases". Isso não é uma lacuna da pesquisa — é o estado real do ecossistema, confirmado por 3 tentativas de busca com resultados consistentes. Essa é uma informação real e útil por si só: qualquer caminho que não seja tufup vai exigir escrever a lógica de cliente por conta própria (o que aproxima a Opção C, na prática, da Opção D).

## 8. Opção D — implementação própria mínima

GitHub Releases (mesmo fluxo da Opção B) + lógica de cliente escrita para o Steve especificamente:
- Comparação de versão (`Settings` já tem um padrão de campos simples — um `version.txt`/campo no `steve_status` já existente serviria).
- Checksum SHA-256 (stdlib `hashlib`, GitHub já expõe o dele por asset).
- Assinatura Ed25519 (via `cryptography`, biblioteca madura e amplamente auditada — precisaria ser adicionada como dependência nova, mas é uma única lib, não um framework).
- Backup (copiar a pasta atual antes de aplicar) + aplicação atômica (baixar tudo para uma pasta temporária, só trocar depois de validar) + rollback (restaurar o backup se a validação pós-update falhar).

---

## 9. Comparação técnica

| Critério | A) tufup | B) GitHub Releases (próprio) | C) OSS dedicado | D) Implementação própria |
|---|---|---|---|---|
| Licença | MIT | — | (nenhum viável) | — |
| Atividade | Ativa | — | ❌ todos abandonados | — |
| Maturidade | Alta (TUF é padrão da indústria) | Média (você escreve a lógica) | N/A | Média |
| Assinatura | Sim, nativa | Precisa adicionar | N/A | Precisa adicionar |
| Integridade | Sim, nativa | Via checksum do GitHub | N/A | Via checksum próprio |
| Rollback | Sim, nativo | Precisa implementar | N/A | Precisa implementar |
| Atualização incremental (patch) | Sim, nativa | Não, por padrão (full download) | N/A | Não, a menos que implementado |
| Compatibilidade PyInstaller | Sim | Sim | N/A | Sim |
| Compatibilidade com o Launcher atual | Baixa (exige reestruturar) | Alta | N/A | Alta |
| Dependências novas | `tufup` + suas transitivas | Nenhuma (`requests` já existe) | N/A | `cryptography` (madura, comum) |
| Complexidade operacional | Alta (keystore + repo TUF próprio) | Baixa-média | N/A | Média |
| Tamanho conceitual | Framework completo | Biblioteca fina + lógica própria | N/A | Lógica própria |
| Facilidade CI/CD | Precisa workflow específico do tufup | GitHub Actions padrão (`actions/create-release`) | N/A | GitHub Actions padrão |
| Facilidade de publicação via GitHub | Indireta (via repo TUF separado) | Direta (é literalmente Releases) | N/A | Direta |

## 10. Segurança

Distinguindo, como pedido:

**Segurança criptográfica** (autenticidade + integridade fortes, resistência a ataque de downgrade):
- Opção A resolve isso de fábrica.
- Opções B/D resolvem integridade de transporte "de graça" (checksum do GitHub), mas autenticidade real (garantir que o pacote é seu, não de alguém que comprometeu a conta/CDN) exige assinatura própria — viável, mas não vem pronta.
- Proteção contra rollback attack (alguém forçar uma versão antiga e vulnerável) não vem de graça em B/D — precisaria de um número de versão monotônico assinado, não só comparar strings.

**Confiabilidade operacional** (energia cai no meio, processo ainda aberto, arquivo bloqueado, atualização parcial):
- Nenhuma das 4 opções resolve isso "de graça" no Windows — travamento de arquivo (`.exe`/`.pyc` em uso) é um problema real do SO, não de biblioteca. A estratégia comum (baixar tudo numa pasta nova, trocar o ponteiro/pasta ativa só no final, nunca sobrescrever arquivos em uso) funciona igual em qualquer das opções — é desenho de processo, não escolha de biblioteca.

## 11. Custo operacional

- A: manter um repositório TUF + keystore pelo resto da vida do projeto.
- B/D: manter só o workflow de Release no próprio repositório do Steve — nenhuma infraestrutura extra.

## 12. Dependências

- A: `tufup` + transitivas (não investigadas em detalhe nesta etapa).
- B: nenhuma nova.
- D: `cryptography` (1 dependência nova, madura e já comum no ecossistema Python/Windows).

## 13. Compatibilidade

Todas as 4 são compatíveis com Windows e PyInstaller em teoria. B e D são as que menos exigem mudar a estrutura já existente do Launcher.

## 14. Arquitetura proposta

**Recomendação: Opção D** — GitHub Releases como mecanismo de distribuição (igual ao fluxo que você desenhou), com uma camada mínima própria de verificação (checksum + assinatura Ed25519) e backup/rollback, integrada ao Launcher existente sem reescrevê-lo.

```
GitHub (steve-main-sync, ou uma branch/tag de release futura)
   │
   ▼
GitHub Actions (build + gera zip do runtime + assina)
   │
   ▼
GitHub Release (asset .zip + asset .sig + versão na tag)
   │
   ▼
Launcher: verifica versão local vs. tag mais recente (API pública, sem auth)
   │
   ├── atualizado → inicia o Steve normalmente (fluxo atual, inalterado)
   │
   └── atualização disponível
          │
          ▼
       backup (copia a pasta atual do Steve para uma pasta de versão anterior)
          │
          ▼
       download (zip para uma pasta temporária, nunca sobrescreve arquivos em uso)
          │
          ▼
       verify (checksum SHA-256 + assinatura Ed25519 contra uma chave pública fixa no launcher)
          │
          ▼
       apply (só troca a pasta ativa depois de tudo validado)
          │
          ▼
       validate (o launcher consegue iniciar o Steve novo e ele passa por um health-check mínimo — ex.: importa sem erro)
          │
     ┌────┴────┐
     │         │
    OK      FALHA
     │         │
     ▼         ▼
   inicia   rollback (restaura o backup, inicia a versão anterior, avisa o usuário)
```

## 15. Fluxo de atualização

Descrito na seção 14. Sempre full-download (sem patch incremental) — mais simples, aceitável para o tamanho atual do projeto (código-fonte é pequeno; `.venv`/modelos não fazem parte do pacote de atualização).

## 16. Fluxo de rollback

Backup da pasta ativa ANTES de qualquer substituição. Se a validação pós-update falhar (Steve novo não inicia/não passa no health-check mínimo), o launcher restaura o backup automaticamente e informa o usuário — nunca deixa o Steve num estado "meio atualizado".

## 17. Fluxo de falha

- Download incompleto/corrompido → checksum falha → não aplica, mantém a versão atual, avisa o usuário.
- Assinatura inválida → não aplica, avisa o usuário (isso pode indicar um asset adulterado — tratar como evento de segurança, não só erro comum).
- Energia cai durante o download → arquivo temporário incompleto, nunca chega a "apply" → próxima execução do launcher detecta e descarta o download parcial.
- Energia cai durante o "apply" → é o ponto mais delicado; mitigado fazendo o "apply" ser uma troca de pasta/ponteiro (renomear pasta nova → ativa) em vez de sobrescrever arquivo por arquivo, o que reduz bastante a janela de risco no NTFS.

## 18. GitHub Actions necessário

Um workflow novo (ainda não criado) que, ao criar uma tag/Release:
1. Roda os testes (`pytest`).
2. Empacota o código-fonte relevante (o mesmo conjunto de pastas já definido no `.gitignore`/commit inicial).
3. Assina o pacote com uma chave privada guardada em GitHub Secrets.
4. Publica o zip + a assinatura como assets da Release.

## 19. Estrutura de releases

Uma tag por versão (ex. `v1.6.0`), Release do GitHub associada, 2 assets: `steve-ai-vX.Y.Z.zip` + `steve-ai-vX.Y.Z.zip.sig`.

## 20. Estrutura de versionamento

Semântico (`MAJOR.MINOR.PATCH`), guardado num único lugar (ex. um arquivo `VERSION` na raiz, já que hoje não existe nenhum — `tools/steve_status.py` poderia passar a ler dali em vez de não ter versão nenhuma).

## 21. Estrutura local

Sem mudança no que já existe: `.venv/`, `data/`, `logs/` continuam fora do pacote de atualização (são estado do usuário, não código). O launcher passaria a manter uma pasta de backup (ex. `launcher/backups/`, fora do controle de versão).

## 22. C: vs G:

Sem impacto direto — nem código-fonte nem os backups do launcher têm relação com o G:\Grok\SteveLearning (que é dado de aprendizado do usuário, não parte do runtime a ser atualizado). Os backups do launcher ficariam em C:, junto do resto do projeto, a menos que o espaço em disco (já apertado, ~7 GB livres conforme auditoria anterior) exija reconsiderar isso — vale monitorar.

## 23. Decisão

**Opção D** (GitHub Releases + verificação própria mínima), não a A (tufup).

## 24. Justificativa

- Corresponde exatamente ao fluxo que você já desenhou (GitHub Actions → Release → Launcher), sem inventar infraestrutura nova.
- Não exige manter um repositório TUF + keystore separados — para um projeto de um usuário só, esse custo operacional contínuo não parece proporcional ao ganho.
- Não exige reescrever o Launcher existente — só estende o que já existe.
- A segurança real necessária (garantir que o pacote é seu e não foi adulterado) é alcançável com uma assinatura Ed25519 simples, sem precisar do aparato completo do TUF (múltiplos papéis de confiança, rotação de chave formal) — que foi desenhado para um modelo de ameaça diferente (muitos usuários, servidor de atualização potencialmente comprometido por terceiros).
- Opção C não existe de fato (todos os candidatos reais estão abandonados) — não há "meio-termo pronto" para adotar.

**Onde ainda há dúvida real, sem esconder**: não investiguei a fundo se a opção A poderia usar GitHub Pages como o "repositório TUF" de forma mais simples do que presumi (existe algum guia oficial pra isso que eu não encontrei nesta pesquisa) — se você quiser a segurança mais forte do TUF e não se importar com a complexidade operacional extra, vale uma segunda rodada de pesquisa focada só nisso antes de descartar de vez a Opção A.

## 25. O que NÃO deve ser implementado

- Nada de código do launcher/updater foi escrito nesta etapa.
- Nenhuma dependência nova foi instalada (`cryptography` incluída).
- Nenhum workflow do GitHub Actions foi criado.
- Nenhuma tag/Release foi criada.
- A interface do Steve não foi tocada.

## 26. Próxima etapa

Aguardando autorização explícita para implementar. Se autorizado, a ordem lógica seria: (1) criar um `VERSION` simples, (2) escrever o workflow de Release, (3) escrever a lógica de verificação/backup/rollback no launcher, (4) testar todo o fluxo de falha antes de testar o fluxo de sucesso.
