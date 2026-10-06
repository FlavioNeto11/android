import {
  Cable, Camera, Cpu, HardDrive, KeyRound, ListOrdered, MemoryStick, Plus, ScrollText, Server, Smartphone, Star,
  Trash2, TriangleAlert, User, Wrench,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api, profileAvatarUrl, toApiError } from '../../api/client';
import type {
  AppConfig, DeviceAppState, EventRecord, Health, Instance, Metrics, PersonaOnDevice, RunSummary, Worker,
  WorkerDevice,
} from '../../api/types';
import { Avatar } from '../../components/Avatar';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Dialog } from '../../components/Dialog';
import { EmptyState } from '../../components/EmptyState';
import { ProgressBar } from '../../components/ProgressBar';
import { Tabs } from '../../components/Tabs';
import { cx, formatGb, formatMb, formatPercent, plural } from '../../lib/format';
import { SESSION_STATUS, metaOf, type Tone } from '../../lib/status';
import { ageMs, tempoRelativo, formatClock, useNow } from '../../lib/time';
import { selectInstanceList, useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { personasPorAparelho } from '../profiles/pessoa';
import { usePersonas } from '../profiles/usePersonas';
import { TerminalDoWorker } from './TerminalDoWorker';
import {
  centralMeta, eventosDoServidor, filaDoServidor, fracaoDeDisco, groupByWorker, instanceStateMeta, isStale,
  ocupacaoDoServidor, orphanInstances, pausaDoReparoMeta, renderizadorMeta, tipoDoAparelho,
} from './infraState';
import { estadoContado, type Ocupacao } from '../../store/metricas';
import { CriarAparelhoDialog } from './CriarAparelho';
import { recusaDaAposentadoria, type RecusaNaTela } from './provisionamento';
import appStyles from '../../App.module.css';
import styles from './Infra.module.css';

/**
 * Onde cada coisa está rodando. Existe porque a distribuição já era real — seis aparelhos em outra máquina — e
 * nada no painel dizia isso: o cartão de um aparelho remoto tinha a mesma aparência de um emulador local.
 */

const CRIAR_NO_WORKER_INDISPONIVEL = 'Criar aparelho num servidor remoto ainda não é possível: o agente só conhece o '
  + 'inventário do worker.yaml dele';

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
  const conectado = useAppStore((s) => s.conn.status === 'connected');
  const events = useAppStore((s) => s.recentEvents);
  const runs = useAppStore((s) => s.runs);
  const apps = useAppStore((s) => s.apps);
  const hydrateCount = useAppStore((s) => s.hydrateCount);
  const [inscrevendo, setInscrevendo] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [rotatedCredential, setRotatedCredential] = useState<{ worker: string; token: string } | null>(null);
  const now = useNow();

  // Personas e estado de app por aparelho não vêm no snapshot: são poucos, mudam devagar, e recarregar a cada
  // hidratação basta. Sem eles a aba "Personas e apps" seria um título vazio — e o pedido (E5) pede o conteúdo. As
  // personas de cada aparelho saem dos `devices[]` de cada persona (N:N, v0.29): Servidor → Aparelho → Persona(s).
  const pessoas = usePersonas();
  const personas = useMemo(() => personasPorAparelho(pessoas ?? []), [pessoas]);
  const [appState, setAppState] = useState<DeviceAppState[]>([]);
  useEffect(() => {
    let vivo = true;
    void api.listAppState().then((a) => vivo && setAppState(a)).catch(() => undefined);
    return () => {
      vivo = false;
    };
  }, [hydrateCount]);

  const todosOsWorkers = useMemo(() => Object.values(workersMap), [workersMap]);
  // O central agora TEM linha em `workers` (ele virou um worker como outro qualquer). Ele continua com cartão
  // próprio, então sai da lista: sem isto apareceria duas vezes, com o mesmo nome e os mesmos aparelhos.
  const central = useMemo(() => todosOsWorkers.find((w) => w.local) ?? null, [todosOsWorkers]);
  const workers = useMemo(() => todosOsWorkers.filter((w) => !w.local), [todosOsWorkers]);
  // Aposentados nesta visita: o store ainda não trata o evento `instance.retired`, e sem isto o aparelho continuaria
  // na lista até o próximo snapshot — parecendo que a aposentadoria não pegou.
  const [aposentados, setAposentados] = useState<ReadonlySet<string>>(() => new Set());
  const instances = useMemo(
    () => selectInstanceList({ instances: instancesMap, instanceOrder: order }).filter((i) => !aposentados.has(i.id)),
    [instancesMap, order, aposentados]);
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
    <section className={cx(appStyles.page, styles.page)} aria-label="Infraestrutura">
      <header className={appStyles.pageHeader}>
        <div>
          <h1 className={appStyles.pageTitle}>Infraestrutura</h1>
          <p className={appStyles.pageLead}>
            {plural(workers.length + 1, 'servidor', 'servidores')} — este e {plural(workers.length, 'remoto', 'remotos')}
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
          aparelho em Configuração → Aparelhos e contas.
        </Banner>
      ) : null}

      <CartaoCentral instancias={locais} metrics={metrics} health={health} worker={central} now={now}
                     conectado={conectado} dados={{ events, runs, apps, personas, appState }}
                     onAposentado={(id) => setAposentados((s) => new Set(s).add(id))} />

      {workers.length === 0 ? (
        <EmptyState
          icon={Server}
          title="Nenhum servidor remoto inscrito"
          hint="Um servidor remoto é uma máquina que hospeda aparelhos. Gere um token de inscrição e rode o agente nela; o passo a passo está em docs/worker.md."
        />
      ) : (
        workers
          .slice()
          .sort((a, b) => a.name.localeCompare(b.name))
          .map((w) => (
            <CartaoWorker key={w.id} worker={w} instancias={porWorker.get(w.id) ?? []} now={now}
                         dados={{ events, runs, apps, personas, appState }}
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
function CartaoCentral({ instancias, metrics, health, worker, now, conectado, dados, onAposentado }: {
  instancias: readonly Instance[];
  metrics: Metrics | null;
  health: Health | null;
  worker: Worker | null;
  now: number;
  conectado: boolean;
  dados: DadosDoServidor;
  onAposentado: (id: string) => void;
}) {
  const [criando, setCriando] = useState(false);
  // Ocupação conta o que ocupa RAM, não o que já respondeu ao ADB: `booting` come a vaga desde o primeiro
  // segundo, e contá-lo só depois fazia o painel prometer vaga que não existia.
  const vagas = ocupacaoDoServidor(instancias, worker);
  const livreGb = metrics?.mem_available_gb ?? null;
  const usadoPct = metrics?.mem_used_percent ?? null;
  const r = worker?.resources;
  // O selo era `online` fixo: dizia "online" com a saúde degradada e com o painel desconectado (#63).
  const estado = centralMeta(health, conectado, worker);
  const idadeDoDado = ageMs(metrics?.ts ?? null, now);
  return (
    <Card>
      <CardHeader
        title="Este servidor (central)"
        subtitle={worker
          ? `Painel, banco, IA e catálogo de aplicativos — ${plural(vagas.vagas, 'vaga', 'vagas')}`
          : 'Painel, banco, IA e catálogo de aplicativos'}
        actions={(
          <div className={styles.acoes}>
            <Badge tone={estado.tone} icon={Server}>{estado.label}</Badge>
            {/* Só aqui: o aparelho novo nasce no hospedeiro (ADR-045). O worker remoto ainda não sabe criar. */}
            <Button size="sm" variant="outline" icon={Smartphone} onClick={() => setCriando(true)}>Criar aparelho</Button>
          </div>
        )}
      />
      <CardBody>
        {/* Idade do dado também aqui: uma tela parada parecia atual porque o central nunca se declarava velho. */}
        <p className={cx(styles.dim, idadeDoDado !== null && idadeDoDado > 30_000 && styles.alerta)}>
          Últimas métricas: {metrics ? tempoRelativo(metrics.ts, now) : 'ainda não chegaram'}
          {health?.problems?.length ? ` · ${plural(health.problems.length, 'problema', 'problemas')} em Diagnóstico` : ''}
        </p>
        <div className={styles.recursos}>
          <Recurso icon={Cpu} rotulo="CPU" valor={formatPercent(metrics?.cpu_percent ?? null)}
                   fracao={(metrics?.cpu_percent ?? 0) / 100} />
          <Recurso icon={MemoryStick} rotulo="RAM livre" valor={formatGb(livreGb)}
                   fracao={(usadoPct ?? 0) / 100} />
          <Recurso icon={HardDrive} rotulo="Disco livre" valor={formatGb(r?.disk_free_gb ?? null)}
                   fracao={fracaoDeDisco(r?.disk_free_gb, r?.disk_total_gb)} />
          <VagasOcupadas ocupacao={vagas} />
        </div>
        <AvisoDeVagas ocupacao={vagas} />
        <CapacidadesDoServidor worker={worker} health={health} />
        <ListaDeAparelhos instancias={instancias} personas={dados.personas} onAposentado={onAposentado} />
        <AbasDoServidor id="central" instancias={instancias} dados={dados} now={now} />
      </CardBody>
      {criando ? <CriarAparelhoDialog onClose={() => setCriando(false)} /> : null}
    </Card>
  );
}

/**
 * O que este servidor SABE FAZER. A tela dizia o estado da máquina e nada sobre a capacidade dela — e era a
 * capacidade que decidia se um aparelho dali ganhava ciclo de vida ou só teclas (#63).
 */
function CapacidadesDoServidor({ worker, health }: { worker: Worker | null; health?: Health | null }) {
  const verbos = worker?.verbs ?? [];
  const appium = worker
    ? (worker.appium_mode === 'local' ? `Appium local${worker.appium_url ? ` (${worker.appium_url})` : ''}`
                                      : 'Appium do central')
    : health?.appium ? `Appium ${health.appium.running ? 'no ar' : 'fora'}${health.appium.detail ? ` — ${health.appium.detail}` : ''}`
    : null;
  if (verbos.length === 0 && !appium) return null;
  return (
    <p className={styles.dim}>
      Capacidades: {appium ?? '—'}
      {verbos.length > 0 ? ` · verbos: ${verbos.join(', ')}` : ' · nenhum verbo declarado (só teclas)'}
    </p>
  );
}

function CartaoWorker({ worker, instancias, now, dados, onRotated }: {
  worker: Worker; instancias: readonly Instance[]; now: number; dados: DadosDoServidor;
  onRotated: (token: string) => void;
}) {
  const [ocupado, setOcupado] = useState(false);
  const meta = ESTADO_WORKER[worker.state] ?? ESTADO_WORKER.offline;
  const idade = ageMs(worker.last_seen_at, now);
  const velho = isStale(idade);
  const r = worker.resources;
  const vagas = ocupacaoDoServidor(instancias, worker);

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
            {/* Achado #137: a cópia do agente em `C:\farm\agent` não é checkout, e as duas pontas diziam
                `0.1.0` — não havia como saber pelo painel que aquela máquina roda código velho. Agora a versão
                traz o commit, e a etiqueta diz EM RELAÇÃO A QUÊ: sem o número do central ao lado, "defasado"
                manda o operador procurar a resposta em outro lugar. */}
            {worker.agent_outdated ? (
              <Badge tone="warning" title={`o central roda ${worker.expected_agent_version ?? '?'}`}>
                agente defasado
              </Badge>
            ) : null}
            {/* Sem KVM o emulador não sobe, ou sobe em emulação de software: um boot de 2 min vira dezenas.
                Antes isso só aparecia como comando estourando prazo, sem causa visível. */}
            {worker.accel && worker.accel !== 'kvm' ? (
              <Badge tone="danger" title="sem KVM utilizável: o emulador não sobe em tempo útil nesta máquina">
                {worker.accel === 'kvm-inacessivel' ? 'KVM sem permissão' : 'sem KVM'}
              </Badge>
            ) : null}
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
            {/* O botão existe desabilitado, com o motivo, para a pessoa não procurar a ação que falta (ADR-045). */}
            <Button size="sm" variant="outline" icon={Smartphone} disabledReason={CRIAR_NO_WORKER_INDISPONIVEL}>
              Criar aparelho
            </Button>
          </div>
        )}
      />
      <CardBody>
        {/* Idade do dado é informação de primeira classe: sem ela, uma tela velha parece atual. */}
        <p className={cx(styles.dim, velho && styles.alerta)}>
          Último contato: {tempoRelativo(worker.last_seen_at, now)}
          {velho ? ' — os dados abaixo podem estar desatualizados' : ''}
          {worker.state_detail ? ` · ${worker.state_detail}` : ''}
        </p>
        <TunelDoWorker worker={worker} now={now} />
        <div className={styles.recursos}>
          <Recurso icon={Cpu} rotulo={`CPU (${r.cpu_count ?? '?'} núcleos)`} valor={formatPercent(r.cpu_percent ?? null)}
                   fracao={(r.cpu_percent ?? 0) / 100} />
          <Recurso icon={MemoryStick} rotulo="RAM livre" valor={formatMb(r.ram_free_mb ?? null)}
                   fracao={r.ram_total_mb && r.ram_free_mb ? 1 - r.ram_free_mb / r.ram_total_mb : 0} />
          <Recurso icon={HardDrive} rotulo="Disco livre" valor={formatGb(r.disk_free_gb ?? null)}
                   fracao={fracaoDeDisco(r.disk_free_gb, r.disk_total_gb)} />
          <VagasOcupadas ocupacao={vagas} />
        </div>
        <AvisoDeVagas ocupacao={vagas} />
        <CapacidadesDoServidor worker={worker} />
        <ListaDeAparelhos instancias={instancias} personas={dados.personas} doWorker={worker.devices} />
        <AparelhosParaAdotar worker={worker} />
        <TerminalDoWorker worker={worker} />
        <AbasDoServidor id={worker.id} instancias={instancias} dados={dados} now={now} />
      </CardBody>
    </Card>
  );
}

/**
 * Achado #179: o túnel SSH é o único transporte do ADB remoto e do canal do agente — hoje a queda dele aparece
 * como sintomas espalhados (aparelhos "sem ADB", worker "sem batida"). Esta linha diz a causa direto: no ar
 * desde quando, ou caído desde quando e por quê.
 */
function TunelDoWorker({ worker, now }: { worker: Worker; now: number }) {
  if (!worker.transport_state) {
    return null;      // nunca sondado: sem aparelho externo vinculado a este worker, nada a mostrar ainda
  }
  const fora = worker.transport_state === 'down';
  return (
    <p className={cx(styles.dim, fora && styles.alerta)}>
      <Cable size={14} style={{ verticalAlign: 'text-bottom', marginRight: 4 }} />
      Túnel: {fora ? 'fora' : 'no ar'}
      {worker.transport_since ? ` (${tempoRelativo(worker.transport_since, now)})` : ''}
      {fora && worker.transport_detail ? ` — ${worker.transport_detail}` : ''}
    </p>
  );
}

/** Infraestrutura é a tela dona das vagas (revisão de UX, tarefa 02): a conta sai de `store/metricas`, a mesma do
 *  semáforo do cabeçalho. Fora do ar, a ocupação lá não se sabe — "0 de 6" seria um número inventado. */
function VagasOcupadas({ ocupacao: o }: { ocupacao: Ocupacao }) {
  return (
    <Recurso icon={Smartphone} rotulo="Vagas ocupadas"
             valor={o.ocupadas === null ? `? de ${o.vagas}` : `${o.ocupadas} de ${o.vagas}`}
             fracao={o.ocupadas !== null && o.vagas ? o.ocupadas / o.vagas : 0} />
  );
}

/** "5 de 4" é real (o backend conta igual), não erro de cálculo: diz-se o que é e o que implica, sem adivinhar a causa. */
function AvisoDeVagas({ ocupacao: o }: { ocupacao: Ocupacao }) {
  if (o.ocupadas === null) return <p className={styles.dim}>Servidor fora do ar: a ocupação das vagas lá não é conhecida agora.</p>;
  if (!o.acima) return null;
  return (
    <p className={styles.alerta} role="note">
      Acima da capacidade: {o.ocupadas} aparelhos ligados para {plural(o.vagas, 'vaga', 'vagas')}. A vaga é o quanto
      este servidor comporta ligado ao mesmo tempo; acima dela os aparelhos disputam a RAM e o próximo a ligar pode
      ficar sem memória.
    </p>
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

/**
 * Aparelhos que este worker ANUNCIA e que ainda não são instância deste parque (item 4.5).
 *
 * O inventário já chegava no `hello` (`devices[].serial`, `.adb_port`) e era descartado: o central só usava o
 * que estivesse em `instances.external`. Acrescentar um aparelho significava editar dois blocos do
 * `config.yaml`, reinstalar a tarefa agendada do túnel com o mapa novo e reiniciar o backend. Aqui é um clique:
 * o central aloca a porta do túnel, cria a instância e regrava o arquivo de mapa que o túnel relê sozinho.
 */
function AparelhosParaAdotar({ worker }: { worker: Worker }) {
  const [ocupado, setOcupado] = useState<string | null>(null);
  // Contra TODAS as instâncias, não só as deste servidor: um aparelho que o worker declara com um id que já
  // pertence a outra máquina não é candidato a adoção — é divergência de inventário, e a API recusaria com 409.
  const todas = useAppStore((s) => s.instances);
  const livres = (worker.devices ?? []).filter((d) => !d.instance_id || !todas[d.instance_id]);
  if (livres.length === 0) return null;

  async function adotar(serial: string): Promise<void> {
    setOcupado(serial);
    try {
      const r = await api.adoptWorkerDevice(worker.id, serial);
      toast({
        tone: 'success',
        title: `${serial} virou ${r.instance.id}`,
        message: `Túnel: ${r.tunnel_map}. O mapa foi regravado — não é preciso reinstalar a tarefa do túnel.`,
      });
    } catch (e) {
      toastError(`Não foi possível adotar ${serial}`, e);
    } finally {
      setOcupado(null);
    }
  }

  return (
    <div>
      <p className={styles.dim}>
        {plural(livres.length, 'aparelho anunciado', 'aparelhos anunciados')} por este servidor que ainda não
        {livres.length === 1 ? ' é instância' : ' são instâncias'} do parque:
      </p>
      <ul className={styles.aparelhos}>
        {livres.map((d) => (
          <li key={d.serial} className={styles.aparelho}>
            <span className={styles.aparelhoId}>{d.serial}</span>
            <span className={styles.aparelhoMeio}>
              <span className={styles.dim}>
                {d.avd_name ? `${d.avd_name} · ` : ''}{d.state}
                {d.adb_port ? ` · adb ${d.adb_port}` : ' · sem porta de ADB declarada'}
              </span>
            </span>
            <Button size="sm" variant="outline" loading={ocupado === d.serial}
                    disabledReason={!d.adb_port
                      ? 'O agente anunciou este aparelho sem porta de ADB: o túnel não teria para onde encaminhar.'
                      : undefined}
                    onClick={() => void adotar(d.serial)}>
              Adotar
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Servidor → aparelho → persona(s) → tarefa: cada linha leva ao aparelho e mostra o que ele está fazendo agora; embaixo
 * dela, as personas vinculadas (N:N), cada uma com o app do vínculo e a sessão AQUI, e o atalho para abri-la.
 */
function ListaDeAparelhos({ instancias, personas, doWorker, onAposentado }: {
  instancias: readonly Instance[];
  /** Aparelho → personas (v0.29). */
  personas: ReadonlyMap<string, readonly PersonaOnDevice[]>;
  doWorker?: readonly WorkerDevice[];
  /** Só na lista do hospedeiro: aparelho `dynamic` daqui pode ser aposentado (o AVD dele mora nesta máquina). */
  onAposentado?: (id: string) => void;
}) {
  const openFocus = useUiStore((s) => s.openFocus);
  const openPersona = useUiStore((s) => s.openPersona);
  const workers = useAppStore((s) => s.workers);
  const selectRun = useUiStore((s) => s.selectRun);
  const setView = useUiStore((s) => s.setView);
  const apps = useAppStore((s) => s.apps);
  const [aposentando, setAposentando] = useState<string | null>(null);
  const [recusas, setRecusas] = useState<Record<string, RecusaNaTela | undefined>>({});
  if (instancias.length === 0) {
    return <p className={styles.dim}>Nenhum aparelho amarrado a este servidor.</p>;
  }
  const processo = new Map((doWorker ?? []).map((d) => [d.instance_id ?? '', d]));

  async function aposentar(i: Instance): Promise<void> {
    const { confirmed } = await confirm({
      title: `Aposentar ${i.id}?`,
      danger: true,
      confirmLabel: 'Aposentar aparelho',
      cancelLabel: 'Cancelar',
      body: `O registro de ${i.id} sai do parque e o AVD dele — o disco do emulador, com apps, contas e dados — é `
        + 'apagado desta máquina. Não dá para desfazer, e o id e as portas não são reaproveitados. O aparelho '
        + 'precisa estar desligado, sem persona vinculada e sem trabalho em curso.',
    });
    if (!confirmed) return;
    setAposentando(i.id);
    setRecusas((r) => ({ ...r, [i.id]: undefined }));
    try {
      const r = await api.retireInstance(i.id);
      toast({
        tone: 'success',
        title: `${i.id} aposentado`,
        message: r.avd_removed ? 'O AVD foi apagado do disco.' : 'Não havia AVD no disco para apagar.',
      });
      onAposentado?.(i.id);
    } catch (e) {
      // A recusa fica na linha do aparelho, com o motivo do backend: um toast sumiria antes de a pessoa ler.
      setRecusas((r) => ({ ...r, [i.id]: recusaDaAposentadoria(toApiError(e)) }));
    } finally {
      setAposentando(null);
    }
  }

  return (
    <ul className={styles.aparelhos}>
      {instancias.map((i) => {
        // RF-40: aparelho de servidor fora do ar é "desconhecido", como no Painel e no Foco (era "parado").
        const meta = instanceStateMeta(estadoContado(i, workers));
        const tipo = tipoDoAparelho(i.kind);
        const proc = processo.get(i.id);
        // A instância do config.yaml sai editando o arquivo: o botão nem aparece para ela.
        const aposentavel = !!onAposentado && i.origin === 'dynamic';
        const recusa = recusas[i.id];
        const aqui = personas.get(i.id) ?? [];
        return (
          <li key={i.id} className={styles.aparelho}>
            <button type="button" className={styles.aparelhoBtn} onClick={() => openFocus(i.id)}
                    aria-label={`Abrir ${i.id} na visão de foco`}>
              <span className={styles.aparelhoId}>{i.id}</span>
              <Badge tone={meta.tone} size="sm" plain>{meta.label}</Badge>
              {tipo ? <Badge tone="muted" size="sm" plain title={tipo.title}>{tipo.label}</Badge> : null}
              {/* As três fontes de inventário discordam sobre qual aparelho está por trás deste id: enquanto
                  isso durar, o backend recusa verbo destrutivo — e a tela precisa dizer por quê. */}
              {i.inventory_state === 'divergent' ? (
                <Badge tone="danger" size="sm" plain title={i.inventory_detail ?? undefined}>inventário divergente</Badge>
              ) : null}
            </button>
            <span className={styles.aparelhoMeio}>
              {/* O que o worker vê do PROCESSO, que é diferente do que o central vê do Android. */}
              {proc ? <span className={styles.dim}>processo: {proc.state}</span> : null}
              {proc?.detail ? <span className={styles.dim} title={proc.detail}> · {proc.detail}</span> : null}
              <Capacidades instancia={i} />
              <PausaDoReparo instancia={i} />
            </span>
            {i.current?.run_id ? (
              <button type="button" className={styles.tarefa}
                      onClick={() => { selectRun(i.current!.run_id!); setView('execucoes'); }}>
                {i.current.step_title ?? 'em execução'}
                {i.current.steps_total ? ` (${i.current.steps_done}/${i.current.steps_total})` : ''}
              </button>
            ) : <span className={cx(styles.dim, styles.semTarefa)}>sem tarefa</span>}
            {aposentavel ? (
              <Button size="sm" variant="dangerGhost" icon={Trash2} loading={aposentando === i.id}
                      aria-label={`Aposentar ${i.id}`} onClick={() => void aposentar(i)}>
                Aposentar
              </Button>
            ) : null}
            {recusa ? (
              <p role="alert" className={styles.recusaDaLinha}>
                <strong>{recusa.passo ?? recusa.titulo}</strong> {recusa.mensagem}
              </p>
            ) : null}
            {aqui.length > 0 ? (
              // `role` explícito: sem marcador (`list-style: none`), o Safari deixa de anunciar a lista.
              <ul role="list" className={styles.personasDoAparelho} aria-label={`Personas em ${i.id}`}>
                {aqui.map((p) => {
                  const app = p.app_id ? apps.find((a) => a.id === p.app_id)?.name ?? p.app_id : null;
                  const sessao = p.session ? metaOf(SESSION_STATUS, p.session.status) : null;
                  return (
                    <li key={`${p.profile_id}:${p.app_id ?? ''}`}>
                      <button type="button" className={styles.personaDoAparelho} onClick={() => openPersona(p.profile_id)}
                              aria-label={`Abrir a persona ${p.name}${app ? ` (${app})` : ''}`}>
                        <Avatar src={profileAvatarUrl(p.profile_id, p.has_avatar)} name={p.name || p.profile_id} size={18} />
                        <span className={styles.personaNome}>{p.name}</span>
                        {p.username ? <span className={styles.dim}>@{p.username}</span> : null}
                        {app ? <span className={styles.dim}>· {app}</span> : null}
                        {sessao ? <Badge size="sm" plain tone={sessao.tone}>{sessao.label}</Badge> : null}
                        {p.is_primary ? <Star size={12} aria-label="aparelho principal" className={styles.principal} /> : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : null}
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
/** "reparo pausado até 15:27": discreta como as capacidades, com o porquê na dica (25.13). */
function PausaDoReparo({ instancia }: { instancia: Instance }) {
  const agora = useNow();
  const pausa = pausaDoReparoMeta(instancia.repair_pause, agora);
  return pausa ? <span className={styles.dim} title={pausa.title}> · {pausa.label}</span> : null;
}

function Capacidades({ instancia }: { instancia: Instance }) {
  const partes: string[] = [];
  if (instancia.api_level) partes.push(`API ${instancia.api_level}`);
  const abi = instancia.abis?.[0];
  if (abi) partes.push(abi);
  if (instancia.play_store === true) partes.push('Play Services');
  if (instancia.play_store === false) partes.push('AOSP');
  // O renderizador do emulador (29.11) é capacidade como as outras: decide se um app roda aqui. O fallback sai em
  // destaque, porque é o que a pessoa não pediu — o resto acompanha a linha, sem chamar atenção.
  const gpu = renderizadorMeta(instancia.renderer);
  if (gpu && !gpu.fallback) partes.push(gpu.label);
  if (partes.length === 0 && !gpu?.fallback) return null;
  const dica = [instancia.system_image, gpu && !gpu.fallback ? gpu.title : null].filter(Boolean).join(' — ');
  return (
    <>
      {partes.length > 0 ? <span className={styles.dim} title={dica || undefined}> · {partes.join(' · ')}</span> : null}
      {gpu?.fallback ? <Badge tone="warning" size="sm" plain title={gpu.title}>{gpu.label}</Badge> : null}
    </>
  );
}

/** O que a tela precisa para responder "o que aconteceu NESTE servidor" sem inventar uma rota por servidor. */
export interface DadosDoServidor {
  events: readonly EventRecord[];
  runs: readonly RunSummary[];
  apps: readonly AppConfig[];
  /** Aparelho → personas vinculadas (N:N, v0.29). */
  personas: ReadonlyMap<string, readonly PersonaOnDevice[]>;
  appState: readonly DeviceAppState[];
}

type Aba = 'logs' | 'evidencias' | 'fila' | 'perfis';

/**
 * Logs, evidências, fila e perfis/apps POR SERVIDOR — o que o pedido (seção 6) e o E5 listam e a tela não
 * tinha. Nenhum evento carrega `worker_id`; o que ele carrega é `instance_id`, e quem hospeda cada aparelho é
 * exatamente o que esta tela sabe. Por isso o filtro é feito aqui, com os aparelhos do cartão.
 */
function AbasDoServidor({ id, instancias, dados, now }: {
  id: string; instancias: readonly Instance[]; dados: DadosDoServidor; now: number;
}) {
  const [aba, setAba] = useState<Aba>('logs');
  const selectRun = useUiStore((s) => s.selectRun);
  const setView = useUiStore((s) => s.setView);
  const ids = useMemo(() => new Set(instancias.map((i) => i.id)), [instancias]);
  const logs = useMemo(() => eventosDoServidor(dados.events, ids, ['log', 'command.updated', 'worker.refused']),
                       [dados.events, ids]);
  const evidencias = useMemo(() => eventosDoServidor(dados.events, ids, ['evidence.added', 'action.logged']),
                             [dados.events, ids]);
  const fila = useMemo(() => filaDoServidor(dados.runs, ids), [dados.runs, ids]);

  if (instancias.length === 0) return null;

  const abas = [
    { id: 'logs' as const, label: 'Registros', icon: ScrollText, count: logs.length },
    { id: 'evidencias' as const, label: 'Evidências', icon: Camera, count: evidencias.length },
    { id: 'fila' as const, label: 'Fila', icon: ListOrdered, count: fila.length, alert: fila.length > 0 },
    { id: 'perfis' as const, label: 'Personas e apps', icon: User, count: instancias.length },
  ];

  return (
    <div className={styles.abas}>
      <Tabs tabs={abas} active={aba} onChange={setAba} idBase={`infra-${id}`} label={`Detalhes de ${id}`} />
      <div role="tabpanel" id={`infra-${id}-panel-${aba}`} aria-labelledby={`infra-${id}-tab-${aba}`}>
        {aba === 'logs' ? (
          <ListaDeEventos itens={logs} now={now}
                          vazio="Nenhum evento recente dos aparelhos deste servidor. O painel guarda só os últimos; o histórico completo está em Execuções." />
        ) : aba === 'evidencias' ? (
          <ListaDeEventos itens={evidencias} now={now}
                          vazio="Nenhuma evidência recente destes aparelhos." />
        ) : aba === 'fila' ? (
          fila.length === 0 ? <p className={styles.dim}>Nada na fila: nenhuma execução em voo nestes aparelhos.</p> : (
            <ul className={styles.linhas}>
              {fila.map((r) => {
                const aqui = r.instance_ids.filter((i) => ids.has(i));
                return (
                  <li key={r.id} className={styles.linha}>
                    <button type="button" className={styles.tarefa}
                            onClick={() => { selectRun(r.id); setView('execucoes'); }}>
                      {r.short_id}
                    </button>
                    <span className="truncate" title={r.command}>{r.command}</span>
                    <span className={styles.dim}>
                      {r.status} · {plural(aqui.length, 'aparelho aqui', 'aparelhos aqui')}
                      {r.counts.pending > 0 ? ` · ${r.counts.pending} aguardando vaga` : ''}
                    </span>
                  </li>
                );
              })}
            </ul>
          )
        ) : (
          <ul className={styles.linhas}>
            {instancias.map((i) => {
              const app = dados.apps.find((a) => a.id === i.app_id);
              const aqui = dados.personas.get(i.id) ?? [];
              const instalado = dados.appState.filter((a) => a.instance_id === i.id);
              return (
                <li key={i.id} className={styles.linha}>
                  <span className={styles.aparelhoId}>{i.id}</span>
                  <span className="truncate">
                    {app ? app.name : i.app_id ?? 'sem app associado'}
                    {instalado.length > 0 ? (
                      <span className={styles.dim}>
                        {' · '}
                        {instalado.map((a) => `${a.package_name} ${a.observed_version_name ?? a.state}`).join(' · ')}
                      </span>
                    ) : null}
                  </span>
                  <span className={styles.dim}>
                    {aqui.length > 0
                      ? aqui.map((p) => (p.username ? `@${p.username}` : p.name)
                        + (p.session ? ` (${metaOf(SESSION_STATUS, p.session.status).label})` : '')).join(' · ')
                      : i.account_label ?? 'sem persona'}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

function ListaDeEventos({ itens, now, vazio }: { itens: readonly EventRecord[]; now: number; vazio: string }) {
  if (itens.length === 0) return <p className={styles.dim}>{vazio}</p>;
  return (
    <ul className={styles.linhas}>
      {itens.map((e, n) => (
        <li key={e.id ?? `${e.ts}-${n}`} className={cx(styles.linha, e.level === 'error' && styles.alerta)}>
          <span className={styles.dim} title={tempoRelativo(e.ts, now)}>{formatClock(e.ts)}</span>
          <span className={styles.aparelhoId}>{e.instance_id}</span>
          <span className="truncate" title={e.message}>{e.message}</span>
        </li>
      ))}
    </ul>
  );
}
