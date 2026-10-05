/**
 * Modo treinamento no Foco (item 13.3). Com o controle na mão, a pessoa diz o que vai ensinar e faz a tarefa; cada
 * toque, texto, deslize, tecla e "abrir app" é gravado com o elemento tocado. Ao concluir, a revisão (IA propõe,
 * pessoa ajusta) transforma a gravação em habilidade para os perfis escolhidos.
 */
import { CircleDot, GraduationCap, Square, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { Instance, TrainingSession } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { Field, Select, TextInput } from '../../components/Field';
import { isRecord, plural } from '../../lib/format';
import { useAppStore } from '../../store/app';
import { onLiveEvent } from '../../store/live';
import { toast, toastError } from '../../store/toasts';
import { TrainingReview } from './TrainingReview';
import { useTrainingStore } from './trainingStore';
import styles from './Training.module.css';

/** Quantos caracteres do nome da gravação cabem no botão de "Para revisar"; o nome inteiro vai no rótulo. */
const ROTULO_DA_GRAVACAO = 40;

/** Corta o nome na última palavra inteira que cabe, com reticências (29.142: antes o corte caía no meio da palavra). */
function encurtar(nome: string): string {
  if (nome.length <= ROTULO_DA_GRAVACAO) return nome;
  const corte = nome.slice(0, ROTULO_DA_GRAVACAO - 1);
  const espaco = corte.lastIndexOf(' ');
  return `${(espaco > ROTULO_DA_GRAVACAO / 2 ? corte.slice(0, espaco) : corte).trimEnd()}…`;
}

const DESCRICAO: Record<string, string> = {
  tap: 'toque', long_press: 'toque longo', swipe: 'deslize', text: 'texto', key: 'tecla', open_app: 'abrir app',
};

export function TrainingBar({ instance, leaseId, mine, somenteRevisao = false }: {
  instance: Instance; leaseId: string | null; mine: boolean;
  /** Aparelho fora do ar (31.86): sem formulário de iniciar; a lista "Para revisar" e a revisão seguem de pé. */
  somenteRevisao?: boolean;
}) {
  const apps = useAppStore((s) => s.apps);
  const [sessoes, setSessoes] = useState<TrainingSession[]>([]);
  const [ativa, setAtiva] = useState<TrainingSession | null>(null);
  const [intencao, setIntencao] = useState('');
  const [appId, setAppId] = useState('');
  // Uma ação em voo por vez; cada botão gira só pela sua e o outro explica por que espera.
  const [ocupado, setOcupado] = useState<'iniciar' | 'concluir' | 'descartar' | null>(null);
  const [revisando, setRevisando] = useState<string | null>(null);
  const recusadas = useTrainingStore((s) => s.recusadas[instance.id] ?? 0);
  const definirGravando = useTrainingStore((s) => s.definirGravando);
  // Só é "gravando" para o Foco (padrão de "Limpar o campo antes", contador de recusas) quando a gravação é desta pessoa.
  const gravandoMesmo = !!ativa && mine;
  useEffect(() => {
    definirGravando(instance.id, gravandoMesmo);
  }, [instance.id, gravandoMesmo, definirGravando]);
  useEffect(() => () => definirGravando(instance.id, false), [instance.id, definirGravando]);

  const carregar = useCallback(async () => {
    try {
      const lista = await api.listTraining(instance.id);
      setSessoes(lista);
      const gravando = lista.find((s) => s.status === 'recording');
      setAtiva(gravando ? await api.getTraining(gravando.id) : null);
    } catch {
      /* sem gravações: a barra fica no estado inicial */
    }
  }, [instance.id]);

  useEffect(() => {
    void carregar();
  }, [carregar, mine]);

  // Cada entrada gravada chega como evento: a lista ao vivo acompanha sem consultar a cada segundo.
  useEffect(() => onLiveEvent((ev) => {
    if (ev.instance_id !== instance.id) return;
    if (ev.kind === 'training.input' || (isRecord(ev.data) && 'training_session_id' in ev.data)) void carregar();
  }), [instance.id, carregar]);

  async function iniciar() {
    if (!leaseId || !intencao.trim() || ocupado) return;
    setOcupado('iniciar');
    try {
      setAtiva(await api.startTraining(instance.id, { intent: intencao.trim(), lease_id: leaseId, app_id: appId || null }));
      toast({ tone: 'info', title: 'Gravando o treinamento', message: 'Faça a tarefa na tela. Cada toque lê a tela antes, então fica um pouco mais lento.' });
    } catch (e) {
      toastError('Não foi possível iniciar o treinamento', e);
    } finally {
      setOcupado(null);
    }
  }

  async function concluir(descartar = false) {
    if (!ativa || ocupado) return;
    if (descartar) {
      const entradas = (ativa.inputs ?? []).length;
      const { confirmed } = await confirm({
        title: 'Descartar a gravação?',
        danger: true,
        confirmLabel: 'Descartar gravação',
        cancelLabel: 'Cancelar',
        body: `“${ativa.intent}” e ${plural(entradas, 'entrada gravada', 'entradas gravadas')} se perdem; nada vira fluxo nem habilidade. O controle continua com você.`,
      });
      if (!confirmed) return;
    }
    setOcupado(descartar ? 'descartar' : 'concluir');
    try {
      const s = descartar ? await api.discardTraining(ativa.id, leaseId) : await api.stopTraining(ativa.id, leaseId);
      setAtiva(null);
      setIntencao('');
      await carregar();
      if (!descartar) setRevisando(s.id);
    } catch (e) {
      toastError('Não foi possível encerrar o treinamento', e);
    } finally {
      setOcupado(null);
    }
  }

  const botoesDaGravacao = () => (
    <div className={styles.actions}>
      <Button size="sm" variant="primary" icon={Square} loading={ocupado === 'concluir'}
              disabledReason={ocupado === 'descartar' ? 'Descartando a gravação…' : null} onClick={() => void concluir(false)}>Concluir e revisar</Button>
      <Button size="sm" variant="dangerGhost" icon={Trash2} loading={ocupado === 'descartar'}
              disabledReason={ocupado === 'concluir' ? 'Concluindo a gravação…' : null} onClick={() => void concluir(true)}>Descartar</Button>
    </div>
  );

  const pendentes = sessoes.filter((s) => s.status === 'recorded' || s.status === 'proposed');

  return (
    <section className={styles.bar} aria-label="Modo treinamento">
      <h3 className={styles.title}><GraduationCap size={15} aria-hidden /> Modo treinamento</h3>
      {ativa && !mine && instance.control === 'user' ? (
        // Gravação viva de quem tem o controle em outra aba (ou depois de um F5: o lease vive só em memória) ou de
        // outra pessoa: quem só olha não encerra nem descarta a gravação alheia.
        <p className={styles.hint} role="status">Há uma gravação em andamento neste aparelho por quem está com o controle.</p>
      ) : ativa && !mine ? (
        // 31.80: sessão `recording` sem NINGUÉM com o controle é gravação órfã (ex.: o servidor reiniciou); nada mais
        // é gravado, então a barra não diz "Gravando".
        <div className={styles.recording}>
          <p className={styles.hint} role="status">Há uma gravação aberta neste aparelho que não está mais gravando: <strong>{ativa.intent}</strong>.</p>
          {botoesDaGravacao()}
        </div>
      ) : ativa ? (
        <div className={styles.recording}>
          <p className={styles.recLine}>
            <CircleDot size={14} className={styles.recDot} aria-hidden /> Gravando: <strong>{ativa.intent}</strong>
            <Badge size="sm">{(ativa.inputs ?? []).length} entrada(s)</Badge>
          </p>
          <ol className={styles.liveList}>
            {(ativa.inputs ?? []).slice(-6).map((e) => (
              <li key={e.seq}>
                <span className={styles.muted}>#{e.seq}</span> {DESCRICAO[e.type] ?? e.type}
                {e.target?.text || e.target?.desc ? <> em <strong>{e.target.text || e.target.desc}</strong></> : null}
                {e.type === 'text' ? (e.text !== null ? <> “{e.text}”</> : <> ({e.text_len} caractere(s) sigilosos — não gravados)</>) : null}
                {e.type === 'open_app' ? <> {e.app_id}</> : null}
                {e.type === 'key' ? <> {e.key_name}</> : null}
              </li>
            ))}
          </ol>
          {/* A região viva nasce vazia com a gravação: o texto que entra depois é anunciado, o que já nasce com ele não. */}
          <p className={styles.hint} role="status" aria-live="polite">{recusadas ? `${recusadas} entrada(s) recusada(s): refaça` : ''}</p>
          <p className={styles.hint}>Para trocar um texto, marque Limpar o campo antes em vez de apertar Apagar.</p>
          {botoesDaGravacao()}
        </div>
      ) : somenteRevisao ? (
        <p className={styles.hint}>Aparelho fora do ar: dá para revisar e salvar o fluxo; as receitas das etapas só são gravadas com o aparelho online.</p>
      ) : mine ? (
        <div className={styles.start}>
          <Field label="O que você vai ensinar?">
            {({ id }) => (
              <TextInput id={id} value={intencao} maxLength={400} placeholder="Ex.: responder a DM de um cliente com uma saudação"
                         onChange={(e) => setIntencao(e.target.value)} />
            )}
          </Field>
          <Field label="App" unit="opcional">
            {({ id }) => (
              <Select id={id} value={appId} onChange={(e) => setAppId(e.target.value)}>
                <option value="">Descobrir pela tela</option>
                {apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
            )}
          </Field>
          <Button size="sm" variant="primary" icon={CircleDot} loading={ocupado === 'iniciar'}
                  disabledReason={intencao.trim() ? null : 'Diga o que vai ensinar.'} onClick={() => void iniciar()}>
            Iniciar treinamento
          </Button>
          <p className={styles.hint}>Senhas e códigos digitados não são gravados. Devolver o controle encerra a gravação.</p>
          <p className={styles.hint}>Para trocar um texto, marque Limpar o campo antes em vez de apertar Apagar.</p>
        </div>
      ) : (
        <p className={styles.hint}>Assuma o controle para ensinar uma tarefa: você faz, a IA mapeia o processo e ele vira habilidade para os perfis que você escolher.</p>
      )}
      {pendentes.length ? (
        <div className={styles.pending}>
          <span className={styles.muted}>Para revisar:</span>
          {pendentes.map((s) => (
            <Button key={s.id} size="sm" variant="ghost" onClick={() => setRevisando(s.id)}
                    label={`Revisar “${s.intent}”${s.status === 'proposed' ? ', proposta pronta' : ', só gravada'}`}>
              {/* 29.142: o rótulo leva o nome inteiro; o estado aparece nas duas situações, para a só gravada não
                  parecer igual à de proposta pronta. */}
              {encurtar(s.intent)}
              {s.status === 'proposed' ? ' · proposta pronta' : ' · só gravada'}
            </Button>
          ))}
        </div>
      ) : null}
      {revisando ? (
        <TrainingReview sessionId={revisando} onClose={() => { setRevisando(null); void carregar(); }} />
      ) : null}
    </section>
  );
}
