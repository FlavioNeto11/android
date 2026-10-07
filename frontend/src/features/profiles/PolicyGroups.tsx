/**
 * Grupos de acesso (pedido do dono, 24/09): um conjunto de políticas que se atribui a quantos perfis
 * se quiser. O perfil herda do grupo; o que for mudado deliberadamente no próprio perfil sobrepõe o grupo.
 *
 * A tela de Perfis ganha a seção com os grupos em cartões (nome, quantos perfis, o resumo "N sozinho · N com
 * aprovação · N só manual" e quem está dentro); criar e editar abrem o MESMO editor visual da aba Configurações
 * do perfil — nada de formulário novo. Membros se escolhem ali mesmo, marcando os perfis.
 */
import { Plus, ShieldCheck, Trash2, Users } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { AppCatalogEntry, Capability, InstagramProfile, PolicyGroup, PolicyName } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Dialog } from '../../components/Dialog';
import { Field, Select, TextInput } from '../../components/Field';
import { plural } from '../../lib/format';
import { toast, toastError } from '../../store/toasts';
import { PolicyActionsEditor, resumoDePoliticas } from './PolicyEditor';
import styles from './Profiles.module.css';
import { nomeDe, rotuloDaConta } from './pessoa';

/** Apps com catálogo de ações, o âncora primeiro (23.10). Antes o painel pegava "o primeiro app com login
 *  gerenciado NA ORDEM DA LISTA" — só funcionava porque, até aqui, o Instagram era o único; um segundo app com
 *  conta gerenciada (ex.: Outlook, quando ganhar catálogo) mudaria a resposta por acidente de ordem, não por
 *  escolha. Aqui a escolha é deliberada: o âncora por padrão, ou a pessoa escolhe entre os que têm catálogo. */
function appsComCatalogo(catalogo: readonly AppCatalogEntry[]): AppCatalogEntry[] {
  return catalogo.filter((a) => a.has_catalog)
    .slice()
    .sort((a, b) => Number(b.profile_anchor) - Number(a.profile_anchor) || a.label.localeCompare(b.label));
}

/** Ações do app pedido (`pacote`; `null` = o âncora, ou o único com catálogo) — o mesmo caminho da aba
 *  Configurações. Devolve também a lista de apps com catálogo, para o seletor aparecer quando há mais de um. */
function useAcoesDoApp(pacote: string | null): {
  acoes: Capability[]; apps: AppCatalogEntry[]; pacoteEfetivo: string | null; pronto: boolean;
} {
  // `null` = o catálogo ainda não chegou: até lá, `pacoteEfetivo` ainda não quer dizer "nenhum app".
  const [catalogo, setCatalogo] = useState<AppCatalogEntry[] | null>(null);
  const [acoes, setAcoes] = useState<Capability[]>([]);
  useEffect(() => {
    let vivo = true;
    // Sem o registro, a seção segue como antes (sem lista de ações), mas deixa de esperar por ele.
    api.listAppCatalog().then((c) => { if (vivo) setCatalogo(c); }).catch(() => { if (vivo) setCatalogo([]); });
    return () => { vivo = false; };
  }, []);
  const apps = appsComCatalogo(catalogo ?? []);
  const pacoteEfetivo = pacote ?? apps[0]?.package ?? null;
  useEffect(() => {
    let vivo = true;
    if (!pacoteEfetivo) {
      setAcoes([]);
      return () => { vivo = false; };
    }
    api.listCapabilities(pacoteEfetivo).then((c) => { if (vivo) setAcoes(c); }).catch(() => undefined);
    return () => { vivo = false; };
  }, [pacoteEfetivo]);
  return { acoes, apps, pacoteEfetivo, pronto: catalogo !== null };
}

