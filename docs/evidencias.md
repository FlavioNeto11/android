# Evidências, avatares e catálogo de APK: onde os arquivos ficam

> **Este arquivo descreve ONDE os arquivos de evidência ficam guardados** (storage: disco local × S3-compatível,
> formato de chave, interface `Storage`) — **não é o registro de provas**. Para "o que foi comprovado, quando e
> em qual máquina", ver [docs/relatorio-validacao.md](relatorio-validacao.md) (histórico datado) e
> `.claude/plano-100/estado.json`/[docs/execucao-plano-100-runner.md](execucao-plano-100-runner.md) (estado
> vigente por item do plano).

**Item 5.7 do plano-100 · achados #172 e #89.**

## O problema que isto resolve

Até aqui, a chave gravada em `evidence.path` era um **caminho relativo ao disco do processo que executou a
etapa**, e o painel lia o arquivo do disco do processo que atendia o `GET`. Numa máquina só, funciona.

No instante em que existe um **segundo backend** — topologia que a posse de etapa (016) e o `hosted_by` (027) já
admitem —, três coisas quebram de uma vez:

1. a evidência gravada por A responde **404 em B**, sem dizer por quê;
2. a retenção de B **apaga do banco compartilhado** as linhas cujos arquivos estão no disco de A — o arquivo
   continua lá ocupando espaço, e a prova da execução some sem ninguém ter apagado arquivo nenhum;
3. as linhas de `app_releases` apontam para APKs que só existem em quem importou, e B vê a release como
   `installable` e falha ao instalar.

Fora isso, a escrita era **síncrona dentro do laço de eventos**. Em disco local é inofensivo; com o destino na
rede, cada captura de tela travaria o scheduler inteiro.

## A interface

`backend/app/storage.py` tem uma interface (`put`/`get`/`stream`/`exists`/`delete_prefix`/`local_path`/`url`) e
dois back-ends:

| | `DiskStorage` | `S3Storage` |
|---|---|---|
| Onde | pasta local (`data/evidence`) | bucket S3-compatível (AWS, MinIO) |
| Como o painel serve | `FileResponse` do arquivo | redireciona para URL pré-assinada (5 min), ou streaming |
| Entre réplicas | **só quem gravou lê** | qualquer réplica lê |
| Dependência | nenhuma | `pip install boto3` |

**Chave, não caminho.** A chave é sempre `execução/aparelho/arquivo.jpg`, com barra normal, nos dois back-ends e
nos dois sistemas operacionais. As linhas antigas gravadas no Windows têm barra invertida; `normalizar_chave`
as converte na leitura, então **não há migração de dados** — o histórico abre como sempre abriu.

A travessia (`..`) é recusada na interface, num lugar só. `evidence.path` vem do banco, e o banco é
compartilhado entre réplicas: "veio do banco" nunca foi o mesmo que "é seguro abrir".

**A escrita sai do laço.** `Repository.add_evidence_async` joga o `put` numa thread (`asyncio.to_thread`) e só
o registro curto no banco fica no laço. O executor usa essa versão em todas as suas capturas.

## Duas colunas novas (migração 030)

- `evidence.storage` — `disk` ou `s3`. Sem ela, um parque que migra no meio do caminho não sabe se a linha
  antiga aponta para a pasta ou para o bucket.
- `evidence.stored_by` — o `OWNER_ID` de quem gravou, quando é disco. É o que faz a **retenção parar de apagar
  linha de arquivo alheio**: em disco, a linha vence no dono, junto com o arquivo. Com `s3` a pergunta não se
  aplica, e qualquer réplica apaga.

Nulo nas duas = linha anterior à migração: disco, de quem estava rodando. Com um backend só, nada muda.

**Quem manda é a LINHA, não a configuração do processo.** Num parque que ligou o S3 no meio do caminho, a
linha antiga continua em `disk` e continua sendo servida do disco — mandá-la para o bucket devolveria uma URL
pré-assinada de um objeto que não existe, ou seja, um 307 para um 404. Pela mesma razão, uma réplica ainda em
disco **não apaga** as linhas marcadas como `s3`: só apaga a linha quem consegue apagar o arquivo, senão o
objeto fica órfão no bucket e a prova some do banco.

Dois 404 com nome, no lugar do 404 mudo que mandava o operador procurar defeito na retenção:
`em_outro_servidor` (está no disco da réplica X) e `storage_nao_configurado` (a linha aponta para um back-end
que este backend não tem ligado).

**As chaves que já existem foram conferidas.** As 982 linhas de `evidence` com arquivo, os 8 ids de perfil
usados como chave de avatar e todos os `catalog_dir` de `app_releases` passam por `normalizar_chave` sem uma
única recusa — a classe de caracteres aceita cobre o histórico real, e nenhuma delas vira 404 ao migrar.

## Ligar o storage compartilhado

```
# no .env
EVIDENCE_STORAGE=s3
S3_ENDPOINT_URL=http://127.0.0.1:9000      # vazio = AWS
S3_BUCKET=parque-evidencias
S3_ACCESS_KEY_ID=…
S3_SECRET_ACCESS_KEY=…
pip install boto3
```

Um MinIO local serve, e é o que o teste de contrato descreve:

```
docker run -d --name minio -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=… -e MINIO_ROOT_PASSWORD=… \
  quay.io/minio/minio server /data --console-address ":9001"
```

Depois, para as evidências que **já existem em disco**:

```
python -m app.tools.upload_evidence --conferir     # relata, não grava
python -m app.tools.upload_evidence                # copia e carimba storage/stored_by
```

