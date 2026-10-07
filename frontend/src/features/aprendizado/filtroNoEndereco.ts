/**
 * 31.144: os filtros do Livro (Tipo, Estado, Origem, Prova e a visão Produto/QA/Todos) viajam no endereço, ao lado de `app` e
 * `item` que já viajavam: recarregar a página ou mandar o link não perde o filtro. A leitura é tolerante (valor desconhecido
 * vira "sem filtro") e fica isolada aqui, para os nomes da query mudarem num lugar só.
 */
import type { FiltroDoLivro } from './api';
import { PROVAS_DO_LIVRO } from './api';
import { ASSUNTO_MAX } from './assunto';
import { isEstadoDoLivro, isLivroKind, ORIGENS, ROTULOS, type Rotulo } from './model';

export const PARAMS_DO_FILTRO = { kind: 'tipo', state: 'estado', origem: 'origem', prova: 'prova', rotulo: 'visao', assunto: 'assunto' } as const;

type Query = Record<string, string | undefined>;

const assuntoValido = (v: string | undefined): string | undefined => (v && v.trim() && v.length <= ASSUNTO_MAX ? v.trim() : undefined);

/** O filtro que o endereço diz (sem o `app`, que o Livro lê à parte). */
export function lerFiltroDoEndereco(q: Query): Omit<FiltroDoLivro, 'app'> {
  const kind = q[PARAMS_DO_FILTRO.kind];
  const state = q[PARAMS_DO_FILTRO.state];
  return {
    kind: kind && isLivroKind(kind) ? kind : undefined,
    state: state && isEstadoDoLivro(state) ? state : undefined,
    origem: ORIGENS.find((o) => o === q[PARAMS_DO_FILTRO.origem]),
    prova: PROVAS_DO_LIVRO.find((p) => p === q[PARAMS_DO_FILTRO.prova]),
    rotulo: ROTULOS.find((r): r is Rotulo => r === q[PARAMS_DO_FILTRO.rotulo]),
    // 31.209: o servidor recusa mais de 200 caracteres (422); um link com mais do que isso vira "sem filtro".
    assunto: assuntoValido(q[PARAMS_DO_FILTRO.assunto]),
  };
}

/** A parte da query que escreve a mudança: o campo que ficou sem valor sai do endereço (`undefined`). */
export function queryDoFiltro(parcial: Partial<Omit<FiltroDoLivro, 'app'>>): Query {
  const saida: Query = {};
  for (const campo of Object.keys(PARAMS_DO_FILTRO) as (keyof typeof PARAMS_DO_FILTRO)[]) {
    if (campo in parcial) saida[PARAMS_DO_FILTRO[campo]] = parcial[campo] || undefined;
  }
  return saida;
}
