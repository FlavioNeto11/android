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
import { AppWindow, ArrowLeft, CircleDollarSign, KeyRound, ListChecks, Package, Smartphone, Sparkles, Store, Users, Wifi } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { AppDetail, AppOverview } from '../../api/types';
import appStyles from '../../App.module.css';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { type LoadError, LoadErrorState, toLoadError } from '../../lib/loadError';
import { ACCOUNT_SESSION_STATUS, APP_INSTALL_STATE, DRIFT_KIND, FLOW_STATUS, metaOf, RUN_STATUS } from '../../lib/status';
import { tempoRelativo, useNow } from '../../lib/time';
import { useUiStore } from '../../store/ui';
import { LojaPage } from '../loja/LojaPage';
import { ProxyPage } from '../loja/ProxyPage';
import { RedePage } from '../rede/RedePage';
import { ReleasesPage } from '../releases/ReleasesPage';
import { RECIPE_STATUS } from '../settings/flowsRecipes';
import { LegendaDaLoja } from './glossario';
import styles from './Apps.module.css';

type Aba = 'loja' | 'apps' | 'versoes' | 'rede';

const LEAD: Record<Aba, string> = {
  loja: 'Os aplicativos do parque no jeito de uma loja: a versão de cada um, quem está atrasado, e distribuir para '
    + 'todos, para N aparelhos ou para os que você escolher, com prévia antes de confirmar.',
  apps: 'Cada app com as contas dos perfis nele, os aparelhos onde está, as execuções que o tocaram, o custo de IA '
    + 'e o quanto do trabalho já roda por receita, sem IA.',
  versoes: 'Todas as versões de todos os apps numa lista só, com a loja (Play Store) e o que cada aparelho tem.',
  // Item 25.8 (ADR-056): a aba principal é Rede — VPN e proxy por aparelho, com IP de saída medido e prova de
  // tráfego. O "Proxy (legado)" deixou de ser aba (revisão de UX, tarefa 06) e mora no fim da Rede, recolhido e
  // marcado como descontinuado: é a única tela que ainda sabe TIRAR o proxy HTTP global da migração 041 do aparelho
  // (RedePage só lê esse legado, nunca escreve nele — achado do revisor no 25.8).
  rede: 'VPN e proxy por aparelho: perfis, política, IP de saída medido e prova de tráfego, com prévia antes de confirmar.',
};

const usd = (v: number) => (v >= 0.01 ? `US$ ${v.toFixed(2)}` : v > 0 ? '< US$ 0,01' : 'US$ 0');

const ABAS: readonly Aba[] = ['loja', 'apps', 'versoes', 'rede'];
const ehAba = (v: string | undefined): v is Aba => !!v && (ABAS as readonly string[]).includes(v);
/** `?aba=proxy` (link antigo da aba "Proxy (legado)") abre a Rede com o trecho do legado já aberto. */
const ABA_ANTIGA_DO_PROXY = 'proxy';

export function AppsPage() {
  // Guia e app aberto vêm do link: `#/aplicativos?aba=versoes`, `#/aplicativos/<app>` (o app abre em "Por app").
  const rota = useUiStore((s) => s.rota);
  const navegar = useUiStore((s) => s.navegar);
  const trocarQuery = useUiStore((s) => s.trocarQuery);
  const voltarPara = useUiStore((s) => s.voltarPara);
  const aberto = rota.tela === 'aplicativos' ? rota.segmentos[0] ?? null : null;
  const proxyAntigo = !aberto && rota.query.aba === ABA_ANTIGA_DO_PROXY;
  const aba: Aba = aberto ? 'apps' : proxyAntigo ? 'rede' : ehAba(rota.query.aba) ? rota.query.aba : 'loja';
  // Trocar de guia substitui o link; "Loja" é a guia padrão e não aparece nele.
  const setAba = (a: Aba) => trocarQuery({ aba: a === 'loja' ? undefined : a });
  const abrirApp = (id: string) => navegar({ tela: 'aplicativos', segmentos: [id] });
  const fecharApp = () => voltarPara({ tela: 'aplicativos', query: { aba: 'apps' } }, 'push',
    (de) => de.tela === 'aplicativos' && de.segmentos.length === 0);
  const abas: TabDef<Aba>[] = [
    { id: 'loja', label: 'Loja', icon: Store },
    { id: 'apps', label: 'Por app', icon: AppWindow },
    { id: 'versoes', label: 'Versões e instalação', icon: Package },
    { id: 'rede', label: 'Rede', icon: Wifi },
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
      {aba === 'loja' || aba === 'versoes' ? <LegendaDaLoja /> : null}
      <TabPanel idBase="aplicativos" id={aba}>
        {aba === 'apps' ? (aberto ? <AppDetailView appId={aberto} onBack={fecharApp} /> : <AppsGrid onOpen={abrirApp} />) : null}
        {aba === 'versoes' ? <ReleasesPage embutida /> : null}
        {aba === 'loja' ? <LojaPage /> : null}
        {aba === 'rede' ? (
          <>
            <RedePage />
            <ProxyDescontinuado abertoDeInicio={proxyAntigo} />
          </>
        ) : null}
      </TabPanel>
    </div>
  );
}

