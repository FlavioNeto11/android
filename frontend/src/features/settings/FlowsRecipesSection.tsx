import { Flag, GraduationCap, RefreshCw, ScrollText, ServerCrash, ShieldAlert, ShieldCheck, Trash2, Workflow } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { Flow, FlowCoverage, Recipe, SkillState, SkillSummary } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { Switch } from '../../components/Switch';
import { flowsLabel, recipesModeLabel } from '../../lib/aiLabels';
import { formatInt, plural } from '../../lib/format';
import { jumpTo, useSectionOpen } from '../../lib/sections';
import { metaOf } from '../../lib/status';
import { formatDateTime } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import { LEARN_ONCE_NOTE, RECIPE_STATUS, recipeToggleTarget, scrollText, selectorText, shadowText, splitTemplate } from './flowsRecipes';
import styles from './Settings.module.css';

interface LoadError {
  message: string;
  hint: string;
}

interface ListState<T> {
  items: T[] | null;
  error: LoadError | null;
  loading: boolean;
}

const INITIAL = { items: null, error: null, loading: true } as const;

function toLoadError(e: unknown): LoadError {
  const err = toApiError(e);
  return { message: err.message, hint: hintForError(err) };
}

export function FlowsRecipesSection() {
  const features = useAppStore((s) => s.health?.features ?? null);
  // Recarrega a cada novo snapshot (reconexão): as listas não vêm no snapshot nem em eventos.
  const hydrateCount = useAppStore((s) => s.hydrateCount);
  const [flows, setFlows] = useState<ListState<Flow>>(INITIAL);
  const [recipes, setRecipes] = useState<ListState<Recipe>>(INITIAL);
  // Cobertura por fluxo (item "caminhos mapeados"): quantas etapas do plano-modelo já têm receita ativa para a
  // versão promovida do app. Vem de `/api/flows/cobertura`; falhar aqui só esconde a coluna, nunca a lista.
  const [cobertura, setCobertura] = useState<Map<string, FlowCoverage>>(new Map());
  const token = useRef(0);
  // Fase F: habilidades versionadas só com `features.skills`. Desligado, nenhuma requisição a mais e nada novo na tela.
  const skillsOn = features?.skills === true;
  const [skills, setSkills] = useState<ListState<SkillSummary>>(INITIAL);

  const loadSkills = useCallback(async () => {
    setSkills((s) => ({ ...s, loading: true, error: null }));
    try {
      const lista = await api.listSkills();
      setSkills({ items: Array.isArray(lista) ? lista : [], error: null, loading: false });
    } catch (e) {
      setSkills((s) => ({ items: s.items, error: toLoadError(e), loading: false }));
    }
  }, []);

  useEffect(() => {
    if (skillsOn) void loadSkills();
  }, [skillsOn, loadSkills, hydrateCount]);

  const load = useCallback(async () => {
    const my = ++token.current;
    setFlows((s) => ({ ...s, loading: true, error: null }));
    setRecipes((s) => ({ ...s, loading: true, error: null }));
    // As duas listas são independentes: a falha de uma não esconde a outra.
    const [f, r, c] = await Promise.allSettled([api.listFlows(), api.listRecipes(), api.flowsCoverage()]);
    if (my !== token.current) return;
    if (c.status === 'fulfilled' && Array.isArray(c.value)) setCobertura(new Map(c.value.map((x) => [x.flow_id, x])));
    setFlows((s) => (f.status === 'fulfilled'
      ? { items: Array.isArray(f.value) ? f.value : [], error: null, loading: false }
      : { items: s.items, error: toLoadError(f.reason), loading: false }));
    setRecipes((s) => (r.status === 'fulfilled'
      ? { items: Array.isArray(r.value) ? r.value : [], error: null, loading: false }
      : { items: s.items, error: toLoadError(r.reason), loading: false }));
  }, []);

  useEffect(() => {
    void load();
    return () => {
      token.current += 1;
    };
  }, [load, hydrateCount]);

  // Item 11.5: sumário com âncoras + seções recolhíveis, como no Diagnóstico — aqui as duas listas podem crescer
  // bastante (um fluxo/receita por comando aprendido), e cada uma some de vista mais rápido com a outra fechada.
  // Abertas por padrão: é o comportamento de hoje, só ganha o controle de recolher.
  const [openFlows, setOpenFlows] = useSectionOpen('settings.section.fluxos', true);
  const [openRecipes, setOpenRecipes] = useSectionOpen('settings.section.receitas', true);

  return (
    <>
      <div className={styles.sectionIntro}>
        <p className={styles.sectionLead}>
          O que a IA já aprendeu. Um <strong>fluxo</strong> guarda o plano de um comando comprovado; uma <strong>receita</strong> guarda as ações de uma etapa, por seletores. {LEARN_ONCE_NOTE}
        </p>
        <Button size="sm" variant="ghost" icon={RefreshCw} loading={flows.loading || recipes.loading} onClick={() => void load()}>Atualizar</Button>
      </div>

      {features ? (
        <p className={styles.fieldsetHint}>
          No backend agora — fluxos: <strong>{flowsLabel(features.flows).toLowerCase()}</strong> · receitas: <strong>{recipesModeLabel(features.recipes).toLowerCase()}</strong>. Isso é definido no arquivo de configuração do backend.
        </p>
      ) : null}

      <nav className={styles.anchorNav} aria-label="Ir para">
        <button type="button" className={styles.anchorLink} onClick={() => jumpTo('settings-fluxos')}>Fluxos{flows.items ? ` (${flows.items.length})` : ''}</button>
        <button type="button" className={styles.anchorLink} onClick={() => jumpTo('settings-receitas')}>Receitas{recipes.items ? ` (${recipes.items.length})` : ''}</button>
        {skillsOn ? (
          <button type="button" className={styles.anchorLink} onClick={() => jumpTo('settings-habilidades')}>Habilidades{skills.items ? ` (${skills.items.length})` : ''}</button>
        ) : null}
      </nav>

      <Disclosure
        id="settings-fluxos"
        className={styles.learnBlock}
        summary={<span className={styles.learnTitle}><Workflow size={15} aria-hidden /> Fluxos</span>}
        defaultOpen={openFlows}
        onToggle={setOpenFlows}
      >
        {() => (
          <FlowList state={flows} cobertura={cobertura} onRetry={() => void load()} onChange={(update) => setFlows((s) => ({ ...s, items: s.items ? update(s.items) : s.items }))} />
        )}
      </Disclosure>

      <Disclosure
        id="settings-receitas"
        className={styles.learnBlock}
        summary={<span className={styles.learnTitle}><ScrollText size={15} aria-hidden /> Receitas</span>}
        defaultOpen={openRecipes}
        onToggle={setOpenRecipes}
      >
        {() => (
          <RecipeList state={recipes} onRetry={() => void load()} onChange={(update) => setRecipes((s) => ({ ...s, items: s.items ? update(s.items) : s.items }))} />
        )}
      </Disclosure>

      {skillsOn ? (
        <Disclosure
          id="settings-habilidades"
          className={styles.learnBlock}
          summary={<span className={styles.learnTitle}><GraduationCap size={15} aria-hidden /> Habilidades</span>}
          defaultOpen
        >
          {() => <SkillList state={skills} onRetry={() => void loadSkills()} />}
        </Disclosure>
      ) : null}
    </>
  );
}

