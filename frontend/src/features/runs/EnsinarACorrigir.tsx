/**
 * 31.111 F5 (adendo v1.75): "Ensinar a corrigir" na etapa que falhou ou ficou sem prova. A pessoa ensina a tarefa no
 * aparelho da etapa e o treino nasce LIGADO a ela (`POST /api/training/from-run`), com o contexto da execução na
 * revisão. Nada é automático: o botão pede o controle do aparelho (a IA fica em espera nele) só depois da escolha da
 * pessoa, e o treino abre no Foco. É o ÚNICO caminho de ensino na etapa (31.91 T1, ADR-078): o ensino por habilidade (v2)
 * saiu da tela.
 *
 * 31.116 parte 2 (adendo v1.80): ao abrir, o campo vem preenchido com a intenção que o diagnóstico da falha sugere
 * (`GET .../ensino-sugerido`), e a causa provável e o que mostrar aparecem como dica. A intenção da pessoa vence: só
 * se manda `intent` quando o texto difere da sugestão (o backend usa a mesma sugestão quando o `intent` falta).
 * "O que mostrar" fica em destaque acima do campo, "Lendo a sugestão…" cobre a espera, e "Voltar à sugestão" desfaz a edição.
 *
 * 31.124: o formulário avisa que a etapa JÁ foi ensinada (sessão salva, com ligação para abri-la em leitura e para o fluxo
 * no Livro) ou que já há treino aberto dela, e pede confirmação para ensinar de novo. Só lê `GET /api/training`.
 * 31.125: o mesmo formulário abre também pelo atalho do cartão de resultado e do cabeçalho do aparelho
 * (`AtalhoParaEnsinar`), sem precisar descer até a etapa.
 */
