import {
  BadgeCheck, Ban, CircleCheck, FilePen, FileSearch, Flag, GraduationCap, History, RefreshCw, ScrollText, ServerCrash,
  ShieldAlert, ShieldCheck, Trash2, TriangleAlert, Undo2, Workflow,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { api, hintForError, toApiError, type ApiError } from '../../api/client';
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
import { metaOf, type StatusMeta } from '../../lib/status';
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

/** A recusa do domínio como a API a manda (`{code, message}` e, conforme o caso, `errors` ou `pending`). */
interface Refusal {
  code: string;
  message: string;
  details: string[];
}

function toRefusal(e: unknown): Refusal {
  const err: ApiError = toApiError(e);
  const lista = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
  return { code: err.code, message: err.message, details: [...lista(err.detail?.errors), ...lista(err.detail?.pending)] };
}

function RefusalBanner({ refusal }: { refusal: Refusal }) {
  return (
    <Banner tone="danger" icon={ShieldAlert} compact role="alert" title={<span className="mono">{refusal.code}</span>}>
      {refusal.message}
      {refusal.details.length > 0 ? (
        <ul className={styles.refusalList}>
          {refusal.details.map((d, i) => <li key={i} className="mono">{d}</li>)}
        </ul>
      ) : null}
    </Banner>
  );
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

  // Fase J: que fluxo virou qual habilidade (`legacy_flow_id` na lista de versões). Com a conversão ou uma
  // transição, as duas listas mudam juntas (a conversão desliga o fluxo; desfazer o religa).
  const adotantes = useMemo(() => {
    const porFluxo = new Map<string, SkillSummary[]>();
    for (const sk of skills.items ?? []) {
      if (sk.legacy_flow_id) porFluxo.set(sk.legacy_flow_id, [...(porFluxo.get(sk.legacy_flow_id) ?? []), sk]);
    }
    return porFluxo;
  }, [skills.items]);
  const recarregar = useCallback(() => {
    void load();
    if (skillsOn) void loadSkills();
  }, [load, loadSkills, skillsOn]);

  // Item 11.5: sumário com âncoras + seções recolhíveis, como no Diagnóstico — aqui as duas listas podem crescer
  // bastante (um fluxo/receita por comando aprendido), e cada uma some de vista mais rápido com a outra fechada.
  // Abertas por padrão: é o comportamento de hoje, só ganha o controle de recolher.
  const [openFlows, setOpenFlows] = useSectionOpen('settings.section.fluxos', true);
  const [openRecipes, setOpenRecipes] = useSectionOpen('settings.section.receitas', true);
  const [openSkills, setOpenSkills] = useSectionOpen('settings.section.habilidades', true);
  // A contagem é de habilidades (uma por `skill_id`), não de versões: é assim que a lista as mostra.
  const totalSkills = useMemo(() => (skills.items ? agruparPorSkill(skills.items).length : null), [skills.items]);

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
          <button type="button" className={styles.anchorLink} onClick={() => jumpTo('settings-habilidades')}>Habilidades{totalSkills !== null ? ` (${totalSkills})` : ''}</button>
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
          <FlowList
            state={flows}
            cobertura={cobertura}
            conversao={skillsOn ? { adotantes, onMudou: recarregar } : undefined}
            onRetry={() => void load()}
            onChange={(update) => setFlows((s) => ({ ...s, items: s.items ? update(s.items) : s.items }))}
          />
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
          defaultOpen={openSkills}
          onToggle={setOpenSkills}
        >
          {() => <SkillList state={skills} onRetry={() => void loadSkills()} onMudou={recarregar} />}
        </Disclosure>
      ) : null}
    </>
  );
}

// ---- habilidades (fase F) -----------------------------------------------------------------------

/** Estado de uma versão (§10.3): ícone + rótulo + tom, como `RECIPE_STATUS`. O rascunho é neutro aqui e no quadro de
 * ensino (`TeachingPanel.tsx::TEACHING_STATUS.published`), porque "virou rascunho" e "rascunho" são a mesma coisa. */
const SKILL_STATE: Record<SkillState, StatusMeta> = {
  draft: { label: 'rascunho', tone: 'neutral', icon: FilePen, description: 'Conteúdo ainda editável; não casa com comandos.' },
  candidate: { label: 'candidata', tone: 'info', icon: FileSearch, description: 'Conteúdo congelado, à espera da validação.' },
  validated: { label: 'validada', tone: 'accent', icon: BadgeCheck, description: 'Provada (observações ou validação manual do dono); pode ser publicada.' },
  published: { label: 'publicada', tone: 'success', icon: CircleCheck, description: 'É esta versão que resolve o comando-modelo hoje.' },
  deprecated: { label: 'substituída', tone: 'muted', icon: History, description: 'Deixou de ser a publicada; dá para publicá-la de novo (rollback).' },
  disabled: { label: 'desabilitada', tone: 'danger', icon: Ban, description: 'Parada definitiva: não casa com comandos e não volta.' },
};

