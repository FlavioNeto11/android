/**
 * Grupos de acesso (pedido do dono, 24/09): um conjunto de políticas e limites que se atribui a quantos perfis
 * se quiser. O perfil herda do grupo; o que for mudado deliberadamente no próprio perfil sobrepõe o grupo.
 *
 * A tela de Perfis ganha a seção com os grupos em cartões (nome, quantos perfis, o resumo "N sozinho · N com
 * aprovação · N só manual" e quem está dentro); criar e editar abrem o MESMO editor visual da aba Configurações
 * do perfil — nada de formulário novo. Membros se escolhem ali mesmo, marcando os perfis.
 */
import { Plus, ShieldCheck, Trash2, Users } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { Capability, InstagramProfile, PolicyGroup, PolicyName } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Dialog } from '../../components/Dialog';
import { Field, Select, TextInput } from '../../components/Field';
import { toast, toastError } from '../../store/toasts';
import { LimitsEditor, PolicyActionsEditor, resumoDePoliticas } from './PolicyEditor';
import styles from './Profiles.module.css';

/** Ações do app que provê a conta (o Instagram), pelo registro — o mesmo caminho da aba Configurações. */
function useAcoesDoCatalogo(): Capability[] {
  const [acoes, setAcoes] = useState<Capability[]>([]);
  useEffect(() => {
    let vivo = true;
    api.listAppCatalog()
      .then(async (apps) => {
        const alvo = apps.find((a) => a.session_provider !== null);
        const c = alvo ? await api.listCapabilities(alvo.package) : [];
        if (vivo) setAcoes(c);
      })
      .catch(() => undefined);
    return () => {
      vivo = false;
    };
  }, []);
  return acoes;
}

export function PolicyGroupsSection({ grupos, profiles, onChanged }: {
  grupos: PolicyGroup[];
  profiles: InstagramProfile[];
  onChanged: () => Promise<void>;
}) {
  const acoes = useAcoesDoCatalogo();
  const [editando, setEditando] = useState<PolicyGroup | 'novo' | null>(null);

  async function apagar(g: PolicyGroup) {
    const { confirmed } = await confirm({
      title: `Apagar o grupo ${g.name}?`,
      body: g.members.length
        ? `${g.members.length} perfil(is) deixam o grupo e voltam a herdar só do padrão do catálogo. As escolhas próprias de cada perfil ficam.`
        : 'Nenhum perfil está neste grupo.',
      confirmLabel: 'Apagar grupo', danger: true,
    });
    if (!confirmed) return;
    try {
      await api.deletePolicyGroup(g.id);
      toast({ tone: 'success', title: `Grupo ${g.name} apagado` });
      await onChanged();
    } catch (e) {
      toastError('Não foi possível apagar o grupo', e);
    }
  }

  return (
    <section className={styles.groupsSection} aria-labelledby="grupos-de-acesso">
      <div className={styles.groupsHead}>
        <div>
          <h3 id="grupos-de-acesso" className={styles.groupsTitle}><ShieldCheck size={16} aria-hidden /> Grupos de acesso</h3>
          <p className={styles.detail}>
            Políticas e limites que valem para vários perfis de uma vez. O que um perfil mudar para si sobrepõe o grupo.
          </p>
        </div>
        <Button size="sm" icon={Plus} onClick={() => setEditando('novo')}>Novo grupo</Button>
      </div>
      {grupos.length === 0 ? (
        <p className={styles.detail}>Nenhum grupo ainda. Crie um e marque os perfis que devem segui-lo.</p>
      ) : (
        <div className={styles.groupGrid}>
          {grupos.map((g) => {
            const efetivo = (c: Capability): PolicyName => g.capabilities[c.key] ?? c.default_policy;
            const mudancas = Object.keys(g.capabilities).length + Object.keys(g.limits).length;
            return (
              <Card key={g.id}>
                <CardHeader
                  title={<span className={styles.groupName}>{g.name}</span>}
                  subtitle={g.description || undefined}
                  actions={
                    <div className={styles.actions}>
                      <Button size="sm" variant="ghost" onClick={() => setEditando(g)}>Editar</Button>
                      <Button size="sm" variant="dangerGhost" icon={Trash2} iconOnly label={`Apagar o grupo ${g.name}`}
                              onClick={() => void apagar(g)} />
                    </div>
                  } />
                <CardBody>
                  <p className={styles.detail}>{acoes.length ? resumoDePoliticas(acoes, efetivo) : '—'}</p>
                  <p className={styles.detail}>
                    {mudancas ? `${mudancas} mudança(s) em relação ao padrão` : 'Igual ao padrão do catálogo'}
                    {g.loosened.length ? <> · <Badge tone="danger" size="sm">afrouxa {g.loosened.length} ação(ões) de risco</Badge></> : null}
                  </p>
                  <div className={styles.memberChips} aria-label={`Perfis no grupo ${g.name}`}>
                    <Badge size="sm" tone={g.members.length ? 'info' : 'muted'}>
                      <Users size={12} aria-hidden /> {g.members.length} perfil(is)
                    </Badge>
                    {g.members.slice(0, 8).map((m) => <span key={m.id} className={styles.memberChip}>@{m.username}</span>)}
                    {g.members.length > 8 ? <span className={styles.muted}>+{g.members.length - 8}</span> : null}
                  </div>
                </CardBody>
              </Card>
            );
          })}
        </div>
      )}
      {editando ? (
        <PolicyGroupDialog grupo={editando === 'novo' ? null : editando} acoes={acoes} profiles={profiles}
                           grupos={grupos}
                           onClose={() => setEditando(null)}
                           onSaved={async () => {
                             setEditando(null);
                             await onChanged();
                           }} />
      ) : null}
    </section>
  );
}

