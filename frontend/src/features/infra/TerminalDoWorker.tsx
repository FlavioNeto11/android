import { Ban, Play, Power, ShieldAlert, SquareTerminal } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, toApiError } from '../../api/client';
import type { ComandoRemoto, ComandoRemotoInterruptor, EstadoDoComando, Worker } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { Disclosure } from '../../components/Disclosure';
import { Field, TextInput } from '../../components/Field';
import { cx } from '../../lib/format';
import type { Tone } from '../../lib/status';
import { tempoRelativo, useNow } from '../../lib/time';
import { toast, toastError } from '../../store/toasts';
import styles from './Infra.module.css';

/** Consulta a cada 2 s até o fim (desenho do 29.154: sem rolagem ao vivo na primeira versão do painel). */
export const INTERVALO_DA_CONSULTA_MS = 2000;
const PRAZO_PADRAO_S = 60;
const PRAZO_MAXIMO_S = 600;

const FINAIS: ReadonlySet<EstadoDoComando> =
  new Set(['succeeded', 'failed', 'timed_out', 'cancelled', 'uncertain', 'rejected']);

const ESTADO: Record<EstadoDoComando, { label: string; tone: Tone }> = {
  created: { label: 'na fila', tone: 'info' },
  dispatched: { label: 'enviado', tone: 'info' },
  running: { label: 'rodando', tone: 'info' },
  succeeded: { label: 'concluído', tone: 'success' },
  failed: { label: 'falhou', tone: 'danger' },
  timed_out: { label: 'estourou o prazo', tone: 'warning' },
  cancelled: { label: 'cancelado', tone: 'warning' },
  uncertain: { label: 'incerto', tone: 'warning' },
  rejected: { label: 'recusado', tone: 'danger' },
};

/** Por que o botão Executar está parado: o primeiro dos três interruptores que está desligado. */
export function motivoDeNaoExecutar(i: ComandoRemotoInterruptor): string | null {
  if (i.negociado) return null;
  if (i.e_o_central) return 'O central não aceita comando remoto: é a máquina do dono.';
  if (!i.central_ativo) {
    return 'Desligado no config do central (`comando_remoto.ativo`); só muda com o reinício da tarefa farm-central.';
  }
  if (!i.worker_ligado) return 'O interruptor deste worker está desligado: ligue acima.';
  if (!i.agente_anuncia) {
    return 'O agente desta máquina não anuncia o comando remoto: `comando_remoto: true` no worker.yaml e reinício do agente.';
  }
  return 'O agente ainda não negociou o comando remoto nesta conexão; aguarde alguns segundos.';
}

/** A mensagem de cada recusa do central, no lugar do texto cru do backend. */
export function textoDoErro(e: unknown): string {
  const err = toApiError(e);
  switch (err.code) {
    case 'sem_operador':
      return 'Entre com o seu usuário nomeado: o comando remoto não aceita o token da API nem o acesso sem sessão.';
    case 'linha_com_credencial':
      return 'A linha parece conter uma credencial (senha, token ou chave) e foi recusada; o texto não foi guardado.';
    case 'comando_remoto_desligado':
      return 'O comando remoto está desligado (config do central ou interruptor deste worker).';
    case 'worker_sem_remote_exec':
      return 'O agente desta máquina não negociou o comando remoto nesta conexão.';
    case 'limite_por_minuto':
      return 'Muitos comandos neste minuto; espere um pouco.';
    case 'fila_cheia':
      return 'A fila deste worker está cheia; espere um comando terminar.';
    case 'central_fora':
      return 'O central não aceita comando remoto.';
    default:
      return err.status === 404 && err.code.startsWith('http_')
        ? 'O comando remoto não existe neste endereço.' : err.message;
  }
}

