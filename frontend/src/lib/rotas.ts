/**
 * Contrato de rotas do portal (hash). Fonte única: toda tela que monta ou lê um link usa estas funções, nunca
 * concatena `#/...` à mão.
 *
 *   #/painel                      #/painel?foco=android-01&estado=desconhecido
 *   #/personas                    #/personas?situacao=bloqueada&q=ana&ordem=nome&visao=tabela
 *   #/personas/<id>               #/personas/<id>/<aba>
 *   #/aplicativos                 #/aplicativos/<pacote>?aba=versoes
 *   #/execucoes                   #/execucoes/<id>?aba=linha-do-tempo
 *   #/aprendizado  #/infraestrutura  #/configuracao?aba=aplicativos  #/diagnostico
 *
 * `#/perfis[...]` é o nome antigo de Personas: continua valendo (parse devolve `legado: true` para a tela trocar o
 * hash por `hashDe`, sem empilhar histórico).
 */
export const TELAS = ['painel', 'personas', 'aplicativos', 'execucoes', 'aprendizado', 'infraestrutura',
                      'configuracao', 'diagnostico'] as const;
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