// ---- habilidades (fase F) -----------------------------------------------------------------------

const SKILL_STATE: Record<SkillState, { label: string; tone: 'neutral' | 'info' | 'accent' | 'success' | 'muted' | 'danger' }> = {
  draft: { label: 'rascunho', tone: 'neutral' },
  candidate: { label: 'candidata', tone: 'info' },
  validated: { label: 'validada', tone: 'accent' },
  published: { label: 'publicada', tone: 'success' },
  deprecated: { label: 'substituída', tone: 'muted' },
  disabled: { label: 'desabilitada', tone: 'danger' },
};

function SkillList({ state, onRetry }: { state: ListState<SkillSummary>; onRetry: () => void }) {
  const items = state.items;
  if (!items) {
    return state.error ? <ListError what="as habilidades" error={state.error} onRetry={onRetry} /> : <ListLoading label="Carregando as habilidades…" />;
  }
  return (
    <>
      {state.error ? <StaleBanner error={state.error} /> : null}
      {items.length === 0 ? (
        <EmptyState icon={GraduationCap} compact title="Nenhuma habilidade versionada ainda">
          Ensine uma no Foco: grave o treinamento e, na revisão, gere a candidata de habilidade.
        </EmptyState>
      ) : (
        <ul className={styles.learnList} aria-label="Habilidades">
          {items.map((sk) => {
            const meta = SKILL_STATE[sk.state] ?? { label: sk.state, tone: 'neutral' as const };
            return (
              <li key={sk.ref} className={styles.learnItem}>
                <div className={styles.learnHead}>
                  <span className={`${styles.appName} truncate`} title={sk.name}>{sk.name}</span>
                  <span className={styles.learnSpacer} />
                  <Badge tone={meta.tone}>{meta.label}</Badge>
                  {!sk.intact ? <Badge tone="danger">conteúdo alterado</Badge> : null}
                </div>
                {sk.command_template ? (
                  <p className={styles.template} aria-label="Comando-modelo">
                    {splitTemplate(sk.command_template).map((part, i) =>
                      part.placeholder ? <mark key={i} className={styles.placeholder}>{part.text}</mark> : <span key={i}>{part.text}</span>,
                    )}
                  </p>
                ) : null}
                <span className={styles.appMeta}>
                  <span className="mono">{sk.ref}</span>{sk.app_id ? ` · app: ${sk.app_id}` : ''} · desde {formatDateTime(sk.state_at)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

// ---- estados comuns -----------------------------------------------------------------------------

function ListLoading({ label }: { label: string }) {
  return (
    <LoadingRegion label={label}>
      <Skeleton height={64} radius={8} />
      <Skeleton height={64} radius={8} style={{ marginTop: 8 }} />
    </LoadingRegion>
  );
}

function ListError({ what, error, onRetry }: { what: string; error: LoadError; onRetry: () => void }) {
  return (
    <EmptyState icon={ServerCrash} tone="danger" compact title={`Não foi possível carregar ${what}`} hint={error.hint} actions={<Button variant="outline" icon={RefreshCw} onClick={onRetry}>Tentar de novo</Button>}>
      {error.message}
    </EmptyState>
  );
}

function StaleBanner({ error }: { error: LoadError }) {
  return <Banner tone="warning" icon={ServerCrash} compact title="Mostrando a última lista carregada">{error.message} {error.hint}</Banner>;
}

// ---- fluxos -------------------------------------------------------------------------------------

interface ListProps<T> {
  state: ListState<T>;
  onRetry: () => void;
  /** Atualização funcional: aplicada sobre a lista ATUAL (outra requisição pode ter terminado no meio-tempo). */
  onChange: (update: (items: T[]) => T[]) => void;
}

const CUSTO_IA: Record<string, { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' }> = {
  zero: { label: 'roda sem IA', tone: 'success' },
  parcial: { label: 'parte por receita', tone: 'warning' },
  total: { label: 'a IA faz tudo', tone: 'danger' },
};

function FlowList({ state, onRetry, onChange, cobertura }: ListProps<Flow> & { cobertura?: Map<string, FlowCoverage> }) {
  const apps = useAppStore((s) => s.apps);
  const appNames = useMemo(() => new Map(apps.map((a) => [a.id, a.name])), [apps]);
  const [busy, setBusy] = useState<Record<string, 'toggle' | 'delete' | undefined>>({});
  const items = state.items;

  if (!items) {
    return state.error ? <ListError what="os fluxos" error={state.error} onRetry={onRetry} /> : <ListLoading label="Carregando os fluxos…" />;
  }

  const setStatus = async (flow: Flow, active: boolean) => {
    if (busy[flow.id]) return;
    setBusy((b) => ({ ...b, [flow.id]: 'toggle' }));
    try {
      const saved = await api.updateFlow(flow.id, { status: active ? 'active' : 'disabled' });
      onChange((list) => list.map((f) => (f.id === flow.id ? { ...f, ...saved } : f)));
      toast({ tone: 'success', title: active ? `Fluxo “${flow.name}” ativado` : `Fluxo “${flow.name}” desativado`, message: active ? undefined : 'Comandos parecidos voltam a ser planejados pela IA.' });
    } catch (e) {
      toastError(`Não foi possível ${active ? 'ativar' : 'desativar'} “${flow.name}”`, e);
    } finally {
      setBusy((b) => ({ ...b, [flow.id]: undefined }));
    }
  };

  const remove = async (flow: Flow) => {
    if (busy[flow.id]) return;
    const { confirmed } = await confirm({
      title: `Excluir o fluxo “${flow.name}”?`,
      danger: true,
      confirmLabel: 'Excluir fluxo',
      cancelLabel: 'Cancelar',
      body: `O plano guardado será perdido${flow.uses > 0 ? ` (já foi reaproveitado ${plural(flow.uses, 'vez', 'vezes')})` : ''}. Na próxima vez que este comando aparecer, a IA planeja do zero. As receitas das etapas não são afetadas.`,
    });
    if (!confirmed) return;
    setBusy((b) => ({ ...b, [flow.id]: 'delete' }));
    try {
      await api.deleteFlow(flow.id);
      onChange((list) => list.filter((f) => f.id !== flow.id));
      toast({ tone: 'success', title: `Fluxo “${flow.name}” excluído` });
    } catch (e) {
      toastError(`Não foi possível excluir “${flow.name}”`, e);
    } finally {
      setBusy((b) => ({ ...b, [flow.id]: undefined }));
    }
  };

  return (
    <>
      {state.error ? <StaleBanner error={state.error} /> : null}
      {items.length === 0 ? (
        <EmptyState icon={Workflow} compact title="Nenhum fluxo salvo ainda" hint={LEARN_ONCE_NOTE}>
          Quando uma execução termina com tudo comprovado, o comando vira um modelo reaproveitável e aparece aqui.
        </EmptyState>
      ) : (
        <ul className={styles.learnList}>
          {items.map((flow) => (
            <li key={flow.id} className={styles.learnItem}>
              <div className={styles.learnHead}>
                <span className={`${styles.appName} truncate`} title={flow.name}>{flow.name}</span>
                <span className={styles.learnSpacer} />
                <Switch
                  checked={flow.status === 'active'}
                  label={`Fluxo “${flow.name}” ativo`}
                  busy={busy[flow.id] === 'toggle'}
                  disabled={busy[flow.id] === 'delete'}
                  onChange={(next) => void setStatus(flow, next)}
                />
                <Button size="sm" variant="dangerGhost" icon={Trash2} loading={busy[flow.id] === 'delete'} disabled={busy[flow.id] === 'toggle'} aria-label={`Excluir o fluxo ${flow.name}`} onClick={() => void remove(flow)}>
                  Excluir
                </Button>
              </div>
              <p className={styles.template} aria-label="Comando-modelo">
                {splitTemplate(flow.command_template).map((part, i) =>
                  part.placeholder ? <mark key={i} className={styles.placeholder}>{part.text}</mark> : <span key={i}>{part.text}</span>,
                )}
              </p>
              {(() => {
                const c = cobertura?.get(flow.id);
                if (!c || c.steps_total === 0) return null;
                const custo = CUSTO_IA[c.ai_cost] ?? { label: 'cobertura desconhecida', tone: 'neutral' as const };
                return (
                  <p className={styles.appMeta} aria-label="Cobertura de receitas">
                    Cobertura: {c.steps_with_recipe} de {c.steps_total} etapas com receita
                    {c.target_version ? ` (versão ${c.target_version})` : ''} · <Badge tone={custo.tone}>{custo.label}</Badge>
                  </p>
                );
              })()}
              <span className={styles.appMeta}>
                {plural(flow.uses, 'uso', 'usos')} · último uso: {flow.last_used_at ? formatDateTime(flow.last_used_at) : 'nunca'}
                {flow.app_id ? ` · app: ${appNames.get(flow.app_id) ?? flow.app_id}` : ''}
                {' '}· criado em {formatDateTime(flow.created_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

// ---- receitas -----------------------------------------------------------------------------------

function RecipeList({ state, onRetry, onChange }: ListProps<Recipe>) {
  const apps = useAppStore((s) => s.apps);
  const appByPackage = useMemo(() => new Map(apps.map((a) => [a.package, a.name])), [apps]);
  const [busy, setBusy] = useState<Record<number, 'toggle' | 'delete' | undefined>>({});
  const items = state.items;

  if (!items) {
    return state.error ? <ListError what="as receitas" error={state.error} onRetry={onRetry} /> : <ListLoading label="Carregando as receitas…" />;
  }

  const nameOf = (r: Recipe) => `${r.step_key} v${r.version}`;

  const toggle = async (recipe: Recipe) => {
    const target = recipeToggleTarget(recipe.status);
    if (!target || busy[recipe.id]) return;
    setBusy((b) => ({ ...b, [recipe.id]: 'toggle' }));
    try {
      // A resposta é só {id,status}: mescla na receita que já temos.
      const res = await api.updateRecipe(recipe.id, { status: target });
      onChange((list) => list.map((r) => (r.id === recipe.id ? { ...r, status: res?.status ?? target } : r)));
      toast({
        tone: 'success',
        title: target === 'active' ? `Receita ${nameOf(recipe)} reativada` : `Receita ${nameOf(recipe)} em quarentena`,
        message: target === 'active' ? 'Volta a ser usada na próxima execução desta etapa.' : 'A IA conduz esta etapa até você reativar a receita.',
      });
    } catch (e) {
      toastError(`Não foi possível alterar a receita ${nameOf(recipe)}`, e);
    } finally {
      setBusy((b) => ({ ...b, [recipe.id]: undefined }));
    }
  };

  const remove = async (recipe: Recipe) => {
    if (busy[recipe.id]) return;
    const { confirmed } = await confirm({
      title: `Excluir a receita ${nameOf(recipe)}?`,
      danger: true,
      confirmLabel: 'Excluir receita',
      cancelLabel: 'Cancelar',
      body: 'As ações aprendidas para esta etapa serão perdidas. Na próxima execução a IA conduz a etapa e pode aprender uma receita nova.',
    });
    if (!confirmed) return;
    setBusy((b) => ({ ...b, [recipe.id]: 'delete' }));
    try {
      await api.deleteRecipe(recipe.id);
      onChange((list) => list.filter((r) => r.id !== recipe.id));
      toast({ tone: 'success', title: `Receita ${nameOf(recipe)} excluída` });
    } catch (e) {
      toastError(`Não foi possível excluir a receita ${nameOf(recipe)}`, e);
    } finally {
      setBusy((b) => ({ ...b, [recipe.id]: undefined }));
    }
  };

  return (
    <>
      {state.error ? <StaleBanner error={state.error} /> : null}
      {items.length === 0 ? (
        <EmptyState icon={ScrollText} compact title="Nenhuma receita aprendida ainda" hint={LEARN_ONCE_NOTE}>
          Cada etapa que a IA conclui e comprova pode virar uma receita: a lista de toques e textos, por seletores.
        </EmptyState>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className="sr-only">Receitas aprendidas</caption>
            <thead>
              <tr>
                <th scope="col">App e versão</th>
                <th scope="col">Etapa</th>
                <th scope="col">Versão</th>
                <th scope="col">Status</th>
                <th scope="col">Acertos / falhas</th>
                <th scope="col">Modo sombra</th>
                <th scope="col">Ações</th>
                <th scope="col"><span className="sr-only">Comandos</span></th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => {
                const target = recipeToggleTarget(r.status);
                const shadow = shadowText(r);
                const appName = appByPackage.get(r.app_package);
                return (
                  <tr key={r.id}>
                    <td className={styles.recipeAppCell}>
                      {appName ? <span>{appName}</span> : null}
                      <span className={styles.appPkg} style={{ display: 'block' }}>{r.app_package}</span>
                      <span className={styles.cellObservedTs}>versão do app: {r.app_version || '—'}</span>
                    </td>
                    <td className={styles.cellId}>{r.step_key}</td>
                    <td>v{r.version}</td>
                    <td><StatusBadge meta={metaOf(RECIPE_STATUS, r.status)} size="sm" /></td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <span className="sr-only">Acertos: </span>{formatInt(r.replay_ok)} / <span className="sr-only">falhas: </span>{formatInt(r.replay_fail)}
                      {r.consecutive_fail > 0 ? <span className={styles.cellObservedTs}>{plural(r.consecutive_fail, 'falha seguida', 'falhas seguidas')}</span> : null}
                      <span className={styles.cellObservedTs}>último uso: {r.last_used_at ? formatDateTime(r.last_used_at) : 'nunca'}</span>
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      {shadow ? <span title="Vezes em que a receita escolheu a mesma ação que a IA / comparações feitas">{shadow}</span> : <span style={{ color: 'var(--text-3)' }}>—</span>}
                    </td>
                    <td className={styles.recipeActionsCell}>
                      <RecipeActions recipe={r} />
                    </td>
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                      <div className={styles.rowButtons}>
                        <Button
                          size="sm"
                          icon={target === 'quarantined' ? ShieldAlert : ShieldCheck}
                          loading={busy[r.id] === 'toggle'}
                          disabled={busy[r.id] === 'delete'}
                          disabledReason={target ? null : 'Receita substituída por uma versão mais nova: não pode ser reativada.'}
                          aria-label={target ? `${target === 'active' ? 'Reativar' : 'Pôr em quarentena'} a receita ${nameOf(r)}` : undefined}
                          onClick={() => void toggle(r)}
                        >
                          {target === 'quarantined' ? 'Pôr em quarentena' : 'Reativar'}
                        </Button>
                        <Button size="sm" variant="dangerGhost" icon={Trash2} loading={busy[r.id] === 'delete'} disabled={busy[r.id] === 'toggle'} aria-label={`Excluir a receita ${nameOf(r)}`} onClick={() => void remove(r)}>
                          Excluir
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function RecipeActions({ recipe }: { recipe: Recipe }) {
  const actions = Array.isArray(recipe.actions) ? recipe.actions : [];
  if (actions.length === 0) return <span style={{ color: 'var(--text-3)' }}>Sem ações</span>;
  return (
    <Disclosure bare summary={plural(actions.length, 'ação', 'ações')}>
      {() => (
        <ol className={styles.recipeSteps}>
          {actions.map((a, i) => {
            const scroll = scrollText(a.scroll);
            return (
              <li key={i} className={styles.recipeStep}>
                <div className={styles.recipeStepHead}>
                  <span className={styles.recipeTool}>{a.tool}</span>
                  {a.commit ? (
                    <Badge tone="warning" icon={Flag} size="sm" title="Ação de confirmação: é ela que efetiva a etapa (ex.: tocar em Enviar). Não é repetida às cegas.">
                      confirma a etapa
                    </Badge>
                  ) : null}
                </div>
                {a.why ? <p className={styles.recipeWhy}>{a.why}</p> : null}
                {a.selectors && a.selectors.length > 0 ? (
                  <ul className={styles.recipeSelectors} aria-label="Seletores, na ordem de tentativa">
                    {a.selectors.map((sel, j) => <li key={j}>{selectorText(sel)}</li>)}
                  </ul>
                ) : (
                  <p className={styles.recipeWhy}>Sem seletor (ação sem alvo na tela).</p>
                )}
                {scroll ? <p className={styles.recipeWhy}>{scroll}</p> : null}
              </li>
            );
          })}
        </ol>
      )}
    </Disclosure>
  );
}
