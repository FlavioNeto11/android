/**
 * Seção "Crenças" da persona (ADR-048): religião e política RICAS, que vão ao modelo e moldam a voz — o bloco
 * `<persona>` do backend (`social/context.py::linhas_de_crencas`) as escreve com a linha de conduta. Dois cartões no
 * contrato de página (`PageSection` + `AutoGrid`), cada um com a leitura visual e a edição própria: salvar manda um
 * PATCH só de `biography.beliefs.<crença>`, que o servidor mescla chave a chave (esvaziar tudo manda `null`, que
 * apaga a crença).
 *
 * O espectro político é uma barra NEUTRA com marcador, sem cor de partido nem de lado. "Apolítica" e "não declara"
 * ficam fora da barra, porque não são um ponto nela.
 */
import { Landmark, Pencil, Plus, Save, ShieldCheck, Trash2, X } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import type {
  BioIssue, BioPolitics, BioReligion, EngajamentoPolitico, OrientacaoPolitica, PersonaBiography,
  PersonaPatchRequest, PraticaReligiosa,
} from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Field, Select, TextArea, TextInput } from '../../components/Field';
import { AutoGrid, PageSection } from '../../components/Page';
import { cx } from '../../lib/format';
import type { Tone } from '../../lib/status';
import { comoTexto, paraLista, paraTexto } from './SecaoEditavel';
import styles from './Profiles.module.css';

// Rótulos: os MESMOS textos de `models.py::ROTULOS_DE_CRENCA` (o que o modelo lê), com a primeira letra maiúscula.
export const PRATICA_OPCOES: readonly { value: PraticaReligiosa; label: string }[] = [
  { value: 'nao_pratica', label: 'Não pratica' },
  { value: 'ocasional', label: 'Pratica às vezes' },
  { value: 'regular', label: 'Pratica com regularidade' },
  { value: 'devota', label: 'Devoção intensa' },
];

/** Os cinco pontos do espectro, da esquerda para a direita: a ordem da barra. */
export const ESPECTRO: readonly { value: OrientacaoPolitica; label: string }[] = [
  { value: 'esquerda', label: 'Esquerda' },
  { value: 'centro_esquerda', label: 'Centro-esquerda' },
  { value: 'centro', label: 'Centro' },
  { value: 'centro_direita', label: 'Centro-direita' },
  { value: 'direita', label: 'Direita' },
];

/** Fora do espectro: não são um ponto na barra, e a tela os diz por extenso. */
export const FORA_DO_ESPECTRO: readonly { value: OrientacaoPolitica; label: string; explica: string }[] = [
  { value: 'apolitica', label: 'Apolítica', explica: 'não se interessa por política' },
  { value: 'nao_declara', label: 'Não declara', explica: 'tem posição e não diz' },
];

export const ENGAJAMENTO_OPCOES: readonly { value: EngajamentoPolitico; label: string }[] = [
  { value: 'nenhum', label: 'Nenhum' }, { value: 'baixo', label: 'Baixo' },
  { value: 'medio', label: 'Médio' }, { value: 'alto', label: 'Alto' },
];

/** A regra de conduta, a mesma do backend (`CONDUTA_DAS_CRENCAS`), dita para quem edita. */
const CONDUTA =
  'A persona não faz propaganda política nem religiosa, não pede voto nem adesão, não espalha desinformação e não '
  + 'ataca grupos nem pessoas por crença, ideologia ou identidade.';

function rotulo<T extends string>(opcoes: readonly { value: T; label: string }[], valor?: T | null): string | null {
  return opcoes.find((o) => o.value === valor)?.label ?? null;
}

export function rotuloDaOrientacao(o?: OrientacaoPolitica | null): string | null {
  return rotulo(ESPECTRO, o) ?? rotulo(FORA_DO_ESPECTRO, o);
}

/** Nada a mostrar: nulo, texto em branco, lista ou objeto só de vazios (a mesma regra do backend). */
function vazio(v: unknown): boolean {
  if (v === null || v === undefined) return true;
  if (typeof v === 'string') return !v.trim();
  if (Array.isArray(v)) return v.every(vazio);
  if (typeof v === 'object') return Object.values(v as Record<string, unknown>).every(vazio);
  return false;
}

