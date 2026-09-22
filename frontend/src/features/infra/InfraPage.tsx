import { Cpu, HardDrive, KeyRound, MemoryStick, Plus, Server, Smartphone, Trash2, TriangleAlert, Wrench } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { Health, Instance, Metrics, Worker, WorkerDevice } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Dialog } from '../../components/Dialog';
import { EmptyState } from '../../components/EmptyState';
import { ProgressBar } from '../../components/ProgressBar';
import { cx, formatGb, formatMb, formatPercent, plural } from '../../lib/format';
import type { Tone } from '../../lib/status';
import { ageMs, formatAgo, useNow } from '../../lib/time';
import { selectInstanceList, useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { groupByWorker, instanceStateMeta, isStale, orphanInstances } from './infraState';
import styles from './Infra.module.css';

/**
 * Onde cada coisa está rodando. Existe porque a distribuição já era real — seis aparelhos em outra máquina — e
 * nada no painel dizia isso: o cartão de um aparelho remoto tinha a mesma aparência de um emulador local.
 */

const ESTADO_WORKER: Record<Worker['state'], { label: string; tone: Tone }> = {
  online: { label: 'online', tone: 'success' },
  degraded: { label: 'degradado', tone: 'warning' },
  maintenance: { label: 'em manutenção', tone: 'info' },
  offline: { label: 'offline', tone: 'danger' },
};

export function InfraPage() {
  // Seletor que devolve estrutura NOVA a cada render faz o Zustand achar que o estado mudou sempre — e o React
  // entra em laço infinito (#185). Por isso se lê a fatia crua e se deriva com `useMemo`, como as outras telas.
  const workersMap = useAppStore((s) => s.workers);
  const instancesMap = useAppStore((s) => s.instances);
  const order = useAppStore((s) => s.instanceOrder);
  const health = useAppStore((s) => s.health);
  const metrics = useAppStore((s) => s.metrics);
  const [inscrevendo, setInscrevendo] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [rotatedCredential, setRotatedCredential] = useState<{ worker: string; token: string } | null>(null);
  const now = useNow();

  const todosOsWorkers = useMemo(() => Object.values(workersMap), [workersMap]);
  // O central agora TEM linha em `workers` (ele virou um worker como outro qualquer). Ele continua com cartão
  // próprio, então sai da lista: sem isto apareceria duas vezes, com o mesmo nome e os mesmos aparelhos.
  const central = useMemo(() => todosOsWorkers.find((w) => w.local) ?? null, [todosOsWorkers]);
  const workers = useMemo(() => todosOsWorkers.filter((w) => !w.local), [todosOsWorkers]);
  const instances = useMemo(() => selectInstanceList({ instances: instancesMap, instanceOrder: order }),
                            [instancesMap, order]);
  const locais = useMemo(() => instances.filter((i) => !i.worker_id || i.worker_id === central?.id),
                         [instances, central]);
  const remotasSemWorker = useMemo(
    () => orphanInstances(instances, todosOsWorkers.map((w) => w.id)), [instances, todosOsWorkers]);
  const porWorker = useMemo(() => groupByWorker(instances), [instances]);

  async function inscrever(): Promise<void> {
    setInscrevendo(true);
    try {
      const r = await api.enrollWorker();
      setToken(r.enrollment_token);
    } catch (e) {
      toastError('Não foi possível gerar o token de inscrição', e);
    } finally {
      setInscrevendo(false);
    }
  }

  return (
    <section className={styles.page} aria-label="Infraestrutura">
      <header className={styles.head}>
        <div>
          <h1 className={styles.title}>Infraestrutura</h1>
          <p className={styles.sub}>
            {plural(workers.length + 1, 'servidor', 'servidores')} — este e {plural(workers.length, 'worker', 'workers')}
          </p>
        </div>
        <Button icon={Plus} loading={inscrevendo} onClick={() => void inscrever()}>Inscrever servidor</Button>
      </header>

      {remotasSemWorker.length > 0 ? (
        <Banner
          tone="warning"
          icon={TriangleAlert}
          title="Aparelhos amarrados a um servidor que não está inscrito"
          // Sem este aviso, o aparelho ficaria sem ciclo de vida e ninguém saberia por quê.
        >
          {remotasSemWorker.map((i) => `${i.id} → ${i.worker_id}`).join(', ')}. Inscreva o servidor ou desamarre o
          aparelho em Configuração → Instâncias.
        </Banner>
      ) : null}

      <CartaoCentral instancias={locais} metrics={metrics} health={health} worker={central} />

      {workers.length === 0 ? (
        <EmptyState
          icon={Server}
          title="Nenhum servidor worker inscrito"
          hint="Um worker é uma máquina que hospeda aparelhos. Gere um token de inscrição e rode o agente nela; o passo a passo está em docs/worker.md."
        />
      ) : (
        workers
          .slice()
          .sort((a, b) => a.name.localeCompare(b.name))
          .map((w) => (
            <CartaoWorker key={w.id} worker={w} instancias={porWorker.get(w.id) ?? []} now={now}
                         onRotated={(tok) => setRotatedCredential({ worker: w.name, token: tok })} />
          ))
      )}

      <Dialog open={token !== null} onClose={() => setToken(null)} title="Token de inscrição">
        <p>
          Este token aparece <strong>uma única vez</strong>, vale 1 hora e serve para <strong>um</strong> servidor.
          Na máquina nova, rode:
        </p>
        <pre className={styles.code}>python -m app.worker --config worker.yaml --enroll {token}</pre>
        <p className={styles.dim}>
          O agente troca o token por uma credencial permanente e a grava lá. Depois disso, ele sobe sem o token.
        </p>
      </Dialog>

      <Dialog open={rotatedCredential !== null} onClose={() => setRotatedCredential(null)} title="Credencial rotacionada">
        <p>
          A credencial antiga de <strong>{rotatedCredential?.worker}</strong> parou de servir agora mesmo. Esta é a
          nova — aparece <strong>uma única vez</strong>. Grave-a em <code>worker-credential.json</code> na máquina
          e reinicie o agente:
        </p>
        <pre className={styles.code}>{rotatedCredential?.token}</pre>
      </Dialog>
    </section>
  );
}

/** O servidor central também é um servidor: esconder isso deixaria a pergunta "onde roda o quê" pela metade.
 *
 * `worker` é a linha dele na tabela `workers` — ele se registra como qualquer outra máquina desde o
 * `LocalWorker`. É de lá que saem vagas e manutenção, que valem para o central exatamente como valem para o
 * notebook; antes este cartão era desenhado só a partir de métricas e não tinha nem uma coisa nem outra. */
function CartaoCentral({ instancias, metrics, health, worker }: {
  instancias: readonly Instance[];
  metrics: Metrics | null;
  health: Health | null;
  worker: Worker | null;
}) {
  const online = instancias.filter((i) => i.state === 'online').length;
  const livreGb = metrics?.mem_available_gb ?? null;
  const usadoPct = metrics?.mem_used_percent ?? null;
  const estado = worker ? ESTADO_WORKER[worker.state] : { label: 'online', tone: 'success' as Tone };
  return (
    <Card>
      <CardHeader
        title="Este servidor (central)"
        subtitle={worker
          ? `Painel, banco, IA e catálogo de aplicativos — ${plural(worker.max_slots, 'vaga', 'vagas')}`
          : 'Painel, banco, IA e catálogo de aplicativos'}
        actions={<Badge tone={estado.tone} icon={Server}>{estado.label}</Badge>}
      />
      <CardBody>
        <div className={styles.recursos}>
          <Recurso icon={Cpu} rotulo="CPU" valor={formatPercent(metrics?.cpu_percent ?? null)}
                   fracao={(metrics?.cpu_percent ?? 0) / 100} />
          <Recurso icon={MemoryStick} rotulo="RAM livre" valor={formatGb(livreGb)}
                   fracao={(usadoPct ?? 0) / 100} />
          <Recurso icon={Smartphone} rotulo="Aparelhos online" valor={`${online} de ${instancias.length}`}
                   fracao={instancias.length ? online / instancias.length : 0} />
        </div>
        {health?.appium ? (
          <p className={styles.dim}>Appium: {health.appium.running ? 'no ar' : 'fora'} — {health.appium.detail ?? '—'}</p>
        ) : null}
        <ListaDeAparelhos instancias={instancias} />
      </CardBody>
    </Card>
  );
}

function CartaoWorker({ worker, instancias, now, onRotated }: {
  worker: Worker; instancias: readonly Instance[]; now: number; onRotated: (token: string) => void;
}) {
  const [ocupado, setOcupado] = useState(false);
  const meta = ESTADO_WORKER[worker.state] ?? ESTADO_WORKER.offline;
  const idade = ageMs(worker.last_seen_at, now);
  const velho = isStale(idade);
  const r = worker.resources;
  const ocupacao = worker.max_slots ? instancias.filter((i) => i.state === 'online').length / worker.max_slots : 0;

  async function manutencao(on: boolean): Promise<void> {
    setOcupado(true);
    try {
      await api.workerMaintenance(worker.id, on);
      toast({
        tone: 'info',
        title: on ? `${worker.name} entrou em manutenção` : `${worker.name} saiu da manutenção`,
        hint: on ? 'Novas atribuições estão suspensas. O que já está em voo continua.' : undefined,
      });
    } catch (e) {
      toastError(`Não foi possível alterar a manutenção de ${worker.name}`, e);
    } finally {
      setOcupado(false);
    }
  }

  async function rotacionar(): Promise<void> {
    const { confirmed } = await confirm({
      title: `Rotacionar credencial de "${worker.name}"?`,
      body: 'A credencial atual para de servir imediatamente e o agente precisa da nova credencial gravada nele '
        + 'para voltar a conectar. Use isto se suspeitar que a máquina foi comprometida.',
      confirmLabel: 'Rotacionar credencial',
      cancelLabel: 'Cancelar',
    });
    if (!confirmed) return;
    setOcupado(true);
    try {
      const r = await api.rotateWorkerCredential(worker.id);
      onRotated(r.credential);
    } catch (e) {
      toastError(`Não foi possível rotacionar a credencial de ${worker.name}`, e);
    } finally {
      setOcupado(false);
    }
  }

  async function remover(): Promise<void> {
    const conectado = worker.connected;
    const { confirmed } = await confirm({
      title: `Remover "${worker.name}"?`,
      danger: true,
      confirmLabel: 'Remover servidor',
      cancelLabel: 'Cancelar',
      body: (instancias.length > 0 ? `${instancias.length} aparelho(s) hospedado(s) ficarão sem ciclo de vida `
        + 'remoto até serem amarrados a outro worker. ' : '')
        + (conectado ? 'Este worker está conectado agora; removê-lo derruba a conexão.' : '')
        + ' Para reinscrever a mesma máquina depois, gere um novo token de inscrição.',
    });
    if (!confirmed) return;
    setOcupado(true);
    try {
      await api.removeWorker(worker.id, conectado);
      toast({ tone: 'success', title: `Worker "${worker.name}" removido` });
    } catch (e) {
      toastError(`Não foi possível remover ${worker.name}`, e);
    } finally {
      setOcupado(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={worker.name}
        subtitle={[worker.os, worker.agent_version && `agente ${worker.agent_version}`,
                   `Appium ${worker.appium_mode}`].filter(Boolean).join(' · ')}
        actions={(
          <div className={styles.acoes}>
            <Badge tone={meta.tone} icon={Server}>{meta.label}</Badge>
            {!worker.connected ? <Badge tone="danger">sem canal</Badge> : null}
            <Button size="sm" variant={worker.maintenance ? 'primary' : 'outline'} icon={Wrench}
                    loading={ocupado} onClick={() => void manutencao(!worker.maintenance)}>
              {worker.maintenance ? 'Retomar atribuições' : 'Manutenção'}
            </Button>
            <Button size="sm" variant="outline" icon={KeyRound} loading={ocupado} onClick={() => void rotacionar()}>
              Rotacionar credencial
            </Button>
            <Button size="sm" variant="outline" icon={Trash2} loading={ocupado} onClick={() => void remover()}>
              Remover
            </Button>
          </div>
        )}
      />
      <CardBody>
        {/* Idade do dado é informação de primeira classe: sem ela, uma tela velha parece atual. */}
        <p className={cx(styles.dim, velho && styles.alerta)}>
          Último contato: {formatAgo(worker.last_seen_at, now)}
          {velho ? ' — os dados abaixo podem estar desatualizados' : ''}
          {worker.state_detail ? ` · ${worker.state_detail}` : ''}
        </p>
        <div className={styles.recursos}>
          <Recurso icon={Cpu} rotulo={`CPU (${r.cpu_count ?? '?'} núcleos)`} valor={formatPercent(r.cpu_percent ?? null)}
                   fracao={(r.cpu_percent ?? 0) / 100} />
          <Recurso icon={MemoryStick} rotulo="RAM livre" valor={formatMb(r.ram_free_mb ?? null)}
                   fracao={r.ram_total_mb && r.ram_free_mb ? 1 - r.ram_free_mb / r.ram_total_mb : 0} />
          <Recurso icon={HardDrive} rotulo="Disco livre" valor={formatGb(r.disk_free_gb ?? null)} fracao={0} />
          <Recurso icon={Smartphone} rotulo="Vagas ocupadas"
                   valor={`${instancias.filter((i) => i.state === 'online').length} de ${worker.max_slots}`}
                   fracao={ocupacao} />
        </div>
        <ListaDeAparelhos instancias={instancias} doWorker={worker.devices} />
      </CardBody>
    </Card>
  );
}

function Recurso({ icon: Icon, rotulo, valor, fracao }: {
  icon: typeof Cpu; rotulo: string; valor: string; fracao: number;
}) {
  const tom: Tone = fracao > 0.9 ? 'danger' : fracao > 0.75 ? 'warning' : 'accent';
  return (
    <div className={styles.recurso}>
      <span className={styles.recursoTopo}><Icon size={13} aria-hidden /> {rotulo}</span>
      <strong className={styles.recursoValor}>{valor}</strong>
      <ProgressBar value={fracao} label={rotulo} tone={tom} />
    </div>
  );
}

/** Servidor → dispositivo → tarefa: cada linha leva ao aparelho, e mostra o que ele está fazendo agora. */
function ListaDeAparelhos({ instancias, doWorker }: {
  instancias: readonly Instance[];
  doWorker?: readonly WorkerDevice[];
}) {
  const openFocus = useUiStore((s) => s.openFocus);
  const selectRun = useUiStore((s) => s.selectRun);
  const setView = useUiStore((s) => s.setView);
  if (instancias.length === 0) {
    return <p className={styles.dim}>Nenhum aparelho amarrado a este servidor.</p>;
  }
  const processo = new Map((doWorker ?? []).map((d) => [d.instance_id ?? '', d]));
  return (
    <ul className={styles.aparelhos}>
      {instancias.map((i) => {
        const meta = instanceStateMeta(i.state);
        const proc = processo.get(i.id);
        return (
          <li key={i.id} className={styles.aparelho}>
            <button type="button" className={styles.aparelhoBtn} onClick={() => openFocus(i.id)}
                    aria-label={`Abrir ${i.id} na visão de foco`}>
              <span className={styles.aparelhoId}>{i.id}</span>
              <Badge tone={meta.tone} size="sm" plain>{meta.label}</Badge>
              {i.kind !== 'emulator' ? <Badge tone="muted" size="sm" plain>{i.kind}</Badge> : null}
            </button>
            <span className={styles.aparelhoMeio}>
              {/* O que o worker vê do PROCESSO, que é diferente do que o central vê do Android. */}
              {proc ? <span className={styles.dim}>processo: {proc.state}</span> : null}
              {proc?.detail ? <span className={styles.dim} title={proc.detail}> · {proc.detail}</span> : null}
              <Capacidades instancia={i} />
            </span>
            {i.current?.run_id ? (
              <button type="button" className={styles.tarefa}
                      onClick={() => { selectRun(i.current!.run_id!); setView('execucoes'); }}>
                {i.current.step_title ?? 'em execução'}
                {i.current.steps_total ? ` (${i.current.steps_done}/${i.current.steps_total})` : ''}
              </button>
            ) : <span className={styles.dim}>sem tarefa</span>}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * O que o aparelho É: nível de API, ABI e se a imagem tem Google Play Services.
 *
 * Existe porque a única pergunta que a tela respondia sobre um aparelho era "que botão ele aceita". Sem isto,
 * quem escolhe onde rodar não tinha como saber que aquele emulador é AOSP, ou que só executa x86 — e a
 * incompatibilidade aparecia no meio, como `INSTALL_FAILED_NO_MATCHING_ABIS`. O que não se sabe não é mostrado:
 * campo vazio aqui quer dizer "ainda não foi observado nem declarado", nunca "não tem".
 */
function Capacidades({ instancia }: { instancia: Instance }) {
  const partes: string[] = [];
  if (instancia.api_level) partes.push(`API ${instancia.api_level}`);
  const abi = instancia.abis?.[0];
  if (abi) partes.push(abi);
  if (instancia.play_store === true) partes.push('Play Services');
  if (instancia.play_store === false) partes.push('AOSP');
  if (partes.length === 0) return null;
  return (
    <span className={styles.dim} title={instancia.system_image ?? undefined}> · {partes.join(' · ')}</span>
  );
}