function chaveDoPedido(): string {
  return `painel-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function duracao(ms: number | null): string {
  if (ms == null) return '—';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function TerminalDoWorker({ worker }: { worker: Worker }) {
  return (
    <Disclosure
      bare
      summary={<span><SquareTerminal size={14} aria-hidden style={{ verticalAlign: 'text-bottom', marginRight: 4 }} />
        Comando remoto</span>}
      meta="desligado de fábrica">
      {() => <TerminalAberto workerId={worker.id} nome={worker.name} />}
    </Disclosure>
  );
}

function TerminalAberto({ workerId, nome }: { workerId: string; nome: string }) {
  const now = useNow();
  const [interruptor, setInterruptor] = useState<ComandoRemotoInterruptor | null>(null);
  const [historico, setHistorico] = useState<ComandoRemoto[]>([]);
  const [erro, setErro] = useState<string | null>(null);
  const [linha, setLinha] = useState('');
  const [pasta, setPasta] = useState('');
  const [prazo, setPrazo] = useState(String(PRAZO_PADRAO_S));
  const [atual, setAtual] = useState<ComandoRemoto | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [ligando, setLigando] = useState(false);
  const vivo = useRef(true);

  useEffect(() => {
    vivo.current = true;
    return () => { vivo.current = false; };
  }, []);

  const recarregarHistorico = useCallback(async () => {
    try {
      const r = await api.comandosRemotos(workerId, 50);
      if (vivo.current) setHistorico(r.items);
    } catch (e) {
      if (vivo.current) setErro(textoDoErro(e));
    }
  }, [workerId]);

  useEffect(() => {
    let cancelado = false;
    void (async () => {
      try {
        const [i, h] = await Promise.all([api.comandoRemoto(workerId), api.comandosRemotos(workerId, 50)]);
        if (cancelado) return;
        setInterruptor(i);
        setHistorico(h.items);
        setErro(null);
      } catch (e) {
        if (!cancelado) setErro(textoDoErro(e));
      }
    })();
    return () => { cancelado = true; };
  }, [workerId]);

  // Consulta o comando em curso a cada 2 s até ele chegar a um estado final.
  const idEmCurso = atual && !FINAIS.has(atual.state) ? atual.id : null;
  useEffect(() => {
    if (!idEmCurso) return undefined;
    const t = window.setTimeout(() => {
      void (async () => {
        try {
          const c = await api.comandoRemotoLer(workerId, idEmCurso);
          if (!vivo.current) return;
          setAtual(c);
          if (FINAIS.has(c.state)) void recarregarHistorico();
        } catch (e) {
          if (vivo.current) setErro(textoDoErro(e));
        }
      })();
    }, INTERVALO_DA_CONSULTA_MS);
    return () => window.clearTimeout(t);
  }, [idEmCurso, atual, workerId, recarregarHistorico]);

  async function alternar() {
    if (!interruptor) return;
    const ligar = !interruptor.worker_ligado;
    if (ligar) {
      const r = await confirm({
        title: `Ligar o comando remoto em "${nome}"?`,
        body: 'Quem tem sessão nomeada passa a executar comandos nesta máquina, com a conta do agente. Fica tudo '
          + 'registrado (quem, quando, a linha sem segredo). Não use para operar aparelho com conta real.',
        confirmLabel: 'Ligar', danger: true,
      });
      if (!r.confirmed) return;
    }
    setLigando(true);
    try {
      setInterruptor(await api.comandoRemotoLigar(workerId, ligar));
      setErro(null);
      toast({ tone: 'success', title: ligar ? 'Comando remoto ligado neste worker' : 'Comando remoto desligado neste worker' });
    } catch (e) {
      toastError('Não foi possível mudar o interruptor', e);
    } finally {
      setLigando(false);
    }
  }

  async function executar() {
    const texto = linha.trim();
    const segundos = Number(prazo);
    if (!texto) {
      setErro('Escreva a linha de comando.');
      return;
    }
    if (!Number.isInteger(segundos) || segundos < 1 || segundos > PRAZO_MAXIMO_S) {
      setErro(`O prazo vai de 1 a ${PRAZO_MAXIMO_S} segundos.`);
      return;
    }
    setEnviando(true);
    setErro(null);
    try {
      const aceito = await api.comandoRemotoPedir(workerId, {
        linha: texto, ...(pasta.trim() ? { pasta: pasta.trim() } : {}), timeout_s: segundos,
        idempotency_key: chaveDoPedido(),
      });
      setAtual({
        id: aceito.id, worker_id: workerId, requested_by: '', modo: 'linha', linha: texto, pasta: pasta.trim() || null,
        timeout_s: segundos, state: aceito.state as EstadoDoComando, exit_code: null, truncated: false,
        duration_ms: null, reason: null, created_at: aceito.created_at, dispatched_at: null, finished_at: null,
      });
      void recarregarHistorico();
    } catch (e) {
      setErro(textoDoErro(e));
    } finally {
      setEnviando(false);
    }
  }

  async function cancelar(id: string) {
    try {
      setAtual(await api.comandoRemotoCancelar(workerId, id));
      void recarregarHistorico();
    } catch (e) {
      setErro(textoDoErro(e));
    }
  }

  async function abrir(id: string) {
    try {
      setAtual(await api.comandoRemotoLer(workerId, id));
      setErro(null);
    } catch (e) {
      setErro(textoDoErro(e));
    }
  }

  const motivo = interruptor ? motivoDeNaoExecutar(interruptor) : 'Lendo o estado do comando remoto…';
  const emCurso = atual != null && !FINAIS.has(atual.state);

  return (
    <div className={styles.terminal}>
      {erro ? <Banner tone="danger" icon={ShieldAlert} role="alert" compact>{erro}</Banner> : null}
      {interruptor ? (
        <div className={styles.acoes}>
          <Badge tone={interruptor.central_ativo ? 'success' : 'muted'}>central {interruptor.central_ativo ? 'ligado' : 'desligado'}</Badge>
          <Badge tone={interruptor.worker_ligado ? 'success' : 'muted'}>worker {interruptor.worker_ligado ? 'ligado' : 'desligado'}</Badge>
          <Badge tone={interruptor.agente_anuncia ? 'success' : 'muted'}>agente {interruptor.agente_anuncia ? 'anuncia' : 'não anuncia'}</Badge>
          <Badge tone={interruptor.negociado ? 'success' : 'warning'}>{interruptor.negociado ? 'negociado' : 'não negociado'}</Badge>
          <Button size="sm" variant="outline" icon={Power} loading={ligando}
                  disabledReason={interruptor.e_o_central ? 'O central não aceita comando remoto.' : null}
                  onClick={() => void alternar()}>
            {interruptor.worker_ligado ? 'Desligar neste worker' : 'Ligar neste worker'}
          </Button>
        </div>
      ) : null}
      <div className={styles.terminalForm}>
        <Field label="Linha de comando" hint="Roda na conta do agente, um comando por vez. Linha com cara de credencial é recusada.">
          {(f) => (
            <TextInput id={f.id} aria-describedby={f.describedBy} invalid={f.invalid} mono value={linha} placeholder="Get-Date" onChange={(e) => setLinha(e.target.value)}
                       onKeyDown={(e) => { if (e.key === 'Enter' && !motivo && !enviando && !emCurso) void executar(); }} />
          )}
        </Field>
        <Field label="Pasta" unit="opcional">
          {(f) => <TextInput id={f.id} aria-describedby={f.describedBy} invalid={f.invalid} mono value={pasta} onChange={(e) => setPasta(e.target.value)} />}
        </Field>
        <Field label="Prazo" unit="segundos">
          {(f) => <TextInput id={f.id} aria-describedby={f.describedBy} invalid={f.invalid} type="number" min={1} max={PRAZO_MAXIMO_S} value={prazo}
                             onChange={(e) => setPrazo(e.target.value)} />}
        </Field>
        <div className={styles.acoes}>
          <Button size="sm" variant="primary" icon={Play} loading={enviando} disabledReason={motivo ?? (emCurso ? 'Há um comando em curso.' : null)}
                  onClick={() => void executar()}>
            Executar
          </Button>
          {emCurso && atual ? (
            <Button size="sm" variant="outline" icon={Ban} onClick={() => void cancelar(atual.id)}>Cancelar</Button>
          ) : null}
        </div>
        {motivo && interruptor ? <p className={cx(styles.dim, styles.alerta)}>{motivo}</p> : null}
      </div>
      {atual ? <SaidaDoComando comando={atual} /> : null}
      <div>
        <p className={styles.dim}>Últimos {historico.length ? `${historico.length} ` : ''}comandos</p>
        {historico.length === 0 ? <p className={styles.dim}>Nenhum comando remoto neste worker ainda.</p> : (
          <ul className={styles.historico}>
            {historico.map((c) => {
              const meta = ESTADO[c.state] ?? ESTADO.failed;
              return (
                <li key={c.id}>
                  <button type="button" className={styles.historicoLinha} onClick={() => void abrir(c.id)}
                          aria-label={`Abrir o comando ${c.linha}`}>
                    <Badge tone={meta.tone}>{meta.label}</Badge>
                    <code className={styles.historicoLinhaTexto}>{c.linha}</code>
                    <span className={styles.dim}>
                      {c.exit_code != null ? `código ${c.exit_code} · ` : ''}{duracao(c.duration_ms)} · {c.requested_by || '—'} · {tempoRelativo(c.created_at, now)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

function SaidaDoComando({ comando: c }: { comando: ComandoRemoto }) {
  const meta = ESTADO[c.state] ?? ESTADO.failed;
  return (
    <div className={styles.saidaDoComando} aria-live="polite">
      <div className={styles.acoes}>
        <Badge tone={meta.tone}>{meta.label}</Badge>
        <code>{c.linha}</code>
        {c.exit_code != null ? <span className={styles.dim}>código de saída {c.exit_code}</span> : null}
        {c.duration_ms != null ? <span className={styles.dim}>{duracao(c.duration_ms)}</span> : null}
      </div>
      {c.reason ? <p className={cx(styles.dim, styles.alerta)}>{c.reason}</p> : null}
      {c.state === 'uncertain' ? (
        <p className={cx(styles.dim, styles.alerta)}>
          Não se sabe se rodou: a conexão caiu no meio. O central não repete o comando; confira na máquina.
        </p>
      ) : null}
      {c.stdout ? <><p className={styles.dim}>Saída</p><pre className={styles.code}>{c.stdout}</pre></> : null}
      {c.stderr ? <><p className={styles.dim}>Erro</p><pre className={styles.code}>{c.stderr}</pre></> : null}
      {FINAIS.has(c.state) ? (
        <p className={styles.dim}>
          Segredos na saída aparecem mascarados (redigidos antes de guardar).
          {c.truncated ? ' Saída cortada: ficam o começo e o fim.' : ''}
        </p>
      ) : null}
    </div>
  );
}
