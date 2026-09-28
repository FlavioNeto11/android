# Conhecimento de app como dado (ADR-052)

> Pedido do dono de 28/09/2026, depois da execução `r-20260928165254-e31953`: "se eu precisar fazer a mesma coisa
> para o Outlook, a plataforma não vai operar da mesma forma que o Instagram". Meta: **zero Python por app**. O
> conhecimento de um app é dado versionado; código é só motor genérico. Estado: meta aprovada pelo dono em 28/09;
> fatias 1–4 feitas (Fase 18, itens 18.1–18.3 e 18.5–18.7; a 1 implantada em `eafca07`); a 5 e o 12.3 seguem com o
> dono.

## 1. De onde se parte (inventário de 28/09)

**Já é genérico:** o motor de execução (executor, provas locais, guardas de commit, receitas, skills e a DSL) e a
FORMA do catálogo (`Capability` é dado). O ADR-039 tirou do núcleo as comparações com `"instagram"`, travadas por
`tests/test_apps_fora_do_nucleo.py`.

**É conhecimento, mas estava em código** (tudo isto saiu nas fatias 1–4):

| Onde | O que | Linhas |
|---|---|---|
| `integrations/instagram/navigation.py` | sinais de tela por idioma, ordem de classificação, ids de cabeçalho, dispensa de intersticiais, leitura de conteúdo e de autoria | 400 |
| `integrations/instagram/authentication.py` | máquina de login (preencher, `type_secret`, dispensar, observar depois de enviar, conta errada, tetos) | 458 |
| `integrations/instagram/verification.py` | leitura da conta aberta pela aba de perfil | 104 |
| `integrations/instagram/reconciliation.py` | desfechos do login | 86 |
| `planning/catalog/instagram.py` | 23 ações (prova local, guardas, risco, limite), escritas em Python, sem carregador nem versão | 246 |
| núcleo social | tipos `dm_*`/`comment_*`, `InteractionType`, baldes likes/comments/follows/dms | espalhado |

**Bloqueio estrutural:** "persona = um app âncora" (`package_of_provider("instagram")` em `state.py`,
`social/service.py` e `social/repository.py`). Uma persona não tem login gerenciado em dois apps; é o item 12.3.

**O resultado prático:** Outlook, TikTok e Facebook rodam só no "caminho livre" (a IA decide cada passo, sem prova
local, sem catálogo, login pela pessoa). Um app novo com o tratamento do Instagram exigia ~1.000 linhas de Python.

## 2. O alvo

Um pacote de conhecimento por app (arquivos de dado), lido por motores do núcleo:

1. **Telas e sinais:** regras em ordem de precedência (ids por prefixo, sinais de texto por idioma, formulário de
   senha, extrações), o tipo de cada tela no vocabulário do núcleo (`desafio`, `dois_fatores`, `intersticial`,
   `login`, `carregando`, `autenticada`) e o **estado conhecido**: de onde as leituras funcionam e como voltar a ele
   sem efeito externo.
2. **Fluxo de sessão:** seletores e textos declarados (usuário, senha por `type_secret`, enviar, dispensar,
   observar depois de enviar). A máquina de estados é uma só, do núcleo.
3. **Catálogo de ações** num arquivo de dado, com carregador e `contract_version` real.
4. **Extrações:** conta aberta, autor de comentário, mensagem da conversa (seletor + expressão).
5. **Conhecimento aprendido:** uma execução que encontra tela ou ação nova gera uma **candidata** com proveniência,
   que passa por rascunho → validada → publicada, o mesmo caminho das skills. É o que faz o sistema aprender as
   próprias telas em vez de chamar a pessoa toda vez.

**Fica fora do alcance da IA e do aprendizado:** telas de desafio, 2FA e senha. São segurança
(`automation/hierarchy.py::_DESAFIO`, `sensitive_screens`), não conhecimento editável.

## 3. Fatias

| Fatia | O que | Item | Estado |
|---|---|---|---|
| 1 | Telas como dado: `telas.yaml` lido por `automation/conhecimento_de_telas.py`; o Instagram idêntico, mais conversa, post, comentários e busca; a checagem de sessão volta ao estado conhecido antes de chamar pessoa | 18.2 | **feito** (`simulated`) |
| 2 | Catálogo de ações como dado (`catalogo.yaml`, carregador, versão de contrato) e registro que descobre os pacotes | 18.5 | **feito** (`simulated`) |
| 3 | Fluxo de sessão declarativo: o login do Instagram vira `sessao.yaml`, e o motor passa a ser um só (`SessaoDeclarada`) | 18.6 | **feito** (`simulated`) |
| 4 | App âncora do perfil pelo registro, bloco `contas:` no `config.yaml`, links de perfil como dado; texto "instagram" no código com catraca | 18.7 | **feito** (`simulated`) |
| 5 | Aprendizado de telas e ações como candidatas validadas | 18.8 | proposto |
| 6 | Persona com mais de um app com login gerenciado | 12.3 | decisão do dono |

Cada fatia amplia a catraca: o que sai do Python não pode voltar (`tests/test_apps_fora_do_nucleo.py`:
`app/integrations/` só tem o motor genérico, e o texto "instagram" no código de `app/` só desce).

## 4. Fatia 1, como ficou

