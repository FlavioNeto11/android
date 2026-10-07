/**
 * 31.214: a lição candidata que veio de FATO da pesquisa de uma operação (Aprendizado 31.190, `source_kind = operation_fact`). O texto é
 * de fonte externa (a web) e a lição ativa do escritor iria a todo texto do app, sobre qualquer post: por isso só uma PESSOA publica
 * (D1, `FONTES_HUMANAS`). O Livro mostra de onde o fato veio (a operação, o assunto, os domínios das fontes), a confiança, o frescor e
 * quanto já foi usado, para a pessoa decidir com o que o sistema sabe. A leitura é tolerante e fica aqui: se o backend mudar um nome,
 * muda neste arquivo.
 */
import type { AcaoDoItem, ConteudoDoItem, EntradaDoLivro } from './model';

/** O `source_kind` da lição que nasceu de um fato de operação. */
export const SOURCE_KIND_DO_FATO = 'operation_fact';

export interface FatoDaOperacao {
  operacaoId: string | null;
  /** O assunto da pesquisa (a proveniência o traz sem identificador de pessoa); `null` = não informado. */
  assunto: string | null;
  /** Os domínios das fontes que confirmaram o fato. */
  fontes: string[];
  /** Até quando o fato vale; `null` com `frescorInformado` = sem prazo; sem o campo, `frescorInformado` é falso (não informado). */
  frescorAte: string | null;
  frescorInformado: boolean;
  /** Quantos alvos receberam o fato no texto; `null` = não informado, nunca zero. */
  usadoEm: number | null;
  /** As execuções de que o fato saiu (ids). */
  execucoes: string[];
  /** `confirmado` (dois domínios na pesquisa, ou a leitura do alvo) ou o que o backend disser; `null` = não informada. */
  confianca: string | null;
}

const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v.trim() : null);
const lista = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string' && x.trim() !== '').map((x) => x.trim()) : []);

/** O `modelo` do conteúdo legível da lição que nasceu de um fato de operação (hoje o único sinal no detalhe). */
export const MODELO_DO_FATO = 'fato_da_operacao';

/**
 * `null` quando o item não é um fato de operação. Reconhece pelo `source_kind` da entrada, pela regra da proveniência (31.190) ou pelo
 * `modelo` do conteúdo do detalhe; a proveniência só vem no detalhe.
 */
export function fatoDaOperacaoDe(e: Pick<EntradaDoLivro, 'source_kind'>, detalhe?: { proveniencia?: unknown; conteudo?: ConteudoDoItem | null }): FatoDaOperacao | null {
  const p = registro(detalhe?.proveniencia);
  const regra = p ? texto(p.regra) : null;
  const modelo = detalhe?.conteudo?.tipo === 'licao' ? detalhe.conteudo.modelo : null;
  if (e.source_kind !== SOURCE_KIND_DO_FATO && modelo !== MODELO_DO_FATO && !(regra && regra.startsWith('31.190'))) return null;
  const usado = p && typeof p.usado_em === 'number' && Number.isInteger(p.usado_em) && p.usado_em >= 0 ? p.usado_em : null;
  return {
    operacaoId: p ? texto(p.operacao) : null, assunto: p ? texto(p.assunto) : null, fontes: p ? lista(p.fontes) : [],
    frescorAte: p ? texto(p.frescor_ate) : null, frescorInformado: !!p && 'frescor_ate' in p, usadoEm: usado,
    // A confiança não é gravada: só nasce o fato `confirmado` (condição do 31.190); o campo, quando vier, vale mais.
    execucoes: p ? lista(p.execucoes) : [], confianca: (p ? texto(p.confianca) : null) ?? 'confirmado',
  };
}

/** Só pessoa publica o fato (a origem é de fonte externa e a lição ativa iria a todo texto do app): o Publicar é a transição direta para `published`. */
export const ACAO_PUBLICAR_O_FATO: AcaoDoItem = {
  to: 'published', label: 'Publicar', confirmar: 'Confirmar publicação', perigo: false,
  efeito: 'Publica o fato: ele passa a valer para todo texto do app, sobre qualquer post. Só uma pessoa publica; o motivo fica na trilha.',
};

/** O Publicar da candidata que é fato de operação (a rota leva candidate → published num gesto); sem ele quando já está publicada ou há ação que publica. */
export function acaoDePublicarOFato(e: Pick<EntradaDoLivro, 'state' | 'source_kind' | 'acoes'>, detalhe?: { proveniencia?: unknown; conteudo?: ConteudoDoItem | null }): AcaoDoItem | null {
  if (e.state !== 'candidate' || fatoDaOperacaoDe(e, detalhe) === null) return null;
  return (e.acoes ?? []).some((a) => a.to === 'published') ? null : ACAO_PUBLICAR_O_FATO;
}

const CONFIANCA: Record<string, string> = { confirmado: 'Confirmado (dois domínios, ou a leitura do alvo)', hipotese: 'Hipótese, não confirmada' };
export const confiancaEmPalavras = (c: string | null): string => (c === null ? 'Confiança não informada' : CONFIANCA[c] ?? c);

export interface FrescorEmPalavras { texto: string; vencido: boolean }

/** "Vale até 08/10 14:00", "vencido há 2 dias" ou "sem prazo"; sem o campo, "não informado" (nunca "sem prazo" por engano). */
export function frescorEmPalavras(f: Pick<FatoDaOperacao, 'frescorAte' | 'frescorInformado'>, agoraMs: number = Date.now(), quando: (iso: string) => string = (i) => i): FrescorEmPalavras {
  if (!f.frescorInformado) return { texto: 'Frescor não informado', vencido: false };
  if (f.frescorAte === null) return { texto: 'Sem prazo de validade', vencido: false };
  const ate = Date.parse(f.frescorAte);
  if (!Number.isFinite(ate)) return { texto: 'Frescor não informado', vencido: false };
  if (ate > agoraMs) return { texto: `Vale até ${quando(f.frescorAte)}`, vencido: false };
  const dias = Math.floor((agoraMs - ate) / 86_400_000);
  return { texto: dias < 1 ? 'Vencido hoje' : `Vencido há ${dias} ${dias === 1 ? 'dia' : 'dias'}`, vencido: true };
}
