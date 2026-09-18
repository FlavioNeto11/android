import { FlaskConical, FolderInput, Package, ShieldCheck, ShieldX, Smartphone, TrendingUp, Undo2 } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { AppRelease, DeviceAppState, Instance, ReleaseChannel } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { confirm } from '../../components/Confirm';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import type { Tone } from '../../lib/status';
import { toast, toastError } from '../../store/toasts';
import styles from './Releases.module.css';

const ESTADO_TOM: Record<string, Tone> = {
  installable: 'success', validated: 'success', ready: 'success',
  inspected: 'neutral', imported: 'neutral', installed: 'neutral', verifying: 'neutral', installing: 'neutral',
  invalid: 'danger', incompatible: 'danger', install_failed: 'danger', verify_failed: 'danger',
  version_drift: 'warning', missing: 'warning',
};

/** `status` diz se o ARQUIVO pode ser instalado; `channel`, se a VERSÃO já provou que funciona. */
const CANAL: Record<ReleaseChannel, { rotulo: string; tom: Tone }> = {
  candidate: { rotulo: 'nunca provada', tom: 'neutral' },
  canary: { rotulo: 'em prova (canário)', tom: 'info' },
  promoted: { rotulo: 'promovida', tom: 'success' },
  quarantined: { rotulo: 'em quarentena', tom: 'danger' },
  rolled_back: { rotulo: 'substituída', tom: 'muted' },
};

function tom(estado: string): Tone {
  return ESTADO_TOM[estado] ?? 'neutral';
}

/**
 * Aplicativos: o que foi importado da pasta `apks/inbox`, o que está instalado em cada aparelho, qual assinatura
 * foi aprovada e em que ponto do ciclo de vida cada versão está. O sistema nunca baixa APK sozinho — os arquivos
 * são colocados na pasta por uma pessoa — e nunca instala sozinho: canário, promoção e rollback são pedidos daqui.
 */
