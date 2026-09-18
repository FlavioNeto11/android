import { KeyRound, Plus, Smartphone, Trash2, UserRound } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { InstagramProfile, Persona, ProfileCreateRequest } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Dialog } from '../../components/Dialog';
import { EmptyState } from '../../components/EmptyState';
import { Field, Select, TextInput } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { toastError, toast } from '../../store/toasts';
import { SESSION_STATUS, metaOf } from '../../lib/status';
import { useAppStore } from '../../store/app';
import styles from './Profiles.module.css';

const VAZIO: ProfileCreateRequest = {
  username: '', first_name: '', last_name: '', birth_date: '', email: '',
  instance_id: '', persona_id: '', password: '',
};

/** A senha é write-only: ela sai deste formulário para o backend e nunca volta em resposta alguma. */
export function ProfilesPage() {
  const hydrated = useAppStore((s) => s.hydrated);
  const hydrateCount = useAppStore((s) => s.hydrateCount);
  const instances = useAppStore((s) => s.instanceOrder);
  const [profiles, setProfiles] = useState<InstagramProfile[] | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [editing, setEditing] = useState(false);
  const token = useRef(0);

  const load = useCallback(async () => {
    const mine = ++token.current;
    const [p, per] = await Promise.allSettled([api.listProfiles(), api.listPersonas()]);
    if (mine !== token.current) return;
    if (p.status === 'fulfilled') setProfiles(p.value);
    else {
      setProfiles([]);
      toastError('Não foi possível carregar os perfis', p.reason);
    }
    if (per.status === 'fulfilled') setPersonas(per.value);
  }, []);

  // Recarrega a cada novo snapshot (reconexão): perfis não vêm no snapshot nem em eventos.
  useEffect(() => {
    void load();
  }, [load, hydrateCount]);

  if (!hydrated || profiles === null) {
    return (
      <LoadingRegion label="Carregando perfis…">
        <Skeleton height={140} />
      </LoadingRegion>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <h2 className={styles.title}>Perfis do Instagram</h2>
          <p className={styles.lead}>
            Cada perfil tem credencial própria, guardada cifrada, e um aparelho vinculado. A senha é digitada aqui e
            nunca volta: o painel só mostra que existe.
          </p>
        </div>
        <Button icon={Plus} onClick={() => setEditing(true)}>Novo perfil</Button>
      </div>

      {profiles.length === 0 ? (
        <EmptyState
          icon={UserRound}
          title="Nenhum perfil cadastrado"
          hint="Cadastre um perfil com o usuário do Instagram, a senha e o aparelho que vai usá-lo."
          actions={<Button icon={Plus} onClick={() => setEditing(true)}>Novo perfil</Button>}
        >
          Nenhum perfil foi cadastrado ainda.
        </EmptyState>
      ) : (
        <div className={styles.grid}>
          {profiles.map((p) => (
            <ProfileCard key={p.id} profile={p} onChanged={load} />
          ))}
        </div>
      )}

      {editing ? (
        <ProfileEditor
          personas={personas}
          instances={instances}
          usados={profiles.map((p) => p.instance_id).filter(Boolean) as string[]}
          onClose={() => setEditing(false)}
          onSaved={async () => {
            setEditing(false);
            await load();
          }}
        />
      ) : null}
    </div>
  );
}

function ProfileCard({ profile, onChanged }: { profile: InstagramProfile; onChanged: () => Promise<void> }) {
  const sess = metaOf(SESSION_STATUS, profile.session.status);
  const [busy, setBusy] = useState(false);

  async function remover() {
    const ok = await confirm({
      title: `Remover @${profile.username}?`,
      body: 'A credencial guardada no cofre também é apagada. Persona, memória e histórico deste perfil vão junto.',
      confirmLabel: 'Remover',
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api.deleteProfile(profile.id);
      toast({ tone: 'success', title: `@${profile.username} removido` });
      await onChanged();
    } catch (e) {
      toastError('Não foi possível remover o perfil', e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={`@${profile.username}`}
        subtitle={profile.display_name ?? undefined}
        actions={
          <Button size="sm" variant="dangerGhost" icon={Trash2} iconOnly label="Remover perfil"
                  loading={busy} onClick={remover} />
        }
      />
      <CardBody>
        <dl className={styles.rows}>
          <div className={styles.row}>
            <dt><Smartphone size={14} aria-hidden /> Aparelho</dt>
            <dd>{profile.instance_id ?? <span className={styles.muted}>não vinculado</span>}</dd>
          </div>
          <div className={styles.row}>
            <dt><KeyRound size={14} aria-hidden /> Senha</dt>
            <dd>
              {profile.credential.configured ? (
                <span className={styles.mask} title="A senha nunca é devolvida pela API">••••••••••••</span>
              ) : (
                <span className={styles.muted}>não configurada</span>
              )}
            </dd>
          </div>
          <div className={styles.row}>
            <dt>Sessão</dt>
            <dd><StatusBadge meta={sess} /></dd>
          </div>
          {profile.session.observed_username ? (
            <div className={styles.row}>
              <dt>Conta observada</dt>
              <dd>@{profile.session.observed_username}</dd>
            </div>
          ) : null}
          {profile.persona_name ? (
            <div className={styles.row}>
              <dt>Persona</dt>
              <dd><Badge>{profile.persona_name}</Badge></dd>
            </div>
          ) : null}
        </dl>
        {profile.session.detail ? <p className={styles.detail}>{profile.session.detail}</p> : null}
      </CardBody>
    </Card>
  );
}

function ProfileEditor({ personas, instances, usados, onClose, onSaved }: {
  personas: Persona[];
  instances: string[];
  usados: string[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<ProfileCreateRequest>(VAZIO);
  const [erros, setErros] = useState<Record<string, string>>({});
  const [salvando, setSalvando] = useState(false);

  function set<K extends keyof ProfileCreateRequest>(k: K, v: ProfileCreateRequest[K]) {
    setDraft((d) => ({ ...d, [k]: v }));
  }

  function validar(): boolean {
    const e: Record<string, string> = {};
    const user = (draft.username ?? '').trim().replace(/^@/, '');
    if (!/^[A-Za-z0-9._]{1,30}$/.test(user)) e.username = 'Use letras, números, ponto ou sublinhado (até 30).';
    if (!draft.password) e.password = 'Informe a senha; ela vai cifrada para o cofre e nunca volta.';
    if (!draft.instance_id) e.instance_id = 'Escolha o aparelho que vai usar este perfil.';
    setErros(e);
    return Object.keys(e).length === 0;
  }

  async function salvarEConectar() {
    if (!validar()) return;
    setSalvando(true);
    try {
      const limpo: ProfileCreateRequest = {
        ...draft,
        username: (draft.username ?? '').trim().replace(/^@/, ''),
        first_name: draft.first_name || null,
        last_name: draft.last_name || null,
        birth_date: draft.birth_date || null,
        email: draft.email || null,
        persona_id: draft.persona_id || null,
      };
      const criado = await api.createProfile(limpo);
      toast({
        tone: 'success',
        title: `@${criado.username} cadastrado`,
        message: 'Credencial guardada cifrada. A conexão automática com o Instagram entra na próxima etapa.',
      });
      await onSaved();
    } catch (e) {
      toastError('Não foi possível cadastrar o perfil', e);
    } finally {
      setSalvando(false);
    }
  }

  const livres = instances.filter((i) => !usados.includes(i));

  return (
    <Dialog
      open
      onClose={onClose}
      title="Novo perfil Instagram"
      icon={UserRound}
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Cancelar</Button>
          <Button variant="primary" loading={salvando} onClick={salvarEConectar}>Salvar e conectar</Button>
        </>
      }
    >
      <div className={styles.form}>
        <Field label="Usuário do Instagram" error={erros.username} hint="Sem o @; é ele que será verificado na tela.">
          {({ id, describedBy, invalid }) => (
            <TextInput id={id} aria-describedby={describedBy} invalid={invalid} value={draft.username ?? ''}
                       placeholder="mariana.costa91182" onChange={(e) => set('username', e.target.value)} />
          )}
        </Field>
        <div className={styles.pair}>
          <Field label="Nome">
            {({ id }) => (
              <TextInput id={id} value={draft.first_name ?? ''} onChange={(e) => set('first_name', e.target.value)} />
            )}
          </Field>
          <Field label="Sobrenome">
            {({ id }) => (
              <TextInput id={id} value={draft.last_name ?? ''} onChange={(e) => set('last_name', e.target.value)} />
            )}
          </Field>
        </div>
        <div className={styles.pair}>
          <Field label="Nascimento" unit="opcional">
            {({ id }) => (
              <TextInput id={id} value={draft.birth_date ?? ''} placeholder="1991-08-22"
                         onChange={(e) => set('birth_date', e.target.value)} />
            )}
          </Field>
          <Field label="E-mail" unit="opcional">
            {({ id }) => (
              <TextInput id={id} value={draft.email ?? ''} onChange={(e) => set('email', e.target.value)} />
            )}
          </Field>
        </div>
        <Field label="Senha" error={erros.password}
               hint="Vai cifrada para o cofre e nunca é devolvida. Nem o painel nem a IA veem o valor.">
          {({ id, describedBy, invalid }) => (
            <TextInput id={id} type="password" autoComplete="new-password" aria-describedby={describedBy}
                       invalid={invalid} value={draft.password ?? ''}
                       onChange={(e) => set('password', e.target.value)} />
          )}
        </Field>
        <div className={styles.pair}>
          <Field label="Aparelho" error={erros.instance_id}
                 hint="Um perfil por aparelho, e um aparelho por perfil.">
            {({ id, describedBy, invalid }) => (
              <Select id={id} aria-describedby={describedBy} invalid={invalid} value={draft.instance_id ?? ''}
                      onChange={(e) => set('instance_id', e.target.value)}>
                <option value="">Escolha…</option>
                {livres.map((i) => <option key={i} value={i}>{i}</option>)}
              </Select>
            )}
          </Field>
          <Field label="Persona" unit="opcional">
            {({ id }) => (
              <Select id={id} value={draft.persona_id ?? ''} onChange={(e) => set('persona_id', e.target.value)}>
                <option value="">Nenhuma</option>
                {personas.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </Select>
            )}
          </Field>
        </div>
      </div>
    </Dialog>
  );
}
