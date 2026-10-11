/**
 * Guia "Contas e acesso" (evolução 2, onda E1; ADR-040): funde as antigas guias Contas e Autenticação, que gravavam
 * a MESMA senha em dois lugares e mostravam a sessão em quatro. Uma linha por conta — app (ou site, no navegador),
 * handle, identificador de login, senha só de escrita com CONSENTIMENTO, sessão, Conectar/Verificar/Sair e as
 * tentativas — tudo pelas rotas POR CONTA (`…/accounts/{aid}/…`), com os botões gateados por `session_actions`.
 */
import { AtSign, CheckCircle2, ClipboardList, Globe, KeyRound, LogOut, Mail, PlugZap, Plus, ScanEye, ShieldCheck, Trash2 } from 'lucide-react';
import { Fragment, useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { AppCatalogEntry, AuthAttempt, PersonaDevice, ProfileAccount } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Disclosure } from '../../components/Disclosure';
import { Checkbox, Field, Select, TextInput } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { StatusBadge } from '../../components/StatusBadge';
import { type LoadError, LoadErrorState, toLoadError } from '../../lib/loadError';
import { ACCOUNT_SESSION_STATUS, metaOf } from '../../lib/status';
import { tempoRelativo, formatDateTime, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import { PrepararConta, ProvisionamentoDaConta } from './ContaPlanejada';
import { TEXTO_DO_CONSENTIMENTO, ehNavegador, emPreparo, provisionamentoDe } from './provisionamento';
import { aparelhosDe, ehEndereco, handleDe, type Pessoa } from './pessoa';
import { SESSION_PHASE_LABEL, accountGateReason } from './sessionGate';
import { CicloDaConta } from './CicloDaConta';
import { MotivoDoBloqueio } from './MotivoDoBloqueio';
import styles from './Profiles.module.css';

// Regra de usuário do app âncora (hoje sempre o Instagram: letras, números, ponto ou sublinhado, até 30). O NOME
// do app não decide mais nada aqui (23.10): é `profile_anchor` (`GET /api/app-catalog`) que diz QUAL app é a
// conta de cadastro da persona; a regra em si segue a do Instagram porque, por ora, é o único app que pode ser
// âncora (`pacote_ancora()` recusa mais de um registrado).
const USUARIO_DA_CONTA_ANCORA = /^[A-Za-z0-9._]{1,30}$/;

/** O app da conta de CADASTRO da persona: o que o registro do backend declara `profile_anchor` (23.10, sem
 *  comparar nome ou pacote no cliente — antes era `ehInstagram` fixo em `com.instagram.android`). */
function ehAncora(app: { package?: string | null }, catalogo: readonly AppCatalogEntry[]): boolean {
  return catalogo.some((c) => c.package === app.package && c.profile_anchor);
}

function nomeDoApp(c: ProfileAccount): string {
  return c.app_name ?? c.app_id;
}

function temSenha(c: ProfileAccount): boolean {
  return c.credential?.configured ?? c.credential_configured;
}

/** Como a conta de origem aparece na escolha "usar a senha de outra conta": app, site e usuário, nunca a senha. */
function rotuloDaConta(c: ProfileAccount): string {
  return `${nomeDoApp(c)}${c.host ? ` (${c.host})` : ''}${c.handle ? ` — ${c.handle}` : ''}`;
}

const AVISO_DO_CLONE = 'O cofre copia a senha para esta conta sem que ela passe pelo painel. A autorização para a '
  + 'automação digitá-la não vem junto: dê a desta conta depois de conferir.';

export function AbaContasEAcesso({ profile, contas, erro = null, recarregar, onChanged, abrirFormulario }: {
  profile: Pessoa;
  contas: ProfileAccount[] | null;
  erro?: LoadError | null;
  recarregar: () => Promise<void>;
  onChanged: () => Promise<void>;
  abrirFormulario?: boolean;
}) {
  const [adicionando, setAdicionando] = useState(!!abrirFormulario);
  // Preparar uma conta que ainda NÃO existe no serviço (ADR-087): outro caminho, outro formulário.
  const [preparando, setPreparando] = useState(false);
  // De que app é a conta de cadastro (23.10): o registro decide, o formulário só pergunta (`GET /api/app-catalog`).
  const [catalogo, setCatalogo] = useState<AppCatalogEntry[] | null>(null);
  // A falha do catálogo NÃO vira catálogo vazio: sem ele nenhum app seria o âncora, o Instagram de uma persona sem @
  // sairia pela rota da conta solta (`POST …/accounts`) e não pela adoção. O formulário espera o catálogo.
  const [erroCatalogo, setErroCatalogo] = useState<LoadError | null>(null);
  const [tentativa, setTentativa] = useState(0);

  useEffect(() => {
    if (abrirFormulario) setAdicionando(true);
  }, [abrirFormulario]);

  useEffect(() => {
    let vivo = true;
    setErroCatalogo(null);
    api.listAppCatalog()
      .then((c) => { if (vivo) setCatalogo(c); })
      .catch((e: unknown) => { if (vivo) setErroCatalogo(toLoadError(e)); });
    return () => { vivo = false; };
  }, [tentativa]);

  if (contas === null) {
    return erro
      ? <LoadErrorState what="as contas da persona" error={erro} onRetry={() => void recarregar()} />
      : <LoadingRegion label="Carregando as contas…"><Skeleton height={80} /></LoadingRegion>;
  }

  const semCadastro = !handleDe(profile);
  // Mudou conta, senha ou sessão: relê as contas E a persona (o cartão da lista e o cabeçalho mostram o @ e a fase).
  const mudou = async () => {
    await recarregar();
    await onChanged();
  };

  return (
    <div className={styles.configStack}>
      <MotivoDoBloqueio profileId={profile.id} retiradas={profile.contas_retiradas} />
      <Card>
        <CardHeader title="Contas e acesso"
                    subtitle={'Uma conta por app (ou por site, no navegador), todas desta pessoa. A senha vai cifrada '
                      + 'para o cofre, nunca volta, e só é digitada pela automação com a sua autorização — no app ou '
                      + 'no site daquela conta.'}
                    actions={
                      <>
                        <Button size="sm" variant="secondary" icon={ClipboardList} onClick={() => setPreparando((v) => !v)}>
                          {preparando ? 'Fechar o preparo' : 'Preparar conta nova'}
                        </Button>
                        <Button size="sm" icon={Plus} onClick={() => setAdicionando((v) => !v)}>
                          {adicionando ? 'Fechar' : 'Adicionar conta'}
                        </Button>
                      </>
                    } />
        <CardBody>
          {semCadastro ? (
            <p className={styles.detail}>
              Esta persona ainda não tem @ de cadastro. Para ganhar um, adicione a conta do
              {' '}{catalogo?.find((c) => c.profile_anchor)?.name ?? 'app âncora'} dela.
            </p>
          ) : null}
          {preparando ? (
            <PrepararConta profileId={profile.id} contas={contas}
                           onFeita={async () => { setPreparando(false); await mudou(); }}
                           onFechar={() => setPreparando(false)} />
          ) : null}
          {adicionando ? (
            erroCatalogo ? (
              <LoadErrorState what="os aplicativos" error={erroCatalogo} compact
                              onRetry={() => setTentativa((n) => n + 1)} />
            ) : catalogo === null ? (
              <LoadingRegion label="Carregando os aplicativos…"><Skeleton height={80} /></LoadingRegion>
            ) : (
              <NovaConta profile={profile} contas={contas} catalogo={catalogo}
                         onCriada={async () => { setAdicionando(false); await mudou(); }} />
            )
          ) : null}
          {contas.length === 0 ? (
            <p className={styles.detail}>Nenhuma conta ainda.</p>
          ) : (
            <ul className={styles.accountList}>
              {contas.map((c) => (
                <Fragment key={c.id}>
                  <CartaoConta profileId={profile.id} conta={c} onMudou={mudou}
                               aparelhos={aparelhosDe(profile)} principal={profile.instance_id}
                               origens={contas.filter((o) => o.id !== c.id && temSenha(o))} />
                  {/* 31.346: o ciclo só existe para a conta do app âncora (Instagram); o servidor responde 404 às demais. */}
                  {ehAncora(c, catalogo ?? []) ? <li className={styles.cicloDaConta}><CicloDaConta accountId={c.id} /></li> : null}
                </Fragment>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

/**
 * Adicionar conta. Para o Instagram de uma pessoa SEM @, o caminho é a adoção (`POST /instagram/profiles` com
 * `persona_id`): é ela que ganha o @, na mesma linha e com o mesmo id; `POST …/accounts` criaria só uma conta solta,
 * sem @ de cadastro. A senha, quando vier, entra depois pela rota da conta, com o consentimento marcado aqui.
 */
function NovaConta({ profile, contas, catalogo, onCriada }: {
  profile: Pessoa;
  contas: ProfileAccount[];
  /** `GET /api/app-catalog` (23.10): diz qual app é a conta de cadastro (`profile_anchor`), sem o formulário
   *  precisar comparar nome ou pacote. */
  catalogo: readonly AppCatalogEntry[];
  onCriada: () => Promise<void>;
}) {
  const apps = useAppStore((s) => s.apps);
  const [appId, setAppId] = useState('');
  const [handle, setHandle] = useState('');
  const [host, setHost] = useState('');
  const [login, setLogin] = useState('');
  const [senha, setSenha] = useState('');
  const [consentiu, setConsentiu] = useState(false);
  // 23.9 (ADR-057): a senha pode vir de outra conta desta persona, clonada no cofre. É uma OU outra.
  const [clonarDe, setClonarDe] = useState('');
  const [salvando, setSalvando] = useState(false);

  const temCadastro = !!handleDe(profile);
  const origens = contas.filter(temSenha);
  const livres = apps.filter((a) => ehNavegador(a) || !contas.some((c) => c.app_id === a.id))
    .filter((a) => !(ehAncora(a, catalogo) && temCadastro));
  const app = apps.find((a) => a.id === appId) ?? null;
  const adocao = !!app && ehAncora(app, catalogo) && !temCadastro;
  const navegador = !!app && ehNavegador(app);
  // A adoção cria o @ de cadastro por outra rota; a clonagem fica para as contas comuns.
  const clonando = !adocao && !!clonarDe;
  // Nome do app âncora para as mensagens ("Usuário do X"): do catálogo, nunca um literal escrito aqui.
  const nomeDaAncora = catalogo.find((c) => c.profile_anchor)?.name ?? app?.name ?? 'aplicativo';

  const motivo = !app ? 'Escolha o aplicativo.'
    : adocao && !USUARIO_DA_CONTA_ANCORA.test(handle.trim().replace(/^@/, ''))
      ? `Usuário do ${nomeDaAncora}: letras, números, ponto ou sublinhado (até 30).`
      : navegador && !host.trim() ? 'Diga o site desta conta (ex.: portal.exemplo.com.br).'
        : senha && !clonando && !consentiu ? 'Marque a autorização para a automação digitar esta senha.' : null;

  async function adicionar() {
    if (!app || motivo || salvando) return;
    setSalvando(true);
    try {
      if (adocao) {
        const criado = await api.createProfile({ username: handle.trim().replace(/^@/, ''), persona_id: profile.id });
        if (senha) {
          try {
            const conta = (await api.listAccounts(criado.id)).find((c) => c.app_id === app.id);
            if (!conta) throw new Error(`A conta do ${nomeDaAncora} não apareceu depois do cadastro.`);
            await api.setAccountCredential(criado.id, conta.id,
              { password: senha, consent: true, login_identifier: login.trim() || null });
          } catch (e) {
            toastError(`@${criado.username} foi cadastrado, mas a senha não foi guardada`, e);
          }
        }
        toast({ tone: 'success', title: `@${criado.username} é agora o ${nomeDaAncora} desta persona` });
      } else {
        await api.addAccount(profile.id, {
          app_id: app.id,
          handle: handle.trim(),
          host: navegador ? host.trim() : null,
          login_identifier: login.trim() || null,
          // Clonar exclui senha e consentimento: o servidor recusa a combinação (422), e a conta nasce sem autorização.
          ...(clonando ? { clonar_de: clonarDe } : { password: senha || null, ...(senha ? { consent: consentiu } : {}) }),
        });
        toast({ tone: 'success', title: `Conta em ${app.name} adicionada`,
                ...(clonando ? { message: 'Senha clonada no cofre. Autorize a automação a digitá-la no cartão da conta.' } : {}) });
      }
      await onCriada();
    } catch (e) {
      toastError('Não foi possível adicionar a conta', e);
    } finally {
      // A senha passa por aqui a caminho do cofre e não fica no formulário, dê certo ou não.
      setSenha('');
      setSalvando(false);
    }
  }

  return (
    <div className={styles.accountForm}>
      <Field label="Aplicativo">
        {({ id }) => (
          <Select id={id} value={appId} onChange={(e) => setAppId(e.target.value)}>
            <option value="">Escolha…</option>
            {livres.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
        )}
      </Field>
      <Field label={adocao ? `Usuário do ${nomeDaAncora}` : 'Usuário na conta'}
             hint={adocao ? 'Sem o @; vira o @ de cadastro desta persona.' : undefined}>
        {({ id, describedBy }) => (
          <TextInput id={id} aria-describedby={describedBy} value={handle} onChange={(e) => setHandle(e.target.value)} />
        )}
      </Field>
      {navegador ? (
        <Field label="Site" hint="Onde a senha pode ser digitada: só neste site (e subdomínios).">
          {({ id, describedBy }) => (
            <TextInput id={id} aria-describedby={describedBy} value={host} placeholder="portal.exemplo.com.br"
                       onChange={(e) => setHost(e.target.value)} />
          )}
        </Field>
      ) : null}
      <Field label="Identificador de login" unit="opcional"
             hint="Com que a conta entra (e-mail ou usuário). Vazio: o sistema decide (no Instagram, o e-mail da persona).">
        {({ id, describedBy }) => (
          <TextInput id={id} aria-describedby={describedBy} value={login} onChange={(e) => setLogin(e.target.value)} />
        )}
      </Field>
      {!adocao && origens.length > 0 ? (
        <Field label="Usar a senha de outra conta" unit="opcional" hint={AVISO_DO_CLONE}>
          {({ id, describedBy }) => (
            <Select id={id} aria-describedby={describedBy} value={clonarDe}
                    onChange={(e) => { setClonarDe(e.target.value); setSenha(''); }}>
              <option value="">Não: digitar a senha abaixo</option>
              {origens.map((o) => <option key={o.id} value={o.id}>{rotuloDaConta(o)}</option>)}
            </Select>
          )}
        </Field>
      ) : null}
      {clonando ? null : (
        <Field label="Senha" unit="opcional">
          {({ id }) => (
            <TextInput id={id} type="password" autoComplete="new-password" value={senha}
                       onChange={(e) => setSenha(e.target.value)} />
          )}
        </Field>
      )}
      {senha && !clonando ? (
        <Checkbox label={TEXTO_DO_CONSENTIMENTO} aria-label={TEXTO_DO_CONSENTIMENTO} checked={consentiu}
                  onChange={(e) => setConsentiu(e.target.checked)} />
      ) : null}
      <div className={styles.accountFormActions}>
        <Button loading={salvando} disabledReason={motivo} onClick={() => void adicionar()}>Adicionar</Button>
      </div>
    </div>
  );
}

/**
 * Os aparelhos em que ESTA conta age (v0.29): os vínculos do app dela e os sem app (que servem à conta de cadastro),
 * sem repetir, o principal primeiro. É deles que se escolhe onde conectar, verificar e sair.
 */
function aparelhosDaConta(c: ProfileAccount, aparelhos: readonly PersonaDevice[]): PersonaDevice[] {
  const vistos = new Set<string>();
  return aparelhos
    .filter((d) => d.app_id === c.app_id || d.app_id === null)
    .sort((a, b) => Number(b.is_primary) - Number(a.is_primary))
    .filter((d) => (vistos.has(d.instance_id) ? false : (vistos.add(d.instance_id), true)));
}

function CartaoConta({ profileId, conta: c, onMudou, aparelhos = [], principal = null, origens = [] }: {
  profileId: string;
  conta: ProfileAccount;
  onMudou: () => Promise<void>;
  /** As OUTRAS contas desta persona com senha guardada: de onde esta pode clonar a sua (23.9). */
  origens?: readonly ProfileAccount[];
  /** Os vínculos da persona (N:N): com mais de um que sirva a esta conta, a pessoa escolhe o aparelho. */
  aparelhos?: readonly PersonaDevice[];
  principal?: string | null;
}) {
  const now = useNow();
  const [busy, setBusy] = useState(false);
  const daConta = aparelhosDaConta(c, aparelhos);
  const escolhe = daConta.length > 1;
  const [aparelho, setAparelho] = useState<string>(principal ?? daConta[0]?.instance_id ?? '');
  // Os portões (`session_actions`) são calculados para o PRINCIPAL. Noutro aparelho, quem decide é a rota — que
  // recusa com o motivo —, e a tela não finge saber.
  const noPrincipal = !escolhe || aparelho === principal;
  const sessaoAli = escolhe && !noPrincipal ? daConta.find((d) => d.instance_id === aparelho)?.session ?? null : null;
  const portao = (verbo: 'connect' | 'verify' | 'logout') => (noPrincipal ? accountGateReason(c, verbo) : null);
  const status = c.session?.status ?? c.session_status;
  const fase = c.session_actions ? SESSION_PHASE_LABEL[c.session_actions.phase] : null;
  const pronto = status === 'session_ready';
  const preparo = emPreparo(c);
  const desejado = provisionamentoDe(c).desired_handle;
  const observada = c.session?.observed_username ?? null;
  const detalhe = c.session?.detail ?? c.session_detail;
  const conferida = c.session?.verified_at ?? c.session_verified_at;

  async function agir(fn: () => Promise<unknown>, ok: { title: string; message?: string }, erro: string) {
    setBusy(true);
    try {
      await fn();
      toast({ tone: 'success', ...ok });
      await onMudou();
    } catch (e) {
      toastError(erro, e);
    } finally {
      setBusy(false);
    }
  }

  async function sessao(verbo: 'connect' | 'verify' | 'logout') {
    if (verbo === 'logout' && !(await confirm({
      title: escolhe ? `Sair da conta de ${nomeDoApp(c)} em ${aparelho}?` : `Sair da conta de ${nomeDoApp(c)} neste aparelho?`,
      body: 'Os dados do app são apagados; a sessão precisará ser refeita com a senha.',
      confirmLabel: 'Sair da conta', danger: true,
    })).confirmed) return;
    setBusy(true);
    try {
      // `?instance_id=` só quando há escolha: com um aparelho só, a rota usa o principal, como sempre.
      await api.accountSession(profileId, c.id, verbo, escolhe ? aparelho : null);
      toast({ tone: 'info', title: 'Pedido aceito', message: 'O aparelho está sendo usado agora; o estado da sessão aparece aqui em instantes.' });
      // 202: o trabalho roda no aparelho. Relê algumas vezes enquanto o estado muda.
      for (const espera of [2000, 4000, 8000]) setTimeout(() => void onMudou(), espera);
    } catch (e) {
      toastError('Não foi possível executar', e);
    } finally {
      setBusy(false);
    }
  }

  async function remover() {
    const { confirmed } = await confirm({
      title: `Remover a conta de ${nomeDoApp(c)}?`,
      body: 'A senha guardada no cofre também é apagada. Memória e histórico desse app continuam na persona.',
      confirmLabel: 'Remover', danger: true,
    });
    if (!confirmed) return;
    await agir(() => api.deleteAccount(profileId, c.id), { title: 'Conta removida' }, 'Não foi possível remover a conta');
  }

  return (
    <li className={styles.accountRow}>
      <div className={styles.accountMain}>
        <strong>{nomeDoApp(c)}</strong>
        {/* O identificador de um app de e-mail É um endereço: o "@" de nome de usuário na frente dele lia-se
            "@ fulano@dominio" (cartão do Outlook, 30/09). Endereço ganha o ícone de carta; usuário, o arroba. */}
        <span className={styles.accountHandle}>
          {ehEndereco(c.handle || desejado) ? <Mail size={12} aria-hidden /> : <AtSign size={12} aria-hidden />}
          {' '}{c.handle || (preparo && desejado ? `${desejado} (desejado)` : '—')}
        </span>
        {c.host ? <span className={styles.accountHandle}><Globe size={12} aria-hidden /> {c.host}</span> : null}
        <div className={styles.accountBadges}>
          {preparo ? null : <StatusBadge meta={metaOf(ACCOUNT_SESSION_STATUS, status)} size="sm" />}
          {c.session?.stale ? <Badge size="sm" tone="warning">dado velho — relido antes da próxima tarefa</Badge> : null}
          <Badge size="sm" tone={c.automated_login ? 'info' : 'neutral'}>
            {c.automated_login ? 'login automático' : 'login pela pessoa (Foco)'}
          </Badge>
          {fase ? <Badge size="sm" tone={fase.tone}>{fase.label}</Badge> : null}
          {conferida ? <span className={styles.muted}>conferida {tempoRelativo(conferida, now)}</span> : null}
        </div>
        {observada ? <p className={styles.detail}>Conta observada na tela: @{observada}</p> : null}
        {escolhe ? (
          <div className={styles.accountBadges}>
            <Select small aria-label={`Aparelho para conectar, verificar e sair (${nomeDoApp(c)})`} value={aparelho}
                    onChange={(e) => setAparelho(e.target.value)}>
              {daConta.map((d) => (
                <option key={d.instance_id} value={d.instance_id}>
                  {d.instance_id}{d.instance_id === principal ? ' (principal)' : ''}
                </option>
              ))}
            </Select>
            {sessaoAli ? (
              <StatusBadge meta={metaOf(ACCOUNT_SESSION_STATUS, sessaoAli.status)} size="sm" srPrefix={`Sessão em ${aparelho}`} />
            ) : null}
            {!noPrincipal ? <span className={styles.muted}>fora do principal: o servidor confere antes de agir</span> : null}
          </div>
        ) : null}
        {c.session_actions?.detail ? <p className={styles.detail}>{c.session_actions.detail}</p> : null}
        {detalhe ? <p className={styles.detail}>{detalhe}</p> : null}
      </div>
      <div className={styles.accountActions}>
        {preparo ? null : c.automated_login ? (
          <>
            <Button size="sm" variant={pronto ? 'secondary' : 'primary'} icon={PlugZap} loading={busy}
                    disabledReason={portao('connect')} onClick={() => void sessao('connect')}>
              {pronto ? 'Reconectar' : 'Conectar'}
            </Button>
            <Button size="sm" variant="ghost" icon={ScanEye} loading={busy}
                    disabledReason={portao('verify')} onClick={() => void sessao('verify')}>
              Verificar conta
            </Button>
            <Button size="sm" variant="ghost" icon={LogOut} loading={busy}
                    disabledReason={portao('logout')} onClick={() => void sessao('logout')}>
              Sair da conta
            </Button>
          </>
        ) : (
          <>
            {/* Sem login automático, quem entra é a pessoa, pelo Foco; aqui ela só diz o que fez. */}
            <Button size="sm" icon={CheckCircle2} disabled={busy}
                    onClick={() => void agir(() => api.patchAccount(profileId, c.id, { session_status: 'session_ready' }),
                                             { title: `Sessão de ${nomeDoApp(c)} marcada como pronta` }, 'Não foi possível marcar a sessão')}>
              Entrei
            </Button>
            <Button size="sm" variant="ghost" icon={LogOut} disabled={busy}
                    onClick={() => void agir(() => api.patchAccount(profileId, c.id, { session_status: 'auth_required' }),
                                             { title: `Sessão de ${nomeDoApp(c)} marcada como fora` }, 'Não foi possível marcar a sessão')}>
              Saí
            </Button>
            <Button size="sm" variant="dangerGhost" icon={Trash2} iconOnly label={`Remover a conta de ${nomeDoApp(c)}`}
                    disabled={busy} onClick={() => void remover()} />
          </>
        )}
      </div>
      {preparo ? (
        <ProvisionamentoDaConta profileId={profileId} conta={c} onMudou={onMudou} origens={origens} />
      ) : (
        <SenhaDaConta profileId={profileId} conta={c} onMudou={onMudou} origens={origens} />
      )}
      {c.automated_login && !preparo ? (
        <div className={styles.accountWide}>
          <Disclosure bare summary="Tentativas de entrar">
            {() => <Tentativas profileId={profileId} accountId={c.id} />}
          </Disclosure>
        </div>
      ) : null}
    </li>
  );
}

/**
 * Identificador de login e senha da conta. A senha é só de escrita: o campo nasce vazio e esvazia depois de cada
 * envio, dê certo ou não. O identificador mostra o que está GRAVADO — nunca o handle por padrão (o painel antigo
 * mandava o @ como login e o Instagram, que entra por e-mail, recusava).
 */
function SenhaDaConta({ profileId, conta: c, onMudou, origens = [] }: {
  profileId: string;
  conta: ProfileAccount;
  onMudou: () => Promise<void>;
  origens?: readonly ProfileAccount[];
}) {
  const gravado = c.credential?.login_identifier ?? c.login_identifier ?? null;
  const [login, setLogin] = useState(gravado ?? '');
  const [senha, setSenha] = useState('');
  const [consentiu, setConsentiu] = useState(false);
  const [clonarDe, setClonarDe] = useState('');
  const [salvando, setSalvando] = useState(false);
  const configurada = temSenha(c);
  const consentidaEm = c.credential?.consent_at ?? c.consent_at ?? null;
  const app = nomeDoApp(c);

  useEffect(() => {
    setLogin(gravado ?? '');
  }, [gravado]);

  const motivo = !senha ? 'Digite a senha primeiro.'
    : !consentidaEm && !consentiu ? 'Marque a autorização para a automação digitar esta senha.' : null;

  async function salvar() {
    // O botão bloqueia o clique, mas Enter no campo envia o formulário mesmo assim: a guarda fica aqui também.
    if (motivo || salvando) return;
    setSalvando(true);
    try {
      const identificador = login.trim();
      await api.setAccountCredential(profileId, c.id, {
        password: senha,
        // Conta que já consentiu troca a senha sem remarcar: o servidor preserva o consentimento dado.
        ...(consentidaEm ? {} : { consent: true }),
        ...(identificador && identificador !== gravado ? { login_identifier: identificador } : {}),
      });
      toast({ tone: 'success', title: configurada ? 'Senha trocada' : 'Senha guardada',
              message: 'Cifrada no cofre. Ela não volta mais para o painel.' });
      await onMudou();
    } catch (e) {
      toastError('Não foi possível guardar a senha', e);
    } finally {
      setSenha('');
      setSalvando(false);
    }
  }

  async function clonar() {
    const origem = origens.find((o) => o.id === clonarDe);
    if (!origem || salvando) return;
    if (configurada && !(await confirm({
      title: `Trocar a senha de ${app} pela de ${rotuloDaConta(origem)}?`,
      body: 'A senha atual desta conta é substituída no cofre. As duas continuam independentes: trocar ou apagar uma '
        + 'depois não mexe na outra.',
      confirmLabel: 'Usar esta senha',
    })).confirmed) return;
    setSalvando(true);
    try {
      const identificador = login.trim();
      await api.cloneAccountCredential(profileId, c.id, {
        clonar_de: origem.id,
        ...(identificador && identificador !== gravado ? { login_identifier: identificador } : {}),
      });
      toast({ tone: 'success', title: `Senha de ${rotuloDaConta(origem)} clonada`,
              message: consentidaEm ? 'Cifrada no cofre, numa entrada só desta conta.'
                : 'Cifrada no cofre, numa entrada só desta conta. Autorize a automação a digitá-la para usá-la.' });
      setClonarDe('');
      await onMudou();
    } catch (e) {
      toastError('Não foi possível usar a senha da outra conta', e);
    } finally {
      setSalvando(false);
    }
  }

  async function consentirSemRedigitar() {
    setSalvando(true);
    try {
      await api.consentAccountCredential(profileId, c.id);
      toast({ tone: 'success', title: 'Consentimento registrado', message: `A automação pode digitar a senha em ${app}.` });
      await onMudou();
    } catch (e) {
      toastError('Não foi possível registrar o consentimento', e);
    } finally {
      setSalvando(false);
    }
  }

  async function apagar() {
    if (!(await confirm({ title: `Apagar a senha de ${app}?`, body: 'O segredo sai do cofre; o login automático para até uma nova ser guardada.',
                          confirmLabel: 'Apagar senha', danger: true })).confirmed) return;
    setSalvando(true);
    try {
      await api.deleteAccountCredential(profileId, c.id);
      toast({ tone: 'success', title: 'Senha apagada' });
      await onMudou();
    } catch (e) {
      toastError('Não foi possível apagar a senha', e);
    } finally {
      setSalvando(false);
    }
  }

  return (
    <div className={styles.accountWide}>
      <h4 className={styles.accountSub}><KeyRound size={14} aria-hidden /> Senha do {app}</h4>
      <p className={styles.detail}>
        {configurada ? 'Guardada cifrada e nunca exibida. Para trocar, digite a nova.' : 'Ainda não cadastrada.'}
        {' '}Identificador de login gravado: {gravado ? <strong>{gravado}</strong> : <span className={styles.muted}>nenhum</span>}.
      </p>
      {c.credential?.status === 'invalid' ? (
        <p className={styles.detail}>
          O {app} recusou a senha guardada. A autenticação automática fica parada até ela ser trocada aqui.
        </p>
      ) : null}
      <form className={styles.accountPassword} onSubmit={(e) => { e.preventDefault(); void salvar(); }}>
        <Field label="Identificador de login" hint="O que a automação digita no campo de usuário.">
          {({ id, describedBy }) => (
            <TextInput id={id} aria-describedby={describedBy} value={login} placeholder="e-mail ou usuário"
                       onChange={(e) => setLogin(e.target.value)} />
          )}
        </Field>
        <Field label={configurada ? 'Nova senha' : 'Senha'}
               hint="Vai cifrada para o cofre e nunca é devolvida. Nem o painel nem a IA veem o valor.">
          {({ id, describedBy }) => (
            <TextInput id={id} type="password" autoComplete="new-password" aria-describedby={describedBy}
                       value={senha} onChange={(e) => setSenha(e.target.value)} />
          )}
        </Field>
        <div className={styles.accountConsent}>
          {consentidaEm ? (
            <p className={styles.detail}>
              <ShieldCheck size={13} aria-hidden /> Consentimento dado em {formatDateTime(consentidaEm)}
              {c.credential?.consent_by ? ` por ${c.credential.consent_by}` : ''}.
            </p>
          ) : (
            <Checkbox label={TEXTO_DO_CONSENTIMENTO} aria-label={TEXTO_DO_CONSENTIMENTO} checked={consentiu}
                      onChange={(e) => setConsentiu(e.target.checked)} />
          )}
          <div className={styles.accountFormActions}>
            <Button size="sm" type="submit" variant="primary" icon={KeyRound} loading={salvando} disabledReason={motivo}>
              {configurada ? 'Trocar senha' : 'Guardar senha'}
            </Button>
            {configurada && !consentidaEm ? (
              <Button size="sm" variant="secondary" loading={salvando}
                      disabledReason={consentiu ? null : 'Marque a autorização primeiro.'}
                      onClick={() => void consentirSemRedigitar()}>
                Autorizar sem redigitar
              </Button>
            ) : null}
            {configurada ? (
              <Button size="sm" variant="dangerGhost" loading={salvando} onClick={() => void apagar()}>Apagar senha</Button>
            ) : null}
          </div>
        </div>
        {configurada && !consentidaEm ? (
          <p className={styles.detail}>Senha guardada sem consentimento: ninguém a digita até você autorizar.</p>
        ) : null}
      </form>
      {origens.length > 0 ? (
        <div className={styles.accountPassword}>
          <Field label="Usar a senha de outra conta desta persona" hint={AVISO_DO_CLONE}>
            {({ id, describedBy }) => (
              <Select id={id} aria-describedby={describedBy} value={clonarDe} onChange={(e) => setClonarDe(e.target.value)}>
                <option value="">Escolha a conta…</option>
                {origens.map((o) => <option key={o.id} value={o.id}>{rotuloDaConta(o)}</option>)}
              </Select>
            )}
          </Field>
          <div className={styles.accountFormActions}>
            <Button size="sm" variant="secondary" icon={KeyRound} loading={salvando}
                    disabledReason={clonarDe ? null : 'Escolha de qual conta usar a senha.'}
                    onClick={() => void clonar()}>
              Usar esta senha
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Tentativas({ profileId, accountId }: { profileId: string; accountId: string }) {
  const [itens, setItens] = useState<AuthAttempt[] | null>(null);
  const [falhou, setFalhou] = useState(false);
  useEffect(() => {
    let vivo = true;
    api.accountAuthAttempts(profileId, accountId)
      .then((r) => { if (vivo) setItens(r); })
      .catch(() => { if (vivo) setFalhou(true); });
    return () => { vivo = false; };
  }, [profileId, accountId]);
  if (falhou) return <p className={styles.detail}>Não foi possível carregar as tentativas.</p>;
  if (itens === null) return <Skeleton height={40} />;
  if (itens.length === 0) return <p className={styles.detail}>Nenhuma tentativa de autenticação registrada.</p>;
  return (
    <ul className={styles.list}>
      {itens.map((t) => (
        <li key={t.id}>
          <Badge tone={t.outcome === 'session_ready' ? 'success' : t.outcome ? 'warning' : 'neutral'}>
            {t.outcome ? ACCOUNT_SESSION_STATUS[t.outcome]?.label ?? t.outcome : 'em andamento'}
          </Badge>{' '}
          {formatDateTime(t.started_at)} · {t.instance_id} {t.detail ? <span className={styles.detail}>— {t.detail}</span> : null}
        </li>
      ))}
    </ul>
  );
}
