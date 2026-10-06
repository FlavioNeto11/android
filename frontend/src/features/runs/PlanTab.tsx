import { Check, History, ListTree, TriangleAlert } from 'lucide-react';
import { useMemo } from 'react';
import type { AppConfig, PlanStep, RunDetail } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { KvList, KvRow } from '../../components/JsonTree';
import { PacotesAceitos } from '../../components/PacotesAceitos';
import ui from '../../components/ui.module.css';
import { cx } from '../../lib/format';
import { POSTCONDITION_KIND } from '../../lib/status';
import { formatDateTime } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { appLabel } from './model';
import { NOME_DA_IA } from '../../lib/identidade';
import styles from './Runs.module.css';

export const SIDE_EFFECT_LABEL = 'efeito externo — sem repetição automática';

export function SideEffectFlag() {
  return (
    <span className={styles.sideFlag} title="Esta etapa altera algo fora do emulador (ex.: envia uma mensagem). Se falhar no meio, o sistema NÃO repete sozinho.">
      <TriangleAlert size={11} aria-hidden /> {SIDE_EFFECT_LABEL}
    </span>
  );
}

/**
 * As etapas de um plano. `planAppId` é o app do PLANO (`Plan.app_id`): quando uma etapa não declara o seu
 * (`s.app_id` nulo/ausente), ela roda nele. `apps` resolve o id pelo nome do catálogo (item 24.6) — sem o
 * catálogo (ainda carregando), mostra o próprio id, nunca inventa nome.
 */