const HASH_DIVERGENTE = 'O hash do conteúdo desta versão não bate com o registrado quando ela foi salva: o documento mudou fora do ciclo de vida. Não confie nela sem conferir.';

/**
 * Uma habilidade = todas as versões do mesmo `skill_id`, da mais nova para a mais antiga. O que a pessoa decide é
 * sobre a PUBLICADA (a que resolve comandos hoje) e a ÚLTIMA (a que está a caminho); as demais ficam recolhidas.
 */
interface SkillGroup {
  skillId: string;
  name: string;
  appId: string | null;
  publicada: SkillSummary | null;
  ultima: SkillSummary;
  anteriores: SkillSummary[];
  total: number;
}

function agruparPorSkill(items: SkillSummary[]): SkillGroup[] {
  const porSkill = new Map<string, SkillSummary[]>();
  for (const sk of items) porSkill.set(sk.skill_id, [...(porSkill.get(sk.skill_id) ?? []), sk]);
  return [...porSkill.entries()].map(([skillId, versoes]) => {
    const ordenadas = [...versoes].sort((a, b) => b.version - a.version);
    const ultima = ordenadas[0] as SkillSummary;
    const publicada = ordenadas.find((v) => v.state === 'published') ?? null;
    const destaque = new Set([ultima.ref, publicada?.ref]);
    return {
      skillId, name: ultima.name, appId: ultima.app_id ?? publicada?.app_id ?? null, publicada, ultima,
      anteriores: ordenadas.filter((v) => !destaque.has(v.ref)), total: ordenadas.length,
    };
  });
}

/** As transições que a pessoa decide (§10.3, `lifecycle.TRANSITIONS`), com o texto de cada botão e do diálogo. A
 * recusa, quando houver, é do domínio (`code`/`message`) e aparece na linha da versão. */
interface SkillAction {
  to: SkillState;
  label: string;
  title: (sk: SkillSummary) => string;
  body: string;
  danger?: boolean;
  note: string;
}

const DESABILITAR: SkillAction = {
  to: 'disabled', label: 'Desabilitar', danger: true, note: 'Motivo (fica no histórico)',
  title: (sk) => `Desabilitar ${sk.ref}?`,
  body: 'Parada definitiva desta versão: ela deixa de casar com comandos na hora e não volta (desabilitada é terminal). O histórico e as observações ficam.',
};

const ACOES: Record<SkillState, SkillAction[]> = {
  draft: [{
    to: 'candidate', label: 'Submeter', note: 'Observação (opcional)',
    title: (sk) => `Submeter ${sk.ref}?`,
    body: 'O conteúdo congela daqui em diante (mudar vira uma versão nova). Só submete o rascunho que compila sem erro.',
  }],
  candidate: [{
    to: 'validated', label: 'Validar', note: 'Motivo da validação manual (vazio = pelas observações registradas)',
    title: (sk) => `Validar ${sk.ref}?`,
    body: 'Sem motivo, a validação é pelas observações registradas dos casos da habilidade (caso de aparelho só com prova real). Com motivo, é a validação manual do dono (P4): fica registrada na transição, com as pendências.',
  }, DESABILITAR],
  validated: [{
    to: 'published', label: 'Publicar', note: 'Motivo (opcional)',
    title: (sk) => `Publicar ${sk.ref}?`,
    body: 'Com as habilidades ligadas, o comando-modelo passa a resolver para esta versão (a publicada anterior da mesma habilidade é substituída na mesma operação). Recusado se um fluxo ativo ou outra habilidade publicada tiver o mesmo comando.',
  }, DESABILITAR],
  published: [{
    to: 'deprecated', label: 'Recolher', note: 'Motivo (opcional)',
    title: (sk) => `Recolher ${sk.ref}?`,
    body: 'A versão deixa de ser a publicada e fica substituída; dá para publicá-la de novo depois (rollback).',
  }, DESABILITAR],
  deprecated: [{
    to: 'published', label: 'Publicar de novo', note: 'Motivo (opcional)',
    title: (sk) => `Publicar ${sk.ref} de novo?`,
    body: 'Rollback: esta versão volta a ser a publicada, e a publicada atual é substituída na mesma operação. As validações continuam valendo (o conteúdo é o mesmo).',
  }, DESABILITAR],
  disabled: [],
};

