/**
 * Contrato de rotas do portal (hash). Fonte única: toda tela que monta ou lê um link usa estas funções, nunca
 * concatena `#/...` à mão.
 *
 *   #/painel                      #/painel?foco=android-01&estado=desconhecido&visao=lista
 *   #/personas                    #/personas?situacao=bloqueada&q=ana&ordem=nome&visao=tabela
 *   #/personas/<persona>          #/personas/<persona>/<guia>   (<persona> = nome legível `tadeu-quintela` ou o id antigo)
 *   #/aplicativos                 #/aplicativos/<app_id>?aba=versoes
 *   #/execucoes                   #/execucoes/<id>?aba=linha-do-tempo
 *   #/pedidos                     #/pedidos/<id>?aba=ocorrencias|execucoes|memoria   (o objetivo que dura; sem `aba` = Resumo)
 *   #/pendencias                  a caixa única do que espera uma decisão sua (aprendizado, personas, execuções, intervenções)
 *   #/aprendizado?aba=aprovar|aprendido|falhas|sinais   (sem `aba` = Para aprovar)
 *   #/infraestrutura  #/diagnostico
 *   #/canais                      o estado do aviso e da conversa pelo Telegram e do espelho no Trello (só leitura, 32.5)
 *   #/configuracao?aba=aplicativos|instancias|ia|fluxos|limites   (sem `aba` = Aplicativos: o link antigo
 *                                 `#/configuracao` continua abrindo a mesma guia)
 *
 * `#/perfis[...]` é o nome antigo de Personas: continua valendo (parse devolve `legado: true` para a tela trocar o
 * hash por `hashDe`, sem empilhar histórico).
 *
 * Parâmetros com dono fixo (não reutilize o nome para outra coisa):
 * - `foco=<id do aparelho>`: o aparelho aberto no painel de Foco. É GLOBAL (vale em qualquer tela, não é filtro do
 *   Painel) e acompanha a troca de tela; fechar o Foco tira só ele. Ver `store/ui.ts`.
 * - `aba=<guia>`: a guia ativa da tela ou do objeto aberto (Aplicativos, Execuções, Configuração…). Em Personas a guia
 *   é segmento do caminho (`#/personas/tadeu-quintela/memoria`). A tela agrupa as 11 guias em 5 seções, mas a URL guarda
 *   a GUIA: a seção é derivada dela (`features/profiles/abas.ts`), então todo link antigo continua abrindo o mesmo lugar.
 * - `<persona>` em Personas: o slug do nome (`features/profiles/slugPersona.ts`; homônimos ganham um sufixo curto do id,
 *   `tadeu-quintela-fqg8`) ou o id antigo (`ig-Ex4mpl0Pers0na12`). Os dois abrem a mesma pessoa; quem chega por id é
 *   levado ao slug por substituição de hash (sem empilhar histórico). Resolve pela lista já carregada, sem endpoint.
 * - Filtros de lista (`situacao`, `q`, `ordem`, `visao`, `estado`): da tela que os lê; quem compõe um hash novo a
 *   partir da rota atual preserva os que não são seus.
 *   - Personas (tarefa UX 05, `features/profiles/filtroPersonas.ts`): `q` (nome ou @), `situacao` = `ativa` |
 *     `atencao` | `bloqueada` (status `blocked`, o mesmo do contador da saúde do ambiente) | `pausada` | `sem-conta`;
 *     `vinculo` = `com` | `sem` (aparelho vinculado); `grupo` = id do grupo de acesso ou `nenhum`; `app` = id do app
 *     de um vínculo; `ordem` = `situacao` | `atividade` (sem = nome); `visao` = `cards` | `tabela`.
 *   - Painel: `estado` (estado do aparelho, `features/devices/DeviceGrid.tsx`); `visao` = `cards` | `lista`.
 *   - `visao` (Painel e Personas, decisão D3, `lib/visao.ts`): o link manda; sem ela, vale a última visão escolhida
 *     neste navegador; escolher grava nos dois. O menu leva à tela limpa (sem filtros nem `visao`).
 *   - Execuções (tarefa UX 05, `features/runs/filtroExecucoes.ts`): `q` (objetivo ou código), `status` =
 *     `andamento` | `planejada` | `concluida` | `pendencia` (rótulo "Pede atenção") | `falha` | `cancelada`;
 *     `periodo` = `24h` | `7d` | `30d`; `exploracao=1` (só as com etapa descoberta pela IA, 31.306);
 *     `aparelho` = id do aparelho; `servidor` = id do servidor. Convivem com `aba` da execução aberta.
 *   - Pedidos (item 28.9, `features/pedidos/filtro.ts`): os nomes são os da query de `GET /api/pedidos`, para o link e a chamada
 *     serem a mesma coisa: `q` (título ou objetivo), `estado` (um ou mais, separados por vírgula), `autonomia`, `tipo`
 *     (de gatilho), `profile_id`, `pede_atencao=1`, `ordem` = `atualizado` | `proxima` | `criado`. Convivem com `aba`.
 */
export const TELAS = ['painel', 'personas', 'aplicativos', 'execucoes', 'pedidos', 'pendencias', 'aprendizado',
                      'infraestrutura', 'configuracao', 'diagnostico', 'canais', 'operacoes', 'host'] as const;
export type Tela = (typeof TELAS)[number];

export interface Rota {
  tela: Tela;
  /** Partes do caminho depois da tela (id do objeto, aba). */
  segmentos: string[];
  query: Record<string, string>;
  /** Veio de um nome antigo (`#/perfis`): quem lê deve reescrever o hash com `hashDe(rota)`. */
  legado?: boolean;
}

const ALIAS_LEGADO: Record<string, Tela> = { perfis: 'personas' };

function ehTela(v: string): v is Tela {
  return (TELAS as readonly string[]).includes(v);
}

function decodificar(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}

/** `null` quando o hash não nomeia uma tela conhecida. */
export function parseHash(hash: string): Rota | null {
  const m = /^#\/?([^?]*)(?:\?(.*))?$/.exec(hash);
  if (!m) return null;
  const [nome, ...segmentos] = (m[1] ?? '').split('/').filter(Boolean).map(decodificar);
  const tela = nome && ehTela(nome) ? nome : nome ? ALIAS_LEGADO[nome] : undefined;
  if (!tela) return null;
  const query: Record<string, string> = {};
  for (const [k, v] of new URLSearchParams(m[2] ?? '')) query[k] = v;
  return { tela, segmentos, query, ...(nome !== tela ? { legado: true } : {}) };
}

/** Hash canônico: chaves da query em ordem alfabética, valores vazios omitidos (link estável e comparável). */
export function hashDe(tela: Tela, opc: { segmentos?: readonly string[]; query?: Record<string, string | undefined> } = {}): string {
  const caminho = [tela, ...(opc.segmentos ?? []).map(encodeURIComponent)].join('/');
  const q = new URLSearchParams();
  for (const k of Object.keys(opc.query ?? {}).sort()) {
    const v = opc.query?.[k];
    if (v) q.set(k, v);
  }
  const s = q.toString();
  return `#/${caminho}${s ? `?${s}` : ''}`;
}

export function hashDaRota(r: Rota): string {
  return hashDe(r.tela, { segmentos: r.segmentos, query: r.query });
}