export function PlanStepList({ steps, planAppId = null, apps = [] }: {
  steps: PlanStep[];
  planAppId?: string | null;
  apps?: readonly Pick<AppConfig, 'id' | 'name'>[];
}) {
  const titles = useMemo(() => new Map(steps.map((s) => [s.key, s.title])), [steps]);
  return (
    <ol className={styles.planSteps}>
      {steps.map((s, i) => {
        // Etapa em app diferente do plano (comando entre apps, ADR-058): sinal visível na lista, não só no detalhe.
        const outroApp = s.app_id && s.app_id !== planAppId ? appLabel(apps, s.app_id) : null;
        const appDaEtapa = appLabel(apps, s.app_id ?? planAppId);
        return (
          <li key={s.key} className={cx(styles.planStep, s.side_effect && styles.planStepSide)}>
            <span className={styles.stepNum} aria-hidden>{i + 1}</span>
            <div>
              <p className={styles.planStepTitle}><span className="sr-only">Etapa {i + 1}: </span>{s.title}</p>
              <p className={styles.planStepGoal}>{s.goal}</p>
              <PacotesAceitos pacotes={s.pacotes_aceitos} />
              {s.depends_on.length > 0 || s.side_effect || outroApp ? (
                <div className={styles.chips}>
                  {s.depends_on.length > 0 ? <span className={styles.muted}>depende de:</span> : null}
                  {s.depends_on.map((k) => (
                    <span key={k} className={cx(ui.chip, ui.chipStatic)} title={k}>{titles.get(k) ?? k}</span>
                  ))}
                  {outroApp ? (
                    <Badge size="sm" tone="info" title="Esta etapa roda em outro app, não no app do plano.">
                      app: {outroApp}
                    </Badge>
                  ) : null}
                  {s.side_effect ? <SideEffectFlag /> : null}
                </div>
              ) : null}
              <Disclosure bare summary="Detalhes técnicos" className={styles.techWrap}>
                <KvList>
                  <KvRow label="Chave"><span className="mono">{s.key}</span></KvRow>
                  <KvRow label="App">{appDaEtapa ?? '—'}</KvRow>
                  <KvRow label="Pré-condição">{s.precondition ?? '—'}</KvRow>
                  <KvRow label="Verificação de sucesso">
                    {POSTCONDITION_KIND[s.postcondition?.kind] ?? s.postcondition?.kind ?? '—'}
                    {s.postcondition?.value ? <> · <span className="mono">{s.postcondition.value}</span></> : null}
                    {s.postcondition?.description ? <><br />{s.postcondition.description}</> : null}
                  </KvRow>
                  <KvRow label="Tempo limite">{s.timeout_s} s</KvRow>
                  <KvRow label="Tentativas máximas">{s.max_attempts}</KvRow>
                </KvList>
              </Disclosure>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function PlanTab({ detail }: { detail: RunDetail }) {
  const plan = detail.plan;
  const apps = useAppStore((s) => s.apps);
  const versionsByObjective = useMemo(() => {
    const instanceOf = new Map(detail.objectives.map((o) => [o.id, o.instance_id]));
    const groups = new Map<string, { instanceId: string; versions: RunDetail['plan_versions'] }>();
    for (const v of detail.plan_versions) {
      const g = groups.get(v.objective_id) ?? { instanceId: instanceOf.get(v.objective_id) ?? v.objective_id, versions: [] };
      g.versions.push(v);
      groups.set(v.objective_id, g);
    }
    return Array.from(groups.values())
      .map((g) => ({ ...g, versions: g.versions.slice().sort((a, b) => b.version - a.version) }))
      .sort((a, b) => a.instanceId.localeCompare(b.instanceId));
  }, [detail.plan_versions, detail.objectives]);

  if (!plan) {
    return (
      <EmptyState
        icon={ListTree}
        compact
        title={detail.status === 'planning' ? `${NOME_DA_IA} está montando o plano…` : 'Esta execução não tem plano'}
        hint={detail.status === 'planning' ? 'Isto costuma levar alguns segundos. O plano aparece aqui automaticamente.' : 'Veja a Linha do tempo para entender o que aconteceu.'}
      />
    );
  }

  const params = Object.entries(plan.parameters ?? {});
  const revised = versionsByObjective.filter((g) => g.versions.length > 1 || g.versions.some((v) => v.version > 1));

  return (
    <div className={styles.stack}>
      <section>
        <h3 className={styles.subTitle}>Resumo</h3>
        <p className={styles.lead}>{plan.summary}</p>
        <p className={styles.muted} style={{ marginTop: 6 }}>
          Planejado por {plan.planner?.provider ?? '—'}{plan.planner?.model ? ` · ${plan.planner.model}` : ''}
          {plan.planner?.simulated ? ' · simulado' : ''}
          {plan.app_package ? <> · app <span className="mono" title={`Identificador: ${plan.app_package}`}>{appLabel(apps, plan.app_id) ?? plan.app_package}</span></> : null}
          {/* ADR-058 (T18): comando que atravessa apps — a lista de etapas abaixo mostra qual roda em qual. */}
          {plan.required_apps && plan.required_apps.length > 1 ? (
            <> · apps exigidos: {plan.required_apps.map((id) => appLabel(apps, id) ?? id).join(', ')}</>
          ) : null}
        </p>
      </section>

      <div className={styles.twoCol}>
        <section>
          <h3 className={styles.subTitle}>Parâmetros</h3>
          {params.length > 0 ? (
            <KvList>
              {params.map(([k, v]) => (
                <KvRow key={k} label={k}><span className="mono">{v}</span></KvRow>
              ))}
            </KvList>
          ) : (
            <p className={styles.muted}>O comando não tem parâmetros variáveis.</p>
          )}
        </section>
        <section>
          <h3 className={styles.subTitle}>Critérios de sucesso</h3>
          {plan.success_criteria?.length > 0 ? (
            <ul className={styles.criteria}>
              {plan.success_criteria.map((c, i) => (
                <li key={i}><Check size={14} aria-hidden /> {c}</li>
              ))}
            </ul>
          ) : (
            <p className={styles.muted}>Nenhum critério explícito foi definido.</p>
          )}
        </section>
      </div>

      <section>
        <h3 className={styles.subTitle}>Etapas ({plan.steps?.length ?? 0})</h3>
        {plan.steps?.length > 0
          ? <PlanStepList steps={plan.steps} planAppId={plan.app_id} apps={apps} />
          : <p className={styles.muted}>O plano ainda não tem etapas.</p>}
      </section>

      <section>
        <h3 className={styles.subTitle}>Histórico de versões do plano</h3>
        {revised.length === 0 ? (
          <p className={styles.muted}>Nenhuma instância precisou revisar o plano até agora.</p>
        ) : (
          <div className={styles.objList}>
            {revised.map((g) => (
              <Disclosure
                key={g.instanceId}
                summary={<span className="mono">{g.instanceId}</span>}
                meta={`${g.versions.length} ${g.versions.length === 1 ? 'versão' : 'versões'}`}
              >
                <div className={styles.objList}>
                  {g.versions.map((v) => (
                    <div key={v.version} className={styles.versionItem}>
                      <div className={styles.versionHead}>
                        <Badge tone="info" icon={History} size="sm">v{v.version}</Badge>
                        <span className={styles.muted}>{formatDateTime(v.created_at)}</span>
                      </div>
                      <p className={styles.versionReason}><span className={styles.muted}>Motivo: </span>{v.reason || '—'}</p>
                      <ol className={styles.versionSteps}>
                        {v.steps.map((s) => (
                          <li key={s.key}>{s.title}{s.side_effect ? ' — efeito externo' : ''}</li>
                        ))}
                      </ol>
                    </div>
                  ))}
                </div>
              </Disclosure>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
