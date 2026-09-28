# Conhecimento de app como dado (ADR-052)

> Pedido do dono de 28/09/2026, depois da execução `r-20260928165254-e31953`: "se eu precisar fazer a mesma coisa
> para o Outlook, a plataforma não vai operar da mesma forma que o Instagram". Meta: **zero Python por app**. O
> conhecimento de um app é dado versionado; código é só motor genérico. Estado: meta aprovada pelo dono em 28/09;
> fatia 1 implantada (Fase 18, itens 18.1–18.3); fatias 2–4 em andamento; a 5 e o 12.3 seguem com o dono.

## 1. De onde se parte (inventário de 28/09)

**Já é genérico:** o motor de execução (executor, provas locais, guardas de commit, receitas, skills e a DSL) e a
FORMA do catálogo (`Capability` é dado). O ADR-039 tirou do núcleo as comparações com `"instagram"`, travadas por
`tests/test_apps_fora_do_nucleo.py`.

**É conhecimento, mas estava em código:**

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
| 1 | Telas como dado: `integrations/instagram/conhecimento/telas.yaml` lido por `automation/conhecimento_de_telas.py`; o Instagram idêntico, mais conversa, post, comentários e busca; a checagem de sessão volta ao estado conhecido antes de chamar pessoa | 18.2 | **feito** (`simulated`) |
| 2 | Catálogo de ações como dado (carregador, versão de contrato) e registro que descobre pacotes | 18.5 | em andamento |
| 3 | Fluxo de sessão declarativo: o login do Instagram vira dado, e o motor passa a ser um só | 18.6 | em andamento |
| 4 | App âncora do perfil pelo registro (sem "instagram" no núcleo) e vocabulário social revisto | 18.7 | em andamento |
| 5 | Aprendizado de telas e ações como candidatas validadas | 18.8 | proposto |
| 6 | Persona com mais de um app com login gerenciado | 12.3 | decisão do dono |

Cada fatia amplia a catraca: o que sai do Python não pode voltar
(`tests/test_conhecimento_de_telas.py::test_conhecimento_do_instagram_e_dado_e_o_python_nao_guarda_mais_as_tabelas`).

## 4. Fatia 1, como ficou

- **Motor** (`automation/conhecimento_de_telas.py`): `de_dados` valida e monta (recusa tipo fora do vocabulário,
  sinal, extração ou tela de casa inexistente, regex inválida); `classificar` aplica as regras em ordem;
  `voltar_ao_estado_conhecido` usa só "voltar" do Android até `voltar_max` vezes e, no máximo uma vez, reabre o app.
  Ele não sai de login, desafio, 2FA, intersticial nem "carregando".
- **Instagram:** `navigation.py` perdeu as tabelas (`SIGNALS`, ids, leitura da conta) e passou a ler o YAML; a
  interface de quem o usa (`classify`, `signals`, `header_username`…) ficou igual, e os 139 testes de sessão, desafio
  e leitura passaram sem mudar asserção. O `Screen` ganhou `THREAD`, `COMMENTS`, `POST` e `SEARCH`.
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
