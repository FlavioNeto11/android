/**
 * Conta planejada (ADR-087, 31.283): preparar a conta de uma persona num app ou site ANTES de ela existir no serviço,
 * e acompanhar o ciclo até a confirmação. Duas peças:
 *
 * - `PrepararConta`: app, endereço desejado (sugerido pelos dados da persona, editável) e a senha — gerada pelo
 *   servidor, digitada num campo seguro ou reaproveitada de outra conta da MESMA pessoa (só se escolhida).
 * - `ProvisionamentoDaConta`: o cartão de uma conta que ainda não está confirmada — desejado x confirmado, o passo
 *   em que está e os eventos que o servidor aceita agora. Nunca finge que a conta existe no serviço.
 *
 * A senha NUNCA volta do servidor: `gerar` não a devolve e os campos de digitação esvaziam depois de cada envio.
 */
import { CircleCheck, CircleDot, KeyRound, Plus, Sparkles, X } from 'lucide-react';
import { useEffect, useId, useState } from 'react';
import { api } from '../../api/client';
import type {
  AppConfig, CredentialPrepareRequest, HandleSuggestion, ProfileAccount, ProvisioningCancelled, ProvisioningEvent,
  ProvisioningState,
} from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { confirm } from '../../components/Confirm';
import { Checkbox, Field, Select, TextInput } from '../../components/Field';
import { cx } from '../../lib/format';
import { formatDateTime } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { toast, toastError } from '../../store/toasts';
import {
  PASSOS, PROXIMO_PASSO, ROTULO_DO_ESTADO, ROTULO_DO_EVENTO, TEXTO_DO_CONSENTIMENTO, ehNavegador, provisionamentoDe,
} from './provisionamento';
import styles from './Profiles.module.css';

type Modo = CredentialPrepareRequest['modo'];

const AVISO_DA_REUTILIZACAO = 'O cofre copia a senha para esta conta sem que ela passe pelo painel. Só vale se você '
  + 'escolher: reaproveitar a mesma senha em dois serviços é decisão sua, nunca o padrão.';

function rotuloDeOrigem(c: ProfileAccount): string {
  return `${c.app_name ?? c.app_id}${c.host ? ` (${c.host})` : ''}${c.handle ? ` — ${c.handle}` : ''}`;
}

/** O estado do bloco de senha. A senha digitada vive só aqui e sai do estado em `limpar`. */
export function useFormDeCredencial(origens: readonly ProfileAccount[], modoInicial: Modo = 'gerar') {
  const [modo, setModo] = useState<Modo>(modoInicial);
  const [senha, setSenha] = useState('');
  const [clonarDe, setClonarDe] = useState('');
  const [consentiu, setConsentiu] = useState(false);

  const motivo = !consentiu ? 'Marque a autorização para a automação digitar esta senha.'
    : modo === 'digitar' && !senha ? 'Digite a senha.'
      : modo === 'reutilizar' && !clonarDe ? 'Escolha de qual conta reaproveitar a senha.' : null;

  const corpo = (substituir: boolean): CredentialPrepareRequest => ({
    modo, consent: true,
    ...(substituir ? { substituir: true } : {}),
    ...(modo === 'digitar' ? { password: senha } : {}),
    ...(modo === 'reutilizar' ? { clonar_de: clonarDe } : {}),
  });

  /** A senha passa por aqui a caminho do cofre e não fica na tela, dê certo ou não. A autorização vale para UM envio. */
  const limpar = () => { setSenha(''); setConsentiu(false); };

  return {
    modo, setModo, senha, setSenha, clonarDe, setClonarDe, consentiu, setConsentiu, motivo, corpo, limpar,
    origens,
  };
}

export type FormDeCredencial = ReturnType<typeof useFormDeCredencial>;