/** O proxy global antigo: recolhido, com o selo "descontinuado" e o aviso de quando ainda serve. */
function ProxyDescontinuado({ abertoDeInicio }: { abertoDeInicio: boolean }) {
  return (
    <Disclosure
      className={styles.proxyLegado}
      defaultOpen={abertoDeInicio}
      summary={<>Proxy global (antigo) <Badge size="sm" tone="warning">descontinuado</Badge></>}
    >
      <div className={styles.avisoDescontinuado}>
        <p className={styles.muted}>
          É o proxy HTTP único de antes da rede por aparelho (26/09), pedido como uma versão. Use a Rede, acima, para
          tudo o que é novo. Este trecho só serve para tirar um proxy antigo que ainda esteja aplicado em algum aparelho.
        </p>
        <ProxyPage />
      </div>
    </Disclosure>
  );
}

function AppsGrid({ onOpen }: { onOpen: (id: string) => void }) {
  const [apps, setApps] = useState<AppOverview[] | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const now = useNow();
  const carregar = useCallback(async () => {
    setErro(null);
    try {
      setApps(await api.appsOverview(7));
    } catch (e) {
      // A falha fica na tela: `[]` aqui dizia "Nenhum aplicativo cadastrado" com a API caída (P1.3).
      setErro(toLoadError(e));
    }
  }, []);
  useEffect(() => { void carregar(); }, [carregar]);
  if (erro && apps === null) return <LoadErrorState what="os aplicativos" error={erro} onRetry={() => void carregar()} />;
  if (apps === null) {
    return <LoadingRegion label="Carregando aplicativos…"><Skeleton height={160} /></LoadingRegion>;
  }
  if (apps.length === 0) {
    return <EmptyState icon={AppWindow} title="Nenhum aplicativo cadastrado" hint="Cadastre um aplicativo na aba Loja, com o botão Novo aplicativo.">Sem apps.</EmptyState>;
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
              {a.last_run_at ? `última execução ${tempoRelativo(a.last_run_at, now)}` : 'nenhuma execução nos últimos 7 dias'}
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
  const [erro, setErro] = useState<LoadError | null>(null);
  const now = useNow();
  const selectRun = useUiStore((s) => s.selectRun);
  const setView = useUiStore((s) => s.setView);
  const carregar = useCallback(async () => {
    setErro(null);
    try {
      setD(await api.appOverview(appId, 30));
    } catch (e) {
      // Antes o erro ia só para um toast e o esqueleto ficava para sempre (P2.11).
      setErro(toLoadError(e));
    }
  }, [appId]);
  useEffect(() => {
    setD(null);
    void carregar();
  }, [carregar]);
  if (!d) {
    return erro
      ? <LoadErrorState what="o aplicativo" error={erro} onRetry={() => void carregar()} />
      : <LoadingRegion label="Carregando o aplicativo…"><Skeleton height={220} /></LoadingRegion>;
  }

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
                      <span className={styles.muted}>{tempoRelativo(r.created_at, now)}</span>
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
                      <span className={styles.muted}>{f.finished_at ? tempoRelativo(f.finished_at, now) : ''}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Contas" subtitle="Personas que têm conta neste app." />
          <CardBody>
            {d.accounts.length === 0 ? <p className={styles.muted}>Nenhuma persona tem conta neste app. Adicione em Personas → a pessoa → Contas e acesso.</p> : (
              <ul className={styles.simpleList}>
                {d.accounts.map((c) => (
                  <li key={c.id}>
                    <KeyRound size={12} aria-hidden /> <strong>@{c.username}</strong>
                    <span className={styles.muted}>{c.handle}</span>
                    <StatusBadge meta={metaOf(ACCOUNT_SESSION_STATUS, c.session_status)} size="sm" />
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
                    <StatusBadge meta={metaOf(APP_INSTALL_STATE, x.state)} size="sm" />
                    {x.drift_kind ? <StatusBadge meta={metaOf(DRIFT_KIND, x.drift_kind)} size="sm" /> : null}
                    <span className={styles.muted}>{x.observed_version_name ?? ''}</span>
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
                  <li key={f.id}>
                    <strong>{f.name}</strong>
                    <StatusBadge meta={metaOf(FLOW_STATUS, f.status)} size="sm" />
                    <span className={styles.muted}>{f.uses}×</span>
                  </li>
                ))}
              </ul>
            )}
            <h4 className={styles.subTitle}>Receitas ({d.recipes.length})</h4>
            {d.recipes.length === 0 ? <p className={styles.muted}>Nenhuma receita aprendida.</p> : (
              <ul className={styles.simpleList}>
                {d.recipes.map((r) => (
                  <li key={r.id}>
                    <strong>{r.step_key ?? `receita ${r.id}`}</strong>
                    <StatusBadge meta={metaOf(RECIPE_STATUS, r.status)} size="sm" />
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