function SkillList({ state, onRetry, onMudou }: { state: ListState<SkillSummary>; onRetry: () => void; onMudou: () => void }) {
  const apps = useAppStore((s) => s.apps);
  const appNames = useMemo(() => new Map(apps.map((a) => [a.id, a.name])), [apps]);
  const [busy, setBusy] = useState<Record<string, SkillState | undefined>>({});
  const [recusas, setRecusas] = useState<Record<string, Refusal | undefined>>({});
  const items = state.items;
  const grupos = useMemo(() => agruparPorSkill(items ?? []), [items]);
  if (!items) {
    return state.error ? <ListError what="as habilidades" error={state.error} onRetry={onRetry} /> : <ListLoading label="Carregando as habilidades…" />;
  }

  const mover = async (sk: SkillSummary, acao: SkillAction) => {
    if (busy[sk.ref]) return;
    const { confirmed, note } = await confirm({
      title: acao.title(sk), body: acao.body, confirmLabel: acao.label, cancelLabel: 'Cancelar', danger: acao.danger,
      note: { label: acao.note },
    });
    if (!confirmed) return;
    setBusy((b) => ({ ...b, [sk.ref]: acao.to }));
    setRecusas((r) => ({ ...r, [sk.ref]: undefined }));
    try {
      const manual = acao.to === 'validated' && note.length > 0;
      const salvo = await api.transitionSkill(sk.skill_id, sk.version, { to: acao.to, reason: note, manual });
      toast({ tone: 'success', title: `${salvo.ref}: ${metaOf(SKILL_STATE, salvo.state).label}` });
      onMudou();
    } catch (e) {
      setRecusas((r) => ({ ...r, [sk.ref]: toRefusal(e) }));
    } finally {
      setBusy((b) => ({ ...b, [sk.ref]: undefined }));
    }
  };

  // Uma linha por versão: estado, ações que o domínio permite, comando-modelo e a recusa, quando houver.
  const versao = (sk: SkillSummary, papel: 'publicada' | 'última' | null) => {
    const recusa = recusas[sk.ref];
    return (
      <li key={sk.ref} className={styles.skillVersion}>
        <div className={styles.learnHead}>
          {papel ? <span className={styles.skillRole}>{papel}</span> : null}
          <span className="mono">{sk.ref}</span>
          <StatusBadge meta={metaOf(SKILL_STATE, sk.state)} size="sm" srPrefix="Estado" />
          {!sk.intact ? <Badge tone="danger" size="sm" icon={TriangleAlert} title={HASH_DIVERGENTE}>conteúdo alterado</Badge> : null}
          <span className={styles.learnSpacer} />
          {(ACOES[sk.state] ?? []).map((acao) => (
            <Button
              key={acao.to}
              size="sm"
              variant={acao.danger ? 'dangerGhost' : 'secondary'}
              loading={busy[sk.ref] === acao.to}
              disabledReason={busy[sk.ref] !== undefined && busy[sk.ref] !== acao.to ? 'Aguarde a transição em andamento.' : null}
              aria-label={`${acao.label} ${sk.ref}`}
              onClick={() => void mover(sk, acao)}
            >
              {acao.label}
            </Button>
          ))}
        </div>
        {sk.command_template ? (
          <p className={styles.template} aria-label="Comando-modelo">
            {splitTemplate(sk.command_template).map((part, i) =>
              part.placeholder ? <mark key={i} className={styles.placeholder}>{part.text}</mark> : <span key={i}>{part.text}</span>,
            )}
          </p>
        ) : null}
        <span className={styles.appMeta}>
          {sk.legacy_flow_id ? <>convertida do fluxo <span className="mono">{sk.legacy_flow_id}</span> · </> : null}
          desde {formatDateTime(sk.state_at)}
        </span>
        {recusa ? <RefusalBanner refusal={recusa} /> : null}
      </li>
    );
  };

  return (
    <>
      {state.error ? <StaleBanner error={state.error} /> : null}
      {items.length === 0 ? (
        <EmptyState icon={GraduationCap} compact title="Nenhuma habilidade versionada ainda">
          Ensine uma no Foco: grave o treinamento e, na revisão, gere a candidata de habilidade. Ou converta um fluxo acima.
        </EmptyState>
      ) : (
        <ul className={styles.learnList} aria-label="Habilidades">
          {grupos.map((g) => {
            const publicadaEUltima = g.publicada?.ref === g.ultima.ref;
            return (
              <li key={g.skillId} className={styles.learnItem}>
                <div className={styles.learnHead}>
                  <span className={`${styles.appName} truncate`} title={g.name}>{g.name}</span>
                  <span className={styles.learnSpacer} />
                  <span className={styles.appMeta}>
                    <span className="mono">{g.skillId}</span>
                    {g.appId ? ` · app: ${appNames.get(g.appId) ?? g.appId}` : ''}
                    {' '}· {plural(g.total, 'versão', 'versões')}
                  </span>
                </div>
                <ul className={styles.skillVersions} aria-label={`Versões de ${g.skillId}`}>
                  {g.publicada && !publicadaEUltima ? versao(g.publicada, 'publicada') : null}
                  {versao(g.ultima, publicadaEUltima ? 'publicada' : 'última')}
                </ul>
                {g.anteriores.length > 0 ? (
                  <Disclosure bare summary={plural(g.anteriores.length, 'versão anterior', 'versões anteriores')}>
                    <ul className={styles.skillVersions} aria-label={`Versões anteriores de ${g.skillId}`}>
                      {g.anteriores.map((sk) => versao(sk, null))}
                    </ul>
                  </Disclosure>
                ) : null}
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

/** Fase J, só com `features.skills`: que habilidade adotou cada fluxo, e o que recarregar depois de converter. */
interface FlowConversionProps {
  adotantes: Map<string, SkillSummary[]>;
  onMudou: () => void;
}

function FlowList({ state, onRetry, onChange, cobertura, conversao }: ListProps<Flow> & { cobertura?: Map<string, FlowCoverage>; conversao?: FlowConversionProps }) {
  const apps = useAppStore((s) => s.apps);
  const appNames = useMemo(() => new Map(apps.map((a) => [a.id, a.name])), [apps]);
  const [busy, setBusy] = useState<Record<string, 'toggle' | 'delete' | 'convert' | 'undo' | undefined>>({});
  const [recusas, setRecusas] = useState<Record<string, Refusal | undefined>>({});
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

  // Fase J: converter = adotar (v1 publicada = este plano, sem mudança) + rascunho v2 descompilado, fluxo desligado,
  // numa transação; desfazer = a publicada desabilitada e o fluxo religado como era. A recusa (ex.: o plano não se
  // reproduz pela DSL) aparece na linha, com os erros da ida e volta.
  const converter = async (flow: Flow) => {
    if (!conversao || busy[flow.id]) return;
    const { confirmed, note } = await confirm({
      title: `Converter o fluxo “${flow.name}” em habilidade?`,
      confirmLabel: 'Converter',
      cancelLabel: 'Cancelar',
      note: { label: 'Motivo (opcional)' },
      body: 'A versão 1 da habilidade é o plano deste fluxo, sem mudança: as execuções seguem iguais e as receitas continuam valendo. A versão 2 fica em rascunho, com o documento para editar e publicar quando quiser. O fluxo é desligado na mesma operação, e dá para desfazer.',
    });
    if (!confirmed) return;
    setBusy((b) => ({ ...b, [flow.id]: 'convert' }));
    setRecusas((r) => ({ ...r, [flow.id]: undefined }));
    try {
      const feito = await api.adoptFlow(flow.id, note ? { reason: note } : {});
      const avisos = feito.warnings.length;
      toast({
        tone: 'success',
        title: `Fluxo “${flow.name}” convertido em ${feito.skill_id}`,
        message: `${feito.published.ref} publicada · ${feito.draft.ref} em rascunho${avisos ? ` · ${plural(avisos, 'aviso', 'avisos')}` : ''}.`,
      });
      conversao.onMudou();
    } catch (e) {
      setRecusas((r) => ({ ...r, [flow.id]: toRefusal(e) }));
    } finally {
      setBusy((b) => ({ ...b, [flow.id]: undefined }));
    }
  };

  const desfazer = async (flow: Flow, skillId: string) => {
    if (!conversao || busy[flow.id]) return;
    const { confirmed, note } = await confirm({
      title: `Desfazer a conversão de “${flow.name}”?`,
      danger: true,
      confirmLabel: 'Desfazer',
      cancelLabel: 'Cancelar',
      note: { label: 'Motivo (fica no histórico)' },
      body: `A versão publicada de ${skillId} é desabilitada e o fluxo volta a valer exatamente como era. O rascunho da conversão, se ainda for rascunho, é apagado.`,
    });
    if (!confirmed) return;
    setBusy((b) => ({ ...b, [flow.id]: 'undo' }));
    setRecusas((r) => ({ ...r, [flow.id]: undefined }));
    try {
      await api.releaseFlow(flow.id, note ? { reason: note } : {});
      toast({ tone: 'success', title: `Fluxo “${flow.name}” de volta`, message: `${skillId} desabilitada.` });
      conversao.onMudou();
    } catch (e) {
      setRecusas((r) => ({ ...r, [flow.id]: toRefusal(e) }));
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
          {items.map((flow) => {
            const versoes = conversao?.adotantes.get(flow.id) ?? [];
            const publicada = versoes.find((v) => v.state === 'published') ?? null;
            const adotante = versoes[0]?.skill_id ?? null;
            const ocupado = busy[flow.id];
            return (
            <li key={flow.id} className={styles.learnItem}>
              <div className={styles.learnHead}>
                <span className={`${styles.appName} truncate`} title={flow.name}>{flow.name}</span>
                <span className={styles.learnSpacer} />
                {publicada ? <Badge tone="accent" title="O comando deste fluxo é resolvido pela habilidade">habilidade {publicada.ref}</Badge> : null}
                {conversao && publicada ? (
                  <Button size="sm" variant="ghost" icon={Undo2} loading={ocupado === 'undo'} disabled={!!ocupado && ocupado !== 'undo'} aria-label={`Desfazer a conversão do fluxo ${flow.name}`} onClick={() => void desfazer(flow, publicada.skill_id)}>
                    Desfazer conversão
                  </Button>
                ) : null}
                {conversao && !publicada && flow.status === 'active' ? (
                  <Button size="sm" variant="secondary" icon={GraduationCap} loading={ocupado === 'convert'} disabled={!!ocupado && ocupado !== 'convert'} aria-label={`Converter o fluxo ${flow.name} em habilidade`} onClick={() => void converter(flow)}>
                    Converter em habilidade
                  </Button>
                ) : null}
                <Switch
                  checked={flow.status === 'active'}
                  label={`Fluxo “${flow.name}” ativo`}
                  busy={ocupado === 'toggle'}
                  disabled={(!!ocupado && ocupado !== 'toggle') || !!publicada}
                  onChange={(next) => void setStatus(flow, next)}
                />
                <Button size="sm" variant="dangerGhost" icon={Trash2} loading={ocupado === 'delete'} disabled={!!ocupado && ocupado !== 'delete'} disabledReason={adotante ? `Fluxo adotado por ${adotante}: é o caminho de volta da conversão.` : null} aria-label={`Excluir o fluxo ${flow.name}`} onClick={() => void remove(flow)}>
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
              {recusas[flow.id] ? <RefusalBanner refusal={recusas[flow.id] as Refusal} /> : null}
            </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

// ---- receitas -----------------------------------------------------------------------------------

/**
 * Há tabela escondida à direita? Liga a sombra da coluna fixa só enquanto houver o que rolar: a tabela de receitas
 * passa da largura da tela até em 1440 px, e sem pista a coluna dos botões ficava fora da vista. `ativo` refaz a
 * medição quando a tabela passa a existir (a lista começa carregando, sem o elemento).
 */
function useOverflowRight(ativo: boolean): [RefObject<HTMLDivElement | null>, boolean] {
  const ref = useRef<HTMLDivElement>(null);
  const [overflow, setOverflow] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || !ativo) return;
    const medir = () => setOverflow(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
    medir();
    el.addEventListener('scroll', medir, { passive: true });
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(medir) : null;
    ro?.observe(el);
    return () => {
      el.removeEventListener('scroll', medir);
      ro?.disconnect();
    };
  }, [ativo]);
  return [ref, overflow];
}

function RecipeList({ state, onRetry, onChange }: ListProps<Recipe>) {
  const apps = useAppStore((s) => s.apps);
  const appByPackage = useMemo(() => new Map(apps.map((a) => [a.package, a.name])), [apps]);
  const [busy, setBusy] = useState<Record<number, 'toggle' | 'delete' | undefined>>({});
  const items = state.items;
  const [wrapRef, transborda] = useOverflowRight(!!items && items.length > 0);

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
        <div ref={wrapRef} className={`${styles.tableWrap} ${transborda ? styles.tableWrapOverflow : ''}`}>
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
                <th scope="col" className={styles.stickyCol}><span className="sr-only">Comandos</span></th>
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
                    <td className={styles.stickyCol} style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
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