export function CamposDeCredencial({ f, idBase }: { f: FormDeCredencial; idBase: string }) {
  const modos: { id: Modo; rotulo: string; dica: string; indisponivel?: boolean }[] = [
    { id: 'gerar', rotulo: 'Gerar uma senha forte', dica: 'O servidor cria a senha e a guarda no cofre. Ela não aparece aqui nem volta nunca.' },
    { id: 'digitar', rotulo: 'Digitar a senha', dica: 'Vai direto ao cofre, cifrada, sem passar pela IA, pelo log nem pelo histórico de comandos.' },
    { id: 'reutilizar', rotulo: 'Reaproveitar de outra conta da pessoa', dica: AVISO_DA_REUTILIZACAO,
      indisponivel: f.origens.length === 0 },
  ];
  const atual = modos.find((m) => m.id === f.modo)!;
  return (
    <>
      <fieldset className={styles.credModos}>
        <legend>Senha da conta</legend>
        {modos.map((m) => (
          <label key={m.id} className={styles.credModo}>
            <input type="radio" name={`${idBase}-modo`} value={m.id} aria-label={m.rotulo} checked={f.modo === m.id} disabled={m.indisponivel}
                   onChange={() => { f.setModo(m.id); f.limpar(); }} />
            <span>{m.rotulo}{m.indisponivel ? ' (nenhuma outra conta com senha)' : ''}</span>
          </label>
        ))}
        <p className={styles.detail}>{atual.dica}</p>
      </fieldset>
      {f.modo === 'digitar' ? (
        <Field label="Senha">
          {({ id }) => (
            <TextInput id={id} type="password" autoComplete="new-password" value={f.senha}
                       onChange={(e) => f.setSenha(e.target.value)} />
          )}
        </Field>
      ) : null}
      {f.modo === 'reutilizar' ? (
        <Field label="Reaproveitar a senha de">
          {({ id }) => (
            <Select id={id} value={f.clonarDe} onChange={(e) => f.setClonarDe(e.target.value)}>
              <option value="">Escolha…</option>
              {f.origens.map((o) => <option key={o.id} value={o.id}>{rotuloDeOrigem(o)}</option>)}
            </Select>
          )}
        </Field>
      ) : null}
      <Checkbox label={TEXTO_DO_CONSENTIMENTO} aria-label={TEXTO_DO_CONSENTIMENTO} checked={f.consentiu}
                onChange={(e) => f.setConsentiu(e.target.checked)} />
    </>
  );
}

/**
 * Prepara a conta: planeja (idempotente) e grava a credencial. `appFixo` trava o app (vem do assistente do comando,
 * que já sabe qual). Depois de dar certo ou falhar, `onFeita` relê a lista: uma conta planejada sem senha já existe.
 */
