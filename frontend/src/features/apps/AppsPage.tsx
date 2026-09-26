/**
 * Menu Aplicativos centrado no APP (item 12.2, pedido do dono em 24/09).
 *
 * Antes o menu era a página de versões de APK: nada dizia, de um app, quantas execuções teve, quanto de IA custou,
 * que perfis têm conta nele, em que aparelhos está, onde falha e quanto já roda por receita. Agora:
 * - "Por app": um cartão por app com esses números (últimos 7 dias) — e, ao abrir, a página do app com execuções
 *   (o rastro de cada uma abre na tela de Execuções), custo por dia, etapas por origem, falhas recentes, contas,
 *   aparelhos, receitas e fluxos.
 * - "Versões e instalação": a página de APKs de sempre, sem mudança.
 *
 * Loja de apps (pedido do dono, 26/09): a aba "Loja" abre primeiro — vitrine no jeito da Play Store, cadastro de app
 * novo, distribuição com prévia e atualização de quem ficou para trás — e "Proxy" distribui o proxy do aparelho.
 */
import { AppWindow, ArrowLeft, CircleDollarSign, Globe, KeyRound, ListChecks, Package, Smartphone, Sparkles, Store, Users } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { AppDetail, AppOverview } from '../../api/types';
import appStyles from '../../App.module.css';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { metaOf, RUN_STATUS } from '../../lib/status';
import { formatAgo, useNow } from '../../lib/time';
import { toastError } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { LojaPage } from '../loja/LojaPage';
import { ProxyPage } from '../loja/ProxyPage';
import { ReleasesPage } from '../releases/ReleasesPage';
import styles from './Apps.module.css';

type Aba = 'loja' | 'apps' | 'versoes' | 'proxy';

const LEAD: Record<Aba, string> = {
  loja: 'Os aplicativos do parque no jeito de uma loja: a versão de cada um, quem está atrasado, e distribuir para '
    + 'todos, para N aparelhos ou para os que você escolher, com prévia antes de confirmar.',
  apps: 'Cada app com as contas dos perfis nele, os aparelhos onde está, as execuções que o tocaram, o custo de IA '
    + 'e o quanto do trabalho já roda por receita, sem IA.',
  versoes: 'Todas as versões de todos os apps numa lista só, com a loja (Play Store) e o que cada aparelho tem.',
  proxy: 'O proxy HTTP de cada aparelho, pedido como uma versão: ligado recebe agora, desligado quando ligar.',
};

const usd = (v: number) => (v >= 0.01 ? `US$ ${v.toFixed(2)}` : v > 0 ? '< US$ 0,01' : 'US$ 0');

export function AppsPage() {
  const [aba, setAba] = useState<Aba>('loja');
  const [aberto, setAberto] = useState<string | null>(null);
  const abas: TabDef<Aba>[] = [
    { id: 'loja', label: 'Loja', icon: Store },
    { id: 'apps', label: 'Por app', icon: AppWindow },
    { id: 'versoes', label: 'Versões e instalação', icon: Package },
    { id: 'proxy', label: 'Proxy', icon: Globe },
  ];
  return (
    <div className={appStyles.page}>
      {aba !== 'versoes' && !aberto ? (
        <div className={appStyles.pageHeader}>
          <div>
            <h1 className={appStyles.pageTitle}>Aplicativos</h1>
            <p className={appStyles.pageLead}>{LEAD[aba]}</p>
          </div>
        </div>
      ) : null}
      {!aberto ? <Tabs tabs={abas} active={aba} onChange={setAba} idBase="aplicativos" label="Aplicativos" /> : null}
      <TabPanel idBase="aplicativos" id={aba}>
        {aba === 'apps' ? (aberto ? <AppDetailView appId={aberto} onBack={() => setAberto(null)} /> : <AppsGrid onOpen={setAberto} />) : null}
        {aba === 'versoes' ? <ReleasesPage embutida /> : null}
        {aba === 'loja' ? <LojaPage /> : null}
        {aba === 'proxy' ? <ProxyPage /> : null}
      </TabPanel>
    </div>
  );
}

