/**
 * Perfil com contas em vários apps (itens 12.1/12.2). O perfil é a IDENTIDADE (persona, aparelho, memória); cada app
 * — Instagram, Outlook, TikTok, Facebook… — é uma conta dela, com sessão e senha próprias.
 *
 * - `AppSwitcher`: a fileira de apps acima das abas. Escolher um app filtra Memória e Interações para o que aconteceu
 *   NAQUELE app; "Todos" mostra a identidade inteira (inclusive os fatos gerais, que valem em qualquer app).
 * - `AbaContas`: uma linha por app, com sessão, senha e as ações que fazem sentido para cada tipo de login. Só o
 *   Instagram tem login automático hoje; nos demais, a pessoa entra pelo Foco e marca "entrei".
 */
import { AtSign, CheckCircle2, KeyRound, LogOut, Plus, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { InstagramProfile, ProfileAccount } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Field, Select, TextInput } from '../../components/Field';
import type { Tone } from '../../lib/status';
import { formatAgo, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import styles from './Profiles.module.css';

const SESSAO: Record<string, { label: string; tone: Tone }> = {
  session_ready: { label: 'Conectado', tone: 'success' },
  unknown: { label: 'Não verificada', tone: 'neutral' },
  logged_out: { label: 'Fora da conta', tone: 'warning' },
  auth_required: { label: 'Precisa entrar', tone: 'warning' },
  needs_person: { label: 'Precisa de uma pessoa', tone: 'warning' },
  auth_challenge: { label: 'Ação necessária', tone: 'warning' },
  wrong_account: { label: 'Conta errada', tone: 'danger' },
};

export function useContas(profileId: string): [ProfileAccount[] | null, () => Promise<void>] {
  const [contas, setContas] = useState<ProfileAccount[] | null>(null);
  const recarregar = useCallback(async () => {
    try {
      setContas(await api.listAccounts(profileId));
    } catch (e) {
      setContas([]);
      toastError('Não foi possível carregar as contas do perfil', e);
    }
  }, [profileId]);
  useEffect(() => {
    void recarregar();
  }, [recarregar]);
  return [contas, recarregar];
}

/** Fileira de apps do perfil. `null` = todos os apps (a identidade inteira). */
export function AppSwitcher({ contas, valor, onChange, onAdicionar }: {
  contas: ProfileAccount[];
  valor: string | null;
  onChange: (appId: string | null) => void;
  onAdicionar: () => void;
}) {
  return (
    <div className={styles.appSwitcher} role="group" aria-label="App do perfil">
      <span className={styles.appSwitcherLabel}>Apps deste perfil</span>
      <button type="button" className={styles.appChip} aria-pressed={valor === null} onClick={() => onChange(null)}>
        Todos
      </button>
      {contas.map((c) => (
        <button key={c.id} type="button" className={styles.appChip} aria-pressed={valor === c.app_id}
                title={c.handle ? `${c.app_name ?? c.app_id} · ${c.handle}` : undefined}
                onClick={() => onChange(c.app_id)}>
          <span className={styles.appChipDot} data-tone={SESSAO[c.session_status]?.tone ?? 'neutral'} aria-hidden />
          {c.app_name ?? c.app_id}
        </button>
      ))}
      <Button size="sm" variant="ghost" icon={Plus} onClick={onAdicionar}>Conta em outro app</Button>
    </div>
  );
}

export function AbaContas({ profile, contas, recarregar, abrirFormulario }: {
  profile: InstagramProfile;
  contas: ProfileAccount[] | null;
  recarregar: () => Promise<void>;
  abrirFormulario?: boolean;
}) {
  const apps = useAppStore((s) => s.apps);
  const now = useNow();
  const [adicionando, setAdicionando] = useState(!!abrirFormulario);
  const [novo, setNovo] = useState({ app_id: '', handle: '', login_identifier: '', password: '' });
  const [senhaDe, setSenhaDe] = useState<string | null>(null);
  const [senha, setSenha] = useState('');
  const [salvando, setSalvando] = useState(false);

  useEffect(() => {
    if (abrirFormulario) setAdicionando(true);
  }, [abrirFormulario]);

  const livres = apps.filter((a) => !(contas ?? []).some((c) => c.app_id === a.id));

  async function agir(fn: () => Promise<unknown>, ok: string, erro: string) {
    setSalvando(true);
    try {
      await fn();
      toast({ tone: 'success', title: ok });
      await recarregar();
    } catch (e) {
      toastError(erro, e);
    } finally {
      setSalvando(false);
    }
  }

  async function adicionar() {
    if (!novo.app_id) return;
    await agir(() => api.addAccount(profile.id, {
      app_id: novo.app_id, handle: novo.handle.trim(), login_identifier: novo.login_identifier.trim() || null,
      password: novo.password || null,
    }), 'Conta adicionada', 'Não foi possível adicionar a conta');
    setNovo({ app_id: '', handle: '', login_identifier: '', password: '' });
    setAdicionando(false);
  }

  async function remover(c: ProfileAccount) {
    const { confirmed } = await confirm({
      title: `Remover a conta de ${c.app_name ?? c.app_id}?`,
      body: 'A senha guardada no cofre também é apagada. Memória e histórico desse app continuam no perfil.',
      confirmLabel: 'Remover', danger: true,
    });
    if (!confirmed) return;
    await agir(() => api.deleteAccount(profile.id, c.id), 'Conta removida', 'Não foi possível remover a conta');
  }

  if (contas === null) return <p className={styles.detail}>Carregando…</p>;

  return (
    <div className={styles.configStack}>
      <Card>
        <CardHeader title="Contas deste perfil"
                    subtitle="Uma conta por app, todas da mesma pessoa, no mesmo aparelho. A senha vai cifrada para o cofre e nunca volta."
                    actions={livres.length ? (
                      <Button size="sm" icon={Plus} onClick={() => setAdicionando((v) => !v)}>
                        {adicionando ? 'Fechar' : 'Adicionar conta'}
                      </Button>
                    ) : undefined} />
        <CardBody>
          {adicionando ? (
            <div className={styles.accountForm}>
              <Field label="Aplicativo">
                {({ id }) => (
                  <Select id={id} value={novo.app_id} onChange={(e) => setNovo({ ...novo, app_id: e.target.value })}>
                    <option value="">Escolha…</option>
                    {livres.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </Select>
                )}
              </Field>
              <Field label="Usuário ou e-mail na conta">
                {({ id }) => <TextInput id={id} value={novo.handle} onChange={(e) => setNovo({ ...novo, handle: e.target.value })} />}
              </Field>
              <Field label="Identificador de login" unit="opcional">
                {({ id }) => <TextInput id={id} value={novo.login_identifier} placeholder="se for diferente do usuário"
                                        onChange={(e) => setNovo({ ...novo, login_identifier: e.target.value })} />}
              </Field>
              <Field label="Senha" unit="opcional">
                {({ id }) => <TextInput id={id} type="password" autoComplete="new-password" value={novo.password}
                                        onChange={(e) => setNovo({ ...novo, password: e.target.value })} />}
              </Field>
              <div className={styles.accountFormActions}>
                <Button loading={salvando} disabledReason={novo.app_id ? null : 'Escolha o aplicativo.'}
                        onClick={() => void adicionar()}>Adicionar</Button>
              </div>
            </div>
          ) : null}
          <ul className={styles.accountList}>
            {contas.map((c) => {
              const sessao = SESSAO[c.session_status] ?? { label: c.session_status, tone: 'neutral' as Tone };
              return (
                <li key={c.id} className={styles.accountRow}>
                  <div className={styles.accountMain}>
                    <strong>{c.app_name ?? c.app_id}</strong>
                    <span className={styles.accountHandle}><AtSign size={12} aria-hidden /> {c.handle || '—'}</span>
                    <div className={styles.accountBadges}>
                      <Badge size="sm" tone={sessao.tone}>{sessao.label}</Badge>
                      <Badge size="sm" tone={c.automated_login ? 'info' : 'neutral'}>
                        {c.automated_login ? 'login automático' : 'login pela pessoa (Foco)'}
                      </Badge>
                      <Badge size="sm" tone={c.credential_configured ? 'success' : 'muted'}>
                        {c.credential_configured ? 'senha guardada' : 'sem senha'}
                      </Badge>
                      {c.session_verified_at ? (
                        <span className={styles.muted}>conferida {formatAgo(c.session_verified_at, now)}</span>
                      ) : null}
                    </div>
                    {c.session_detail ? <p className={styles.detail}>{c.session_detail}</p> : null}
                  </div>
                  <div className={styles.accountActions}>
                    {c.automated_login ? (
                      <span className={styles.muted}>Conectar e verificar ficam na aba Autenticação.</span>
                    ) : (
                      <>
                        <Button size="sm" icon={CheckCircle2} disabled={salvando}
                                onClick={() => void agir(() => api.patchAccount(profile.id, c.id, { session_status: 'session_ready' }),
                                                         `Sessão de ${c.app_name ?? c.app_id} marcada como pronta`, 'Não foi possível marcar a sessão')}>
                          Entrei
                        </Button>
                        <Button size="sm" variant="ghost" icon={LogOut} disabled={salvando}
                                onClick={() => void agir(() => api.patchAccount(profile.id, c.id, { session_status: 'logged_out' }),
                                                         `Sessão de ${c.app_name ?? c.app_id} marcada como fora`, 'Não foi possível marcar a sessão')}>
                          Saí
                        </Button>
                      </>
                    )}
                    <Button size="sm" variant="ghost" icon={KeyRound} disabled={salvando}
                            onClick={() => { setSenhaDe(senhaDe === c.id ? null : c.id); setSenha(''); }}>
                      Senha
                    </Button>
                    {!c.automated_login ? (
                      <Button size="sm" variant="dangerGhost" icon={Trash2} iconOnly label={`Remover a conta de ${c.app_name ?? c.app_id}`}
                              disabled={salvando} onClick={() => void remover(c)} />
                    ) : null}
                  </div>
                  {senhaDe === c.id ? (
                    <div className={styles.accountPassword}>
                      <Field label={`Nova senha de ${c.app_name ?? c.app_id}`}>
                        {({ id }) => <TextInput id={id} type="password" autoComplete="new-password" value={senha}
                                                onChange={(e) => setSenha(e.target.value)} />}
                      </Field>
                      <Button size="sm" loading={salvando} disabledReason={senha ? null : 'Digite a senha.'}
                              onClick={() => void agir(async () => {
                                await api.setAccountCredential(profile.id, c.id, { password: senha, login_identifier: c.handle || null });
                                setSenha('');
                                setSenhaDe(null);
                              }, 'Senha guardada no cofre', 'Não foi possível guardar a senha')}>
                        Guardar
                      </Button>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </CardBody>
      </Card>
    </div>
  );
}
