import { Smartphone } from 'lucide-react';
import { useState } from 'react';
import { api } from '../../api/client';
import type { OfflinePolicy } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { EmptyState } from '../../components/EmptyState';
import { Select } from '../../components/Field';
import { StatusBadge } from '../../components/StatusBadge';
import { APP_INSTALL_STATE, DRIFT_KIND, SESSION_STATUS, metaOf } from '../../lib/status';
import { toast, toastError } from '../../store/toasts';
import { Carregando, Linha, useLista } from './detalheComum';
import type { Pessoa } from './pessoa';
import styles from './Profiles.module.css';

export function AbaAparelho({ profile, onChanged }: { profile: Pessoa; onChanged: () => Promise<void> }) {
  const [apps, recarregar] = useLista(() => api.listAppState(), [profile.instance_id]);
  const doAparelho = (apps ?? []).filter((a) => a.instance_id === profile.instance_id);

  if (!profile.instance_id) {
    return (
      <EmptyState icon={Smartphone} title="Sem aparelho" hint="Vincule um aparelho para este perfil poder operar.">
        Este perfil não está vinculado a nenhum aparelho.
      </EmptyState>
    );
  }
  if (apps === null) return <Carregando />;
  return (
    <>
    <Localidade profile={profile} onChanged={onChanged} />
    <Card>
      <CardHeader title={`Aparelho ${profile.instance_id}`}
                  subtitle="O que está instalado, lido do próprio aparelho — nunca presumido."
                  actions={<Button size="sm" variant="ghost" onClick={() => void recarregar()}>Atualizar</Button>} />
      <CardBody>
        {doAparelho.length === 0 ? (
          <p className={styles.detail}>Nenhum aplicativo catalogado neste aparelho ainda.</p>
        ) : (
          <dl className={styles.rows}>
            {doAparelho.map((a) => (
              <Linha key={a.package_name} rotulo={a.package_name}>
                <StatusBadge meta={metaOf(APP_INSTALL_STATE, a.state)} size="sm" />{' '}
                {a.observed_version_name ? `${a.observed_version_name} (${a.observed_version_code ?? '?'})` : '—'}
                {a.drift_kind ? <> · <StatusBadge meta={metaOf(DRIFT_KIND, a.drift_kind)} size="sm" /></> : null}
              </Linha>
            ))}
          </dl>
        )}
        <p className={styles.detail}>
          Sessão: {metaOf(SESSION_STATUS, profile.session.status).label} {profile.session.detail ? `— ${profile.session.detail}` : ''}
        </p>
      </CardBody>
    </Card>
    </>
  );
}

/**
 * Onde os dados deste perfil VIVEM (E9, item 4.4) e o que fazer quando aquele servidor não responde.
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
          ? 'Este perfil espera o servidor onde os dados vivem voltar.'
          : 'Este perfil pode entrar na conta de novo em outro servidor, quando for usado lá.',
      });
    } catch (e) {
      toastError('Não foi possível mudar a política', e);
    } finally {
      setSalvando(false);
    }
  }

  return (
    <Card>
      <CardHeader title="Onde este perfil vive"
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
                    aria-label="O que fazer quando o servidor deste perfil não responde"
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