export function PolicyGroupsSection({ grupos, profiles, onChanged }: {
  grupos: PolicyGroup[];
  profiles: InstagramProfile[];
  onChanged: () => Promise<void>;
}) {
  const { acoes } = useAcoesDoApp(null);
  const [editando, setEditando] = useState<PolicyGroup | 'novo' | null>(null);

  async function apagar(g: PolicyGroup) {
    const { confirmed } = await confirm({
      title: `Apagar o grupo ${g.name}?`,
      body: g.members.length
        ? `${g.members.length === 1 ? '1 persona deixa o grupo e volta' : `${g.members.length} personas deixam o grupo e voltam`} a herdar só do padrão do catálogo. As escolhas próprias de cada persona ficam.`
        : 'Nenhuma persona está neste grupo.',
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
            Políticas que valem para várias personas de uma vez. O que uma persona mudar para si sobrepõe o grupo.
          </p>
        </div>
        <Button size="sm" icon={Plus} onClick={() => setEditando('novo')}>Novo grupo</Button>
      </div>
      {grupos.length === 0 ? (
        <p className={styles.detail}>Nenhum grupo ainda. Crie um e marque as personas que devem segui-lo.</p>
      ) : (
        <div className={styles.groupGrid}>
          {grupos.map((g) => {
            const efetivo = (c: Capability): PolicyName => g.capabilities[c.key] ?? c.default_policy;
            const mudancas = Object.keys(g.capabilities).length;
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
                  <div className={styles.memberChips} aria-label={`Personas no grupo ${g.name}`}>
                    <Badge size="sm" tone={g.members.length ? 'info' : 'muted'}>
                      <Users size={12} aria-hidden /> {plural(g.members.length, 'persona', 'personas')}
                    </Badge>
                    {g.members.slice(0, 8).map((m) => {
                      const q = rotuloDaConta(m);
                      return <span key={m.id} className={styles.memberChip} data-sem-conta={q.semConta || undefined}>{q.texto}</span>;
                    })}
                    {g.members.length > 8 ? <span className={styles.muted}>+{g.members.length - 8}</span> : null}
                  </div>
                </CardBody>
              </Card>
            );
          })}
        </div>
      )}
      {editando ? (
        <PolicyGroupDialog grupo={editando === 'novo' ? null : editando} profiles={profiles}
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

function PolicyGroupDialog({ grupo, profiles, grupos, onClose, onSaved }: {
  grupo: PolicyGroup | null;
  profiles: InstagramProfile[];
  grupos: PolicyGroup[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [nome, setNome] = useState(grupo?.name ?? '');
  const [descricao, setDescricao] = useState(grupo?.description ?? '');
  // O grupo guarda um recorte de política POR APP (23.10): a mesma chave de ação pode existir em dois catálogos
  // (SEND_MESSAGE), e a escolha para um app não vale para o outro. `rascunhos` é o que está sendo editado, por
  // pacote; `originais`, o que o servidor tem, para mandar `null` no que saiu. `grupo.capabilities` chega da
  // listagem sem `?package=`, isto é, o recorte do âncora (`grupo.package`). Chave '' = nenhum app se resolveu.
  const pacoteDaLista = grupo?.package ?? '';
  const [originais, setOriginais] = useState<Record<string, Record<string, PolicyName>>>(
    grupo ? { [pacoteDaLista]: grupo.capabilities } : {});
  const [rascunhos, setRascunhos] = useState<Record<string, Record<string, PolicyName>>>(
    grupo ? { [pacoteDaLista]: grupo.capabilities } : {});
  const [membros, setMembros] = useState<Set<string>>(new Set(grupo?.members.map((m) => m.id) ?? []));
  // Membro cuja conta saiu da plataforma (29.23) some da listagem de perfis, mas continua no grupo: sem esta lista,
  // o editor contava 2 e mostrava 1, e não havia como tirá-lo do grupo (29.25).
  const foraDaLista = (grupo?.members ?? []).filter((m) => !profiles.some((p) => p.id === m.id));
  const [salvando, setSalvando] = useState(false);
  // "Começar a partir de" em voo (29.106): só a última escolha vale. Escolher A e logo B deixava a resposta de A, se
  // chegasse depois, por cima de B; e salvar nesse meio criava o grupo sem a escolha.
  const leituraDoPerfil = useRef(0);
  const [lendoPerfil, setLendoPerfil] = useState(false);
  // A dica só aparece se a leitura demorar: a comum leva ~40 ms, e uma linha que surge e some empurraria o formulário
  // duas vezes a cada escolha (M1 da leitura do #398). A trava continua imediata.
  const [dicaDaLeitura, setDicaDaLeitura] = useState(false);
  useEffect(() => {
    if (!lendoPerfil) {
      setDicaDaLeitura(false);
      return undefined;
    }
    const espera = setTimeout(() => setDicaDaLeitura(true), DICA_DA_LEITURA_MS);
    return () => clearTimeout(espera);
  }, [lendoPerfil]);
  const [pacoteEscolhido, setPacoteEscolhido] = useState<string | null>(null);
  const { acoes, apps, pacoteEfetivo, pronto } = useAcoesDoApp(pacoteEscolhido);
  const chave = pacoteEfetivo ?? '';
  const caps = rascunhos[chave] ?? {};
  // Grupo existente, app que a listagem não trouxe: o recorte dele vem do servidor antes de editar — senão salvar
  // mandaria `null` para o que o grupo já tinha nele sem a pessoa ter visto.
  const carregandoApp = !pronto || (grupo !== null && !(chave in originais));
  // O grupo já criado numa tentativa anterior de salvar (a gravação de um segundo app falhou): repetir não cria outro.
  const [criado, setCriado] = useState<string | null>(null);
  const nomeDoGrupo = useMemo(() => new Map(grupos.map((g) => [g.id, g.name])), [grupos]);

  useEffect(() => {
    if (!grupo || !pronto || chave in originais) return undefined;
    let vivo = true;
    api.getPolicyGroup(grupo.id, pacoteEfetivo)
      .then((g) => {
        if (!vivo) return;
        setOriginais((atual) => ({ ...atual, [chave]: g.capabilities }));
        setRascunhos((atual) => (chave in atual ? atual : { ...atual, [chave]: g.capabilities }));
      })
      .catch((e) => toastError('Não foi possível ler o grupo neste aplicativo', e));
    return () => { vivo = false; };
  }, [grupo, pronto, originais, chave, pacoteEfetivo]);

  async function partirDe(profileId: string) {
    const minha = ++leituraDoPerfil.current;    // a escolha nova, mesmo a do padrão, aposenta a leitura em voo
    if (!profileId || !pronto) {                // sem o catálogo, o rascunho ficaria sem app (chave '')
      setLendoPerfil(false);
      // "Padrão do catálogo" também SUBSTITUI: depois de partir de A, voltar ao padrão tira o que veio de A (29.109).
      if (!profileId) setRascunhos({});
      return;
    }
    setLendoPerfil(true);
    try {
      // O que o perfil tem HOJE de diferente do padrão, em CADA app com catálogo: o grupo dele por baixo, as
      // escolhas dele por cima. SUBSTITUI o rascunho inteiro — escolher A e depois B é partir de B; mesclar
      // levaria para o grupo uma ação de risco que só A afrouxou, sem aparecer como escolha de B.
      const pacotes = apps.length ? apps.map((a) => a.package) : [pacoteEfetivo];
      const politicas = await Promise.all(pacotes.map((pkg) => api.getPolicy(profileId, pkg)));
      if (minha !== leituraDoPerfil.current) return;
      setRascunhos(Object.fromEntries(pacotes.map((pkg, i) =>
        [pkg ?? '', { ...(politicas[i]!.group ?? {}), ...(politicas[i]!.own ?? {}) }])));
    } catch (e) {
      if (minha === leituraDoPerfil.current) toastError('Não foi possível ler o acesso da persona', e);
    } finally {
      if (minha === leituraDoPerfil.current) setLendoPerfil(false);
    }
  }

  async function salvar() {
    if (carregandoApp || lendoPerfil) return;
    if (!nome.trim()) {
      toast({ tone: 'warning', title: 'Dê um nome ao grupo' });
      return;
    }
    setSalvando(true);
    try {
      const profile_ids = [...membros];
      // Um pedido por app editado, cada um com o `package` dele (o servidor valida contra o catálogo certo e grava
      // no recorte certo). O do app em tela vai primeiro, levando nome, descrição e membros.
      const pacotes = [...new Set([chave, ...Object.keys(rascunhos)])];
      const pedido = (pkg: string) => (pkg === '' ? null : pkg);
      const comum = { name: nome.trim(), description: descricao.trim() };
      let id = grupo?.id ?? criado;
      for (const [i, pkg] of pacotes.entries()) {
        const rascunho = rascunhos[pkg] ?? {};
        if (id === null) {
          const novo = await api.createPolicyGroup({ ...comum, capabilities: rascunho, profile_ids },
                                                   pedido(pkg));
          id = novo.id;
          setCriado(id);
          continue;
        }
        // Chave que saiu do rascunho vai como `null`: é assim que o grupo volta ao padrão naquela ação. App que
        // ninguém abriu nem editou não entra: o recorte dele no servidor fica como está.
        if (i > 0 && !(pkg in rascunhos)) continue;
        const original = grupo === null ? {} : (originais[pkg] ?? {});
        const capsPatch: Record<string, PolicyName | null> = {};
        for (const k of new Set([...Object.keys(original), ...Object.keys(rascunho)])) capsPatch[k] = rascunho[k] ?? null;
        if (i === 0) {
          await api.updatePolicyGroup(id, { ...comum, capabilities: capsPatch, profile_ids }, pedido(pkg));
        } else if (Object.entries(capsPatch).some(([k, v]) => v !== (original[k] ?? null))) {
          // Outro app: só se algo mudou nele (o do âncora vem aberto da listagem, e regravá-lo igual é ruído).
          await api.updatePolicyGroup(id, { capabilities: capsPatch }, pedido(pkg));
        }
      }
      toast({ tone: 'success', title: grupo ? `Grupo ${nome.trim()} salvo` : `Grupo ${nome.trim()} criado`,
              message: `${plural(profile_ids.length, 'persona segue', 'personas seguem')} este grupo.` });
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

  return (
    <Dialog open onClose={onClose} title={grupo ? `Grupo ${grupo.name}` : 'Novo grupo de acesso'} icon={ShieldCheck}
            size="lg"
            footer={
              <>
                <Button variant="ghost" onClick={onClose}>Cancelar</Button>
                {/* Antes do catálogo, o rascunho não tem app: o grupo nasceria vazio e só um segundo pedido o
                    corrigiria — com os membros já dentro, herdando o padrão nesse intervalo. */}
                <Button loading={salvando} disabled={carregandoApp || lendoPerfil} onClick={() => void salvar()}>
                  {grupo ? 'Salvar grupo' : 'Criar grupo'}
                </Button>
              </>
            }>
      <div className={styles.groupDialog}>
        <div className={styles.groupDialogTop}>
          <Field label="Nome">
            {({ id }) => <TextInput id={id} value={nome} maxLength={80} onChange={(e) => setNome(e.target.value)} placeholder="Ex.: Cautelosos" />}
          </Field>
          <Field label="Descrição" unit="opcional">
            {({ id }) => <TextInput id={id} value={descricao} maxLength={400} onChange={(e) => setDescricao(e.target.value)}
                                    placeholder="Para que serve este grupo" />}
          </Field>
          {grupo === null ? (
            <Field label="Começar a partir de" unit="opcional"
                   hint={dicaDaLeitura ? 'Lendo o acesso de hoje da persona; salvar e editar esperam a resposta.' : undefined}>
              {({ id }) => (
                <Select id={id} defaultValue="" disabled={!pronto} onChange={(e) => void partirDe(e.target.value)}>
                  <option value="">Padrão do catálogo</option>
                  {profiles.map((p) => <option key={p.id} value={p.id}>o acesso de hoje de {rotuloDaConta({ username: p.username, name: nomeDe(p) }).texto}</option>)}
                </Select>
              )}
            </Field>
          ) : null}
          {apps.length > 1 ? (
            <Field label="Aplicativo" hint="As ações abaixo são deste app; o grupo guarda a escolha de cada app à parte.">
              {({ id, describedBy }) => (
                <Select id={id} aria-describedby={describedBy} value={pacoteEfetivo ?? ''}
                        onChange={(e) => setPacoteEscolhido(e.target.value)}>
                  {apps.map((a) => <option key={a.package} value={a.package}>{a.label}</option>)}
                </Select>
              )}
            </Field>
          ) : null}
        </div>

        <fieldset className={styles.memberPick}>
          <legend>Perfis neste grupo <Badge size="sm">{membros.size}</Badge></legend>
          {profiles.length === 0 && foraDaLista.length === 0
            ? <p className={styles.detail}>Nenhuma persona com conta cadastrada.</p> : null}
          {foraDaLista.map((m) => {
            const q = rotuloDaConta({ username: m.username, name: m.name });
            return (
              <label key={m.id} className={styles.memberOption} data-checked={membros.has(m.id) || undefined}>
                <input type="checkbox" aria-label={q.texto} checked={membros.has(m.id)} onChange={() => alternar(m.id)} />
                <span data-sem-conta={q.semConta || undefined}>{q.texto}</span>
              </label>
            );
          })}
          {profiles.map((p) => {
            const outro = p.policy_group_id && p.policy_group_id !== grupo?.id ? nomeDoGrupo.get(p.policy_group_id) : null;
            const q = rotuloDaConta({ username: p.username, name: nomeDe(p) });
            return (
              <label key={p.id} className={styles.memberOption} data-checked={membros.has(p.id) || undefined}>
                <input type="checkbox" aria-label={q.texto} checked={membros.has(p.id)} onChange={() => alternar(p.id)} />
                <span data-sem-conta={q.semConta || undefined}>{q.texto}</span>
                {outro ? <span className={styles.muted}>{membros.has(p.id) ? `sai de ${outro}` : `em ${outro}`}</span> : null}
              </label>
            );
          })}
        </fieldset>

        <div className={styles.personaLayout}>
          <div>
            <h4 className={styles.groupDialogSub}>O que as personas do grupo podem fazer</h4>
            <PolicyActionsEditor
              acoes={acoes} efetivo={efetivo} salvando={salvando || carregandoApp || lendoPerfil}
              loosened={acoes.filter((c) => c.risk === 'high' && caps[c.key] && RANK[caps[c.key]!] > RANK[c.default_policy]).map((c) => c.key)}
              origem={(c) => (caps[c.key]
                ? { propria: true, rotulo: 'definido no grupo', tone: 'info' }
                : { propria: false, rotulo: 'padrão', tone: 'muted' })}
              herdaria={(c) => c.default_policy}
              nomeDaHeranca={() => 'do padrão do catálogo'}
              onChange={(keys, valor) => setRascunhos((atual) => {
                const novo = { ...(atual[chave] ?? {}) };
                for (const k of keys) {
                  if (valor === null) delete novo[k];
                  else novo[k] = valor;
                }
                return { ...atual, [chave]: novo };
              })} />
          </div>
        </div>
      </div>
    </Dialog>
  );
}

/** Quanto a leitura do "começar a partir de" espera antes de mostrar a dica (M1 da leitura do #398). */
const DICA_DA_LEITURA_MS = 300;

const RANK: Record<PolicyName, number> = { disabled: 0, manual_only: 1, approval_required: 2, autonomous: 3 };
