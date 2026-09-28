/**
 * Guia "Aparelhos" (por ora, o aparelho vinculado; a lista N:N vem na onda E2): onde os dados da persona vivem
 * (Localidade), o aparelho com estado, servidor e apps, o atalho "Abrir no Foco" e o contexto operacional (servidor
 * → aparelho → tela → apps → sessão), que antes ficava na guia Autenticação.
 */
import { Crosshair, Smartphone } from 'lucide-react';
import { useState } from 'react';
import { api } from '../../api/client';
import type { DeviceAppState, OfflinePolicy } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { EmptyState } from '../../components/EmptyState';
import { Select } from '../../components/Field';
import { StatusBadge } from '../../components/StatusBadge';
import { APP_INSTALL_STATE, DRIFT_KIND, INSTANCE_STATE, metaOf } from '../../lib/status';
import { useAppStore } from '../../store/app';
import { chaveDoApp } from '../../store/reducer';
import { toast, toastError } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import { OperationalContextCard } from '../devices/OperationalContextCard';
import { serverHintOf } from '../devices/deviceState';
import { ServerBadge } from '../devices/ServerBadge';
import { Carregando, Linha, useLista } from './detalheComum';
import type { Pessoa } from './pessoa';
import styles from './Profiles.module.css';

export function AbaAparelho({ profile, onChanged }: { profile: Pessoa; onChanged: () => Promise<void> }) {
  const iid = profile.instance_id;
  const inst = useAppStore((s) => (iid ? s.instances[iid] : undefined));
  const workers = useAppStore((s) => s.workers);
  const aoVivo = useAppStore((s) => s.appState);
  const openFocus = useUiStore((s) => s.openFocus);
  // O store só recebe estado de app por evento (`app_state.updated`); o snapshot não o traz. A leitura inicial
  // continua pela API, e o que chegar ao vivo deste aparelho passa por cima.
  const [lidos, recarregar] = useLista<DeviceAppState>(() => api.listAppState(), [iid]);

  if (!iid) {
    return (
      <EmptyState icon={Smartphone} title="Sem aparelho"
                  hint="O vínculo com um aparelho é feito em Configuração → Instâncias e contas (até a próxima etapa, que traz vários aparelhos por persona).">
        Esta persona não está vinculada a nenhum aparelho.
      </EmptyState>
    );
  }
  if (lidos === null) return <Carregando />;

  const porChave = new Map<string, DeviceAppState>();
  for (const a of lidos) if (a.instance_id === iid) porChave.set(chaveDoApp(a.instance_id, a.package_name), a);
  for (const [k, a] of Object.entries(aoVivo)) if (a.instance_id === iid) porChave.set(k, a);
  const apps = [...porChave.values()].sort((a, b) => a.package_name.localeCompare(b.package_name));
  const server = inst ? serverHintOf(inst, workers) : null;

  return (
    <div className={styles.stack}>
      <Localidade profile={profile} onChanged={onChanged} />
      <Card>
        <CardHeader title={`Aparelho ${iid}`}
                    subtitle="O que está instalado, lido do próprio aparelho — nunca presumido."
                    actions={
                      <div className={styles.headerButtons}>
                        <Button size="sm" variant="primary" icon={Crosshair} onClick={() => openFocus(iid)}>Abrir no Foco</Button>
                        <Button size="sm" variant="ghost" onClick={() => void recarregar()}>Atualizar</Button>
                      </div>
                    } />
        <CardBody>
          <dl className={styles.rows}>
            <Linha rotulo="Estado">
              {inst ? <StatusBadge meta={metaOf(INSTANCE_STATE, inst.state)} size="sm" /> : <span className={styles.muted}>não está no parque</span>}
              {inst?.state_detail ? <span className={styles.muted}> · {inst.state_detail}</span> : null}
            </Linha>
            <Linha rotulo="Servidor">
              {server ? <ServerBadge server={server} size="sm" estatico /> : inst ? 'este servidor' : '—'}
            </Linha>
          </dl>
          {apps.length === 0 ? (
            <p className={styles.detail}>Nenhum aplicativo catalogado neste aparelho ainda.</p>
          ) : (
            <dl className={styles.rows} style={{ marginTop: 'var(--sp-3)' }}>
              {apps.map((a) => (
                <Linha key={a.package_name} rotulo={a.package_name}>
                  <StatusBadge meta={metaOf(APP_INSTALL_STATE, a.state)} size="sm" />{' '}
                  {a.observed_version_name ? `${a.observed_version_name} (${a.observed_version_code ?? '?'})` : '—'}
                  {a.drift_kind ? <> · <StatusBadge meta={metaOf(DRIFT_KIND, a.drift_kind)} size="sm" /></> : null}
                </Linha>
              ))}
            </dl>
          )}
          <p className={styles.detail}>A sessão de cada conta neste aparelho fica na guia Contas e acesso.</p>
        </CardBody>
      </Card>
      {/* Da persona ao aparelho sem trocar de tela: servidor, tela, apps e sessão, da mesma fonte que o Foco. */}
      <Card><CardBody>
        <OperationalContextCard profileId={profile.id}
                                refreshKey={`${profile.session.status}:${profile.session_actions?.phase ?? ''}`} />
      </CardBody></Card>
    </div>
  );
}

/**
 * Onde os dados desta persona VIVEM (E9, item 4.4) e o que fazer quando aquele servidor não responde.
 *
 * A sessão do Instagram mora na partição de dados do aparelho, no disco de UMA máquina: o pedido do dono diz que
 * "perfil armazenado num servidor NÃO está automaticamente disponível em outro". Até aqui a tela mostrava só o
 * `instance_id`, e um perfil cujo servidor tinha mudado aparecia igual aos demais.
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
                  subtitle="Os dados da sessão ficam no disco de uma máquina; mudar de servidor exige entrar na conta de novo." />
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