/** A religião da biografia. Texto é um backend anterior (v1): vira o resumo, como o backend novo faz na leitura. */
export function religiaoDe(bio?: PersonaBiography | null): BioReligion | null {
  const r = bio?.beliefs?.religion;
  if (r === null || r === undefined) return null;
  if (typeof r === 'string') return r.trim() ? { summary: r.trim() } : null;
  return vazio(r) ? null : r;
}

export function politicaDe(bio?: PersonaBiography | null): BioPolitics | null {
  const p = bio?.beliefs?.politics;
  if (p === null || p === undefined) return null;
  if (typeof p === 'string') return p.trim() ? { summary: p.trim() } : null;
  return vazio(p) ? null : p;
}

/** Uma linha para a Visão geral: "católica · pratica às vezes"; sem afiliação nem prática, o resumo. */
export function resumoDaReligiao(r: BioReligion | null): string | null {
  if (!r) return null;
  const partes = [r.affiliation?.trim(), rotulo(PRATICA_OPCOES, r.practice)?.toLowerCase()].filter(Boolean);
  return partes.length > 0 ? partes.join(' · ') : r.summary?.trim() || null;
}

/** "centro-esquerda · engajamento baixo"; sem orientação nem engajamento, o resumo. */
export function resumoDaPolitica(p: BioPolitics | null): string | null {
  if (!p) return null;
  const engajamento = rotulo(ENGAJAMENTO_OPCOES, p.engagement);
  const partes = [rotuloDaOrientacao(p.orientation)?.toLowerCase(),
    engajamento ? `engajamento ${engajamento.toLowerCase()}` : null].filter(Boolean);
  return partes.length > 0 ? partes.join(' · ') : p.summary?.trim() || null;
}

/**
 * A barra do espectro: trilho neutro, um marcador, os cinco pontos embaixo. Para leitor de tela é um `meter` com o
 * nome do ponto em `aria-valuetext` ("Centro-esquerda", não "1 de 4").
 */
export function EspectroPolitico({ orientacao }: { orientacao?: OrientacaoPolitica | null }) {
  const fora = FORA_DO_ESPECTRO.find((f) => f.value === orientacao);
  if (fora) {
    return (
      <p className={styles.espectroFora}>
        <Badge tone="neutral">{fora.label}</Badge>
        <span>{fora.explica}; fica fora do espectro esquerda–direita.</span>
      </p>
    );
  }
  const indice = ESPECTRO.findIndex((e) => e.value === orientacao);
  const ponto = ESPECTRO[indice];
  if (!ponto) return <p className={styles.muted}>Orientação não registrada.</p>;
  const posicao = (indice / (ESPECTRO.length - 1)) * 100;
  return (
    <div className={styles.espectro}>
      <div role="meter" aria-label="Orientação política" aria-valuemin={0} aria-valuemax={ESPECTRO.length - 1}
           aria-valuenow={indice} aria-valuetext={ponto.label} className={styles.espectroTrilho}>
        {ESPECTRO.map((e, k) => (
          <span key={e.value} className={styles.espectroParada} style={{ left: `${(k / (ESPECTRO.length - 1)) * 100}%` }}
                aria-hidden />
        ))}
        <span className={styles.espectroMarcador} style={{ left: `${posicao}%` }} aria-hidden />
      </div>
      <ol className={styles.espectroRotulos} aria-hidden>
        {ESPECTRO.map((e) => <li key={e.value} data-active={e.value === orientacao || undefined}>{e.label}</li>)}
      </ol>
    </div>
  );
}

function Fichas({ itens, tone = 'neutral' }: { itens?: string[] | null; tone?: Tone }) {
  const lista = (itens ?? []).filter((i) => i.trim());
  if (lista.length === 0) return null;
  return (
    <ul className={styles.tagList}>
      {lista.map((it, i) => <li key={`${it}-${i}`}><Badge tone={tone}>{it}</Badge></li>)}
    </ul>
  );
}

/** Um bloco rotulado do cartão; some quando não há o que mostrar (nada de "—" empilhado). */
function Bloco({ titulo, children, mostrar }: { titulo: string; children: ReactNode; mostrar: boolean }) {
  if (!mostrar) return null;
  return (
    <div className={styles.crencaBloco}>
      <h5>{titulo}</h5>
      {children}
    </div>
  );
}

