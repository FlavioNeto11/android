# Aceite visual da onda E1 (evolução 2) — 28/09/2026

Painel no branch `claude/evo2-pe` (contrato de página, Configuração, Foco em seções, Personas, Comando sem senha,
Infraestrutura com criar/aposentar aparelho). Design: [`persona-e-parque.md`](../design/persona-e-parque.md) §9.
O jsdom não calcula layout; esta página é a prova de layout, a de comportamento está no vitest.

## Como foi medido (`simulated`)

- **Backend simulado** do próprio worktree em `127.0.0.1:8765` (`python -m app.main` com `AI_PROVIDER=simulated`,
  `POC_CONFIG`/`POC_DB_PATH` numa pasta de rascunho, `base_console_port: 5640`, `appium.autostart: false` e
  `android.sdk_root` apontando para uma pasta que não existe: nenhum adb, emulador ou IA real; a produção em 8000 não
  foi tocada). Por isso todos os aparelhos aparecem `absent` ("AVD ausente"): os estados de saúde do Foco além desse
  não foram exercitados na tela.
- **Dados semeados pela API simulada**: 3 personas (Marina com conta do Instagram no android-01 e senha de teste com
  consentimento; Rafael com Instagram no android-02 e conta de portal no Chrome com `host`; Beatriz sem conta), 2
  imagens simuladas por pessoa além da automática, e um aparelho dinâmico (`POST /instances` com `create: false`).
- **Painel**: `npm run dev -- --mode simulado --port 5188` (o `vite.config.ts` aponta o proxy para 8765 nesse modo;
  entrada `painel-evo2` em `.claude/launch.json`).
- **Capturas**: Edge headless por CDP (o navegador embutido não grava arquivo), janelas de 375×812, 1024×768,
  1366×768 e 1920×1080, Foco fechado e aberto (android-01). Em cada captura um script mede, no DOM inteiro (não só no
  recorte da imagem): elemento do `main` ou do painel do Foco que sai da caixa sem estar num rolador (**transbordo**),
  largura do documento maior que a janela, e texto cortado com reticências. As imagens mostram o topo da tela; Limites,
  IA, Persona e Contas e acesso têm também a variante `-fim` (rolada até o fim), e o Foco a variante rolada até a Zona
  de perigo.
- **Navegador embutido** (conferência manual das costuras entre telas): Foco → "Abrir persona" abre a Marina na
  tela Personas; Infraestrutura → "Aposentar android-07" mostra a confirmação e faz `DELETE /api/instances/android-07`
  (200), e o aparelho some da lista.

Capturas: [`capturas/evo2/`](capturas/evo2/) (124 JPEG; nome = `tela-largura[-foco][-fim]`).

## Resultado: tela × largura × Foco

Nenhum transbordo em nenhuma das 124 capturas.

| Tela | 375 | 375 + Foco | 1024 | 1024 + Foco | 1366 | 1366 + Foco | 1920 | 1920 + Foco |
|---|---|---|---|---|---|---|---|---|
| Painel (Foco fechado) / Foco aberto | ok | ok | ok | ok | ok | ok | ok | ok |
| Configuração — Aplicativos | ok | Foco cobre (esperado) | ok | ok | ok | ok | ok | ok |
| Configuração — Instâncias e contas | ok | Foco cobre (esperado) | ok | ok | ok | ok | ok | ok |
| Configuração — IA | ok | Foco cobre (esperado) | ok | ok | ok | ok | ok | ok |
| Configuração — Fluxos e receitas | ok | Foco cobre (esperado) | ok | ok | ok | ok | ok | ok |
| Configuração — Limites | ok | Foco cobre (esperado) | ok | ok | ok | ok | ok | ok |
| Personas — lista | ok ¹ | Foco cobre (esperado) | ok | ok | ok | ok ¹ | ok ¹ | ok ¹ |
| Persona — Visão geral | ok | Foco cobre (esperado) | ok | ok | ok | ok | ok | ok |
| Persona — Persona | ok | Foco cobre (esperado) | ok | ok | ok | ok | ok | ok |
| Persona — Contas e acesso | ok | Foco cobre (esperado) | ok | ok | ok | ok | ok | ok |
| Persona — Imagens | ok | Foco cobre (esperado) | ok | ok | ok | ok | ok | ok |
| Persona — Aparelhos | ok | Foco cobre (esperado) | ok | ok | ok | ok | ok | ok |
| Infraestrutura | ok | Foco cobre (esperado) | ok | ok | ok | ok | ok | ok |

"Foco cobre (esperado)": abaixo de 720 px de janela o Foco é tela cheia com "Voltar" (P1.1 da auditoria).

¹ O selo "app não verificado no aparelho" do cartão da persona sai com reticências por 5 a 25 % da largura (o selo
tem `max-width: 100%`; é o comportamento do `Badge`, sem estourar o cartão).

| Janela | `main` sem Foco | `main` com Foco | painel do Foco | colunas do Foco |
|---|---|---|---|---|
| 375 | 375 | — (Foco em tela cheia) | 375 | 1 (tela em cima) |
| 1024 | 1024 | 424 | 600 | 2 |
| 1366 | 1366 | 765 | 601 | 2 |
| 1920 | 1920 | 1075 | 845 | 2 |

## O que as medições pegaram e foi corrigido nesta onda

| Achado | Onde | Correção |
|---|---|---|
| Foco empilhado (tela em cima) em qualquer janela abaixo de ~1636 px | limiar `@container focus` de 720 px de painel (design §9.3), com o painel em `clamp(600px, 44vw, 920px)` | limiar em 559 px (600 px de painel = 599 de conteúdo): duas colunas de 1024 a 1920; uma coluna só em tela cheia |
| Em uma coluna, as seções do Foco subiam por cima da tela | trilha `auto` encolhendo até o `min-height: 0` da coluna da tela | `grid-auto-rows: max-content` |
| Nome da persona reduzido a "Ma…" na lista | ações no cabeçalho do cartão | ações no pé do cartão; rótulos mais estreitos no cartão |
| "Executar" cortado em 375 px | rodapé do Comando sem quebra de linha | `.actions` e `.selection` com `flex-wrap` |
| "Limpar" da grade para fora do `main` com o Foco aberto em 1024 | `.sectionActions` sem quebra | `flex-wrap` |
| Selo da fase cortado na linha de persona do Foco | botão "Abrir persona" ao lado | a linha quebra antes de cortar |

## Ainda aberto

- Os selos longos ainda saem com reticências em colunas estreitas (nota ¹; também o `@usuário` do cartão do Painel com
  o Foco aberto e as linhas de log da Infraestrutura, que são reticências de propósito).
- Estados de aparelho ligado, controle na mão (Controle manual), comando em voo e prévia ao vivo não aparecem nas
  capturas: sem SDK não há aparelho online. Comportamento provado no vitest (`simulated`), layout `not_run`.
- Geração de persona por IA paga, imagens reais (OpenAI), upload de foto e produção: `not_run`.
