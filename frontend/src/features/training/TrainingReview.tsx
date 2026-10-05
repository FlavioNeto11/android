/**
 * Revisão do treinamento (item 13.3) — é isto que faz o fluxo ser "inteligência assistida" e não macro:
 * a gravação aparece do lado esquerdo; a IA propõe, do lado direito, o COMANDO com parâmetros, as ETAPAS com
 * objetivo e verificação, o que foi DESCARTADO (erro, vai-e-volta) e as DÚVIDAS; a pessoa ajusta e escolhe quem
 * recebe. Salvar cria o fluxo + as receitas; o relatório diz, etapa a etapa, o que já roda sem IA. "Habilidade" aqui é
 * só a versionada (ensino v2, `TeachingPanel`), que com `features.skills` ligado é o caminho principal.
 */
import { RefreshCw, ServerCrash, Sparkles, WandSparkles } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { Capability, InstagramProfile, PolicyGroup, TrainingInput, TrainingProposal, TrainingSaveResult, TrainingSession, TrainingStep } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { Dialog } from '../../components/Dialog';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select, TextInput } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import { TeachingPanel } from './TeachingPanel';
import styles from './Training.module.css';

const TIPO: Record<string, string> = { tap: 'toque', long_press: 'toque longo', swipe: 'deslize', text: 'texto', key: 'tecla', open_app: 'abrir app' };

/** O motivo que vai no `discarded[].why` quando é a pessoa, e não a IA, que tira a entrada da etapa. */
export const MOTIVO_DE_QUEM_ENSINOU = 'descartada por quem ensinou';

// 31.90-A: cada entrada tem um lugar só, numa etapa OU no descarte. O save recusa entrada sem destino
// (`entradas_sem_etapa`) e entrada em dois lugares (`entrada_duplicada`); por isso as duas ações tiram a entrada de
// todo outro lugar antes de pô-la no novo.
function descartarEntrada(p: TrainingProposal, seq: number): TrainingProposal {
  return {
    ...p,
    steps: p.steps.map((s) => (s.inputs.includes(seq) ? { ...s, inputs: s.inputs.filter((n) => n !== seq) } : s)),
    discarded: p.discarded.some((d) => d.seq === seq)
      ? p.discarded
      : [...p.discarded, { seq, why: MOTIVO_DE_QUEM_ENSINOU }].sort((a, b) => a.seq - b.seq),
  };
}

function devolverEntrada(p: TrainingProposal, seq: number, etapa: number): TrainingProposal {
  return {
    ...p,
    steps: p.steps.map((s, k) => {
      const sem = s.inputs.filter((n) => n !== seq);
      // A ordem das entradas na etapa é a ordem em que foram feitas: a devolvida entra no lugar dela.
      if (k === etapa) return { ...s, inputs: [...sem, seq].sort((a, b) => a - b) };
      return sem.length === s.inputs.length ? s : { ...s, inputs: sem };
    }),
    discarded: p.discarded.filter((d) => d.seq !== seq),
  };
}

const listaDeSeqs = (seqs: number[]) => seqs.map((n) => `#${n}`).join(', ');

/** O que a entrada foi. Texto não gravado (tela sensível, senha, cara de segredo) nunca tem valor na tela. */
function DescricaoEntrada({ e }: { e: TrainingInput }) {
  return (
    <>
      {TIPO[e.type] ?? e.type}
      {e.target?.text || e.target?.desc ? <> em <strong>{e.target.text || e.target.desc}</strong></> : null}
      {e.type === 'text' ? (e.text !== null && !e.sensitive ? <> “{e.text}”</> : <span className={styles.muted}> (texto não gravado)</span>) : null}
      {e.type === 'open_app' ? <> {e.app_id}</> : null}
      {e.type === 'key' ? <> {e.key_name}</> : null}
    </>
  );
}

interface Falha {
  message: string;
  hint: string;
}

function falhaDe(e: unknown): Falha {
  const err = toApiError(e);
  return { message: err.message, hint: hintForError(err) };
}

/**
 * Perfis e grupos do escopo. "Nada marcado = todos os perfis" só é verdade quando a lista carregou: com a carga
 * falha, a lista vazia mentiria, e a pessoa salvaria para todos sem saber. Por isso o erro fica guardado aqui e
 * bloqueia o "Salvar".
 */
interface Escopo {
  perfis: InstagramProfile[];
  grupos: PolicyGroup[];
  carregando: boolean;
  erro: Falha | null;
}