export function SecaoCrencas({ biography, onSalvar }: {
  biography?: PersonaBiography | null;
  /** `true` quando o servidor gravou: só então o formulário fecha (se falhar, o que foi digitado fica). */
  onSalvar: (patch: PersonaPatchRequest, rotulo: string) => Promise<boolean>;
}) {
  return (
    <PageSection level={3} className={styles.crencasSecao}
                 title={<span className={styles.secaoTitulo}>Crenças <Badge size="sm" tone="info">vai ao modelo</Badge></span>}
                 subtitle="Religião e política dão coerência aos valores, ao tom e às escolhas desta pessoa.">
      <Banner tone="info" icon={ShieldCheck} compact className={styles.crencasAviso}
              title="Vão ao modelo e moldam a voz, não viram assunto">
        O modelo recebe estas crenças para a pessoa reagir de forma coerente ao que aprova e ao que evita. {CONDUTA}
      </Banner>
      <AutoGrid min="320px">
        <CartaoReligiao religiao={religiaoDe(biography)}
                        onSalvar={(r) => onSalvar({ biography: { beliefs: { religion: r } } }, 'Religião')} />
        <CartaoPolitica politica={politicaDe(biography)}
                        onSalvar={(p) => onSalvar({ biography: { beliefs: { politics: p } } }, 'Política')} />
      </AutoGrid>
    </PageSection>
  );
}

// ---------------------------------------------------------------- religião
interface FormReligiao {
  affiliation: string; practice: string; practices: string; importance: string; in_speech: string;
  values: string; sensitive_topics: string; summary: string;
}

function formDaReligiao(r: BioReligion | null): FormReligiao {
  return {
    affiliation: comoTexto(r?.affiliation), practice: r?.practice ?? '', practices: comoTexto(r?.practices),
    importance: comoTexto(r?.importance), in_speech: comoTexto(r?.in_speech), values: comoTexto(r?.values),
    sensitive_topics: comoTexto(r?.sensitive_topics), summary: comoTexto(r?.summary),
  };
}

/** O objeto inteiro da crença: vazio vira `null` (apaga a chave), lista vazia vira `[]`. Tudo em branco = `null`. */
function religiaoDoForm(f: FormReligiao): BioReligion | null {
  const r: BioReligion = {
    affiliation: paraTexto(f.affiliation), practice: (f.practice || null) as PraticaReligiosa | null,
    practices: paraLista(f.practices), importance: paraTexto(f.importance), in_speech: paraTexto(f.in_speech),
    values: paraLista(f.values), sensitive_topics: paraLista(f.sensitive_topics), summary: paraTexto(f.summary),
  };
  return vazio(r) ? null : r;
}

function CartaoReligiao({ religiao, onSalvar }: {
  religiao: BioReligion | null;
  onSalvar: (r: BioReligion | null) => Promise<boolean>;
}) {
  const tituloId = useId();
  const [editando, setEditando] = useState(false);
  const r = religiao ?? {};
  const pratica = rotulo(PRATICA_OPCOES, r.practice);
  return (
    <article role="group" aria-labelledby={tituloId} className={styles.crenca}>
      <header className={styles.crencaTopo}>
        <div>
          <h4 id={tituloId} className={styles.crencaTitulo}><Landmark size={15} aria-hidden /> Religião</h4>
          <p className={styles.crencaSub}>{r.affiliation || (religiao ? 'Afiliação não registrada' : 'Nada registrado ainda')}</p>
        </div>
        {!editando ? (
          <Button size="sm" variant="ghost" icon={Pencil} onClick={() => setEditando(true)}>Editar religião</Button>
        ) : null}
      </header>
      {editando ? (
        <EditorReligiao inicial={religiao} onCancelar={() => setEditando(false)}
                        onSalvar={async (novo) => { if (await onSalvar(novo)) setEditando(false); }} />
      ) : (
        <>
          {pratica ? <div><Badge tone="info">{pratica}</Badge></div> : null}
          {r.summary ? <p className={styles.crencaResumo}>{r.summary}</p> : null}
          <Bloco titulo="Peso na vida" mostrar={!vazio(r.importance)}><p>{r.importance}</p></Bloco>
          <Bloco titulo="O que pratica" mostrar={!vazio(r.practices)}><Fichas itens={r.practices} /></Bloco>
          <Bloco titulo="Como aparece na fala" mostrar={!vazio(r.in_speech)}>
            <p className={styles.crencaFala}>{r.in_speech}</p>
          </Bloco>
          <Bloco titulo="Valores" mostrar={!vazio(r.values)}><Fichas itens={r.values} /></Bloco>
          <Bloco titulo="Evita ou trata com cuidado" mostrar={!vazio(r.sensitive_topics)}>
            <Fichas itens={r.sensitive_topics} tone="warning" />
          </Bloco>
          {!religiao ? <p className={styles.muted}>Sem religião registrada: o modelo não recebe nada sobre fé.</p> : null}
        </>
      )}
    </article>
  );
}

