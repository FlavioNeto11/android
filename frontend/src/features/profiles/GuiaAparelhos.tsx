/**
 * Guia "Aparelhos" (evolução 2, onda E2; ADR-043): os N aparelhos da persona, cada vínculo com o estado do aparelho,
 * o servidor, o app do vínculo, a sessão da conta NAQUELE aparelho e o selo "Principal" (alvo padrão de conectar,
 * verificar, sair e das tarefas sem aparelho dito). Tornar principal, desvincular e "Abrir no Foco" por vínculo;
 * "Vincular a um aparelho" soma um sem tirar ninguém de lá. Onde os dados vivem (Localidade) continua em cima.
 */
import { Crosshair, Link2, RefreshCw, Smartphone, Star, TriangleAlert, Unlink } from 'lucide-react';
import { useState } from 'react';
import { api, toApiError } from '../../api/client';
import type { DeviceAppState, OfflinePolicy, PersonaDevice } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { Select } from '../../components/Field';
import { AutoGrid, PageSection } from '../../components/Page';
import { StatusBadge } from '../../components/StatusBadge';
import { plural } from '../../lib/format';
import { APP_INSTALL_STATE, DRIFT_KIND, INSTANCE_STATE, SESSION_STATUS, metaOf } from '../../lib/status';
import { tempoRelativo, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { chaveDoApp } from '../../store/reducer';
import { toast, toastError } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { OperationalContextCard } from '../devices/OperationalContextCard';
import { serverHintOf } from '../devices/deviceState';
import { ServerBadge } from '../devices/ServerBadge';
import { aparelhoDesconhecido, seloDoAparelho } from '../devices/selos';
import { Carregando, Linha, useLista } from './detalheComum';
import { aparelhosDe, nomeDe, type Pessoa } from './pessoa';
import styles from './Profiles.module.css';
import { VincularForm } from './VincularForm';
import { recusaDoVinculo, type RecusaDoVinculo } from './vinculo';

/** Um vínculo é (aparelho, app): o mesmo aparelho pode aparecer duas vezes, uma por app. */
const chaveDoVinculo = (v: Pick<PersonaDevice, 'instance_id' | 'app_id'>) => `${v.instance_id}:${v.app_id ?? ''}`;

export function AbaAparelho({ profile, onChanged }: { profile: Pessoa; onChanged: () => Promise<void> }) {
  const vinculos = aparelhosDe(profile);
  const ids = [...new Set(vinculos.map((v) => v.instance_id))];
  const aoVivo = useAppStore((s) => s.appState);
  const apps = useAppStore((s) => s.apps);
  const [vinculando, setVinculando] = useState(vinculos.length === 0);
  // O store só recebe estado de app por evento (`app_state.updated`); o snapshot não o traz. A leitura inicial
  // continua pela API, e o que chegar ao vivo destes aparelhos passa por cima.
  const [lidos, recarregar] = useLista<DeviceAppState>(() => api.listAppState(), [ids.join(',')]);

  if (vinculos.length > 0 && lidos === null) return <Carregando />;

  const porAparelho = new Map<string, Map<string, DeviceAppState>>();
  const guardar = (a: DeviceAppState, chave: string) => {
    if (!ids.includes(a.instance_id)) return;
    const m = porAparelho.get(a.instance_id) ?? new Map<string, DeviceAppState>();
    m.set(chave, a);
    porAparelho.set(a.instance_id, m);
  };
  for (const a of lidos ?? []) guardar(a, chaveDoApp(a.instance_id, a.package_name));
  for (const [k, a] of Object.entries(aoVivo)) guardar(a, k);

  // D3: a MESMA conta em mais de um aparelho é permitida, mas o Instagram pode pedir verificação — e aí o ADR-029
  // bloqueia a persona. O aviso vem antes, não depois do bloqueio.
  const porApp = new Map<string, number>();
  for (const v of vinculos) if (v.session) porApp.set(v.app_id ?? '', (porApp.get(v.app_id ?? '') ?? 0) + 1);
  const repetidas = [...porApp.entries()].filter(([, n]) => n > 1)
    .map(([app]) => apps.find((a) => a.id === app)?.name ?? (app || 'a conta de cadastro'));

  return (
    <div className={styles.stack}>
      <Localidade profile={profile} onChanged={onChanged} />
      <PageSection
        title="Aparelhos desta persona"
        subtitle={vinculos.length === 0 ? undefined
          : `${plural(ids.length, 'aparelho', 'aparelhos')}. O principal é onde conectar, verificar e sair agem, e o `
            + 'preferido das tarefas quando nenhum aparelho é dito.'}
        actions={
          <div className={styles.headerButtons}>
            <Button size="sm" variant={vinculos.length === 0 ? 'primary' : 'outline'} icon={Link2}
                    onClick={() => setVinculando((v) => !v)}>
              {vinculando ? 'Fechar' : 'Vincular a um aparelho'}
            </Button>
            {vinculos.length > 0 ? (
              <Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => void recarregar()}>Atualizar</Button>
            ) : null}
          </div>
        }
      >
        {vinculando ? (
          <VincularForm personaId={profile.id} jaVinculados={ids}
                        onVinculado={async () => { setVinculando(false); await onChanged(); }}
                        onCancelar={vinculos.length > 0 ? () => setVinculando(false) : undefined} />
        ) : null}
        {vinculos.length === 0 ? (
          <EmptyState icon={Smartphone} title="Sem aparelho"
                      hint="Vincule um aparelho aqui (“Vincular a um aparelho”). Uma persona pode ter vários, e um aparelho, várias personas — uma por app.">
            Esta persona não está vinculada a nenhum aparelho.
          </EmptyState>
        ) : (
          <>
            {repetidas.length > 0 ? (
              <Banner tone="warning" icon={TriangleAlert} compact title="A mesma conta em mais de um aparelho">
                {repetidas.join(', ')}: entrar na mesma conta de aparelhos diferentes pode fazer o app pedir
                verificação — e, se pedir, esta persona fica bloqueada até uma pessoa resolver.
              </Banner>
            ) : null}
            <AutoGrid min="300px" className={styles.vinculos} role="list" aria-label="Vínculos desta persona">
              {vinculos.map((v) => (
                <CartaoDoVinculo key={chaveDoVinculo(v)} profile={profile} vinculo={v}
                                 apps={[...(porAparelho.get(v.instance_id)?.values() ?? [])]}
                                 onChanged={onChanged} />
              ))}
            </AutoGrid>
          </>
        )}
      </PageSection>
    </div>
  );
}