export function TrainingReview({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [sessao, setSessao] = useState<TrainingSession | null>(null);
  const [falhaSessao, setFalhaSessao] = useState<Falha | null>(null);
  const [tentativa, setTentativa] = useState(0);
  const [proposta, setProposta] = useState<TrainingProposal | null>(null);
  const [escopo, setEscopo] = useState<Escopo>({ perfis: [], grupos: [], carregando: true, erro: null });
  const [acoes, setAcoes] = useState<Capability[]>([]);
  const [escolhidosP, setEscolhidosP] = useState<Set<string>>(new Set());
  const [escolhidosG, setEscolhidosG] = useState<Set<string>>(new Set());
  // Edições da pessoa: na proposta (comando, etapas, destino das entradas, ações do catálogo) e no escopo. Outra proposta substitui só a
  // primeira; fechar perde as duas. Descartá-las sem perguntar é o que P2.6 proíbe.
  const [editado, setEditado] = useState(false);
  const [escopoMudou, setEscopoMudou] = useState(false);
  const [pensando, setPensando] = useState(false);
  const [salvando, setSalvando] = useState(false);
  const [resultado, setResultado] = useState<TrainingSaveResult | null>(null);

  useEffect(() => {
    let vivo = true;
    setFalhaSessao(null);
    void api.getTraining(sessionId).then((s) => {
      if (!vivo) return;
      setSessao(s);
      setProposta(s.proposal);
      if (s.profile_id) setEscolhidosP(new Set([s.profile_id]));
    }).catch((e) => { if (vivo) setFalhaSessao(falhaDe(e)); });
    return () => { vivo = false; };
  }, [sessionId, tentativa]);

  const carregarEscopo = useCallback(async () => {
    setEscopo((x) => ({ ...x, carregando: true, erro: null }));
    const [p, g] = await Promise.allSettled([api.listProfiles(), api.listPolicyGroups()]);
    const falha = [p, g].find((r): r is PromiseRejectedResult => r.status === 'rejected');
    setEscopo({
      perfis: p.status === 'fulfilled' ? p.value : [],
      grupos: g.status === 'fulfilled' ? g.value : [],
      carregando: false,
      erro: falha ? falhaDe(falha.reason) : null,
    });
  }, []);

  useEffect(() => {
    void carregarEscopo();
  }, [carregarEscopo]);

  // Fase F: com `features.skills` ligado, a revisão ganha o ensino v2, que passa a ser o caminho principal (o único
  // botão primário do diálogo); o fluxo de sempre vira o caminho secundário. Desligado (ou backend sem o campo), o
  // painel é exatamente o de antes.
  const ensinoV2 = useAppStore((st) => st.health?.features?.skills === true);

  // Catálogo do app (se houver): etapa com efeito num app com catálogo precisa dizer QUAL ação ela é.
  const appsDoStore = useAppStore((st) => st.apps);
  const appDaProposta = proposta?.app_id ?? sessao?.app_id ?? null;
  const pacote = appsDoStore.find((a) => a.id === appDaProposta)?.package ?? null;
  useEffect(() => {
    if (!pacote) return;
    void api.listAppCatalog().then(async (cat) => {
      const app = cat.find((a) => a.package === pacote);
      setAcoes(app && app.has_catalog ? await api.listCapabilities(pacote) : []);
    }).catch(() => setAcoes([]));
  }, [pacote]);

  const porSeq = useMemo(() => new Map((sessao?.inputs ?? []).map((e) => [e.seq, e])), [sessao]);
  const descartadas = new Set((proposta?.discarded ?? []).map((d) => d.seq));
  // Quantos lugares cada entrada ocupa na proposta: zero é "sem destino", mais de um é duplicada.
  const lugares = useMemo(() => {
    const m = new Map<number, number>();
    for (const s of proposta?.steps ?? []) for (const n of s.inputs) m.set(n, (m.get(n) ?? 0) + 1);
    for (const d of proposta?.discarded ?? []) m.set(d.seq, (m.get(d.seq) ?? 0) + 1);
    return m;
  }, [proposta]);
  const semDestino = proposta ? (sessao?.inputs ?? []).filter((e) => !lugares.has(e.seq)) : [];
  const duplicadas = [...lugares].filter(([, n]) => n > 1).map(([seq]) => seq).sort((a, b) => a - b);

  async function pedirProposta() {
    if (editado) {
      const { confirmed } = await confirm({
        title: 'Pedir outra proposta?',
        confirmLabel: 'Pedir outra proposta',
        cancelLabel: 'Voltar',
        body: 'A proposta atual e o que você mudou nela (comando, etapas, destino das entradas, ações do catálogo) são substituídos pela nova. Quem recebe o fluxo continua marcado.',
      });
      if (!confirmed) return;
    }
    setPensando(true);
    try {
      const s = await api.proposeTraining(sessionId);
      setSessao(s);
      setProposta(s.proposal);
      setEditado(false);
    } catch (e) {
      toastError('A IA não conseguiu propor o fluxo', e);
    } finally {
      setPensando(false);
    }
  }

  function mudarProposta(parcial: Partial<TrainingProposal>) {
    setProposta((p) => (p ? { ...p, ...parcial } : p));
    setEditado(true);
  }

  function mudarEtapa(i: number, parcial: Partial<TrainingStep>) {
    setProposta((p) => (p ? { ...p, steps: p.steps.map((s, k) => (k === i ? { ...s, ...parcial } : s)) } : p));
    setEditado(true);
  }

  function descartar(seq: number) {
    setProposta((p) => (p ? descartarEntrada(p, seq) : p));
    setEditado(true);
  }

  function devolver(seq: number, etapa: number) {
    setProposta((p) => (p ? devolverEntrada(p, seq, etapa) : p));
    setEditado(true);
  }

  async function salvar() {
    if (!proposta) return;
    setSalvando(true);
    try {
      const r = await api.saveTraining(sessionId, { proposal: proposta, profile_ids: [...escolhidosP], group_ids: [...escolhidosG] });
      setResultado(r);
      const avisos = r.warnings ?? [];
      toast({ tone: 'success', title: 'Fluxo salvo',
              message: `${r.steps.filter((x) => x.recipe).length} de ${r.steps.length} etapas já rodam sem IA.${avisos.length ? ` Avisos: ${avisos.join(' · ')}` : ''}` });
    } catch (e) {
      toastError('Não foi possível salvar o fluxo', e);
    } finally {
      setSalvando(false);
    }
  }

  // "Depois", Esc e o clique no fundo passam por aqui: com edição pendente, a pessoa confirma antes de perder.
  async function fechar() {
    if ((editado || escopoMudou) && proposta && !resultado) {
      const { confirmed } = await confirm({
        title: 'Sair sem salvar o fluxo?',
        danger: true,
        confirmLabel: 'Sair sem salvar',
        cancelLabel: 'Voltar',
        body: 'O que você mudou na proposta (comando, etapas, destino das entradas, quem recebe) se perde. A gravação continua na lista "Para revisar".',
      });
      if (!confirmed) return;
    }
    onClose();
  }

  const alterna = (set: Set<string>, id: string) => {
    const n = new Set(set);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    return n;
  };

  function motivoNaoSalvar(): string | null {
    if (!proposta) return 'Peça a proposta da IA primeiro.';
    // O save recusaria as duas (`entradas_sem_etapa`, `entrada_duplicada`): a tela diz antes e diz como resolver.
    if (semDestino.length) return `Falta destino para ${listaDeSeqs(semDestino.map((e) => e.seq))}: descarte ou devolva a uma etapa.`;
    if (duplicadas.length) return `${listaDeSeqs(duplicadas)} está em mais de um lugar: descarte ou devolva a uma etapa só.`;
    if (escopo.carregando) return 'Aguarde a lista de perfis e grupos.';
    if (escopo.erro) return 'A lista de perfis e grupos não carregou: sem ela, "nada marcado" não quer dizer "todos". Tente de novo.';
    return null;
  }

  const duplicada = new Set(duplicadas);
  const linhaDaEntrada = (seq: number) => {
    const e = porSeq.get(seq);
    return (
      <>
        {e ? <DescricaoEntrada e={e} /> : <span className={styles.muted}>entrada que a gravação não tem</span>}
        {duplicada.has(seq) ? <> <Badge size="sm" tone="warning">em mais de um lugar</Badge></> : null}
      </>
    );
  };

  const acoesDaEntrada = (seq: number, comDevolver: boolean, comDescartar = true) => (
    <span className={styles.entryActions}>
      {comDescartar ? (
        <Button size="sm" variant="ghost" label={`Descartar a entrada #${seq}`} onClick={() => descartar(seq)}>Descartar</Button>
      ) : null}
      {comDevolver && proposta?.steps.length ? (
        <Select small aria-label={`Devolver a entrada #${seq} à etapa`} value=""
                onChange={(ev) => { if (ev.target.value !== '') devolver(seq, Number(ev.target.value)); }}>
          <option value="">Devolver à etapa…</option>
          {proposta.steps.map((s, i) => <option key={s.key} value={i}>{`${i + 1}. ${s.title}`}</option>)}
        </Select>
      ) : null}
    </span>
  );

  return (
    <Dialog open onClose={() => void fechar()} title={sessao ? `Treinamento: ${sessao.intent}` : 'Treinamento'} icon={WandSparkles} size="lg"
            footer={resultado ? <Button onClick={onClose}>Fechar</Button> : (
              <>
                <Button variant="ghost" onClick={() => void fechar()}>Depois</Button>
                <Button variant={proposta && !ensinoV2 ? 'primary' : 'secondary'} icon={Sparkles} loading={salvando}
                        disabledReason={motivoNaoSalvar()} onClick={() => void salvar()}>
                  Salvar como fluxo
                </Button>
              </>
            )}>
      {!sessao ? (
        falhaSessao ? (
          <EmptyState icon={ServerCrash} tone="danger" compact title="Não foi possível abrir o treinamento" hint={falhaSessao.hint}
                      actions={<Button variant="outline" icon={RefreshCw} onClick={() => setTentativa((t) => t + 1)}>Tentar de novo</Button>}>
            {falhaSessao.message}
          </EmptyState>
        ) : (
          <LoadingRegion label="Carregando o treinamento…">
            <Skeleton height={120} radius={8} />
          </LoadingRegion>
        )
      ) : resultado ? (
        <div className={styles.result}>
          <p>Fluxo <strong>{resultado.flow_id}</strong> salvo. Quem estiver no escopo pode pedir pelo comando:</p>
          <code className={styles.command}>{proposta?.command_template}</code>
          <ul className={styles.stepReport}>
            {resultado.steps.map((s) => (
              <li key={s.key}>
                <Badge size="sm" tone={s.recipe ? 'success' : 'neutral'}>{s.recipe ? 'sem IA' : 'com IA'}</Badge> {s.title}
                <span className={styles.muted}> — {s.reason}</span>
              </li>
            ))}
          </ul>
          {resultado.warnings?.length ? (
            <ul className={styles.questions} aria-label="Avisos do salvar">{resultado.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          ) : null}
        </div>
      ) : (
        <div className={styles.review}>
          <div className={styles.recordingCol}>
            <h4 className={styles.sub}>O que você fez ({sessao.inputs?.length ?? 0} entradas)</h4>
            <ol className={styles.inputList}>
              {(sessao.inputs ?? []).map((e) => (
                <li key={e.seq} className={descartadas.has(e.seq) ? styles.discarded : undefined}>
                  <span className={styles.seq}>#{e.seq}</span>
                  <span>
                    <DescricaoEntrada e={e} />
                    {e.screen_title ? <span className={styles.muted}> · tela {e.screen_title}</span> : null}
                  </span>
                </li>
              ))}
            </ol>
          </div>

          <div className={styles.proposalCol}>
            {!proposta ? (
              <div className={styles.ask}>
                <p>A IA vai ler a gravação e propor o fluxo: o comando com o que varia, as etapas com o objetivo de cada
                  uma e o que foi engano. Uma chamada do modelo do planejador (poucos centavos).</p>
                <Button variant={ensinoV2 ? 'secondary' : 'primary'} icon={WandSparkles} loading={pensando} onClick={() => void pedirProposta()}>Pedir proposta à IA</Button>
              </div>
            ) : (
              <>
                {semDestino.length ? (
                  <section className={styles.semDestino} aria-label="Sem destino">
                    <h4 className={styles.sub}>Sem destino ({semDestino.length})</h4>
                    <p className={styles.muted}>Entradas gravadas que não estão em nenhuma etapa nem no descarte. Dê um destino a cada uma para salvar.</p>
                    <ul className={styles.entryList}>
                      {semDestino.map((e) => (
                        <li key={e.seq}>
                          <span className={styles.seq}>#{e.seq}</span>
                          <span className={styles.entryText}><DescricaoEntrada e={e} /></span>
                          {acoesDaEntrada(e.seq, true)}
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}
                <Field label="Comando (o que varia fica entre chaves)">
                  {({ id }) => (
                    <TextInput id={id} value={proposta.command_template}
                               onChange={(e) => mudarProposta({ command_template: e.target.value })} />
                  )}
                </Field>
                {proposta.parameters.length ? (
                  <p className={styles.params}>
                    {proposta.parameters.map((p) => <Badge key={p.name} size="sm" tone="accent">{`{${p.name}}`} = {p.example}</Badge>)}
                  </p>
                ) : null}
                {proposta.questions.length ? (
                  <ul className={styles.questions}>{proposta.questions.map((q) => <li key={q}>{q}</li>)}</ul>
                ) : null}
                <ol className={styles.steps}>
                  {proposta.steps.map((s, i) => (
                    <li key={s.key} className={styles.step}>
                      <div className={styles.stepHead}>
                        <TextInput aria-label={`Título da etapa ${i + 1}`} value={s.title} onChange={(e) => mudarEtapa(i, { title: e.target.value })} />
                        {s.side_effect ? <Badge size="sm" tone="warning">efeito externo</Badge> : null}
                      </div>
                      <TextInput aria-label={`Objetivo da etapa ${i + 1}`} value={s.goal} onChange={(e) => mudarEtapa(i, { goal: e.target.value })} />
                      {s.inputs.length ? (
                        <ul className={styles.entryList} aria-label={`Entradas da etapa ${i + 1}`}>
                          {s.inputs.map((n, k) => (
                            <li key={`${n}-${k}`}>
                              <span className={styles.seq}>#{n}</span>
                              <span className={styles.entryText}>{linhaDaEntrada(n)}</span>
                              {acoesDaEntrada(n, false)}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      <p className={styles.muted}>Confere: {s.postcondition.kind} {s.postcondition.value ? `“${s.postcondition.value}”` : ''}
                        {s.inputs.map((n) => porSeq.get(n)).filter(Boolean).length ? '' : ' · sem entradas: a IA conduz esta etapa'}</p>
                      {acoes.length && (s.side_effect || s.capability) ? (
                        <Select aria-label={`Ação do catálogo da etapa ${i + 1}`} value={s.capability ?? ''}
                                onChange={(e) => mudarEtapa(i, { capability: e.target.value || null })}>
                          <option value="">{s.side_effect ? 'Escolha a ação do catálogo…' : 'Sem ação do catálogo'}</option>
                          {acoes.map((c) => <option key={c.key} value={c.key}>{c.title}</option>)}
                        </Select>
                      ) : null}
                    </li>
                  ))}
                </ol>
                {proposta.discarded.length ? (
                  <section aria-label="Descartadas">
                    <h4 className={styles.sub}>Descartadas ({proposta.discarded.length})</h4>
                    <ul className={styles.entryList}>
                      {proposta.discarded.map((d, k) => (
                        <li key={`${d.seq}-${k}`}>
                          <span className={styles.seq}>#{d.seq}</span>
                          <span className={styles.entryText}>{linhaDaEntrada(d.seq)} <span className={styles.muted}>— {d.why}</span></span>
                          {acoesDaEntrada(d.seq, true, false)}
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}
                <fieldset className={styles.scope}>
                  <legend>Quem recebe o fluxo</legend>
                  {escopo.erro ? (
                    <Banner tone="danger" icon={ServerCrash} compact role="alert" title="A lista de perfis e grupos não carregou"
                            actions={<Button size="sm" variant="outline" icon={RefreshCw} loading={escopo.carregando} onClick={() => void carregarEscopo()}>Tentar de novo</Button>}>
                      {escopo.erro.message} {escopo.erro.hint}
                    </Banner>
                  ) : escopo.carregando ? (
                    <LoadingRegion label="Carregando perfis e grupos…">
                      <Skeleton height={20} width="60%" />
                    </LoadingRegion>
                  ) : (
                    <>
                      <p className={styles.muted}>Nada marcado = todos os perfis.</p>
                      <div className={styles.scopeGrid}>
                        {escopo.grupos.map((g) => (
                          <label key={g.id}><input type="checkbox" aria-label={`Grupo ${g.name}`} checked={escolhidosG.has(g.id)}
                                                   onChange={() => { setEscolhidosG((x) => alterna(x, g.id)); setEscopoMudou(true); }} /> grupo {g.name}</label>
                        ))}
                        {escopo.perfis.map((p) => (
                          <label key={p.id}><input type="checkbox" aria-label={`@${p.username}`} checked={escolhidosP.has(p.id)}
                                                   onChange={() => { setEscolhidosP((x) => alterna(x, p.id)); setEscopoMudou(true); }} /> @{p.username}</label>
                        ))}
                      </div>
                    </>
                  )}
                </fieldset>
                <Button size="sm" variant="ghost" icon={WandSparkles} loading={pensando} onClick={() => void pedirProposta()}>Pedir outra proposta</Button>
              </>
            )}
            {ensinoV2 ? <TeachingPanel trainingSessionId={sessao.id} intent={sessao.intent} appId={sessao.app_id} /> : null}
          </div>
        </div>
      )}
    </Dialog>
  );
}