function PolicyGroupDialog({ grupo, acoes, profiles, grupos, onClose, onSaved }: {
  grupo: PolicyGroup | null;
  acoes: Capability[];
  profiles: InstagramProfile[];
  grupos: PolicyGroup[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [nome, setNome] = useState(grupo?.name ?? '');
  const [descricao, setDescricao] = useState(grupo?.description ?? '');
  const [caps, setCaps] = useState<Record<string, PolicyName>>(grupo?.capabilities ?? {});
  const [limites, setLimites] = useState<Record<string, number>>(grupo?.limits ?? {});
  const [membros, setMembros] = useState<Set<string>>(new Set(grupo?.members.map((m) => m.id) ?? []));
  const [padraoLimites, setPadraoLimites] = useState<Record<string, number>>({});
  const [salvando, setSalvando] = useState(false);
  const nomeDoGrupo = useMemo(() => new Map(grupos.map((g) => [g.id, g.name])), [grupos]);

  useEffect(() => {
    api.policyDefaults().then((d) => setPadraoLimites(d.limits)).catch(() => undefined);
  }, []);

  async function partirDe(profileId: string) {
    if (!profileId) return;
    try {
      const p = await api.getPolicy(profileId);
      // O que o perfil tem HOJE de diferente do padrão: o grupo dele por baixo, as escolhas dele por cima.
      setCaps({ ...(p.group ?? {}), ...(p.own ?? {}) });
      setLimites({ ...(p.group_limits ?? {}), ...(p.own_limits ?? {}) });
    } catch (e) {
      toastError('Não foi possível ler o acesso do perfil', e);
    }
  }

  async function salvar() {
    if (!nome.trim()) {
      toast({ tone: 'warning', title: 'Dê um nome ao grupo' });
      return;
    }
    setSalvando(true);
    try {
      const profile_ids = [...membros];
      if (grupo === null) {
        await api.createPolicyGroup({ name: nome.trim(), description: descricao.trim(), capabilities: caps, limits: limites, profile_ids });
      } else {
        // Chave que saiu do rascunho vai como `null`: é assim que o grupo volta ao padrão naquela ação.
        const capsPatch: Record<string, PolicyName | null> = {};
        for (const k of new Set([...Object.keys(grupo.capabilities), ...Object.keys(caps)])) capsPatch[k] = caps[k] ?? null;
        const limPatch: Record<string, number | null> = {};
        for (const k of new Set([...Object.keys(grupo.limits), ...Object.keys(limites)])) limPatch[k] = limites[k] ?? null;
        await api.updatePolicyGroup(grupo.id, { name: nome.trim(), description: descricao.trim(), capabilities: capsPatch, limits: limPatch, profile_ids });
      }
      toast({ tone: 'success', title: grupo ? `Grupo ${nome.trim()} salvo` : `Grupo ${nome.trim()} criado`,
              message: `${profile_ids.length} perfil(is) seguem este grupo.` });
      await onSaved();
    } catch (e) {
      toastError('Não foi possível salvar o grupo', e);
    } finally {
      setSalvando(false);
    }
  }

  function alternar(id: string) {
    setMembros((atual) => {
      const novo = new Set(atual);
      if (novo.has(id)) novo.delete(id);
      else novo.add(id);
      return novo;
    });
  }

  const efetivo = (c: Capability): PolicyName => caps[c.key] ?? c.default_policy;
  const limitesEfetivos = { ...padraoLimites, ...limites };

  return (
    <Dialog open onClose={onClose} title={grupo ? `Grupo ${grupo.name}` : 'Novo grupo de acesso'} icon={ShieldCheck}
            size="lg"
            footer={
              <>
                <Button variant="ghost" onClick={onClose}>Cancelar</Button>
                <Button loading={salvando} onClick={() => void salvar()}>{grupo ? 'Salvar grupo' : 'Criar grupo'}</Button>
              </>
            }>
      <div className={styles.groupDialog}>
        <div className={styles.groupDialogTop}>
          <Field label="Nome">
            {({ id }) => <TextInput id={id} value={nome} maxLength={80} onChange={(e) => setNome(e.target.value)} placeholder="Ex.: Aquecimento" />}
          </Field>
          <Field label="Descrição" unit="opcional">
            {({ id }) => <TextInput id={id} value={descricao} maxLength={400} onChange={(e) => setDescricao(e.target.value)}
                                    placeholder="Para que serve este grupo" />}
          </Field>
          {grupo === null ? (
            <Field label="Começar a partir de" unit="opcional">
              {({ id }) => (
                <Select id={id} defaultValue="" onChange={(e) => void partirDe(e.target.value)}>
                  <option value="">Padrão do catálogo</option>
                  {profiles.map((p) => <option key={p.id} value={p.id}>o acesso de hoje de @{p.username}</option>)}
                </Select>
              )}
            </Field>
          ) : null}
        </div>

        <fieldset className={styles.memberPick}>
          <legend>Perfis neste grupo <Badge size="sm">{membros.size}</Badge></legend>
          {profiles.length === 0 ? <p className={styles.detail}>Nenhum perfil cadastrado.</p> : null}
          {profiles.map((p) => {
            const outro = p.policy_group_id && p.policy_group_id !== grupo?.id ? nomeDoGrupo.get(p.policy_group_id) : null;
            return (
              <label key={p.id} className={styles.memberOption} data-checked={membros.has(p.id) || undefined}>
                <input type="checkbox" aria-label={`@${p.username}`} checked={membros.has(p.id)} onChange={() => alternar(p.id)} />
                <span>@{p.username}</span>
                {outro ? <span className={styles.muted}>{membros.has(p.id) ? `sai de ${outro}` : `em ${outro}`}</span> : null}
              </label>
            );
          })}
        </fieldset>

        <div className={styles.personaLayout}>
          <div>
            <h4 className={styles.groupDialogSub}>O que os perfis do grupo podem fazer</h4>
            <PolicyActionsEditor
              acoes={acoes} efetivo={efetivo} salvando={salvando}
              loosened={acoes.filter((c) => c.risk === 'high' && caps[c.key] && RANK[caps[c.key]!] > RANK[c.default_policy]).map((c) => c.key)}
              origem={(c) => (caps[c.key]
                ? { propria: true, rotulo: 'definido no grupo', tone: 'info' }
                : { propria: false, rotulo: 'padrão', tone: 'muted' })}
              herdaria={(c) => c.default_policy}
              nomeDaHeranca={() => 'do padrão do catálogo'}
              onChange={(keys, valor) => setCaps((atual) => {
                const novo = { ...atual };
                for (const k of keys) {
                  if (valor === null) delete novo[k];
                  else novo[k] = valor;
                }
                return novo;
              })} />
          </div>
          <div>
            <h4 className={styles.groupDialogSub}>Limites</h4>
            <div className={styles.limitGrid}>
              <LimitsEditor
                limites={limitesEfetivos} salvando={salvando}
                origem={(k) => (k in limites
                  ? { propria: true, rotulo: 'definido no grupo', tone: 'info' }
                  : { propria: false, rotulo: 'padrão', tone: 'muted' })}
                herdaria={(k) => padraoLimites[k]}
                onChange={(k, valor) => setLimites((atual) => {
                  const novo = { ...atual };
                  if (valor === null) delete novo[k];
                  else novo[k] = valor;
                  return novo;
                })} />
            </div>
          </div>
        </div>
      </div>
    </Dialog>
  );
}

const RANK: Record<PolicyName, number> = { disabled: 0, manual_only: 1, approval_required: 2, autonomous: 3 };