/** Servidor → aparelho → conta: um vínculo, com o que se pode fazer com ele. */
function CartaoDoVinculo({ profile, vinculo: v, apps: instalados, onChanged }: {
  profile: Pessoa;
  vinculo: PersonaDevice;
  apps: DeviceAppState[];
  onChanged: () => Promise<void>;
}) {
  const now = useNow();
  const inst = useAppStore((s) => s.instances[v.instance_id]);
  const workers = useAppStore((s) => s.workers);
  const apps = useAppStore((s) => s.apps);
  const openFocus = useUiStore((s) => s.openFocus);
  const [busy, setBusy] = useState<'primary' | 'unbind' | null>(null);
  const [recusa, setRecusa] = useState<RecusaDoVinculo | null>(null);
  const server = inst ? serverHintOf(inst, workers) : null;
  // O estado ao vivo (store) ganha do que veio na persona: a persona é relida só quando ela muda.
  const estado = inst?.state ?? v.state;
  const appDoVinculo = v.app_id ? apps.find((a) => a.id === v.app_id)?.name ?? v.app_id : null;
  const nome = nomeDe(profile);
  const ordenados = [...instalados].sort((a, b) => a.package_name.localeCompare(b.package_name));

  async function tornarPrincipal() {
    setBusy('primary');
    setRecusa(null);
    try {
      await api.setPrimaryPersonaDevice(profile.id, v.instance_id);
      toast({ tone: 'success', title: `${v.instance_id} é agora o aparelho principal de ${nome}` });
      await onChanged();
    } catch (e) {
      setRecusa(await recusaDoVinculo(toApiError(e), { instanceId: v.instance_id, appId: v.app_id, appNome: appDoVinculo, profileId: profile.id }));
    } finally {
      setBusy(null);
    }
  }

  async function desvincular() {
    const { confirmed } = await confirm({
      title: `Desvincular ${nome} de ${v.instance_id}?`,
      danger: true,
      confirmLabel: 'Desvincular',
      body: `${nome} deixa de receber tarefas em ${v.instance_id}${appDoVinculo ? ` pelo ${appDoVinculo}` : ''}. Os dados `
        + 'do app continuam no aparelho (sair da conta é outra ação, em Contas e acesso).'
        + (v.is_primary ? ' Este é o principal: o vínculo mais antigo que sobrar passa a ser o principal.' : ''),
    });
    if (!confirmed) return;
    setBusy('unbind');
    setRecusa(null);
    try {
      await api.unbindPersonaDevice(profile.id, v.instance_id, v.app_id);
      toast({ tone: 'success', title: `${nome} desvinculada de ${v.instance_id}` });
      await onChanged();
    } catch (e) {
      // `persona_in_use` (execução dela ali) fica no cartão, com o que fazer: um toast sumiria antes da leitura.
      setRecusa(await recusaDoVinculo(toApiError(e), { instanceId: v.instance_id, appId: v.app_id, appNome: appDoVinculo, profileId: profile.id }));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card role="listitem" aria-label={`Vínculo com ${v.instance_id}`} className={styles.vinculo}>
      <CardHeader
        title={<span className={styles.vinculoTitulo}><Smartphone size={15} aria-hidden /> <span className="mono">{v.instance_id}</span></span>}
        actions={v.is_primary ? <Badge tone="info" icon={Star}>Principal</Badge> : null}
        level={3}
      />
      <CardBody>
        <dl className={`${styles.rows} ${styles.rowsCartao}`}>
          <Linha rotulo="Estado">
            {/* RF-40: o aparelho do parque passa pela regra do desconhecido (servidor sem resposta), como nas outras telas. */}
            {inst ? <StatusBadge meta={seloDoAparelho(inst, workers)} size="sm" />
              : estado ? <StatusBadge meta={metaOf(INSTANCE_STATE, estado)} size="sm" />
              : <span className={styles.muted}>não está no parque</span>}
            {inst?.state_detail && !aparelhoDesconhecido(inst, workers) ? <span className={styles.muted}> · {inst.state_detail}</span> : null}
          </Linha>
          <Linha rotulo="Servidor">
            {server ? <ServerBadge server={server} size="sm" estatico /> : inst || !v.worker_id ? 'este servidor' : v.worker_id}
          </Linha>
          <Linha rotulo="App do vínculo">
            {appDoVinculo ?? <span className={styles.muted}>nenhum (apps sem conta gerenciada)</span>}
          </Linha>
          <Linha rotulo="Sessão aqui">
            {v.session ? (
              <span className={styles.badgeRow}>
                <StatusBadge meta={metaOf(SESSION_STATUS, v.session.status)} size="sm" srPrefix="Sessão neste aparelho" />
                {v.session.stale ? <Badge size="sm" tone="warning">precisa reler</Badge> : null}
                {v.session.verified_at ? <span className={styles.muted}>conferida {tempoRelativo(v.session.verified_at, now)}</span> : null}
              </span>
            ) : <span className={styles.muted}>sem conta que sirva a este vínculo</span>}
          </Linha>
        </dl>
        {v.session?.detail ? <p className={styles.detail}>{v.session.detail}</p> : null}
        {ordenados.length === 0 ? (
          <p className={styles.detail}>Nenhum aplicativo catalogado neste aparelho ainda.</p>
        ) : (
          <dl className={`${styles.rows} ${styles.rowsCartao} ${styles.vinculoApps}`} aria-label={`Apps em ${v.instance_id}`}>
            {ordenados.map((a) => (
              <Linha key={a.package_name} rotulo={a.package_name}>
                <StatusBadge meta={metaOf(APP_INSTALL_STATE, a.state)} size="sm" />{' '}
                {a.observed_version_name ? `${a.observed_version_name} (${a.observed_version_code ?? '?'})` : '—'}
                {a.drift_kind ? <> · <StatusBadge meta={metaOf(DRIFT_KIND, a.drift_kind)} size="sm" /></> : null}
              </Linha>
            ))}
          </dl>
        )}
        {recusa ? (
          <div className={styles.vincularRecusa} role="alert">
            <p><TriangleAlert size={13} aria-hidden /> <strong>{recusa.titulo}.</strong> {recusa.texto}</p>
            <p className={styles.muted}>{recusa.mensagem}</p>
          </div>
        ) : null}
        <div className={styles.actions}>
          <Button size="sm" variant="primary" icon={Crosshair} onClick={() => openFocus(v.instance_id)}>Abrir no Foco</Button>
          {v.is_primary ? null : (
            <Button size="sm" variant="outline" icon={Star} loading={busy === 'primary'} disabled={busy !== null}
                    onClick={() => void tornarPrincipal()}>
              Tornar principal
            </Button>
          )}
          <Button size="sm" variant="dangerGhost" icon={Unlink} loading={busy === 'unbind'} disabled={busy !== null}
                  onClick={() => void desvincular()}>
            Desvincular
          </Button>
        </div>
        {/* Da persona ao aparelho sem trocar de tela: servidor, tela, apps e sessão, da mesma fonte que o Foco —
            agora por aparelho (`?instance_id=`), lido só quando aberto. */}
        <Disclosure bare summary="Contexto operacional neste aparelho" className={styles.vinculoContexto}>
          {() => <OperationalContextCard profileId={profile.id} instanceId={v.instance_id}
                                         refreshKey={`${v.session?.status ?? ''}:${estado ?? ''}`} />}
        </Disclosure>
      </CardBody>
    </Card>
  );
}

/**
 * Onde os dados desta persona VIVEM (E9, item 4.4) e o que fazer quando aquele servidor não responde.
 *
 * A sessão do Instagram mora na partição de dados do aparelho, no disco de UMA máquina: o pedido do dono diz que
 * "perfil armazenado num servidor NÃO está automaticamente disponível em outro". Até aqui a tela mostrava só o
 * `instance_id`, e um perfil cujo servidor tinha mudado aparecia igual aos demais. Com N aparelhos, a localidade
 * mostrada é a do principal (é a que o backend fotografa em `locality`).
 */
function Localidade({ profile, onChanged }: { profile: Pessoa; onChanged: () => Promise<void> }) {
  const loc = profile.locality;
  const [salvando, setSalvando] = useState(false);
  if (!loc) return null;

  async function mudarPolitica(valor: OfflinePolicy) {
    setSalvando(true);
    try {
      await api.patchProfile(profile.id, { offline_policy: valor });
      // Recarrega ANTES do toast: o select é controlado por `profile.offline_policy`, e sem isto ele voltaria
      // visualmente ao valor antigo depois de um PATCH que funcionou — um controle que parece não ter efeito.
      await onChanged();
      toast({
        tone: 'info',
        title: 'Política de localidade atualizada',
        message: valor === 'wait'
          ? 'Esta persona espera o servidor onde os dados vivem voltar.'
          : 'Esta persona pode entrar na conta de novo em outro servidor, quando for usada lá.',
      });
    } catch (e) {
      toastError('Não foi possível mudar a política', e);
    } finally {
      setSalvando(false);
    }
  }

  return (
    <Card>
      <CardHeader title="Onde esta persona vive"
                  subtitle={`Os dados da sessão ficam no disco de uma máquina; mudar de servidor exige entrar na conta de novo.${profile.instance_id ? ` Aparelho principal: ${profile.instance_id}.` : ''}`} />
      <CardBody>
        <dl className={styles.rows}>
          <Linha rotulo="Servidor">
            {loc.worker_name ?? loc.worker_id ?? 'este servidor'}
            {loc.moved ? <> <Badge tone="warning">mudou de servidor</Badge></> : null}
            {!loc.available ? <> <Badge tone="warning">indisponível</Badge></> : null}
            {!loc.known ? <> <Badge>localidade não registrada</Badge></> : null}
          </Linha>
          {loc.physical_id ? <Linha rotulo="Aparelho físico">{loc.physical_id}</Linha> : null}
          <Linha rotulo="Se o servidor estiver fora">
            <Select value={profile.offline_policy} disabled={salvando}
                    aria-label="O que fazer quando o servidor desta persona não responde"
                    onChange={(e) => void mudarPolitica(e.target.value as OfflinePolicy)}>
              <option value="wait">Esperar aquele servidor voltar</option>
              <option value="reauth_elsewhere">Permitir entrar na conta de novo em outro servidor</option>
            </Select>
          </Linha>
        </dl>
        {loc.detail ? <p className={styles.detail}>{loc.detail}</p> : null}
      </CardBody>
    </Card>
  );
}
