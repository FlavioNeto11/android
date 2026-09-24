/**
 * Revisão do treinamento (item 13.3) — é isto que faz a habilidade ser "inteligência assistida" e não macro:
 * a gravação aparece do lado esquerdo; a IA propõe, do lado direito, o COMANDO com parâmetros, as ETAPAS com
 * objetivo e verificação, o que foi DESCARTADO (erro, vai-e-volta) e as DÚVIDAS; a pessoa ajusta e escolhe quem
 * recebe. Salvar cria o fluxo + as receitas; o relatório diz, etapa a etapa, o que já roda sem IA.
 */
import { Sparkles, WandSparkles } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { Capability, InstagramProfile, PolicyGroup, TrainingProposal, TrainingSaveResult, TrainingSession, TrainingStep } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { Field, Select, TextInput } from '../../components/Field';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import styles from './Training.module.css';

const TIPO: Record<string, string> = { tap: 'toque', long_press: 'toque longo', swipe: 'deslize', text: 'texto', key: 'tecla', open_app: 'abrir app' };

export function TrainingReview({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [sessao, setSessao] = useState<TrainingSession | null>(null);
  const [proposta, setProposta] = useState<TrainingProposal | null>(null);
  const [perfis, setPerfis] = useState<InstagramProfile[]>([]);
  const [grupos, setGrupos] = useState<PolicyGroup[]>([]);
  const [acoes, setAcoes] = useState<Capability[]>([]);
  const [escolhidosP, setEscolhidosP] = useState<Set<string>>(new Set());
  const [escolhidosG, setEscolhidosG] = useState<Set<string>>(new Set());
  const [pensando, setPensando] = useState(false);
  const [salvando, setSalvando] = useState(false);
  const [resultado, setResultado] = useState<TrainingSaveResult | null>(null);

  useEffect(() => {
    void api.getTraining(sessionId).then((s) => {
      setSessao(s);
      setProposta(s.proposal);
      if (s.profile_id) setEscolhidosP(new Set([s.profile_id]));
    }).catch((e) => toastError('Não foi possível abrir o treinamento', e));
    void api.listProfiles().then(setPerfis).catch(() => undefined);
    void api.listPolicyGroups().then(setGrupos).catch(() => undefined);
  }, [sessionId]);

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

  async function pedirProposta() {
    setPensando(true);
    try {
      const s = await api.proposeTraining(sessionId);
      setSessao(s);
      setProposta(s.proposal);
    } catch (e) {
      toastError('A IA não conseguiu propor a habilidade', e);
    } finally {
      setPensando(false);
    }
  }

  function mudarEtapa(i: number, parcial: Partial<TrainingStep>) {
    setProposta((p) => (p ? { ...p, steps: p.steps.map((s, k) => (k === i ? { ...s, ...parcial } : s)) } : p));
  }

  async function salvar() {
    if (!proposta) return;
    setSalvando(true);
    try {
      const r = await api.saveTraining(sessionId, { proposal: proposta, profile_ids: [...escolhidosP], group_ids: [...escolhidosG] });
      setResultado(r);
      toast({ tone: 'success', title: 'Habilidade salva', message: `${r.steps.filter((x) => x.recipe).length} de ${r.steps.length} etapas já rodam sem IA.` });
    } catch (e) {
      toastError('Não foi possível salvar a habilidade', e);
    } finally {
      setSalvando(false);
    }
  }

  const alterna = (set: Set<string>, id: string) => {
    const n = new Set(set);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    return n;
  };

  return (
    <Dialog open onClose={onClose} title={sessao ? `Treinamento: ${sessao.intent}` : 'Treinamento'} icon={WandSparkles} size="lg"
            footer={resultado ? <Button onClick={onClose}>Fechar</Button> : (
              <>
                <Button variant="ghost" onClick={onClose}>Depois</Button>
                <Button variant="primary" icon={Sparkles} loading={salvando}
                        disabledReason={proposta ? null : 'Peça a proposta da IA primeiro.'} onClick={() => void salvar()}>
                  Salvar habilidade
                </Button>
              </>
            )}>
      {!sessao ? <p className={styles.muted}>Carregando…</p> : resultado ? (
        <div className={styles.result}>
          <p>Habilidade <strong>{resultado.flow_id}</strong> salva. Quem estiver no escopo pode pedir pelo comando:</p>
          <code className={styles.command}>{proposta?.command_template}</code>
          <ul className={styles.stepReport}>
            {resultado.steps.map((s) => (
              <li key={s.key}>
                <Badge size="sm" tone={s.recipe ? 'success' : 'neutral'}>{s.recipe ? 'sem IA' : 'com IA'}</Badge> {s.title}
                <span className={styles.muted}> — {s.reason}</span>
              </li>
            ))}
          </ul>
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
                    {TIPO[e.type] ?? e.type}
                    {e.target?.text || e.target?.desc ? <> em <strong>{e.target.text || e.target.desc}</strong></> : null}
                    {e.type === 'text' ? (e.text !== null ? <> “{e.text}”</> : <> (sigiloso)</>) : null}
                    {e.type === 'open_app' ? <> {e.app_id}</> : null}
                    {e.type === 'key' ? <> {e.key_name}</> : null}
                    {e.screen_title ? <span className={styles.muted}> · tela {e.screen_title}</span> : null}
                  </span>
                </li>
              ))}
            </ol>
          </div>

          <div className={styles.proposalCol}>
            {!proposta ? (
              <div className={styles.ask}>
                <p>A IA vai ler a gravação e propor a habilidade: o comando com o que varia, as etapas com o objetivo de cada
                  uma e o que foi engano. Uma chamada do modelo do planejador (poucos centavos).</p>
                <Button variant="primary" icon={WandSparkles} loading={pensando} onClick={() => void pedirProposta()}>Pedir proposta à IA</Button>
              </div>
            ) : (
              <>
                <Field label="Comando (o que varia fica entre chaves)">
                  {({ id }) => (
                    <TextInput id={id} value={proposta.command_template}
                               onChange={(e) => setProposta({ ...proposta, command_template: e.target.value })} />
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
                        <span className={styles.muted}>{s.inputs.map((n) => `#${n}`).join(' ')}</span>
                      </div>
                      <TextInput aria-label={`Objetivo da etapa ${i + 1}`} value={s.goal} onChange={(e) => mudarEtapa(i, { goal: e.target.value })} />
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
                  <p className={styles.muted}>Descartadas: {proposta.discarded.map((d) => `#${d.seq} (${d.why})`).join(' · ')}</p>
                ) : null}
                <fieldset className={styles.scope}>
                  <legend>Quem recebe a habilidade</legend>
                  <p className={styles.muted}>Nada marcado = todos os perfis.</p>
                  <div className={styles.scopeGrid}>
                    {grupos.map((g) => (
                      <label key={g.id}><input type="checkbox" aria-label={`Grupo ${g.name}`} checked={escolhidosG.has(g.id)}
                                               onChange={() => setEscolhidosG((x) => alterna(x, g.id))} /> grupo {g.name}</label>
                    ))}
                    {perfis.map((p) => (
                      <label key={p.id}><input type="checkbox" aria-label={`@${p.username}`} checked={escolhidosP.has(p.id)}
                                               onChange={() => setEscolhidosP((x) => alterna(x, p.id))} /> @{p.username}</label>
                    ))}
                  </div>
                </fieldset>
                <Button size="sm" variant="ghost" icon={WandSparkles} loading={pensando} onClick={() => void pedirProposta()}>Pedir outra proposta</Button>
              </>
            )}
          </div>
        </div>
      )}
    </Dialog>
  );
}