export function PrepararConta({ profileId, contas, appFixo, hostInicial, modoInicial, onFeita, onFechar }: {
  profileId: string;
  contas: readonly ProfileAccount[];
  appFixo?: string;
  hostInicial?: string | null;
  /** `reutilizar` quando a pessoa já escolheu usar credencial existente no assistente. */
  modoInicial?: Modo;
  onFeita: (conta: ProfileAccount | null) => Promise<void> | void;
  onFechar?: () => void;
}) {
  const apps = useAppStore((s) => s.apps);
  const idBase = useId();
  const [appId, setAppId] = useState(appFixo ?? '');
  const [host, setHost] = useState(hostInicial ?? '');
  const [desejado, setDesejado] = useState('');
  const [sugestoes, setSugestoes] = useState<HandleSuggestion[]>([]);
  const [enviando, setEnviando] = useState(false);
  // Reaproveitar vem de OUTRA conta da mesma pessoa: a do próprio app e site é a que está sendo preparada.
  const origens = contas.filter((c) => (c.credential?.configured ?? c.credential_configured)
    && !(c.app_id === appId && (c.host ?? '') === host.trim().toLowerCase()));
  const f = useFormDeCredencial(origens, modoInicial);

  const app: AppConfig | null = apps.find((a) => a.id === appId) ?? null;
  const navegador = !!app && ehNavegador(app);

  useEffect(() => {
    if (!appId) { setSugestoes([]); return; }
    let vivo = true;
    api.handleSuggestions(profileId, appId)
      .then((r) => { if (vivo) setSugestoes(r.suggestions); })
      .catch(() => { if (vivo) setSugestoes([]); });  // a sugestão é cortesia: sem ela a pessoa digita
    return () => { vivo = false; };
  }, [profileId, appId]);

  const motivo = !app ? 'Escolha o aplicativo.'
    : navegador && !host.trim() ? 'Diga o site desta conta (ex.: portal.exemplo.com.br).' : f.motivo;

  async function preparar() {
    if (!app || motivo || enviando) return;
    setEnviando(true);
    let conta: ProfileAccount | null = null;
    try {
      const planejada = await api.planAccount(profileId, {
        app_id: app.id, host: navegador ? host.trim() : null, desired_handle: desejado.trim() || null,
      });
      conta = planejada;
      const estado = provisionamentoDe(planejada).state;
      if (estado === 'credencial_preparada') {
        // Já há senha no cofre desta conta: trocá-la é decisão expressa, nunca efeito colateral de "preparar".
        const { confirmed } = await confirm({
          title: `Trocar a senha já guardada da conta de ${app.name}?`,
          body: 'Esta conta já tem uma senha preparada no cofre. Continuar a substitui pela nova, antes do cadastro no serviço.',
          confirmLabel: 'Trocar a senha', danger: true,
        });
        if (!confirmed) { setEnviando(false); f.limpar(); await onFeita(planejada); return; }
      }
      if (estado === 'planejada' || estado === 'credencial_preparada') {
        conta = await api.prepareAccountCredential(profileId, planejada.id, f.corpo(estado === 'credencial_preparada'));
        toast({ tone: 'success', title: `Conta de ${app.name} preparada`,
                message: 'A senha está no cofre. O cadastro no serviço é o próximo passo.' });
      } else {
        toast({ tone: 'info', title: `A conta de ${app.name} já está em andamento`, message: ROTULO_DO_ESTADO[estado] });
      }
    } catch (e) {
      toastError('Não foi possível preparar a conta', e);
    } finally {
      f.limpar();
      setEnviando(false);
    }
    await onFeita(conta);
  }

  return (
    <div className={styles.accountForm}>
      <Field label="Aplicativo ou serviço">
        {({ id }) => (
          <Select id={id} value={appId} disabled={!!appFixo} onChange={(e) => setAppId(e.target.value)}>
            <option value="">Escolha…</option>
            {apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </Select>
        )}
      </Field>
      {navegador ? (
        <Field label="Site" hint="Onde a senha pode ser digitada: só neste site (e subdomínios).">
          {({ id, describedBy }) => (
            <TextInput id={id} aria-describedby={describedBy} value={host} placeholder="portal.exemplo.com.br"
                       disabled={!!hostInicial} onChange={(e) => setHost(e.target.value)} />
          )}
        </Field>
      ) : null}
      <Field label="Endereço ou usuário desejado" unit="opcional"
             hint="É o que você quer; só vira o endereço da conta quando o serviço confirmar.">
        {({ id, describedBy }) => (
          <TextInput id={id} aria-describedby={describedBy} value={desejado} onChange={(e) => setDesejado(e.target.value)} />
        )}
      </Field>
      {sugestoes.length > 0 ? (
        <div className={styles.accountWide}>
          <span className={styles.muted}>Sugestões pelos dados da pessoa: </span>
          <span className={styles.accountBadges}>
            {sugestoes.map((s) => (
              <button key={s.handle} type="button" className={styles.sugestao} aria-pressed={desejado === s.handle}
                      onClick={() => setDesejado(s.handle)}>
                {s.handle}
              </button>
            ))}
          </span>
        </div>
      ) : null}
      <div className={styles.accountWide}>
        <CamposDeCredencial f={f} idBase={idBase} />
      </div>
      <div className={styles.accountFormActions}>
        <Button icon={Plus} loading={enviando} disabledReason={motivo} onClick={() => void preparar()}>
          Preparar conta
        </Button>
        {onFechar ? <Button variant="ghost" icon={X} disabled={enviando} onClick={onFechar}>Fechar</Button> : null}
      </div>
    </div>
  );
}

/** O ciclo na ordem em que acontece, com o passo atual marcado. */
function Passos({ estado, retomaEm }: { estado: ProvisioningState; retomaEm: ProvisioningState | null }) {
  const indice = PASSOS.findIndex((p) => p.estado === (estado === 'falha' ? retomaEm ?? 'planejada' : estado));
  return (
    <ol className={styles.passos} aria-label="Andamento da conta">
      {PASSOS.map((p, i) => {
        const feito = i < indice || estado === 'confirmada';
        const atual = i === indice && estado !== 'confirmada';
        return (
          <li key={p.estado} className={cx(styles.passo, feito && styles.passoFeito, atual && styles.passoAtual)}
              aria-current={atual ? 'step' : undefined}>
            {feito ? <CircleCheck size={13} aria-hidden /> : <CircleDot size={13} aria-hidden />}
            <span>{p.rotulo}</span>
            {feito ? <span className="sr-only"> (feito)</span> : null}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Uma conta que ainda não está confirmada. Conectar, verificar e sair NÃO aparecem aqui: o servidor responde 409
 * `conta_nao_confirmada`. Cada evento leva o estado que a tela viu (`estado_esperado`): se outra pessoa mexeu antes,
 * o servidor recusa em vez de pular passo, e a tela relê.
 */
export function ProvisionamentoDaConta({ profileId, conta, onMudou, origens = [] }: {
  profileId: string;
  conta: ProfileAccount;
  onMudou: () => Promise<void>;
  /** As OUTRAS contas desta persona com senha: de onde reaproveitar. */
  origens?: readonly ProfileAccount[];
}) {
  const p = provisionamentoDe(conta);
  const idBase = useId();
  const nome = conta.app_name ?? conta.app_id;
  const [ocupado, setOcupado] = useState(false);
  const [desejado, setDesejado] = useState(p.desired_handle ?? '');
  const [confirmando, setConfirmando] = useState(false);
  const [confirmado, setConfirmado] = useState('');
  const [falhando, setFalhando] = useState(false);
  const [motivoDaFalha, setMotivoDaFalha] = useState('');
  const [trocando, setTrocando] = useState(false);
  const f = useFormDeCredencial(origens);
  const temSenha = conta.credential?.configured ?? conta.credential_configured;
  const podePreparar = p.state === 'planejada' || p.state === 'credencial_preparada';

  useEffect(() => { setDesejado(p.desired_handle ?? ''); }, [p.desired_handle]);

  async function agir(fn: () => Promise<unknown>, ok: string, erro: string) {
    setOcupado(true);
    try {
      await fn();
      toast({ tone: 'success', title: ok });
    } catch (e) {
      toastError(erro, e);
    } finally {
      setOcupado(false);
    }
    await onMudou();  // dê certo ou não: o estado que o servidor tem é o que vale
  }

  const evento = (ev: ProvisioningEvent, extra: { motivo?: string; evidencia?: { tipo: 'declarada'; handle_confirmado: string } } = {}) =>
    api.provisionAccount(profileId, conta.id, { evento: ev, estado_esperado: p.state, ...extra });

  async function cancelar() {
    const { confirmed } = await confirm({
      title: `Cancelar a conta de ${nome}?`,
      body: 'A conta planejada sai da lista. A senha só é apagada do cofre se nenhuma outra conta a usa.',
      confirmLabel: 'Cancelar a conta', danger: true,
    });
    if (!confirmed) return;
    await agir(async () => {
      const r = (await evento('cancelar')) as ProfileAccount | ProvisioningCancelled;
      if ('removida' in r && r.removida) return;
    }, 'Conta cancelada', 'Não foi possível cancelar a conta');
  }

  async function guardarSenha() {
    if (f.motivo) return;
    await agir(() => api.prepareAccountCredential(profileId, conta.id, f.corpo(p.state === 'credencial_preparada')),
               'Senha guardada no cofre', 'Não foi possível guardar a senha').finally(() => {
      f.limpar();
      setTrocando(false);
    });
  }

  const acoes = new Set(p.actions);
  const semConfirmar = [...p.actions].filter((a) => a !== 'confirmar' && a !== 'falhar' && a !== 'cancelar');

  return (
    <div className={cx(styles.accountWide, styles.provisionamento)}>
      <div className={styles.accountBadges}>
        <Badge size="sm" tone={p.state === 'falha' ? 'danger' : 'info'}>{ROTULO_DO_ESTADO[p.state]}</Badge>
        <span className={styles.muted}>Esta conta ainda não existe no serviço: nada aqui é login.</span>
      </div>
      <Passos estado={p.state} retomaEm={p.resume_state} />

      <dl className={styles.desejadoConfirmado}>
        <div>
          <dt>Endereço desejado</dt>
          <dd>
            <TextInput small aria-label={`Endereço desejado de ${nome}`} value={desejado}
                       onChange={(e) => setDesejado(e.target.value)} />
            <Button size="sm" variant="ghost" loading={ocupado}
                    disabledReason={desejado.trim() === (p.desired_handle ?? '') ? 'Nada mudou.' : null}
                    onClick={() => void agir(() => api.patchAccount(profileId, conta.id, { desired_handle: desejado.trim() }),
                                             'Endereço desejado salvo', 'Não foi possível salvar o endereço')}>
              Salvar
            </Button>
          </dd>
        </div>
        <div>
          <dt>Endereço confirmado</dt>
          <dd>{conta.handle || <span className={styles.muted}>ainda não confirmado pelo serviço</span>}</dd>
        </div>
      </dl>

      {p.detail ? <p className={styles.detail}>{p.detail}</p> : null}
      {PROXIMO_PASSO[p.state] ? <p className={styles.detail}>{PROXIMO_PASSO[p.state]}</p> : null}
      {temSenha ? (
        <p className={styles.detail}><KeyRound size={12} aria-hidden /> Senha guardada no cofre; ela não é exibida nem volta.</p>
      ) : null}

      {podePreparar && (p.state === 'planejada' || trocando) ? (
        <div className={styles.accountForm}>
          <div className={styles.accountWide}><CamposDeCredencial f={f} idBase={idBase} /></div>
          <div className={styles.accountFormActions}>
            <Button icon={Sparkles} loading={ocupado} disabledReason={f.motivo} onClick={() => void guardarSenha()}>
              {p.state === 'planejada' ? 'Preparar a senha' : 'Trocar a senha'}
            </Button>
            {trocando ? <Button variant="ghost" onClick={() => { setTrocando(false); f.limpar(); }}>Fechar</Button> : null}
          </div>
        </div>
      ) : null}

      <div className={styles.accountBadges}>
        {semConfirmar.map((ev) => (
          <Button key={ev} size="sm" variant={ev === 'retomar' || ev === 'iniciar_cadastro' ? 'primary' : 'secondary'}
                  loading={ocupado} onClick={() => void agir(() => evento(ev), `${ROTULO_DO_EVENTO[ev]}: feito`, 'Não foi possível avançar a conta')}>
            {ROTULO_DO_EVENTO[ev]}
          </Button>
        ))}
        {p.state === 'credencial_preparada' && !trocando ? (
          <Button size="sm" variant="ghost" onClick={() => setTrocando(true)}>Trocar a senha</Button>
        ) : null}
        {acoes.has('confirmar') ? (
          <Button size="sm" onClick={() => setConfirmando((v) => !v)}>{ROTULO_DO_EVENTO.confirmar}</Button>
        ) : null}
        {acoes.has('falhar') ? (
          <Button size="sm" variant="ghost" onClick={() => setFalhando((v) => !v)}>{ROTULO_DO_EVENTO.falhar}</Button>
        ) : null}
        {acoes.has('cancelar') ? (
          <Button size="sm" variant="dangerGhost" loading={ocupado} onClick={() => void cancelar()}>
            {ROTULO_DO_EVENTO.cancelar}
          </Button>
        ) : null}
      </div>

      {confirmando && acoes.has('confirmar') ? (
        <div className={styles.accountForm}>
          <Field label="Endereço que o serviço confirmou"
                 hint="Declare só o que viu no serviço. A conta só vira confirmada com esta evidência, marcada em seu nome.">
            {({ id, describedBy }) => (
              <TextInput id={id} aria-describedby={describedBy} value={confirmado} onChange={(e) => setConfirmado(e.target.value)} />
            )}
          </Field>
          <div className={styles.accountFormActions}>
            <Button loading={ocupado} disabledReason={confirmado.trim() ? null : 'Diga o endereço confirmado.'}
                    onClick={() => void agir(() => evento('confirmar', { evidencia: { tipo: 'declarada', handle_confirmado: confirmado.trim() } }),
                                             'Conta confirmada', 'Não foi possível confirmar a conta')}>
              Confirmar com esta evidência
            </Button>
          </div>
        </div>
      ) : null}
      {falhando && acoes.has('falhar') ? (
        <div className={styles.accountForm}>
          <Field label="O que aconteceu" hint="Fica no histórico da conta; a senha e os dados continuam guardados.">
            {({ id, describedBy }) => (
              <TextInput id={id} aria-describedby={describedBy} value={motivoDaFalha} onChange={(e) => setMotivoDaFalha(e.target.value)} />
            )}
          </Field>
          <div className={styles.accountFormActions}>
            <Button loading={ocupado} disabledReason={motivoDaFalha.trim() ? null : 'Diga o motivo.'}
                    onClick={() => void agir(() => evento('falhar', { motivo: motivoDaFalha.trim() }),
                                             'Falha registrada', 'Não foi possível registrar a falha')}>
              Registrar a falha
            </Button>
          </div>
        </div>
      ) : null}
      {p.confirmed_at ? <p className={styles.muted}>Confirmada em {formatDateTime(p.confirmed_at)}</p> : null}
    </div>
  );
}