function AppsGrid({ onOpen }: { onOpen: (id: string) => void }) {
  const [apps, setApps] = useState<AppOverview[] | null>(null);
  const now = useNow();
  useEffect(() => {
    api.appsOverview(7).then(setApps).catch((e) => {
      setApps([]);
      toastError('Não foi possível carregar os aplicativos', e);
    });
  }, []);
  if (apps === null) {
    return <LoadingRegion label="Carregando aplicativos…"><Skeleton height={160} /></LoadingRegion>;
  }
  if (apps.length === 0) {
    return <EmptyState icon={AppWindow} title="Nenhum aplicativo cadastrado" hint="Cadastre em Configuração → Aplicativos.">Sem apps.</EmptyState>;
  }
  return (
    <div className={styles.grid}>
      {apps.map((a) => {
        const instalados = a.devices.installed ?? 0;
        const receitas = a.recipes.active ?? 0;
        return (
          <button key={a.app_id} type="button" className={styles.appCard} onClick={() => onOpen(a.app_id)}
                  aria-label={`Abrir ${a.name}`}>
            <div className={styles.appCardHead}>
              <span className={styles.appIcon} aria-hidden>{a.name.slice(0, 1)}</span>
              <div className={styles.appCardTitle}>
                <strong>{a.name}</strong>
                <code>{a.package}</code>
              </div>
            </div>
            <div className={styles.badges}>
              <Badge size="sm" tone={a.has_catalog ? 'success' : 'neutral'}>{a.has_catalog ? 'catálogo de ações' : 'IA livre'}</Badge>
              <Badge size="sm" tone={a.automated_login ? 'info' : 'neutral'}>{a.automated_login ? 'login automático' : 'login pela pessoa'}</Badge>
            </div>
            <dl className={styles.stats}>
              <Stat icon={Users} rotulo="contas" valor={`${a.accounts_ready}/${a.accounts}`} dica="prontas / total" />
              <Stat icon={Smartphone} rotulo="aparelhos" valor={String(instalados)} dica="com o app instalado" />
              <Stat icon={ListChecks} rotulo="execuções 7d" valor={`${a.runs_completed}/${a.runs}`} dica="concluídas / total" />
              <Stat icon={CircleDollarSign} rotulo="IA 7d" valor={usd(a.ai_usd)} />
              <Stat icon={Sparkles} rotulo="receitas" valor={String(receitas)} dica="ativas" />
              <Stat icon={Package} rotulo="versões" valor={String(a.releases)} />
            </dl>
            <span className={styles.muted}>
              {a.last_run_at ? `última execução ${formatAgo(a.last_run_at, now)}` : 'nenhuma execução nos últimos 7 dias'}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function Stat({ icon: Icon, rotulo, valor, dica }: { icon: typeof Users; rotulo: string; valor: string; dica?: string }) {
  return (
    <div className={styles.stat} title={dica}>
      <dt><Icon size={12} aria-hidden /> {rotulo}</dt>
      <dd>{valor}</dd>
    </div>
  );
}

function AppDetailView({ appId, onBack }: { appId: string; onBack: () => void }) {
  const [d, setD] = useState<AppDetail | null>(null);
  const now = useNow();
  const selectRun = useUiStore((s) => s.selectRun);
  const setView = useUiStore((s) => s.setView);
  useEffect(() => {
    api.appOverview(appId, 30).then(setD).catch((e) => toastError('Não foi possível carregar o aplicativo', e));
  }, [appId]);
  if (!d) return <LoadingRegion label="Carregando o aplicativo…"><Skeleton height={220} /></LoadingRegion>;

  const abrirExecucao = (id: string) => {
    selectRun(id);
    setView('execucoes');
  };
  const maxDia = Math.max(0.0001, ...d.ai_usd_by_day.map((x) => x.usd));
  const totalIa = d.ai_usd_by_day.reduce((s, x) => s + x.usd, 0);
  const porOrigem: Record<string, number> = {};
  for (const s of d.steps) if (s.status === 'succeeded') porOrigem[s.origem] = (porOrigem[s.origem] ?? 0) + s.n;
  const totalOk = Object.values(porOrigem).reduce((a, b) => a + b, 0);
  const semIa = (porOrigem.recipe ?? 0);

  return (
    <div className={styles.detail}>
      <div className={styles.detailHead}>
        <Button size="sm" variant="ghost" icon={ArrowLeft} onClick={onBack}>Aplicativos</Button>
        <div className={styles.appCardHead}>
          <span className={styles.appIcon} aria-hidden>{d.name.slice(0, 1)}</span>
          <div className={styles.appCardTitle}>
            <h2 className={styles.detailTitle}>{d.name}</h2>
            <code>{d.package}</code>
          </div>
        </div>
        <div className={styles.badges}>
          <Badge tone={d.has_catalog ? 'success' : 'neutral'}>{d.has_catalog ? 'catálogo de ações e políticas' : 'IA livre (sem catálogo)'}</Badge>
          <Badge tone={d.automated_login ? 'info' : 'neutral'}>{d.automated_login ? 'login automático' : 'login pela pessoa, pelo Foco'}</Badge>
        </div>
      </div>

      <div className={styles.detailGrid}>
        <Card>
          <CardHeader title="Execuções" subtitle="As mais recentes que tocaram este app. Abrir mostra o rastro completo: plano, etapas, decisões e telas." />
          <CardBody>
            {d.runs.length === 0 ? <p className={styles.muted}>Nenhuma execução ainda.</p> : (
              <ul className={styles.runList}>
                {d.runs.map((r) => (
                  <li key={r.id}>
                    <button type="button" className={styles.runRow} onClick={() => abrirExecucao(r.id)}>
                      <StatusBadge meta={metaOf(RUN_STATUS, r.status)} size="sm" />
                      <span className={styles.runCommand}>{r.command}</span>
                      {(r.app_ids ?? []).length > 1 ? <Badge size="sm" tone="accent">{(r.app_ids ?? []).length} apps</Badge> : null}
                      <span className={styles.muted}>{formatAgo(r.created_at, now)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <div className={styles.sideStack}>
          <Card>
            <CardHeader title="Custo de IA" subtitle={`Últimos ${d.days} dias · total ${usd(totalIa)}`} />
            <CardBody>
              {d.ai_usd_by_day.length === 0 ? <p className={styles.muted}>Sem chamadas de IA no período.</p> : (
                <div className={styles.bars} role="img" aria-label={`Custo de IA por dia, total ${usd(totalIa)}`}>
                  {d.ai_usd_by_day.map((x) => (
                    <span key={x.day} className={styles.bar} title={`${x.day.slice(8, 10)}/${x.day.slice(5, 7)}: ${usd(x.usd)}`}
                          style={{ height: `${Math.max(4, (x.usd / maxDia) * 100)}%` }} />
                  ))}
                </div>
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Etapas concluídas" subtitle="Quanto já roda por receita (sem IA)." />
            <CardBody>
              <p className={styles.big}>{totalOk ? `${Math.round((semIa / totalOk) * 100)}%` : '—'}</p>
              <p className={styles.muted}>{semIa} por receita · {porOrigem.ai ?? 0} pela IA · {porOrigem['recipe+ai'] ?? 0} receita + IA</p>
            </CardBody>
          </Card>
        </div>

        <Card>
          <CardHeader title="Falhas recentes" subtitle="Etapas que falharam ou ficaram incertas neste app." />
          <CardBody>
            {d.recent_failures.length === 0 ? <p className={styles.muted}>Nenhuma falha registrada.</p> : (
              <ul className={styles.failList}>
                {d.recent_failures.map((f, i) => (
                  <li key={`${f.run_id}-${i}`}>
                    <button type="button" className={styles.failRow} onClick={() => abrirExecucao(f.run_id)}>
                      <strong>{f.title}</strong>
                      <span className={styles.failDetail}>{f.status_detail ?? '—'}</span>
                      <span className={styles.muted}>{f.finished_at ? formatAgo(f.finished_at, now) : ''}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Contas" subtitle="Perfis que têm conta neste app." />
          <CardBody>
            {d.accounts.length === 0 ? <p className={styles.muted}>Nenhum perfil tem conta neste app. Adicione pela aba Contas do perfil.</p> : (
              <ul className={styles.simpleList}>
                {d.accounts.map((c) => (
                  <li key={c.id}>
                    <KeyRound size={12} aria-hidden /> <strong>@{c.username}</strong>
                    <span className={styles.muted}>{c.handle}</span>
                    <Badge size="sm" tone={c.session_status === 'session_ready' ? 'success' : 'neutral'}>
                      {c.session_status === 'session_ready' ? 'conectado' : c.session_status}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Aparelhos" subtitle="Onde o app está instalado e em que versão." />
          <CardBody>
            {d.devices.length === 0 ? <p className={styles.muted}>Sem registro de instalação.</p> : (
              <ul className={styles.simpleList}>
                {d.devices.map((x) => (
                  <li key={x.instance_id}>
                    <Smartphone size={12} aria-hidden /> <strong>{x.instance_id}</strong>
                    <Badge size="sm" tone={x.state === 'installed' ? 'success' : x.state === 'missing' ? 'neutral' : 'warning'}>{x.state}</Badge>
                    <span className={styles.muted}>{x.observed_version_name ?? ''}{x.drift_kind ? ` · ${x.drift_kind}` : ''}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Receitas e fluxos" subtitle="O que este app já sabe fazer sem chamar a IA." />
          <CardBody>
            <h4 className={styles.subTitle}>Fluxos ({d.flows.length})</h4>
            {d.flows.length === 0 ? <p className={styles.muted}>Nenhum fluxo salvo.</p> : (
              <ul className={styles.simpleList}>
                {d.flows.map((f) => (
                  <li key={f.id}><strong>{f.name}</strong><span className={styles.muted}>{f.uses}× · {f.status}</span></li>
                ))}
              </ul>
            )}
            <h4 className={styles.subTitle}>Receitas ({d.recipes.length})</h4>
            {d.recipes.length === 0 ? <p className={styles.muted}>Nenhuma receita aprendida.</p> : (
              <ul className={styles.simpleList}>
                {d.recipes.map((r) => (
                  <li key={r.id}>
                    <strong>{r.step_key ?? `receita ${r.id}`}</strong>
                    <Badge size="sm" tone={r.status === 'active' ? 'success' : 'warning'}>{r.status}</Badge>
                    <span className={styles.muted}>v{r.app_version} · {r.replay_ok} ok / {r.replay_fail} falhas</span>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