function EditorReligiao({ inicial, onSalvar, onCancelar }: {
  inicial: BioReligion | null;
  onSalvar: (r: BioReligion | null) => Promise<void>;
  onCancelar: () => void;
}) {
  const base = formDaReligiao(inicial);
  const [f, setF] = useState<FormReligiao>(base);
  const [salvando, setSalvando] = useState(false);
  const muda = (chave: keyof FormReligiao) => (e: { target: { value: string } }) => setF((v) => ({ ...v, [chave]: e.target.value }));
  const mudou = JSON.stringify(f) !== JSON.stringify(base);

  async function salvar() {
    if (!mudou || salvando) return;
    setSalvando(true);
    try {
      await onSalvar(religiaoDoForm(f));
    } finally {
      setSalvando(false);
    }
  }

  return (
    <div className={styles.crencaForm}>
      <Field label="Afiliação" hint="Tradição ou denominação; ou “sem religião”, “agnóstica”, “ateia”.">
        {({ id, describedBy }) => <TextInput id={id} aria-describedby={describedBy} value={f.affiliation} onChange={muda('affiliation')} />}
      </Field>
      <Field label="Prática">
        {({ id }) => (
          <Select id={id} value={f.practice} onChange={muda('practice')}>
            <option value="">—</option>
            {PRATICA_OPCOES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </Select>
        )}
      </Field>
      <Field label="O que pratica" hint="Um por linha: missa, culto, meditação, festas…" className={styles.secaoCampoLargo}>
        {({ id, describedBy }) => <TextArea id={id} aria-describedby={describedBy} rows={3} value={f.practices} onChange={muda('practices')} />}
      </Field>
      <Field label="Peso na vida" className={styles.secaoCampoLargo}>
        {({ id }) => <TextInput id={id} value={f.importance} onChange={muda('importance')} />}
      </Field>
      <Field label="Como aparece na fala" hint="Expressões e referências que ela usa sem pensar." className={styles.secaoCampoLargo}>
        {({ id, describedBy }) => <TextArea id={id} aria-describedby={describedBy} rows={2} value={f.in_speech} onChange={muda('in_speech')} />}
      </Field>
      <Field label="Valores" hint="Um por linha.">
        {({ id, describedBy }) => <TextArea id={id} aria-describedby={describedBy} rows={3} value={f.values} onChange={muda('values')} />}
      </Field>
      <Field label="Evita ou trata com cuidado" hint="Um por linha.">
        {({ id, describedBy }) => <TextArea id={id} aria-describedby={describedBy} rows={3} value={f.sensitive_topics} onChange={muda('sensitive_topics')} />}
      </Field>
      <Field label="Resumo" className={styles.secaoCampoLargo}>
        {({ id }) => <TextArea id={id} rows={2} value={f.summary} onChange={muda('summary')} />}
      </Field>
      <div className={styles.crencaAcoes}>
        <Button size="sm" variant="ghost" icon={X} onClick={onCancelar}>Cancelar</Button>
        <Button size="sm" icon={Save} loading={salvando} disabledReason={mudou ? null : 'Nada mudou nesta crença.'}
                onClick={() => void salvar()}>
          Salvar religião
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- política
interface FormPolitica {
  orientation: string; engagement: string; issues: { topic: string; stance: string }[]; discussion_style: string;
  sources: string; values: string; summary: string;
}

function formDaPolitica(p: BioPolitics | null): FormPolitica {
  return {
    orientation: p?.orientation ?? '', engagement: p?.engagement ?? '',
    issues: (p?.issues ?? []).map((i) => ({ topic: i.topic ?? '', stance: i.stance ?? '' })),
    discussion_style: comoTexto(p?.discussion_style), sources: comoTexto(p?.sources), values: comoTexto(p?.values),
    summary: comoTexto(p?.summary),
  };
}

function politicaDoForm(f: FormPolitica): BioPolitics | null {
  // Pauta sem tema não diz nada: sai da lista (o backend também a descarta).
  const issues: BioIssue[] = f.issues.filter((i) => i.topic.trim())
    .map((i) => ({ topic: i.topic.trim(), stance: paraTexto(i.stance) }));
  const p: BioPolitics = {
    orientation: (f.orientation || null) as OrientacaoPolitica | null,
    engagement: (f.engagement || null) as EngajamentoPolitico | null, issues,
    discussion_style: paraTexto(f.discussion_style), sources: paraLista(f.sources), values: paraLista(f.values),
    summary: paraTexto(f.summary),
  };
  return vazio(p) ? null : p;
}

function CartaoPolitica({ politica, onSalvar }: {
  politica: BioPolitics | null;
  onSalvar: (p: BioPolitics | null) => Promise<boolean>;
}) {
  const tituloId = useId();
  const [editando, setEditando] = useState(false);
  const p = politica ?? {};
  const engajamento = rotulo(ENGAJAMENTO_OPCOES, p.engagement);
  const pautas = (p.issues ?? []).filter((i) => i.topic?.trim());
  return (
    <article role="group" aria-labelledby={tituloId} className={styles.crenca}>
      <header className={styles.crencaTopo}>
        <div>
          <h4 id={tituloId} className={styles.crencaTitulo}><Landmark size={15} aria-hidden /> Política</h4>
          <p className={styles.crencaSub}>
            {rotuloDaOrientacao(p.orientation) ?? (politica ? 'Orientação não registrada' : 'Nada registrado ainda')}
          </p>
        </div>
        {!editando ? (
          <Button size="sm" variant="ghost" icon={Pencil} onClick={() => setEditando(true)}>Editar política</Button>
        ) : null}
      </header>
      {editando ? (
        <EditorPolitica inicial={politica} onCancelar={() => setEditando(false)}
                        onSalvar={async (novo) => { if (await onSalvar(novo)) setEditando(false); }} />
      ) : (
        <>
          {engajamento ? <div><Badge tone="info">Engajamento: {engajamento.toLowerCase()}</Badge></div> : null}
          {politica ? <EspectroPolitico orientacao={p.orientation} /> : null}
          {p.summary ? <p className={styles.crencaResumo}>{p.summary}</p> : null}
          <Bloco titulo="Pautas e posição" mostrar={pautas.length > 0}>
            <ul className={styles.pautas}>
              {pautas.map((i, k) => (
                <li key={`${i.topic}-${k}`} className={styles.pauta}>
                  <strong>{i.topic}</strong>
                  {i.stance ? <span>{i.stance}</span> : null}
                </li>
              ))}
            </ul>
          </Bloco>
          <Bloco titulo="Como fala de política" mostrar={!vazio(p.discussion_style)}>
            <p className={styles.crencaFala}>{p.discussion_style}</p>
          </Bloco>
          <Bloco titulo="Onde se informa" mostrar={!vazio(p.sources)}><Fichas itens={p.sources} /></Bloco>
          <Bloco titulo="Valores" mostrar={!vazio(p.values)}><Fichas itens={p.values} /></Bloco>
          {!politica ? <p className={styles.muted}>Sem política registrada: o modelo não recebe nada sobre política.</p> : null}
        </>
      )}
    </article>
  );
}

function EditorPolitica({ inicial, onSalvar, onCancelar }: {
  inicial: BioPolitics | null;
  onSalvar: (p: BioPolitics | null) => Promise<void>;
  onCancelar: () => void;
}) {
  const base = formDaPolitica(inicial);
  const [f, setF] = useState<FormPolitica>(base);
  const [salvando, setSalvando] = useState(false);
  const muda = (chave: Exclude<keyof FormPolitica, 'issues'>) => (e: { target: { value: string } }) =>
    setF((v) => ({ ...v, [chave]: e.target.value }));
  const mudaPauta = (k: number, campo: 'topic' | 'stance', valor: string) =>
    setF((v) => ({ ...v, issues: v.issues.map((i, j) => (j === k ? { ...i, [campo]: valor } : i)) }));
  const mudou = JSON.stringify(f) !== JSON.stringify(base);

  async function salvar() {
    if (!mudou || salvando) return;
    setSalvando(true);
    try {
      await onSalvar(politicaDoForm(f));
    } finally {
      setSalvando(false);
    }
  }

  return (
    <div className={styles.crencaForm}>
      <Field label="Orientação" hint="Um ponto no espectro, ou fora dele.">
        {({ id, describedBy }) => (
          <Select id={id} aria-describedby={describedBy} value={f.orientation} onChange={muda('orientation')}>
            <option value="">—</option>
            <optgroup label="No espectro">
              {ESPECTRO.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </optgroup>
            <optgroup label="Fora do espectro">
              {FORA_DO_ESPECTRO.map((o) => <option key={o.value} value={o.value}>{o.label} ({o.explica})</option>)}
            </optgroup>
          </Select>
        )}
      </Field>
      <Field label="Engajamento">
        {({ id }) => (
          <Select id={id} value={f.engagement} onChange={muda('engagement')}>
            <option value="">—</option>
            {ENGAJAMENTO_OPCOES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </Select>
        )}
      </Field>
      <fieldset className={cx(styles.pautasForm, styles.secaoCampoLargo)}>
        <legend>Pautas e posição</legend>
        {f.issues.length === 0 ? <p className={styles.muted}>Nenhuma pauta. Quem é apolítica pode ficar sem.</p> : null}
        {f.issues.map((i, k) => (
          <div key={k} className={styles.pautaLinha}>
            <Field label={`Tema ${k + 1}`}>
              {({ id }) => <TextInput id={id} value={i.topic} onChange={(e) => mudaPauta(k, 'topic', e.target.value)} />}
            </Field>
            <Field label={`Posição ${k + 1}`}>
              {({ id }) => <TextInput id={id} value={i.stance} onChange={(e) => mudaPauta(k, 'stance', e.target.value)} />}
            </Field>
            <Button size="sm" variant="ghost" icon={Trash2} iconOnly label={`Remover pauta ${k + 1}`}
                    onClick={() => setF((v) => ({ ...v, issues: v.issues.filter((_, j) => j !== k) }))} />
          </div>
        ))}
        <div>
          <Button size="sm" variant="ghost" icon={Plus}
                  onClick={() => setF((v) => ({ ...v, issues: [...v.issues, { topic: '', stance: '' }] }))}>
            Adicionar pauta
          </Button>
        </div>
      </fieldset>
      <Field label="Como fala de política" hint="Evita, ironiza, debate com calma, só com amigos…" className={styles.secaoCampoLargo}>
        {({ id, describedBy }) => <TextArea id={id} aria-describedby={describedBy} rows={2} value={f.discussion_style} onChange={muda('discussion_style')} />}
      </Field>
      <Field label="Onde se informa" hint="Um por linha, pelo TIPO de veículo (jornal local, podcast…), nunca nome de pessoa.">
        {({ id, describedBy }) => <TextArea id={id} aria-describedby={describedBy} rows={3} value={f.sources} onChange={muda('sources')} />}
      </Field>
      <Field label="Valores" hint="Um por linha.">
        {({ id, describedBy }) => <TextArea id={id} aria-describedby={describedBy} rows={3} value={f.values} onChange={muda('values')} />}
      </Field>
      <Field label="Resumo" className={styles.secaoCampoLargo}>
        {({ id }) => <TextArea id={id} rows={2} value={f.summary} onChange={muda('summary')} />}
      </Field>
      <div className={styles.crencaAcoes}>
        <Button size="sm" variant="ghost" icon={X} onClick={onCancelar}>Cancelar</Button>
        <Button size="sm" icon={Save} loading={salvando} disabledReason={mudou ? null : 'Nada mudou nesta crença.'}
                onClick={() => void salvar()}>
          Salvar política
        </Button>
      </div>
    </div>
  );
}
