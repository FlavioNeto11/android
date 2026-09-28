/**
 * O MAPA da pessoa na guia Persona (pedido do dono de 28/09): em vez de formulários empilhados numa coluna, um
 * retrato no topo — quem ela é num relance, cada fato levando à sua seção — e um índice fixo com o estado de cada
 * seção. As seções abrem LENDO (dados, etiquetas, linha do tempo, gosta × não gosta), na mesma linguagem visual das
 * Crenças e da voz; editar é um gesto por seção.
 *
 * Só apresentação: o que vai ao modelo é decidido no backend (`PERSONA_BIO_FIELDS`, espelhado em `pessoa.ts`).
 */
import { Check, Circle, CircleDashed, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import type { PersonaDTO } from '../../api/types';
import { Badge } from '../../components/Badge';
import { cx } from '../../lib/format';
import { politicaDe, religiaoDe, rotuloDaOrientacao } from './CrencasPersona';
import { EMOJI_OPTIONS, FORMALITY_OPTIONS, TagList } from './PersonaVisual';
import type { Preenchimento } from './SecaoEditavel';
import styles from './Profiles.module.css';

export interface ItemDoMapa {
  id: string;
  titulo: string;
  icone: LucideIcon;
  estado: Preenchimento;
  /** Alguma coisa desta seção vai ao modelo? (a marca do índice). */
  vaiAoModelo: boolean;
}

/** Rola até a seção e põe o foco nela (leitor de tela e teclado chegam junto com os olhos). */
export function irPara(id: string): void {
  const el = typeof document !== 'undefined' ? document.getElementById(id) : null;
  if (!el) return;
  try {
    if (typeof el.scrollIntoView === 'function') el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch {
    /* ambiente sem scrollIntoView */
  }
  if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
  el.focus({ preventScroll: true });
}

const ICONE_DO_ESTADO: Record<Preenchimento, { icone: LucideIcon; texto: string }> = {
  completa: { icone: Check, texto: 'completa' },
  parcial: { icone: CircleDashed, texto: 'parcial' },
  vazia: { icone: Circle, texto: 'vazia' },
};

/** O índice fixo: todas as seções, o estado de cada uma e a marca do que vai ao modelo. */
export function IndiceDoMapa({ itens }: { itens: ItemDoMapa[] }) {
  return (
    <nav className={styles.indice} aria-label="Mapa da persona">
      <p className={styles.indiceTitulo}>Mapa da pessoa</p>
      <ol>
        {itens.map((it) => {
          const Icone = it.icone;
          const Estado = ICONE_DO_ESTADO[it.estado].icone;
          return (
            <li key={it.id}>
              <button type="button" className={styles.indiceItem} data-estado={it.estado} onClick={() => irPara(it.id)}>
                <Icone size={14} aria-hidden />
                <span className={styles.indiceRotulo}>{it.titulo}</span>
                {it.vaiAoModelo ? <span className={styles.indiceModelo} title="vai ao modelo">IA</span> : null}
                <Estado size={13} aria-hidden className={styles.indiceEstado} />
                <span className="sr-only">({ICONE_DO_ESTADO[it.estado].texto})</span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** Idade de hoje pela data de nascimento (AAAA-MM-DD), ou a aproximada da biografia. */
export function idadeDe(nascimento: string | null | undefined, aproximada: number | null | undefined,
                        hoje: Date = new Date()): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(nascimento ?? '');
  if (!m) return aproximada ?? null;
  const [ano, mes, dia] = [Number(m[1]), Number(m[2]), Number(m[3])];
  let idade = hoje.getFullYear() - ano;
  if (hoje.getMonth() + 1 < mes || (hoje.getMonth() + 1 === mes && hoje.getDate() < dia)) idade -= 1;
  return idade >= 0 && idade < 130 ? idade : aproximada ?? null;
}

interface FatoDoRetrato {
  rotulo: string;
  valor: string | null;
  secao: string;
}

function rotuloDe(opcoes: readonly { value: string; label: string }[], valor?: string | null): string | null {
  return opcoes.find((o) => o.value === valor)?.label ?? null;
}

/**
 * O retrato: quem a pessoa é num relance. Cada fato é um botão que leva à seção de onde ele vem; o que ainda não
 * existe aparece como "a completar", para o buraco ficar visível em vez de sumir. Embaixo dos fatos, o JEITO dela
 * (tom, formalidade, emojis, a expressão que usa, do que gosta) — é o que muda nas mensagens. As ações (ex.:
 * "Completar com IA") ficam numa faixa no rodapé, não numa coluna alta que deixava o bloco vazio.
 */
export function RetratoDaPessoa({ persona, preenchidas, total, acoes }: {
  persona: PersonaDTO;
  preenchidas: number;
  total: number;
  /** Ações do retrato (ex.: "Completar com IA"), numa faixa no rodapé. */
  acoes?: ReactNode;
}) {
  const bio = persona.biography ?? {};
  const t = persona.traits ?? {};
  const idade = idadeDe(persona.birth_date, bio.approx_age);
  const religiao = religiaoDe(bio);
  const politica = politicaDe(bio);
  const onde = [bio.home?.city, bio.home?.state].filter(Boolean).join(', ') || null;
  const fatos: FatoDoRetrato[] = [
    { rotulo: 'Idade', valor: idade !== null ? `${idade} anos` : null, secao: 'mapa-identidade' },
    { rotulo: 'Mora em', valor: onde, secao: 'mapa-origem' },
    { rotulo: 'Trabalho', valor: bio.work?.profession ?? null, secao: 'mapa-trabalho' },
    { rotulo: 'Vida', valor: bio.life?.marital_status ?? null, secao: 'mapa-vida' },
    { rotulo: 'Religião', valor: religiao?.affiliation ?? religiao?.summary ?? null, secao: 'mapa-crencas' },
    { rotulo: 'Política', valor: rotuloDaOrientacao(politica?.orientation) ?? politica?.summary ?? null,
      secao: 'mapa-crencas' },
  ];
  const jeito = [
    t.tone ? `tom ${t.tone}` : null,
    rotuloDe(FORMALITY_OPTIONS, t.formality)?.toLowerCase() ?? null,
    t.emojis ? `emojis: ${rotuloDe(EMOJI_OPTIONS, t.emojis)?.toLowerCase() ?? t.emojis}` : null,
    t.humor ? `humor ${t.humor}` : null,
  ].filter((x): x is string => Boolean(x));
  const expressao = (t.common_phrases ?? [])[0] ?? null;
  const interesses = t.interests ?? [];
  const gosta = (bio.tastes?.preferences ?? []).slice(0, 3);
  const frase = persona.summary ?? t.personality ?? null;
  const personalidade = persona.summary && t.personality && t.personality !== persona.summary ? t.personality : null;
  return (
    <section className={styles.retrato} aria-label="Retrato da persona">
      <div className={styles.retratoTexto}>
        {frase ? <p className={styles.retratoFrase}>{frase}</p>
          : <p className={styles.naoPreenchido}>Sem resumo ainda — “Completar com IA” escreve um a partir do resto.</p>}
        {personalidade ? <p className={styles.retratoPersonalidade}>“{personalidade}”</p> : null}
        <ul className={styles.retratoFatos}>
          {fatos.map((f) => (
            <li key={f.rotulo}>
              <button type="button" className={cx(styles.fato, !f.valor && styles.fatoVazio)} onClick={() => irPara(f.secao)}
                      title={f.valor ?? undefined}>
                <span className={styles.fatoRotulo}>{f.rotulo}</span>
                <span className={styles.fatoValor}>{f.valor ?? 'a completar'}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className={styles.retratoJeito}>
          <div className={styles.jeitoBloco}>
            <span className={styles.fatoRotulo}>Jeito de falar</span>
            {jeito.length > 0 || expressao ? (
              <p className={styles.jeitoTexto}>
                {jeito.join(' · ')}
                {expressao ? <>{jeito.length > 0 ? ' · ' : ''}diz <q>{expressao}</q></> : null}
              </p>
            ) : <p className={styles.naoPreenchido}>a completar na voz</p>}
          </div>
          <div className={styles.jeitoBloco}>
            <span className={styles.fatoRotulo}>Interesses</span>
            {interesses.length > 0 ? <TagList items={interesses.slice(0, 8)} />
              : <p className={styles.naoPreenchido}>a completar na voz</p>}
          </div>
          <div className={styles.jeitoBloco}>
            <span className={styles.fatoRotulo}>Gosta de</span>
            {gosta.length > 0 ? <p className={styles.jeitoTexto}>{gosta.join(' · ')}</p>
              : <p className={styles.naoPreenchido}>a completar em Gostos</p>}
          </div>
        </div>
      </div>
      <aside className={styles.retratoLado}>
        <div className={styles.retratoMedidor} role="meter" aria-label="Mapa preenchido" aria-valuemin={0}
             aria-valuemax={total} aria-valuenow={preenchidas} aria-valuetext={`${preenchidas} de ${total} seções`}>
          <strong>{preenchidas}<span>/{total}</span></strong>
          <span>seções preenchidas</span>
          <span className={styles.retratoBarra} aria-hidden>
            <span style={{ width: `${total ? Math.round((preenchidas / total) * 100) : 0}%` }} />
          </span>
        </div>
      </aside>
      {acoes ? <div className={styles.retratoRodape}>{acoes}</div> : null}
    </section>
  );
}

/** Um dado lido: rótulo pequeno em cima, valor embaixo; vazio vira "não preenchido", nunca um campo em branco. */
export interface DadoLido {
  rotulo: string;
  valor: ReactNode;
  vaiAoModelo?: boolean;
  largo?: boolean;
}

export function Dados({ itens }: { itens: DadoLido[] }) {
  return (
    <dl className={styles.dados}>
      {itens.map((d) => (
        <div key={d.rotulo} className={cx(styles.dado, d.largo && styles.dadoLargo)}>
          <dt>
            {d.rotulo}
            {d.vaiAoModelo ? <Badge size="sm" tone="info">vai ao modelo</Badge> : null}
          </dt>
          <dd>{vazio(d.valor) ? <span className={styles.naoPreenchido}>não preenchido</span> : d.valor}</dd>
        </div>
      ))}
    </dl>
  );
}

function vazio(v: ReactNode): boolean {
  return v === null || v === undefined || v === '' || (Array.isArray(v) && v.length === 0);
}

/** Lista como etiquetas (hobbies, formação); vazia não some — diz que falta. */
export function Etiquetas({ itens }: { itens: string[] | undefined }) {
  return itens && itens.length > 0 ? <TagList items={itens} /> : null;
}

/** Os marcos da vida como uma linha do tempo, na ordem em que foram escritos. */
export function LinhaDoTempo({ marcos }: { marcos: string[] | undefined }) {
  if (!marcos || marcos.length === 0) return null;
  return (
    <ol className={styles.linhaDoTempo}>
      {marcos.map((m, i) => <li key={`${i}-${m}`}>{m}</li>)}
    </ol>
  );
}

/** Gosta × não gosta, lado a lado — a mesma forma do "Diz × Nunca diz" da voz. */
export function GostaNaoGosta({ gosta, naoGosta }: { gosta: string[] | undefined; naoGosta: string[] | undefined }) {
  return (
    <div className={styles.phrasePair}>
      <div className={cx(styles.phraseCol, styles.phraseColSay)}>
        <h4>Gosta</h4>
        {gosta && gosta.length > 0 ? <ul>{gosta.map((g, i) => <li key={i}>{g}</li>)}</ul>
          : <p className={styles.muted}>Nada registrado.</p>}
      </div>
      <div className={cx(styles.phraseCol, styles.phraseColNever)}>
        <h4>Não gosta</h4>
        {naoGosta && naoGosta.length > 0 ? <ul>{naoGosta.map((g, i) => <li key={i}>{g}</li>)}</ul>
          : <p className={styles.muted}>Nada registrado.</p>}
      </div>
    </div>
  );
}