export function ReleasesPage() {
  const [releases, setReleases] = useState<AppRelease[] | null>(null);
  const [estados, setEstados] = useState<DeviceAppState[]>([]);
  const [aparelhos, setAparelhos] = useState<Instance[]>([]);
  const [alvo, setAlvo] = useState<Record<string, string>>({});
  const [importando, setImportando] = useState(false);
  const token = useRef(0);

  const carregar = useCallback(async () => {
    const meu = ++token.current;
    const [r, e, i] = await Promise.allSettled([api.listReleases(), api.listAppState(), api.listInstances()]);
    if (meu !== token.current) return;
    if (r.status === 'fulfilled') setReleases(r.value);
    else {
      setReleases([]);
      toastError('Não foi possível listar os aplicativos', r.reason);
    }
    if (e.status === 'fulfilled') setEstados(e.value);
    if (i.status === 'fulfilled') setAparelhos(i.value);
  }, []);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  async function importar() {
    setImportando(true);
    try {
      const { imported } = await api.importReleases();
      toast({
        tone: imported.length ? 'success' : 'info',
        title: imported.length ? `${imported.length} conjunto(s) inspecionado(s)` : 'Nada novo na pasta de entrada',
        message: 'Pacote, versão, splits e assinatura vêm do próprio arquivo — o nome não decide nada.',
      });
      await carregar();
    } catch (e) {
      toastError('A importação falhou', e);
    } finally {
      setImportando(false);
    }
  }

  async function aprovar(r: AppRelease) {
    const { confirmed, note } = await confirm({
      title: `Aprovar a assinatura de ${r.package_name}?`,
      body: `Assinatura ${r.signature_sha256.slice(0, 16)}… passa a ser a confiável para este aplicativo. `
        + 'Depois disso, release com assinatura diferente fica bloqueada até nova aprovação. Isto não prova a '
        + 'origem do arquivo: prova apenas que ele continua sendo o mesmo de antes.',
      confirmLabel: 'Aprovar assinatura',
      note: { label: 'Observação (de onde veio o arquivo)', placeholder: 'ex.: baixado por mim em 17/09' },
    });
    if (!confirmed) return;
    try {
      await api.approveSignature(r.id, note);
      await carregar();
      toast({ tone: 'success', title: 'Assinatura aprovada' });
    } catch (e) {
      toastError('Não foi possível aprovar', e);
    }
  }

  function aparelhoEscolhido(r: AppRelease): string {
    return alvo[r.id] || r.canary_instance_id || aparelhos[0]?.id || '';
  }

  async function canario(r: AppRelease) {
    const instancia = aparelhoEscolhido(r);
    if (!instancia) {
      toastError('Sem aparelho', new Error('Nenhum aparelho disponível para a prova.'));
      return;
    }
    const saindoDaQuarentena = r.channel === 'quarantined';
    const { confirmed } = await confirm({
      title: `Colocar ${r.version_name} em prova no ${instancia}?`,
      body: saindoDaQuarentena
        ? 'Esta versão já falhou uma prova antes. Colocá-la em canário de novo é uma decisão sua, e fica '
          + 'registrada. Se falhar outra vez, ela volta para a quarentena sozinha.'
        : 'A versão é instalada num aparelho só. Ela só é considerada boa se instalar, abrir e continuar de pé — '
          + 'código de retorno do ADB não conta. Falhando, vai para a quarentena automaticamente.',
      confirmLabel: saindoDaQuarentena ? 'Tentar de novo' : 'Colocar em prova',
      danger: saindoDaQuarentena,
    });
    if (!confirmed) return;
    await pedir(r.id, { verb: 'canary', instance_id: instancia }, `Prova começou em ${instancia}`);
  }

  async function promover(r: AppRelease) {
    await pedir(r.id, { verb: 'promote' }, 'Versão promovida');
  }

  async function porEmQuarentena(r: AppRelease) {
    const { confirmed, note } = await confirm({
      title: `Colocar ${r.version_name} em quarentena?`,
      body: 'A instalação desta versão fica bloqueada. Nenhum arquivo é apagado e nenhum aparelho muda sozinho: '
        + 'quem já está nela continua onde está até você pedir o rollback.',
      confirmLabel: 'Bloquear esta versão',
      danger: true,
      note: { label: 'Motivo', placeholder: 'ex.: travou ao abrir o feed' },
    });
    if (!confirmed) return;
    await pedir(r.id, { verb: 'quarantine', note: note || undefined }, 'Versão em quarentena');
  }

  async function voltar(e: DeviceAppState) {
    // Depois de uma prova de abertura que falha, `installed_release_id` fica nulo — o app está no aparelho mas não
    // roda. É exatamente o caso em que voltar importa, então a release desejada serve para identificar o pacote.
    const referencia = e.installed_release_id ?? e.desired_release_id;
    if (!referencia) return;
    const recusado = e.drift_kind === 'downgrade_refused';
    const { confirmed, note } = await confirm({
      title: `Voltar ${e.package_name} no ${e.instance_id}?`,
      body: recusado
        ? 'O aparelho já recusou voltar preservando os dados. O único caminho que resta é desinstalar e instalar de '
          + 'novo, o que APAGA os dados do aplicativo — a sessão será perdida e o login terá de ser refeito.'
        : 'Primeiro tentamos voltar preservando os dados. O Android pode recusar: nesse caso nada é apagado, o '
          + 'aparelho continua como está e o pedido volta aqui pedindo a reinstalação de propósito.',
      confirmLabel: recusado ? 'Reinstalar e perder a sessão' : 'Voltar versão',
      danger: recusado,
      note: { label: 'Observação', placeholder: 'ex.: a 448 travava ao abrir' },
    });
    if (!confirmed) return;
    await pedir(referencia,
      { verb: 'rollback', instance_id: e.instance_id, note: note || undefined, confirm_reinstall: recusado },
      `Rollback pedido em ${e.instance_id}`);
  }

  async function pedir(releaseId: string, body: Parameters<typeof api.releaseLifecycle>[1], titulo: string) {
    try {
      await api.releaseLifecycle(releaseId, body);
      await carregar();
      toast({
        tone: 'success', title: titulo,
        message: body.instance_id ? 'O resultado aparece aqui quando o aparelho responder.' : undefined,
      });
    } catch (e) {
      toastError('O pedido foi recusado', e);
    }
  }

  if (releases === null) {
    return (
      <LoadingRegion label="Carregando aplicativos…">
        <Skeleton height={120} />
      </LoadingRegion>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <h2 className={styles.title}>Aplicativos</h2>
          <p className={styles.lead}>
            Coloque os arquivos em <code>apks/inbox</code> e importe. O sistema lê pacote, versão, splits, ABIs e
            assinatura do próprio arquivo, guarda uma cópia imutável e confere o hash antes de cada instalação.
            Uma versão só é promovida depois de instalar e abrir num aparelho de prova.
          </p>
        </div>
        <Button icon={FolderInput} loading={importando} onClick={() => void importar()}>
          Importar da pasta
        </Button>
      </div>

      {releases.length === 0 ? (
        <EmptyState
          icon={Package}
          title="Nenhum aplicativo importado"
          hint="Nada é baixado automaticamente: os arquivos entram pela pasta apks/inbox."
          actions={<Button icon={FolderInput} loading={importando} onClick={() => void importar()}>Importar da pasta</Button>}
        >
          O catálogo está vazio.
        </EmptyState>
      ) : (
        <div className={styles.grid}>
          {releases.map((r) => (
            <Card key={r.id}>
              <CardHeader
                title={`${r.package_name} ${r.version_name}`}
                subtitle={`versionCode ${r.version_code} · ${r.artifact_type}`}
                actions={
                  <Button size="sm" variant="ghost" icon={ShieldCheck} onClick={() => void aprovar(r)}>
                    Aprovar assinatura
                  </Button>
                }
              />
              <CardBody>
                <dl className={styles.rows}>
                  <div className={styles.row}>
                    <dt>Estado</dt>
                    <dd><Badge tone={tom(r.status)}>{r.status}</Badge>{r.detail ? ` — ${r.detail}` : ''}</dd>
                  </div>
                  <div className={styles.row}>
                    <dt>Ciclo de vida</dt>
                    <dd>
                      <Badge tone={CANAL[r.channel].tom}>{CANAL[r.channel].rotulo}</Badge>
                      {r.channel_detail ? ` — ${r.channel_detail}` : ''}
                    </dd>
                  </div>
                  <div className={styles.row}>
                    <dt>Assinatura</dt>
                    <dd className={styles.mono}>{r.signature_sha256.slice(0, 24)}…</dd>
                  </div>
                  <div className={styles.row}>
                    <dt>SDK</dt>
                    <dd>min {r.min_sdk ?? '—'} · alvo {r.target_sdk ?? '—'}</dd>
                  </div>
                  <div className={styles.row}>
                    <dt>ABIs</dt>
                    <dd>{r.supported_abis.length ? r.supported_abis.join(', ') : 'qualquer (sem código nativo)'}</dd>
                  </div>
                  <div className={styles.row}>
                    <dt>Arquivos</dt>
                    <dd>{r.files.map((f) => f.file_name).join(', ') || '—'}</dd>
                  </div>
                  <div className={styles.row}>
                    <dt>Instalada em</dt>
                    <dd>{r.devices.length ? r.devices.join(', ') : 'nenhum aparelho'}</dd>
                  </div>
                  {r.validations.length > 0 && (
                    <div className={styles.row}>
                      <dt>Provas</dt>
                      <dd>
                        <ul className={styles.provas}>
                          {r.validations.slice(-4).map((v, i) => (
                            <li key={`${v.observed_at}:${i}`}>
                              <Badge size="sm" tone={v.ok ? 'success' : 'danger'}>
                                {v.stage === 'install' ? 'instalou' : 'abriu'}{v.ok ? '' : ' — não'}
                              </Badge>{' '}
                              {v.instance_id}{v.detail ? ` · ${v.detail}` : ''}
                            </li>
                          ))}
                        </ul>
                      </dd>
                    </div>
                  )}
                </dl>

                <div className={styles.acoes}>
                  {r.channel !== 'canary' && (
                    <>
                      <select
                        className={styles.picker}
                        aria-label={`Aparelho de prova para ${r.version_name}`}
                        value={aparelhoEscolhido(r)}
                        onChange={(ev) => setAlvo((a) => ({ ...a, [r.id]: ev.target.value }))}
                      >
                        {aparelhos.length === 0 && <option value="">nenhum aparelho</option>}
                        {aparelhos.map((i) => <option key={i.id} value={i.id}>{i.id}</option>)}
                      </select>
                      <Button size="sm" variant="ghost" icon={FlaskConical} onClick={() => void canario(r)}>
                        {r.channel === 'quarantined' ? 'Tentar de novo' : 'Colocar em prova'}
                      </Button>
                    </>
                  )}
                  {r.channel === 'canary' && (
                    <Button size="sm" icon={TrendingUp} onClick={() => void promover(r)}>Promover</Button>
                  )}
                  {r.channel !== 'quarantined' && (
                    <Button size="sm" variant="ghost" icon={ShieldX} onClick={() => void porEmQuarentena(r)}>
                      Quarentena
                    </Button>
                  )}
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      <Card>
        <CardHeader title="O que está instalado em cada aparelho"
                    subtitle="Estado lido do aparelho, nunca presumido pelo código de retorno da instalação." />
        <CardBody>
          {estados.length === 0 ? (
            <p className={styles.lead}>Nenhum aplicativo catalogado nos aparelhos ainda.</p>
          ) : (
            <ul className={styles.list}>
              {estados.map((e) => (
                <li key={`${e.instance_id}:${e.package_name}`}>
                  <Smartphone size={14} aria-hidden /> <strong>{e.instance_id}</strong> · {e.package_name}{' '}
                  <Badge tone={tom(e.state)}>{e.state}</Badge>{' '}
                  {e.observed_version_name ? `${e.observed_version_name} (${e.observed_version_code ?? '?'})` : '—'}
                  {e.drift_kind ? <> · <Badge tone="warning">{e.drift_kind}</Badge></> : null}
                  {e.detail ? <span className={styles.detail}> — {e.detail}</span> : null}
                  {e.previous_release_id && (
                    <Button size="sm" variant="ghost" icon={Undo2} onClick={() => void voltar(e)}>
                      Voltar versão
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