import { Wrench } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { api, toApiError } from '../../api/client';
import type { EnsinoSugerido, PersonaOnDevice, RunDetail, Step, StepStatus, TrainingSession } from '../../api/types';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { Dialog } from '../../components/Dialog';
import { Field, Select, TextArea } from '../../components/Field';
import { hashDe } from '../../lib/rotas';
import { tempoRelativo, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { useControlStore } from '../../store/control';
import { toast } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { SessaoSalva } from '../training/SessaoSalva';
import { personasDoEnsino } from '../training/TrainingBar';
import { currentSteps } from './model';
import styles from './EnsinarACorrigir.module.css';

/**
 * As etapas de onde se ensina: a que falhou, a que ficou sem prova e a que parou esperando uma pessoa (o backend aceita
 * as três: `training/origem.py::STATUS_ENSINAVEIS`, 31.111 A).
 */
export const ETAPA_CORRIGIVEL: ReadonlySet<StepStatus> = new Set<StepStatus>(['failed', 'uncertain', 'waiting_user']);

/** 31.125: a primeira etapa (na ordem do plano atual) de onde dá para ensinar; `null` se nenhuma. */
export function primeiraEtapaAEnsinar(steps: readonly Step[]): Step | null {
  return steps.find((s) => ETAPA_CORRIGIVEL.has(s.status)) ?? null;
}

/** 31.125: a primeira etapa de onde ensinar na execução inteira: o primeiro objetivo (na ordem da execução) que tem uma. */
export function primeiraEtapaAEnsinarDaExecucao(detail: Pick<RunDetail, 'objectives' | 'steps'>): Step | null {
  for (const o of detail.objectives) {
    const achada = primeiraEtapaAEnsinar(currentSteps(detail as RunDetail, o));
    if (achada) return achada;
  }
  return null;
}

/**
 * A linha da causa pelo CÓDIGO do diagnóstico (v1.82), nunca pela frase: `null` = sem linha (o diagnóstico falhou),
 * `indeterminada` = "não deu para saber". Sem o campo (backend anterior ao v1.82) vale o rótulo, como no v1.80.
 */
export function linhaDaCausa(s: Pick<EnsinoSugerido, 'rotulo' | 'causa'>): string | null {
  if (s.causa === null) return null;
  if (s.causa === 'indeterminada') return 'Causa: não deu para saber.';
  return s.rotulo ? `Causa provável: ${s.rotulo}.` : null;
}

/** O texto de abertura que o backend usaria sozinho (adendo v1.75): só se manda quando a pessoa o reescreve. */
export function intencaoDaCorrecao(titulo: string): string {
  return `Corrigir a etapa «${titulo}»`.slice(0, 400);
}

/** Quantas sessões o aviso lista de cada tipo; o resto vira "e mais N". */
const MAXIMO_NO_AVISO = 3;

const ESTADO_EM_ABERTO: Record<string, string> = { recording: 'gravando agora', recorded: 'gravado, falta revisar', proposed: 'com proposta, falta salvar' };

/**
 * 31.124: o que o histórico de treinos diz desta etapa. A etapa é a mesma pela execução e pela chave (o plano revisto
 * troca a versão no id, mas é a mesma etapa para quem ensina). Descartada não conta: nada dela ficou.
 */
export function ensinoDaEtapa(treinos: readonly TrainingSession[], runId: string, stepKey: string): { salvas: TrainingSession[]; emAberto: TrainingSession[] } {
  const daEtapa = treinos.filter((t) => t.origin?.run_id === runId && t.origin.step_key === stepKey);
  const recente = (a: TrainingSession, b: TrainingSession) => (b.updated_at || b.created_at).localeCompare(a.updated_at || a.created_at);
  return {
    salvas: daEtapa.filter((t) => t.status === 'saved').sort(recente),
    emAberto: daEtapa.filter((t) => t.status === 'recording' || t.status === 'recorded' || t.status === 'proposed').sort(recente),
  };
}

function AvisoDeEtapaEnsinada({ salvas, emAberto, abrirSessao }: {
  salvas: readonly TrainingSession[]; emAberto: readonly TrainingSession[]; abrirSessao: (id: string) => void;
}) {
  const agora = useNow();
  if (!salvas.length && !emAberto.length) return null;
  return (
    <div className={styles.aviso} role="status" aria-label="O que já foi ensinado nesta etapa">
      {salvas.length ? (
        <>
          <p className={styles.avisoTitulo}><strong>Esta etapa já foi ensinada.</strong> Se o que foi ensinado não resolveu, ensine de novo; senão, confira o fluxo antes.</p>
          <ul className={styles.avisoLista}>
            {salvas.slice(0, MAXIMO_NO_AVISO).map((t) => (
              <li key={t.id}>
                <span>«{t.intent}» · salvo {tempoRelativo(t.updated_at || t.finished_at || t.created_at, agora)}</span>
                <Button size="sm" variant="ghost" onClick={() => abrirSessao(t.id)} label={`Abrir o treino salvo «${t.intent}»`}>Abrir o treino salvo</Button>
                {t.flow_id ? <a className={styles.avisoLink} href={hashDe('aprendizado', { query: { aba: 'aprendido', item: `fluxo:${t.flow_id}` } })}>Ver o fluxo no Livro</a> : null}
              </li>
            ))}
          </ul>
          {salvas.length > MAXIMO_NO_AVISO ? <p className={styles.rotulo}>e mais {salvas.length - MAXIMO_NO_AVISO} {salvas.length - MAXIMO_NO_AVISO === 1 ? 'treino salvo' : 'treinos salvos'}.</p> : null}
        </>
      ) : null}
      {emAberto.length ? (
        <>
          <p className={styles.avisoTitulo}><strong>Já há treino aberto desta etapa.</strong> Ele aparece em «Para revisar» ou no Foco do aparelho.</p>
          <ul className={styles.avisoLista}>
            {emAberto.slice(0, MAXIMO_NO_AVISO).map((t) => (
              <li key={t.id}><span>«{t.intent}» · {ESTADO_EM_ABERTO[t.status] ?? t.status}</span></li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}

/** O que a pessoa lê na confirmação de ensinar uma etapa que já tem ensino. */
function corpoDaConfirmacao(salvas: readonly TrainingSession[], emAberto: readonly TrainingSession[]): string {
  const partes: string[] = [];
  if (salvas[0]) partes.push(`Esta etapa já foi ensinada em «${salvas[0].intent}»${salvas.length > 1 ? ` (e em mais ${salvas.length - 1})` : ''}.`);
  if (emAberto[0]) partes.push(`Já há um treino aberto dela: «${emAberto[0].intent}».`);
  partes.push('Ensinar de novo abre outra sessão de gravação; nada do que foi ensinado antes é apagado.');
  return partes.join(' ');
}

interface FormularioProps {
  detail: Pick<RunDetail, 'id'>;
  step: Step;
  id?: string;
  /** `abriu`: o treino abriu e o Foco veio; `cancelou`: a pessoa desistiu. */
  onClose: (motivo: 'abriu' | 'cancelou') => void;
}

/** O formulário em si: monta quando se abre (as leituras começam aí) e não guarda nada quando se fecha. */
export function FormularioDeEnsino({ detail, step, id: formId, onClose }: FormularioProps) {
  const padrao = intencaoDaCorrecao(step.title);
  const [texto, setTexto] = useState(padrao);
  const [sugestao, setSugestao] = useState<EnsinoSugerido | null>(null);
  const [lendoSugestao, setLendoSugestao] = useState(false);
  const editou = useRef(false);                                  // a pessoa mexeu no campo: a sugestão que chega depois não o sobrescreve
  const [personas, setPersonas] = useState<PersonaOnDevice[] | null>(null);
  const [treinos, setTreinos] = useState<TrainingSession[]>([]);
  // A leitura do histórico em andamento: quem clica em abrir antes de ela chegar espera por ela, para o aviso nunca ser pulado.
  const historico = useRef<Promise<TrainingSession[]>>(Promise.resolve([]));
  const [lendoSessao, setLendoSessao] = useState<string | null>(null);
  const [quem, setQuem] = useState('');
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const aparelho = step.instance_id;

  // As personas do aparelho se leem quando o formulário abre: com mais de uma o backend exige a escolha (409 `persona_ambigua`).
  useEffect(() => {
    let vivo = true;
    setPersonas(null);
    api.instancePersonas(aparelho)
      .then((lista) => { if (vivo) setPersonas(lista); })
      .catch(() => { if (vivo) setPersonas([]); });
    return () => { vivo = false; };
  }, [aparelho]);

  // 31.124: o histórico de treinos do aparelho diz se a etapa já foi ensinada. Só leitura; se falha, o formulário segue sem o aviso.
  useEffect(() => {
    let vivo = true;
    const leitura = api.listTraining(aparelho).then((lista) => (Array.isArray(lista) ? lista : [])).catch((): TrainingSession[] => []);
    historico.current = leitura;
    void leitura.then((lista) => { if (vivo) setTreinos(lista); });
    return () => { vivo = false; };
  }, [aparelho]);

  // A sugestão é só leitura e não toma controle: se não vem (404, 409, falha de rede), vale o texto padrão, que o backend também usaria.
  useEffect(() => {
    let vivo = true;
    editou.current = false;
    setSugestao(null);
    setTexto(padrao);
    setLendoSugestao(true);
    api.ensinoSugerido(detail.id, step.id)
      .then((s) => {
        if (!vivo || !s) return;
        setSugestao(s);
        if (!editou.current) setTexto(s.intent);
      })
      .catch(() => {})
      .finally(() => { if (vivo) setLendoSugestao(false); });
    return () => { vivo = false; };
  }, [detail.id, step.id, padrao]);

  const candidatas = personasDoEnsino(personas ?? [], step.app_id ?? '');
  const precisaEscolher = candidatas.length > 1;
  const escolhaValida = candidatas.some((p) => p.profile_id === quem);
  const lendo = personas === null;
  const base = sugestao?.intent ?? padrao;                       // o que o backend usaria sozinho: só o que difere disto vai como `intent`
  const { salvas, emAberto } = ensinoDaEtapa(treinos, detail.id, step.key);

  const abrir = async () => {
    if (enviando || lendo || (precisaEscolher && !escolhaValida)) return;
    setEnviando(true);
    setErro(null);
    try {
      // 31.124: etapa que já tem ensino só abre outro depois de a pessoa dizer que quer (com o histórico já lido).
      const jaEnsinado = ensinoDaEtapa(await historico.current, detail.id, step.key);
      if (jaEnsinado.salvas.length || jaEnsinado.emAberto.length) {
        const { confirmed } = await confirm({
          title: 'Ensinar esta etapa de novo?', confirmLabel: 'Ensinar de novo', cancelLabel: 'Cancelar',
          body: corpoDaConfirmacao(jaEnsinado.salvas, jaEnsinado.emAberto),
        });
        if (!confirmed) return;
      }
      let lease = useControlStore.getState().leases[aparelho];
      const instancia = useAppStore.getState().instances[aparelho];
      if (!lease || lease.status !== 'granted' || instancia?.control !== 'user') {
        await useControlStore.getState().take(aparelho);
        lease = useControlStore.getState().leases[aparelho];
      }
      if (!lease) { setErro(`Sem o controle de ${aparelho} o treino não abre. O aviso acima diz o que impediu.`); return; }
      if (lease.status !== 'granted') {
        setErro(`Pedi o controle de ${aparelho}: a IA termina a ação atual e ele vira seu. Quando estiver com você, clique de novo.`);
        return;
      }
      const intencao = texto.trim();
      await api.startTrainingFromRun({
        run_id: detail.id, step_id: step.id, lease_id: lease.leaseId,
        ...(intencao && intencao !== base ? { intent: intencao } : {}),
        ...(precisaEscolher && escolhaValida ? { profile_id: quem } : {}),
      });
      toast({ tone: 'success', title: 'Treino aberto a partir da falha', message: `Ensine a tarefa em ${aparelho}; a gravação segue a etapa «${step.title}».` });
      onClose('abriu');
      useUiStore.getState().openFocus(aparelho);
    } catch (e) {
      // A recusa não muda nada (etapa que já não falhou, controle perdido, persona de outro aparelho): a mensagem vem do backend.
      setErro(toApiError(e).message);
    } finally {
      setEnviando(false);
    }
  };

  const causa = sugestao ? linhaDaCausa(sugestao) : null;
  const dica = lendoSugestao ? 'Lendo a sugestão…' : sugestao && (causa || sugestao.pergunta) ? (
    <>
      {causa ? <>{causa} </> : null}
      O texto é uma sugestão da plataforma; o que você escrever vale no lugar.
    </>
  ) : null;
  const editado = sugestao !== null && texto !== base;           // a pessoa mexeu e há sugestão a que voltar

  const voltarASugestao = () => {
    editou.current = false;
    setTexto(base);
  };

  return (
    <>
    <form id={formId} className={styles.form} onSubmit={(e) => { e.preventDefault(); void abrir(); }}>
      <p className={styles.rotulo}>
        Isto assume o controle de <strong>{aparelho}</strong> (a IA fica em espera nele até você devolver) e abre o treino no Foco.
        Nada roda sozinho.
      </p>
      <AvisoDeEtapaEnsinada salvas={salvas} emAberto={emAberto} abrirSessao={setLendoSessao} />
      {sugestao?.pergunta ? <p className={styles.mostrar}><strong>O que mostrar:</strong> {sugestao.pergunta}</p> : null}
      <Field label="O que você vai ensinar?" hint={dica}>
        {({ id, describedBy }) => (
          <TextArea id={id} aria-describedby={describedBy} aria-busy={lendoSugestao || undefined} rows={2} value={texto} maxLength={400}
                    onChange={(e) => { editou.current = true; setTexto(e.target.value.replace(/\s*\n\s*/g, ' ')); }}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} />
        )}
      </Field>
      {editado ? <div className={styles.linha}><Button size="sm" variant="ghost" onClick={voltarASugestao}>Voltar à sugestão</Button></div> : null}
      {precisaEscolher ? (
        <Field label="De quem é o ensino?">
          {({ id }) => (
            <Select id={id} value={quem} onChange={(e) => setQuem(e.target.value)}>
              <option value="">Escolha a persona</option>
              {candidatas.map((p) => <option key={p.profile_id} value={p.profile_id}>{p.display_name || p.name}</option>)}
            </Select>
          )}
        </Field>
      ) : null}
      <div className={styles.linha}>
        <Button size="sm" variant="primary" type="submit" loading={enviando}
                disabledReason={lendo ? 'Lendo as personas deste aparelho.' : precisaEscolher && !escolhaValida ? 'Escolha de qual persona é o ensino.' : null}>
          Assumir o controle e abrir o treino
        </Button>
        <Button size="sm" variant="ghost" onClick={() => onClose('cancelou')}>Cancelar</Button>
      </div>
      {erro ? <p className={styles.erro} role="alert">{erro}</p> : null}
    </form>
    {/* Fora do <form>: o diálogo de leitura não pode submeter nem herdar o Enter do formulário. */}
    {lendoSessao ? <SessaoSalva sessionId={lendoSessao} onClose={() => setLendoSessao(null)} /> : null}
    </>
  );
}

export function EnsinarACorrigir({ detail, step }: { detail: Pick<RunDetail, 'id'>; step: Step }) {
  const [aberto, setAberto] = useState(false);
  const formId = useId();
  const botao = useRef<HTMLButtonElement>(null);
  const aparelho = step.instance_id;

  if (!ETAPA_CORRIGIVEL.has(step.status)) return null;

  return (
    <div className={styles.corrigir}>
      <div className={styles.linha}>
        <Button ref={botao} size="sm" variant="outline" icon={Wrench} onClick={() => setAberto(true)} aria-expanded={aberto}
                aria-controls={aberto ? formId : undefined}>
          Ensinar a corrigir
        </Button>
        <span className={styles.rotulo}>Você refaz a tarefa em {aparelho} e o treino fica ligado a esta etapa.</span>
      </div>
      {aberto ? (
        <FormularioDeEnsino id={formId} detail={detail} step={step}
                            onClose={(motivo) => { setAberto(false); if (motivo === 'cancelou') botao.current?.focus(); }} />
      ) : null}
    </div>
  );
}

/**
 * 31.125: o atalho para ensinar a partir do cartão de resultado da execução e do cabeçalho do aparelho: abre o MESMO
 * formulário num diálogo, para a etapa que falhou, sem descer até ela. `rotulo` é o nome acessível do botão.
 */
export function AtalhoParaEnsinar({ detail, step }: { detail: Pick<RunDetail, 'id'>; step: Step }) {
  const [aberto, setAberto] = useState(false);
  const botao = useRef<HTMLButtonElement>(null);
  const fechar = (motivo: 'abriu' | 'cancelou') => {
    setAberto(false);
    if (motivo === 'cancelou') botao.current?.focus();
  };
  return (
    <>
      <Button ref={botao} size="sm" variant="outline" icon={Wrench} onClick={() => setAberto(true)}
              label={`Ensinar a corrigir a etapa «${step.title}» em ${step.instance_id}`}>
        Ensinar a corrigir
      </Button>
      {aberto ? (
        <Dialog open onClose={() => fechar('cancelou')} title={`Ensinar a corrigir: ${step.title}`} icon={Wrench} size="md">
          <FormularioDeEnsino detail={detail} step={step} onClose={fechar} />
        </Dialog>
      ) : null}
    </>
  );
}