- **Motor** (`automation/conhecimento_de_telas.py`): `de_dados` valida e monta (recusa tipo fora do vocabulário,
  sinal, extração ou tela de casa inexistente, regex inválida); `classificar` aplica as regras em ordem;
  `voltar_ao_estado_conhecido` usa só "voltar" do Android até `voltar_max` vezes e, no máximo uma vez, reabre o app.
  Ele não sai de login, desafio, 2FA, intersticial nem "carregando".
- **Instagram:** as tabelas de `navigation.py` (`SIGNALS`, ids, leitura da conta) viraram o `telas.yaml`, e os 139
  testes de sessão, desafio e leitura passaram sem mudar asserção. O app ganhou as telas de conversa, comentários,
  post e busca. (O `navigation.py` inteiro saiu depois, na integração das fatias 2–4.)
- **Checagem de sessão:** fora de casa (conversa, post, comentários, busca) ou em tela desconhecida, o autenticador
  chama o motor antes de concluir. Reproduzido com o dublê que retoma a tela ao abrir, como o app real
  (`FakeInstagram.retoma_tela_ao_abrir`).
- **Prova que o motor não conhece app:** um cliente de e-mail declarado só em dado é classificado e levado do rascunho
  à caixa (`test_um_app_novo_ganha_classificacao_e_volta_ao_estado_conhecido_so_com_dados`).

## 5. Mudanças da mesma rodada (fora das fatias)

- **18.1 Digitação com conferência** (`automation/tools.py::_conferir_digitacao`): relê o campo, completa só o que
  faltou, nunca duplica, não aperta Enter com texto incompleto e devolve o que de fato entrou.
- **18.3 Projeção e orçamento por ação** (`taskqueue/projecao.py`): mediana e p90 de chamadas, tempo e US$ por (app,
  ação) no histórico real; projeção do plano no evento e em `GET /api/runs/{id}/projection`; aviso ao passar do p90;
  parada acima de `max(p90 × 2, p90 + 4)` (`ai.step_budget`).
- **18.4 CI × parque** foi feito pela sessão Evolução (`0d73f3b`: runner com prioridade ociosa, espera o parque
  ocioso, vitest com 3 workers).

## 6. Fatias 2–4, como ficou

**O pacote.** `backend/app/conhecimento/apps/<pacote>/`, uma pasta por app, com o nome do pacote Android:

| Arquivo | O que diz | Motor que lê |
|---|---|---|
| `app.yaml` (obrigatório) | nome, rótulo, conta gerenciada (`provedor_de_sessao`), perfil e internet obrigatórios, `ancora_do_perfil`, tipos de texto, leituras de conversa, `leitura` (rascunho), `links_de_perfil` | `integrations/app_declarado/pacote.py`, `automation/leitura_de_tela.py` |
| `telas.yaml` | sinais por idioma, regras de tela, extrações, estado conhecido | `automation/conhecimento_de_telas.py` |
| `sessao.yaml` | ajustes padrão, formulário, dispensa, aba de perfil, desfechos depois do envio, textos de ajuda | `integrations/app_declarado/{conhecimento,formulario,sessao}.py` |
| `catalogo.yaml` | as ações (prova local, guardas, risco, limite), `contract_version` | `planning/capabilities.py::carregar_catalogo` |

**A descoberta.** O registro de apps (`modules/applications/infrastructure/registry.py`) chama
`pacote.descobrir()` na primeira consulta: cada pasta com `app.yaml` vira um `AppManifest` (definição, catálogo,
leitura de tela e a fábrica do `SessaoDeclarada`). Pasta com `app.yaml` inválido derruba a descoberta: um pacote pela
metade seria pior que nenhum, porque o núcleo passaria a confiar nele. `provedor_de_sessao` e `sessao.yaml` vêm
juntos, e no máximo um app é âncora.

**O app âncora.** `registry.pacote_ancora()` responde "o app da conta da persona" (onde vivem a credencial e a
sessão). Era `package_of_provider("instagram")` em `state.py`, `social/service.py` e `social/repository.py`: o núcleo
perguntava pelo tipo de conta de um app com nome. As mensagens ao dono usam o rótulo do app (`AppDefinition.label`).

**O `config.yaml`.** O bloco `instagram:` saiu. A validade do "Conectado" é `contas.session_max_age_s`; os ajustes do
login (tetos, cooldown, prazos) são por pacote, em `contas.sessao.<pacote>`, por cima do `sessao.yaml`. Um bloco
`instagram:` esquecido não é ignorado em silêncio: a instalação não sobe e diz para onde cada ajuste foi.

**O que ainda cita o Instagram no código, e por quê.** Nomes históricos que não são conhecimento: a tabela
`instagram_profiles`, o prefixo de rota `/api/instagram/…` e o nome antigo da variável da chave mestra (renomear é
migração e versão de contrato, fica para uma rodada própria). E textos contados na catraca `TEXTO_LEGADO`: exemplos ao
modelo nos prompts e a tabela de blocos que saíram do `config.yaml`.

**A prova de "zero Python por app".** `tests/test_pacote_declarado.py`: um cliente de e-mail declarado só em quatro
arquivos entra no registro com catálogo, leitura de tela e login, sem uma linha de Python e sem tocar no registro.
