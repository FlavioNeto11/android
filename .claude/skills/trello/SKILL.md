---
name: trello
description: 'Manter o Trello do dono como espelho do plano-100 e das frentes (sessão Canais). Use a cada mudança de estado, deploy, rotina diária/semanal ou carga em lote.'
---

# Trello — manutenção pela sessão Canais

O dono decidiu em 03/10/2026: **todas** as tarefas (concluídas, em execução e futuras) ficam no Trello com descrição
técnica **e** para quem não é técnico; objetivos, métricas, maturidade, custos, marcos e decisões também. A
manutenção nasceu com a orquestradora e, por decisão do dono no mesmo dia (18:15Z), passou à sessão **Canais**
(handoff `.claude/handoffs/canais.md`), que também faz a integração com a Central (32.2). A orquestradora escreve os
fatos em `.claude/handoffs/canais/eventos.md` e a Canais atualiza os cartões. **O
repositório e o banco continuam sendo a verdade**; o Trello é espelho + canal: divergência corrige o espelho.

## Estrutura (fonte única: `.claude/trello/estrutura.json`)

| Quadro | URL | Listas |
|---|---|---|
| Execução | https://trello.com/b/GalGk5Ya | 📌 Como ler · 🧭 Próximas · 🛠 Em execução · 🧪 Em validação · 🚧 Bloqueado/decisão do dono · ✅ Concluído nesta semana · 🤖 Central (automático) · ✅ Aprovado · ⛔ Vetado |
| Programa | https://trello.com/b/rf9PaM2E | 🎯 Objetivos · 📏 Métricas (meta × atual) · 🌱 Maturidade · 💰 Custos · 📅 Marcos e deploys · ⚖️ Decisões do dono · ⚠️ Riscos |
| Histórico | https://trello.com/b/1wCjXnBn | uma lista por grupo de fases (0–5, 6–10, 11, 14–15, 16–17, 18–20, 21–22, 23–27, 28, 29, 30, 31) |

Etiquetas (só cor; o MCP não renomeia): verde Android · roxo Jev · azul Aprendizado · amarelo orquestradora/dono ·
laranja custo pago · vermelho bloqueado/urgente. ARIs por quadro no JSON.

## Ferramentas

- MCP do Trello da sessão: `ToolSearch "+trello"` (max_results 15). Criar `trelloWriteCard create` (listId ARI, name,
  desc, due ISO UTC, pos); etiqueta `attach_label`; mover `move` (listId; `boardId` só se for outro quadro); concluir
  `mark_done`; comentar `add_comment`; checklist `trelloWriteChecklist create/add_item/update_item`; ler
  `trelloReadCard list_by_list|get`.
- **Sem chave no repositório.** A Central terá credencial própria no `.env` (32.2); a sessão usa o MCP.
- `mapa.json` (`.claude/trello/mapa.json`): `id do plano → {card, url, lista}`. Atualize ao criar/mover. Leia e grave
  sempre no checkout central (`C:/git/android`), nunca na cópia de um worktree.

## Modelo de cartão

Título `ID · nome curto` (PR: `#162 · 31.21 …`; sem ID: nome curto). Descrição em markdown, nesta ordem:
`**Para quem não é técnico:**` (1–2 frases, sem jargão) · `**Por que importa:**` · `**Técnico:**` (requisito,
prova `real|simulated|not_run`, arquivos, testes, ADR/migração/adendo) · `**Frente:** · **Esforço:** · **Custo pago:**` ·
`**Datas:**` (início, prazo = data do cartão, conclusão; horas Z + Brasília) · `**Fonte:**` (linha do plano, handoff,
reservas). Checklist “Etapas” nos itens em execução/validação: Desenho → Código → Testes simulated → Suíte → Deploy →
Validação no navegador → Prova real.

## Gatilhos (atualize NA HORA, com comentário curto `HH:MMZ · o que mudou`)

| Evento | Ação |
|---|---|
| ID novo reservado/aprovado | cartão em **Próximas** com prazo e frente |
| executora começou (branch/worktree) | mover para **Em execução**; hora de início no desc |
| PR pronto / entrou numa suíte | mover para **Em validação**; marcar etapas do checklist |
| deploy no ar | marcar “Deploy”; cartão do marco em **Programa › Marcos** (commit, migração, o que subiu) |
| validação Chrome / prova real feita | marcar etapa; se o item fechou: mover para **Concluído nesta semana** + `mark_done` |
| decisão do dono pendente / bloqueio | mover para **Bloqueado**; escrever “O que destrava” |
| decisão do dono tomada | cartão em **Programa › Decisões** (data, texto curto) |
| custo medido (rodada, P4, QA pareado) | atualizar **Programa › Custos › Gasto de hoje** |

## Rotinas

- **Diária (primeira hora):** mover os cartões de “Concluído nesta semana” do dia anterior para **Histórico** na lista da
  fase (`move` com `boardId` do Histórico); conferir prazos vencidos (due < agora) e comentar o novo prazo ou o motivo.
- **A cada deploy:** marco + métricas tocadas (M8 aparelhos, M9 testes).
- **Semanal (segunda):** revisar **Métricas** (meta × atual com data e fonte), **Maturidade** e **Riscos**; nada estimado
  sem dizer que é estimado.
- **Ao trocar de sessão:** o handoff `.claude/handoffs/canais.md` aponta para esta skill; a sessão nova lê
  `estrutura.json`, `mapa.json` e as linhas novas de `.claude/handoffs/canais/eventos.md`.

## Carga em lote (plano → cartões)

```bash
python .claude/trello/gerar.py --destino <pasta> --so hoje|historico|pendentes|todos
```

Gera lotes JSON (parte técnica pronta; marcadores `{{NAO_TECNICO}}` e `{{POR_QUE}}`). Agentes Sonnet
(`general-purpose`, prompt padrão em `.claude/trello/prompt-lote.md`) escrevem a parte para leigos e criam os cartões
pelo MCP, devolvendo `mapa/lote_<nome>.json`; depois `python .claude/trello/mapa.py <pasta>` funde os mapas em
`mapa.json`. O gerador passa o texto inteiro por `redacao.redigir`; mesmo assim, confira nome de pessoa que não está
no banco (persona de teste, autor em flag de comando) antes de gravar.

## Regras duras

- Nunca: segredo, nome de pessoa real ou de persona, @conta, e-mail, telefone, IP, senha, texto de comando de
  execução, dado de terceiro.
- Nunca inventar funcionalidade: só o que a linha do plano, a evidência e o CHANGELOG dizem.
- Horas só por `date -u` (Z) + Brasília (UTC-3). Prazos são compromissos: ao perder um, comentar o porquê e o novo.
- Não apagar cartões: arquivar (`archive`) e registrar no handoff.