A ferramenta **não apaga o arquivo local**: o disco é a cópia de segurança até alguém conferir uma evidência
antiga pelo painel.

**Estado hoje: este parque roda com `EVIDENCE_STORAGE=disk`.** O back-end S3 está escrito e coberto por teste de
contrato contra um cliente em memória; ele **não foi exercitado contra um MinIO nem contra a AWS** nesta
máquina, porque não há nenhum dos dois aqui. O que falta para fechar é isso, e só isso.

## Avatares

Vão para o mesmo storage, sob `avatars/<id>.jpg`. Em disco a raiz é `data/`, então o arquivo continua
exatamente onde sempre esteve (`data/avatars/<id>.jpg`): não há nada a mover.

## Catálogo de APK (achado #89)

O catálogo é o mesmo problema, com uma consequência pior: um APK ausente vira `install_failed` por aparelho pela
entrega automática, e rearmar no mesmo backend falha de novo.

- **Com storage compartilhado**, o conjunto importado é publicado nele (`_publicar_no_catalogo`) e a outra
  réplica o **baixa para o cache local** na hora de instalar, conferindo o `sha256` já gravado no banco —
  no mesmo ponto de sempre: o que vem do bucket não ganha confiança extra por ter vindo de lá.
- **Sem storage compartilhado**, a mensagem passa a distinguir os dois casos, que antes eram confundidos:
  - *"Arquivo ausente NESTE servidor"* — está em outra máquina, continua íntegro lá, e a release **permanece
    `installable`**, porque ela é;
  - *"Artefato adulterado"* — o hash mudou no disco, e aí sim a release vira `invalid` e exige gente.

**Lacuna conhecida:** `_publicar_no_catalogo` sobe o conjunto para o bucket de forma SÍNCRONA, dentro da
importação. Importar é ação do operador, rara e já lenta (aapt2 + apksigner em cada arquivo), então não é o
mesmo caso das capturas de tela — mas, se a importação passar a ser chamada de um caminho assíncrono quente,
esta escrita precisa ir para `to_thread` como a das evidências já foi.

Ver também `docs/banco.md`, seção "Dois backends no mesmo banco", que agora lista `apks/` entre o que precisa
acompanhar o banco num backup ou numa restauração.

## Logs de emulador

Continuam em `data/logs/`, e **continuam locais de propósito**: eles são diagnóstico do processo daquela
máquina, não prova de execução. A cauda que interessa ao operador já sobe no `result` do comando
(`commands.store._log_do_emulador`) e, por isso, atravessa réplicas pelo banco, sem arquivo nenhum.

## O que conta como "tela sensível" (item 9.5 · achado #127)

Uma tela sensível não vira JPEG em `data/evidence` e não entra no corpo da requisição ao provedor de IA — nem
como imagem, nem, quando é um código, como texto. Por muito tempo o critério foi **um só**: existir na árvore um
elemento com `password="true"`. O aviso do painel dizia isso com todas as letras ("Telas com campo de senha nunca
são enviadas"), e era literalmente verdade — o problema é que tela sensível sem campo de senha é a regra, não a
exceção: desafio de 2FA, código por e-mail, dados da conta, conversa de terceiro, a tela da VM-loja com a conta
Google do parque.

Hoje `automation/hierarchy.parse_hierarchy` aplica quatro critérios, e `UiTree.sensitive_reason` diz qual deles
pegou (o motivo aparece na evidência e na mensagem da etapa; **não** vai ao modelo — descrever o que há na tela
seria contar justamente o que a imagem omite):

| Critério | O que casa |
| --- | --- |
| `campo de senha` | qualquer elemento com `password="true"` (o de sempre) |
| `desafio de verificação (2FA/código de acesso)` | texto de desafio **e** um campo onde digitar |
| declarado por app | `config.yaml: sensitive_screens` — por pacote, resource-id ou texto |
| `aparelho-loja` | **toda** tela do aparelho-loja, sem exceção |

A conjunção do segundo critério não é detalhe: "Autenticação de dois fatores" é uma **linha de menu** nas
configurações do Instagram. Sem exigir um campo de digitação, entrar nas configurações marcaria a tela inteira
como sensível e o executor pararia a etapa pedindo intervenção humana no meio de uma navegação comum.

Numa tela já classificada como desafio, todo elemento cujo texto é **só dígitos** vira `••••` — é o código, que
o campo mostra depois de digitado, e o texto dos elementos continua indo ao modelo mesmo quando a imagem não vai.
Fora de uma tela de desafio o mesmo formato é preço, contador ou ano, e mascará-lo apagaria metade da interface:
por isso a máscara depende da TELA, não do elemento.

Declarar uma tela por app, em `config.yaml`:

```yaml
sensitive_screens:
  - package: com.exemplo.banco
    resource_ids: [":id/cpf", ":id/saldo"]   # casa por SUFIXO do resource-id
    texts: ["dados do titular"]              # casa por texto contido, sem acento e sem caixa
    why: dados pessoais do titular da conta  # aparece na mensagem da etapa e na evidência
```

Isto existe por causa do catálogo de apps: o parque passa a operar aplicativos que ninguém analisou, e o critério
genérico não tem como saber que a tela de "dados da conta" daquele app tem documento. Quem cadastrou o app sabe.

> **Uma leitura, um lugar.** Toda hierarquia passa por `DeviceManager.arvore()`, que é quem aplica as regras e o
> `sempre_sensivel` da VM-loja. Foi assim que o critério antigo ficou preso a `password=true`: cada chamador
> novo de `parse_hierarchy` nascia sem o resto, e ninguém percebia.
