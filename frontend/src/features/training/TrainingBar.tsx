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
import { Field, Select, TextInput } from '../../components/Field';
import { isRecord } from '../../lib/format';
import { useAppStore } from '../../store/app';
import { onLiveEvent } from '../../store/live';
import { toast, toastError } from '../../store/toasts';
import { TrainingReview } from './TrainingReview';
import styles from './Training.module.css';

const DESCRICAO: Record<string, string> = {
  tap: 'toque', long_press: 'toque longo', swipe: 'deslize', text: 'texto', key: 'tecla', open_app: 'abrir app',
};

export function TrainingBar({ instance, leaseId, mine }: { instance: Instance; leaseId: string | null; mine: boolean }) {
  const apps = useAppStore((s) => s.apps);
  const [sessoes, setSessoes] = useState<TrainingSession[]>([]);
  const [ativa, setAtiva] = useState<TrainingSession | null>(null);
  const [intencao, setIntencao] = useState('');
  const [appId, setAppId] = useState('');
  const [ocupado, setOcupado] = useState(false);
  const [revisando, setRevisando] = useState<string | null>(null);

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
    if (!leaseId || !intencao.trim()) return;
    setOcupado(true);
    try {
      setAtiva(await api.startTraining(instance.id, { intent: intencao.trim(), lease_id: leaseId, app_id: appId || null }));
      toast({ tone: 'info', title: 'Gravando o treinamento', message: 'Faça a tarefa na tela. Cada toque lê a tela antes, então fica um pouco mais lento.' });
    } catch (e) {
      toastError('Não foi possível iniciar o treinamento', e);
    } finally {
      setOcupado(false);
    }
  }

  async function concluir(descartar = false) {
    if (!ativa) return;
    setOcupado(true);
    try {
      const s = descartar ? await api.discardTraining(ativa.id) : await api.stopTraining(ativa.id);
      setAtiva(null);
      setIntencao('');
      await carregar();
      if (!descartar) setRevisando(s.id);
    } catch (e) {
      toastError('Não foi possível encerrar o treinamento', e);
    } finally {
      setOcupado(false);
    }
  }

  const pendentes = sessoes.filter((s) => s.status === 'recorded' || s.status === 'proposed');

  return (
    <section className={styles.bar} aria-label="Modo treinamento">
      <h3 className={styles.title}><GraduationCap size={15} aria-hidden /> Modo treinamento</h3>
      {ativa ? (
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
          <div className={styles.actions}>
            <Button size="sm" variant="primary" icon={Square} loading={ocupado} onClick={() => void concluir(false)}>Concluir e revisar</Button>
            <Button size="sm" variant="dangerGhost" icon={Trash2} disabled={ocupado} onClick={() => void concluir(true)}>Descartar</Button>
          </div>
        </div>
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
          <Button size="sm" variant="primary" icon={CircleDot} loading={ocupado}
                  disabledReason={intencao.trim() ? null : 'Diga o que vai ensinar.'} onClick={() => void iniciar()}>
            Iniciar treinamento
          </Button>
          <p className={styles.hint}>Senhas e códigos digitados não são gravados. Devolver o controle encerra a gravação.</p>
        </div>
      ) : (
        <p className={styles.hint}>Assuma o controle para ensinar uma tarefa: você faz, a IA mapeia o processo e ele vira habilidade para os perfis que você escolher.</p>
      )}
      {pendentes.length ? (
        <div className={styles.pending}>
          <span className={styles.muted}>Para revisar:</span>
          {pendentes.map((s) => (
            <Button key={s.id} size="sm" variant="ghost" onClick={() => setRevisando(s.id)}>
              {s.intent.slice(0, 40)}{s.status === 'proposed' ? ' · proposta pronta' : ''}
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
